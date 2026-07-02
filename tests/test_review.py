"""
Unit tests for routers/review.py — feedback endpoint logic.

Uses FastAPI TestClient with a stubbed get_current_creator dependency.
DB session is mocked via dependency override.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import Clip, Creator, RenderStatus
from tests._helpers import override_current_creator, owned_lookup_result


def _make_creator():
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.channel_title = "TestChannel"
    c.email = "test@example.com"
    return c


def _make_clip(creator_id: uuid.UUID) -> MagicMock:
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 10.0
    clip.start_s = 10.0
    clip.end_s = 70.0
    clip.peak_s = 55.0
    clip.score = 0.8
    clip.rank = 1
    clip.signals_jsonb = {"principle": "Hook in the first 3 seconds", "reasoning": "Strong."}
    clip.render_status = RenderStatus.pending
    clip.render_uri = None
    return clip


def _build_client(creator, clip):
    async def fake_session():
        session = AsyncMock()
        # get_owned ownership select (Issue 109e) — emulates the DB predicate.
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, clip)
        )
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    client = TestClient(app, raise_server_exceptions=True)
    return client


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_submit_upvote():
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(f"/clips/{clip.id}/feedback", json={"action": "upvote"})
    assert resp.status_code == 201
    assert resp.json()["action"] == "upvote"


def test_submit_downvote():
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(f"/clips/{clip.id}/feedback", json={"action": "downvote"})
    assert resp.status_code == 201


def test_submit_trim_with_times():
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    # trim values must be within [clip.start_s=10.0, clip.end_s=70.0].
    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 15.0, "trim_end_s": 55.0},
    )
    assert resp.status_code == 201
    assert resp.json()["action"] == "trim"


def test_submit_skip():
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(f"/clips/{clip.id}/feedback", json={"action": "skip"})
    assert resp.status_code == 201


def test_feedback_invalid_action_rejected():
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(f"/clips/{clip.id}/feedback", json={"action": "invalid"})
    assert resp.status_code == 422


def test_feedback_wrong_creator_returns_404():
    """A different creator's clip must return 404, not the clip data."""
    creator = _make_creator()
    other_creator_id = uuid.uuid4()

    clip = _make_clip(other_creator_id)  # belongs to someone else

    async def fake_session():
        session = AsyncMock()
        # Foreign clip: the ownership predicate filters it out server-side (Issue 109e).
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, clip)
        )
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(f"/clips/{clip.id}/feedback", json={"action": "upvote"})
    assert resp.status_code == 404


def test_feedback_clip_not_found_returns_404():
    creator = _make_creator()

    async def fake_session():
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, None)
        )
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(f"/clips/{uuid.uuid4()}/feedback", json={"action": "upvote"})
    assert resp.status_code == 404


# ── Issue 339: trim / feedback_note validation ────────────────────────────────


def test_trim_negative_start_rejected():
    """trim_start_s < 0 → 422 (Pydantic model validator)."""
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": -1.0, "trim_end_s": 50.0},
    )
    assert resp.status_code == 422


def test_trim_inverted_rejected():
    """trim_start_s > trim_end_s → 422 (inverted trim window)."""
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 50.0, "trim_end_s": 10.0},
    )
    assert resp.status_code == 422


def test_trim_start_equals_end_rejected():
    """trim_start_s == trim_end_s → 422 (zero-width window)."""
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 30.0, "trim_end_s": 30.0},
    )
    assert resp.status_code == 422


def test_trim_nonfinite_rejected_at_model_level():
    """NaN and inf trim values are rejected by the model validator.

    These non-finite floats cannot be tested via standard JSON (the error
    response serialization also fails on nan/inf). Testing at the Pydantic
    model level is the correct approach — it's where the validator runs.
    """
    import math

    import pytest as _pytest
    from pydantic import ValidationError

    from routers.review import FeedbackRequest

    with _pytest.raises(ValidationError):
        FeedbackRequest(action="trim", trim_start_s=math.nan, trim_end_s=50.0)

    with _pytest.raises(ValidationError):
        FeedbackRequest(action="trim", trim_start_s=math.inf, trim_end_s=50.0)

    with _pytest.raises(ValidationError):
        FeedbackRequest(action="trim", trim_start_s=10.0, trim_end_s=math.inf)


def test_trim_outside_clip_window_rejected():
    """Trim values outside [clip.start_s, clip.end_s] → 422.

    clip.start_s = 10.0, clip.end_s = 70.0.  trim_start = 5.0 is before
    clip start; trim_end = 80.0 is past clip end — both invalid.
    """
    creator = _make_creator()
    clip = _make_clip(creator.id)  # start_s=10.0, end_s=70.0
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 5.0, "trim_end_s": 80.0},
    )
    assert resp.status_code == 422


def test_trim_within_clip_window_accepted():
    """Trim values within [clip.start_s, clip.end_s] → 201 (happy path)."""
    creator = _make_creator()
    clip = _make_clip(creator.id)  # start_s=10.0, end_s=70.0
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 15.0, "trim_end_s": 60.0},
    )
    assert resp.status_code == 201


def test_feedback_note_over_max_length_rejected():
    """feedback_note > 2000 chars → 422 (Pydantic max_length constraint)."""
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "upvote", "feedback_note": "x" * 2001},
    )
    assert resp.status_code == 422


def test_feedback_note_at_max_length_accepted():
    """feedback_note exactly 2000 chars → 201 (boundary is inclusive)."""
    creator = _make_creator()
    clip = _make_clip(creator.id)
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "upvote", "feedback_note": "x" * 2000},
    )
    assert resp.status_code == 201
