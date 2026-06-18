"""Frontend activity logging endpoint (Issue 122).

Receives structured UI events from the browser (clicks, form submits, page
navigation) and writes them as structured log lines via log_event() so they land
in the same rotating app.log file alongside server-side events. No auth required —
events are low-sensitivity telemetry for beta testing; the creator_id field is
populated when a valid session exists, "anonymous" otherwise.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address

import event_log
from limiter import limiter
from observability import log_event, request_id_ctx

router = APIRouter(prefix="/api/activity", tags=["activity"])

_MAX_EXTRA_KEYS = 10
_MAX_STR_LEN = 200


class ActivityEvent(BaseModel):
    page: str = Field(max_length=100)
    event_type: str = Field(max_length=50)  # "click" | "submit" | "navigate"
    target: str = Field(max_length=200)
    extra: dict[str, Any] = Field(default_factory=dict)


@router.post("", status_code=204, include_in_schema=False)
@limiter.limit("200/minute", key_func=get_remote_address)
async def record_activity(request: Request, event: ActivityEvent) -> None:
    # Resolve creator id from session JWT if present; fall back to anonymous so
    # pre-login pages (onboarding, pricing) are still captured.
    creator_id = "anonymous"
    creator_uuid: uuid.UUID | None = None
    try:
        from auth import get_current_creator

        creator = await get_current_creator(request)
        creator_id = str(creator.id)
        creator_uuid = creator.id
    except Exception:
        pass

    # Sanitize extra: cap key count and string value lengths to prevent log bloat.
    safe_extra = {
        k: (v[:_MAX_STR_LEN] if isinstance(v, str) else v)
        for k, v in list(event.extra.items())[:_MAX_EXTRA_KEYS]
    }

    # File sink (rotating app.log) — unchanged, keeps the structured-log trail.
    log_event(
        "ui_activity",
        creator_id=creator_id,
        page=event.page,
        event_type=event.event_type,
        target=event.target,
        **safe_extra,
    )

    # DB sink (Issue 151) — queryable beta telemetry; record_event redacts and is
    # best-effort (never raises). creator_uuid is None for anonymous events.
    await event_log.record_event(
        source="ui",
        event=event.event_type,
        creator_id=creator_uuid,
        page=event.page,
        target=event.target,
        request_id=request_id_ctx.get(),
        extra=event.extra,
    )
