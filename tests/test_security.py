"""Unit tests for app/core/security.py.

No DB or HTTP — pure function tests, no async needed.
Settings override via monkeypatching get_settings cache.
"""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from jose import jwt

from app.core.exceptions import InvalidTokenError, TokenExpiredError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    make_refresh_token,
    verify_password,
)

# ===== Helpers =====

USER_ID = uuid4()
TENANT_ID = uuid4()
ROLE = "admin"
SECRET = "a-secret-key-that-is-at-least-32-chars!!"
ALGORITHM = "HS256"


def _fake_settings(**overrides):
    from app.core.config import Settings

    defaults = dict(
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        jwt_secret_key=SECRET,
        jwt_algorithm=ALGORITHM,
        jwt_access_ttl_minutes=15,
        jwt_refresh_ttl_days=7,
    )
    defaults.update(overrides)
    return Settings.model_construct(**defaults)


# ===== Password hashing =====


def test_hash_password_returns_bcrypt_hash():
    hashed = hash_password("hunter2")
    assert hashed.startswith("$2b$")
    assert hashed != "hunter2"


def test_hash_password_different_salts():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2


def test_verify_password_correct():
    hashed = hash_password("correct-horse")
    assert verify_password("correct-horse", hashed) is True


def test_verify_password_wrong():
    hashed = hash_password("correct-horse")
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_empty_string():
    hashed = hash_password("")
    assert verify_password("", hashed) is True
    assert verify_password("notempty", hashed) is False


# ===== JWT create / decode =====


def test_create_access_token_is_valid_jwt():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        token = create_access_token(USER_ID, TENANT_ID, ROLE)

    payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    assert payload["sub"] == str(USER_ID)
    assert payload["tenant_id"] == str(TENANT_ID)
    assert payload["role"] == ROLE
    assert "jti" in payload
    assert "iat" in payload
    assert "exp" in payload


def test_create_access_token_exp_respects_ttl():
    with patch("app.core.security.get_settings", return_value=_fake_settings(jwt_access_ttl_minutes=30)):
        before = datetime.now(UTC)
        token = create_access_token(USER_ID, TENANT_ID, ROLE)
        after = datetime.now(UTC)

    payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    assert before + timedelta(minutes=29) < exp < after + timedelta(minutes=31)


def test_decode_access_token_round_trips():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        token = create_access_token(USER_ID, TENANT_ID, ROLE)
        payload = decode_access_token(token)

    assert payload["sub"] == str(USER_ID)
    assert payload["tenant_id"] == str(TENANT_ID)
    assert payload["role"] == ROLE


def test_decode_access_token_expired_raises():
    with patch("app.core.security.get_settings", return_value=_fake_settings(jwt_access_ttl_minutes=-1)):
        token = create_access_token(USER_ID, TENANT_ID, ROLE)

    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        with pytest.raises(TokenExpiredError):
            decode_access_token(token)


def test_decode_access_token_bad_signature_raises():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        token = create_access_token(USER_ID, TENANT_ID, ROLE)

    tampered = token[:-4] + "xxxx"
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        with pytest.raises(InvalidTokenError):
            decode_access_token(tampered)


def test_decode_access_token_wrong_secret_raises():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        token = create_access_token(USER_ID, TENANT_ID, ROLE)

    with patch("app.core.security.get_settings", return_value=_fake_settings(jwt_secret_key="a-completely-different-secret-key!!")):
        with pytest.raises(InvalidTokenError):
            decode_access_token(token)


def test_decode_access_token_malformed_raises():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        with pytest.raises(InvalidTokenError):
            decode_access_token("not.a.jwt")


def test_each_token_has_unique_jti():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        t1 = create_access_token(USER_ID, TENANT_ID, ROLE)
        t2 = create_access_token(USER_ID, TENANT_ID, ROLE)

    p1 = jwt.decode(t1, SECRET, algorithms=[ALGORITHM])
    p2 = jwt.decode(t2, SECRET, algorithms=[ALGORITHM])
    assert p1["jti"] != p2["jti"]


# ===== Refresh token =====


def test_make_refresh_token_returns_three_values():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        raw, hashed, expires_at = make_refresh_token()

    assert isinstance(raw, str)
    assert isinstance(hashed, str)
    assert isinstance(expires_at, datetime)


def test_make_refresh_token_hash_matches():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        raw, hashed, _ = make_refresh_token()

    assert hash_token(raw) == hashed


def test_make_refresh_token_raw_not_equal_hash():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        raw, hashed, _ = make_refresh_token()

    assert raw != hashed


def test_make_refresh_token_expiry_respects_ttl():
    with patch("app.core.security.get_settings", return_value=_fake_settings(jwt_refresh_ttl_days=7)):
        before = datetime.now(UTC)
        _, _, expires_at = make_refresh_token()
        after = datetime.now(UTC)

    assert before + timedelta(days=6, hours=23) < expires_at < after + timedelta(days=7, hours=1)


def test_make_refresh_token_unique_each_call():
    with patch("app.core.security.get_settings", return_value=_fake_settings()):
        raw1, _, _ = make_refresh_token()
        raw2, _, _ = make_refresh_token()

    assert raw1 != raw2


# ===== hash_token =====


def test_hash_token_deterministic():
    assert hash_token("abc") == hash_token("abc")


def test_hash_token_different_inputs():
    assert hash_token("abc") != hash_token("xyz")


def test_hash_token_is_sha256_hex():
    result = hash_token("test")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
