"""Cron job (every 1 min): expire pending_payment bookings whose hold has timed out."""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine
from app.modules.booking.models import BookingStatus, CancelledBy
from app.modules.booking.repository import BookingRepository, BookingSlotRepository, SlotLockRepository

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def run_expire_holds(session: AsyncSession) -> int:
    """Core logic — testable. Returns number of bookings expired."""
    now = _utcnow()
    booking_repo = BookingRepository(session)
    bs_repo = BookingSlotRepository(session)
    slot_repo = SlotLockRepository(session)

    stale = await booking_repo.list_expired_holds(now)
    count = 0
    for booking in stale:
        try:
            booking_slots = await bs_repo.list_by_booking(booking.id)
            slot_ids = [bs.slot_id for bs in booking_slots]
            slots = await slot_repo.get_by_ids_for_update(slot_ids, booking.court_id)
            booking.status = BookingStatus.expired
            booking.cancelled_by = CancelledBy.system
            await booking_repo.save(booking)
            await slot_repo.mark_available(slots)
            count += 1
        except Exception:
            logger.exception("Failed to expire booking %s", booking.id)
    return count


async def expire_holds() -> None:
    """Cron entry point."""
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        count = await run_expire_holds(session)
        await session.commit()
        if count:
            logger.info("Expired %d holds", count)
