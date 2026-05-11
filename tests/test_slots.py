"""Integration tests for Slice 4 — Slot."""

import uuid
from datetime import date, timedelta

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User, UserStatus
from app.modules.facility.models import SlotStatus
from app.modules.facility.repository import SlotRepository
from app.modules.facility.service import SlotService

_OWNER_EMAIL = "owner@slots-test.com"
_PASSWORD = "pass1234"
_TODAY = date.today()
_TOMORROW = _TODAY + timedelta(days=1)


# ===== Auth fixture =====


async def _register_and_verify(
    client: AsyncClient,
    db_session: AsyncSession,
    email: str,
    role: str,
    default_tenant: object,
) -> dict[str, str]:
    r = await client.post(
        "/auth/register", json={"email": email, "password": _PASSWORD, "role": role}
    )
    assert r.status_code == 201

    from sqlmodel import select

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.status = UserStatus.verified  # type: ignore[assignment]
    db_session.add(user)
    await db_session.flush()

    r = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def owner_headers(
    client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> dict[str, str]:
    return await _register_and_verify(client, db_session, _OWNER_EMAIL, "owner", default_tenant)


# ===== Resource fixtures =====


@pytest_asyncio.fixture
async def facility(client: AsyncClient, owner_headers: dict) -> dict:
    r = await client.post(
        "/facilities",
        json={"name": "Slot Test Facility", "address": "1 Test St"},
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
            "name": "Court Slot A",
            "sport_type": "badminton",
            "default_price": "120000.00",
        },
        headers=owner_headers,
    )
    assert r.status_code == 201
    return r.json()


@pytest_asyncio.fixture
async def court_with_pricing(client: AsyncClient, owner_headers: dict, court: dict) -> dict:
    """Court with pricing rules covering tomorrow (all-day 06:00-22:00)."""
    tomorrow_dow = _TOMORROW.isoweekday() % 7  # 0=Sun, 1=Mon, ..., 6=Sat
    rules = [
        {
            "day_of_week": tomorrow_dow,
            "start_time": "06:00:00",
            "end_time": "14:00:00",
            "price": "100000",
        },
        {
            "day_of_week": tomorrow_dow,
            "start_time": "14:00:00",
            "end_time": "22:00:00",
            "price": "150000",
        },
    ]
    r = await client.put(
        f"/courts/{court['id']}/pricing",
        json={"rules": rules},
        headers=owner_headers,
    )
    assert r.status_code == 200
    return court


@pytest_asyncio.fixture
async def generated_slots(db_session: AsyncSession, court_with_pricing: dict) -> int:
    """Generate slots for tomorrow for the test court. Returns count inserted."""
    service = SlotService(db_session)
    court_id = uuid.UUID(court_with_pricing["id"])
    inserted = await service.generate_for_court_on_date(court_id, _TOMORROW)
    await db_session.commit()
    return inserted


# ===== Tests =====


async def test_availability_no_auth_required(
    client: AsyncClient, facility: dict, court_with_pricing: dict, generated_slots: int
) -> None:
    """GET /courts/availability is public."""
    r = await client.get(f"/courts/availability?facility_id={facility['id']}&date={_TOMORROW}")
    assert r.status_code == 200


async def test_availability_returns_correct_slot_count(
    client: AsyncClient, facility: dict, court_with_pricing: dict, generated_slots: int
) -> None:
    """2 pricing rules nhân 8h each ÷ 1h/slot = 16 slots."""
    assert generated_slots == 16
    r = await client.get(f"/courts/availability?facility_id={facility['id']}&date={_TOMORROW}")
    body = r.json()
    assert body["facility_id"] == facility["id"]
    assert len(body["courts"]) == 1
    slots = body["courts"][0]["slots"]
    assert len(slots) == 16


async def test_availability_status_mapping(
    client: AsyncClient,
    facility: dict,
    court_with_pricing: dict,
    generated_slots: int,
    db_session: AsyncSession,
) -> None:
    """held + booked slots must appear as 'unavailable', not leak the real status."""
    court_id = uuid.UUID(court_with_pricing["id"])
    # Manually set 2 slots to held/booked
    slot_repo = SlotRepository(db_session)
    all_slots = await slot_repo.list_by_courts_and_date([court_id], _TOMORROW)
    assert len(all_slots) >= 2

    all_slots[0].status = SlotStatus.held
    all_slots[1].status = SlotStatus.booked
    db_session.add(all_slots[0])
    db_session.add(all_slots[1])
    await db_session.flush()

    r = await client.get(f"/courts/availability?facility_id={facility['id']}&date={_TOMORROW}")
    slots = r.json()["courts"][0]["slots"]
    statuses = {s["id"]: s["status"] for s in slots}
    assert statuses[all_slots[0].id] == "unavailable"
    assert statuses[all_slots[1].id] == "unavailable"
    # available slots stay available
    assert statuses[all_slots[2].id] == "available"


async def test_availability_prices_from_rules(
    client: AsyncClient, facility: dict, court_with_pricing: dict, generated_slots: int
) -> None:
    """First 8 slots (06:00-14:00) priced at 100000; next 8 at 150000."""
    r = await client.get(f"/courts/availability?facility_id={facility['id']}&date={_TOMORROW}")
    slots = r.json()["courts"][0]["slots"]
    assert float(slots[0]["price"]) == 100000.0  # 06:00 slot
    assert float(slots[7]["price"]) == 100000.0  # 13:00 slot
    assert float(slots[8]["price"]) == 150000.0  # 14:00 slot
    assert float(slots[15]["price"]) == 150000.0  # 21:00 slot


async def test_availability_filter_by_court(
    client: AsyncClient, facility: dict, court_with_pricing: dict, generated_slots: int
) -> None:
    court_id = court_with_pricing["id"]
    r = await client.get(
        f"/courts/availability?facility_id={facility['id']}&date={_TOMORROW}&court_id={court_id}"
    )
    body = r.json()
    assert len(body["courts"]) == 1
    assert body["courts"][0]["id"] == court_id


async def test_availability_no_slots_for_date(
    client: AsyncClient, facility: dict, court_with_pricing: dict
) -> None:
    """Date with no generated slots → court present but empty slots list."""
    future_date = _TODAY + timedelta(days=60)
    r = await client.get(f"/courts/availability?facility_id={facility['id']}&date={future_date}")
    assert r.status_code == 200
    assert r.json()["courts"][0]["slots"] == []


async def test_availability_facility_not_found(client: AsyncClient) -> None:
    r = await client.get(f"/courts/availability?facility_id={uuid.uuid4()}&date={_TOMORROW}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FACILITY_NOT_FOUND"


async def test_generate_idempotent(db_session: AsyncSession, court_with_pricing: dict) -> None:
    """Running generate twice inserts 0 new slots on second run."""
    service = SlotService(db_session)
    court_id = uuid.UUID(court_with_pricing["id"])
    first = await service.generate_for_court_on_date(court_id, _TOMORROW)
    await db_session.commit()
    second = await service.generate_for_court_on_date(court_id, _TOMORROW)
    await db_session.commit()
    assert first == 16
    assert second == 0


async def test_close_slots(
    client: AsyncClient, owner_headers: dict, court_with_pricing: dict, generated_slots: int
) -> None:
    court_id = court_with_pricing["id"]
    r = await client.post(
        f"/courts/{court_id}/slots/close",
        json={"date": str(_TOMORROW), "start_time": "06:00:00", "end_time": "10:00:00"},
        headers=owner_headers,
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 4  # 06, 07, 08, 09 slots closed


async def test_reopen_slots(
    client: AsyncClient, owner_headers: dict, court_with_pricing: dict, generated_slots: int
) -> None:
    court_id = court_with_pricing["id"]
    # Close first
    await client.post(
        f"/courts/{court_id}/slots/close",
        json={"date": str(_TOMORROW), "start_time": "06:00:00", "end_time": "08:00:00"},
        headers=owner_headers,
    )
    # Reopen
    r = await client.post(
        f"/courts/{court_id}/slots/reopen",
        json={"date": str(_TOMORROW), "start_time": "06:00:00", "end_time": "08:00:00"},
        headers=owner_headers,
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 2


async def test_close_does_not_affect_booked(
    client: AsyncClient,
    owner_headers: dict,
    court_with_pricing: dict,
    generated_slots: int,
    db_session: AsyncSession,
) -> None:
    """Closing a range skips slots that are already booked."""
    court_id = uuid.UUID(court_with_pricing["id"])
    slot_repo = SlotRepository(db_session)
    slots = await slot_repo.list_by_courts_and_date([court_id], _TOMORROW)

    # Manually mark slot 0 as booked
    slots[0].status = SlotStatus.booked
    db_session.add(slots[0])
    await db_session.flush()

    r = await client.post(
        f"/courts/{court_id}/slots/close",
        json={"date": str(_TOMORROW), "start_time": "06:00:00", "end_time": "08:00:00"},
        headers=owner_headers,
    )
    # 2 slots in range, 1 booked → only 1 closed
    assert r.json()["updated"] == 1


async def test_close_slots_requires_owner(
    client: AsyncClient, court_with_pricing: dict, generated_slots: int
) -> None:
    court_id = court_with_pricing["id"]
    r = await client.post(
        f"/courts/{court_id}/slots/close",
        json={"date": str(_TOMORROW), "start_time": "06:00:00", "end_time": "08:00:00"},
    )
    assert r.status_code == 401


async def test_no_pricing_rules_no_slots(db_session: AsyncSession, court: dict) -> None:
    """Court with no pricing rules generates 0 slots."""
    service = SlotService(db_session)
    court_id = uuid.UUID(court["id"])
    inserted = await service.generate_for_court_on_date(court_id, _TOMORROW)
    assert inserted == 0
