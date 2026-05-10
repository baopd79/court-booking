"""FastAPI dependencies shared across modules."""

from typing import Annotated
from uuid import UUID
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AccountSuspendedError, InvalidTokenError
from app.core.security import decode_access_token
from app.modules.auth.models import User, UserStatus
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import AuthService

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise InvalidTokenError("Authorization header required")

    payload = decode_access_token(credentials.credentials)

    user = await UserRepository(session).get_by_id(UUID(payload["sub"]))
    if not user:
        raise InvalidTokenError("User not found")
    if user.status == UserStatus.suspended:
        raise AccountSuspendedError("Your account has been suspended")
    return user


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(session)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
