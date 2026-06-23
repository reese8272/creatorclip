"""Empty-state response envelopes (DECISIONS 2026-06-08).

Three list endpoints converge on a single shape:
``{<resource>: [...], state, message, next_action}``. This file pins the
state-transition contract per endpoint — populated stays quiet,
empty_initial carries guided copy — so a future refactor cannot silently
drop the empty-state guidance and re-introduce the "barren dashboard"
UX bug the 2026-06-08 ``/assess`` flagged.
"""

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

from auth import get_current_creator
from db import get_session
from main import app
from models import IngestStatus, OnboardingState, RenderStatus, VideoKind, VideoOrigin


def _mock_creator(onboarding_state=OnboardingState.active):
    c = MagicMock()
    c.id = uuid.uuid4()
    c.onboarding_state = onboarding_state
    return c


class _Scalars:
    """Stand-in for SQLAlchemy ScalarResult — supports iter() AND .all().

    /videos consumes ``list(result.scalars())`` (iterates) while
    /insights/saved consumes ``result.scalars().all()``. The real
    ScalarResult supports both; this helper does too.
    """

    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)


def _scalars_session(rows):
    async def _session():
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value = _Scalars(rows)
        session.execute = AsyncMock(return_value=result)
        yield session

    return _session


def _mock_video(creator_id):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.youtube_video_id = "abc12345678"
    v.title = "Test"
    v.kind = VideoKind.long
    v.ingest_status = IngestStatus.done
    v.duration_s = 600.0
    v.created_at = datetime.datetime.now(datetime.UTC)
    v.origin = VideoOrigin.upload
    v.source_uri = f"source/{creator_id}/abc12345678.mp4"
    return v


def _mock_clip(creator_id, video_id):
    c = MagicMock()
    c.id = uuid.uuid4()
    c.video_id = video_id
    c.creator_id = creator_id
    c.setup_start_s = None
    c.start_s = 10.0
    c.end_s = 70.0
    c.peak_s = None
    c.score = 0.7
    c.rank = 1
    c.signals_jsonb = {"principle": "setup-first", "reasoning": "ok"}
    c.render_status = RenderStatus.done
    c.render_uri = "r2://clip.mp4"
    c.cleaned_render_uri = None
    return c


def _mock_insight(creator_id):
    i = MagicMock()
    i.id = uuid.uuid4()
    i.video_id = None
    i.insight_type = MagicMock(value="trend")
    i.title = "Pinned"
    i.content = "..."
    i.dna_version = 1
    i.is_saved = True
    i.created_at = datetime.datetime.now(datetime.UTC)
    return i


# ── /videos ───────────────────────────────────────────────────────────────────


def test_videos_empty_connected_uses_open_form(client):
    """OnboardingState.connected = creator linked YouTube but hasn't linked
    a video yet. Envelope nudges them to the inline link form (open_form)
    rather than navigating to OAuth again."""
    creator = _mock_creator(onboarding_state=OnboardingState.connected)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _scalars_session([])
    try:
        body = client.get("/videos").json()
    finally:
        app.dependency_overrides.clear()
    assert body["state"] == "empty_initial"
    assert body["next_action"]["action_type"] == "open_form"


def test_videos_populated_carries_no_copy(client):
    """``populated`` state must NOT carry message/next_action — the frontend
    would render stale guidance over a healthy list otherwise."""
    creator = _mock_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _scalars_session([_mock_video(creator.id)])
    try:
        body = client.get("/videos").json()
    finally:
        app.dependency_overrides.clear()
    assert body["state"] == "populated"
    assert body["message"] is None
    assert body["next_action"] is None


# ── /creators/me/insights/saved ──────────────────────────────────────────────


def test_saved_insights_empty_returns_envelope(client):
    creator = _mock_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _scalars_session([])
    try:
        body = client.get("/creators/me/insights/saved").json()
    finally:
        app.dependency_overrides.clear()
    assert body["insights"] == []
    assert body["state"] == "empty_initial"
    assert body["next_action"]["url"] == "/static/insights.html"


def test_saved_insights_populated(client):
    creator = _mock_creator()
    insight = _mock_insight(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _scalars_session([insight])
    try:
        body = client.get("/creators/me/insights/saved").json()
    finally:
        app.dependency_overrides.clear()
    assert body["state"] == "populated"
    assert len(body["insights"]) == 1


# ── /videos/{id}/clips ───────────────────────────────────────────────────────


def _clips_session(video, clips):
    """list_clips calls session.get(Video, ...) first, then session.execute() twice:
    once for the Clip query and once for the PreferenceModel query (Issue 216).
    Return the clips result for the first execute and a no-model result for the second.
    """

    async def _session():
        session = AsyncMock()
        session.get = AsyncMock(return_value=video)

        clips_result = MagicMock()
        clips_result.scalars.return_value = clips

        # PreferenceModel query returns no rows — simulates no model yet.
        pref_result = MagicMock()
        pref_result.first.return_value = None

        session.execute = AsyncMock(side_effect=[clips_result, pref_result])
        yield session

    return _session


def test_clips_empty_while_ingesting_says_wait(client):
    """Ingest still running → empty clips message must NOT push the user
    to /generate (which would 400). The message text is the cue."""
    creator = _mock_creator()
    video = _mock_video(creator.id)
    video.ingest_status = IngestStatus.running
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _clips_session(video, [])
    try:
        body = client.get(f"/videos/{video.id}/clips").json()
    finally:
        app.dependency_overrides.clear()
    assert body["state"] == "empty_initial"
    assert body["next_action"] is None
    assert "ingesting" in body["message"].lower()


def test_clips_empty_after_ingest_offers_generate(client):
    """Ingest done + 0 clips → next_action points at /generate so the user
    has a one-click path forward."""
    creator = _mock_creator()
    video = _mock_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _clips_session(video, [])
    try:
        body = client.get(f"/videos/{video.id}/clips").json()
    finally:
        app.dependency_overrides.clear()
    assert body["state"] == "empty_initial"
    assert body["next_action"]["url"].endswith("/clips/generate")


def test_clips_populated(client):
    creator = _mock_creator()
    video = _mock_video(creator.id)
    clip = _mock_clip(creator.id, video.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _clips_session(video, [clip])
    try:
        body = client.get(f"/videos/{video.id}/clips").json()
    finally:
        app.dependency_overrides.clear()
    assert body["state"] == "populated"
    assert len(body["clips"]) == 1
    assert body["message"] is None
