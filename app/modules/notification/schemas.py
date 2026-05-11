"""Request/response schemas for notification module."""

from datetime import datetime
from uuid import UUID

from sqlmodel import SQLModel

from app.modules.notification.models import NotificationChannel, NotificationStatus


class NotificationItem(SQLModel):
    id: UUID
    event_type: str
    channel: NotificationChannel
    status: NotificationStatus
    payload: dict
    read_at: datetime | None
    created_at: datetime


class PaginatedNotificationResponse(SQLModel):
    items: list[NotificationItem]
    total: int
    page: int
    limit: int
    has_next: bool
