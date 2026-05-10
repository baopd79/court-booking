"""Business logic for auth module."""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountSuspendedError,
    AppException,
    EmailAlreadyExistsError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidTokenError,
    InvalidVerificationTokenError,
    TenantNotFoundError,
    TokenExpiredError,
)
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    make_refresh_token,
    verify_password,
)
from app.modules.auth.models import (
    AuditLog,
    AuditOutcome,
    EmailVerificationToken,
    RefreshToken,
    User,
    UserStatus,
)
from app.modules.auth.repository import (
    AuditLogRepository,
    EmailVerificationTokenRepository,
    RefreshTokenRepository,
    TenantRepository,
    UserRepository,
)
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenPairResponse,
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
        self._refresh_tokens = RefreshTokenRepository(session)
        self._audit = AuditLogRepository(session)

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

        user = await self._users.get_by_id(token.user_id)
        if user is None:
            raise InvalidVerificationTokenError("User no longer exists")

        await self._verify_tokens.mark_used(token)
        user.status = UserStatus.verified
        await self._users.create(user)
        await self._session.commit()

    async def resend_verification(self, data: ResendVerificationRequest) -> None:
        user = await self._users.get_by_email(data.email)
        # Silent no-op: don't leak whether email exists or is already verified
        if not user or user.status != UserStatus.unverified:
            return
        await self._issue_verification_token(user.id, data.email)
        await self._session.commit()

    async def login(
        self, data: LoginRequest, ip: str | None, user_agent: str | None
    ) -> LoginResponse:
        user = await self._users.get_by_email(data.email)
        try:
            if not user or not verify_password(data.password, user.password_hash):
                raise InvalidCredentialsError("Invalid email or password")
            if user.status == UserStatus.unverified:
                raise EmailNotVerifiedError(
                    "Please verify your email before logging in"
                )
            if user.status == UserStatus.suspended:
                raise AccountSuspendedError("Your account has been suspended")
        except AppException as exc:
            await self._audit.create(
                AuditLog(
                    user_id=user.id if user else None,
                    event_type="login",
                    outcome=AuditOutcome.failed,
                    ip=ip,
                    user_agent=user_agent,
                    meta={"reason": exc.code},
                )
            )
            await self._session.commit()
            raise

        access_token = create_access_token(user.id, user.tenant_id, user.role)
        raw, token_hash, expires_at = make_refresh_token()

        await self._refresh_tokens.create(
            RefreshToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at.replace(tzinfo=None),
                ip=ip,
                user_agent=user_agent,
            )
        )
        await self._audit.create(
            AuditLog(
                user_id=user.id,
                event_type="login",
                outcome=AuditOutcome.success,
                ip=ip,
                user_agent=user_agent,
            )
        )
        await self._session.commit()
        return LoginResponse(
            access_token=access_token,
            refresh_token=raw,
            user=UserResponse.model_validate(user),
        )

    async def refresh(
        self, data: RefreshRequest, ip: str | None, user_agent: str | None
    ) -> TokenPairResponse:
        stored = await self._refresh_tokens.get_by_hash(hash_token(data.refresh_token))

        if not stored or stored.revoked_at is not None:
            raise InvalidTokenError("Refresh token is invalid or has been revoked")
        if stored.expires_at < _utcnow():
            raise TokenExpiredError("Refresh token has expired")

        user = await self._users.get_by_id(stored.user_id)
        if user is None:
            # User bị xóa sau khi token được tạo — token vô hiệu
            raise InvalidTokenError("User associated with token no longer exists")
        if user.status == UserStatus.suspended:
            raise AccountSuspendedError("Your account has been suspended")

        await self._refresh_tokens.revoke(stored)

        access_token = create_access_token(user.id, user.tenant_id, user.role)
        raw, token_hash, expires_at = make_refresh_token()

        await self._refresh_tokens.create(
            RefreshToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at.replace(tzinfo=None),
                ip=ip,
                user_agent=user_agent,
            )
        )
        await self._session.commit()
        return TokenPairResponse(access_token=access_token, refresh_token=raw)

    async def logout(self, refresh_token: str, current_user_id: UUID) -> None:
        stored = await self._refresh_tokens.get_by_hash(hash_token(refresh_token))
        # Idempotent: already revoked / not found → 204 silently
        if not stored or stored.revoked_at is not None:
            return
        # Don't leak that token belongs to another user
        if stored.user_id != current_user_id:
            return
        await self._refresh_tokens.revoke(stored)
        await self._session.commit()

    async def _issue_verification_token(self, user_id: UUID, email: str) -> None:
        raw = secrets.token_urlsafe(32)
        token = EmailVerificationToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=_utcnow() + timedelta(hours=_VERIFICATION_TTL_HOURS),
        )
        await self._verify_tokens.create(token)
        # TODO(slice-8): gửi email thật thay cho log này.
        # ⚠️ SECURITY: raw token trong log — chỉ dùng trong dev, KHÔNG deploy production!
        logger.warning("[DEV ONLY] Verification token for %s: %s", email, raw)
