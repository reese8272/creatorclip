"""Unit tests for the POST /creators/me/insights/analyze-performer endpoint.

Uses mocked DB sessions and a mocked Anthropic client — no real external calls.
The real-DB integration paths for the insights aggregation endpoint live in
tests/test_insights_integration.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, CreatorInsight, InsightType, Video
from routers.insights import _build_analysis_prompt
from tests._helpers import override_current_creator

_URL = "/creators/me/insights/analyze-performer"


def _make_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.channel_id = "UC_test"
    return c


def _make_video(creator_id: uuid.UUID) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.title = "How I built this in 30 days"
    v.youtube_video_id = "dQw4w9WgXcQ"
    v.kind = MagicMock()
    v.kind.value = "tutorial"
    return v


def _make_execute_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.first.return_value = value
    return result


def _make_cached_insight(creator_id: uuid.UUID, video_id: uuid.UUID) -> MagicMock:
    ins = MagicMock(spec=CreatorInsight)
    ins.id = uuid.uuid4()
    ins.video_id = video_id
    ins.insight_type = InsightType.performer_analysis
    ins.title = "Cached insight"
    ins.content = "Cached analysis content."
    ins.dna_version = 1
    ins.is_saved = False
    ins.created_at = datetime.now(UTC)
    return ins


def _fake_session(
    video: object,
    metrics: object = None,
    dna: object = None,
    existing_insight: object = None,
):
    async def _mock_refresh(obj: CreatorInsight) -> None:
        obj.id = uuid.uuid4()
        obj.created_at = datetime.now(UTC)

    async def _gen():
        session = AsyncMock()
        session.get = AsyncMock(return_value=video)
        session.execute = AsyncMock(
            side_effect=[
                _make_execute_result(metrics),
                _make_execute_result(dna),
                _make_execute_result(existing_insight),
            ]
        )
        session.refresh = AsyncMock(side_effect=_mock_refresh)
        yield session

    return _gen


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


# ── Unit: prompt builder ───────────────────────────────────────────────────────


def test_build_analysis_prompt_top_performer() -> None:
    result = _build_analysis_prompt(
        video_title="Test Video",
        kind="tutorial",
        views=10_000,
        engagement_rate=0.08,
        performer_kind="top",
        dna_brief="Creator DNA brief.",
    )
    assert "top performer" in result
    assert "10,000" in result
    assert "8.0%" in result
    assert "Creator DNA summary" in result


def test_build_analysis_prompt_underperformer_no_metrics() -> None:
    result = _build_analysis_prompt(
        video_title="Another Video",
        kind="vlog",
        views=None,
        engagement_rate=None,
        performer_kind="bottom",
        dna_brief=None,
    )
    assert "underperformer" in result
    assert "unknown" in result
    assert "Creator DNA summary" not in result


# ── API endpoint tests ─────────────────────────────────────────────────────────


def test_analyze_performer_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(_URL, json={"video_id": str(uuid.uuid4()), "performer_kind": "top"})
    assert resp.status_code == 401


def test_analyze_performer_invalid_video_id() -> None:
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(video=None)

    with TestClient(app) as client:
        resp = client.post(
            _URL,
            json={"video_id": "not-a-uuid", "performer_kind": "top"},
            cookies={"session": "x"},
        )
    assert resp.status_code == 422


def test_analyze_performer_video_not_found() -> None:
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(video=None)

    with TestClient(app) as client:
        resp = client.post(
            _URL,
            json={"video_id": str(uuid.uuid4()), "performer_kind": "top"},
            cookies={"session": "x"},
        )
    assert resp.status_code == 404


def test_analyze_performer_cache_hit_skips_llm() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)
    cached = _make_cached_insight(creator.id, video.id)

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(video=video, existing_insight=cached)

    with (
        patch("routers.insights._ANTHROPIC") as mock_anthropic,
        TestClient(app) as client,
    ):
        resp = client.post(
            _URL,
            json={"video_id": str(video.id), "performer_kind": "top"},
            cookies={"session": "x"},
        )

    assert resp.status_code == 200
    assert resp.json()["content"] == "Cached analysis content."
    mock_anthropic.messages.create.assert_not_called()


def test_analyze_performer_llm_success_creates_insight() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(video=video)

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Strong hooks drove retention above channel average.")]
    mock_msg.usage.input_tokens = 150
    mock_msg.usage.output_tokens = 60

    with (
        patch("routers.insights._ANTHROPIC") as mock_anthropic,
        TestClient(app) as client,
    ):
        mock_anthropic.messages.create.return_value = mock_msg
        resp = client.post(
            _URL,
            json={"video_id": str(video.id), "performer_kind": "top"},
            cookies={"session": "x"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "Strong hooks drove retention above channel average."
    assert data["is_saved"] is False
    assert data["insight_type"] == "performer_analysis"


def test_analyze_performer_no_inert_cache_marker() -> None:
    """Issue 140 + 218: the analyze-performer system prefix (~30 tokens) is below
    Haiku 4.5's 4096-token cacheable-prefix floor, so a cache_control marker would
    be inert (pays the 1.25x write premium for zero reads). Assert none is sent.
    Note: other brief endpoints (titles/thumbnails/analysis) DO carry cache markers
    via the DNA brief block where the combined prefix clears the 1024-token floor
    for Sonnet 4.6. (Issue 218 — Sonnet 4.6 floor confirmed 1024, not 2048)."""
    creator = _make_creator()
    video = _make_video(creator.id)

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(video=video)

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 20

    with (
        patch("routers.insights._ANTHROPIC") as mock_anthropic,
        TestClient(app) as client,
    ):
        mock_anthropic.messages.create.return_value = mock_msg
        client.post(
            _URL,
            json={"video_id": str(video.id), "performer_kind": "top"},
            cookies={"session": "x"},
        )

    system = mock_anthropic.messages.create.call_args.kwargs["system"]
    assert all("cache_control" not in block for block in system)


def test_analyze_performer_empty_llm_content_returns_fallback() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(video=video)

    mock_msg = MagicMock()
    mock_msg.content = []  # empty → fallback text
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 0

    with (
        patch("routers.insights._ANTHROPIC") as mock_anthropic,
        TestClient(app) as client,
    ):
        mock_anthropic.messages.create.return_value = mock_msg
        resp = client.post(
            _URL,
            json={"video_id": str(video.id), "performer_kind": "top"},
            cookies={"session": "x"},
        )

    assert resp.status_code == 200
    assert resp.json()["content"] == "Analysis unavailable."


def test_analyze_performer_llm_error_returns_503() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(video=video)

    with (
        patch("routers.insights._ANTHROPIC") as mock_anthropic,
        TestClient(app) as client,
    ):
        mock_anthropic.messages.create.side_effect = Exception("upstream API error")
        resp = client.post(
            _URL,
            json={"video_id": str(video.id), "performer_kind": "top"},
            cookies={"session": "x"},
        )

    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()
