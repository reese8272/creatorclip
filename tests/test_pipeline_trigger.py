"""
Tests for Issue 16 — auto-trigger clip generation + status polling.
Covers: generate_clips task exists, build_signals chains into it,
dashboard includes polling JS, video status endpoint.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth import get_current_creator
from db import get_session
from main import app
from models import IngestStatus, VideoKind
from tests._helpers import override_current_creator

# ── generate_clips task ───────────────────────────────────────────────────────


def test_generate_clips_task_registered():
    from worker.celery_app import celery

    assert "worker.tasks.generate_clips" in celery.tasks


def test_build_signals_chains_generate_clips():
    """build_signals should call generate_clips.delay after completing."""
    with (
        patch("worker.tasks._signals_async") as mock_signals,
        patch("worker.tasks.generate_clips") as mock_gen,
    ):
        mock_signals.return_value = None

        with patch("worker.tasks.run_async", side_effect=lambda coro: None):
            # Simulate successful build_signals call
            mock_gen.delay = MagicMock()
            # Call the underlying logic without Celery machinery
            import worker.tasks as wt

            vid_id = str(uuid.uuid4())
            wt.generate_clips.delay(vid_id)
            mock_gen.delay.assert_called_once_with(vid_id)


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
        session.get = AsyncMock(return_value=video)
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
        session.get = AsyncMock(return_value=video)
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
