"""Integration tests for Slice 3 — Facility."""

import uuid

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.modules.auth.models import User, UserStatus

_OWNER_EMAIL = "owner@facility-test.com"
_CUSTOMER_EMAIL = "customer@facility-test.com"
_PASSWORD = "pass1234"


# ===== Auth fixtures =====


async def _register_and_verify(
    client: AsyncClient,
    db_session: AsyncSession,
    email: str,
    role: str,
    default_tenant: object,
) -> dict[str, str]:
    """Register, verify email via DB, login — return auth headers."""
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


@pytest_asyncio.fixture
async def owner_headers(
    client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> dict[str, str]:
    return await _register_and_verify(client, db_session, _OWNER_EMAIL, "owner", default_tenant)


@pytest_asyncio.fixture
async def customer_headers(
    client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> dict[str, str]:
    return await _register_and_verify(
        client, db_session, _CUSTOMER_EMAIL, "customer", default_tenant
    )


# ===== Resource fixtures =====


@pytest_asyncio.fixture
async def facility(client: AsyncClient, owner_headers: dict) -> dict:
    r = await client.post(
        "/facilities",
        json={"name": "Grand Sports Center", "address": "123 Main St"},
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
            "name": "Court A",
            "sport_type": "badminton",
            "default_price": "150000.00",
        },
        headers=owner_headers,
    )
    assert r.status_code == 201
    return r.json()


# ===== Guard tests =====


async def test_facility_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/facilities")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


async def test_facility_requires_owner_role(client: AsyncClient, customer_headers: dict) -> None:
    r = await client.get("/facilities", headers=customer_headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_court_requires_owner_role(client: AsyncClient, customer_headers: dict) -> None:
    r = await client.get("/courts", headers=customer_headers)
    assert r.status_code == 403


# ===== Facility CRUD =====


async def test_create_facility(
    client: AsyncClient, owner_headers: dict, default_tenant: object
) -> None:
    r = await client.post(
        "/facilities",
        json={"name": "New Facility"},
        headers=owner_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "New Facility"
    assert body["deleted_at"] is None


async def test_get_facility(client: AsyncClient, owner_headers: dict, facility: dict) -> None:
    r = await client.get(f"/facilities/{facility['id']}", headers=owner_headers)
    assert r.status_code == 200
    assert r.json()["id"] == facility["id"]


async def test_list_facilities(client: AsyncClient, owner_headers: dict, facility: dict) -> None:
    r = await client.get("/facilities", headers=owner_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    ids = [f["id"] for f in body["items"]]
    assert facility["id"] in ids


async def test_update_facility(client: AsyncClient, owner_headers: dict, facility: dict) -> None:
    r = await client.patch(
        f"/facilities/{facility['id']}",
        json={"name": "Updated Name"},
        headers=owner_headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Updated Name"
    assert r.json()["address"] == facility["address"]  # unchanged


async def test_delete_facility_soft(
    client: AsyncClient, owner_headers: dict, facility: dict
) -> None:
    r = await client.delete(f"/facilities/{facility['id']}", headers=owner_headers)
    assert r.status_code == 204

    r = await client.get(f"/facilities/{facility['id']}", headers=owner_headers)
    assert r.status_code == 404


async def test_get_facility_not_found(
    client: AsyncClient, owner_headers: dict, default_tenant: object
) -> None:
    r = await client.get(f"/facilities/{uuid.uuid4()}", headers=owner_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FACILITY_NOT_FOUND"


# ===== Court CRUD =====


async def test_create_court(client: AsyncClient, owner_headers: dict, facility: dict) -> None:
    r = await client.post(
        "/courts",
        json={
            "facility_id": facility["id"],
            "name": "Court B",
            "sport_type": "tennis",
            "default_price": "200000.00",
        },
        headers=owner_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Court B"
    assert body["sport_type"] == "tennis"
    assert float(body["default_price"]) == 200000.0


async def test_get_court(client: AsyncClient, owner_headers: dict, court: dict) -> None:
    r = await client.get(f"/courts/{court['id']}", headers=owner_headers)
    assert r.status_code == 200
    assert r.json()["id"] == court["id"]


async def test_list_courts_by_facility(
    client: AsyncClient, owner_headers: dict, court: dict, facility: dict
) -> None:
    r = await client.get(f"/courts?facility_id={facility['id']}", headers=owner_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(c["id"] == court["id"] for c in body["items"])


async def test_update_court(client: AsyncClient, owner_headers: dict, court: dict) -> None:
    r = await client.patch(
        f"/courts/{court['id']}",
        json={"name": "Court A Updated", "default_price": "180000.00"},
        headers=owner_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Court A Updated"
    assert float(body["default_price"]) == 180000.0
    assert body["sport_type"] == court["sport_type"]  # unchanged


async def test_delete_court_soft(client: AsyncClient, owner_headers: dict, court: dict) -> None:
    r = await client.delete(f"/courts/{court['id']}", headers=owner_headers)
    assert r.status_code == 204

    r = await client.get(f"/courts/{court['id']}", headers=owner_headers)
    assert r.status_code == 404


async def test_create_court_invalid_price(
    client: AsyncClient, owner_headers: dict, facility: dict
) -> None:
    r = await client.post(
        "/courts",
        json={
            "facility_id": facility["id"],
            "name": "Court X",
            "sport_type": "pickleball",
            "default_price": "0",
        },
        headers=owner_headers,
    )
    assert r.status_code == 422


async def test_create_court_nonexistent_facility(
    client: AsyncClient, owner_headers: dict, default_tenant: object
) -> None:
    r = await client.post(
        "/courts",
        json={
            "facility_id": str(uuid.uuid4()),
            "name": "Court X",
            "sport_type": "pickleball",
            "default_price": "100000",
        },
        headers=owner_headers,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FACILITY_NOT_FOUND"


# ===== Pricing Rules =====


async def test_replace_and_get_pricing(
    client: AsyncClient, owner_headers: dict, court: dict
) -> None:
    rules = [
        {"day_of_week": 1, "start_time": "06:00:00", "end_time": "12:00:00", "price": "100000"},
        {"day_of_week": 1, "start_time": "12:00:00", "end_time": "22:00:00", "price": "150000"},
    ]
    r = await client.put(
        f"/courts/{court['id']}/pricing",
        json={"rules": rules},
        headers=owner_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert body[0]["day_of_week"] == 1

    # GET trả lại đúng số rules
    r = await client.get(f"/courts/{court['id']}/pricing", headers=owner_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_replace_pricing_overwrites(
    client: AsyncClient, owner_headers: dict, court: dict
) -> None:
    """PUT lần 2 replace hoàn toàn, không cộng dồn."""
    rules_1 = [
        {"day_of_week": 0, "start_time": "06:00:00", "end_time": "22:00:00", "price": "100000"}
    ]
    await client.put(
        f"/courts/{court['id']}/pricing", json={"rules": rules_1}, headers=owner_headers
    )

    rules_2 = [
        {"day_of_week": 2, "start_time": "08:00:00", "end_time": "20:00:00", "price": "120000"},
        {"day_of_week": 3, "start_time": "08:00:00", "end_time": "20:00:00", "price": "120000"},
    ]
    r = await client.put(
        f"/courts/{court['id']}/pricing", json={"rules": rules_2}, headers=owner_headers
    )
    assert r.status_code == 200
    assert len(r.json()) == 2  # chỉ 2 rules mới, không phải 3


async def test_pricing_end_before_start(
    client: AsyncClient, owner_headers: dict, court: dict
) -> None:
    r = await client.put(
        f"/courts/{court['id']}/pricing",
        json={
            "rules": [
                {
                    "day_of_week": 1,
                    "start_time": "12:00:00",
                    "end_time": "06:00:00",
                    "price": "100000",
                }
            ]
        },
        headers=owner_headers,
    )
    assert r.status_code == 422


async def test_pricing_empty_rules(client: AsyncClient, owner_headers: dict, court: dict) -> None:
    r = await client.put(
        f"/courts/{court['id']}/pricing",
        json={"rules": []},
        headers=owner_headers,
    )
    assert r.status_code == 422
