"""
Tests for Issue 16 — auto-trigger clip generation + status polling.
Covers: generate_clips task exists, build_signals chains into it,
dashboard includes polling JS, video status endpoint.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import get_current_creator
from db import get_session
from main import app
from models import IngestStatus, VideoKind
from tests._helpers import override_current_creator, owned_lookup_result

# ── generate_clips task ───────────────────────────────────────────────────────


def test_generate_clips_task_registered():
    from worker.celery_app import celery

    assert "worker.tasks.generate_clips" in celery.tasks


def test_build_signals_chains_generate_clips(monkeypatch):
    """build_signals must enqueue generate_clips.delay(video_id) after its body
    succeeds — the signals→generate_clips stage hop (W1: now guarded by a
    try/except that must NOT swallow the success path)."""
    from worker import tasks

    vid = str(uuid.uuid4())

    async def _creator(video_id):
        return "creator-1"

    async def _ok(video_id, creator_id=None):
        return None

    monkeypatch.setattr(tasks, "_creator_id_for_video", _creator)
    monkeypatch.setattr(tasks, "_signals_async", _ok)
    delay = MagicMock()
    monkeypatch.setattr(tasks.generate_clips, "delay", delay)

    assert tasks.build_signals(vid) == vid
    delay.assert_called_once_with(vid)


def test_build_signals_enqueue_failure_marks_failed_and_refunds_once(monkeypatch):
    """Broker publish failure on the signals→generate_clips hop (Celery raises
    OperationalError after its built-in ~0.4s publish retries): the video must
    be marked failed with the actionable queue reason, the enqueue-failed event
    emitted, NO task retry (the retry message would go to the same down broker),
    and the on_failure refund must fire exactly once — no double-refund."""
    from worker import tasks

    vid = str(uuid.uuid4())
    statuses: list[tuple[IngestStatus, str | None]] = []
    events: list[str] = []

    async def _creator(video_id):
        return "creator-1"

    async def _ok(video_id, creator_id=None):
        return None

    async def _set_status(video_id, status, reason=None):
        statuses.append((status, reason))

    def _no_retry(**kwargs):
        raise AssertionError("a publish failure must not retry — the broker is down")

    monkeypatch.setattr(tasks, "_creator_id_for_video", _creator)
    monkeypatch.setattr(tasks, "_signals_async", _ok)
    monkeypatch.setattr(tasks, "_set_status", _set_status)
    monkeypatch.setattr(tasks, "log_event", lambda event, **kw: events.append(event))
    monkeypatch.setattr(tasks.build_signals, "retry", _no_retry)

    fake_refund = AsyncMock()
    monkeypatch.setattr("billing.refund.refund_for_video", fake_refund)
    monkeypatch.setattr(tasks, "_fire_refund_notification_async", AsyncMock())
    monkeypatch.setattr(
        tasks.generate_clips,
        "delay",
        MagicMock(side_effect=tasks.generate_clips.OperationalError("broker down")),
    )

    # .apply() runs eagerly through the Celery tracer so on_failure (the refund
    # path) fires exactly as it would in a real worker.
    result = tasks.build_signals.apply(args=[vid], task_id="tid-enqueue-fail")

    assert result.failed()
    assert statuses == [
        (IngestStatus.failed, "Clip generation could not be queued — please retry.")
    ]
    assert "generate_clips_enqueue_failed" in events
    assert "build_signals_done" not in events
    assert fake_refund.await_count == 1, "refund must fire exactly once (idempotent pack_id)"


# ── Video status endpoint ─────────────────────────────────────────────────────


def _mock_video(creator_id, status=IngestStatus.running):
    import datetime

    v = MagicMock()
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.youtube_video_id = "yt123"
    v.title = "Test"
    v.kind = VideoKind.long
    v.ingest_status = status
    v.source_uri = None
    v.captions_available = False
    v.duration_s = None
    v.created_at = datetime.datetime.now(datetime.UTC)
    return v


def test_video_status_endpoint_returns_status(client):
    creator = MagicMock()
    creator.id = uuid.uuid4()
    video = _mock_video(creator.id, status=IngestStatus.running)

    async def fake_session():
        session = AsyncMock()
        # get_owned ownership select (Issue 109e) — emulates the DB predicate.
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, video)
        )
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    try:
        resp = client.get(f"/videos/{video.id}/status")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["ingest_status"] == "running"


def test_video_status_404_wrong_creator(client):
    creator = MagicMock()
    creator.id = uuid.uuid4()
    other_creator_id = uuid.uuid4()
    video = _mock_video(other_creator_id)

    async def fake_session():
        session = AsyncMock()
        # get_owned ownership select (Issue 109e) — emulates the DB predicate.
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, video)
        )
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    try:
        resp = client.get(f"/videos/{video.id}/status")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


# ── Dashboard includes polling JS ─────────────────────────────────────────────


@pytest.mark.skip("Issue 226: legacy static pages retired — index.html deleted")
def test_dashboard_includes_polling(client):
    # Legacy-dashboard content; `/` redirects to the SPA once built (Issue 85g).
    content = client.get("/static/index.html").text
    # The dashboard schedules an in-progress poll. The 2026-06-08 BLOCKER fix
    # switched from `setInterval` (no cap, no backoff) to a `setTimeout`
    # recursion so we can cap total ticks AND back off when nothing changes.
    # Accept either to keep this test resilient to future scheduler swaps.
    assert "setInterval" in content or "setTimeout" in content
    assert "ingest_status" in content or "data-status" in content


@pytest.mark.skip("Issue 226: legacy static pages retired — index.html deleted")
def test_dashboard_polling_calls_status_endpoint(client):
    content = client.get("/static/index.html").text
    assert "/videos/" in content
    assert "/status" in content


@pytest.mark.skip("Issue 226: legacy static pages retired — index.html deleted")
def test_dashboard_starts_polling_after_load(client):
    content = client.get("/static/index.html").text
    assert "startPolling" in content
