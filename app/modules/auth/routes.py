"""HTTP routes for auth module."""

from fastapi import APIRouter, Request, status

from app.core.deps import AuthServiceDep, CurrentUserDep
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

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse
)
async def register(
    data: RegisterRequest,
    auth_service: AuthServiceDep,
) -> UserResponse:
    return await auth_service.register(data)


@router.post(
    "/verify-email",
    status_code=status.HTTP_200_OK,
    response_model=dict[str, str],
)
async def verify_email(
    data: VerifyEmailRequest,
    auth_service: AuthServiceDep,
) -> dict[str, str]:
    await auth_service.verify_email(data)
    return {"message": "Email verified successfully"}


@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    request: Request,
    auth_service: AuthServiceDep,
) -> LoginResponse:
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await auth_service.login(data, ip=ip, user_agent=user_agent)


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    data: RefreshRequest,
    request: Request,
    auth_service: AuthServiceDep,
) -> TokenPairResponse:
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await auth_service.refresh(data, ip=ip, user_agent=user_agent)


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: LogoutRequest,
    auth_service: AuthServiceDep,
    current_user: CurrentUserDep,
) -> None:
    await auth_service.logout(data.refresh_token, current_user.id)


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    data: ResendVerificationRequest,
    auth_service: AuthServiceDep,
) -> dict[str, str]:
    await auth_service.resend_verification(data)
    return {
        "message": "If your email is registered and unverified, a new link has been sent"
    }
