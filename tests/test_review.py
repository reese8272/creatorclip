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


def test_submit_skip_is_acknowledged_but_never_persisted():
    """Issue 472: skip is queue navigation, not a verdict — a 201 ack with
    id=None, no ClipFeedback row, no triage change, no retrain enqueue."""
    from unittest.mock import patch

    creator = _make_creator()
    clip = _make_clip(creator.id)
    clip.triage = "kept"
    session_holder = {}

    async def fake_session():
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, clip)
        )
        session.add = MagicMock()
        session.commit = AsyncMock()
        session_holder["session"] = session
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    client = TestClient(app, raise_server_exceptions=True)

    with patch("worker.tasks.retrain_preference") as task:
        resp = client.post(f"/clips/{clip.id}/feedback", json={"action": "skip"})

    assert resp.status_code == 201
    assert resp.json() == {"id": None, "action": "skip"}
    session = session_holder["session"]
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    assert clip.triage == "kept", "skip must not move the pile"
    task.apply_async.assert_not_called()


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
    """Trim values past the clip duration → 422 (clip-relative timebase).

    clip duration = end_s - (setup_start_s ?? start_s) = 70 - 10 = 60s.
    trim_end = 80.0 is past the rendered clip's end — invalid.
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
    """Trim values within the clip duration → 201 (happy path)."""
    creator = _make_creator()
    clip = _make_clip(creator.id)  # duration = 70 - 10 = 60s
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 15.0, "trim_end_s": 60.0},
    )
    assert resp.status_code == 201


def test_trim_clip_relative_values_accepted_for_late_starting_clip():
    """Clip-relative trim near 0 is valid even when the clip starts late in the video.

    Regression (ready-pass W1): the Issue-339 check compared the frontend's
    CLIP-RELATIVE values against video-absolute start_s/end_s, so a trim of
    2→20s on a clip spanning 10→70s in the source 422'd with "trim_start_s is
    before the clip start" — i.e. Save trim was broken for nearly every clip.
    """
    creator = _make_creator()
    clip = _make_clip(creator.id)  # source window 10→70s; duration 60s
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 2.0, "trim_end_s": 20.0},
    )
    assert resp.status_code == 201


def test_trim_drag_to_end_rounding_accepted():
    """trim_end_s a few ms past the duration → 201 (one-frame tolerance).

    Regression (2026-07-29 assess): Save trim 422'd at exactly clip_duration_s
    while its sibling /trim-render allowed MIN_KEEP_SEGMENT_S past it — the UI
    drag-to-end float rounding lands a few ms over, so Save must accept what
    the re-render endpoint accepts.
    """
    creator = _make_creator()
    clip = _make_clip(creator.id)  # duration = 70 - 10 = 60s
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 15.0, "trim_end_s": 60.02},
    )
    assert resp.status_code == 201


def test_trim_end_past_tolerance_rejected():
    """trim_end_s beyond duration + MIN_KEEP_SEGMENT_S → 422."""
    from clip_engine.edits import MIN_KEEP_SEGMENT_S

    creator = _make_creator()
    clip = _make_clip(creator.id)  # duration = 60s
    client = _build_client(creator, clip)

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={
            "action": "trim",
            "trim_start_s": 15.0,
            "trim_end_s": 60.0 + MIN_KEEP_SEGMENT_S + 0.01,
        },
    )
    assert resp.status_code == 422


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
