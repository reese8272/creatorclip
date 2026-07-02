"""Server-Sent Events endpoint for per-task live progress (Issue 86).

See ``worker/progress.py`` for the producer side. The events written to
``task:{task_id}:events`` Redis Stream are tailed here and re-emitted as
SSE events.

Auth: session cookie via ``get_current_creator``.
Ownership: ``task:{task_id}:owner`` Redis key must match the authenticated
creator id — set by the API endpoint that enqueued the task. A leaked /
guessed task id therefore can't be used to attach to another creator's
stream.

The response sets the three Cloudflare-safe headers (per the Phase 1
research) plus nginx's ``X-Accel-Buffering: no`` so no reverse proxy buffers
the stream. A ``: keepalive`` comment is emitted on every ~12s idle tick so
the connection survives mobile-network and CDN idle timeouts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from auth import get_current_creator
from limiter import creator_key, limiter
from models import Creator
from worker import progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Max concurrent SSE streams per creator. 3 covers the realistic "two tabs
# open plus a stuck reconnect" case without enabling hold-open exhaustion.
MAX_CONCURRENT_SSE_PER_CREATOR = 3

# Keepalive cadence. Must be shorter than typical TCP/proxy idle timeouts
# (~25s). 12s gives one missed beat of headroom on mobile networks.
KEEPALIVE_INTERVAL_S = 12.0

# Hard upper bound on stream lifetime so a forgotten subscriber can't hold
# a coroutine indefinitely. DNA builds are ~30s today; LLM_TIMEOUT_SECONDS
# caps individual calls at 120s; 600s covers the worst plausible end-to-end
# latency including retries and queueing.
MAX_STREAM_LIFETIME_S = 600.0


def _format_sse(event_id: str, event_type: str, data: dict) -> bytes:
    """Encode a single SSE event.

    Wire format: plain JSON-per-event with named ``event:`` types, so the
    vanilla-JS frontend can use ``EventSource.addEventListener('thinking', …)``
    natively. See docs/DECISIONS.md (Issue 86) for why this beat the Vercel
    AI SDK Data Stream Protocol for our use case.
    """
    payload = f"id: {event_id}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
    return payload.encode("utf-8")


async def _event_stream(
    request: Request, task_id: str, creator_id: str, last_event_id: str
) -> AsyncIterator[bytes]:
    """Yield SSE-encoded events from the task's Redis Stream.

    Honors the EventSource ``Last-Event-ID`` header for reconnect (resume from
    cursor). Sends a keepalive comment every ``KEEPALIVE_INTERVAL_S``. Closes
    on terminal event, client disconnect, or ``MAX_STREAM_LIFETIME_S``.

    Acquires a per-creator concurrent slot at entry and releases it in finally
    — the release runs even when the generator is GC'd mid-stream.
    """
    if not await progress.aacquire_slot(creator_id, MAX_CONCURRENT_SSE_PER_CREATOR):
        # Encoded as an SSE event so the client can react cleanly (vs the
        # connection just hanging up before any event is yielded).
        yield _format_sse("0-0", "error", {"message": "too many open streams"})
        return

    loop = asyncio.get_event_loop()
    deadline = loop.time() + MAX_STREAM_LIFETIME_S
    cursor = last_event_id or "0-0"

    try:
        while True:
            if await request.is_disconnected():
                return
            if loop.time() > deadline:
                yield _format_sse(cursor, "error", {"message": "stream lifetime exceeded"})
                return

            block_ms = int(KEEPALIVE_INTERVAL_S * 1000)
            events = await progress.aread_since(task_id, last_id=cursor, block_ms=block_ms)
            if not events:
                # SSE comment — prevents the proxy / CDN from buffering and
                # timing out, and keeps NAT mappings alive on mobile networks.
                yield b": keepalive\n\n"
                continue

            for event_id, fields in events:
                cursor = event_id
                etype = fields.get("type", "delta")
                try:
                    data = json.loads(fields.get("data", "{}"))
                except json.JSONDecodeError:
                    data = {"_raw": fields.get("data", "")}
                yield _format_sse(event_id, etype, data)
                if etype in progress.TERMINAL_EVENT_TYPES:
                    return
    finally:
        await progress.arelease_slot(creator_id)


@router.get("/{task_id}/events", response_class=StreamingResponse)
@limiter.limit("120/minute", key_func=creator_key)
async def task_events(
    request: Request,
    task_id: str,
    creator: Creator = Depends(get_current_creator),
) -> StreamingResponse:
    """Tail a task's progress events as Server-Sent Events.

    Auth: session cookie required (via ``get_current_creator``).
    Authorization: the ``task_id`` must be owned by the authenticated creator
    — ownership is set when the task was enqueued (see
    ``routers/creators.py::build_dna``).
    """
    owner = await progress.aget_owner(task_id)
    if owner is None:
        # The stream key may also exist with the owner key missing if the
        # ownership TTL has elapsed but the stream hasn't. Either way: don't
        # leak whether the task ever existed.
        raise HTTPException(status_code=404, detail="Unknown task")
    if owner != str(creator.id):
        raise HTTPException(status_code=403, detail="Not your task")

    last_event_id = request.headers.get("Last-Event-ID", "")

    response = StreamingResponse(
        _event_stream(request, task_id, str(creator.id), last_event_id),
        media_type="text/event-stream",
    )
    # Three Cloudflare-safe headers + nginx's hint, all together so no layer
    # buffers this stream. See docs/DECISIONS.md (Issue 86) for the rationale.
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response
