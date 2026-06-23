"""Tests for the video analysis feature (Issue 121)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from analysis.brief import _build_request
from auth import get_current_creator
from db import get_session
from main import app
from models import Creator
from routers.analysis import _extract_video_id
from tests._helpers import override_current_creator

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_creator(*, channel_id: str | None = "UC_test_channel") -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.channel_id = channel_id
    c.channel_title = "Test Channel"
    c.email = "test@example.com"
    return c


def _fake_session(scalar_return=None):
    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=scalar_return)
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        yield session

    return _gen


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


# ── Unit: URL extraction ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
        ("not-a-url", None),
        ("", None),
        ("tooshort", None),
    ],
)
def test_extract_video_id(raw: str, expected: str | None) -> None:
    assert _extract_video_id(raw) == expected


# ── Unit: analysis brief prompt structure ─────────────────────────────────────


def test_build_request_minimal() -> None:
    system, messages = _build_request(
        channel_title="Test Channel",
        youtube_video_id="dQw4w9WgXcQ",
        video_title=None,
        query="Why did this flop?",
        video_metrics=None,
        retention_summary=None,
        channel_avg=None,
        dna_brief=None,
    )
    # Two system blocks: static instructions + per-video data block.
    # No cache_control breakpoint — see DECISIONS (Issue-135 audit fix):
    # static prefix is below Sonnet 4.6's 1024-token cache floor.
    assert len(system) == 2
    assert "cache_control" not in system[0]
    assert "CREATOR AND VIDEO DATA:" in system[1]["text"]
    assert "dQw4w9WgXcQ" in system[1]["text"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Why did this flop?"


def test_build_request_includes_metrics_and_dna() -> None:
    """When dna_brief is provided, 3 system blocks are built (Issue 218):
    Block 0: static instructions, Block 1: DNA (cache_control), Block 2: per-video data.
    """
    system, _ = _build_request(
        channel_title="Backboard Media",
        youtube_video_id="abc12345678",
        video_title="My Best Short",
        query="What made this take off?",
        video_metrics={"views": 50000, "engagement_rate": 0.08},
        retention_summary={"at_25pct": 0.82, "at_50pct": 0.61},
        channel_avg={"avg_views": 12000, "sample_size": 30},
        dna_brief="Creator makes basketball content.",
    )
    # 3 blocks when DNA brief present; DNA in block 1 with cache_control, data in block 2.
    assert len(system) == 3
    assert "basketball" in system[1]["text"]
    assert system[1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    data_block = system[2]["text"]
    assert "50000" in data_block
    assert "at_25pct" in data_block
    assert "12000" in data_block


def test_dna_brief_capped_at_1000_chars() -> None:
    long_dna = "x" * 2000
    system, _ = _build_request(
        channel_title="Ch",
        youtube_video_id="aaaaaaaaaaa",
        video_title=None,
        query="Why?",
        video_metrics=None,
        retention_summary=None,
        channel_avg=None,
        dna_brief=long_dna,
    )
    # 1001+ consecutive x's would exceed the cap
    assert "x" * 1001 not in system[1]["text"]


# ── API: endpoint guards ───────────────────────────────────────────────────────


def test_analysis_rejects_invalid_url() -> None:
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(scalar_return=None)

    with TestClient(app) as c:
        resp = c.post(
            "/creators/me/video-analysis",
            json={"youtube_url": "not-a-valid-url", "query": "why?"},
        )
    assert resp.status_code == 422
    assert "YouTube video ID" in resp.json()["detail"]


def test_analysis_rejects_empty_query() -> None:
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(scalar_return=None)

    with TestClient(app) as c:
        resp = c.post(
            "/creators/me/video-analysis",
            json={"youtube_url": "dQw4w9WgXcQ", "query": ""},
        )
    assert resp.status_code == 422


def test_analysis_no_channel_returns_400() -> None:
    creator = _make_creator(channel_id=None)
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session()

    with TestClient(app) as c:
        resp = c.post(
            "/creators/me/video-analysis",
            json={"youtube_url": "dQw4w9WgXcQ", "query": "Why?"},
        )
    assert resp.status_code == 400
    assert "Channel not connected" in resp.json()["detail"]


def test_analysis_queues_task_returns_202() -> None:
    creator = _make_creator()
    # scalar() returns None twice: once for video lookup, once for metrics
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(scalar_return=None)

    fake_task = MagicMock()
    fake_task.id = "task-abc-123"

    with (
        TestClient(app) as c,
        patch("worker.tasks.generate_video_analysis") as mock_task,
        patch("worker.progress.aset_owner", new_callable=AsyncMock),
    ):
        mock_task.delay.return_value = fake_task
        resp = c.post(
            "/creators/me/video-analysis",
            json={"youtube_url": "dQw4w9WgXcQ", "query": "Why did this perform well?"},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "task-abc-123"
    assert body["stream_url"] == "/tasks/task-abc-123/events"
    assert body["has_metrics"] is False
    assert body["video_title"] is None


def test_analysis_stream_url_none_on_redis_failure() -> None:
    """If aset_owner raises RedisError, stream_url is None (fail-open)."""
    import redis as redis_pkg

    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(scalar_return=None)

    fake_task = MagicMock()
    fake_task.id = "task-xyz-999"

    with (
        TestClient(app) as c,
        patch("worker.tasks.generate_video_analysis") as mock_task,
        patch(
            "worker.progress.aset_owner",
            new_callable=AsyncMock,
            side_effect=redis_pkg.RedisError("down"),
        ),
    ):
        mock_task.delay.return_value = fake_task
        resp = c.post(
            "/creators/me/video-analysis",
            json={"youtube_url": "dQw4w9WgXcQ", "query": "What happened?"},
        )

    assert resp.status_code == 202
    assert resp.json()["stream_url"] is None
