"""Shared response shapes for router endpoints.

Extracted (Issue 108) so the four near-identical ``*QueuedOut`` Pydantic
models — ``BuildQueuedOut``, ``CatalogSyncQueuedOut``, ``RenderQueuedOut``,
``BriefQueuedOut`` — stop duplicating the same three-field shape across
four router modules.
"""

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
