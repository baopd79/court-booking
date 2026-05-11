"""SQLModel table definitions for booking module: Booking, BookingSlot, IdempotencyKey."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class BookingStatus(StrEnum):
    pending_payment = "pending_payment"
    payment_processing = "payment_processing"
    confirmed = "confirmed"
    in_use = "in_use"
    completed = "completed"
    expired = "expired"
    payment_failed = "payment_failed"
    cancelled = "cancelled"


class BookingType(StrEnum):
    online = "online"
    walkin = "walkin"


class CancelledBy(StrEnum):
    customer = "customer"
    owner = "owner"
    system = "system"


class Booking(SQLModel, table=True):
    __tablename__ = "bookings"
    __table_args__ = (
        sa.CheckConstraint(
            "(booking_type = 'online'"
            "  AND customer_id IS NOT NULL"
            "  AND walkin_name IS NULL"
            "  AND walkin_phone IS NULL)"
            " OR "
            "(booking_type = 'walkin'"
            "  AND customer_id IS NULL"
            "  AND walkin_name IS NOT NULL"
            "  AND walkin_phone IS NOT NULL)",
            name="chk_booking_owner",
        ),
        sa.CheckConstraint("total_amount > 0", name="chk_booking_amount"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id")
    customer_id: uuid.UUID | None = Field(
        default=None,
        sa_column=sa.Column(sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
    )
    court_id: uuid.UUID = Field(foreign_key="courts.id")
    status: BookingStatus = Field(
        sa_column=sa.Column(
            sa.Enum(BookingStatus, name="bookingstatus"), nullable=False
        )
    )
    total_amount: Decimal = Field(
        sa_column=sa.Column(sa.Numeric(10, 2), nullable=False)
    )
    booking_type: BookingType = Field(
        sa_column=sa.Column(sa.Enum(BookingType, name="bookingtype"), nullable=False)
    )
    walkin_name: str | None = Field(default=None)
    walkin_phone: str | None = Field(default=None)
    hold_expires_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime, nullable=True)
    )
    cancelled_by: CancelledBy | None = Field(
        default=None,
        sa_column=sa.Column(
            sa.Enum(CancelledBy, name="cancelledby"), nullable=True
        ),
    )
    cancellation_reason: str | None = Field(default=None)
    cancelled_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=sa.Column(sa.DateTime, nullable=False),
    )


class BookingSlot(SQLModel, table=True):
    __tablename__ = "booking_slots"
    __table_args__ = (
        sa.CheckConstraint(
            "price_at_booking > 0", name="chk_booking_slot_price"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    booking_id: uuid.UUID = Field(foreign_key="bookings.id")
    slot_id: int = Field(
        sa_column=sa.Column(
            sa.BigInteger, sa.ForeignKey("slots.id"), nullable=False
        )
    )
    price_at_booking: Decimal = Field(
        sa_column=sa.Column(sa.Numeric(10, 2), nullable=False)
    )


class IdempotencyKey(SQLModel, table=True):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        sa.Index("idx_idempotency_expires", "expires_at"),
    )

    key: str = Field(
        sa_column=sa.Column(sa.String(64), primary_key=True, nullable=False)
    )
    user_id: uuid.UUID = Field(
        sa_column=sa.Column(
            sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True, nullable=False
        )
    )
    endpoint: str = Field(
        sa_column=sa.Column(sa.String(100), nullable=False)
    )
    request_hash: str | None = Field(
        default=None, sa_column=sa.Column(sa.String(64), nullable=True)
    )
    response_status: int | None = Field(
        default=None, sa_column=sa.Column(sa.Integer, nullable=True)
    )
    response_body: dict | None = Field(
        default=None, sa_column=sa.Column(JSONB, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=sa.Column(sa.DateTime, nullable=False),
    )
    expires_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime, nullable=False)
    )
