"""Integration tests for Slice 2 — Auth."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.security import hash_token
from app.modules.auth.models import AuditLog, AuditOutcome, EmailVerificationToken, User, UserStatus

_EMAIL = "test@example.com"
_PASSWORD = "pass1234"
_PAYLOAD = {"email": _EMAIL, "password": _PASSWORD, "role": "customer"}


# ===== Fixtures =====


@pytest_asyncio.fixture
async def registered(client: AsyncClient, default_tenant: object) -> dict:
    r = await client.post("/auth/register", json=_PAYLOAD)
    assert r.status_code == 201
    return r.json()


@pytest_asyncio.fixture
async def db_user(db_session: AsyncSession, registered: dict) -> User:
    result = await db_session.execute(select(User).where(User.email == _EMAIL))
    return result.scalar_one()


@pytest_asyncio.fixture
async def verified(db_session: AsyncSession, db_user: User) -> User:
    db_user.status = UserStatus.verified  # type: ignore[assignment]
    db_session.add(db_user)
    await db_session.flush()
    return db_user


@pytest_asyncio.fixture
async def tokens(client: AsyncClient, verified: User) -> dict:
    r = await client.post("/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert r.status_code == 200
    return r.json()


@pytest_asyncio.fixture
async def auth_headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ===== Register =====


async def test_register_success(client: AsyncClient, default_tenant: object) -> None:
    r = await client.post("/auth/register", json=_PAYLOAD)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == _EMAIL
    assert body["status"] == "unverified"
    assert "password_hash" not in body


async def test_register_duplicate_email(client: AsyncClient, registered: dict) -> None:
    r = await client.post("/auth/register", json=_PAYLOAD)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


async def test_register_invalid_password(client: AsyncClient, default_tenant: object) -> None:
    # Too short
    r = await client.post(
        "/auth/register", json={**_PAYLOAD, "email": "a@b.com", "password": "abc"}
    )
    assert r.status_code == 422

    # No digit
    r = await client.post(
        "/auth/register", json={**_PAYLOAD, "email": "a@b.com", "password": "nodigithere"}
    )
    assert r.status_code == 422


# ===== Verify email =====


async def test_verify_email_success(
    client: AsyncClient, db_session: AsyncSession, db_user: User
) -> None:
    raw = secrets.token_urlsafe(32)
    db_session.add(
        EmailVerificationToken(
            user_id=db_user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
    )
    await db_session.flush()

    r = await client.post("/auth/verify-email", json={"token": raw})
    assert r.status_code == 200

    await db_session.refresh(db_user)
    assert db_user.status == UserStatus.verified


async def test_verify_email_invalid_token(client: AsyncClient, registered: dict) -> None:
    r = await client.post("/auth/verify-email", json={"token": "not-a-valid-token"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_VERIFICATION_TOKEN"


async def test_verify_email_expired_token(
    client: AsyncClient, db_session: AsyncSession, db_user: User
) -> None:
    raw = secrets.token_urlsafe(32)
    db_session.add(
        EmailVerificationToken(
            user_id=db_user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
        )
    )
    await db_session.flush()

    r = await client.post("/auth/verify-email", json={"token": raw})
    assert r.status_code == 400


# ===== Login =====


async def test_login_before_verify(client: AsyncClient, registered: dict) -> None:
    r = await client.post("/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


async def test_login_wrong_password(client: AsyncClient, verified: User) -> None:
    r = await client.post("/auth/login", json={"email": _EMAIL, "password": "wrongpass9"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_login_success(client: AsyncClient, verified: User) -> None:
    r = await client.post("/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == _EMAIL
    assert body["user"]["status"] == "verified"


async def test_login_writes_audit_log(
    client: AsyncClient, db_session: AsyncSession, verified: User
) -> None:
    await client.post("/auth/login", json={"email": _EMAIL, "password": _PASSWORD})

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == "login", AuditLog.outcome == AuditOutcome.success
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.user_id == verified.id


async def test_login_failed_writes_audit_log(
    client: AsyncClient, db_session: AsyncSession, verified: User
) -> None:
    await client.post("/auth/login", json={"email": _EMAIL, "password": "wrongpass9"})

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == "login", AuditLog.outcome == AuditOutcome.failed
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.meta == {"reason": "INVALID_CREDENTIALS"}


# ===== Refresh =====


async def test_refresh_rotation(client: AsyncClient, tokens: dict) -> None:
    old_refresh = tokens["refresh_token"]
    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200
    body = r.json()
    assert body["refresh_token"] != old_refresh
    assert "access_token" in body


async def test_refresh_revoked_token(client: AsyncClient, tokens: dict) -> None:
    old_refresh = tokens["refresh_token"]
    await client.post("/auth/refresh", json={"refresh_token": old_refresh})

    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


# ===== /auth/me =====


async def test_me_authenticated(client: AsyncClient, auth_headers: dict, verified: User) -> None:
    r = await client.get("/auth/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["email"] == _EMAIL


async def test_me_unauthenticated(client: AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


# ===== Logout =====


async def test_logout_success(client: AsyncClient, auth_headers: dict, tokens: dict) -> None:
    r = await client.post(
        "/auth/logout", headers=auth_headers, json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 204


async def test_logout_then_refresh(client: AsyncClient, auth_headers: dict, tokens: dict) -> None:
    await client.post(
        "/auth/logout", headers=auth_headers, json={"refresh_token": tokens["refresh_token"]}
    )
    r = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"
