"""Response schemas for report module."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlmodel import SQLModel

from app.modules.facility.models import SportType


class CourtRevenue(SQLModel):
    court_id: UUID
    court_name: str
    sport_type: SportType
    revenue: Decimal
    bookings: int


class DailyRevenue(SQLModel):
    date: date
    revenue: Decimal
    bookings: int


class RevenueReportResponse(SQLModel):
    from_date: date
    to_date: date
    total_revenue: Decimal
    total_bookings: int
    by_court: list[CourtRevenue]
    by_day: list[DailyRevenue]
