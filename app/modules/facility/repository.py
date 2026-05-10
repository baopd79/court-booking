"""Database query layer for facility module.

Pattern: mỗi repository nhận session, KHÔNG commit — service layer lo commit.
Soft-delete: query mặc định exclude deleted (deleted_at IS NULL).
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, func, select

from app.modules.facility.models import Court, Facility, PricingRule


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

        # Count
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        # Items
        items_stmt = base.order_by(Facility.name).offset((page - 1) * limit).limit(limit)
        result = await self._session.execute(items_stmt)
        items = list(result.scalars().all())
        return items, total

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

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        items_stmt = base.order_by(Court.name).offset((page - 1) * limit).limit(limit)
        result = await self._session.execute(items_stmt)
        items = list(result.scalars().all())
        return items, total

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
        existing = await self.list_by_court(court_id)
        for rule in existing:
            await self._session.delete(rule)
        await self._session.flush()
        return len(existing)

    async def bulk_create(self, rules: list[PricingRule]) -> list[PricingRule]:
        """Insert multiple pricing rules in a single flush."""
        for rule in rules:
            self._session.add(rule)
        await self._session.flush()
        for rule in rules:
            await self._session.refresh(rule)
        return rules
