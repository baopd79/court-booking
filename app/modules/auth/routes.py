"""HTTP routes for auth module."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenPairResponse,
    UserResponse,
    VerifyEmailRequest,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def register(
    data: RegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await AuthService(session).register(data)


@router.post("/verify-email")
async def verify_email(
    data: VerifyEmailRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await AuthService(session).verify_email(data)
    return {"message": "Email verified successfully"}


@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await AuthService(session).login(data, ip=ip, user_agent=user_agent)


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    data: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> TokenPairResponse:
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await AuthService(session).refresh(data, ip=ip, user_agent=user_agent)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: LogoutRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    await AuthService(session).logout(data.refresh_token, current_user.id)


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    data: ResendVerificationRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await AuthService(session).resend_verification(data)
    return {"message": "If your email is registered and unverified, a new link has been sent"}
