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
        # Issue 270 parity for OFFLINE (--sql) mode: online mode gets these via
        # create_async_engine connect_args, which never render into offline SQL.
        # Offline SQL is a real prod path (applied via psql when the CLI runner is
        # unavailable — see DECISIONS 2026-06-24 alembic-rollback incident), so the
        # rendered script must carry its own lock/statement timeouts.
        context.execute("SET lock_timeout = '5s'")
        context.execute("SET statement_timeout = '120s'")
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Issue 270: bound lock_timeout / statement_timeout on the migration connection
    # so a blocking ALTER or unsafe ADD COLUMN NOT NULL DEFAULT cannot lock prod
    # indefinitely. lock_timeout = 5s matches Squawk's recommendation; statement_timeout
    # = 120s covers the largest expected migration (index build on a non-empty table).
    #
    # These MUST be applied via libpq `options` at connect time, NOT via
    # `connection.execute("SET ...")` inside do_run_migrations: a pre-transaction
    # execute auto-begins a transaction on the SQLAlchemy 2.0 connection, which
    # alembic's context.begin_transaction() then treats as caller-owned and never
    # commits — so every migration silently rolled back (exit 0, no error) and prod
    # drifted behind head. Confirmed on prod 2026-06-24; see docs/OFF_COURSE_BUGS.md.
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
        connect_args={"options": "-c lock_timeout=5s -c statement_timeout=120s"},
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
