"""Frontend activity logging endpoint (Issue 122).

Receives structured UI events from the browser (clicks, form submits, page
navigation) and writes them as structured log lines via log_event() so they land
in the same rotating app.log file alongside server-side events. No auth required —
events are low-sensitivity telemetry for beta testing; the creator_id field is
populated when a valid session exists, "anonymous" otherwise.
"""

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address

import event_log
from limiter import limiter
from observability import log_event, request_id_ctx

router = APIRouter(prefix="/api/activity", tags=["activity"])

_MAX_EXTRA_KEYS = 10
_MAX_STR_LEN = 200
_MAX_EXTRA_JSON_LEN = 2000


class ActivityEvent(BaseModel):
    page: str = Field(max_length=100)
    event_type: str = Field(max_length=50)  # "click" | "submit" | "navigate"
    target: str = Field(max_length=200)
    extra: dict[str, Any] = Field(default_factory=dict)


def _sanitize_extra(extra: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Whitelist scalar values and clamp key count / string lengths.

    Issue 352 Batch C: client keys are NEVER splatted into log kwargs — a key
    colliding with a named kwarg (``page``/``creator_id``/…) raised TypeError
    (500), a reserved LogRecord attribute (``message``/``args``/…) raised
    KeyError inside logging, and arbitrary keys became top-level structured-log
    fields (log injection). Non-scalar values are dropped so nested dicts/lists
    cannot bypass the length cap.
    """
    clean: dict[str, str | int | float | bool] = {}
    for k, v in extra.items():
        if len(clean) >= _MAX_EXTRA_KEYS:
            break
        key = str(k)[:_MAX_STR_LEN]
        if isinstance(v, str):
            clean[key] = v[:_MAX_STR_LEN]
        elif isinstance(v, bool | int | float):
            clean[key] = v
    return clean


@router.post(
    "", status_code=204, include_in_schema=False, response_class=Response, response_model=None
)
@limiter.limit("200/minute", key_func=get_remote_address)
async def record_activity(request: Request, event: ActivityEvent) -> None:
    # Resolve creator id from the signed session cookie (no DB lookup — this
    # endpoint runs outside the dependency graph); fall back to anonymous so
    # pre-login pages (onboarding, pricing) are still captured. The previous
    # manual `get_current_creator(request)` call passed the Depends() sentinel as
    # the session and always threw → every event logged as anonymous (Issue 151
    # bug, caught by the event_log integration tests on their first CI run).
    from auth import creator_id_from_cookie

    creator_uuid: uuid.UUID | None = creator_id_from_cookie(request)
    creator_id = str(creator_uuid) if creator_uuid else "anonymous"

    safe_extra = _sanitize_extra(event.extra)
    # ONE server-controlled field carrying the client payload as a bounded JSON
    # string — never **splatted, so client keys cannot collide with log kwargs.
    extra_json = (
        json.dumps(safe_extra, ensure_ascii=False)[:_MAX_EXTRA_JSON_LEN] if safe_extra else None
    )

    # File sink (rotating app.log) — unchanged, keeps the structured-log trail.
    log_event(
        "ui_activity",
        creator_id=creator_id,
        page=event.page,
        event_type=event.event_type,
        target=event.target,
        extra=extra_json,
    )

    # DB sink (Issue 151) — queryable beta telemetry; record_event redacts and is
    # best-effort (never raises). creator_uuid is None for anonymous events.
    # Sanitized dict here too (Issue 352 Batch C): the raw client `extra` was
    # unbounded → large per-row telemetry writes / table bloat on an
    # unauthenticated, IP-limited route.
    await event_log.record_event(
        source="ui",
        event=event.event_type,
        creator_id=creator_uuid,
        page=event.page,
        target=event.target,
        request_id=request_id_ctx.get(),
        extra=safe_extra or None,
    )
