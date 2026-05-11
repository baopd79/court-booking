"""SQLModel table definitions for facility module: Facility, Court, PricingRule, Slot."""

import uuid
from datetime import datetime, time
from decimal import Decimal
from enum import StrEnum

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SportType(StrEnum):
    pickleball = "pickleball"
    badminton = "badminton"
    tennis = "tennis"


class Facility(SQLModel, table=True):
    __tablename__ = "facilities"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "name", name="uq_facility_tenant_name"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id")
    name: str
    address: str | None = Field(default=None)
    deleted_at: datetime | None = Field(default=None)


class Court(SQLModel, table=True):
    __tablename__ = "courts"
    __table_args__ = (
        sa.CheckConstraint("default_price > 0", name="chk_court_default_price"),
        sa.UniqueConstraint("facility_id", "name", name="uq_court_facility_name"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    facility_id: uuid.UUID = Field(foreign_key="facilities.id")
    name: str
    sport_type: SportType
    default_price: Decimal = Field(sa_column=sa.Column(sa.Numeric(10, 2), nullable=False))
    deleted_at: datetime | None = Field(default=None)


class PricingRule(SQLModel, table=True):
    __tablename__ = "pricing_rules"
    __table_args__ = (sa.CheckConstraint("price > 0", name="chk_pricing_rule_price"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    court_id: uuid.UUID = Field(foreign_key="courts.id")
    day_of_week: int = Field(sa_column=sa.Column(sa.SmallInteger, nullable=False))
    start_time: time = Field(sa_column=sa.Column(sa.Time, nullable=False))
    end_time: time = Field(sa_column=sa.Column(sa.Time, nullable=False))
    price: Decimal = Field(sa_column=sa.Column(sa.Numeric(10, 2), nullable=False))


class SlotStatus(StrEnum):
    available = "available"
    held = "held"
    booked = "booked"
    closed = "closed"


class Slot(SQLModel, table=True):
    __tablename__ = "slots"
    __table_args__ = (
        sa.UniqueConstraint("court_id", "slot_start", name="uq_slot_court_start"),
        # Partial index for cron cleanup of expired holds — must match migration
        sa.Index(
            "idx_slots_held_until",
            "held_until",
            postgresql_where=sa.text("status = 'held'"),
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=sa.Column(sa.BigInteger, primary_key=True, autoincrement=True),
    )
    court_id: uuid.UUID = Field(foreign_key="courts.id")
    slot_start: datetime = Field(sa_column=sa.Column(sa.DateTime, nullable=False))
    slot_end: datetime = Field(sa_column=sa.Column(sa.DateTime, nullable=False))
    status: SlotStatus = Field(
        default=SlotStatus.available,
        sa_column=sa.Column(sa.Enum(SlotStatus, name="slotstatus"), nullable=False),
    )
    held_until: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime, nullable=True),
    )
    held_by_booking_id: uuid.UUID | None = Field(
        default=None,
        sa_column=sa.Column(
            sa.Uuid(),
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    version: int = Field(
        default=0,
        sa_column=sa.Column(sa.Integer, nullable=False, server_default="0"),
    )
