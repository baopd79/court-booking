"""Database query layer for booking module."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func as sa_func
from sqlmodel import col, func, select

from app.modules.booking.models import (
    Booking,
    BookingSlot,
    BookingStatus,
    IdempotencyKey,
)
from app.modules.facility.models import Slot, SlotStatus


class BookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, booking: Booking) -> Booking:
        self._session.add(booking)
        await self._session.flush()
        await self._session.refresh(booking)
        return booking

    async def get_by_id(self, booking_id: UUID) -> Booking | None:
        result = await self._session.execute(select(Booking).where(Booking.id == booking_id))
        return result.scalar_one_or_none()

    async def list_by_customer(
        self, customer_id: UUID, *, page: int = 1, limit: int = 20
    ) -> tuple[list[Booking], int]:
        base = select(Booking).where(Booking.customer_id == customer_id)
        total = (
            await self._session.execute(
                select(func.count(Booking.id)).where(Booking.customer_id == customer_id)
            )
        ).scalar_one()
        items = list(
            (
                await self._session.execute(
                    base.order_by(Booking.created_at.desc()).offset((page - 1) * limit).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return items, total

    async def list_by_tenant(
        self, tenant_id: UUID, *, page: int = 1, limit: int = 20
    ) -> tuple[list[Booking], int]:
        base = select(Booking).where(Booking.tenant_id == tenant_id)
        total = (
            await self._session.execute(
                select(func.count(Booking.id)).where(Booking.tenant_id == tenant_id)
            )
        ).scalar_one()
        items = list(
            (
                await self._session.execute(
                    base.order_by(Booking.created_at.desc()).offset((page - 1) * limit).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return items, total

    async def get_by_id_for_customer_locked(
        self, booking_id: UUID, customer_id: UUID
    ) -> Booking | None:
        result = await self._session.execute(
            select(Booking)
            .where(Booking.id == booking_id, Booking.customer_id == customer_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_id_locked(self, booking_id: UUID) -> Booking | None:
        result = await self._session.execute(
            select(Booking).where(Booking.id == booking_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def save(self, booking: Booking) -> Booking:
        self._session.add(booking)
        await self._session.flush()
        await self._session.refresh(booking)
        return booking

    async def list_expired_holds(self, now: datetime) -> list[Booking]:
        """Find pending_payment bookings whose hold has expired. FOR UPDATE."""
        result = await self._session.execute(
            select(Booking)
            .where(
                Booking.status == BookingStatus.pending_payment,
                col(Booking.hold_expires_at).isnot(None),
                Booking.hold_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def list_completable(self, now: datetime) -> list[Booking]:
        """Find confirmed/in_use bookings whose last slot has ended. FOR UPDATE."""
        # Subquery: booking IDs where max(slot_end) < now — GROUP BY prevents FOR UPDATE here
        completable_ids = (
            select(BookingSlot.booking_id)
            .join(Slot, BookingSlot.slot_id == Slot.id)
            .group_by(BookingSlot.booking_id)
            .having(sa_func.max(Slot.slot_end) < now)
        )
        # Outer query locks the Booking rows (no GROUP BY → FOR UPDATE allowed)
        result = await self._session.execute(
            select(Booking)
            .where(
                Booking.status.in_([BookingStatus.confirmed, BookingStatus.in_use]),  # type: ignore[union-attr]
                Booking.id.in_(completable_ids),  # type: ignore[union-attr]
            )
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def count_pending(self, customer_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count(Booking.id)).where(
                Booking.customer_id == customer_id,
                Booking.status == BookingStatus.pending_payment,
            )
        )
        return result.scalar_one()


class BookingSlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(self, booking_slots: list[BookingSlot]) -> list[BookingSlot]:
        for bs in booking_slots:
            self._session.add(bs)
        await self._session.flush()
        for bs in booking_slots:
            await self._session.refresh(bs)
        return booking_slots

    async def list_by_booking(self, booking_id: UUID) -> list[BookingSlot]:
        result = await self._session.execute(
            select(BookingSlot).where(BookingSlot.booking_id == booking_id)
        )
        return list(result.scalars().all())


class SlotLockRepository:
    """Slot queries that require row-level locking. Separate from SlotRepository
    to make the locking intent explicit at the call site."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_ids_for_update(self, slot_ids: list[int], court_id: UUID) -> list[Slot]:
        """SELECT ... FOR UPDATE ORDER BY id — prevents deadlock on multi-row lock."""
        result = await self._session.execute(
            select(Slot)
            .where(
                col(Slot.id).in_(slot_ids),
                Slot.court_id == court_id,
                col(Slot.deleted_at).is_(None) if hasattr(Slot, "deleted_at") else True,
            )
            .order_by(Slot.id)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def get_by_ids(self, slot_ids: list[int]) -> list[Slot]:
        """Plain SELECT for read-only purposes (building responses)."""
        result = await self._session.execute(
            select(Slot).where(col(Slot.id).in_(slot_ids)).order_by(Slot.slot_start)
        )
        return list(result.scalars().all())

    async def mark_held(self, slots: list[Slot], booking_id: UUID, held_until: datetime) -> None:
        for slot in slots:
            slot.status = SlotStatus.held
            slot.held_until = held_until
            slot.held_by_booking_id = booking_id
            self._session.add(slot)
        await self._session.flush()

    async def mark_booked(self, slots: list[Slot], booking_id: UUID) -> None:
        for slot in slots:
            slot.status = SlotStatus.booked
            slot.held_until = None
            slot.held_by_booking_id = booking_id
            self._session.add(slot)
        await self._session.flush()

    async def mark_available(self, slots: list[Slot]) -> None:
        for slot in slots:
            slot.status = SlotStatus.available
            slot.held_until = None
            slot.held_by_booking_id = None
            self._session.add(slot)
        await self._session.flush()


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, key: str, user_id: UUID) -> IdempotencyKey | None:
        result = await self._session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.key == key,
                IdempotencyKey.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def reserve(
        self,
        key: str,
        user_id: UUID,
        endpoint: str,
        request_hash: str,
        expires_at: datetime,
    ) -> bool:
        """INSERT ... ON CONFLICT DO NOTHING. Returns True if we own the key."""
        stmt = (
            pg_insert(IdempotencyKey)
            .values(
                key=key,
                user_id=user_id,
                endpoint=endpoint,
                request_hash=request_hash,
                expires_at=expires_at,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
            .on_conflict_do_nothing(index_elements=["key", "user_id"])
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0

    async def save_response(self, key: str, user_id: UUID, status: int, body: dict) -> None:
        record = await self.find(key, user_id)
        if record:
            record.response_status = status
            record.response_body = body
            self._session.add(record)
            await self._session.flush()
