"""HTTP routes for auth module."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.schemas import (
    RegisterRequest,
    ResendVerificationRequest,
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


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    data: ResendVerificationRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await AuthService(session).resend_verification(data)
    return {"message": "If your email is registered and unverified, a new link has been sent"}
