"""HTTP routes for facility module."""

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.deps import CourtServiceDep, FacilityServiceDep, OwnerDep, PricingServiceDep
from app.modules.facility.schemas import (
    CourtCreate,
    CourtResponse,
    CourtUpdate,
    FacilityCreate,
    FacilityResponse,
    FacilityUpdate,
    PaginatedCourtResponse,
    PaginatedFacilityResponse,
    PricingRuleReplace,
    PricingRuleResponse,
)

router = APIRouter(tags=["facility"])


# ===== Facilities =====


@router.get("/facilities", response_model=PaginatedFacilityResponse)
async def list_facilities(
    owner: OwnerDep,
    facility_service: FacilityServiceDep,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> PaginatedFacilityResponse:
    return await facility_service.list(owner, page=page, limit=limit)


@router.post("/facilities", status_code=status.HTTP_201_CREATED, response_model=FacilityResponse)
async def create_facility(
    data: FacilityCreate,
    owner: OwnerDep,
    facility_service: FacilityServiceDep,
) -> FacilityResponse:
    return await facility_service.create(data, owner)


@router.get("/facilities/{facility_id}", response_model=FacilityResponse)
async def get_facility(
    facility_id: UUID,
    owner: OwnerDep,
    facility_service: FacilityServiceDep,
) -> FacilityResponse:
    return await facility_service.get(facility_id, owner)


@router.patch("/facilities/{facility_id}", response_model=FacilityResponse)
async def update_facility(
    facility_id: UUID,
    data: FacilityUpdate,
    owner: OwnerDep,
    facility_service: FacilityServiceDep,
) -> FacilityResponse:
    return await facility_service.update(facility_id, data, owner)


@router.delete("/facilities/{facility_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_facility(
    facility_id: UUID,
    owner: OwnerDep,
    facility_service: FacilityServiceDep,
) -> None:
    await facility_service.delete(facility_id, owner)


# ===== Courts =====


@router.get("/courts", response_model=PaginatedCourtResponse)
async def list_courts(
    owner: OwnerDep,
    court_service: CourtServiceDep,
    facility_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> PaginatedCourtResponse:
    return await court_service.list(owner, facility_id=facility_id, page=page, limit=limit)


@router.post("/courts", status_code=status.HTTP_201_CREATED, response_model=CourtResponse)
async def create_court(
    data: CourtCreate,
    owner: OwnerDep,
    court_service: CourtServiceDep,
) -> CourtResponse:
    return await court_service.create(data, owner)


@router.get("/courts/{court_id}", response_model=CourtResponse)
async def get_court(
    court_id: UUID,
    owner: OwnerDep,
    court_service: CourtServiceDep,
) -> CourtResponse:
    return await court_service.get(court_id, owner)


@router.patch("/courts/{court_id}", response_model=CourtResponse)
async def update_court(
    court_id: UUID,
    data: CourtUpdate,
    owner: OwnerDep,
    court_service: CourtServiceDep,
) -> CourtResponse:
    return await court_service.update(court_id, data, owner)


@router.delete("/courts/{court_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_court(
    court_id: UUID,
    owner: OwnerDep,
    court_service: CourtServiceDep,
) -> None:
    await court_service.delete(court_id, owner)


# ===== Pricing Rules =====


@router.get("/courts/{court_id}/pricing", response_model=list[PricingRuleResponse])
async def get_pricing(
    court_id: UUID,
    owner: OwnerDep,
    pricing_service: PricingServiceDep,
) -> list[PricingRuleResponse]:
    return await pricing_service.get_by_court(court_id, owner)


@router.put("/courts/{court_id}/pricing", response_model=list[PricingRuleResponse])
async def replace_pricing(
    court_id: UUID,
    data: PricingRuleReplace,
    owner: OwnerDep,
    pricing_service: PricingServiceDep,
) -> list[PricingRuleResponse]:
    return await pricing_service.replace(court_id, data, owner)
