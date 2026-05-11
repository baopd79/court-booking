"""Integration tests for Slice 8 — Notification."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.modules.auth.models import User, UserStatus
from app.modules.booking.models import Booking
from app.modules.facility.service import SlotService
from app.modules.notification.models import Notification, NotificationChannel, NotificationStatus
from app.modules.notification.service import NotificationService

_OWNER_EMAIL = "owner@notif.com"
_CUSTOMER_EMAIL = "cust@notif.com"
_PASSWORD = "pass1234"


# ===== Fixtures =====


@pytest_asyncio.fixture
async def owner_h(client: AsyncClient, db_session: AsyncSession, default_tenant: object) -> dict:
    r = await client.post(
        "/auth/register", json={"email": _OWNER_EMAIL, "password": _PASSWORD, "role": "owner"}
    )
    assert r.status_code == 201
    result = await db_session.execute(select(User).where(User.email == _OWNER_EMAIL))
    u = result.scalar_one()
    u.status = UserStatus.verified
    db_session.add(u)
    await db_session.flush()
    r = await client.post("/auth/login", json={"email": _OWNER_EMAIL, "password": _PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def customer_h(client: AsyncClient, db_session: AsyncSession, default_tenant: object) -> dict:
    r = await client.post(
        "/auth/register", json={"email": _CUSTOMER_EMAIL, "password": _PASSWORD, "role": "customer"}
    )
    assert r.status_code == 201
    result = await db_session.execute(select(User).where(User.email == _CUSTOMER_EMAIL))
    u = result.scalar_one()
    u.status = UserStatus.verified
    db_session.add(u)
    await db_session.flush()
    r = await client.post("/auth/login", json={"email": _CUSTOMER_EMAIL, "password": _PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def booking(
    client: AsyncClient, owner_h: dict, customer_h: dict, db_session: AsyncSession
) -> dict:
    r = await client.post("/facilities", json={"name": "Notif Fac"}, headers=owner_h)
    fac_id = r.json()["id"]
    r = await client.post(
        "/courts",
        json={
            "facility_id": fac_id,
            "name": "CN",
            "sport_type": "badminton",
            "default_price": "150000",
        },
        headers=owner_h,
    )
    court_id = r.json()["id"]

    target = (datetime.now(UTC).replace(tzinfo=None) + timedelta(days=2)).date()
    dow = target.isoweekday() % 7
    await client.put(
        f"/courts/{court_id}/pricing",
        json={
            "rules": [
                {"day_of_week": dow, "start_time": "08:00", "end_time": "22:00", "price": "150000"}
            ]
        },
        headers=owner_h,
    )
    await SlotService(db_session).generate_for_court_on_date(uuid.UUID(court_id), target)

    from app.modules.facility.repository import SlotRepository

    slots = await SlotRepository(db_session).list_by_courts_and_date([uuid.UUID(court_id)], target)
    slot_ids = [s.id for s in slots[:2]]

    r = await client.post(
        "/bookings",
        json={"court_id": court_id, "slot_ids": slot_ids},
        headers={**customer_h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201
    return r.json()


async def _get_customer(db_session: AsyncSession) -> User:
    result = await db_session.execute(select(User).where(User.email == _CUSTOMER_EMAIL))
    return result.scalar_one()


# ===== NotificationService unit tests (with mock email sender) =====


async def test_notify_booking_confirmed_creates_records(
    db_session: AsyncSession, booking: dict
) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()

    await svc.notify_booking_confirmed(b)
    await db_session.flush()

    result = await db_session.execute(select(Notification).where(Notification.booking_id == b.id))
    notifs = result.scalars().all()
    channels = {n.channel for n in notifs}
    assert NotificationChannel.in_app in channels
    assert NotificationChannel.email in channels
    assert all(n.event_type == "booking_confirmed" for n in notifs)

    mock_sender.send.assert_awaited_once()


async def test_notify_booking_confirmed_in_app_auto_sent(
    db_session: AsyncSession, booking: dict
) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    await svc.notify_booking_confirmed(b)
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(
            Notification.booking_id == b.id,
            Notification.channel == NotificationChannel.in_app,
        )
    )
    in_app = result.scalar_one()
    assert in_app.status == NotificationStatus.sent
    assert in_app.sent_at is not None


async def test_notify_email_failure_marks_failed(db_session: AsyncSession, booking: dict) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock(side_effect=Exception("SMTP connection refused"))
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    await svc.notify_booking_confirmed(b)
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(
            Notification.booking_id == b.id,
            Notification.channel == NotificationChannel.email,
        )
    )
    email_notif = result.scalar_one()
    assert email_notif.status == NotificationStatus.failed
    assert email_notif.retry_count == 1


async def test_notify_payment_failed_creates_records(
    db_session: AsyncSession, booking: dict
) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    await svc.notify_payment_failed(b)
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(
            Notification.booking_id == b.id,
            Notification.event_type == "payment_failed",
        )
    )
    assert len(result.scalars().all()) == 2  # in_app + email


async def test_notify_booking_cancelled_creates_records(
    db_session: AsyncSession, booking: dict
) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    await svc.notify_booking_cancelled(b, refund_amount="300000")
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(
            Notification.booking_id == b.id,
            Notification.event_type == "booking_cancelled",
        )
    )
    notifs = result.scalars().all()
    assert len(notifs) == 2
    assert notifs[0].payload["refund_amount"] == "300000"


# ===== Retry cron =====


async def test_retry_job_resends_failed(db_session: AsyncSession, booking: dict) -> None:
    from app.jobs.notification_retry import run_notification_retry

    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()

    # Create a failed email notification
    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()

    customer = await _get_customer(db_session)
    failed_notif = Notification(
        user_id=customer.id,
        booking_id=b.id,
        channel=NotificationChannel.email,
        event_type="booking_confirmed",
        payload={"booking_id": str(b.id), "total_amount": "300000"},
        status=NotificationStatus.failed,
        retry_count=1,
    )
    db_session.add(failed_notif)
    await db_session.flush()

    # Patch the email sender in the service
    original_init = NotificationService.__init__

    def patched_init(self, session, email_sender=None):
        original_init(self, session, email_sender=mock_sender)

    import app.modules.notification.service as notif_module

    original_cls_init = notif_module.NotificationService.__init__
    notif_module.NotificationService.__init__ = patched_init

    try:
        count = await run_notification_retry(db_session)
    finally:
        notif_module.NotificationService.__init__ = original_cls_init

    assert count >= 1
    mock_sender.send.assert_awaited()


# ===== HTTP endpoints =====


async def test_list_notifications_empty(
    client: AsyncClient, customer_h: dict, default_tenant: object
) -> None:
    r = await client.get("/notifications", headers=customer_h)
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert r.json()["items"] == []


async def test_list_notifications_shows_own_only(
    client: AsyncClient, db_session: AsyncSession, customer_h: dict, booking: dict
) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    await svc.notify_booking_confirmed(b)
    await db_session.commit()

    r = await client.get("/notifications", headers=customer_h)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2  # in_app + email
    assert all(item["event_type"] == "booking_confirmed" for item in data["items"])


async def test_mark_notification_read(
    client: AsyncClient, db_session: AsyncSession, customer_h: dict, booking: dict
) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    await svc.notify_booking_confirmed(b)
    await db_session.commit()

    r = await client.get("/notifications", headers=customer_h)
    notif_id = r.json()["items"][0]["id"]

    r = await client.post(f"/notifications/{notif_id}/read", headers=customer_h)
    assert r.status_code == 204

    r = await client.get("/notifications", headers=customer_h)
    updated = next(n for n in r.json()["items"] if n["id"] == notif_id)
    assert updated["read_at"] is not None


async def test_mark_notification_read_other_user_forbidden(
    client: AsyncClient, db_session: AsyncSession, owner_h: dict, customer_h: dict, booking: dict
) -> None:
    mock_sender = AsyncMock()
    mock_sender.send = AsyncMock()
    svc = NotificationService(db_session, email_sender=mock_sender)

    result = await db_session.execute(select(Booking).where(Booking.id == booking["id"]))
    b = result.scalar_one()
    await svc.notify_booking_confirmed(b)
    await db_session.commit()

    r = await client.get("/notifications", headers=customer_h)
    notif_id = r.json()["items"][0]["id"]

    # Owner tries to mark customer's notification as read
    r = await client.post(f"/notifications/{notif_id}/read", headers=owner_h)
    assert r.status_code == 403
