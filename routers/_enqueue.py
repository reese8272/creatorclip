"""Shared Celery-enqueue + SSE-ownership helpers (ready-pass W2, routers-DRY lane).

Collapses the enqueue → ``aset_owner`` → ``stream_url`` tail that was
copy-pasted across 10 router modules (18 sites / 19 endpoints):

- ``enqueue_stream_task`` — the full plain pattern: ``.delay()`` off the event
  loop, then stamp SSE ownership. The stream key defaults to the Celery task id;
  resource-keyed routes (clip/video/summary) pass their domain id instead.
- ``stamp_stream_owner`` — just the fail-open ownership stamp, for routes whose
  enqueue step has bespoke ordering or compensation that must stay route-local
  (Issue-359c snapshot restore, improvement's job_id commit, the Issue-313
  stamp-before-``start_pipeline`` ordering, the OAuth-callback 302).

Fail-open posture (Wave-5 Fix 1): a Redis blip on ``aset_owner`` returns
``stream_url=None`` instead of 500-ing — the Celery task is already enqueued and
still runs; the client just loses live progress and polls the resource instead.

``aset_owner`` is resolved via ``progress.aset_owner`` attribute lookup at call
time so test patches of ``worker.progress.aset_owner`` keep intercepting.
"""

import asyncio
import logging
from typing import Any, Protocol

import redis

from worker import progress

logger = logging.getLogger(__name__)


class _DelayCapable(Protocol):
    """The slice of the Celery task surface the helper needs."""

    def delay(self, *args: Any) -> Any: ...


async def stamp_stream_owner(stream_key: str, creator_id: str, *, log_label: str) -> str | None:
    """Register SSE stream ownership for ``stream_key``; fail open on Redis errors.

    Returns the stream URL on success, ``None`` when Redis is unreachable.
    ``log_label`` keeps the per-route warning greppable (e.g. ``"render"``).
    """
    try:
        await progress.aset_owner(stream_key, creator_id)
    except redis.RedisError as exc:
        logger.warning(
            "%s aset_owner failed (Redis down?) key=%s err=%s",
            log_label,
            stream_key,
            exc,
        )
        return None
    return f"/tasks/{stream_key}/events"


async def enqueue_stream_task(
    task_fn: _DelayCapable,
    /,
    *args: Any,
    creator_id: str,
    stream_key: str | None = None,
    log_label: str,
) -> tuple[Any, str | None]:
    """Enqueue ``task_fn.delay(*args)`` off the event loop and stamp SSE ownership.

    ``.delay()`` is sync Redis I/O, so it runs in a thread (scale-checklist B).
    ``stream_key`` defaults to the Celery task id; pass a domain id (clip/video)
    where the worker emits to ``task:{domain_id}:events`` instead.
    Returns ``(task, stream_url)``.
    """
    task = await asyncio.to_thread(task_fn.delay, *args)
    stream_url = await stamp_stream_owner(stream_key or task.id, creator_id, log_label=log_label)
    return task, stream_url
