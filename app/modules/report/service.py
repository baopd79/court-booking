"""Business logic for report module."""

from datetime import date

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.report.repository import ReportRepository
from app.modules.report.schemas import RevenueReportResponse


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = ReportRepository(session)

    async def revenue_report(
        self,
        owner: User,
        from_date: date,
        to_date: date,
        facility_id: str | None = None,
    ) -> RevenueReportResponse:
        from uuid import UUID
        fac_id = UUID(facility_id) if facility_id else None

        total_revenue, total_bookings, by_court, by_day = await self._repo.revenue(
            tenant_id=owner.tenant_id,
            from_date=from_date,
            to_date=to_date,
            facility_id=fac_id,
        )
        return RevenueReportResponse(
            from_date=from_date,
            to_date=to_date,
            total_revenue=total_revenue,
            total_bookings=total_bookings,
            by_court=by_court,
            by_day=by_day,
        )
