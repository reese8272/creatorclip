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
        async def _impl(clip_id):
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

    async def _ok(clip_id):
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


def test_base_exception_not_caught_by_render_clip(render_harness, monkeypatch):
    """KeyboardInterrupt / SystemExit are BaseExceptions, not Exceptions — they must
    propagate out of render_clip without being swallowed by either the permanent or
    transient except branches. This pins the 'no catch BaseException' contract."""
    tasks, statuses, retry_calls, _ = render_harness

    async def _raises_keyboard(_clip_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(tasks, "_render_clip_async", _raises_keyboard)

    with pytest.raises(KeyboardInterrupt):
        tasks.render_clip("clip-1")

    assert retry_calls == [], "KeyboardInterrupt must not trigger retry"
