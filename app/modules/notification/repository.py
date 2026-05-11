"""Database query layer for notification module."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from app.modules.notification.models import Notification, NotificationStatus


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, notification: Notification) -> Notification:
        self._session.add(notification)
        await self._session.flush()
        await self._session.refresh(notification)
        return notification

    async def get_by_id(self, notification_id: UUID) -> Notification | None:
        result = await self._session.execute(
            select(Notification).where(Notification.id == notification_id)
        )
        return result.scalar_one_or_none()

    async def save(self, notification: Notification) -> Notification:
        self._session.add(notification)
        await self._session.flush()
        return notification

    async def list_by_user(
        self, user_id: UUID, *, page: int = 1, limit: int = 20
    ) -> tuple[list[Notification], int]:
        base = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
        )
        total = (
            await self._session.execute(
                select(func.count(Notification.id)).where(Notification.user_id == user_id)
            )
        ).scalar_one()
        items = list(
            (await self._session.execute(base.offset((page - 1) * limit).limit(limit)))
            .scalars()
            .all()
        )
        return items, total

    async def list_failed_retryable(self, max_retry: int = 3) -> list[Notification]:
        result = await self._session.execute(
            select(Notification).where(
                Notification.status == NotificationStatus.failed,
                Notification.retry_count < max_retry,
            )
        )
        return list(result.scalars().all())
