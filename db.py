import threading
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction

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
    # Worker concurrency is --concurrency=2; each process opens at most
    # pool_size + max_overflow admin connections.  pool_size=2, max_overflow=2
    # (=4 max) is sufficient for two in-flight tasks and avoids the 750-direct-
    # connection budget violation documented in docs/DEPLOYMENT.md (Issue 259).
    # The worker pods route through the PgBouncer sidecar added in Issue 259, so
    # this value feeds into the worker.pgbouncer.defaultPoolSize budget inequality.
    return create_async_engine(
        settings.database_migration_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_recycle=_POOL_RECYCLE_S,
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


_recreate_lock = threading.Lock()


def recreate_engine() -> None:
    """Rebind the module-global engines + sessionmakers to fresh instances
    bound to the current process. Required after fork (Issue 39): each Celery
    worker child must own a pool tied to its own event loop, not the parent's.
    Inherited connections are abandoned (close=False) so we don't close FDs
    the parent still holds.

    Re-entry guard (Issue 123, hardened Issue 352): concurrent Celery prefork
    signals can call this simultaneously. A non-blocking lock acquire makes
    check-and-set atomic — the second caller returns immediately rather than
    tearing down the pool the first caller is rebuilding. New objects are
    built fully BEFORE the module globals are swapped and the old engines are
    disposed only AFTER the swap, so a concurrent reader can never observe a
    disposed engine or a half-built sessionmaker.
    """
    global engine, AsyncSessionLocal, admin_engine, AdminSessionLocal
    if not _recreate_lock.acquire(blocking=False):
        return
    try:
        new_engine = _make_engine()
        new_admin_engine = _make_admin_engine()
        new_factory = async_sessionmaker(
            new_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        new_admin_factory = async_sessionmaker(
            new_admin_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        old_engine, old_admin_engine = engine, admin_engine
        # Swap references only once fully built. Each assignment is atomic under
        # the GIL, and readers go through the sessionmaker (which holds its own
        # engine reference), so they always see a consistent engine/factory pair.
        AsyncSessionLocal = new_factory
        engine = new_engine
        AdminSessionLocal = new_admin_factory
        admin_engine = new_admin_engine
        old_engine.sync_engine.dispose(close=False)
        old_admin_engine.sync_engine.dispose(close=False)
        # Listener is class-level (registered on Session) — no re-registration
        # needed on engine rebind.
    finally:
        _recreate_lock.release()


async def dispose_engine() -> None:
    """Cleanly dispose both pools. Call on worker shutdown."""
    await engine.dispose()
    await admin_engine.dispose()


class Base(DeclarativeBase):
    pass


# ── RLS context injection (Issue 79) ──────────────────────────────────────────


@event.listens_for(Session, "after_begin")
def _set_app_creator_id(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    """Emit ``SET LOCAL app.creator_id = <uuid>`` on every transaction whose
    session has a creator id attached via ``session.info["creator_id"]``.

    The listener is registered on the global ``Session`` class so it fires
    for sessions from both factories; the discriminator is presence of the
    info key. App sessions get the id set by the FastAPI auth dependency
    (`auth.get_current_creator`); per-creator worker tasks get it via
    ``tenant_session`` (Issue 231). Admin sessions (cross-tenant sweeps,
    integration test fixtures) leave it unset — no GUC is emitted, and since
    the admin role has ``BYPASSRLS`` the policies do not gate visibility anyway.

    The bootstrap-auth Creator lookup runs before the listener has anything
    to inject, which is fine — the ``creators`` table is exempt from RLS
    (per Issue 56) so that initial query is not gated by any policy.
    """
    creator_id = session.info.get("creator_id")
    if creator_id is None:
        return
    # `SET LOCAL` is utility SQL and does NOT accept bind parameters in any
    # Postgres protocol path — psycopg routes it as a regular `Execute` and
    # the server rejects the `$1` placeholder with a syntax error. The
    # `set_config(setting_name, new_value, is_local)` function is the
    # parameterized equivalent — `is_local=true` makes it transaction-scoped
    # so it's wiped by COMMIT/ROLLBACK, matching `SET LOCAL` semantics.
    # See: https://www.postgresql.org/docs/current/functions-admin.html
    connection.execute(
        text("SELECT set_config('app.creator_id', :cid, true)"),
        {"cid": str(creator_id)},
    )


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


# ── Worker tenant session (Issue 231) ─────────────────────────────────────────


@asynccontextmanager
async def tenant_session(creator_id: uuid.UUID | str) -> AsyncGenerator[AsyncSession, None]:
    """Yield an app-role session pre-stamped with ``creator_id``.

    The DRY entry point for per-creator worker tasks: stamping
    ``session.info["creator_id"]`` BEFORE the first statement means the
    ``after_begin`` listener emits the ``app.creator_id`` GUC on every
    transaction, so the RLS policies gate every read (USING) and write
    (WITH CHECK) the task performs — the same structural backstop request
    paths get from ``auth.get_current_creator``. A call site cannot forget
    the GUC because the id is a required argument.

    Cross-tenant sweeps (purge_*, analytics fan-out, outcome polling) must
    keep using ``AdminSessionLocal`` — see the allowlist pinned by
    ``tests/test_worker_invariants.py``.
    """
    # Look up the module-global factory at call time so a post-fork
    # recreate_engine() rebind is always honoured.
    async with AsyncSessionLocal() as session:
        session.info["creator_id"] = str(creator_id)
        yield session
