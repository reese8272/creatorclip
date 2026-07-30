"""Tests for the video-level style review endpoints (Issue 370)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, Video, VideoFeedback, VideoSentiment
from tests._helpers import owned_lookup_result


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id) -> MagicMock:
    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.creator_id = creator_id
    return video


def _feedback_row(creator_id, video_id, **over) -> MagicMock:
    row = MagicMock(spec=VideoFeedback)
    row.id = uuid.uuid4()
    row.creator_id = creator_id
    row.video_id = video_id
    row.sentiment = over.get("sentiment", VideoSentiment.like)
    row.feedback_tags = over.get("feedback_tags", ["pacing_feels_right"])
    row.feedback_note = over.get("feedback_note")
    row.created_at = over.get("created_at", datetime.now(UTC))
    return row


def _overrides(creator, video, *, list_rows=None):
    added = []

    async def _session():
        session = AsyncMock()

        def _execute(stmt, *a, **kw):
            desc = str(stmt)
            if "video_feedback" in desc:
                result = MagicMock()
                result.scalars.return_value = iter(list_rows or [])
                return result
            return owned_lookup_result(stmt, video)

        session.execute = AsyncMock(side_effect=_execute)
        session.add = MagicMock(side_effect=added.append)

        async def _refresh(obj):
            obj.id = obj.id or uuid.uuid4()
            obj.created_at = obj.created_at or datetime.now(UTC)

        session.refresh = AsyncMock(side_effect=_refresh)
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    return added


def _post(client, video_id, body):
    return client.post(f"/videos/{video_id}/feedback", json=body, cookies={"session": "x"})


def test_submit_video_feedback_happy_path(client):
    creator = _creator()
    video = _video(creator.id)
    added = _overrides(creator, video)
    try:
        resp = _post(
            client,
            video.id,
            {
                "sentiment": "like",
                "feedback_tags": ["pacing_feels_right", "tone_on_brand"],
                "feedback_note": "The cold-open pacing is exactly my style.",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201
    body = resp.json()
    assert body["sentiment"] == "like"
    assert body["feedback_tags"] == ["pacing_feels_right", "tone_on_brand"]
    assert len(added) == 1
    assert added[0].sentiment == VideoSentiment.like


def test_submit_video_feedback_cross_creator_404(client):
    creator = _creator()
    video = _video(uuid.uuid4())  # foreign
    _overrides(creator, video)
    try:
        resp = _post(client, video.id, {"sentiment": "like", "feedback_tags": ["pacing_off"]})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"sentiment": "meh", "feedback_tags": ["pacing_off"]},  # bad sentiment
        {"sentiment": "like"},  # bare valence — no tags, no note
        {"sentiment": "like", "feedback_note": "   "},  # whitespace-only note
        {"sentiment": "dislike", "feedback_tags": [f"t{i}" for i in range(11)]},  # 11 tags
        {"sentiment": "dislike", "feedback_tags": ["x" * 65]},  # tag too long
        {"sentiment": "like", "feedback_note": "x" * 2001},  # note over cap
    ],
)
def test_submit_video_feedback_invalid_422(client, body):
    creator = _creator()
    video = _video(creator.id)
    _overrides(creator, video)
    try:
        resp = _post(client, video.id, body)
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422


def test_list_video_feedback_returns_own_rows(client):
    creator = _creator()
    video = _video(creator.id)
    rows = [
        _feedback_row(creator.id, video.id, feedback_note="love the intro"),
        _feedback_row(
            creator.id, video.id, sentiment=VideoSentiment.dislike, feedback_tags=["pacing_off"]
        ),
    ]
    _overrides(creator, video, list_rows=rows)
    try:
        resp = client.get(f"/videos/{video.id}/feedback", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert items[0]["feedback_note"] == "love the intro"
    assert items[1]["sentiment"] == "dislike"
