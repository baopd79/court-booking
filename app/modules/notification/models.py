"""SQLModel table definitions for notification module."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class NotificationChannel(StrEnum):
    email = "email"
    in_app = "in_app"


class NotificationStatus(StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    __table_args__ = (
        sa.Index("idx_notifications_user_status", "user_id", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id")
    booking_id: uuid.UUID | None = Field(
        default=None,
        sa_column=sa.Column(sa.Uuid(), sa.ForeignKey("bookings.id"), nullable=True),
    )
    channel: NotificationChannel = Field(
        sa_column=sa.Column(
            sa.Enum(NotificationChannel, name="notificationchannel"), nullable=False
        )
    )
    event_type: str = Field(sa_column=sa.Column(sa.String(64), nullable=False))
    status: NotificationStatus = Field(
        default=NotificationStatus.pending,
        sa_column=sa.Column(
            sa.Enum(NotificationStatus, name="notificationstatus"), nullable=False
        ),
    )
    payload: dict = Field(
        default_factory=dict,
        sa_column=sa.Column(JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )
    retry_count: int = Field(
        default=0, sa_column=sa.Column(sa.Integer, nullable=False, server_default="0")
    )
    sent_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime, nullable=True)
    )
    read_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=sa.Column(sa.DateTime, nullable=False),
    )
