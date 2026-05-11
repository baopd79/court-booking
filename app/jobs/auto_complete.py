"""Cron job (every 5 min): complete confirmed/in_use bookings whose slots have ended."""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine
from app.modules.booking.models import BookingStatus
from app.modules.booking.repository import BookingRepository

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def run_auto_complete(session: AsyncSession) -> int:
    """Core logic — testable. Returns number of bookings completed."""
    now = _utcnow()
    booking_repo = BookingRepository(session)
    completable = await booking_repo.list_completable(now)
    count = 0
    for booking in completable:
        try:
            booking.status = BookingStatus.completed
            await booking_repo.save(booking)
            count += 1
        except Exception:
            logger.exception("Failed to complete booking %s", booking.id)
    return count


async def auto_complete() -> None:
    """Cron entry point."""
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        count = await run_auto_complete(session)
        await session.commit()
        if count:
            logger.info("Auto-completed %d bookings", count)
