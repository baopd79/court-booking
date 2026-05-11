"""Integration tests for Slice 5 — Booking."""

import uuid
from datetime import timedelta

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.modules.auth.models import User, UserStatus
from app.modules.facility.models import SlotStatus
from app.modules.facility.repository import SlotRepository
from app.modules.facility.service import SlotService

_OWNER_EMAIL = "owner@booking-test.com"
_CUSTOMER_EMAIL = "customer@booking-test.com"
_CUSTOMER2_EMAIL = "customer2@booking-test.com"
_PASSWORD = "pass1234"


# ===== Auth helpers =====


async def _register_and_verify(
    client: AsyncClient,
    db_session: AsyncSession,
    email: str,
    role: str,
) -> dict[str, str]:
    r = await client.post(
        "/auth/register", json={"email": email, "password": _PASSWORD, "role": role}
    )
    assert r.status_code == 201

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.status = UserStatus.verified  # type: ignore[assignment]
    db_session.add(user)
    await db_session.flush()

    r = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ===== Fixtures =====


@pytest_asyncio.fixture
async def owner_headers(
    client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> dict[str, str]:
    return await _register_and_verify(client, db_session, _OWNER_EMAIL, "owner")


@pytest_asyncio.fixture
async def customer_headers(
    client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> dict[str, str]:
    return await _register_and_verify(client, db_session, _CUSTOMER_EMAIL, "customer")


@pytest_asyncio.fixture
async def customer2_headers(
    client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> dict[str, str]:
    return await _register_and_verify(client, db_session, _CUSTOMER2_EMAIL, "customer")


@pytest_asyncio.fixture
async def facility(client: AsyncClient, owner_headers: dict) -> dict:
    r = await client.post(
        "/facilities",
        json={"name": "Booking Test Facility"},
        headers=owner_headers,
    )
    assert r.status_code == 201
    return r.json()


@pytest_asyncio.fixture
async def court(client: AsyncClient, owner_headers: dict, facility: dict) -> dict:
    r = await client.post(
        "/courts",
        json={
            "facility_id": facility["id"],
            "name": "Court B1",
            "sport_type": "badminton",
            "default_price": "100000.00",
        },
        headers=owner_headers,
    )
    assert r.status_code == 201
    return r.json()


@pytest_asyncio.fixture
async def court_with_slots(
    client: AsyncClient, owner_headers: dict, court: dict, db_session: AsyncSession
) -> dict:
    """Court with pricing rules + pre-generated slots for tomorrow."""
    from datetime import date

    tomorrow = date.today() + timedelta(days=1)
    dow = tomorrow.isoweekday() % 7

    r = await client.put(
        f"/courts/{court['id']}/pricing",
        json={
            "rules": [
                {
                    "day_of_week": dow,
                    "start_time": "08:00:00",
                    "end_time": "22:00:00",
                    "price": "100000",
                }
            ]
        },
        headers=owner_headers,
    )
    assert r.status_code == 200

    service = SlotService(db_session)
    await service.generate_for_court_on_date(uuid.UUID(court["id"]), tomorrow)
    await db_session.commit()
    return court


@pytest_asyncio.fixture
async def available_slots(db_session: AsyncSession, court_with_slots: dict) -> list[int]:
    """Return first 4 consecutive available slot IDs for the court."""
    from datetime import date

    tomorrow = date.today() + timedelta(days=1)
    repo = SlotRepository(db_session)
    slots = await repo.list_by_courts_and_date([uuid.UUID(court_with_slots["id"])], tomorrow)
    return [s.id for s in slots[:4]]


# ===== Guard tests =====


async def test_create_booking_requires_customer_role(
    client: AsyncClient, owner_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**owner_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 403


async def test_create_booking_requires_auth(
    client: AsyncClient, court_with_slots: dict, available_slots: list
) -> None:
    r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 401


async def test_missing_idempotency_key(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers=customer_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"


# ===== Happy path =====


async def test_create_booking_single_slot(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending_payment"
    assert body["booking_type"] == "online"
    assert len(body["slots"]) == 1
    assert float(body["total_amount"]) == 100000.0
    assert body["hold_expires_at"] is not None


async def test_create_booking_multi_slot(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    """Book 3 consecutive slots."""
    r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:3]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201
    body = r.json()
    assert len(body["slots"]) == 3
    assert float(body["total_amount"]) == 300000.0


async def test_slots_become_held_after_booking(
    client: AsyncClient,
    customer_headers: dict,
    court_with_slots: dict,
    available_slots: list,
    db_session: AsyncSession,
) -> None:
    await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:2]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    from app.modules.facility.models import Slot

    result = await db_session.execute(select(Slot).where(Slot.id.in_(available_slots[:2])))
    slots = result.scalars().all()
    assert all(s.status == SlotStatus.held for s in slots)
    assert all(s.held_by_booking_id is not None for s in slots)


# ===== Idempotency =====


async def test_idempotency_replay(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    """Same key + same body → returns same response."""
    key = str(uuid.uuid4())
    payload = {"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]}

    r1 = await client.post(
        "/bookings", json=payload, headers={**customer_headers, "Idempotency-Key": key}
    )
    r2 = await client.post(
        "/bookings", json=payload, headers={**customer_headers, "Idempotency-Key": key}
    )

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


async def test_idempotency_reused_different_body(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    """Same key, different body → 409."""
    key = str(uuid.uuid4())
    r1 = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": key},
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[1:2]},
        headers={**customer_headers, "Idempotency-Key": key},
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED_DIFFERENT_BODY"


# ===== Slot validation =====


async def test_slot_not_available_after_booking(
    client: AsyncClient,
    customer_headers: dict,
    customer2_headers: dict,
    court_with_slots: dict,
    available_slots: list,
) -> None:
    """Second booking for same slot → 409 SLOT_NOT_AVAILABLE."""
    # First booking
    r1 = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r1.status_code == 201

    # Second booking for same slot
    r2 = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer2_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r2.status_code == 409
    body = r2.json()
    assert body["error"]["code"] == "SLOT_NOT_AVAILABLE"
    assert available_slots[0] in body["error"]["details"]["unavailable_slot_ids"]


async def test_non_consecutive_slots_rejected(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    # Slots 0 and 2 are not consecutive
    r = await client.post(
        "/bookings",
        json={
            "court_id": court_with_slots["id"],
            "slot_ids": [available_slots[0], available_slots[2]],
        },
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_SLOTS"


async def test_too_many_slots_rejected(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    """max_length=4 in schema → FastAPI returns 422."""
    r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots},  # 4 is OK
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201  # 4 slots is the max allowed

    # 5 slots → 422 from Pydantic validation
    r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": [1, 2, 3, 4, 5]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


async def test_slot_wrong_court_rejected(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    """Slots exist but belong to different court_id → INVALID_SLOTS."""
    r = await client.post(
        "/bookings",
        json={"court_id": str(uuid.uuid4()), "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_SLOTS"


# ===== Walk-in =====


async def test_walkin_booking(
    client: AsyncClient, owner_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    r = await client.post(
        "/bookings/walk-in",
        json={
            "court_id": court_with_slots["id"],
            "slot_ids": available_slots[:2],
            "walkin_name": "Nguyễn Văn A",
            "walkin_phone": "0901234567",
        },
        headers={**owner_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "confirmed"  # immediate confirm
    assert body["booking_type"] == "walkin"
    assert body["walkin_name"] == "Nguyễn Văn A"
    assert body["hold_expires_at"] is None


async def test_walkin_slots_become_booked(
    client: AsyncClient,
    owner_headers: dict,
    court_with_slots: dict,
    available_slots: list,
    db_session: AsyncSession,
) -> None:
    await client.post(
        "/bookings/walk-in",
        json={
            "court_id": court_with_slots["id"],
            "slot_ids": available_slots[:1],
            "walkin_name": "Test",
            "walkin_phone": "0900000000",
        },
        headers={**owner_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    from app.modules.facility.models import Slot

    result = await db_session.execute(select(Slot).where(Slot.id == available_slots[0]))
    slot = result.scalar_one()
    assert slot.status == SlotStatus.booked


async def test_walkin_requires_owner(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    r = await client.post(
        "/bookings/walk-in",
        json={
            "court_id": court_with_slots["id"],
            "slot_ids": available_slots[:1],
            "walkin_name": "X",
            "walkin_phone": "0900000000",
        },
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 403


# ===== Read endpoints =====


async def test_get_booking(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    create_r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = create_r.json()["id"]

    r = await client.get(f"/bookings/{booking_id}", headers=customer_headers)
    assert r.status_code == 200
    assert r.json()["id"] == booking_id
    assert len(r.json()["slots"]) == 1


async def test_get_booking_by_owner(
    client: AsyncClient,
    customer_headers: dict,
    owner_headers: dict,
    court_with_slots: dict,
    available_slots: list,
) -> None:
    create_r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = create_r.json()["id"]

    r = await client.get(f"/bookings/{booking_id}", headers=owner_headers)
    assert r.status_code == 200


async def test_customer_cannot_see_other_booking(
    client: AsyncClient,
    customer_headers: dict,
    customer2_headers: dict,
    court_with_slots: dict,
    available_slots: list,
) -> None:
    create_r = await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = create_r.json()["id"]

    r = await client.get(f"/bookings/{booking_id}", headers=customer2_headers)
    assert r.status_code == 403


async def test_list_my_bookings(
    client: AsyncClient, customer_headers: dict, court_with_slots: dict, available_slots: list
) -> None:
    await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    r = await client.get("/bookings/me", headers=customer_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


async def test_owner_list_bookings(
    client: AsyncClient,
    owner_headers: dict,
    customer_headers: dict,
    court_with_slots: dict,
    available_slots: list,
) -> None:
    await client.post(
        "/bookings",
        json={"court_id": court_with_slots["id"], "slot_ids": available_slots[:1]},
        headers={**customer_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    r = await client.get("/bookings", headers=owner_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


async def test_booking_not_found(client: AsyncClient, customer_headers: dict) -> None:
    r = await client.get(f"/bookings/{uuid.uuid4()}", headers=customer_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "BOOKING_NOT_FOUND"
