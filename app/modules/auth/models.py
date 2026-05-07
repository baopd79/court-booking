"""SQLModel table definitions for auth module: Tenant, User."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class UserRole(StrEnum):
    customer = "customer"
    owner = "owner"


class UserStatus(StrEnum):
    unverified = "unverified"
    verified = "verified"
    suspended = "suspended"


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class EmailVerificationToken(SQLModel, table=True):
    __tablename__ = "email_verification_tokens"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id")
    token_hash: str = Field(unique=True)
    expires_at: datetime
    used_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id")
    token_hash: str = Field(unique=True)
    expires_at: datetime
    revoked_at: datetime | None = Field(default=None)
    user_agent: str | None = Field(default=None)
    ip: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class AuditOutcome(StrEnum):
    success = "success"
    failed = "failed"


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    event_type: str
    ip: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)
    outcome: AuditOutcome
    # "metadata" is reserved in SQLAlchemy — mapped via sa_column
    meta: dict | None = Field(
        default=None, sa_column=sa.Column("metadata", JSONB, nullable=True)
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id")
    email: str = Field(unique=True, index=True)
    password_hash: str
    role: UserRole
    status: UserStatus = Field(default=UserStatus.unverified)
    full_name: str | None = Field(default=None)
    phone: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
