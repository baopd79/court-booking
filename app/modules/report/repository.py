"""Aggregate queries for report module."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.modules.booking.models import Booking
from app.modules.facility.models import Court, Facility
from app.modules.payment.models import Payment, PaymentStatus
from app.modules.report.schemas import CourtRevenue, DailyRevenue


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def revenue(
        self,
        tenant_id: UUID,
        from_date: date,
        to_date: date,
        facility_id: UUID | None = None,
    ) -> tuple[Decimal, int, list[CourtRevenue], list[DailyRevenue]]:
        from_dt = datetime(from_date.year, from_date.month, from_date.day)
        to_dt = datetime(to_date.year, to_date.month, to_date.day) + timedelta(days=1)

        base_filters = [
            Payment.status == PaymentStatus.success,
            Facility.tenant_id == tenant_id,
            Payment.paid_at >= from_dt,
            Payment.paid_at < to_dt,
        ]
        if facility_id:
            base_filters.append(Facility.id == facility_id)

        def _base(stmt):
            return (
                stmt.join(Booking, Payment.booking_id == Booking.id)
                .join(Court, Booking.court_id == Court.id)
                .join(Facility, Court.facility_id == Facility.id)
                .where(*base_filters)
            )

        # ===== Total =====
        total_row = (
            await self._session.execute(
                _base(
                    select(
                        func.coalesce(func.sum(Payment.amount), 0).label("revenue"),
                        func.count(Payment.id).label("bookings"),
                    )
                )
            )
        ).one()
        total_revenue = Decimal(str(total_row.revenue))
        total_bookings = total_row.bookings

        # ===== By court =====
        court_rows = (
            await self._session.execute(
                _base(
                    select(
                        Court.id,
                        Court.name,
                        Court.sport_type,
                        func.sum(Payment.amount).label("revenue"),
                        func.count(Payment.id).label("bookings"),
                    )
                )
                .group_by(Court.id, Court.name, Court.sport_type)
                .order_by(func.sum(Payment.amount).desc())
            )
        ).all()

        by_court = [
            CourtRevenue(
                court_id=row.id,
                court_name=row.name,
                sport_type=row.sport_type,
                revenue=Decimal(str(row.revenue)),
                bookings=row.bookings,
            )
            for row in court_rows
        ]

        # ===== By day =====
        day_rows = (
            await self._session.execute(
                _base(
                    select(
                        func.date(Payment.paid_at).label("day"),
                        func.sum(Payment.amount).label("revenue"),
                        func.count(Payment.id).label("bookings"),
                    )
                )
                .group_by(func.date(Payment.paid_at))
                .order_by(func.date(Payment.paid_at))
            )
        ).all()

        by_day = [
            DailyRevenue(
                date=row.day,
                revenue=Decimal(str(row.revenue)),
                bookings=row.bookings,
            )
            for row in day_rows
        ]

        return total_revenue, total_bookings, by_court, by_day
