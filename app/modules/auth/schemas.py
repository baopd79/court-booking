"""Request/response schemas for auth module."""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, EmailStr, field_validator
from sqlmodel import SQLModel

from app.modules.auth.models import UserRole, UserStatus

# ===== Requests =====


class RegisterRequest(SQLModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    phone: str | None = None
    role: UserRole = UserRole.customer

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("at least 8 characters")
        if not any(c.isdigit() for c in v):
            raise ValueError("must contain at least one digit")
        return v


class VerifyEmailRequest(SQLModel):
    token: str


class ResendVerificationRequest(SQLModel):
    email: EmailStr


class LoginRequest(SQLModel):
    email: EmailStr
    password: str


class RefreshRequest(SQLModel):
    refresh_token: str


class LogoutRequest(SQLModel):
    refresh_token: str


# ===== Responses =====


class UserResponse(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: UserRole
    status: UserStatus
    full_name: str | None
    phone: str | None
    created_at: datetime


class TokenPairResponse(SQLModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginResponse(SQLModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
