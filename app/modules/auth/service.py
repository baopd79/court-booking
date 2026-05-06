"""Business logic for auth module."""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    EmailAlreadyExistsError,
    InvalidVerificationTokenError,
    TenantNotFoundError,
)
from app.core.security import hash_password, hash_token
from app.modules.auth.models import EmailVerificationToken, User, UserStatus
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    TenantRepository,
    UserRepository,
)
from app.modules.auth.schemas import (
    RegisterRequest,
    ResendVerificationRequest,
    UserResponse,
    VerifyEmailRequest,
)

logger = logging.getLogger(__name__)

_VERIFICATION_TTL_HOURS = 24


def _utcnow() -> datetime:
    """Naive UTC datetime for DB storage (TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.now(UTC).replace(tzinfo=None)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._tenants = TenantRepository(session)
        self._verify_tokens = EmailVerificationTokenRepository(session)

    async def register(self, data: RegisterRequest) -> UserResponse:
        if await self._users.get_by_email(data.email):
            raise EmailAlreadyExistsError("Email already registered")

        tenant = await self._tenants.get_default()
        if not tenant:
            raise TenantNotFoundError()

        user = User(
            tenant_id=tenant.id,
            email=data.email,
            password_hash=hash_password(data.password),
            role=data.role,
            full_name=data.full_name,
            phone=data.phone,
        )
        user = await self._users.create(user)
        await self._issue_verification_token(user.id, data.email)
        await self._session.commit()
        return UserResponse.model_validate(user)

    async def verify_email(self, data: VerifyEmailRequest) -> None:
        token = await self._verify_tokens.get_by_hash(hash_token(data.token))

        if not token or token.used_at is not None or token.expires_at < _utcnow():
            raise InvalidVerificationTokenError("Token is invalid or has expired")

        await self._verify_tokens.mark_used(token)
        user = await self._users.get_by_id(token.user_id)
        user.status = UserStatus.verified  # type: ignore[assignment]
        await self._users.save(user)
        await self._session.commit()

    async def resend_verification(self, data: ResendVerificationRequest) -> None:
        user = await self._users.get_by_email(data.email)
        # Silent no-op: don't leak whether email exists or is already verified
        if not user or user.status != UserStatus.unverified:
            return
        await self._issue_verification_token(user.id, data.email)
        await self._session.commit()

    async def _issue_verification_token(self, user_id: UUID, email: str) -> None:
        raw = secrets.token_urlsafe(32)
        token = EmailVerificationToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=_utcnow() + timedelta(hours=_VERIFICATION_TTL_HOURS),
        )
        await self._verify_tokens.create(token)
        # TODO(notification): replace with email send in Slice 8
        logger.info("Verification token for %s: %s", email, raw)
