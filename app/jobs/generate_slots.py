"""Nightly cron job: generate slots for day 31 ahead."""

import logging
from datetime import UTC, datetime, timedelta

from app.core.database import get_engine

logger = logging.getLogger(__name__)


async def generate_day_ahead(days_ahead: int = 31) -> None:
    """Generate slots for `days_ahead` from today. Called by nightly cron."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.facility.service import SlotService

    target_date = (datetime.now(UTC).replace(tzinfo=None) + timedelta(days=days_ahead)).date()
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            service = SlotService(session)
            inserted = await service.generate_for_all_courts_on_date(target_date)
            await session.commit()
            logger.info("Slot generation: %d new slots for %s", inserted, target_date)
        except Exception:
            await session.rollback()
            logger.exception("Slot generation failed for %s", target_date)
