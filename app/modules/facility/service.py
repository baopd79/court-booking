"""Business logic for facility module."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    CourtNotFoundError,
    DuplicateNameError,
    FacilityNotFoundError,
    ForbiddenError,
)
from app.modules.auth.models import User
from app.modules.facility.models import Court, Facility, PricingRule, Slot, SlotStatus
from app.modules.facility.pricing import find_price as _find_price
from app.modules.facility.repository import (
    CourtRepository,
    FacilityRepository,
    PricingRuleRepository,
    SlotRepository,
)
from app.modules.facility.schemas import (
    AvailabilityResponse,
    CourtAvailability,
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
    SlotAvailability,
    SlotRangeRequest,
    SlotUpdateResult,
)

_SLOT_DURATION = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _map_slot_status(status: SlotStatus) -> str:
    if status in (SlotStatus.held, SlotStatus.booked):
        return "unavailable"
    return status.value


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

    async def list(self, owner: User, page: int = 1, limit: int = 20) -> PaginatedFacilityResponse:
        items, total = await self._repo.list_by_tenant(owner.tenant_id, page=page, limit=limit)
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

    async def update(self, court_id: UUID, data: CourtUpdate, owner: User) -> CourtResponse:
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
        facility = await self._facility_repo.get_by_id(court.facility_id, include_deleted=True)
        if not facility or facility.tenant_id != owner.tenant_id:
            raise ForbiddenError()
        return court


class PricingRuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PricingRuleRepository(session)
        self._court_repo = CourtRepository(session)
        self._facility_repo = FacilityRepository(session)

    async def get_by_court(self, court_id: UUID, owner: User) -> list[PricingRuleResponse]:
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
        facility = await self._facility_repo.get_by_id(court.facility_id, include_deleted=True)
        if not facility or facility.tenant_id != owner.tenant_id:
            raise ForbiddenError()


class SlotService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._slot_repo = SlotRepository(session)
        self._court_repo = CourtRepository(session)
        self._facility_repo = FacilityRepository(session)
        self._pricing_repo = PricingRuleRepository(session)

    # ===== Availability (public) =====

    async def get_availability(
        self,
        facility_id: UUID,
        target_date: date,
        court_id: UUID | None = None,
    ) -> AvailabilityResponse:
        facility = await self._facility_repo.get_by_id(facility_id)
        if not facility:
            raise FacilityNotFoundError("Facility not found")

        courts = await self._court_repo.list_active_by_facility(facility_id, court_id=court_id)
        if not courts:
            return AvailabilityResponse(date=target_date, facility_id=facility_id, courts=[])

        court_ids = [c.id for c in courts]
        slots = await self._slot_repo.list_by_courts_and_date(court_ids, target_date)
        rules = await self._pricing_repo.list_by_courts(court_ids)

        # Group by court_id for O(1) lookup
        slots_by_court: dict[UUID, list[Slot]] = {c.id: [] for c in courts}
        for s in slots:
            slots_by_court[s.court_id].append(s)

        rules_by_court: dict[UUID, list[PricingRule]] = {c.id: [] for c in courts}
        for r in rules:
            rules_by_court[r.court_id].append(r)

        court_availability = []
        for court in courts:
            court_rules = rules_by_court[court.id]
            court_slots = [
                SlotAvailability(
                    id=s.id,
                    slot_start=s.slot_start,
                    slot_end=s.slot_end,
                    status=_map_slot_status(s.status),  # type: ignore[arg-type]
                    price=_find_price(s, court_rules, court.default_price),
                )
                for s in slots_by_court[court.id]
            ]
            court_availability.append(
                CourtAvailability(
                    id=court.id,
                    name=court.name,
                    sport_type=court.sport_type,
                    slots=court_slots,
                )
            )

        return AvailabilityResponse(
            date=target_date,
            facility_id=facility_id,
            courts=court_availability,
        )

    # ===== Owner: close / reopen slots =====

    async def close_slots(
        self, court_id: UUID, data: SlotRangeRequest, owner: User
    ) -> SlotUpdateResult:
        await self._verify_court_owned(court_id, owner)
        slot_start = datetime(
            data.date.year,
            data.date.month,
            data.date.day,
            data.start_time.hour,
            data.start_time.minute,
        )
        slot_end = datetime(
            data.date.year, data.date.month, data.date.day, data.end_time.hour, data.end_time.minute
        )
        updated = await self._slot_repo.update_status_in_range(
            court_id,
            slot_start,
            slot_end,
            from_status=SlotStatus.available,
            to_status=SlotStatus.closed,
        )
        await self._session.commit()
        return SlotUpdateResult(updated=updated)

    async def reopen_slots(
        self, court_id: UUID, data: SlotRangeRequest, owner: User
    ) -> SlotUpdateResult:
        await self._verify_court_owned(court_id, owner)
        slot_start = datetime(
            data.date.year,
            data.date.month,
            data.date.day,
            data.start_time.hour,
            data.start_time.minute,
        )
        slot_end = datetime(
            data.date.year, data.date.month, data.date.day, data.end_time.hour, data.end_time.minute
        )
        updated = await self._slot_repo.update_status_in_range(
            court_id,
            slot_start,
            slot_end,
            from_status=SlotStatus.closed,
            to_status=SlotStatus.available,
        )
        await self._session.commit()
        return SlotUpdateResult(updated=updated)

    # ===== Slot generation =====

    async def generate_for_court_on_date(self, court_id: UUID, target_date: date) -> int:
        """Generate hourly slots for a court on a given date from its pricing rules.

        Idempotent: ON CONFLICT DO NOTHING on (court_id, slot_start).
        Returns number of new slots inserted.
        """
        day_of_week = target_date.isoweekday() % 7  # 0=Sun, 1=Mon, ..., 6=Sat
        all_rules = await self._pricing_repo.list_by_court(court_id)
        day_rules = [r for r in all_rules if r.day_of_week == day_of_week]

        slots: list[Slot] = []
        for rule in day_rules:
            current = datetime.combine(target_date, rule.start_time)
            end = datetime.combine(target_date, rule.end_time)
            while current + _SLOT_DURATION <= end:
                slots.append(
                    Slot(
                        court_id=court_id,
                        slot_start=current,
                        slot_end=current + _SLOT_DURATION,
                        status=SlotStatus.available,
                    )
                )
                current += _SLOT_DURATION

        return await self._slot_repo.bulk_insert_ignore(slots)

    async def generate_for_all_courts_on_date(self, target_date: date) -> int:
        """Generate slots for all active courts on a date. Returns total inserted."""
        courts = await self._court_repo.list_all_active()
        total = 0
        for court in courts:
            total += await self.generate_for_court_on_date(court.id, target_date)
        return total

    async def generate_upcoming(self, days: int = 30) -> int:
        """Generate slots for the next `days` days. Called on app startup."""
        today = _utcnow().date()
        total = 0
        for i in range(days + 1):
            total += await self.generate_for_all_courts_on_date(today + timedelta(days=i))
        await self._session.commit()
        return total

    async def _verify_court_owned(self, court_id: UUID, owner: User) -> None:
        court = await self._court_repo.get_by_id(court_id)
        if not court:
            raise CourtNotFoundError("Court not found")
        facility = await self._facility_repo.get_by_id(court.facility_id, include_deleted=True)
        if not facility or facility.tenant_id != owner.tenant_id:
            raise ForbiddenError()
