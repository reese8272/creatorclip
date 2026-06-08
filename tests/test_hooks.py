"""Tests for the hook analyzer feature (Issue 130)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from knowledge.hooks import compute_retention_drop, parse_hook_report
from main import app
from models import Creator, Video
from tests._helpers import override_current_creator

_HOOK_URL = "/creators/me/videos/{video_id}/hook-analysis"


# ── Unit: retention math ───────────────────────────────────────────────────────


def test_compute_retention_drop_detects_drop() -> None:
    """Video that plummets at second 10 while creator median is steady."""
    # Creator median: holds around 0.80 for 30s
    video_curve = [(float(t), 0.80 if t < 10 else 0.55) for t in range(0, 31, 5)]
    creator_curves = [
        [(float(t), 0.80) for t in range(0, 31, 5)],
        [(float(t), 0.82) for t in range(0, 31, 5)],
    ]
    drop_at, ratio_at = compute_retention_drop(video_curve, creator_curves)
    assert drop_at is not None
    assert ratio_at is not None
    assert 8.0 <= drop_at <= 12.0  # drop starts around second 10
    assert ratio_at < 0.70  # well below median


def test_compute_retention_drop_no_significant_drop() -> None:
    """Video tracks close to creator median — no significant drop."""
    curve = [(float(t), 0.80 - t * 0.002) for t in range(0, 31, 5)]
    creator_curves = [
        [(float(t), 0.80 - t * 0.002) for t in range(0, 31, 5)],
        [(float(t), 0.81 - t * 0.002) for t in range(0, 31, 5)],
    ]
    drop_at, ratio_at = compute_retention_drop(curve, creator_curves)
    assert drop_at is None
    assert ratio_at is None


def test_compute_retention_drop_empty_inputs() -> None:
    assert compute_retention_drop([], []) == (None, None)
    assert compute_retention_drop([(5.0, 0.8)], []) == (None, None)
    assert compute_retention_drop([], [[(5.0, 0.8)]]) == (None, None)


def test_compute_retention_drop_insufficient_video_points() -> None:
    """Single video curve point — not enough to interpolate."""
    drop_at, _ = compute_retention_drop(
        [(5.0, 0.6)],
        [[(t, 0.85) for t in range(0, 31, 3)]],
    )
    assert drop_at is None


# ── Unit: parse_hook_report ────────────────────────────────────────────────────


def test_parse_hook_report_valid() -> None:
    raw = """{
        "retention_drop_at_s": 12.5,
        "retention_at_drop": 0.62,
        "transcript_at_drop": "And today we're talking about...",
        "diagnosis": "Retention drops at 12.5s.",
        "rewrite_suggestion": "Consider opening with...",
        "honesty_disclaimer": "This analysis is grounded in your channel's retention data."
    }"""
    result = parse_hook_report(raw)
    assert result["retention_drop_at_s"] == 12.5
    assert result["retention_at_drop"] == 0.62
    assert result["diagnosis"] == "Retention drops at 12.5s."


def test_parse_hook_report_null_drop() -> None:
    raw = """{
        "retention_drop_at_s": null,
        "retention_at_drop": null,
        "transcript_at_drop": "",
        "diagnosis": "No significant drop detected.",
        "rewrite_suggestion": "Your hook is already strong.",
        "honesty_disclaimer": "This is an estimate."
    }"""
    result = parse_hook_report(raw)
    assert result["retention_drop_at_s"] is None


def test_parse_hook_report_missing_fields() -> None:
    with pytest.raises(ValueError, match="missing fields"):
        parse_hook_report('{"retention_drop_at_s": 5.0}')


def test_parse_hook_report_invalid_json() -> None:
    import json

    with pytest.raises(json.JSONDecodeError):
        parse_hook_report("not json")


# ── Unit: format_timestamp (borrowed from chapters, but hooks displays it) ────


def test_format_timestamp_seconds_only() -> None:
    from knowledge.chapters import format_timestamp

    assert format_timestamp(0) == "0:00"
    assert format_timestamp(63) == "1:03"
    assert format_timestamp(3661) == "1:01:01"


# ── API endpoint tests ─────────────────────────────────────────────────────────


def _make_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.channel_id = "UC_test"
    c.channel_title = "Test Channel"
    return c


def _make_video(creator_id: uuid.UUID) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.title = "Test Video"
    return v


def _fake_session(video: object, curve_count: int = 0):
    async def _gen():
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = video
        session.scalar = AsyncMock(side_effect=[video, curve_count])
        yield session

    return _gen


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_hook_analysis_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(_HOOK_URL.format(video_id=str(uuid.uuid4())))
    assert resp.status_code == 401


def test_hook_analysis_invalid_video_id() -> None:
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(None)
    with TestClient(app) as client:
        resp = client.post(_HOOK_URL.format(video_id="not-a-uuid"), cookies={"session": "x"})
    assert resp.status_code == 422


def test_hook_analysis_video_not_found() -> None:
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session(None)
    with TestClient(app) as client:
        resp = client.post(_HOOK_URL.format(video_id=str(uuid.uuid4())), cookies={"session": "x"})
    assert resp.status_code == 404


def test_hook_analysis_no_retention_data() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[video, 0])  # 0 curve rows
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _gen

    with TestClient(app) as client:
        resp = client.post(_HOOK_URL.format(video_id=str(video.id)), cookies={"session": "x"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_data"
    assert "message" in data


def test_hook_analysis_queued_when_data_exists() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[video, 5])  # 5 curve rows
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _gen

    fake_task = MagicMock()
    fake_task.id = "fake-hook-task-id"

    with (
        patch("worker.tasks.analyze_hook", **{"delay.return_value": fake_task}),
        patch("worker.progress.aset_owner", new=AsyncMock()),
        TestClient(app) as client,
    ):
        resp = client.post(_HOOK_URL.format(video_id=str(video.id)), cookies={"session": "x"})

    assert resp.status_code == 202
    data = resp.json()
    assert data["task_id"] == "fake-hook-task-id"
    assert data["stream_url"] == f"/tasks/{fake_task.id}/events"
