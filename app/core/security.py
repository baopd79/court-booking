"""Password hashing and JWT utilities."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import bcrypt
from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import get_settings
from app.core.exceptions import InvalidTokenError, TokenExpiredError


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: UUID, tenant_id: UUID, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_ttl_minutes),
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and verify JWT access token. Raises TokenError on invalid/expired."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except ExpiredSignatureError as exc:
        raise TokenExpiredError(str(exc)) from exc
    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc


def make_refresh_token() -> tuple[str, str, datetime]:
    """Return (raw_token, token_hash, expires_at). Store hash in DB; send raw to client."""
    settings = get_settings()
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days)
    return raw, hash_token(raw), expires_at


def hash_token(raw: str) -> str:
    """SHA-256 an opaque token for DB lookup (refresh token, email verification)."""
    return hashlib.sha256(raw.encode()).hexdigest()
