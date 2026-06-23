import asyncio

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

import models  # noqa: F401 — registers all models with Base.metadata
from alembic import context
from config import settings
from db import Base

config = context.config
# Issue 79: migrations run as the admin role (BYPASSRLS) so DDL like CREATE
# POLICY / ENABLE RLS / ALTER ROLE succeeds and so existing data INSERT/UPDATE
# in data-migrations is not blocked by tenant policies. Falls back to
# DATABASE_URL when DATABASE_MIGRATION_URL is unset (dev single-role default).
config.set_main_option("sqlalchemy.url", settings.database_migration_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    # Issue 270: set lock_timeout and statement_timeout on the migration connection
    # so a blocking ALTER or unsafe ADD COLUMN NOT NULL DEFAULT cannot lock prod
    # indefinitely. lock_timeout = 5s matches Squawk's recommendation; statement_timeout
    # = 120s is enough for the largest expected migration (index build on non-empty table).
    # Both are session-level — they apply only to this migration connection.
    from sqlalchemy import text

    connection.execute(text("SET lock_timeout = '5s'"))
    connection.execute(text("SET statement_timeout = '120s'"))
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
