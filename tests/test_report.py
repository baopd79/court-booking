"""Integration tests for Slice 9 — Revenue Report."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.modules.auth.models import User, UserStatus
from app.modules.booking.models import Booking, BookingStatus, BookingType
from app.modules.facility.models import Court, Facility
from app.modules.payment.models import Payment, PaymentMethod, PaymentStatus

_OWNER_EMAIL = "owner@report.com"
_PASSWORD = "pass1234"


@pytest_asyncio.fixture
async def owner_h(client: AsyncClient, db_session: AsyncSession, default_tenant: object) -> dict:
    r = await client.post("/auth/register", json={
        "email": _OWNER_EMAIL, "password": _PASSWORD, "role": "owner"
    })
    assert r.status_code == 201
    result = await db_session.execute(select(User).where(User.email == _OWNER_EMAIL))
    u = result.scalar_one()
    u.status = UserStatus.verified
    db_session.add(u)
    await db_session.flush()
    r = await client.post("/auth/login", json={"email": _OWNER_EMAIL, "password": _PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession, owner_h: dict, default_tenant: object) -> dict:
    """Create facility, court, 2 confirmed bookings with successful payments."""
    result = await db_session.execute(select(User).where(User.email == _OWNER_EMAIL))
    owner = result.scalar_one()

    from app.modules.auth.models import Tenant
    result = await db_session.execute(select(Tenant))
    tenant = result.scalar_one()

    facility = Facility(tenant_id=tenant.id, name="Report Fac", address="1 St")
    db_session.add(facility)
    await db_session.flush()

    court = Court(
        facility_id=facility.id,
        name="Court R",
        sport_type="badminton",
        default_price=Decimal("150000"),
    )
    db_session.add(court)
    await db_session.flush()

    today = datetime.now(UTC).replace(tzinfo=None)

    bookings_data = []
    for i in range(2):
        b = Booking(
            tenant_id=tenant.id,
            customer_id=owner.id,
            court_id=court.id,
            status=BookingStatus.confirmed,
            total_amount=Decimal("300000"),
            booking_type=BookingType.online,
        )
        db_session.add(b)
        await db_session.flush()

        p = Payment(
            booking_id=b.id,
            method=PaymentMethod.vnpay,
            amount=Decimal("300000"),
            status=PaymentStatus.success,
            paid_at=today - timedelta(days=i),
        )
        db_session.add(p)
        await db_session.flush()
        bookings_data.append({"booking_id": str(b.id), "amount": "300000"})

    return {
        "facility_id": str(facility.id),
        "court_id": str(court.id),
        "bookings": bookings_data,
        "total": "600000",
    }


async def test_revenue_report_total(
    client: AsyncClient, owner_h: dict, seeded: dict
) -> None:
    today = datetime.now(UTC).date()
    r = await client.get(
        f"/reports/revenue?from_date={today - timedelta(days=7)}&to_date={today}",
        headers=owner_h,
    )
    assert r.status_code == 200
    data = r.json()
    assert float(data["total_revenue"]) == 600000.0
    assert data["total_bookings"] == 2


async def test_revenue_report_by_court(
    client: AsyncClient, owner_h: dict, seeded: dict
) -> None:
    today = datetime.now(UTC).date()
    r = await client.get(
        f"/reports/revenue?from_date={today - timedelta(days=7)}&to_date={today}",
        headers=owner_h,
    )
    data = r.json()
    assert len(data["by_court"]) == 1
    court = data["by_court"][0]
    assert court["court_name"] == "Court R"
    assert float(court["revenue"]) == 600000.0
    assert court["bookings"] == 2


async def test_revenue_report_by_day(
    client: AsyncClient, owner_h: dict, seeded: dict
) -> None:
    today = datetime.now(UTC).date()
    r = await client.get(
        f"/reports/revenue?from_date={today - timedelta(days=7)}&to_date={today}",
        headers=owner_h,
    )
    data = r.json()
    assert len(data["by_day"]) == 2
    assert all(float(d["revenue"]) == 300000.0 for d in data["by_day"])


async def test_revenue_report_filter_by_facility(
    client: AsyncClient, owner_h: dict, seeded: dict
) -> None:
    today = datetime.now(UTC).date()
    r = await client.get(
        f"/reports/revenue?from_date={today - timedelta(days=7)}&to_date={today}"
        f"&facility_id={seeded['facility_id']}",
        headers=owner_h,
    )
    assert r.status_code == 200
    assert float(r.json()["total_revenue"]) == 600000.0


async def test_revenue_report_empty_range(
    client: AsyncClient, owner_h: dict, seeded: dict
) -> None:
    future = datetime.now(UTC).date() + timedelta(days=30)
    r = await client.get(
        f"/reports/revenue?from_date={future}&to_date={future}",
        headers=owner_h,
    )
    assert r.status_code == 200
    assert float(r.json()["total_revenue"]) == 0
    assert r.json()["total_bookings"] == 0
    assert r.json()["by_court"] == []
    assert r.json()["by_day"] == []


async def test_revenue_report_requires_owner(
    client: AsyncClient, default_tenant: object, db_session: AsyncSession
) -> None:
    r = await client.post("/auth/register", json={
        "email": "cust@report.com", "password": _PASSWORD, "role": "customer"
    })
    result = await db_session.execute(select(User).where(User.email == "cust@report.com"))
    u = result.scalar_one()
    u.status = UserStatus.verified
    db_session.add(u)
    await db_session.flush()
    r = await client.post("/auth/login", json={"email": "cust@report.com", "password": _PASSWORD})
    cust_token = r.json()["access_token"]

    today = datetime.now(UTC).date()
    r = await client.get(
        f"/reports/revenue?from_date={today}&to_date={today}",
        headers={"Authorization": f"Bearer {cust_token}"},
    )
    assert r.status_code == 403
