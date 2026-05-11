"""FastAPI dependencies shared across modules."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AccountSuspendedError, ForbiddenError, InvalidTokenError
from app.core.security import decode_access_token
from app.core.vnpay import VNPayClient, get_vnpay_client
from app.modules.auth.models import User, UserRole, UserStatus
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import AuthService
from app.modules.booking.service import BookingService
from app.modules.facility.service import (
    CourtService,
    FacilityService,
    PricingRuleService,
    SlotService,
)
from app.modules.notification.service import NotificationService
from app.modules.payment.service import PaymentService
from app.modules.report.service import ReportService

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


async def get_current_customer(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != UserRole.customer:
        raise ForbiddenError("Customer access required")
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


def get_booking_service(session: AsyncSession = Depends(get_db)) -> BookingService:
    return BookingService(session)


def get_vnpay_client_dep() -> VNPayClient:
    return get_vnpay_client()


def get_payment_service(
    session: AsyncSession = Depends(get_db),
    vnpay_client: VNPayClient = Depends(get_vnpay_client_dep),
) -> PaymentService:
    return PaymentService(session, vnpay_client)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
OwnerDep = Annotated[User, Depends(get_current_owner)]
CustomerDep = Annotated[User, Depends(get_current_customer)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
FacilityServiceDep = Annotated[FacilityService, Depends(get_facility_service)]
CourtServiceDep = Annotated[CourtService, Depends(get_court_service)]
PricingServiceDep = Annotated[PricingRuleService, Depends(get_pricing_service)]
SlotServiceDep = Annotated[SlotService, Depends(get_slot_service)]
BookingServiceDep = Annotated[BookingService, Depends(get_booking_service)]
PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]


def get_notification_service(session: AsyncSession = Depends(get_db)) -> NotificationService:
    return NotificationService(session)


NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]


def get_report_service(session: AsyncSession = Depends(get_db)) -> ReportService:
    return ReportService(session)


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
