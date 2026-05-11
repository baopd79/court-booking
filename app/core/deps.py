"""FastAPI dependencies shared across modules."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AccountSuspendedError, ForbiddenError, InvalidTokenError
from app.core.security import decode_access_token
from app.modules.auth.models import User, UserRole, UserStatus
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import AuthService
from app.modules.facility.service import (
    CourtService,
    FacilityService,
    PricingRuleService,
    SlotService,
)

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


async def get_current_owner(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != UserRole.owner:
        raise ForbiddenError("Owner access required")
    return user


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(session)


def get_facility_service(session: AsyncSession = Depends(get_db)) -> FacilityService:
    return FacilityService(session)


def get_court_service(session: AsyncSession = Depends(get_db)) -> CourtService:
    return CourtService(session)


def get_pricing_service(session: AsyncSession = Depends(get_db)) -> PricingRuleService:
    return PricingRuleService(session)


def get_slot_service(session: AsyncSession = Depends(get_db)) -> SlotService:
    return SlotService(session)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
OwnerDep = Annotated[User, Depends(get_current_owner)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
FacilityServiceDep = Annotated[FacilityService, Depends(get_facility_service)]
CourtServiceDep = Annotated[CourtService, Depends(get_court_service)]
PricingServiceDep = Annotated[PricingRuleService, Depends(get_pricing_service)]
SlotServiceDep = Annotated[SlotService, Depends(get_slot_service)]
