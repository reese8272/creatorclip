"""Tests for GET /videos/{video_id}/transcript — full-source transcript (Issue 372)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, Transcript, Video
from tests._helpers import owned_lookup_result


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id, *, duration_s=120.0) -> MagicMock:
    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.creator_id = creator_id
    video.duration_s = duration_s
    return video


def _transcript(segments) -> MagicMock:
    t = MagicMock(spec=Transcript)
    t.source = "deepgram"
    t.segments_jsonb = {"segments": segments}
    return t


def _overrides(creator, video, transcript):
    async def _session():
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, video)
        )
        session.get = AsyncMock(return_value=transcript)
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session


def test_transcript_cross_creator_returns_404(client):
    creator = _creator()
    video = _video(uuid.uuid4())
    _overrides(creator, video, None)
    try:
        resp = client.get(f"/videos/{video.id}/transcript", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_transcript_missing_returns_honest_empty_envelope(client):
    # Not-yet-transcribed video → 200 empty_initial with a message, never 404
    # (Recap retry idiom: absence of data must not read as a missing video).
    creator = _creator()
    video = _video(creator.id)
    _overrides(creator, video, None)
    try:
        resp = client.get(f"/videos/{video.id}/transcript", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "empty_initial"
    assert body["segments"] == []
    assert "transcribed" in body["message"]


def test_transcript_maps_segments_and_skips_malformed(client):
    creator = _creator()
    video = _video(creator.id, duration_s=None)  # duration falls back to last segment end
    transcript = _transcript(
        [
            {"text": "Hello there", "start": 0.0, "end": 2.5},
            {"start": 2.5},  # malformed — must be skipped, not crash the panel
            {"text": "General Kenobi", "start": 2.5, "end": 5.0},
        ]
    )
    _overrides(creator, video, transcript)
    try:
        resp = client.get(f"/videos/{video.id}/transcript", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "populated"
    assert [s["text"] for s in body["segments"]] == ["Hello there", "General Kenobi"]
    assert body["segments"][0]["start_s"] == 0.0
    assert body["duration_s"] == 5.0  # last segment end fallback
    assert body["source"] == "deepgram"
