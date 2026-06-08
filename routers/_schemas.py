"""Shared response shapes for router endpoints.

Extracted (Issue 108) so the four near-identical ``*QueuedOut`` Pydantic
models — ``BuildQueuedOut``, ``CatalogSyncQueuedOut``, ``RenderQueuedOut``,
``BriefQueuedOut`` — stop duplicating the same three-field shape across
four router modules.
"""

from typing import Literal

from pydantic import BaseModel


class TaskQueuedOut(BaseModel):
    """Canonical 202 Accepted response for endpoints that enqueue a Celery
    task and return a SSE stream URL the caller can subscribe to for live
    progress.

    ``stream_url`` is ``str | None``: ``None`` when the Redis ``aset_owner``
    call failed (Wave-5 Fix 1 fail-open posture — the Celery task still
    runs; the client polls the resource state instead).
    """

    task_id: str
    status: str
    stream_url: str | None = None


# ── Empty-state envelope (DECISIONS 2026-06-08) ───────────────────────────────

EmptyState = Literal["empty_initial", "empty_filtered", "populated"]


class NextActionOut(BaseModel):
    """Server-suggested next step for an empty list response.

    ``action_type`` lets the client decide HOW to act on ``url`` without
    hardcoding which endpoint maps to which UI gesture: ``navigate`` →
    in-app route, ``open_form`` → expand a form on the current page,
    ``external`` → open in new tab.
    """

    label: str
    action_type: Literal["navigate", "open_form", "external"]
    url: str


def build_envelope_state(count: int, *, is_filtered: bool = False) -> EmptyState:
    """Resolve the canonical state literal for a list response.

    ``is_filtered`` is True when the caller applied a non-default filter that
    could explain emptiness (search query, status filter, etc.) — used to
    distinguish "you have nothing yet" from "your filter excluded everything".
    """
    if count > 0:
        return "populated"
    return "empty_filtered" if is_filtered else "empty_initial"
