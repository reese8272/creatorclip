from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session

from config import settings

# ── Engines ───────────────────────────────────────────────────────────────────
#
# Two engines (Issue 79):
#   - `engine` / `AsyncSessionLocal`: connects as the app role. In production
#     this role has NO BYPASSRLS, so every transaction is subject to the
#     row-level-security policies defined in alembic 0010_rls_policies. Sessions
#     created from this factory carry an `after_begin` listener that emits
#     `SET LOCAL app.creator_id = <uuid>` from `session.info["creator_id"]`.
#   - `admin_engine` / `AdminSessionLocal`: connects as the migration role
#     (BYPASSRLS). Used by Celery worker tasks for cross-tenant sweeps and by
#     integration test fixtures for setup / teardown. In dev / single-role
#     deployments `DATABASE_MIGRATION_URL` defaults to `DATABASE_URL`, so the
#     two factories are functionally equivalent until the prod role split lands.

# psycopg3 auto-prepares a statement server-side after its 5th execution. PgBouncer
# in transaction-pooling mode (docs/DEPLOYMENT.md) reuses server connections across
# clients, so a prepared statement created on one connection is gone on the next →
# `prepared statement "_pg3_…" does not exist`. prepare_threshold=None disables
# server-side preparation entirely. CI never catches this (it hits Postgres directly).
_CONNECT_ARGS = {"prepare_threshold": None}

# Per-pod connection ceiling = pool_size + max_overflow = 20, which stays under the
# 25-conn PgBouncer sidecar (docs/DEPLOYMENT.md). See the total-connections
# inequality in DEPLOYMENT.md before changing replica counts. pool_recycle cycles
# connections so a Postgres/PgBouncer restart can't strand a stale handle.
_POOL_SIZE = 15
_MAX_OVERFLOW = 5
_POOL_RECYCLE_S = 1800


def _make_engine() -> AsyncEngine:
    return create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=_POOL_SIZE,
        max_overflow=_MAX_OVERFLOW,
        pool_recycle=_POOL_RECYCLE_S,
        connect_args=_CONNECT_ARGS,
    )


def _make_admin_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_migration_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args=_CONNECT_ARGS,
    )


engine: AsyncEngine = _make_engine()
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

admin_engine: AsyncEngine = _make_admin_engine()
AdminSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    admin_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def recreate_engine() -> None:
    """Rebind the module-global engines + sessionmakers to fresh instances
    bound to the current process. Required after fork (Issue 39): each Celery
    worker child must own a pool tied to its own event loop, not the parent's.
    Inherited connections are abandoned (close=False) so we don't close FDs
    the parent still holds.
    """
    global engine, AsyncSessionLocal, admin_engine, AdminSessionLocal
    engine.sync_engine.dispose(close=False)
    admin_engine.sync_engine.dispose(close=False)
    engine = _make_engine()
    admin_engine = _make_admin_engine()
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    AdminSessionLocal = async_sessionmaker(
        admin_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    # Listener is class-level (registered on Session) — no re-registration
    # needed on engine rebind.


async def dispose_engine() -> None:
    """Cleanly dispose both pools. Call on worker shutdown."""
    await engine.dispose()
    await admin_engine.dispose()


class Base(DeclarativeBase):
    pass


# ── RLS context injection (Issue 79) ──────────────────────────────────────────


@event.listens_for(Session, "after_begin")
def _set_app_creator_id(session, transaction, connection):
    """Emit ``SET LOCAL app.creator_id = <uuid>`` on every transaction whose
    session has a creator id attached via ``session.info["creator_id"]``.

    The listener is registered on the global ``Session`` class so it fires
    for sessions from both factories; the discriminator is presence of the
    info key. App sessions get the id set by the FastAPI auth dependency
    (`auth.get_current_creator`). Admin sessions (worker tasks, integration
    test fixtures) leave it unset — no GUC is emitted, and since the admin
    role has ``BYPASSRLS`` the policies do not gate visibility anyway.

    The bootstrap-auth Creator lookup runs before the listener has anything
    to inject, which is fine — the ``creators`` table is exempt from RLS
    (per Issue 56) so that initial query is not gated by any policy.
    """
    creator_id = session.info.get("creator_id")
    if creator_id is None:
        return
    connection.execute(
        text("SET LOCAL app.creator_id = :cid"),
        {"cid": str(creator_id)},
    )


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
