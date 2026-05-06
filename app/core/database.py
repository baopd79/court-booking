"""Database engine + session factory.

Pattern:
- Engine = singleton, created at app startup
- Session = per-request, injected via FastAPI Depends
- Caller (service layer) chịu trách nhiệm commit/rollback
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# Engine: singleton, manage connection pool
engine = create_async_engine(
    str(settings.database_url),
    echo=settings.debug,  # log SQL khi DEBUG=true
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # detect stale connection
    pool_recycle=3600,  # recycle sau 1h
)

# Session factory: tạo session mới mỗi request
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # standard cho async
    autoflush=False,  # explicit flush, tránh surprise query
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield session, auto close on exit.

    KHÔNG commit ở đây — service layer tự commit.
    Exception trong request → session close → auto rollback.
    """
    async with AsyncSessionLocal() as session:
        yield session
