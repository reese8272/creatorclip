"""Unit tests for render_clip's permanent-vs-transient retry classification.

The render retry-storm (owner report, 2026-06-29): every render failure went
through ``self.retry`` (max_retries=3, 60s apart), so a deterministically-broken
clip burned ~3 minutes of retries while the UI sat on "Rendering…". Permanent
errors (``ValueError``/``FileNotFoundError`` — missing clip/source, bad range) are
now terminal: set the clip ``failed`` and re-raise WITHOUT retry. Transient errors
still retry. These tests pin that contract without a DB (the async helpers are
patched out).
"""

from __future__ import annotations

import pytest

from models import RenderStatus


class _RetryCalled(Exception):
    """Sentinel raised by the patched ``self.retry`` so the test can detect retries."""


@pytest.fixture
def render_harness(monkeypatch):
    """Patch render_clip's async helpers + self.retry; record status writes + retries."""
    from worker import tasks

    statuses: list[RenderStatus] = []
    retry_calls: list[dict] = []

    async def _creator(clip_id):
        return "creator-1"

    async def _set_status(clip_id, status):
        statuses.append(status)

    def _fake_retry(**kwargs):
        retry_calls.append(kwargs)
        return _RetryCalled()

    monkeypatch.setattr(tasks, "_creator_id_for_clip", _creator)
    monkeypatch.setattr(tasks, "_set_clip_render_status", _set_status)
    monkeypatch.setattr(tasks.render_clip, "retry", _fake_retry)

    def _set_render_async(exc):
        async def _impl(clip_id, creator_id=None):
            raise exc

        monkeypatch.setattr(tasks, "_render_clip_async", _impl)

    return tasks, statuses, retry_calls, _set_render_async


@pytest.mark.parametrize("exc", [ValueError("Clip not found"), FileNotFoundError("source gone")])
def test_permanent_error_marks_failed_without_retry(render_harness, exc):
    tasks, statuses, retry_calls, set_render_async = render_harness
    set_render_async(exc)

    with pytest.raises(type(exc)):
        tasks.render_clip("clip-1")

    assert retry_calls == [], "permanent errors must not retry"
    assert statuses == [RenderStatus.failed]


def test_transient_error_retries(render_harness):
    tasks, statuses, retry_calls, set_render_async = render_harness
    set_render_async(RuntimeError("ffmpeg blip / R2 hiccup"))

    with pytest.raises(_RetryCalled):
        tasks.render_clip("clip-1")

    assert len(retry_calls) == 1, "transient errors must go through self.retry"
    assert statuses == [RenderStatus.failed]


def test_success_marks_no_failure(render_harness, monkeypatch):
    tasks, statuses, retry_calls, _ = render_harness

    async def _ok(clip_id, creator_id=None):
        return None

    monkeypatch.setattr(tasks, "_render_clip_async", _ok)
    result = tasks.render_clip("clip-1")

    assert result == "clip-1"
    assert statuses == []
    assert retry_calls == []


def test_render_clip_soft_timeout_is_terminal(render_harness):
    """SoftTimeLimitExceeded inside render_clip must be re-raised WITHOUT retry —
    re-rendering a clip that already exceeded the soft limit would time out again.
    The clip must be marked failed so on_failure can fire. (Issue 336)"""
    from celery.exceptions import SoftTimeLimitExceeded

    tasks, statuses, retry_calls, set_render_async = render_harness
    set_render_async(SoftTimeLimitExceeded())

    with pytest.raises(SoftTimeLimitExceeded):
        tasks.render_clip("clip-1")

    assert retry_calls == [], "SoftTimeLimitExceeded must not retry"
    assert RenderStatus.failed in statuses, "clip must be marked failed on soft-timeout"


def test_render_clip_events_carry_clip_id_not_video_id(render_harness, monkeypatch):
    """2026-07-20 incident (ISSUE-2026-07-20-02 fallout): render_clip's structured
    events labeled the clip id as video_id, misdirecting triage to the wrong
    entity. Every render_clip event must carry clip_id and never video_id."""
    events: list[tuple[str, dict]] = []

    tasks, statuses, retry_calls, set_render_async = render_harness
    monkeypatch.setattr(tasks, "log_event", lambda event, **kw: events.append((event, kw)))
    set_render_async(ValueError("Clip not found"))

    with pytest.raises(ValueError):
        tasks.render_clip("clip-1")

    names = [name for name, _ in events]
    assert "render_clip_started" in names and "render_clip_failed_permanent" in names
    for name, fields in events:
        assert fields.get("clip_id") == "clip-1", (name, fields)
        assert "video_id" not in fields, (name, fields)


async def test_render_clip_async_source_expired_emits_actionable_message(monkeypatch):
    """W1 ready-pass: a source purged in the enqueue-to-run race window must emit
    the ACTIONABLE SSE message (re-upload), not the generic "Render failed." —
    SourceExpiredError stays a ValueError so the no-retry classification holds."""
    from unittest.mock import AsyncMock

    import worker.progress
    from worker import tasks

    fake_emit = AsyncMock()
    monkeypatch.setattr(worker.progress, "aemit", fake_emit)

    async def _creator(clip_id):
        return "creator-1"

    async def _load(clip_id, creator_id):
        raise tasks.SourceExpiredError(f"Source video not available for clip {clip_id}")

    monkeypatch.setattr(tasks, "_creator_id_for_clip", _creator)
    monkeypatch.setattr(tasks, "_load_clip_render_plan", _load)

    with pytest.raises(tasks.SourceExpiredError):
        await tasks._render_clip_async("clip-1")

    assert issubclass(tasks.SourceExpiredError, ValueError)
    error_calls = [c for c in fake_emit.call_args_list if c.args[1] == "error"]
    assert len(error_calls) == 1
    assert (
        error_calls[0].kwargs["message"]
        == "Source media expired — re-upload the video to render this clip."
    )
    assert error_calls[0].kwargs["exc_type"] == "SourceExpiredError"


def test_base_exception_not_caught_by_render_clip(render_harness, monkeypatch):
    """KeyboardInterrupt / SystemExit are BaseExceptions, not Exceptions — they must
    propagate out of render_clip without being swallowed by either the permanent or
    transient except branches. This pins the 'no catch BaseException' contract."""
    tasks, statuses, retry_calls, _ = render_harness

    async def _raises_keyboard(_clip_id, creator_id=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(tasks, "_render_clip_async", _raises_keyboard)

    with pytest.raises(KeyboardInterrupt):
        tasks.render_clip("clip-1")

    assert retry_calls == [], "KeyboardInterrupt must not trigger retry"
