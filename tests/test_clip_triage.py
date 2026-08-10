"""
Tests for Issue 444 — PUT /clips/{id}/triage, the creator's current verdict.

Triage is a mutable, reversible workflow state. Moving a clip between piles IS a
change of verdict, so it records one in the SAME transaction — state and label
can never disagree. That is only safe because preference/train.py now keeps one
label per clip (the newest); the derived row supersedes the previous verdict
instead of stacking a contradiction on it. The dedup itself lives in SQL and is
proven in tests/test_clip_triage_integration.py.

DB/Redis are mocked — these run without Docker.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from db import get_session
from main import app
from models import ClipFeedback, ClipTriage, Creator, FeedbackAction


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _clip(triage: ClipTriage = ClipTriage.pending) -> MagicMock:
    clip = MagicMock()
    clip.id = uuid.uuid4()
    clip.triage = triage
    return clip


def _overrides(creator: MagicMock, session: MagicMock) -> None:
    async def _session():
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session


def _session_mock(first_keep: bool = False) -> MagicMock:
    """A session whose _is_first_keep existence probe returns a known answer."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = not first_keep
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


def _put(client, clip, triage: str, body: dict | None = None):
    return client.put(
        f"/clips/{clip.id}/triage",
        json=body if body is not None else {"triage": triage},
        cookies={"session": "x"},
    )


def _added_feedback(session: MagicMock) -> list:
    return [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], ClipFeedback)]


def test_triage_sets_the_state_and_records_the_verdict(client):
    """A pile move is a change of opinion, so it writes the matching label —
    in one transaction, so the pile and the model cannot drift apart."""
    creator, clip = _creator(), _clip()
    session = _session_mock()
    _overrides(creator, session)
    try:
        with (
            patch("routers.review.get_owned", AsyncMock(return_value=clip)),
            patch("routers.review._enqueue_retrain", AsyncMock()) as retrain,
        ):
            resp = _put(client, clip, "kept")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["triage"] == "kept"
    assert clip.triage == ClipTriage.kept
    rows = _added_feedback(session)
    assert len(rows) == 1 and rows[0].action == FeedbackAction.upvote
    retrain.assert_awaited_once()


def test_triage_back_to_pending_records_a_retraction(client):
    """Sending a clip back to the queue writes `skip`, which wins the training
    partition and then drops out — so the old verdict is withdrawn rather than
    left behind as a stale label."""
    creator, clip = _creator(), _clip(triage=ClipTriage.dropped)
    session = _session_mock()
    _overrides(creator, session)
    try:
        with (
            patch("routers.review.get_owned", AsyncMock(return_value=clip)),
            patch("routers.review._enqueue_retrain", AsyncMock()),
        ):
            resp = _put(client, clip, "pending")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = _added_feedback(session)
    assert len(rows) == 1 and rows[0].action == FeedbackAction.skip


def test_triage_is_idempotent_and_a_repeat_is_a_total_noop(client):
    """THE reason this is a PUT. An unchanged state writes no row, enqueues no
    task and fires no activation event, so a double-click or an offline replay
    is harmless."""
    creator, clip = _creator(), _clip(triage=ClipTriage.kept)
    session = _session_mock()
    _overrides(creator, session)
    try:
        with (
            patch("routers.review.get_owned", AsyncMock(return_value=clip)),
            patch("routers.review._enqueue_retrain", AsyncMock()) as retrain,
        ):
            first = _put(client, clip, "kept")
            second = _put(client, clip, "kept")
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == second.status_code == 200
    assert first.json()["triage"] == second.json()["triage"] == "kept"
    assert _added_feedback(session) == []
    retrain.assert_not_awaited()


def test_triage_rejects_an_unknown_state(client):
    """422, so the API can never accept a value clip_triage_enum would reject."""
    creator, clip = _creator(), _clip()
    session = _session_mock()
    _overrides(creator, session)
    try:
        with patch("routers.review.get_owned", AsyncMock(return_value=clip)):
            resp = _put(client, clip, "archived")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert clip.triage == ClipTriage.pending, "a rejected state must not be written"


def test_triage_rejects_an_unknown_field(client):
    """Pins extra='forbid'. Without it a client sending the wrong key name gets
    a cheerful 200 and a silent no-op."""
    creator, clip = _creator(), _clip()
    session = _session_mock()
    _overrides(creator, session)
    try:
        with patch("routers.review.get_owned", AsyncMock(return_value=clip)):
            resp = _put(client, clip, "kept", body={"state": "kept"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422


def test_triage_404s_for_another_creators_clip(client):
    """Ownership rides on get_owned, which 404s for missing AND foreign alike so
    the response never confirms someone else's clip id exists."""
    from fastapi import HTTPException

    creator, clip = _creator(), _clip()
    session = _session_mock()
    _overrides(creator, session)
    try:
        with patch(
            "routers.review.get_owned",
            AsyncMock(side_effect=HTTPException(status_code=404, detail="Clip not found")),
        ):
            resp = _put(client, clip, "kept")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


def test_first_keep_via_triage_fires_the_activation_event(client):
    """The clip_kept funnel must not die just because the creator now keeps
    clips from the pile board instead of the feedback endpoint."""
    creator, clip = _creator(), _clip()
    session = _session_mock(first_keep=True)
    _overrides(creator, session)
    try:
        with (
            patch("routers.review.get_owned", AsyncMock(return_value=clip)),
            patch("routers.review._enqueue_retrain", AsyncMock()),
            patch("event_log.record_event_nowait") as rec,
        ):
            resp = _put(client, clip, "kept")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # The request-telemetry middleware uses the same helper, so filter by event.
    kept = [c for c in rec.call_args_list if c.kwargs.get("event") == "clip_kept"]
    assert len(kept) == 1


def test_dropping_never_fires_the_activation_event(client):
    creator, clip = _creator(), _clip()
    session = _session_mock(first_keep=True)
    _overrides(creator, session)
    try:
        with (
            patch("routers.review.get_owned", AsyncMock(return_value=clip)),
            patch("routers.review._enqueue_retrain", AsyncMock()),
            patch("event_log.record_event_nowait") as rec,
        ):
            resp = _put(client, clip, "dropped")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert [c for c in rec.call_args_list if c.kwargs.get("event") == "clip_kept"] == []


def test_retrain_is_enqueued_with_a_countdown(client):
    """A burst of triage moves must coalesce into one model fit — the advisory
    lock only drops OVERLAPPING tasks, so without a countdown 20 pile moves
    would mean 20 full LightGBM fits."""
    from config import settings

    creator, clip = _creator(), _clip()
    session = _session_mock()
    _overrides(creator, session)
    try:
        with (
            patch("routers.review.get_owned", AsyncMock(return_value=clip)),
            patch("worker.tasks.retrain_preference") as task,
        ):
            resp = _put(client, clip, "kept")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    task.apply_async.assert_called_once()
    assert task.apply_async.call_args.kwargs["countdown"] == (
        settings.PREFERENCE_RETRAIN_DEBOUNCE_S
    )
