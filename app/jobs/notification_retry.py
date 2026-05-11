"""Cron job (every 1 min): retry failed notifications up to 3 times."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine
from app.modules.notification.service import NotificationService

logger = logging.getLogger(__name__)


async def run_notification_retry(session: AsyncSession) -> int:
    """Testable core logic. Returns number of notifications retried."""
    svc = NotificationService(session)
    count = await svc.retry_failed()
    return count


async def notification_retry() -> None:
    """Cron entry point."""
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        count = await run_notification_retry(session)
        await session.commit()
        if count:
            logger.info("Retried %d failed notifications", count)
