import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from config import settings
from observability import configure_logging, install_celery_observability

# Structured logs + request-id propagation on the worker side (Issue 75f). The
# signals carry the originating request id across the publish→run boundary so a
# worker log line is correlatable with the API request that enqueued it.
configure_logging(json_logs=settings.LOG_JSON, log_dir=settings.LOG_DIR, filename="worker.log")
install_celery_observability()

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
    # At-least-once safety (Issue 62). acks_late alone drops a task whose worker
    # is killed mid-run (routine OOM during ffmpeg/WhisperX); reject_on_worker_lost
    # requeues it instead. Safe only because the tasks are idempotent (Issue 61).
    task_reject_on_worker_lost=True,
    # The invariant: soft < hard time limit < broker visibility_timeout. A task is
    # killed *before* Redis would redeliver a still-running copy, so no double-run;
    # genuine crashes still redeliver via reject_on_worker_lost. Long-form sources
    # on CPU WhisperX may need a per-task override or the hosted backend — see
    # docs/DECISIONS.md.
    # CELERY_SOFT_TIME_LIMIT_S is the single source of truth for this value; the
    # transcription-timeout config validator (config.py) asserts
    # TRANSCRIPTION_TIMEOUT_S < soft_limit - 30 using this setting. Keep in sync.
    task_soft_time_limit=settings.CELERY_SOFT_TIME_LIMIT_S,
    task_time_limit=settings.CELERY_SOFT_TIME_LIMIT_S + 300,
    broker_transport_options={"visibility_timeout": 3600},
    # RedBeat distributed beat scheduler (Issue 263).
    # Replaces the file-backed PersistentScheduler. RedBeat stores the schedule in
    # Redis (key prefix "redbeat::") and acquires a distributed lock (TTL 1500s)
    # so a restarting beat pod cannot produce duplicate scheduled tasks while the
    # old pod's lock TTL is still live. Required companion for the HA Redis migration
    # (docs/DEPLOYMENT.md) and for Beat liveness-probe recovery.
    # Falls back to REDIS_URL in dev (REDBEAT_REDIS_URL unset → config property).
    beat_scheduler="redbeat.RedBeatScheduler",
    redbeat_redis_url=settings.redbeat_redis_url,
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
            from youtube import _http

            _LOOP.run_until_complete(db.dispose_engine())
            _LOOP.run_until_complete(_http.aclose())  # close shared HTTP client (Issue 72)
    finally:
        if not _LOOP.is_closed():
            _LOOP.close()
        _LOOP = None
