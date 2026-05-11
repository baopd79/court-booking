"""Request/response schemas for facility module."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator
from sqlmodel import SQLModel

from app.modules.facility.models import SportType

# ===== Facility =====


class FacilityCreate(SQLModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None


class FacilityUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = None


class FacilityResponse(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    address: str | None
    deleted_at: datetime | None


# ===== Court =====


class CourtCreate(SQLModel):
    facility_id: UUID
    name: str = Field(min_length=1, max_length=200)
    sport_type: SportType
    default_price: Decimal = Field(gt=0)


class CourtUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sport_type: SportType | None = None
    default_price: Decimal | None = Field(default=None, gt=0)


class CourtResponse(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    facility_id: UUID
    name: str
    sport_type: SportType
    default_price: Decimal
    deleted_at: datetime | None


# ===== Pricing Rule =====


class PricingRuleItem(SQLModel):
    """A single pricing rule within a PUT request or GET response."""

    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    price: Decimal = Field(gt=0)

    @field_validator("start_time", "end_time")
    @classmethod
    def must_be_hour_aligned(cls, v: time) -> time:
        if v.minute != 0 or v.second != 0 or v.microsecond != 0:
            raise ValueError("time must be on the hour (e.g. 06:00, not 06:30)")
        return v

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, v: time, info: ValidationInfo) -> time:
        start = info.data.get("start_time")
        if start and v <= start:
            raise ValueError("end_time must be after start_time")
        return v


class PricingRuleReplace(SQLModel):
    """PUT /courts/{id}/pricing — replace toàn bộ pricing rules."""

    rules: list[PricingRuleItem] = Field(min_length=1)

    @model_validator(mode="after")
    def no_overlapping_rules(self) -> "PricingRuleReplace":
        by_day: dict[int, list[PricingRuleItem]] = {}
        for rule in self.rules:
            by_day.setdefault(rule.day_of_week, []).append(rule)
        for day, day_rules in by_day.items():
            sorted_rules = sorted(day_rules, key=lambda r: r.start_time)
            for i in range(1, len(sorted_rules)):
                if sorted_rules[i].start_time < sorted_rules[i - 1].end_time:
                    raise ValueError(
                        f"Pricing rules for day_of_week={day} have overlapping time ranges"
                    )
        return self


class PricingRuleResponse(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    court_id: UUID
    day_of_week: int
    start_time: time
    end_time: time
    price: Decimal


# ===== Pagination =====


class PaginatedFacilityResponse(SQLModel):
    items: list[FacilityResponse]
    total: int
    page: int
    limit: int
    has_next: bool


class PaginatedCourtResponse(SQLModel):
    items: list[CourtResponse]
    total: int
    page: int
    limit: int
    has_next: bool


# ===== Slot =====


class SlotAvailability(SQLModel):
    id: int
    slot_start: datetime
    slot_end: datetime
    status: Literal["available", "unavailable", "closed"]
    price: Decimal


class CourtAvailability(SQLModel):
    id: UUID
    name: str
    sport_type: SportType
    slots: list[SlotAvailability]


class AvailabilityResponse(SQLModel):
    date: date
    facility_id: UUID
    courts: list[CourtAvailability]


class SlotRangeRequest(SQLModel):
    """Request body for bulk close/reopen slots."""

    date: date
    start_time: time
    end_time: time


class SlotUpdateResult(SQLModel):
    updated: int
