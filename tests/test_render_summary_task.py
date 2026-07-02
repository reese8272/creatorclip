"""Wiring tests for the render_summary Celery task (Issue 191).

House pattern (test_render_retry.py + test_progress_emit_wiring.py): the DB
session factory, SSE emitter, storage, and ffmpeg are all mocked — these tests
pin the task's contracts, not the render itself:

  - durable idempotency: a redelivery of an already-rendered summary no-ops
  - permanent vs transient classification (ValueError terminal, no retry)
  - missing source (72h retention window) fails cleanly with an actionable message
  - step events fire in stage order: render_start → download_source →
    ffmpeg_encode → upload_r2 → done
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import RenderStatus, Summary, Video


class _RetryCalled(Exception):
    """Sentinel raised by the patched ``self.retry`` so tests can detect retries."""


def _emit_labels(mock_emit: AsyncMock) -> list[str]:
    out: list[str] = []
    for call in mock_emit.call_args_list:
        event_type = call.args[1]
        if event_type in ("done", "error"):
            out.append(event_type)
        else:
            out.append(call.kwargs.get("label") or event_type)
    return out


def _mock_session(mocker, get_side_effect) -> AsyncMock:
    """Patch db.AdminSessionLocal with a fake async-context session."""
    fake_session = AsyncMock()
    fake_session.get = AsyncMock(side_effect=get_side_effect)
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))
    return fake_session


def _summary_stub(summary_id: str, *, status=RenderStatus.pending, render_uri=None) -> MagicMock:
    stub = MagicMock(spec=Summary)
    stub.id = uuid.UUID(summary_id)
    stub.creator_id = uuid.uuid4()
    stub.video_id = uuid.uuid4()
    stub.render_status = status
    stub.render_uri = render_uri
    stub.segments = [
        {"start_s": 10.0, "end_s": 25.0, "score": 0.9, "principle": "P", "rationale": ""},
        {"start_s": 60.0, "end_s": 90.0, "score": 0.8, "principle": "P", "rationale": ""},
    ]
    return stub


# ── _render_summary_async: idempotency + source retention + step events ──────


async def test_redelivery_of_rendered_summary_noops(mocker):
    """render_status=done + render_uri set → emit terminal done, never re-encode."""
    from worker import tasks

    summary_id = str(uuid.uuid4())
    stub = _summary_stub(summary_id, status=RenderStatus.done, render_uri="r2://summaries/x.mp4")
    _mock_session(mocker, lambda model, pk, **kw: stub)

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)
    fake_upload = AsyncMock()
    mocker.patch("worker.storage.aupload_file", fake_upload)
    fake_render = mocker.patch("clip_engine.render.render_summary_file", MagicMock())

    await tasks._render_summary_async(summary_id)

    fake_render.assert_not_called()
    fake_upload.assert_not_called()
    assert stub.render_status == RenderStatus.done  # untouched
    assert "done" in _emit_labels(fake_emit)


async def test_missing_source_raises_actionable_valueerror(mocker):
    """source_uri purged by the 72h retention window → permanent ValueError with
    an actionable message; the error event fires."""
    from worker import tasks

    summary_id = str(uuid.uuid4())
    stub = _summary_stub(summary_id)
    video_stub = MagicMock(spec=Video)
    video_stub.source_uri = None

    async def _get(model, pk, **kw):
        return stub if model is Summary else video_stub

    _mock_session(mocker, _get)
    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    with pytest.raises(ValueError, match="retention window.*re-upload"):
        await tasks._render_summary_async(summary_id)

    assert "error" in _emit_labels(fake_emit)


async def test_happy_path_emits_steps_and_marks_done(mocker, tmp_path):
    """Full render: step events in stage order, upload keyed on the summary id,
    render_uri + render_status=done persisted, temp media cleaned."""
    from worker import tasks

    summary_id = str(uuid.uuid4())
    stub = _summary_stub(summary_id)
    video_stub = MagicMock(spec=Video)
    video_stub.source_uri = "r2://source/vod.mp4"

    async def _get(model, pk, **kw):
        return stub if model is Summary else video_stub

    _mock_session(mocker, _get)

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    src = tmp_path / "vod.mp4"
    src.touch()
    fake_local_cm = MagicMock()
    fake_local_cm.__aenter__ = AsyncMock(return_value=src)
    fake_local_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("worker.storage.alocal_path", MagicMock(return_value=fake_local_cm))
    fake_upload = AsyncMock(return_value=f"r2://summaries/{summary_id}.mp4")
    mocker.patch("worker.storage.aupload_file", fake_upload)

    fake_render = mocker.patch("clip_engine.render.render_summary_file", MagicMock())

    await tasks._render_summary_async(summary_id)

    # render got the chronological (start,end) tuples from segments JSONB
    kwargs = fake_render.call_args.kwargs
    assert kwargs["segments"] == [(10.0, 25.0), (60.0, 90.0)]
    assert kwargs["source_path"] == src

    # upload keyed under summaries/ prefix; temp out file cleaned
    upload_path, upload_key = fake_upload.call_args.args
    assert upload_key == f"summaries/{summary_id}.mp4"
    assert not upload_path.exists(), "temp render output must be cleaned up"

    assert stub.render_status == RenderStatus.done
    assert stub.render_uri == f"r2://summaries/{summary_id}.mp4"

    labels = _emit_labels(fake_emit)
    for expected in ("render_start", "download_source", "ffmpeg_encode", "upload_r2", "done"):
        assert expected in labels
    assert labels.index("ffmpeg_encode") < labels.index("upload_r2") < labels.index("done")
    for call in fake_emit.call_args_list:
        assert call.args[0] == summary_id, "SSE stream key must be the summary id"


# ── render_summary task: permanent vs transient classification ───────────────


@pytest.fixture
def summary_harness(monkeypatch):
    """Patch render_summary's async helpers + self.retry (test_render_retry pattern)."""
    from worker import tasks

    statuses: list[RenderStatus] = []
    retry_calls: list[dict] = []

    async def _creator(summary_id):
        return "creator-1"

    async def _set_status(summary_id, status):
        statuses.append(status)

    def _fake_retry(**kwargs):
        retry_calls.append(kwargs)
        return _RetryCalled()

    monkeypatch.setattr(tasks, "_creator_id_for_summary", _creator)
    monkeypatch.setattr(tasks, "_set_summary_render_status", _set_status)
    monkeypatch.setattr(tasks.render_summary, "retry", _fake_retry)

    def _set_render_async(exc):
        async def _impl(summary_id):
            raise exc

        monkeypatch.setattr(tasks, "_render_summary_async", _impl)

    return tasks, statuses, retry_calls, _set_render_async


@pytest.mark.parametrize("exc", [ValueError("source purged"), FileNotFoundError("local src gone")])
def test_permanent_error_marks_failed_without_retry(summary_harness, exc):
    tasks, statuses, retry_calls, set_render_async = summary_harness
    set_render_async(exc)

    with pytest.raises(type(exc)):
        tasks.render_summary("summary-1")

    assert retry_calls == [], "permanent errors must not retry"
    assert statuses == [RenderStatus.failed]


def test_transient_error_retries(summary_harness):
    tasks, statuses, retry_calls, set_render_async = summary_harness
    set_render_async(RuntimeError("ffmpeg blip / R2 hiccup"))

    with pytest.raises(_RetryCalled):
        tasks.render_summary("summary-1")

    assert len(retry_calls) == 1, "transient errors must go through self.retry"
    assert statuses == [RenderStatus.failed]


def test_success_returns_summary_id(summary_harness, monkeypatch):
    tasks, statuses, retry_calls, _ = summary_harness

    async def _ok(summary_id):
        return None

    monkeypatch.setattr(tasks, "_render_summary_async", _ok)
    assert tasks.render_summary("summary-1") == "summary-1"
    assert statuses == [] and retry_calls == []
