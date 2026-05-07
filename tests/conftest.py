"""Pytest fixtures shared across all tests.

Strategy:
- Migration: session-scoped sync fixture chạy alembic một lần
- DB session: function-scoped async, tạo engine riêng trong function loop
  → tránh asyncpg Future cross event-loop với pytest-asyncio 1.x
- HTTP client: function-scoped (đảm bảo không leak state giữa test)
"""

import subprocess
import sys
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app as fastapi_app
from app.modules.auth.models import Tenant

# ===== Settings override cho test =====


def get_test_settings() -> Settings:
    """Settings cho test env — point tới court_booking_test DB."""
    base = get_settings()
    return base.model_copy(
        update={
            "app_env": "test",
            "database_url": str(base.database_url).replace("/court_booking", "/court_booking_test"),
        }
    )


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return get_test_settings()


# ===== Migration: session-scoped SYNC fixture =====


@pytest.fixture(scope="session", autouse=True)
def run_migrations(test_settings: Settings) -> Generator[None, None, None]:
    """Apply migrations to test DB once per session (sync — no event loop binding)."""
    test_db_url = str(test_settings.database_url)
    env = {**dict(__import__("os").environ), "DATABASE_URL": test_db_url}

    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *cmd],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Alembic {cmd} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
    yield


# ===== DB session: function-scoped, fresh engine per test =====


@pytest_asyncio.fixture
async def db_session(test_settings: Settings) -> AsyncGenerator[AsyncSession, None]:
    """Fresh engine per test — tránh asyncpg Future cross event-loop.

    pytest-asyncio 1.x: session-scoped async fixtures dùng session loop nhưng
    test functions dùng function loop → cross-loop error nếu dùng chung engine.
    Giải pháp: mỗi test tạo engine + connection riêng trong function loop của nó.

    Isolation: outer BEGIN + create_savepoint mode —
    service.commit() → RELEASE SAVEPOINT (không commit thật vào DB),
    trans.rollback() dọn sạch sau mỗi test.
    """
    _engine = create_async_engine(str(test_settings.database_url), pool_pre_ping=True)
    conn = await _engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await _engine.dispose()


# ===== Tenant seed =====


@pytest_asyncio.fixture
async def default_tenant(db_session: AsyncSession) -> Tenant:
    """Seed tenant trong savepoint của test — rollback sạch sau mỗi test."""
    tenant = Tenant(name="Default")
    db_session.add(tenant)
    await db_session.flush()
    return tenant


# ===== FastAPI app + client =====


@pytest.fixture(scope="session")
def app() -> Any:
    """FastAPI app instance."""
    return fastapi_app


@pytest_asyncio.fixture
async def client(app: Any, db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client với DB session đã override."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
