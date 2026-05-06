"""FastAPI application entry point.

Bootstrap order:
1. Load settings
2. Create FastAPI app với lifespan
3. Register routers (sẽ thêm ở các slice sau)
4. Health check endpoints
"""

import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.redis import close_redis, redis_client

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """App lifecycle hooks.

    Startup: chỉ log info — pool connection lazy (tạo khi request đầu tiên).
    Shutdown: đóng pool DB + Redis để graceful exit.
    """
    # === STARTUP ===
    print(f"🚀 Starting app in {settings.app_env} mode")
    yield

    # === SHUTDOWN ===
    print("🛑 Shutting down")
    await get_engine.dispose()  # close all DB connections in pool
    await close_redis()  # close Redis pool


app = FastAPI(
    title="Court Booking API",
    description="Sports court booking system — pickleball / badminton / tennis",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)


# ===== Health checks =====


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """Liveness probe — process còn sống.

    KHÔNG check dependencies. App freeze → orchestrator restart.
    """
    return {"status": "alive"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        traceback.print_exc()  # ← in full traceback ra console
        checks["database"] = f"error: {type(e).__name__}: {e}"

    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        traceback.print_exc()
        checks["redis"] = f"error: {type(e).__name__}: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ready" if all_ok else "degraded", "checks": checks}
