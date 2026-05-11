"""Request/response schemas for payment module."""

from datetime import datetime
from uuid import UUID

from pydantic import Field
from sqlmodel import SQLModel

from app.modules.payment.models import PaymentStatus


class InitiatePaymentRequest(SQLModel):
    booking_id: UUID
    return_url: str = Field(min_length=1)


class InitiatePaymentResponse(SQLModel):
    payment_url: str
    expires_at: datetime
    is_existing: bool


class VNPayReturnResponse(SQLModel):
    payment_status: PaymentStatus
    booking_id: UUID | None
    message: str
