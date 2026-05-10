"""Business logic for facility module."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    CourtNotFoundError,
    DuplicateNameError,
    FacilityNotFoundError,
    ForbiddenError,
)
from app.modules.auth.models import User
from app.modules.facility.models import Court, Facility, PricingRule
from app.modules.facility.repository import (
    CourtRepository,
    FacilityRepository,
    PricingRuleRepository,
)
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


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class FacilityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = FacilityRepository(session)

    async def create(self, data: FacilityCreate, owner: User) -> FacilityResponse:
        if await self._repo.get_by_name(data.name, owner.tenant_id):
            raise DuplicateNameError("A facility with this name already exists")
        facility = Facility(
            tenant_id=owner.tenant_id,
            name=data.name,
            address=data.address,
        )
        facility = await self._repo.create(facility)
        await self._session.commit()
        return FacilityResponse.model_validate(facility)

    async def list(
        self, owner: User, page: int = 1, limit: int = 20
    ) -> PaginatedFacilityResponse:
        items, total = await self._repo.list_by_tenant(
            owner.tenant_id, page=page, limit=limit
        )
        return PaginatedFacilityResponse(
            items=[FacilityResponse.model_validate(f) for f in items],
            total=total,
            page=page,
            limit=limit,
            has_next=(page * limit) < total,
        )

    async def get(self, facility_id: UUID, owner: User) -> FacilityResponse:
        return FacilityResponse.model_validate(await self._get_owned(facility_id, owner))

    async def update(
        self, facility_id: UUID, data: FacilityUpdate, owner: User
    ) -> FacilityResponse:
        facility = await self._get_owned(facility_id, owner)
        if (
            data.name
            and data.name != facility.name
            and await self._repo.get_by_name(data.name, owner.tenant_id, exclude_id=facility_id)
        ):
            raise DuplicateNameError("A facility with this name already exists")
        for key, val in data.model_dump(exclude_unset=True).items():
            setattr(facility, key, val)
        facility = await self._repo.save(facility)
        await self._session.commit()
        return FacilityResponse.model_validate(facility)

    async def delete(self, facility_id: UUID, owner: User) -> None:
        facility = await self._get_owned(facility_id, owner)
        facility.deleted_at = _utcnow()  # type: ignore[assignment]
        await self._repo.save(facility)
        await self._session.commit()

    async def _get_owned(self, facility_id: UUID, owner: User) -> Facility:
        facility = await self._repo.get_by_id(facility_id)
        if not facility:
            raise FacilityNotFoundError("Facility not found")
        if facility.tenant_id != owner.tenant_id:
            raise ForbiddenError()
        return facility


class CourtService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CourtRepository(session)
        self._facility_repo = FacilityRepository(session)

    async def create(self, data: CourtCreate, owner: User) -> CourtResponse:
        facility = await self._facility_repo.get_by_id(data.facility_id)
        if not facility:
            raise FacilityNotFoundError("Facility not found")
        if facility.tenant_id != owner.tenant_id:
            raise ForbiddenError()
        if await self._repo.get_by_name(data.name, data.facility_id):
            raise DuplicateNameError("A court with this name already exists in this facility")

        court = Court(
            facility_id=data.facility_id,
            name=data.name,
            sport_type=data.sport_type,
            default_price=data.default_price,
        )
        court = await self._repo.create(court)
        await self._session.commit()
        return CourtResponse.model_validate(court)

    async def list(
        self,
        owner: User,
        facility_id: UUID | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedCourtResponse:
        items, total = await self._repo.list_by_tenant(
            owner.tenant_id, facility_id=facility_id, page=page, limit=limit
        )
        return PaginatedCourtResponse(
            items=[CourtResponse.model_validate(c) for c in items],
            total=total,
            page=page,
            limit=limit,
            has_next=(page * limit) < total,
        )

    async def get(self, court_id: UUID, owner: User) -> CourtResponse:
        return CourtResponse.model_validate(await self._get_owned(court_id, owner))

    async def update(
        self, court_id: UUID, data: CourtUpdate, owner: User
    ) -> CourtResponse:
        court = await self._get_owned(court_id, owner)
        if (
            data.name
            and data.name != court.name
            and await self._repo.get_by_name(data.name, court.facility_id, exclude_id=court_id)
        ):
            raise DuplicateNameError("A court with this name already exists in this facility")
        for key, val in data.model_dump(exclude_unset=True).items():
            setattr(court, key, val)
        court = await self._repo.save(court)
        await self._session.commit()
        return CourtResponse.model_validate(court)

    async def delete(self, court_id: UUID, owner: User) -> None:
        court = await self._get_owned(court_id, owner)
        court.deleted_at = _utcnow()  # type: ignore[assignment]
        await self._repo.save(court)
        await self._session.commit()

    async def _get_owned(self, court_id: UUID, owner: User) -> Court:
        court = await self._repo.get_by_id(court_id)
        if not court:
            raise CourtNotFoundError("Court not found")
        # Verify ownership via facility → tenant chain
        facility = await self._facility_repo.get_by_id(
            court.facility_id, include_deleted=True
        )
        if not facility or facility.tenant_id != owner.tenant_id:
            raise ForbiddenError()
        return court


class PricingRuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PricingRuleRepository(session)
        self._court_repo = CourtRepository(session)
        self._facility_repo = FacilityRepository(session)

    async def get_by_court(
        self, court_id: UUID, owner: User
    ) -> list[PricingRuleResponse]:
        await self._verify_owned(court_id, owner)
        rules = await self._repo.list_by_court(court_id)
        return [PricingRuleResponse.model_validate(r) for r in rules]

    async def replace(
        self, court_id: UUID, data: PricingRuleReplace, owner: User
    ) -> list[PricingRuleResponse]:
        await self._verify_owned(court_id, owner)
        # TODO(facility): validate no-overlap, no-gap between rules (deferred)
        await self._repo.delete_by_court(court_id)
        new_rules = [
            PricingRule(
                court_id=court_id,
                day_of_week=rule.day_of_week,
                start_time=rule.start_time,
                end_time=rule.end_time,
                price=rule.price,
            )
            for rule in data.rules
        ]
        created = await self._repo.bulk_create(new_rules)
        await self._session.commit()
        return [PricingRuleResponse.model_validate(r) for r in created]

    async def _verify_owned(self, court_id: UUID, owner: User) -> None:
        court = await self._court_repo.get_by_id(court_id)
        if not court:
            raise CourtNotFoundError("Court not found")
        facility = await self._facility_repo.get_by_id(
            court.facility_id, include_deleted=True
        )
        if not facility or facility.tenant_id != owner.tenant_id:
            raise ForbiddenError()
