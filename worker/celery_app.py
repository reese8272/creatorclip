import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from config import settings

logger = logging.getLogger(__name__)

celery = Celery(
    "creatorclip",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["worker.tasks", "worker.schedule"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


# Issue 39: per-worker singleton event loop.
# Every task previously called asyncio.run(...) which created a fresh loop per
# invocation, rebinding the SQLAlchemy async engine pool to whichever loop
# touched it first and producing "Future attached to a different loop" errors
# under concurrency. We now own one loop per worker process and bind the
# engine to it once at worker init.
_LOOP: asyncio.AbstractEventLoop | None = None


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Execute *coro* on the per-worker singleton loop.

    Falls back to asyncio.run() when no worker loop has been installed (e.g.
    tasks invoked synchronously from a unit test that did not trigger
    worker_process_init). The fallback is for tests only; in a real worker
    the loop is always present.
    """
    if _LOOP is None or _LOOP.is_closed():
        return asyncio.run(coro)
    return _LOOP.run_until_complete(coro)


@worker_process_init.connect
def _init_worker_loop(**_: Any) -> None:
    import db

    global _LOOP
    _LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_LOOP)
    db.recreate_engine()
    logger.info("worker loop + engine initialized")


@worker_process_shutdown.connect
def _shutdown_worker_loop(**_: Any) -> None:
    import db

    global _LOOP
    if _LOOP is None:
        return
    try:
        if not _LOOP.is_closed():
            _LOOP.run_until_complete(db.dispose_engine())
    finally:
        if not _LOOP.is_closed():
            _LOOP.close()
        _LOOP = None
