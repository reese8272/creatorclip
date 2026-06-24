"""
Tests for Issue 234 — worker/tasks.py log_event instrumentation.

These tests do NOT require Postgres/Redis/ffmpeg — they assert that
structured events are emitted on the correct paths using unittest.mock.
DB-free unit tests only (no `integration` mark).
"""

import asyncio
from unittest.mock import AsyncMock, patch


def test_render_clip_failure_emits_failed_event() -> None:
    """RefundOnFailureTask.on_failure must emit a *_failed structured event.

    on_failure fires only on TERMINAL failure (after retries exhausted).
    Verifies Issue 234 AC: the event has task_id and exc_type fields.
    """
    from worker.tasks import RefundOnFailureTask

    task = RefundOnFailureTask()
    task.name = "worker.tasks.render_clip"

    exc = RuntimeError("ffmpeg died")
    task_id = "test-task-id-render-fail"

    with patch("worker.tasks.log_event") as mock_log:
        task.on_failure(exc, task_id, args=["clip-id-123"], kwargs={}, einfo=None)

    mock_log.assert_called_once()
    call_args = mock_log.call_args
    event_name = call_args.args[0]
    assert event_name.endswith("_failed"), f"Expected *_failed event, got: {event_name!r}"
    assert call_args.kwargs.get("task_id") == task_id, "task_id must be in the event fields"
    assert call_args.kwargs.get("exc_type") == "RuntimeError", (
        "exc_type must be the exception class name"
    )
    # No raw exception message or secrets must appear.
    for v in call_args.kwargs.values():
        assert "ffmpeg died" not in str(v), (
            "Raw exception message must not appear in structured event fields"
        )


def test_on_failure_event_name_derived_from_task_name() -> None:
    """on_failure must derive the event name from self.name (the task's dotted name).

    worker.tasks.ingest_video → ingest_video_failed.
    """
    from worker.tasks import RefundOnFailureTask

    for task_name, expected_event_suffix in [
        ("worker.tasks.ingest_video", "ingest_video_failed"),
        ("worker.tasks.transcribe_video", "transcribe_video_failed"),
        ("worker.tasks.build_signals", "build_signals_failed"),
    ]:
        task = RefundOnFailureTask()
        task.name = task_name
        with patch("worker.tasks.log_event") as mock_log:
            task.on_failure(
                ValueError("boom"), "tid-xyz", args=["video-id-abc"], kwargs={}, einfo=None
            )
        event_name = mock_log.call_args.args[0]
        assert event_name == expected_event_suffix, (
            f"Expected {expected_event_suffix!r} for task {task_name!r}, got {event_name!r}"
        )


def test_on_failure_creator_id_absent() -> None:
    """on_failure must NOT include creator_id — no DB call on the failure hot-path."""
    from worker.tasks import RefundOnFailureTask

    task = RefundOnFailureTask()
    task.name = "worker.tasks.render_clip"

    with patch("worker.tasks.log_event") as mock_log:
        task.on_failure(
            RuntimeError("fail"), "tid-abc", args=["clip-id-xyz"], kwargs={}, einfo=None
        )

    call_kwargs = mock_log.call_args.kwargs
    assert "creator_id" not in call_kwargs, (
        "on_failure must NOT pass creator_id to avoid a DB call on a degraded connection"
    )


def test_build_dna_started_and_done_events_emitted() -> None:
    """build_dna task must emit build_dna_started and build_dna_done events.

    Uses mocks for the async _build_dna_async body so no DB/LLM call is made.
    Calls the task's .run() method (the underlying Python function, bound=False).
    """
    started_events: list[str] = []

    def _capture_event(event: str, **_kwargs: object) -> None:
        started_events.append(event)

    with (
        patch("worker.tasks.log_event", side_effect=_capture_event),
        patch("worker.tasks._build_dna_async", new=AsyncMock(return_value=None)),
        patch("worker.tasks.run_async", side_effect=lambda coro: asyncio.run(coro)),
    ):
        from worker.tasks import build_dna

        # .run() is the unwrapped Celery task function — it takes only the
        # declared arguments (not self) because Celery's bind machinery is
        # bypassed. For bind=True tasks, use .apply() to simulate a real call
        # but that needs a running Celery app. Since we only test event ordering,
        # we call the function via the task registry's run shortcut instead.
        build_dna.run("creator-uuid-xyz")

    assert "build_dna_started" in started_events, "build_dna_started must be emitted"
    assert "build_dna_done" in started_events, "build_dna_done must be emitted"
    assert started_events.index("build_dna_started") < started_events.index("build_dna_done"), (
        "_started must appear before _done"
    )


def test_sync_channel_catalog_started_and_done_events_emitted() -> None:
    """sync_channel_catalog task must emit started/done events (creator_id direct)."""
    started_events: list[str] = []

    def _capture_event(event: str, **_kwargs: object) -> None:
        started_events.append(event)

    with (
        patch("worker.tasks.log_event", side_effect=_capture_event),
        patch("worker.tasks._sync_channel_catalog_async", new=AsyncMock(return_value=None)),
        patch("worker.tasks.run_async", side_effect=lambda coro: asyncio.run(coro)),
    ):
        from worker.tasks import sync_channel_catalog

        sync_channel_catalog.run("creator-uuid-abc")

    assert "sync_channel_catalog_started" in started_events
    assert "sync_channel_catalog_done" in started_events
