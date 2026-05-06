# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
make install-dev     # Install all dependencies (uv)
make up              # Start Postgres + Redis via docker-compose

# Development
make dev             # Run with hot reload on port 8000
make migrate         # Apply all Alembic migrations
make migration m="description"  # Generate new migration

# Code quality
make format          # Format + lint (ruff)
make lint            # Lint check only

# Testing
make test            # Run all tests
make test-cov        # Run with coverage
make test-fast       # Skip slow/concurrency tests
pytest tests/path/to/test_file.py::test_name  # Single test
```

## Architecture

**Stack:** Python 3.12, FastAPI, SQLModel + asyncpg (PostgreSQL 15), Redis 7, APScheduler, Alembic. Package management via `uv`.

**Layers:**
- `app/main.py` — FastAPI app with lifespan hooks managing DB/Redis pool startup/shutdown. Health endpoints at `/health/live` and `/health/ready`.
- `app/core/` — Infrastructure singletons: `config.py` (pydantic-settings), `database.py` (async engine + session via `@lru_cache`), `redis.py` (client singleton). Planned additions: `security.py` (JWT + bcrypt), `exceptions.py` (custom error types), `deps.py` (FastAPI dependencies — `get_current_user`, etc.).
- `app/modules/` — 6 bounded contexts: `auth`, `facility`, `booking`, `payment`, `notification`, `report`. Each module follows: `models.py` → `schemas.py` → `repository.py` → `service.py` → `routes.py`.
- `app/jobs/` — Background jobs via APScheduler: `expire_holds.py` (cron 1 min: holds > 10 min → `expired`), `auto_complete.py` (cron 5 min: ended bookings → `completed`), `reconcile_payments.py` (cron 10 min: VNPay pending > 30 min), `notification_retry.py` (worker: retry failed notifications).
- `alembic/` — Migrations with date-based filenames (`YYYYMMDD_HHMM_<rev>_<slug>.py`). Autogenerate via `make migration`.
- `tests/conftest.py` — Session-scoped DB engine, function-scoped sessions with savepoint rollback for isolation, function-scoped HTTP client with dependency override.

**Async-first:** All DB, Redis, and HTTP operations are async (`sqlalchemy[asyncio]`, `asyncpg`, `httpx`). The engine and session factory use `@lru_cache` to support `uvicorn --reload` and test isolation.

**Test database:** `court_booking_test` (separate from dev DB `court_booking`). Tests reset schema via Alembic downgrade/upgrade in a subprocess before the session. Function-scoped sessions use nested transactions (savepoints) so each test rolls back cleanly.

## Key Configuration

All settings come from environment variables validated by pydantic-settings (`app/core/config.py`). Copy `.env.example` to `.env` for local dev. Critical vars:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://...` |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | Min 32 chars |
| `APP_ENV` | `dev` / `test` / `staging` / `prod` |

## Migrations

```bash
make migration m="add user table"  # Generates alembic/versions/YYYYMMDD_HHMM_<rev>_add_user_table.py
make migrate                       # alembic upgrade head
make migrate-down                  # alembic downgrade -1
```

Import new SQLModel models in `alembic/env.py` for autogenerate to detect them.

## Git Conventions

Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <subject>` (imperative, lowercase subject).
Types: `feat`, `fix`, `refactor`, `test`, `chore`, `docs`, `ci`, `build`, `perf`, `style`, `revert`.
Scopes match module names: `auth`, `facility`, `booking`, `payment`, `notification`, `report`, `db`, `infra`.

Branch naming: `<type>/<short-description>` (e.g. `feat/auth-login`, `fix/booking-race-condition`).

**Pre-commit hooks** (one-time setup — runs ruff + commit-msg lint automatically):
```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

## Domain Design

### Booking State Machine

8 states: `pending_payment` → `payment_processing` → `confirmed` → `in_use` → `completed`. Terminal states: `expired`, `payment_failed`, `cancelled`. Implement via `ALLOWED_TRANSITIONS` dict — validate before every state change, never branch on allowed states ad-hoc.

Slot status changes **atomically** in the same transaction as the booking transition (no sync jobs). Slot states: `available` → `held` (on reserve) → `booked` (on confirm) or back to `available` (on cancel/expire). `closed` is owner-only and independent of booking.

### Concurrency Pattern

Slot reservation uses **pessimistic locking**: `SELECT ... FOR UPDATE ORDER BY id` (ordered by bigint PK to prevent deadlock). `held_until` is business state in DB, not a DB lock — the lock is released immediately after commit. Slots have bigint PK (not UUID) for perf and deterministic ordering.

Slots are **pre-generated** (30 days ahead); a nightly cron generates day 31. This makes concurrency explicit (real row locks) and queries fast.

### Idempotency

Four critical mutations require a client-generated `Idempotency-Key: <uuid_v4>` header: `POST /bookings`, `POST /payments/initiate`, `POST /bookings/{id}/cancel`, `POST /bookings/walk-in`. Stored in `idempotency_keys` table with 24h TTL; same key + different body → 409.

VNPay webhook idempotency uses `payments.vnpay_txn_ref UNIQUE` (DB-level) — no separate key needed.

### API Conventions

**Error response format** (all errors):
```python
{"error": {"code": "SLOT_NOT_AVAILABLE", "message": "...", "details": {...}}}
```

**Public availability API**: `held` and `booked` slots both return as `unavailable` — never leak the `held` status to clients.

**Multi-tenant**: `tenant_id` is stored on root entities (`users`, `facilities`, `bookings`) only — child tables trace it via FK chain. JWT payload carries `tenant_id` for middleware filtering.

**Soft delete**: only `facilities` and `courts`. `bookings` are immutable history; never deleted.

## Code Style

Ruff with line length 100, targeting Python 3.12. Rules: E, W, F, I, B, UP, N, SIM, RUF. Double quotes. The `alembic/versions/` directory is excluded from linting.
