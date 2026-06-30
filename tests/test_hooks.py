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
    assert 5.0 <= drop_at <= 12.0  # interpolation lands somewhere in the descent
    assert ratio_at < 0.75  # below the creator's steady median


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
    # Issue 333: malformed/truncated LLM output is caught and re-raised as a
    # contextful ValueError (graceful-degrade parity with scoring), not a bare
    # JSONDecodeError surfacing as an opaque 500.
    with pytest.raises(ValueError, match="Malformed JSON"):
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


@pytest.fixture(autouse=True)
def _bypass_balance_gate():
    """Issue 228 added a per-creator balance floor on hook analysis. These tests
    assert routing/validation, not billing — neutralize the gate (covered in
    tests/test_creator_quota.py)."""
    with patch("routers.analysis.check_positive_balance", AsyncMock(return_value=None)):
        yield


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


# ── Unit: analyze_hook prompt assembly ────────────────────────────────────────


def test_analyze_hook_builds_prompt_with_drop() -> None:
    """analyze_hook delegates to stream_and_emit with cached DNA + drop stats."""
    from knowledge.hooks import analyze_hook

    fake_stream = MagicMock(
        return_value=(
            '{"diagnosis":"x"}',
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read": 80,
                "cache_creation": 20,
            },
        )
    )
    with patch("worker.anthropic_stream.stream_and_emit", fake_stream):
        result, _usage = analyze_hook(
            channel_title="Test Channel",
            dna_brief="DNA brief text " * 100,
            retention_drop_at_s=12.5,
            retention_at_drop=0.55,
            creator_median_at_drop=0.80,
            transcript_excerpt="hello world",
            task_id="t-1",
        )

    assert result == '{"diagnosis":"x"}'
    assert fake_stream.called
    call_kwargs = fake_stream.call_args.kwargs
    system_blocks = call_kwargs["system"]
    assert len(system_blocks) == 3
    # No cache_control on the DNA block — Issue-135 audit fix: instructions +
    # DNA brief ≈ 900 tokens, below Haiku 4.5's 4096-token cache floor; the
    # marker was inert. See docs/DECISIONS.md.
    assert "cache_control" not in system_blocks[1]
    assert "12.5s" in system_blocks[2]["text"]
    assert "55.0%" in system_blocks[2]["text"]


def test_analyze_hook_no_drop_path() -> None:
    """When drop is None, prompt says no significant drop."""
    from knowledge.hooks import analyze_hook

    fake_stream = MagicMock(
        return_value=(
            "{}",
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read": 0,
                "cache_creation": 0,
            },
        )
    )
    with patch("worker.anthropic_stream.stream_and_emit", fake_stream):
        analyze_hook(
            channel_title="Channel",
            dna_brief=None,
            retention_drop_at_s=None,
            retention_at_drop=None,
            creator_median_at_drop=None,
            transcript_excerpt="",
            task_id="t-2",
        )

    system_blocks = fake_stream.call_args.kwargs["system"]
    assert "No significant retention drop" in system_blocks[2]["text"]
    assert "No DNA profile" in system_blocks[1]["text"]
    assert "No transcript available" in system_blocks[2]["text"]


# ── extract_transcript_excerpt ────────────────────────────────────────────────


def test_extract_transcript_excerpt_filters_by_start() -> None:
    from knowledge.util import extract_transcript_excerpt

    segs = {
        "segments": [
            {"start": 0.0, "text": "first"},
            {"start": 30.0, "text": "second"},
            {"start": 90.0, "text": "third"},
        ]
    }
    result = extract_transcript_excerpt(segs, max_s=60.0)
    assert "first" in result
    assert "second" in result
    assert "third" not in result


def test_extract_transcript_excerpt_empty() -> None:
    from knowledge.util import extract_transcript_excerpt

    assert extract_transcript_excerpt(None, 60.0) == ""
    assert extract_transcript_excerpt({}, 60.0) == ""


def test_get_transcript_segments_empty() -> None:
    from knowledge.util import get_transcript_segments

    assert get_transcript_segments(None) == []
    assert get_transcript_segments({}) == []
    assert get_transcript_segments({"segments": [{"text": "hi"}]}) == [{"text": "hi"}]


# ── Additional unit coverage ──────────────────────────────────────────────────


def test_compute_retention_drop_uses_custom_threshold() -> None:
    """Caller can tighten the threshold to detect smaller drops."""
    video_curve = [(float(t), 0.80 if t < 10 else 0.74) for t in range(0, 31, 5)]
    creator_curves = [
        [(float(t), 0.80) for t in range(0, 31, 5)],
        [(float(t), 0.81) for t in range(0, 31, 5)],
    ]
    # Default threshold (0.10) would NOT trigger; loose threshold does.
    drop_default, _ = compute_retention_drop(video_curve, creator_curves)
    drop_loose, _ = compute_retention_drop(video_curve, creator_curves, threshold=0.03)
    assert drop_default is None
    assert drop_loose is not None


def test_compute_retention_drop_first_point_after_zero() -> None:
    """Curves that start past second 0 get prepended with (0, 1.0) implicit start."""
    video_curve = [(5.0, 0.5), (10.0, 0.4), (15.0, 0.3)]
    creator_curves = [[(5.0, 0.9), (10.0, 0.88), (15.0, 0.87)]]
    drop_at, _ = compute_retention_drop(video_curve, creator_curves)
    assert drop_at is not None  # large diff at second 5 (0.9 - 0.5 = 0.4 > 0.10)


async def test_analyze_hook_async_creator_not_found_returns_early() -> None:
    """When the creator UUID doesn't exist, the task aemits error and returns."""
    import db
    from worker import tasks as worker_tasks

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *_a, **_kw):
            return None  # creator not found

    aemit_mock = AsyncMock()
    with (
        patch.object(db, "AdminSessionLocal", MagicMock(return_value=_FakeSession())),
        patch("worker.progress.aemit", aemit_mock),
    ):
        await worker_tasks._analyze_hook_async("job-x", str(uuid.uuid4()), str(uuid.uuid4()))

    assert aemit_mock.await_count >= 1
    # One of the calls must be the "Creator not found" error
    error_calls = [c for c in aemit_mock.await_args_list if "Creator not found" in str(c)]
    assert error_calls


async def test_analyze_hook_async_video_not_found_returns_early() -> None:
    """When video doesn't exist or belongs to other creator, error + return."""
    import db
    from models import Creator
    from worker import tasks as worker_tasks

    creator = MagicMock(spec=Creator)
    creator.id = uuid.uuid4()

    class _FakeSession:
        def __init__(self):
            self._call = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, _model, _id):
            self._call += 1
            return creator if self._call == 1 else None

    aemit_mock = AsyncMock()
    with (
        patch.object(db, "AdminSessionLocal", MagicMock(return_value=_FakeSession())),
        patch("worker.progress.aemit", aemit_mock),
    ):
        await worker_tasks._analyze_hook_async("job-y", str(creator.id), str(uuid.uuid4()))

    error_calls = [c for c in aemit_mock.await_args_list if "Video not found" in str(c)]
    assert error_calls


def test_parse_hook_report_coerces_string_fields() -> None:
    """Non-string fields are coerced to strings."""
    raw = """{
        "retention_drop_at_s": 10.0,
        "retention_at_drop": 0.5,
        "transcript_at_drop": 12345,
        "diagnosis": "ok",
        "rewrite_suggestion": "ok",
        "honesty_disclaimer": "ok"
    }"""
    result = parse_hook_report(raw)
    assert result["transcript_at_drop"] == "12345"


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
