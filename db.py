from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


def _make_engine() -> AsyncEngine:
    return create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
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
