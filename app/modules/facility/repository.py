"""Database query layer for facility module.

Pattern: mỗi repository nhận session, KHÔNG commit — service layer lo commit.
Soft-delete: query mặc định exclude deleted (deleted_at IS NULL).
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, func, select

from app.modules.facility.models import Court, Facility, PricingRule, Slot, SlotStatus


class FacilityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, facility: Facility) -> Facility:
        self._session.add(facility)
        await self._session.flush()
        await self._session.refresh(facility)
        return facility

    async def get_by_id(
        self, facility_id: UUID, *, include_deleted: bool = False
    ) -> Facility | None:
        stmt = select(Facility).where(Facility.id == facility_id)
        if not include_deleted:
            stmt = stmt.where(col(Facility.deleted_at).is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        page: int = 1,
        limit: int = 20,
        include_deleted: bool = False,
    ) -> tuple[list[Facility], int]:
        """Return (items, total) for pagination."""
        base = select(Facility).where(Facility.tenant_id == tenant_id)
        if not include_deleted:
            base = base.where(col(Facility.deleted_at).is_(None))

        count_stmt = select(func.count(Facility.id)).where(
            Facility.tenant_id == tenant_id,
            *([] if include_deleted else [col(Facility.deleted_at).is_(None)]),
        )
        total = (await self._session.execute(count_stmt)).scalar_one()

        items_stmt = base.order_by(Facility.name).offset((page - 1) * limit).limit(limit)
        result = await self._session.execute(items_stmt)
        items = list(result.scalars().all())
        return items, total

    async def get_by_name(
        self, name: str, tenant_id: UUID, *, exclude_id: UUID | None = None
    ) -> Facility | None:
        """Check name uniqueness within tenant (exclude self for update check)."""
        stmt = select(Facility).where(
            Facility.tenant_id == tenant_id,
            Facility.name == name,
            col(Facility.deleted_at).is_(None),
        )
        if exclude_id:
            stmt = stmt.where(Facility.id != exclude_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, facility: Facility) -> Facility:
        """Persist changes to an already-tracked facility (update / soft-delete)."""
        self._session.add(facility)
        await self._session.flush()
        await self._session.refresh(facility)
        return facility


class CourtRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, court: Court) -> Court:
        self._session.add(court)
        await self._session.flush()
        await self._session.refresh(court)
        return court

    async def get_by_id(self, court_id: UUID, *, include_deleted: bool = False) -> Court | None:
        stmt = select(Court).where(Court.id == court_id)
        if not include_deleted:
            stmt = stmt.where(col(Court.deleted_at).is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_for_tenant(self, court_id: UUID, tenant_id: UUID) -> Court | None:
        """Fetch court only if it belongs to the given tenant (via facility join)."""
        stmt = (
            select(Court)
            .join(Facility, Court.facility_id == Facility.id)
            .where(
                Court.id == court_id,
                Facility.tenant_id == tenant_id,
                col(Court.deleted_at).is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_facility(
        self,
        facility_id: UUID,
        *,
        page: int = 1,
        limit: int = 20,
        include_deleted: bool = False,
    ) -> tuple[list[Court], int]:
        """Return (items, total) for pagination."""
        base = select(Court).where(Court.facility_id == facility_id)
        if not include_deleted:
            base = base.where(col(Court.deleted_at).is_(None))

        count_stmt = select(func.count(Court.id)).where(
            Court.facility_id == facility_id,
            *([] if include_deleted else [col(Court.deleted_at).is_(None)]),
        )
        total = (await self._session.execute(count_stmt)).scalar_one()

        items_stmt = base.order_by(Court.name).offset((page - 1) * limit).limit(limit)
        result = await self._session.execute(items_stmt)
        items = list(result.scalars().all())
        return items, total

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        facility_id: UUID | None = None,
        page: int = 1,
        limit: int = 20,
        include_deleted: bool = False,
    ) -> tuple[list[Court], int]:
        """List courts for a tenant, optionally filtered by facility."""
        base = (
            select(Court)
            .join(Facility, Court.facility_id == Facility.id)
            .where(Facility.tenant_id == tenant_id)
        )
        if facility_id:
            base = base.where(Court.facility_id == facility_id)
        if not include_deleted:
            base = base.where(col(Court.deleted_at).is_(None))

        count_stmt = (
            select(func.count(Court.id))
            .select_from(Court)
            .join(Facility, Court.facility_id == Facility.id)
            .where(Facility.tenant_id == tenant_id)
        )
        if facility_id:
            count_stmt = count_stmt.where(Court.facility_id == facility_id)
        if not include_deleted:
            count_stmt = count_stmt.where(col(Court.deleted_at).is_(None))

        total = (await self._session.execute(count_stmt)).scalar_one()
        items_stmt = base.order_by(Court.name).offset((page - 1) * limit).limit(limit)
        result = await self._session.execute(items_stmt)
        return list(result.scalars().all()), total

    async def get_by_name(
        self, name: str, facility_id: UUID, *, exclude_id: UUID | None = None
    ) -> Court | None:
        """Check name uniqueness within facility (exclude self for update check)."""
        stmt = select(Court).where(
            Court.facility_id == facility_id,
            Court.name == name,
            col(Court.deleted_at).is_(None),
        )
        if exclude_id:
            stmt = stmt.where(Court.id != exclude_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_by_facility(
        self, facility_id: UUID, *, court_id: UUID | None = None
    ) -> list[Court]:
        """Active (non-deleted) courts for a facility. For availability query."""
        stmt = select(Court).where(
            Court.facility_id == facility_id,
            col(Court.deleted_at).is_(None),
        )
        if court_id:
            stmt = stmt.where(Court.id == court_id)
        stmt = stmt.order_by(Court.name)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_active(self) -> list[Court]:
        """All non-deleted courts across all non-deleted facilities. For slot generation."""
        stmt = (
            select(Court)
            .join(Facility, Court.facility_id == Facility.id)
            .where(col(Court.deleted_at).is_(None), col(Facility.deleted_at).is_(None))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save(self, court: Court) -> Court:
        self._session.add(court)
        await self._session.flush()
        await self._session.refresh(court)
        return court


class PricingRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_court(self, court_id: UUID) -> list[PricingRule]:
        stmt = (
            select(PricingRule)
            .where(PricingRule.court_id == court_id)
            .order_by(PricingRule.day_of_week, PricingRule.start_time)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_court(self, court_id: UUID) -> int:
        """Hard-delete all pricing rules for a court. Returns number of rows deleted."""
        result = await self._session.execute(
            delete(PricingRule).where(PricingRule.court_id == court_id)
        )
        await self._session.flush()
        return result.rowcount

    async def bulk_create(self, rules: list[PricingRule]) -> list[PricingRule]:
        """Insert multiple pricing rules in a single flush."""
        for rule in rules:
            self._session.add(rule)
        await self._session.flush()
        for rule in rules:
            await self._session.refresh(rule)
        return rules

    async def list_by_courts(self, court_ids: list[UUID]) -> list[PricingRule]:
        """All pricing rules for multiple courts. Used by slot generation."""
        if not court_ids:
            return []
        stmt = (
            select(PricingRule)
            .where(PricingRule.court_id.in_(court_ids))  # type: ignore[union-attr]
            .order_by(PricingRule.court_id, PricingRule.day_of_week, PricingRule.start_time)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class SlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert_ignore(self, slots: list[Slot]) -> int:
        """INSERT ... ON CONFLICT DO NOTHING. Returns rows inserted."""
        if not slots:
            return 0
        values = [
            {
                "court_id": s.court_id,
                "slot_start": s.slot_start,
                "slot_end": s.slot_end,
                "status": s.status,
            }
            for s in slots
        ]
        stmt = (
            pg_insert(Slot)
            .values(values)
            .on_conflict_do_nothing(index_elements=["court_id", "slot_start"])
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def list_by_courts_and_date(self, court_ids: list[UUID], target_date: date) -> list[Slot]:
        """All slots for a set of courts on a given date, ordered by court + start."""
        if not court_ids:
            return []
        day_start = datetime(target_date.year, target_date.month, target_date.day)
        day_end = datetime(target_date.year, target_date.month, target_date.day + 1)
        stmt = (
            select(Slot)
            .where(
                Slot.court_id.in_(court_ids),  # type: ignore[union-attr]
                Slot.slot_start >= day_start,
                Slot.slot_start < day_end,
            )
            .order_by(Slot.court_id, Slot.slot_start)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status_in_range(
        self,
        court_id: UUID,
        slot_start_gte: datetime,
        slot_end_lte: datetime,
        from_status: SlotStatus,
        to_status: SlotStatus,
    ) -> int:
        """Bulk update slot status within a time range. Only updates slots in from_status."""
        stmt = (
            update(Slot)
            .where(
                Slot.court_id == court_id,
                Slot.slot_start >= slot_start_gte,
                Slot.slot_end <= slot_end_lte,
                Slot.status == from_status,
            )
            .values(status=to_status)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount
