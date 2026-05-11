"""Business logic for payment module."""

import hashlib
import json
import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BookingExpiredError,
    BookingNotFoundError,
    BookingNotPayableError,
    IdempotencyKeyReusedError,
    MissingIdempotencyKeyError,
)
from app.core.vnpay import VNPayClient
from app.modules.auth.models import User
from app.modules.booking.models import Booking, BookingStatus
from app.modules.booking.repository import (
    BookingRepository,
    BookingSlotRepository,
    IdempotencyRepository,
    SlotLockRepository,
)
from app.modules.facility.models import Slot
from app.modules.payment.models import Payment, PaymentMethod, PaymentStatus, Refund
from app.modules.payment.repository import PaymentRepository, RefundRepository
from app.modules.payment.schemas import (
    InitiatePaymentRequest,
    InitiatePaymentResponse,
    VNPayReturnResponse,
)

logger = logging.getLogger(__name__)

_IDEMPOTENCY_TTL = timedelta(hours=24)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _request_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class PaymentService:
    def __init__(self, session: AsyncSession, vnpay_client: VNPayClient) -> None:
        self._session = session
        self._payment_repo = PaymentRepository(session)
        self._refund_repo = RefundRepository(session)
        self._booking_repo = BookingRepository(session)
        self._bs_repo = BookingSlotRepository(session)
        self._slot_repo = SlotLockRepository(session)
        self._idempotency_repo = IdempotencyRepository(session)
        self._vnpay = vnpay_client

    # ===== POST /payments/initiate =====

    async def initiate(
        self,
        data: InitiatePaymentRequest,
        customer: User,
        idempotency_key: str | None,
        client_ip: str,
    ) -> InitiatePaymentResponse:
        if not idempotency_key:
            raise MissingIdempotencyKeyError("Idempotency-Key header is required")

        body_hash = _request_hash(
            {"booking_id": str(data.booking_id), "return_url": data.return_url}
        )
        if cached := await self._idempotency_guard(
            idempotency_key, customer.id, "POST /payments/initiate", body_hash
        ):
            return cached

        # Lock booking row — prevents double-initiate race condition
        booking = await self._booking_repo.get_by_id_for_customer_locked(
            data.booking_id, customer.id
        )
        if not booking:
            raise BookingNotFoundError()

        # Idempotent: already payment_processing with a valid URL → return existing
        if booking.status == BookingStatus.payment_processing:
            existing = await self._payment_repo.get_by_booking(booking.id)
            if existing and existing.url_expires_at and existing.url_expires_at > _utcnow():
                response = InitiatePaymentResponse(
                    payment_url=existing.vnpay_payment_url,
                    expires_at=existing.url_expires_at,
                    is_existing=True,
                )
                await self._save_idempotency(idempotency_key, customer.id, response)
                await self._session.commit()
                return response

        if booking.status != BookingStatus.pending_payment:
            raise BookingNotPayableError()
        if booking.hold_expires_at and booking.hold_expires_at < _utcnow():
            raise BookingExpiredError()

        txn_ref = _uuid.uuid4().hex[:20]
        payment_url, expires_at = self._vnpay.build_payment_url(
            booking_id=booking.id,
            amount=booking.total_amount,
            txn_ref=txn_ref,
            return_url=data.return_url,
            client_ip=client_ip,
        )

        payment = Payment(
            booking_id=booking.id,
            method=PaymentMethod.vnpay,
            amount=booking.total_amount,
            status=PaymentStatus.pending,
            vnpay_txn_ref=txn_ref,
            vnpay_payment_url=payment_url,
            url_expires_at=expires_at,
        )
        await self._payment_repo.create(payment)

        booking.status = BookingStatus.payment_processing
        await self._booking_repo.save(booking)

        response = InitiatePaymentResponse(
            payment_url=payment_url,
            expires_at=expires_at,
            is_existing=False,
        )
        await self._save_idempotency(idempotency_key, customer.id, response)
        await self._session.commit()
        return response

    # ===== POST /payments/vnpay-ipn =====

    async def handle_vnpay_ipn(self, params: dict) -> dict:
        if not self._vnpay.verify_signature(params):
            return {"RspCode": "97", "Message": "Invalid signature"}

        txn_ref = params.get("vnp_TxnRef", "")
        response_code = params.get("vnp_ResponseCode", "")
        try:
            amount = int(params.get("vnp_Amount", "0")) / 100
        except (ValueError, TypeError):
            return {"RspCode": "04", "Message": "Invalid amount"}

        payment = await self._payment_repo.get_by_txn_ref_locked(txn_ref)
        if not payment:
            return {"RspCode": "01", "Message": "Order not found"}

        if payment.status in (PaymentStatus.success, PaymentStatus.failed):
            return {"RspCode": "00", "Message": "Already processed"}

        booking = await self._booking_repo.get_by_id_locked(payment.booking_id)
        if not booking:
            return {"RspCode": "01", "Message": "Booking not found"}

        # Edge case: webhook arrives after booking expired by cron
        if booking.status == BookingStatus.expired:
            if response_code == "00":
                await self._create_orphan_refund(payment, amount)
                logger.warning("Orphan payment after expire: txn_ref=%s", txn_ref)
            await self._payment_repo.mark_failed(payment, response_code)
            await self._session.commit()
            return {"RspCode": "00", "Message": "Refund initiated"}

        if float(booking.total_amount) != amount:
            logger.error(
                "Amount mismatch: txn_ref=%s expected=%s got=%s",
                txn_ref,
                booking.total_amount,
                amount,
            )
            return {"RspCode": "04", "Message": "Amount mismatch"}

        slots = await self._get_booking_slots_locked(booking)

        if response_code == "00":
            await self._payment_repo.mark_success(payment, response_code, _utcnow())
            booking.status = BookingStatus.confirmed
            await self._booking_repo.save(booking)
            await self._slot_repo.mark_booked(slots, booking.id)
        else:
            await self._payment_repo.mark_failed(payment, response_code)
            booking.status = BookingStatus.payment_failed
            await self._booking_repo.save(booking)
            await self._slot_repo.mark_available(slots)

        await self._session.commit()

        from app.modules.notification.service import NotificationService

        notif_svc = NotificationService(self._session)
        if response_code == "00":
            await notif_svc.notify_booking_confirmed(booking)
        else:
            await notif_svc.notify_payment_failed(booking)
        await self._session.commit()

        return {"RspCode": "00", "Message": "Confirm Success"}

    # ===== GET /payments/vnpay-return =====

    async def handle_vnpay_return(self, params: dict) -> VNPayReturnResponse:
        if not self._vnpay.verify_signature(params):
            return VNPayReturnResponse(
                payment_status=PaymentStatus.failed,
                booking_id=None,
                message="Invalid signature",
            )

        txn_ref = params.get("vnp_TxnRef", "")
        response_code = params.get("vnp_ResponseCode", "")
        payment = await self._payment_repo.get_by_txn_ref(txn_ref)

        if not payment:
            return VNPayReturnResponse(
                payment_status=PaymentStatus.failed,
                booking_id=None,
                message="Transaction not found",
            )

        return VNPayReturnResponse(
            payment_status=PaymentStatus.success if response_code == "00" else PaymentStatus.failed,
            booking_id=payment.booking_id,
            message="Success"
            if response_code == "00"
            else f"Payment failed (code={response_code})",
        )

    # ===== Private helpers =====

    async def _idempotency_guard(
        self, key: str, user_id: UUID, endpoint: str, body_hash: str
    ) -> InitiatePaymentResponse | None:
        existing = await self._idempotency_repo.find(key, user_id)
        if existing:
            if existing.request_hash != body_hash:
                raise IdempotencyKeyReusedError()
            if existing.response_body:
                return InitiatePaymentResponse.model_validate(existing.response_body)
            raise IdempotencyKeyReusedError()
        reserved = await self._idempotency_repo.reserve(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            request_hash=body_hash,
            expires_at=_utcnow() + _IDEMPOTENCY_TTL,
        )
        if not reserved:
            raise IdempotencyKeyReusedError()
        return None

    async def _save_idempotency(
        self, key: str, user_id: UUID, response: InitiatePaymentResponse
    ) -> None:
        await self._idempotency_repo.save_response(
            key, user_id, 200, response.model_dump(mode="json")
        )

    async def _get_booking_slots_locked(self, booking: Booking) -> list[Slot]:
        booking_slots = await self._bs_repo.list_by_booking(booking.id)
        slot_ids = [bs.slot_id for bs in booking_slots]
        return await self._slot_repo.get_by_ids_for_update(slot_ids, booking.court_id)

    async def _create_orphan_refund(self, payment: Payment, amount: float) -> None:
        from decimal import Decimal

        refund = Refund(
            payment_id=payment.id,
            amount=Decimal(str(amount)),
            reason="Webhook arrived after booking expired",
        )
        await self._refund_repo.create(refund)
