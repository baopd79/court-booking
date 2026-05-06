"""Pytest fixtures shared across all tests.

Strategy:
- DB engine: session-scoped (1 lần cho toàn bộ test run)
- DB session: function-scoped (mỗi test 1 transaction, rollback cuối)
- HTTP client: function-scoped (đảm bảo không leak state giữa test)
"""

import subprocess
import sys
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app as fastapi_app

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


# ===== Engine: session scope =====


@pytest_asyncio.fixture(scope="session")
async def engine(test_settings: Settings) -> AsyncGenerator[AsyncEngine, None]:
    """Async engine cho test DB. Apply migration qua subprocess.

    Subprocess: tránh nested asyncio.run() (alembic env.py dùng asyncio.run
    cho async migration, không gọi được từ trong event loop của pytest).
    """
    test_db_url = str(test_settings.database_url)

    # Reset schema: downgrade base → upgrade head
    env = {
        **dict(__import__("os").environ),
        "DATABASE_URL": test_db_url,
    }

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

    eng = create_async_engine(
        test_db_url,
        echo=False,
        pool_pre_ping=True,
    )

    yield eng

    await eng.dispose()


# ===== DB session: function scope =====


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """1 session per test, wrap trong transaction, rollback cuối.

    Pattern: nested transaction
    - Outer: connection.begin()
    - Inner: session dùng savepoint, commit chỉ commit savepoint
    - Cuối test rollback outer → mọi thay đổi bay sạch
    """
    async with engine.connect() as conn:
        trans = await conn.begin()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


# ===== FastAPI app + client =====


@pytest.fixture(scope="session")
def app() -> Any:
    """FastAPI app instance, override DB dependency."""
    return fastapi_app


@pytest_asyncio.fixture
async def client(app: Any, db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client với DB session đã override.

    Override get_db dependency để mọi endpoint dùng session test.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
