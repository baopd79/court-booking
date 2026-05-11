"""SQLModel table definitions for payment module: Payment, Refund."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PaymentMethod(StrEnum):
    vnpay = "vnpay"
    offline_cash = "offline_cash"


class PaymentStatus(StrEnum):
    pending = "pending"
    success = "success"
    failed = "failed"


class RefundStatus(StrEnum):
    pending = "pending"
    success = "success"
    failed = "failed"


class Payment(SQLModel, table=True):
    __tablename__ = "payments"
    __table_args__ = (
        sa.Index(
            "idx_payments_booking_success",
            "booking_id",
            postgresql_where=sa.text("status = 'success'"),
            unique=True,
        ),
        sa.CheckConstraint("amount > 0", name="chk_payment_amount"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    booking_id: uuid.UUID = Field(foreign_key="bookings.id")
    method: PaymentMethod = Field(
        sa_column=sa.Column(sa.Enum(PaymentMethod, name="paymentmethod"), nullable=False)
    )
    amount: Decimal = Field(sa_column=sa.Column(sa.Numeric(10, 2), nullable=False))
    status: PaymentStatus = Field(
        default=PaymentStatus.pending,
        sa_column=sa.Column(sa.Enum(PaymentStatus, name="paymentstatus"), nullable=False),
    )
    vnpay_txn_ref: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(64), nullable=True, unique=True),
    )
    vnpay_response_code: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(10), nullable=True),
    )
    vnpay_payment_url: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.Text, nullable=True),
    )
    url_expires_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime, nullable=True),
    )
    paid_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=sa.Column(sa.DateTime, nullable=False),
    )


class Refund(SQLModel, table=True):
    __tablename__ = "refunds"
    __table_args__ = (sa.CheckConstraint("amount > 0", name="chk_refund_amount"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    payment_id: uuid.UUID = Field(foreign_key="payments.id")
    amount: Decimal = Field(sa_column=sa.Column(sa.Numeric(10, 2), nullable=False))
    status: RefundStatus = Field(
        default=RefundStatus.pending,
        sa_column=sa.Column(sa.Enum(RefundStatus, name="refundstatus"), nullable=False),
    )
    reason: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=sa.Column(sa.DateTime, nullable=False),
    )
