"""Business logic for booking module."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BookingNotCancellableError,
    BookingNotCheckInableError,
    BookingNotFoundError,
    CourtNotFoundError,
    ForbiddenError,
    IdempotencyKeyReusedError,
    InvalidSlotsError,
    MissingIdempotencyKeyError,
    SlotNotAvailableError,
    TooManyPendingError,
)
from app.modules.auth.models import User, UserRole
from app.modules.booking.models import (
    Booking,
    BookingSlot,
    BookingStatus,
    BookingType,
    CancelledBy,
)
from app.modules.booking.repository import (
    BookingRepository,
    BookingSlotRepository,
    IdempotencyRepository,
    SlotLockRepository,
)
from app.modules.booking.schemas import (
    BookingListItem,
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CheckInResponse,
    CreateBookingRequest,
    PaginatedBookingResponse,
    SlotInfo,
    WalkInBookingRequest,
)
from app.modules.facility.models import Slot, SlotStatus
from app.modules.facility.pricing import find_price as _find_price
from app.modules.facility.repository import CourtRepository, PricingRuleRepository

# ===== State machine =====

ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.pending_payment: {
        BookingStatus.payment_processing,
        BookingStatus.expired,
        BookingStatus.cancelled,
    },
    BookingStatus.payment_processing: {
        BookingStatus.confirmed,
        BookingStatus.payment_failed,
    },
    BookingStatus.confirmed: {
        BookingStatus.in_use,
        BookingStatus.completed,
        BookingStatus.cancelled,
    },
    BookingStatus.in_use: {BookingStatus.completed},
    BookingStatus.completed: set(),
    BookingStatus.expired: set(),
    BookingStatus.payment_failed: set(),
    BookingStatus.cancelled: set(),
}

_HOLD_DURATION = timedelta(minutes=10)
_IDEMPOTENCY_TTL = timedelta(hours=24)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _request_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _is_consecutive(slots: list[Slot]) -> bool:
    return all(slots[i].slot_start == slots[i - 1].slot_end for i in range(1, len(slots)))


class BookingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._booking_repo = BookingRepository(session)
        self._bs_repo = BookingSlotRepository(session)
        self._slot_repo = SlotLockRepository(session)
        self._idempotency_repo = IdempotencyRepository(session)
        self._court_repo = CourtRepository(session)
        self._pricing_repo = PricingRuleRepository(session)

    # ===== Reserve (online booking) =====

    async def create(
        self,
        data: CreateBookingRequest,
        customer: User,
        idempotency_key: str | None,
    ) -> BookingResponse:
        if not idempotency_key:
            raise MissingIdempotencyKeyError("Idempotency-Key header is required")

        body_hash = _request_hash(
            {"court_id": str(data.court_id), "slot_ids": sorted(data.slot_ids)}
        )
        if cached := await self._idempotency_guard(
            idempotency_key, customer.id, "POST /bookings", body_hash
        ):
            return cached

        slots_sorted = await self._validate_and_lock_slots(data.slot_ids, data.court_id)

        if await self._booking_repo.count_pending(customer.id) >= 5:
            raise TooManyPendingError("Too many pending bookings")

        court = await self._court_repo.get_by_id_for_tenant(data.court_id, customer.tenant_id)
        if not court:
            raise CourtNotFoundError("Court not found")
        rules = await self._pricing_repo.list_by_court(data.court_id)
        slot_prices = [_find_price(s, rules, court.default_price) for s in slots_sorted]
        total = sum(slot_prices, Decimal("0"))

        hold_expires = _utcnow() + _HOLD_DURATION
        booking = Booking(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            court_id=data.court_id,
            status=BookingStatus.pending_payment,
            total_amount=total,
            booking_type=BookingType.online,
            hold_expires_at=hold_expires,
        )
        booking = await self._booking_repo.create(booking)

        booking_slots = [
            BookingSlot(
                booking_id=booking.id,
                slot_id=slots_sorted[i].id,
                price_at_booking=slot_prices[i],
            )
            for i in range(len(slots_sorted))
        ]
        await self._bs_repo.bulk_create(booking_slots)
        await self._slot_repo.mark_held(slots_sorted, booking.id, hold_expires)

        response = self._build_response(booking, slots_sorted, slot_prices)
        await self._idempotency_repo.save_response(
            idempotency_key, customer.id, 201, response.model_dump(mode="json")
        )
        await self._session.commit()
        return response

    # ===== Walk-in (owner, immediate confirm) =====

    async def create_walkin(
        self,
        data: WalkInBookingRequest,
        owner: User,
        idempotency_key: str | None,
    ) -> BookingResponse:
        if not idempotency_key:
            raise MissingIdempotencyKeyError("Idempotency-Key header is required")

        body_hash = _request_hash(
            {
                "court_id": str(data.court_id),
                "slot_ids": sorted(data.slot_ids),
                "walkin_name": data.walkin_name,
                "walkin_phone": data.walkin_phone,
            }
        )
        if cached := await self._idempotency_guard(
            idempotency_key, owner.id, "POST /bookings/walk-in", body_hash
        ):
            return cached

        court = await self._court_repo.get_by_id_for_tenant(data.court_id, owner.tenant_id)
        if not court:
            raise CourtNotFoundError("Court not found")

        slots_sorted = await self._validate_and_lock_slots(data.slot_ids, data.court_id)

        rules = await self._pricing_repo.list_by_court(data.court_id)
        slot_prices = [_find_price(s, rules, court.default_price) for s in slots_sorted]
        total = sum(slot_prices, Decimal("0"))

        booking = Booking(
            tenant_id=owner.tenant_id,
            customer_id=None,
            court_id=data.court_id,
            status=BookingStatus.confirmed,
            total_amount=total,
            booking_type=BookingType.walkin,
            walkin_name=data.walkin_name,
            walkin_phone=data.walkin_phone,
            hold_expires_at=None,
        )
        booking = await self._booking_repo.create(booking)

        booking_slots = [
            BookingSlot(
                booking_id=booking.id,
                slot_id=slots_sorted[i].id,
                price_at_booking=slot_prices[i],
            )
            for i in range(len(slots_sorted))
        ]
        await self._bs_repo.bulk_create(booking_slots)
        await self._slot_repo.mark_booked(slots_sorted, booking.id)

        response = self._build_response(booking, slots_sorted, slot_prices)
        await self._idempotency_repo.save_response(
            idempotency_key, owner.id, 201, response.model_dump(mode="json")
        )
        await self._session.commit()
        return response

    # ===== Read endpoints =====

    async def get(self, booking_id: UUID, user: User) -> BookingResponse:
        booking = await self._booking_repo.get_by_id(booking_id)
        if not booking:
            raise BookingNotFoundError()

        if user.role == UserRole.customer:
            if booking.customer_id != user.id:
                raise ForbiddenError()
        else:
            if booking.tenant_id != user.tenant_id:
                raise ForbiddenError()

        booking_slots = await self._bs_repo.list_by_booking(booking_id)
        slot_ids = [bs.slot_id for bs in booking_slots]
        slots = await self._slot_repo.get_by_ids(slot_ids)
        slot_map = {s.id: s for s in slots}
        bs_sorted = sorted(booking_slots, key=lambda bs: slot_map[bs.slot_id].slot_start)
        slots_sorted = [slot_map[bs.slot_id] for bs in bs_sorted]
        prices = [bs.price_at_booking for bs in bs_sorted]
        return self._build_response(booking, slots_sorted, prices)

    async def list_mine(
        self, customer: User, page: int = 1, limit: int = 20
    ) -> PaginatedBookingResponse:
        items, total = await self._booking_repo.list_by_customer(
            customer.id, page=page, limit=limit
        )
        return PaginatedBookingResponse(
            items=[BookingListItem.model_validate(b) for b in items],
            total=total,
            page=page,
            limit=limit,
            has_next=(page * limit) < total,
        )

    async def list_all(
        self, owner: User, page: int = 1, limit: int = 20
    ) -> PaginatedBookingResponse:
        items, total = await self._booking_repo.list_by_tenant(
            owner.tenant_id, page=page, limit=limit
        )
        return PaginatedBookingResponse(
            items=[BookingListItem.model_validate(b) for b in items],
            total=total,
            page=page,
            limit=limit,
            has_next=(page * limit) < total,
        )

    # ===== Private helpers =====

    async def _idempotency_guard(
        self, key: str, user_id: UUID, endpoint: str, body_hash: str
    ) -> BookingResponse | None:
        """Returns cached response on replay, None to proceed, raises on conflict."""
        existing = await self._idempotency_repo.find(key, user_id)
        if existing:
            if existing.request_hash != body_hash:
                raise IdempotencyKeyReusedError()
            if existing.response_body:
                return BookingResponse.model_validate(existing.response_body)
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

    async def _validate_and_lock_slots(self, slot_ids: list[int], court_id: UUID) -> list[Slot]:
        """FOR UPDATE lock + availability + consecutive check. Returns sorted slots."""
        slots = await self._slot_repo.get_by_ids_for_update(slot_ids, court_id)
        if len(slots) != len(slot_ids):
            raise InvalidSlotsError("Some slots not found or don't belong to this court")
        unavailable = [s.id for s in slots if s.status != SlotStatus.available]
        if unavailable:
            raise SlotNotAvailableError(unavailable)
        slots_sorted = sorted(slots, key=lambda s: s.slot_start)
        if not _is_consecutive(slots_sorted):
            raise InvalidSlotsError("Slots must be consecutive")
        return slots_sorted

    # ===== Cancel (customer) =====

    async def cancel(
        self,
        booking_id: UUID,
        data: CancelBookingRequest,
        customer: User,
        idempotency_key: str | None,
    ) -> CancelBookingResponse:
        if not idempotency_key:
            raise MissingIdempotencyKeyError("Idempotency-Key header is required")

        body_hash = _request_hash({"booking_id": str(booking_id), "reason": data.reason})
        if cached := await self._idempotency_guard_cancel(
            idempotency_key, customer.id, "POST /bookings/cancel", body_hash
        ):
            return cached

        booking = await self._booking_repo.get_by_id_for_customer_locked(booking_id, customer.id)
        if not booking:
            raise BookingNotFoundError()

        self._assert_cancellable(booking)

        slots = await self._get_booking_slots_locked(booking)
        refund_amount, refund_status = await self._apply_cancel_refund(
            booking, slots, CancelledBy.customer, data.reason
        )

        response = CancelBookingResponse(
            booking_id=booking_id,
            status=BookingStatus.cancelled,
            refund_amount=refund_amount,
            refund_status=refund_status,
        )
        await self._idempotency_repo.save_response(
            idempotency_key, customer.id, 200, response.model_dump(mode="json")
        )
        await self._session.commit()

        from app.modules.notification.service import NotificationService
        await NotificationService(self._session).notify_booking_cancelled(
            booking, refund_amount=str(refund_amount)
        )
        await self._session.commit()
        return response

    # ===== Force-cancel (owner) =====

    async def force_cancel(
        self,
        booking_id: UUID,
        data: CancelBookingRequest,
        owner: User,
    ) -> CancelBookingResponse:
        booking = await self._booking_repo.get_by_id_locked(booking_id)
        if not booking or booking.tenant_id != owner.tenant_id:
            raise BookingNotFoundError()

        self._assert_cancellable(booking)

        slots = await self._get_booking_slots_locked(booking)
        refund_amount, refund_status = await self._apply_cancel_refund(
            booking, slots, CancelledBy.owner, data.reason
        )

        await self._session.commit()

        from app.modules.notification.service import NotificationService
        await NotificationService(self._session).notify_booking_cancelled(
            booking, refund_amount=str(refund_amount)
        )
        await self._session.commit()
        return CancelBookingResponse(
            booking_id=booking_id,
            status=BookingStatus.cancelled,
            refund_amount=refund_amount,
            refund_status=refund_status,
        )

    # ===== Check-in (owner) =====

    async def check_in(self, booking_id: UUID, owner: User) -> CheckInResponse:
        booking = await self._booking_repo.get_by_id_locked(booking_id)
        if not booking or booking.tenant_id != owner.tenant_id:
            raise BookingNotFoundError()

        if booking.status != BookingStatus.confirmed:
            raise BookingNotCheckInableError(
                f"Cannot check in booking with status '{booking.status}'"
            )

        booking.status = BookingStatus.in_use
        await self._booking_repo.save(booking)
        await self._session.commit()
        return CheckInResponse(booking_id=booking_id, status=BookingStatus.in_use)

    # ===== Cancel helpers =====

    @staticmethod
    def _assert_cancellable(booking: Booking) -> None:
        if booking.status == BookingStatus.payment_processing:
            raise BookingNotCancellableError(
                "Cannot cancel while payment is processing — wait for payment result"
            )
        cancellable = {BookingStatus.pending_payment, BookingStatus.confirmed}
        if booking.status not in cancellable:
            raise BookingNotCancellableError(
                f"Booking with status '{booking.status}' cannot be cancelled"
            )

    async def _apply_cancel_refund(
        self,
        booking: Booking,
        slots: list,
        cancelled_by: CancelledBy,
        reason: str | None,
    ) -> tuple[Decimal, str | None]:
        """Cancel booking, free slots, create refund if eligible. Returns (amount, status)."""
        from app.modules.payment.models import PaymentStatus, Refund
        from app.modules.payment.repository import PaymentRepository, RefundRepository

        refund_amount = Decimal("0")
        refund_status: str | None = None

        if booking.status == BookingStatus.confirmed and slots:
            first_slot_start = min(s.slot_start for s in slots)
            hours_until = (first_slot_start - _utcnow()).total_seconds() / 3600
            if hours_until >= 24:
                payment_repo = PaymentRepository(self._session)
                payment = await payment_repo.get_by_booking(booking.id)
                if payment and payment.status == PaymentStatus.success:
                    refund = Refund(
                        payment_id=payment.id,
                        amount=booking.total_amount,
                        reason=reason or f"Cancelled by {cancelled_by}",
                    )
                    await RefundRepository(self._session).create(refund)
                    refund_amount = booking.total_amount
                    refund_status = "pending"

        booking.status = BookingStatus.cancelled
        booking.cancelled_by = cancelled_by
        booking.cancellation_reason = reason
        booking.cancelled_at = _utcnow()
        await self._booking_repo.save(booking)
        await self._slot_repo.mark_available(slots)
        return refund_amount, refund_status

    async def _get_booking_slots_locked(self, booking: Booking) -> list:
        booking_slots = await self._bs_repo.list_by_booking(booking.id)
        slot_ids = [bs.slot_id for bs in booking_slots]
        return await self._slot_repo.get_by_ids_for_update(slot_ids, booking.court_id)

    async def _idempotency_guard_cancel(
        self, key: str, user_id: UUID, endpoint: str, body_hash: str
    ) -> CancelBookingResponse | None:
        existing = await self._idempotency_repo.find(key, user_id)
        if existing:
            if existing.request_hash != body_hash:
                raise IdempotencyKeyReusedError()
            if existing.response_body:
                return CancelBookingResponse.model_validate(existing.response_body)
            raise IdempotencyKeyReusedError()
        reserved = await self._idempotency_repo.reserve(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            request_hash=body_hash,
            expires_at=_utcnow() + timedelta(hours=24),
        )
        if not reserved:
            raise IdempotencyKeyReusedError()
        return None

    @staticmethod
    def _build_response(
        booking: Booking, slots: list[Slot], prices: list[Decimal]
    ) -> BookingResponse:
        slot_infos = [
            SlotInfo(
                id=slots[i].id,
                slot_start=slots[i].slot_start,
                slot_end=slots[i].slot_end,
                price=prices[i],
            )
            for i in range(len(slots))
        ]
        return BookingResponse(
            id=booking.id,
            status=booking.status,
            court_id=booking.court_id,
            booking_type=booking.booking_type,
            total_amount=booking.total_amount,
            hold_expires_at=booking.hold_expires_at,
            slots=slot_infos,
            created_at=booking.created_at,
            walkin_name=booking.walkin_name,
            walkin_phone=booking.walkin_phone,
        )
