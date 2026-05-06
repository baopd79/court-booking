"""SQLModel table definitions for auth module: Tenant, User."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
