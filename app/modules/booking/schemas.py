"""Request/response schemas for booking module."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ConfigDict, Field
from sqlmodel import SQLModel

from app.modules.booking.models import BookingStatus, BookingType


class CreateBookingRequest(SQLModel):
    court_id: UUID
    slot_ids: list[int] = Field(min_length=1, max_length=4)


class WalkInBookingRequest(SQLModel):
    court_id: UUID
    slot_ids: list[int] = Field(min_length=1, max_length=4)
    walkin_name: str = Field(min_length=1, max_length=200)
    walkin_phone: str = Field(min_length=1, max_length=20)


class SlotInfo(SQLModel):
    id: int
    slot_start: datetime
    slot_end: datetime
    price: Decimal


class BookingResponse(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: BookingStatus
    court_id: UUID
    booking_type: BookingType
    total_amount: Decimal
    hold_expires_at: datetime | None
    slots: list[SlotInfo]
    created_at: datetime
    walkin_name: str | None = None
    walkin_phone: str | None = None


class BookingListItem(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: BookingStatus
    court_id: UUID
    booking_type: BookingType
    total_amount: Decimal
    hold_expires_at: datetime | None
    created_at: datetime


class PaginatedBookingResponse(SQLModel):
    items: list[BookingListItem]
    total: int
    page: int
    limit: int
    has_next: bool


class CancelBookingRequest(SQLModel):
    reason: str | None = None


class CancelBookingResponse(SQLModel):
    booking_id: UUID
    status: BookingStatus
    refund_amount: Decimal
    refund_status: str | None  # "pending" | None


class CheckInResponse(SQLModel):
    booking_id: UUID
    status: BookingStatus
