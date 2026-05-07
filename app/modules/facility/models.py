"""SQLModel table definitions for facility module: Facility, Court, PricingRule."""

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

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id")
    name: str
    address: str | None = Field(default=None)
    deleted_at: datetime | None = Field(default=None)


class Court(SQLModel, table=True):
    __tablename__ = "courts"
    __table_args__ = (sa.CheckConstraint("default_price > 0", name="chk_court_default_price"),)

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
