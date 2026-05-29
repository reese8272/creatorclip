"""Redis-backed status for the async improvement-brief job (202 + poll).

The brief is a 120s Anthropic+web_search call — too long for a synchronous request
(Cloudflare's ~100s proxy timeout 524s the user). We run it on Celery and track
status here. State is keyed by creator id, so a creator can only ever read its own
job (per-creator isolation by construction) and no migration is needed — the brief
is ephemeral/regenerable, not durable data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from youtube._redis import get_redis_client

Status = Literal["pending", "running", "done", "failed", "none"]

_TTL_S = 3600  # an hour — long enough to poll a slow brief, short enough to self-clean
_ACTIVE = ("pending", "running")


def _key(creator_id: str) -> str:
    return f"improvement_brief:{creator_id}"


async def get_status(creator_id: str) -> dict[str, Any]:
    """Return the creator's current brief job state, or {"status": "none"}."""
    raw = await get_redis_client().get(_key(creator_id))
    if not raw:
        return {"status": "none"}
    return json.loads(raw)


async def is_active(creator_id: str) -> bool:
    """True if a job is pending or running (used to debounce duplicate enqueues)."""
    return (await get_status(creator_id)).get("status") in _ACTIVE


async def set_status(
    creator_id: str,
    status: Status,
    *,
    brief: str | None = None,
    error: str | None = None,
) -> None:
    payload: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC).isoformat()}
    if brief is not None:
        payload["brief"] = brief
    if error is not None:
        payload["error"] = error
    await get_redis_client().set(_key(creator_id), json.dumps(payload), ex=_TTL_S)
