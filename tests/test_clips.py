"""
Tests for Issue 216 — Honest personalization-status surface.

Verifies the ``personalization`` field on GET /videos/{id}/clips:
  (a) creator with labels < PERSONALIZATION_THRESHOLD_LABELS → active=False
  (b) creator with labels >= threshold → active=True
  (c) no virality terms in the new copy (structural compliance check)

DB/Redis are mocked — these tests run without Docker.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from config import settings
from db import get_session
from main import app
from models import Creator, IngestStatus, Video

# ── Helpers ───────────────────────────────────────────────────────────────────


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id: uuid.UUID) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.ingest_status = IngestStatus.done
    return v


def _mock_scorer(label_count: int) -> MagicMock:
    """Return a fake PreferenceScorer with the given label_count."""
    scorer = MagicMock()
    scorer.label_count = label_count
    return scorer


def _fake_session(video: MagicMock, clips: list) -> callable:
    """Build an async dependency override for get_session that returns the given
    video on session.get and the given clips on session.execute."""

    async def _session():
        session = AsyncMock()

        async def _get(model, pk, **kwargs):
            if model is Video:
                return video
            return None

        session.get = AsyncMock(side_effect=_get)

        result = MagicMock()
        result.scalars.return_value = iter(clips)
        session.execute = AsyncMock(return_value=result)
        yield session

    return _session


def _set_overrides(creator: MagicMock, video: MagicMock, clips: list) -> None:
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(video, clips)


# ── Test (a): below threshold → active=False ─────────────────────────────────


def test_personalization_below_threshold_returns_inactive(client):
    """GET /videos/{id}/clips with <20 labels → personalization.active=False."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [])
    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    below_count = max(0, threshold - 1)
    scorer = _mock_scorer(below_count)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=scorer)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    p = body["personalization"]
    assert p["active"] is False
    assert p["labels"] == below_count
    assert p["threshold"] == threshold
    assert p["weight"] == 0.0


def test_personalization_no_model_returns_inactive_zero_labels(client):
    """GET /videos/{id}/clips with no trained model → active=False, labels=0."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [])
    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    p = body["personalization"]
    assert p["active"] is False
    assert p["labels"] == 0
    assert p["threshold"] == threshold
    assert p["weight"] == 0.0


# ── Test (b): active means the blend weight is actually non-zero (Issue 474) ─


def test_personalization_at_exactly_threshold_is_not_active(client):
    """Issue 474 — at exactly T labels the ramp (n−T)/T is 0.0: the blend still
    serves pure DNA, so claiming "Personalized" would be dishonest. The UI must
    not claim active until the weight is > 0."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [])
    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    scorer = _mock_scorer(threshold)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=scorer)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    p = body["personalization"]
    assert p["weight"] == 0.0, "the ramp is (n-T)/T — zero at exactly T"
    assert p["active"] is False, "active must mean the preference model has weight"
    assert p["labels"] == threshold
    assert p["threshold"] == threshold


def test_personalization_one_past_threshold_is_active(client):
    """T+1 labels → the ramp is positive → active=True with the surfaced weight."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [])
    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    scorer = _mock_scorer(threshold + 1)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=scorer)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    p = body["personalization"]
    assert p["active"] is True
    assert p["weight"] > 0.0


def test_personalization_above_threshold_returns_active_with_ramp(client):
    """GET /videos/{id}/clips with labels > threshold → active=True + weight > 0."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [])
    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    above_count = threshold + 5
    scorer = _mock_scorer(above_count)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=scorer)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    p = body["personalization"]
    assert p["active"] is True
    assert p["weight"] > 0.0


# ── Test (c): no virality terms in the personalization copy ──────────────────

VIRALITY_TERMS = {"viral", "virality", "guarantee", "guaranteed", "promise", "promised"}


def test_personalization_below_threshold_copy_contains_no_virality_terms(client):
    """The 'Still learning' copy must not contain virality language."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [])
    scorer = _mock_scorer(0)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=scorer)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body_text = resp.text.lower()
    for term in VIRALITY_TERMS:
        assert term not in body_text, f"Found virality term {term!r} in response"


def test_personalization_above_threshold_copy_contains_no_virality_terms(client):
    """The 'Personalized' copy must not contain virality language."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [])
    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    scorer = _mock_scorer(threshold + 10)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=scorer)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body_text = resp.text.lower()
    for term in VIRALITY_TERMS:
        assert term not in body_text, f"Found virality term {term!r} in response"


# ── Test: _build_personalization_status unit ─────────────────────────────────


def test_build_personalization_status_none_scorer():
    """None scorer → active=False, labels=0, weight=0.0."""
    from routers.clips import _build_personalization_status

    status = _build_personalization_status(None)
    assert status.active is False
    assert status.labels == 0
    assert status.weight == 0.0


def test_build_personalization_status_below_threshold():
    """Scorer with label_count < threshold → active=False."""
    from routers.clips import _build_personalization_status

    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    scorer = _mock_scorer(threshold - 1)
    status = _build_personalization_status(scorer)
    assert status.active is False
    assert status.weight == 0.0


def test_build_personalization_status_at_threshold():
    """Scorer with label_count == threshold → weight 0.0 → NOT active (Issue 474)."""
    from routers.clips import _build_personalization_status

    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    scorer = _mock_scorer(threshold)
    status = _build_personalization_status(scorer)
    assert status.weight == 0.0
    assert status.active is False
    assert status.threshold == threshold


def test_build_personalization_status_above_threshold():
    """Scorer with label_count == 2*threshold → weight == PREFERENCE_WEIGHT_CAP."""
    from routers.clips import _build_personalization_status

    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    cap = settings.PREFERENCE_WEIGHT_CAP
    scorer = _mock_scorer(threshold * 2)
    status = _build_personalization_status(scorer)
    assert status.active is True
    assert abs(status.weight - cap) < 1e-6


# ── Issue 447: downloaded_at + latest-publication summary on the list surface ──


def _full_clip(creator_id: uuid.UUID, video_id: uuid.UUID, rank: int) -> MagicMock:
    """Mock clip carrying every attribute _clip_response reads."""
    from datetime import UTC, datetime

    from models import Clip, ClipTriage, RenderStatus

    c = MagicMock(spec=Clip)
    c.id = uuid.uuid4()
    c.video_id = video_id
    c.creator_id = creator_id
    c.setup_start_s = 5.0
    c.start_s = 5.0
    c.end_s = 65.0
    c.peak_s = 50.0
    c.score = 0.8
    c.rank = rank
    c.signals_jsonb = {"principle": "Hook in the first 3 seconds", "reasoning": "Strong."}
    c.render_status = RenderStatus.done
    c.render_uri = f"s3://b/clips/{c.id}.mp4"
    c.cleaned_render_uri = None
    c.applied_title = None
    c.applied_description = None
    c.suggested_title = None
    c.suggested_description = None
    c.suggested_hook = None
    c.style_preset = None
    c.poster_uri = None
    c.reframe_track_jsonb = None
    c.triage = ClipTriage.kept
    c.downloaded_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    return c


def test_list_clips_carries_download_and_publication_state_without_n_plus_1(client):
    """Issue 447: each clip carries ``downloaded_at`` and a latest-publication
    summary (status / scheduled_at / youtube_video_id), fetched for the WHOLE
    list in ONE aggregate query — execute count stays flat as clips grow."""
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from models import ClipPublication, PublishStatus
    from tests._helpers import owned_result

    creator = _creator()
    video = _video(creator.id)
    clip_a = _full_clip(creator.id, video.id, rank=1)
    clip_b = _full_clip(creator.id, video.id, rank=2)

    pub = MagicMock(spec=ClipPublication)
    pub.clip_id = clip_a.id
    pub.status = PublishStatus.done
    pub.scheduled_at = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)
    pub.youtube_video_id = "yt-abc123"

    clips_result = MagicMock()
    clips_result.scalars.return_value = iter([clip_a, clip_b])
    pubs_result = MagicMock()
    pubs_result.scalars.return_value = [pub]

    async def _session():
        session = AsyncMock(spec=AsyncSession)
        # Three executes TOTAL for a two-clip list: get_owned(Video), the clip
        # select, and ONE publications aggregate (load_latest is patched out).
        # A per-clip lookup would exhaust this side_effect list and error.
        session.execute = AsyncMock(side_effect=[owned_result(video), clips_result, pubs_result])
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    clips = resp.json()["clips"]
    assert len(clips) == 2

    a = next(c for c in clips if c["id"] == str(clip_a.id))
    b = next(c for c in clips if c["id"] == str(clip_b.id))
    assert a["downloaded_at"] is not None
    assert a["latest_publication"] == {
        "status": "done",
        "scheduled_at": "2026-08-11T18:00:00Z",
        "youtube_video_id": "yt-abc123",
    }
    # clip_b has no publications — honest null, never a fabricated state.
    assert b["latest_publication"] is None
