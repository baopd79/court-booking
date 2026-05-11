"""FastAPI application entry point.

Bootstrap order:
1. Load settings
2. Create FastAPI app với lifespan
3. Register routers (sẽ thêm ở các slice sau)
4. Health check endpoints
"""

import logging
import logging.config
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.exceptions import AppException
from app.core.redis import close_redis, redis_client
from app.jobs.auto_complete import auto_complete
from app.jobs.expire_holds import expire_holds
from app.jobs.generate_slots import generate_day_ahead
from app.jobs.notification_retry import notification_retry
from app.jobs.reconcile_payments import reconcile_payments
from app.modules.auth.routes import router as auth_router
from app.modules.booking.routes import router as booking_router
from app.modules.facility.routes import router as facility_router
from app.modules.facility.service import SlotService
from app.modules.notification.routes import router as notification_router
from app.modules.report.routes import router as report_router
from app.modules.payment.routes import router as payment_router

settings = get_settings()

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                "datefmt": "%H:%M:%S",
            },
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "default"},
        },
        "root": {"level": settings.log_level, "handlers": ["console"]},
        "loggers": {
            # Chỉ hiện WARNING+ từ các lib ồn ào — override bằng SQL_ECHO=true khi cần debug SQL
            "sqlalchemy.engine": {
                "level": "DEBUG" if settings.sql_echo else "WARNING",
                "propagate": True,
            },
            "sqlalchemy.pool": {"level": "WARNING", "propagate": True},
            "apscheduler": {"level": "WARNING", "propagate": True},
            # uvicorn tự quản lý logger của nó với colored formatter — không override ở đây
        },
    }
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """App lifecycle hooks."""
    logger.info("Starting app in %s mode", settings.app_env)

    scheduler: AsyncIOScheduler | None = None

    if settings.app_env != "test":
        # Generate slots for next 30 days on startup (idempotent)
        async with AsyncSession(get_engine(), expire_on_commit=False) as session:
            try:
                inserted = await SlotService(session).generate_upcoming(days=30)
                await session.commit()
                logging.getLogger(__name__).info("Startup slot generation: %d new slots", inserted)
            except Exception:
                await session.rollback()
                logging.getLogger(__name__).exception("Startup slot generation failed")

        # Nightly cron: generate day 31 at midnight UTC
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(generate_day_ahead, CronTrigger(hour=0, minute=0))
        scheduler.add_job(reconcile_payments, CronTrigger(minute="*/10"))
        scheduler.add_job(expire_holds, CronTrigger(minute="*/1"))
        scheduler.add_job(auto_complete, CronTrigger(minute="*/5"))
        scheduler.add_job(notification_retry, CronTrigger(minute="*/1"))
        scheduler.start()

    yield

    # === SHUTDOWN ===
    if scheduler:
        scheduler.shutdown(wait=False)
    logger.info("Shutting down")
    engine = get_engine()
    await engine.dispose()
    await close_redis()


app = FastAPI(
    title="Court Booking API",
    description="Sports court booking system — pickleball / badminton / tennis",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Exception handlers =====


@app.exception_handler(AppException)
async def app_exception_handler(_request: Request, exc: AppException) -> JSONResponse:
    error: dict = {"code": exc.code, "message": exc.message}
    if exc.details:
        error["details"] = exc.details
    return JSONResponse(status_code=exc.http_status, content={"error": error})


# ===== Routers =====

app.include_router(auth_router)
app.include_router(facility_router)
app.include_router(booking_router)
app.include_router(payment_router)
app.include_router(notification_router)
app.include_router(report_router)


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
        logger.exception("Database health check failed")
        checks["database"] = f"error: {type(e).__name__}: {e}"

    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        logger.exception("Redis health check failed")
        checks["redis"] = f"error: {type(e).__name__}: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ready" if all_ok else "degraded", "checks": checks}
