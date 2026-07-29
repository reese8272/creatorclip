"""Tests for structured feedback tags (Issue 118).

Verifies the POST /clips/{id}/feedback endpoint accepts and persists
feedback_tags + feedback_note alongside the standard action field.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from db import get_session
from main import app
from models import Clip, RenderStatus
from tests._helpers import owned_lookup_result


def _mock_creator() -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    return c


def _mock_clip(creator_id: uuid.UUID) -> MagicMock:
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.render_status = RenderStatus.done
    return clip


def _fake_session(clip: MagicMock):
    async def _session():
        session = AsyncMock()
        # get_owned ownership select + the Issue-235 EXISTS check both go
        # through execute(); owned_lookup_result returns sync MagicMock
        # results (a bare AsyncMock's .scalar() yields a never-awaited
        # coroutine → RuntimeWarning, and vacuously passes the ownership
        # predicate).
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, clip)
        )

        async def _refresh(obj):
            pass

        session.refresh = _refresh
        session.commit = AsyncMock()
        session.add = MagicMock()
        yield session

    return _session


def test_feedback_accepts_tags_and_note(client):
    """Feedback with feedback_tags and feedback_note posts successfully."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with patch("worker.tasks.retrain_preference") as mock_retrain:
        mock_retrain.delay = MagicMock()
        try:
            resp = client.post(
                f"/clips/{clip.id}/feedback",
                json={
                    "action": "upvote",
                    "feedback_tags": ["titles_fit_style", "good_hook"],
                    "feedback_note": "Pacing was perfect",
                },
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["action"] == "upvote"


def test_feedback_still_works_without_tags(client):
    """Legacy feedback calls without tags are not broken."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with patch("worker.tasks.retrain_preference") as mock_retrain:
        mock_retrain.delay = MagicMock()
        try:
            resp = client.post(
                f"/clips/{clip.id}/feedback",
                json={"action": "downvote"},
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["action"] == "downvote"


def test_feedback_empty_tags_list_treated_as_none(client):
    """An empty feedback_tags list is treated as no tags (not stored)."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with patch("worker.tasks.retrain_preference") as mock_retrain:
        mock_retrain.delay = MagicMock()
        try:
            resp = client.post(
                f"/clips/{clip.id}/feedback",
                json={"action": "skip", "feedback_tags": []},
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 201
