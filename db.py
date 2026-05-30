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
#     row-level-security policies defined in alembic 0005. Sessions created
#     from this factory carry an `after_begin` listener that emits
#     `SET LOCAL app.creator_id = <uuid>` from `session.info["creator_id"]`.
#   - `admin_engine` / `AdminSessionLocal`: connects as the migration role
#     (BYPASSRLS). Used by Celery worker tasks for cross-tenant sweeps and by
#     integration test fixtures for setup / teardown. In dev / single-role
#     deployments `DATABASE_MIGRATION_URL` defaults to `DATABASE_URL`, so the
#     two factories are functionally equivalent until the prod role split lands.


def _make_engine() -> AsyncEngine:
    return create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def _make_admin_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_migration_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
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
