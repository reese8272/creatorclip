"""
Tests for Issue 14 — static serving + UI shell.
Covers: GET /, static file serving, GET /videos (list endpoint).
"""

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

from auth import get_current_creator
from db import get_session
from main import app
from models import IngestStatus, VideoKind

# ── Root and static routes ────────────────────────────────────────────────────


def test_root_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_root_contains_creatorclip(client):
    resp = client.get("/")
    assert b"CreatorClip" in resp.content


def test_static_onboarding_served(client):
    resp = client.get("/static/onboarding.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_static_review_served(client):
    resp = client.get("/static/review.html")
    assert resp.status_code == 200


def test_static_profile_served(client):
    resp = client.get("/static/profile.html")
    assert resp.status_code == 200


def test_static_insights_served(client):
    resp = client.get("/static/insights.html")
    assert resp.status_code == 200


def test_static_tos_served(client):
    resp = client.get("/static/tos.html")
    assert resp.status_code == 200
    assert b"Terms of Service" in resp.content


def test_static_privacy_served(client):
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    assert b"Privacy Policy" in resp.content


# ── GET /videos list endpoint ─────────────────────────────────────────────────


def test_list_videos_requires_auth(client):
    resp = client.get("/videos")
    assert resp.status_code == 401


def _mock_creator():
    c = MagicMock()
    c.id = uuid.uuid4()
    return c


def _mock_video(creator_id, title="Test video", yt_id="abc123"):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.youtube_video_id = yt_id
    v.title = title
    v.kind = VideoKind.long
    v.ingest_status = IngestStatus.done
    v.duration_s = 600.0
    v.created_at = datetime.datetime.now(datetime.UTC)
    return v


def _fake_session(videos):
    async def _session():
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value = videos
        session.execute = AsyncMock(return_value=result)
        yield session

    return _session


def test_list_videos_returns_list(client):
    creator = _mock_creator()
    video = _mock_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([video])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["youtube_video_id"] == "abc123"
    assert data[0]["ingest_status"] == "done"


def test_list_videos_empty_returns_empty_list(client):
    creator = _mock_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_videos_response_has_required_keys(client):
    creator = _mock_creator()
    video = _mock_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([video])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    item = resp.json()[0]
    for key in ("id", "youtube_video_id", "title", "kind", "ingest_status", "created_at"):
        assert key in item
