"""Database query layer for auth module."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.modules.auth.models import Tenant, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def save(self, user: User) -> User:
        """Persist changes to an already-tracked user (status update, etc.)."""
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_default(self) -> Tenant | None:
        """Return the single MVP tenant, or None if not seeded yet."""
        result = await self._session.execute(select(Tenant).limit(1))
        return result.scalar_one_or_none()
