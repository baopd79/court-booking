"""Database engine + session factory.

Engine lazy-init để tương thích uvicorn --reload và testing.
"""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Lazy singleton — engine tạo khi gọi lần đầu, cache lại."""
    settings = get_settings()
    return create_async_engine(
        str(settings.database_url),
        echo=settings.sql_echo,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazy session factory."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield session, auto close on exit.

    KHÔNG commit ở đây — service layer tự commit.
    """
    async with get_session_factory()() as session:
        yield session
