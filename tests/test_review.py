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
from tests._helpers import override_current_creator


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
        session.get = AsyncMock(side_effect=lambda model, pk: clip if model is Clip else None)
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

    resp = client.post(
        f"/clips/{clip.id}/feedback",
        json={"action": "trim", "trim_start_s": 5.0, "trim_end_s": 55.0},
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
        session.get = AsyncMock(return_value=clip)  # returns the clip but wrong creator
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
        session.get = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(f"/clips/{uuid.uuid4()}/feedback", json={"action": "upvote"})
    assert resp.status_code == 404
