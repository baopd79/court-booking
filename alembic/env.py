"""Alembic environment configuration.

Async migration runner. URL đọc từ app settings (không hardcode).
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from alembic import context
from app.core.config import get_settings

# === Import all models để metadata.tables đầy đủ ===
# Khi tạo module mới có model, thêm import vào đây.
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.facility import models as facility_models  # noqa: F401
from app.modules.booking import models as booking_models  # noqa: F401
from app.modules.payment import models as payment_models  # noqa: F401
from app.modules.notification import models as notification_models  # noqa: F401

config = context.config

# Override sqlalchemy.url từ settings
settings = get_settings()
config.set_main_option("sqlalchemy.url", str(settings.database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # detect type change (vd: VARCHAR(50) → VARCHAR(100))
        compare_server_default=True,  # detect default change
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode với async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # migration không cần pool, 1 connection rồi exit
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_offline() -> None:
    """Offline mode: gen SQL ra file, không connect DB."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: connect DB và execute migration."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
