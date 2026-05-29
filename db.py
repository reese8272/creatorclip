from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings

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


engine: AsyncEngine = _make_engine()
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def recreate_engine() -> None:
    """Rebind the module-global engine + sessionmaker to fresh instances bound
    to the current process. Required after fork (Issue 39): each Celery worker
    child must own a pool tied to its own event loop, not the parent's.
    Inherited connections are abandoned (close=False) so we don't close FDs
    the parent still holds.
    """
    global engine, AsyncSessionLocal
    engine.sync_engine.dispose(close=False)
    engine = _make_engine()
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def dispose_engine() -> None:
    """Cleanly dispose the current engine's pool. Call on worker shutdown."""
    await engine.dispose()


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
