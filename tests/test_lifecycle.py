"""Integration tests for Slice 7 — Lifecycle (cancel, force-cancel, check-in, cron jobs)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.modules.auth.models import User, UserStatus
from app.modules.booking.models import Booking, BookingStatus, CancelledBy
from app.modules.facility.models import Slot, SlotStatus
from app.modules.facility.service import SlotService

_OWNER_EMAIL = "owner@lifecycle.com"
_CUSTOMER_EMAIL = "cust@lifecycle.com"
_PASSWORD = "pass1234"


# ===== Shared helpers =====


async def _register_and_verify(client, db_session, email, role):
    r = await client.post(
        "/auth/register", json={"email": email, "password": _PASSWORD, "role": role}
    )
    assert r.status_code == 201
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.status = UserStatus.verified
    db_session.add(user)
    await db_session.flush()
    r = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ===== Fixtures =====


@pytest_asyncio.fixture
async def owner_h(client: AsyncClient, db_session: AsyncSession, default_tenant: object) -> dict:
    return await _register_and_verify(client, db_session, _OWNER_EMAIL, "owner")


@pytest_asyncio.fixture
async def customer_h(client: AsyncClient, db_session: AsyncSession, default_tenant: object) -> dict:
    return await _register_and_verify(client, db_session, _CUSTOMER_EMAIL, "customer")


@pytest_asyncio.fixture
async def court(client: AsyncClient, owner_h: dict) -> dict:
    r = await client.post("/facilities", json={"name": "LC Fac"}, headers=owner_h)
    fac_id = r.json()["id"]
    r = await client.post(
        "/courts",
        json={
            "facility_id": fac_id,
            "name": "Court LC",
            "sport_type": "badminton",
            "default_price": "150000",
        },
        headers=owner_h,
    )
    assert r.status_code == 201
    return r.json()


@pytest_asyncio.fixture
async def slots(
    client: AsyncClient, court: dict, owner_h: dict, db_session: AsyncSession
) -> list[int]:
    """Pricing + generate slots 2 days ahead (> 24h away for refund eligibility)."""
    from datetime import date

    target = date.today() + timedelta(days=2)
    dow = target.isoweekday() % 7
    r = await client.put(
        f"/courts/{court['id']}/pricing",
        json={
            "rules": [
                {"day_of_week": dow, "start_time": "08:00", "end_time": "22:00", "price": "150000"}
            ]
        },
        headers=owner_h,
    )
    assert r.status_code == 200
    await SlotService(db_session).generate_for_court_on_date(uuid.UUID(court["id"]), target)
    from app.modules.facility.repository import SlotRepository

    all_slots = await SlotRepository(db_session).list_by_courts_and_date(
        [uuid.UUID(court["id"])], target
    )
    return [s.id for s in all_slots[:4]]


@pytest_asyncio.fixture
async def booking(client: AsyncClient, customer_h: dict, court: dict, slots: list) -> dict:
    r = await client.post(
        "/bookings",
        json={"court_id": court["id"], "slot_ids": slots[:2]},
        headers={**customer_h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201
    return r.json()


# ===== Cancel (customer) =====


async def test_cancel_pending_payment_frees_slots(
    client: AsyncClient, db_session: AsyncSession, booking: dict, customer_h: dict, slots: list
) -> None:
    r = await client.post(
        f"/bookings/{booking['id']}/cancel",
        json={},
        headers={**customer_h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "cancelled"
    assert data["refund_amount"] == "0"  # pending_payment → no payment made
    assert data["refund_status"] is None

    result = await db_session.execute(select(Slot).where(Slot.id.in_(slots[:2])))  # type: ignore[union-attr]
    assert all(s.status == SlotStatus.available for s in result.scalars().all())


async def test_cancel_idempotent_replay(
    client: AsyncClient, booking: dict, customer_h: dict
) -> None:
    idem = str(uuid.uuid4())
    headers = {**customer_h, "Idempotency-Key": idem}
    r1 = await client.post(f"/bookings/{booking['id']}/cancel", json={}, headers=headers)
    r2 = await client.post(f"/bookings/{booking['id']}/cancel", json={}, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["booking_id"] == r2.json()["booking_id"]


async def test_cancel_requires_idempotency_key(
    client: AsyncClient, booking: dict, customer_h: dict
) -> None:
    r = await client.post(f"/bookings/{booking['id']}/cancel", json={}, headers=customer_h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"


async def test_cancel_payment_processing_returns_409(
    client: AsyncClient, db_session: AsyncSession, booking: dict, customer_h: dict
) -> None:
    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    b.status = BookingStatus.payment_processing
    db_session.add(b)
    await db_session.flush()

    r = await client.post(
        f"/bookings/{booking['id']}/cancel",
        json={},
        headers={**customer_h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "BOOKING_NOT_CANCELLABLE"


async def test_cancel_terminal_state_returns_409(
    client: AsyncClient, db_session: AsyncSession, booking: dict, customer_h: dict
) -> None:
    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    b.status = BookingStatus.completed
    db_session.add(b)
    await db_session.flush()

    r = await client.post(
        f"/bookings/{booking['id']}/cancel",
        json={},
        headers={**customer_h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 409


async def test_cancel_confirmed_over_24h_creates_refund(
    client: AsyncClient, db_session: AsyncSession, booking: dict, customer_h: dict
) -> None:
    """Slots are 2 days ahead → > 24h → 100% refund record created."""
    from app.modules.payment.models import Payment, PaymentMethod, PaymentStatus, Refund

    # Simulate confirmed booking with a successful payment
    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    b.status = BookingStatus.confirmed
    db_session.add(b)

    payment = Payment(
        booking_id=b.id,
        method=PaymentMethod.vnpay,
        amount=b.total_amount,
        status=PaymentStatus.success,
        paid_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(payment)
    await db_session.flush()

    r = await client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"reason": "Changed mind"},
        headers={**customer_h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200
    data = r.json()
    assert float(data["refund_amount"]) == float(b.total_amount)
    assert data["refund_status"] == "pending"

    result = await db_session.execute(select(Refund).where(Refund.payment_id == payment.id))
    refunds = result.scalars().all()
    assert len(refunds) == 1
    assert float(refunds[0].amount) == float(b.total_amount)


async def test_cancel_confirmed_under_24h_no_refund(
    client: AsyncClient, db_session: AsyncSession, booking: dict, customer_h: dict, slots: list
) -> None:
    """Move slot_start to < 24h away → no refund."""
    from app.modules.payment.models import Payment, PaymentMethod, PaymentStatus

    # Stagger slot_start to avoid unique constraint on (court_id, slot_start)
    base = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=12)
    result = await db_session.execute(select(Slot).where(Slot.id.in_(slots[:2])))  # type: ignore[union-attr]
    for i, slot in enumerate(result.scalars().all()):
        slot.slot_start = base + timedelta(hours=i)
        slot.slot_end = base + timedelta(hours=i + 1)
        db_session.add(slot)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    b.status = BookingStatus.confirmed
    db_session.add(b)

    payment = Payment(
        booking_id=b.id,
        method=PaymentMethod.vnpay,
        amount=b.total_amount,
        status=PaymentStatus.success,
        paid_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(payment)
    await db_session.flush()

    r = await client.post(
        f"/bookings/{booking['id']}/cancel",
        json={},
        headers={**customer_h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200
    assert r.json()["refund_amount"] == "0"
    assert r.json()["refund_status"] is None


# ===== Force-cancel (owner) =====


async def test_force_cancel_by_owner(
    client: AsyncClient, db_session: AsyncSession, booking: dict, owner_h: dict, slots: list
) -> None:
    r = await client.post(f"/bookings/{booking['id']}/force-cancel", json={}, headers=owner_h)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    assert b.status == BookingStatus.cancelled
    assert b.cancelled_by == CancelledBy.owner

    result = await db_session.execute(select(Slot).where(Slot.id.in_(slots[:2])))  # type: ignore[union-attr]
    assert all(s.status == SlotStatus.available for s in result.scalars().all())


async def test_force_cancel_requires_owner_role(
    client: AsyncClient, booking: dict, customer_h: dict
) -> None:
    r = await client.post(f"/bookings/{booking['id']}/force-cancel", json={}, headers=customer_h)
    assert r.status_code == 403


# ===== Check-in (owner) =====


async def test_check_in_confirmed_booking(
    client: AsyncClient, db_session: AsyncSession, booking: dict, owner_h: dict
) -> None:
    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    b.status = BookingStatus.confirmed
    db_session.add(b)
    await db_session.flush()

    r = await client.post(f"/bookings/{booking['id']}/check-in", headers=owner_h)
    assert r.status_code == 200
    assert r.json()["status"] == "in_use"

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    assert result.scalar_one().status == BookingStatus.in_use


async def test_check_in_non_confirmed_returns_409(
    client: AsyncClient, booking: dict, owner_h: dict
) -> None:
    # booking is pending_payment by default
    r = await client.post(f"/bookings/{booking['id']}/check-in", headers=owner_h)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "BOOKING_NOT_CHECK_INABLE"


async def test_check_in_requires_owner_role(
    client: AsyncClient, db_session: AsyncSession, booking: dict, customer_h: dict
) -> None:
    r = await client.post(f"/bookings/{booking['id']}/check-in", headers=customer_h)
    assert r.status_code == 403


# ===== Cron: expire_holds =====


async def test_expire_holds_job(db_session: AsyncSession, booking: dict, slots: list) -> None:
    from app.jobs.expire_holds import run_expire_holds

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    b.hold_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db_session.add(b)
    await db_session.flush()

    count = await run_expire_holds(db_session)
    assert count >= 1

    await db_session.refresh(b)
    assert b.status == BookingStatus.expired

    result = await db_session.execute(select(Slot).where(Slot.id.in_(slots[:2])))  # type: ignore[union-attr]
    assert all(s.status == SlotStatus.available for s in result.scalars().all())


# ===== Cron: auto_complete =====


async def test_auto_complete_job(db_session: AsyncSession, booking: dict, slots: list) -> None:
    from app.jobs.auto_complete import run_auto_complete

    # Stagger slot times to avoid unique constraint violation
    base_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=4)
    result = await db_session.execute(select(Slot).where(Slot.id.in_(slots[:2])))  # type: ignore[union-attr]
    for i, slot in enumerate(result.scalars().all()):
        slot.slot_start = base_past + timedelta(hours=i)
        slot.slot_end = base_past + timedelta(hours=i + 1)
        db_session.add(slot)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    b.status = BookingStatus.confirmed
    db_session.add(b)
    await db_session.flush()

    count = await run_auto_complete(db_session)
    assert count >= 1

    await db_session.refresh(b)
    assert b.status == BookingStatus.completed
