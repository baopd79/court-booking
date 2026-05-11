"""Integration tests for Slice 6 — Payment (VNPay)."""

import hashlib
import hmac
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_vnpay_client_dep
from app.core.vnpay import VNPayClient
from app.main import app as fastapi_app
from app.modules.auth.models import UserStatus
from app.modules.booking.models import BookingStatus
from app.modules.facility.models import SlotStatus
from app.modules.payment.models import PaymentStatus

# ===== Test VNPay credentials =====

TEST_TMN = "TESTTMN01"
TEST_SECRET = "TESTSECRET12345678901234567890123456789"
TEST_PAY_URL = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
TEST_API_URL = "https://sandbox.vnpayment.vn/merchant_webapi/api/transaction"

TEST_VNPAY = VNPayClient(
    tmn_code=TEST_TMN,
    hash_secret=TEST_SECRET,
    payment_url=TEST_PAY_URL,
    api_url=TEST_API_URL,
)


def _sign(params: dict, secret: str = TEST_SECRET) -> str:
    clean = {k: v for k, v in params.items() if k not in ("vnp_SecureHash", "vnp_SecureHashType")}
    query = "&".join(f"{k}={v}" for k, v in sorted(clean.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha512).hexdigest()


def _make_ipn_params(
    txn_ref: str,
    amount: int,
    response_code: str = "00",
    txn_status: str = "00",
) -> dict:
    params = {
        "vnp_TxnRef": txn_ref,
        "vnp_Amount": str(amount),
        "vnp_ResponseCode": response_code,
        "vnp_TransactionStatus": txn_status,
        "vnp_TransactionNo": "123456",
        "vnp_BankCode": "NCB",
        "vnp_PayDate": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
    }
    params["vnp_SecureHash"] = _sign(params)
    return params


# ===== Payment-specific client fixture =====


@pytest_asyncio.fixture
async def payment_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_vnpay_client_dep] = lambda: TEST_VNPAY

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac

    fastapi_app.dependency_overrides.clear()


# ===== Helpers =====


async def _full_setup(
    client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> dict:
    """Set up owner, customer, court, slots, and a pending booking."""
    from sqlmodel import select

    from app.modules.auth.models import User

    # Register + login owner
    await client.post(
        "/auth/register",
        json={
            "email": "owner@payment-test.com",
            "password": "pass1234",
            "role": "owner",
            "full_name": "Owner",
        },
    )
    result = await db_session.execute(select(User).where(User.email == "owner@payment-test.com"))
    owner = result.scalar_one()
    owner.status = UserStatus.verified
    db_session.add(owner)
    await db_session.flush()
    r = await client.post(
        "/auth/login", json={"email": "owner@payment-test.com", "password": "pass1234"}
    )
    owner_token = r.json()["access_token"]

    # Register + login customer
    await client.post(
        "/auth/register",
        json={
            "email": "cust@payment-test.com",
            "password": "pass1234",
            "role": "customer",
            "full_name": "Cust",
        },
    )
    result = await db_session.execute(select(User).where(User.email == "cust@payment-test.com"))
    cust = result.scalar_one()
    cust.status = UserStatus.verified
    db_session.add(cust)
    await db_session.flush()
    r = await client.post(
        "/auth/login", json={"email": "cust@payment-test.com", "password": "pass1234"}
    )
    cust_token = r.json()["access_token"]

    # Court setup
    r = await client.post(
        "/facilities",
        json={"name": "Pay Fac", "address": "1 St"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    facility_id = r.json()["id"]
    r = await client.post(
        "/courts",
        json={
            "facility_id": facility_id,
            "name": "Court P",
            "sport_type": "badminton",
            "default_price": "150000",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    court_id = r.json()["id"]
    rules = [
        {"day_of_week": i, "start_time": "06:00", "end_time": "22:00", "price": "150000"}
        for i in range(7)
    ]
    await client.put(
        f"/courts/{court_id}/pricing",
        json={"rules": rules},
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    # Generate slots via same db_session (no new engine needed)
    from app.modules.facility.service import SlotService

    today = datetime.now(UTC).replace(tzinfo=None).date()
    await SlotService(db_session).generate_for_all_courts_on_date(today)

    # Pick 2 available slots
    r = await client.get(f"/courts/availability?facility_id={facility_id}&date={today.isoformat()}")
    available = [s for c in r.json()["courts"] for s in c["slots"] if s["status"] == "available"]
    slot_ids = [available[0]["id"], available[1]["id"]]

    # Create booking
    r = await client.post(
        "/bookings",
        json={"court_id": court_id, "slot_ids": slot_ids},
        headers={"Authorization": f"Bearer {cust_token}", "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201
    booking = r.json()

    return {
        "owner_token": owner_token,
        "cust_token": cust_token,
        "court_id": court_id,
        "facility_id": facility_id,
        "slot_ids": slot_ids,
        "booking_id": booking["id"],
        "total_amount": float(booking["total_amount"]),
    }


@pytest.mark.asyncio
async def test_initiate_creates_payment_url(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    r = await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "payment_url" in data
    assert TEST_TMN in data["payment_url"]
    assert data["is_existing"] is False
    assert "expires_at" in data


@pytest.mark.asyncio
async def test_initiate_requires_idempotency_key(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    r = await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={"Authorization": f"Bearer {ctx['cust_token']}"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"


@pytest.mark.asyncio
async def test_initiate_idempotent_replay(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)
    idem_key = str(uuid.uuid4())
    payload = {"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"}
    headers = {"Authorization": f"Bearer {ctx['cust_token']}", "Idempotency-Key": idem_key}

    r1 = await payment_client.post("/payments/initiate", json=payload, headers=headers)
    r2 = await payment_client.post("/payments/initiate", json=payload, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["payment_url"] == r2.json()["payment_url"]


@pytest.mark.asyncio
async def test_initiate_returns_existing_url_when_processing(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    r1 = await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert r1.status_code == 200

    # Second initiate with different idempotency key — booking is now payment_processing
    r2 = await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert r2.status_code == 200
    assert r2.json()["is_existing"] is True
    assert r2.json()["payment_url"] == r1.json()["payment_url"]


@pytest.mark.asyncio
async def test_initiate_booking_not_found(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    r = await payment_client.post(
        "/payments/initiate",
        json={"booking_id": str(uuid.uuid4()), "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "BOOKING_NOT_FOUND"


@pytest.mark.asyncio
async def test_initiate_booking_not_payable(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    # Initiate once to move to payment_processing
    await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )

    # Manually set to confirmed in DB to test BOOKING_NOT_PAYABLE
    from sqlmodel import select

    from app.modules.booking.models import Booking

    result = await db_session.execute(select(Booking).where(Booking.id == ctx["booking_id"]))
    booking = result.scalar_one()
    booking.status = BookingStatus.confirmed
    db_session.add(booking)
    await db_session.flush()

    r = await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "BOOKING_NOT_PAYABLE"


@pytest.mark.asyncio
async def test_initiate_booking_expired(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    # Set hold_expires_at in the past
    from sqlmodel import select

    from app.modules.booking.models import Booking

    result = await db_session.execute(select(Booking).where(Booking.id == ctx["booking_id"]))
    booking = result.scalar_one()
    booking.hold_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db_session.add(booking)
    await db_session.flush()

    r = await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "BOOKING_EXPIRED"


async def _get_txn_ref(db_session: AsyncSession, booking_id: str) -> tuple[str, int]:
    """Return (txn_ref, amount_x100) for the latest payment of a booking."""
    from uuid import UUID

    from sqlmodel import select

    from app.modules.payment.models import Payment

    result = await db_session.execute(select(Payment).where(Payment.booking_id == UUID(booking_id)))
    payment = result.scalar_one()
    amount_x100 = int(payment.amount * 100)
    return payment.vnpay_txn_ref, amount_x100


@pytest.mark.asyncio
async def test_ipn_success_confirms_booking_and_books_slots(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    txn_ref, amount_x100 = await _get_txn_ref(db_session, ctx["booking_id"])

    params = _make_ipn_params(txn_ref, amount_x100, response_code="00")
    r = await payment_client.post(f"/payments/vnpay-ipn?{urllib.parse.urlencode(params)}")
    assert r.status_code == 200
    assert r.json()["RspCode"] == "00"

    # Booking → confirmed
    from uuid import UUID

    from sqlmodel import select

    from app.modules.booking.models import Booking

    result = await db_session.execute(select(Booking).where(Booking.id == UUID(ctx["booking_id"])))
    booking = result.scalar_one()
    assert booking.status == BookingStatus.confirmed

    # Slots → booked
    from app.modules.facility.models import Slot

    result = await db_session.execute(
        select(Slot).where(Slot.id.in_(ctx["slot_ids"]))  # type: ignore[union-attr]
    )
    slots = result.scalars().all()
    assert all(s.status == SlotStatus.booked for s in slots)

    # Payment → success
    from app.modules.payment.models import Payment

    result = await db_session.execute(
        select(Payment).where(Payment.booking_id == UUID(ctx["booking_id"]))
    )
    payment = result.scalar_one()
    assert payment.status == PaymentStatus.success
    assert payment.paid_at is not None


@pytest.mark.asyncio
async def test_ipn_failure_sets_payment_failed_and_frees_slots(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    txn_ref, amount_x100 = await _get_txn_ref(db_session, ctx["booking_id"])

    params = _make_ipn_params(txn_ref, amount_x100, response_code="24", txn_status="02")
    r = await payment_client.post(f"/payments/vnpay-ipn?{urllib.parse.urlencode(params)}")
    assert r.status_code == 200
    assert r.json()["RspCode"] == "00"

    from uuid import UUID

    from sqlmodel import select

    from app.modules.booking.models import Booking

    result = await db_session.execute(select(Booking).where(Booking.id == UUID(ctx["booking_id"])))
    assert result.scalar_one().status == BookingStatus.payment_failed

    from app.modules.facility.models import Slot

    result = await db_session.execute(
        select(Slot).where(Slot.id.in_(ctx["slot_ids"]))  # type: ignore[union-attr]
    )
    assert all(s.status == SlotStatus.available for s in result.scalars().all())


@pytest.mark.asyncio
async def test_ipn_invalid_signature_rejected(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    txn_ref, amount_x100 = await _get_txn_ref(db_session, ctx["booking_id"])

    params = _make_ipn_params(txn_ref, amount_x100)
    params["vnp_SecureHash"] = "badhash"
    r = await payment_client.post(f"/payments/vnpay-ipn?{urllib.parse.urlencode(params)}")
    assert r.status_code == 200
    assert r.json()["RspCode"] == "97"


@pytest.mark.asyncio
async def test_ipn_duplicate_returns_already_processed(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    txn_ref, amount_x100 = await _get_txn_ref(db_session, ctx["booking_id"])

    params = _make_ipn_params(txn_ref, amount_x100)
    qs = urllib.parse.urlencode(params)
    r1 = await payment_client.post(f"/payments/vnpay-ipn?{qs}")
    r2 = await payment_client.post(f"/payments/vnpay-ipn?{qs}")

    assert r1.json()["RspCode"] == "00"
    assert r2.json()["RspCode"] == "00"
    assert r2.json()["Message"] == "Already processed"


@pytest.mark.asyncio
async def test_ipn_amount_mismatch_rejected(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    txn_ref, _ = await _get_txn_ref(db_session, ctx["booking_id"])

    wrong_amount = 99999999  # != booking total
    params = _make_ipn_params(txn_ref, wrong_amount)
    r = await payment_client.post(f"/payments/vnpay-ipn?{urllib.parse.urlencode(params)}")
    assert r.status_code == 200
    assert r.json()["RspCode"] == "04"


@pytest.mark.asyncio
async def test_return_valid_signature(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    ctx = await _full_setup(payment_client, db_session, default_tenant)

    await payment_client.post(
        "/payments/initiate",
        json={"booking_id": ctx["booking_id"], "return_url": "https://example.com/return"},
        headers={
            "Authorization": f"Bearer {ctx['cust_token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    txn_ref, amount_x100 = await _get_txn_ref(db_session, ctx["booking_id"])

    params = _make_ipn_params(txn_ref, amount_x100, response_code="00")
    r = await payment_client.get(f"/payments/vnpay-return?{urllib.parse.urlencode(params)}")
    assert r.status_code == 200
    data = r.json()
    assert data["payment_status"] == "success"
    assert data["booking_id"] == ctx["booking_id"]


@pytest.mark.asyncio
async def test_return_invalid_signature(
    payment_client: AsyncClient, db_session: AsyncSession, default_tenant: object
) -> None:
    params = {"vnp_TxnRef": "abc", "vnp_ResponseCode": "00", "vnp_SecureHash": "badhash"}
    r = await payment_client.get(f"/payments/vnpay-return?{urllib.parse.urlencode(params)}")
    assert r.status_code == 200
    assert r.json()["payment_status"] == "failed"
    assert r.json()["message"] == "Invalid signature"
