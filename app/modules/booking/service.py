"""Business logic for booking module."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BookingNotFoundError,
    ForbiddenError,
    IdempotencyKeyReusedError,
    InvalidSlotsError,
    MissingIdempotencyKeyError,
    SlotNotAvailableError,
    TooManyPendingError,
)
from app.modules.auth.models import User, UserRole
from app.modules.booking.models import (
    Booking,
    BookingSlot,
    BookingStatus,
    BookingType,
)
from app.modules.booking.repository import (
    BookingRepository,
    BookingSlotRepository,
    IdempotencyRepository,
    SlotLockRepository,
)
from app.modules.booking.schemas import (
    BookingListItem,
    BookingResponse,
    CreateBookingRequest,
    PaginatedBookingResponse,
    SlotInfo,
    WalkInBookingRequest,
)
from app.modules.facility.models import PricingRule, Slot, SlotStatus
from app.modules.facility.repository import CourtRepository, PricingRuleRepository

# ===== State machine =====

ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.pending_payment: {
        BookingStatus.payment_processing,
        BookingStatus.expired,
        BookingStatus.cancelled,
    },
    BookingStatus.payment_processing: {
        BookingStatus.confirmed,
        BookingStatus.payment_failed,
    },
    BookingStatus.confirmed: {
        BookingStatus.in_use,
        BookingStatus.completed,
        BookingStatus.cancelled,
    },
    BookingStatus.in_use: {BookingStatus.completed},
    BookingStatus.completed: set(),
    BookingStatus.expired: set(),
    BookingStatus.payment_failed: set(),
    BookingStatus.cancelled: set(),
}

_HOLD_DURATION = timedelta(minutes=10)
_IDEMPOTENCY_TTL = timedelta(hours=24)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _request_hash(data: dict) -> str:
    import json
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()


def _find_price(slot: Slot, rules: list[PricingRule], default_price: Decimal) -> Decimal:
    slot_day = slot.slot_start.isoweekday() % 7  # 0=Sun
    slot_time = slot.slot_start.time()
    for rule in rules:
        if rule.day_of_week == slot_day and rule.start_time <= slot_time < rule.end_time:
            return rule.price
    return default_price


def _is_consecutive(slots: list[Slot]) -> bool:
    return all(slots[i].slot_start == slots[i - 1].slot_end for i in range(1, len(slots)))


class BookingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._booking_repo = BookingRepository(session)
        self._bs_repo = BookingSlotRepository(session)
        self._slot_repo = SlotLockRepository(session)
        self._idempotency_repo = IdempotencyRepository(session)
        self._court_repo = CourtRepository(session)
        self._pricing_repo = PricingRuleRepository(session)

    # ===== Reserve (online booking) =====

    async def create(
        self,
        data: CreateBookingRequest,
        customer: User,
        idempotency_key: str | None,
    ) -> BookingResponse:
        if not idempotency_key:
            raise MissingIdempotencyKeyError("Idempotency-Key header is required")

        body_hash = _request_hash({"court_id": str(data.court_id), "slot_ids": sorted(data.slot_ids)})

        # ===== Step 1: Idempotency check =====
        existing = await self._idempotency_repo.find(idempotency_key, customer.id)
        if existing:
            if existing.request_hash != body_hash:
                raise IdempotencyKeyReusedError()
            if existing.response_body:
                return BookingResponse.model_validate(existing.response_body)
            # Key reserved but no response yet (concurrent in-flight request)
            raise IdempotencyKeyReusedError()

        reserved = await self._idempotency_repo.reserve(
            key=idempotency_key,
            user_id=customer.id,
            endpoint="POST /bookings",
            request_hash=body_hash,
            expires_at=_utcnow() + _IDEMPOTENCY_TTL,
        )
        if not reserved:
            raise IdempotencyKeyReusedError()

        # ===== Step 2: Lock slots FOR UPDATE =====
        slots = await self._slot_repo.get_by_ids_for_update(data.slot_ids, data.court_id)

        if len(slots) != len(data.slot_ids):
            raise InvalidSlotsError("Some slots not found or don't belong to this court")

        unavailable = [s.id for s in slots if s.status != SlotStatus.available]
        if unavailable:
            raise SlotNotAvailableError(unavailable)

        slots_sorted = sorted(slots, key=lambda s: s.slot_start)
        if not _is_consecutive(slots_sorted):
            raise InvalidSlotsError("Slots must be consecutive")

        if await self._booking_repo.count_pending(customer.id) >= 5:
            raise TooManyPendingError("Too many pending bookings")

        # ===== Step 3: Calculate prices =====
        court = await self._court_repo.get_by_id(data.court_id)
        rules = await self._pricing_repo.list_by_court(data.court_id)
        slot_prices = [_find_price(s, rules, court.default_price) for s in slots_sorted]
        total = sum(slot_prices, Decimal("0"))

        # ===== Step 4: Create booking + update slots (atomic) =====
        hold_expires = _utcnow() + _HOLD_DURATION
        booking = Booking(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            court_id=data.court_id,
            status=BookingStatus.pending_payment,
            total_amount=total,
            booking_type=BookingType.online,
            hold_expires_at=hold_expires,
        )
        booking = await self._booking_repo.create(booking)

        booking_slots = [
            BookingSlot(
                booking_id=booking.id,
                slot_id=slots_sorted[i].id,
                price_at_booking=slot_prices[i],
            )
            for i in range(len(slots_sorted))
        ]
        await self._bs_repo.bulk_create(booking_slots)
        await self._slot_repo.mark_held(slots_sorted, booking.id, hold_expires)

        # ===== Step 5: Commit + save idempotency response =====
        await self._session.commit()

        response = self._build_response(booking, slots_sorted, slot_prices)
        await self._idempotency_repo.save_response(
            idempotency_key, customer.id, 201, response.model_dump(mode="json")
        )
        await self._session.commit()
        return response

    # ===== Walk-in (owner, immediate confirm) =====

    async def create_walkin(
        self,
        data: WalkInBookingRequest,
        owner: User,
        idempotency_key: str | None,
    ) -> BookingResponse:
        if not idempotency_key:
            raise MissingIdempotencyKeyError("Idempotency-Key header is required")

        body_hash = _request_hash({
            "court_id": str(data.court_id),
            "slot_ids": sorted(data.slot_ids),
            "walkin_name": data.walkin_name,
            "walkin_phone": data.walkin_phone,
        })

        existing = await self._idempotency_repo.find(idempotency_key, owner.id)
        if existing:
            if existing.request_hash != body_hash:
                raise IdempotencyKeyReusedError()
            if existing.response_body:
                return BookingResponse.model_validate(existing.response_body)
            raise IdempotencyKeyReusedError()

        reserved = await self._idempotency_repo.reserve(
            key=idempotency_key,
            user_id=owner.id,
            endpoint="POST /bookings/walk-in",
            request_hash=body_hash,
            expires_at=_utcnow() + _IDEMPOTENCY_TTL,
        )
        if not reserved:
            raise IdempotencyKeyReusedError()

        # Verify court belongs to owner's tenant
        court = await self._court_repo.get_by_id(data.court_id)
        if not court:
            from app.core.exceptions import CourtNotFoundError
            raise CourtNotFoundError("Court not found")

        slots = await self._slot_repo.get_by_ids_for_update(data.slot_ids, data.court_id)
        if len(slots) != len(data.slot_ids):
            raise InvalidSlotsError("Some slots not found or don't belong to this court")

        unavailable = [s.id for s in slots if s.status != SlotStatus.available]
        if unavailable:
            raise SlotNotAvailableError(unavailable)

        slots_sorted = sorted(slots, key=lambda s: s.slot_start)
        if not _is_consecutive(slots_sorted):
            raise InvalidSlotsError("Slots must be consecutive")

        rules = await self._pricing_repo.list_by_court(data.court_id)
        slot_prices = [_find_price(s, rules, court.default_price) for s in slots_sorted]
        total = sum(slot_prices, Decimal("0"))

        booking = Booking(
            tenant_id=owner.tenant_id,
            customer_id=None,
            court_id=data.court_id,
            status=BookingStatus.confirmed,
            total_amount=total,
            booking_type=BookingType.walkin,
            walkin_name=data.walkin_name,
            walkin_phone=data.walkin_phone,
            hold_expires_at=None,
        )
        booking = await self._booking_repo.create(booking)

        booking_slots = [
            BookingSlot(
                booking_id=booking.id,
                slot_id=slots_sorted[i].id,
                price_at_booking=slot_prices[i],
            )
            for i in range(len(slots_sorted))
        ]
        await self._bs_repo.bulk_create(booking_slots)
        await self._slot_repo.mark_booked(slots_sorted, booking.id)

        await self._session.commit()

        response = self._build_response(booking, slots_sorted, slot_prices)
        await self._idempotency_repo.save_response(
            idempotency_key, owner.id, 201, response.model_dump(mode="json")
        )
        await self._session.commit()
        return response

    # ===== Read endpoints =====

    async def get(self, booking_id: UUID, user: User) -> BookingResponse:
        booking = await self._booking_repo.get_by_id(booking_id)
        if not booking:
            raise BookingNotFoundError()

        if user.role == UserRole.customer:
            if booking.customer_id != user.id:
                raise ForbiddenError()
        else:
            if booking.tenant_id != user.tenant_id:
                raise ForbiddenError()

        booking_slots = await self._bs_repo.list_by_booking(booking_id)
        slot_ids = [bs.slot_id for bs in booking_slots]
        slots = await self._slot_repo.get_by_ids(slot_ids)
        slot_map = {s.id: s for s in slots}
        bs_sorted = sorted(booking_slots, key=lambda bs: slot_map[bs.slot_id].slot_start)
        slots_sorted = [slot_map[bs.slot_id] for bs in bs_sorted]
        prices = [bs.price_at_booking for bs in bs_sorted]
        return self._build_response(booking, slots_sorted, prices)

    async def list_mine(
        self, customer: User, page: int = 1, limit: int = 20
    ) -> PaginatedBookingResponse:
        items, total = await self._booking_repo.list_by_customer(
            customer.id, page=page, limit=limit
        )
        return PaginatedBookingResponse(
            items=[BookingListItem.model_validate(b) for b in items],
            total=total,
            page=page,
            limit=limit,
            has_next=(page * limit) < total,
        )

    async def list_all(
        self, owner: User, page: int = 1, limit: int = 20
    ) -> PaginatedBookingResponse:
        items, total = await self._booking_repo.list_by_tenant(
            owner.tenant_id, page=page, limit=limit
        )
        return PaginatedBookingResponse(
            items=[BookingListItem.model_validate(b) for b in items],
            total=total,
            page=page,
            limit=limit,
            has_next=(page * limit) < total,
        )

    # ===== Internal helpers =====

    @staticmethod
    def _build_response(
        booking: Booking, slots: list[Slot], prices: list[Decimal]
    ) -> BookingResponse:
        slot_infos = [
            SlotInfo(
                id=slots[i].id,
                slot_start=slots[i].slot_start,
                slot_end=slots[i].slot_end,
                price=prices[i],
            )
            for i in range(len(slots))
        ]
        return BookingResponse(
            id=booking.id,
            status=booking.status,
            court_id=booking.court_id,
            booking_type=booking.booking_type,
            total_amount=booking.total_amount,
            hold_expires_at=booking.hold_expires_at,
            slots=slot_infos,
            created_at=booking.created_at,
            walkin_name=booking.walkin_name,
            walkin_phone=booking.walkin_phone,
        )
