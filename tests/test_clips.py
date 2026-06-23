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


# ── Test (b): at/above threshold → active=True ───────────────────────────────


def test_personalization_at_threshold_returns_active(client):
    """GET /videos/{id}/clips with labels==threshold → personalization.active=True."""
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
    assert p["active"] is True
    assert p["labels"] == threshold
    assert p["threshold"] == threshold


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
    """Scorer with label_count == threshold → active=True."""
    from routers.clips import _build_personalization_status

    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    scorer = _mock_scorer(threshold)
    status = _build_personalization_status(scorer)
    assert status.active is True
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
