"""Request/response schemas for facility module."""

from datetime import datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationInfo, field_validator
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
