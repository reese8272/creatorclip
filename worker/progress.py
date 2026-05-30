"""Live progress events for long-running tasks (Issue 86).

Worker tasks call ``sync_emit(task_id, event_type, **fields)`` (from inside an
``asyncio.to_thread``) or ``aemit(...)`` (from the worker's event loop) to
publish a progress event onto a per-task Redis Stream. The web layer fans these
events out to authenticated SSE subscribers — see ``routers/tasks.py``.

Why Redis Streams over Pub/Sub
------------------------------
A creator who refreshes the page mid-build must replay the events they missed.
``XADD`` / ``XREAD`` is persistent + ordered + supports the EventSource
``Last-Event-ID`` resume protocol natively. Pub/Sub is fire-and-forget and
loses everything on the first dropped connection.

Why both sync_emit AND aemit
----------------------------
The Anthropic sync streaming context manager runs inside ``asyncio.to_thread``
(it's blocking), so its callbacks need a synchronous Redis client. Other call
sites run on the worker's singleton event loop and can use the async client
directly. Both write the same XADD payload.

Progress is observational — never load-bearing
----------------------------------------------
A Redis hiccup must not take down the worker. Every emit catches and logs the
exception rather than re-raising; the actual work has already happened and
losing a progress event is preferable to losing the whole task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import redis
import redis.asyncio as aredis

from config import settings
from observability import request_id_ctx

logger = logging.getLogger(__name__)

# Stream retention. ~200 events covers a typical pipeline (a handful of step
# events + ~50 token chunks) with healthy buffer for late-joiners. MAXLEN ~ N
# is approximate trimming — cheaper than exact, and what Redis Streams docs
# recommend for high-write append-only logs.
_MAXLEN = 200

# TTL applied to the stream key once a terminal event lands. 1h gives a user
# who comes back after the run completes a chance to see the final state.
_STREAM_TTL_SECONDS = 3600

# TTL applied to the ownership key set by the API at task enqueue. Same logic.
_OWNER_TTL_SECONDS = 3600

# Event types that close the stream (set the EXPIRE).
TERMINAL_EVENT_TYPES = frozenset({"done", "error"})


def _stream_key(task_id: str) -> str:
    return f"task:{task_id}:events"


def _owner_key(task_id: str) -> str:
    return f"task:{task_id}:owner"


def _concurrent_key(creator_id: str) -> str:
    return f"sse:count:{creator_id}"


def _serialize(event_type: str, fields: dict[str, Any]) -> dict[str, str]:
    """Build the XADD payload. Redis Stream entries are string→string.

    Adds ``ts`` (epoch seconds) and ``request_id`` (from the observability
    ContextVar) automatically so every event is correlatable end-to-end.
    """
    return {
        "type": event_type,
        "ts": str(time.time()),
        "request_id": request_id_ctx.get(),
        "data": json.dumps(fields, default=str),
    }


# ── Sync clients (for callers inside asyncio.to_thread) ─────────────────────

_SYNC: redis.Redis | None = None


def _sync_client() -> redis.Redis:
    global _SYNC
    if _SYNC is None:
        _SYNC = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _SYNC


def sync_emit(task_id: str, event_type: str, **fields: Any) -> None:
    """Emit a progress event from a synchronous context.

    Use this from any caller inside ``asyncio.to_thread`` — the Anthropic
    stream callback in particular. The Redis client is a process-level
    singleton (redis-py manages its own connection pool).
    """
    payload = _serialize(event_type, fields)
    try:
        client = _sync_client()
        client.xadd(_stream_key(task_id), payload, maxlen=_MAXLEN, approximate=True)  # type: ignore[arg-type]  # redis-py stubs reject dict[str,str] but it's the documented payload shape
        if event_type in TERMINAL_EVENT_TYPES:
            client.expire(_stream_key(task_id), _STREAM_TTL_SECONDS)
    except Exception as exc:
        # Progress is observational, NEVER load-bearing. Swallow + log so a
        # Redis blip doesn't take down a paid LLM/render task whose actual
        # work has already completed.
        logger.warning("progress.sync_emit failed task=%s type=%s err=%s", task_id, event_type, exc)


# ── Async clients (for callers on the worker's event loop) ──────────────────
#
# redis.asyncio.Redis binds its connection pool to whichever event loop first
# touches it. That's fine in prod (one long-lived loop per worker process),
# but pytest-asyncio with `asyncio_default_fixture_loop_scope = function`
# spins up a fresh loop per test — a singleton bound to test 1's (now-closed)
# loop blows up on test 2 with `RuntimeError: no running event loop`. We bind
# the singleton to the *current running loop* and rebuild on mismatch so the
# cross-test pattern works without making the production worker pay any cost.

_AIO: aredis.Redis | None = None
_AIO_LOOP: asyncio.AbstractEventLoop | None = None


def _async_client() -> aredis.Redis:
    global _AIO, _AIO_LOOP
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        # Called from a sync context — let the caller's await raise the real
        # error. We can't bind to a loop that isn't running.
        current = None
    if _AIO is None or _AIO_LOOP is not current:
        _AIO = aredis.from_url(settings.REDIS_URL, decode_responses=True)
        _AIO_LOOP = current
    assert _AIO is not None  # narrow for mypy after the conditional rebuild
    return _AIO


async def aemit(task_id: str, event_type: str, **fields: Any) -> None:
    """Emit a progress event from an async context."""
    payload = _serialize(event_type, fields)
    try:
        client = _async_client()
        await client.xadd(_stream_key(task_id), payload, maxlen=_MAXLEN, approximate=True)  # type: ignore[arg-type]  # redis-py stubs reject dict[str,str] but it's the documented payload shape
        if event_type in TERMINAL_EVENT_TYPES:
            await client.expire(_stream_key(task_id), _STREAM_TTL_SECONDS)
    except Exception as exc:
        # Reset the singleton on any failure so a wedged client doesn't
        # poison every subsequent emit. Progress is observational, so the
        # next call gets a fresh client and retries.
        global _AIO, _AIO_LOOP
        _AIO = None
        _AIO_LOOP = None
        logger.warning("progress.aemit failed task=%s type=%s err=%s", task_id, event_type, exc)


# ── Ownership (API sets it on enqueue; SSE endpoint enforces it) ────────────


async def aset_owner(task_id: str, creator_id: str) -> None:
    """Record that ``task_id`` belongs to ``creator_id``.

    Called from the API endpoint immediately after ``task.delay()``. The SSE
    endpoint refuses subscriptions whose ``creator_id`` doesn't match — so a
    leaked / guessed task id can't be used to read another creator's stream.
    """
    client = _async_client()
    await client.set(_owner_key(task_id), creator_id, ex=_OWNER_TTL_SECONDS)


async def aget_owner(task_id: str) -> str | None:
    """Return the creator_id that owns ``task_id``, or None if absent / expired."""
    client = _async_client()
    return await client.get(_owner_key(task_id))


# ── Replay (SSE endpoint tails the stream) ──────────────────────────────────


async def aread_since(
    task_id: str, last_id: str = "0-0", block_ms: int = 5000, count: int = 100
) -> list[tuple[str, dict[str, str]]]:
    """XREAD events from the task's stream after ``last_id``.

    Returns a list of ``(event_id, fields)`` tuples in arrival order. Empty
    list when the block times out — caller can then send a keepalive comment
    and re-poll. Advance the cursor by passing the last yielded event_id back.

    ``last_id="0-0"`` reads from the beginning (used by late-joiners and
    reconnects honoring the EventSource ``Last-Event-ID`` header).
    """
    client = _async_client()
    key = _stream_key(task_id)
    result = await client.xread({key: last_id}, count=count, block=block_ms)
    if not result:
        return []
    # result shape: [(stream_key, [(id, fields), ...])]
    return result[0][1]


# ── Per-creator concurrent-SSE cap ──────────────────────────────────────────


async def aacquire_slot(creator_id: str, max_concurrent: int) -> bool:
    """Try to take an SSE slot for ``creator_id``. Returns False if over the cap.

    The check + INCR is two ops, so under heavy concurrent connect a creator
    could briefly exceed cap by 1–2 — acceptable for an observational DoS
    guard. A strict cap would need a Lua compare-and-swap; not worth the
    complexity for the threat model.
    """
    client = _async_client()
    count = await client.incr(_concurrent_key(creator_id))
    if count == 1:
        # Bound the key's lifetime in case a DECR is missed (worker kill,
        # process crash, network drop mid-DECR). The counter resets to 0 when
        # the TTL expires.
        await client.expire(_concurrent_key(creator_id), _STREAM_TTL_SECONDS)
    if count > max_concurrent:
        await client.decr(_concurrent_key(creator_id))
        return False
    return True


async def arelease_slot(creator_id: str) -> None:
    """Release an SSE slot. Safe to call on disconnect even mid-stream."""
    client = _async_client()
    # Redis DECR is fine if the counter is at 0 — goes negative, which the
    # EXPIRE in aacquire_slot will eventually reset.
    await client.decr(_concurrent_key(creator_id))


# ── Shutdown ────────────────────────────────────────────────────────────────


async def aclose() -> None:
    """Close the async Redis client cleanly.

    Wire into the FastAPI lifespan shutdown so the worker / web process
    doesn't leak connections (and so tests don't see the noisy
    ``Event loop is closed`` warning at module teardown).

    Defensive: if the singleton was bound to a different (now-closed) loop
    — which happens in pytest's per-test loop scope when a TestClient
    teardown runs after the loop that opened the connection has rolled —
    just null out the globals and return. Letting redis-py call
    ``call_soon`` on a dead loop raises ``RuntimeError`` and crashes the
    teardown. The connection will be GC'd naturally.
    """
    global _AIO, _AIO_LOOP
    if _AIO is None:
        return
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if _AIO_LOOP is not None and _AIO_LOOP is not current:
        # Wrong loop — drop the reference and let GC handle teardown.
        _AIO = None
        _AIO_LOOP = None
        return
    try:
        await _AIO.aclose()
    except Exception as exc:  # pragma: no cover - defensive only
        logger.warning("progress.aclose redis aclose failed: %s", exc)
    finally:
        _AIO = None
        _AIO_LOOP = None
