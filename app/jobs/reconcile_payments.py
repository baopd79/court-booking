"""Cron job (every 10 min): resolve VNPay payments pending > 30 min."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine
from app.core.vnpay import get_vnpay_client
from app.modules.booking.models import BookingStatus
from app.modules.booking.repository import (
    BookingRepository,
    BookingSlotRepository,
    SlotLockRepository,
)
from app.modules.payment.repository import PaymentRepository

logger = logging.getLogger(__name__)

_PENDING_CUTOFF_MINUTES = 30


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def reconcile_payments() -> None:
    """Query VNPay for payments pending > 30 min and force-resolve."""
    cutoff = _utcnow() - timedelta(minutes=_PENDING_CUTOFF_MINUTES)
    engine = get_engine()
    vnpay = get_vnpay_client()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        payment_repo = PaymentRepository(session)
        booking_repo = BookingRepository(session)
        bs_repo = BookingSlotRepository(session)
        slot_repo = SlotLockRepository(session)

        stale = await payment_repo.list_pending_before(cutoff)
        if not stale:
            return

        logger.info("Reconciling %d stale payments", len(stale))

        for payment in stale:
            try:
                txn_date = payment.created_at.strftime("%Y%m%d%H%M%S")
                result = await vnpay.query_transaction(
                    txn_ref=payment.vnpay_txn_ref,
                    txn_date=txn_date,
                )
                await _apply_result(
                    payment, result, booking_repo, bs_repo, slot_repo, payment_repo, session
                )
            except Exception:
                logger.exception("Reconcile failed for txn_ref=%s", payment.vnpay_txn_ref)

        await session.commit()


async def _apply_result(
    payment, result, booking_repo, bs_repo, slot_repo, payment_repo, session
) -> None:  # type: ignore[no-untyped-def]
    txn_status = result.get("vnp_TransactionStatus", "")
    response_code = result.get("vnp_ResponseCode", "99")

    booking = await booking_repo.get_by_id_locked(payment.booking_id)
    if not booking or booking.status not in (
        BookingStatus.payment_processing,
        BookingStatus.pending_payment,
    ):
        logger.info(
            "Reconcile skipped: booking=%s status=%s",
            payment.booking_id,
            booking and booking.status,
        )
        return

    booking_slots = await bs_repo.list_by_booking(booking.id)
    slot_ids = [bs.slot_id for bs in booking_slots]
    slots = await slot_repo.get_by_ids_for_update(slot_ids, booking.court_id)

    if txn_status == "00":
        await payment_repo.mark_success(payment, response_code, _utcnow())
        booking.status = BookingStatus.confirmed
        await booking_repo.save(booking)
        await slot_repo.mark_booked(slots, booking.id)
        logger.info("Reconcile success: txn_ref=%s booking=%s", payment.vnpay_txn_ref, booking.id)
    else:
        await payment_repo.mark_failed(payment, response_code)
        booking.status = BookingStatus.payment_failed
        await booking_repo.save(booking)
        await slot_repo.mark_available(slots)
        logger.info(
            "Reconcile failed: txn_ref=%s booking=%s code=%s",
            payment.vnpay_txn_ref,
            booking.id,
            response_code,
        )
