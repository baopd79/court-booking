"""FastAPI routes for report module."""

from datetime import date

from fastapi import APIRouter, Query

from app.core.deps import OwnerDep, ReportServiceDep
from app.modules.report.schemas import RevenueReportResponse

router = APIRouter(tags=["report"])


@router.get("/reports/revenue", response_model=RevenueReportResponse)
async def revenue_report(
    owner: OwnerDep,
    report_service: ReportServiceDep,
    from_date: date = Query(..., description="Start date (inclusive), YYYY-MM-DD"),
    to_date: date = Query(..., description="End date (inclusive), YYYY-MM-DD"),
    facility_id: str | None = Query(None, description="Filter by facility UUID"),
) -> RevenueReportResponse:
    return await report_service.revenue_report(owner, from_date, to_date, facility_id)
