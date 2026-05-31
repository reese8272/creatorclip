"""
Unit tests for youtube/data_api.py and youtube/analytics.py.

Tests cover: ISO 8601 duration parsing (boundary values), video kind classification,
Analytics report parsing, and the metric/retention/activity conversion math.
No DB, no network — all external calls are patched.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from models import VideoKind
from youtube.analytics import (
    _parse_report,
    fetch_audience_activity,
    fetch_retention_curve,
    fetch_video_metrics,
)
from youtube.data_api import (
    classify_video_kind,
    get_uploads_playlist_id,
    get_videos_metadata,
    parse_duration_seconds,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ── parse_duration_seconds ────────────────────────────────────────────────────


def test_parse_duration_full_fields():
    assert parse_duration_seconds("PT1H30M15S") == 5415.0


def test_parse_duration_minutes_only():
    assert parse_duration_seconds("PT5M") == 300.0


def test_parse_duration_seconds_only():
    assert parse_duration_seconds("PT30S") == 30.0


def test_parse_duration_zero():
    assert parse_duration_seconds("PT0S") == 0.0


def test_parse_duration_days():
    assert parse_duration_seconds("P1DT0H") == 86400.0


def test_parse_duration_invalid_returns_zero():
    assert parse_duration_seconds("not_a_duration") == 0.0


# ── classify_video_kind ───────────────────────────────────────────────────────
# Issue 87: threshold raised to 180s to match YouTube's 2024 Shorts max.
# The four boundary tests below are the load-bearing classification contract.


def test_classify_short_at_new_180s_boundary():
    # Exactly 180s is still a Short — YouTube treats the limit as inclusive.
    assert classify_video_kind(180.0) == VideoKind.short


def test_classify_long_just_above_180s():
    assert classify_video_kind(180.5) == VideoKind.long


def test_classify_short_well_under_threshold():
    # 60s (the old threshold) must STILL be a Short under the new rules.
    assert classify_video_kind(60.0) == VideoKind.short


def test_classify_long_for_typical_long_form():
    # 10-minute video — the canonical "above 10 minutes" case from Issue 87.
    assert classify_video_kind(600.0) == VideoKind.long


# ── _parse_report ─────────────────────────────────────────────────────────────


def test_parse_report_produces_dicts():
    response = load("yt_video_metrics.json")
    rows = _parse_report(response)
    assert len(rows) == 1
    assert rows[0]["views"] == 5000
    assert rows[0]["averageViewPercentage"] == pytest.approx(40.2)


def test_parse_report_empty_rows_returns_empty_list():
    response = {"columnHeaders": [{"name": "views"}], "rows": []}
    assert _parse_report(response) == []


def test_parse_report_null_rows_returns_empty_list():
    response = {"columnHeaders": [{"name": "views"}], "rows": None}
    assert _parse_report(response) == []


# ── fetch_video_metrics ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_video_metrics_engagement_rate_conversion():
    fixture = load("yt_video_metrics.json")
    with patch("youtube.analytics._fetch_report", new=AsyncMock(return_value=fixture)):
        result = await fetch_video_metrics("tok", "video_long_1", "UC_test")
    assert result is not None
    # averageViewPercentage=40.2 → engagement_rate=0.402
    assert result["engagement_rate"] == pytest.approx(0.402)
    assert result["views"] == 5000
    # estimatedMinutesWatched=25000 → watch_time_s=1_500_000
    assert result["watch_time_s"] == 1_500_000


@pytest.mark.asyncio
async def test_fetch_video_metrics_no_data_returns_none():
    empty = {"columnHeaders": [], "rows": []}
    with patch("youtube.analytics._fetch_report", new=AsyncMock(return_value=empty)):
        result = await fetch_video_metrics("tok", "missing_video", "UC_test")
    assert result is None


# ── fetch_retention_curve ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_retention_curve_converts_ratio_to_seconds():
    fixture = load("yt_retention_curve.json")
    duration_s = 754.0  # PT12M34S
    with patch("youtube.analytics._fetch_report", new=AsyncMock(return_value=fixture)):
        points = await fetch_retention_curve("tok", "video_long_1", "UC_test", duration_s)
    assert len(points) == 6
    # ratio=0.0 → timestamp_s=0.0
    assert points[0]["timestamp_s"] == pytest.approx(0.0)
    # ratio=0.5 → timestamp_s=377.0
    assert points[3]["timestamp_s"] == pytest.approx(0.5 * duration_s)
    # ratio=1.0 → timestamp_s=duration_s
    assert points[5]["timestamp_s"] == pytest.approx(duration_s)


@pytest.mark.asyncio
async def test_fetch_retention_curve_preserves_watch_ratios():
    fixture = load("yt_retention_curve.json")
    with patch("youtube.analytics._fetch_report", new=AsyncMock(return_value=fixture)):
        points = await fetch_retention_curve("tok", "vid", "UC_test", 100.0)
    assert points[0]["audience_watch_ratio"] == pytest.approx(1.0)
    assert points[1]["audience_watch_ratio"] == pytest.approx(0.85)


# ── fetch_audience_activity ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_audience_activity_max_day_is_one():
    fixture = load("yt_audience_activity.json")
    with patch("youtube.analytics._fetch_report", new=AsyncMock(return_value=fixture)):
        rows = await fetch_audience_activity("tok", "UC_test")
    # Monday (2026-01-05, weekday=0 → dow=1) has 1200 views = max
    max_row = max(rows, key=lambda r: r["activity_index"])
    assert max_row["activity_index"] == pytest.approx(1.0)
    assert max_row["day_of_week"] == 1  # Monday in Sunday-anchored scheme


@pytest.mark.asyncio
async def test_fetch_audience_activity_hour_stubbed():
    fixture = load("yt_audience_activity.json")
    with patch("youtube.analytics._fetch_report", new=AsyncMock(return_value=fixture)):
        rows = await fetch_audience_activity("tok", "UC_test")
    assert all(r["hour"] == 12 for r in rows)


@pytest.mark.asyncio
async def test_fetch_audience_activity_returns_seven_days():
    fixture = load("yt_audience_activity.json")
    with patch("youtube.analytics._fetch_report", new=AsyncMock(return_value=fixture)):
        rows = await fetch_audience_activity("tok", "UC_test")
    assert len(rows) == 7


# ── get_uploads_playlist_id ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_uploads_playlist_id_extracts_id():
    fixture = load("yt_channel_content_details.json")
    with patch("youtube.data_api._get_json", new=AsyncMock(return_value=fixture)):
        playlist_id = await get_uploads_playlist_id("tok")
    assert playlist_id == "UU_test_channel"


@pytest.mark.asyncio
async def test_get_uploads_playlist_id_raises_on_empty():
    with (
        patch("youtube.data_api._get_json", new=AsyncMock(return_value={"items": []})),
        pytest.raises(ValueError),
    ):
        await get_uploads_playlist_id("tok")


# ── get_videos_metadata ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_videos_metadata_parses_duration_and_kind():
    fixture = load("yt_videos_metadata.json")
    with patch("youtube.data_api._get_json", new=AsyncMock(return_value=fixture)):
        results = await get_videos_metadata("tok", ["video_long_1", "video_short_1"])
    by_id = {r["video_id"]: r for r in results}
    # PT12M34S = 754s → long
    assert by_id["video_long_1"]["duration_s"] == pytest.approx(754.0)
    assert by_id["video_long_1"]["kind"] == VideoKind.long
    # PT58S = 58s → short
    assert by_id["video_short_1"]["duration_s"] == pytest.approx(58.0)
    assert by_id["video_short_1"]["kind"] == VideoKind.short


@pytest.mark.asyncio
async def test_get_videos_metadata_empty_ids_returns_empty():
    result = await get_videos_metadata("tok", [])
    assert result == []


@pytest.mark.asyncio
async def test_fetch_report_honors_retry_after_on_429():
    """Issue A: a 429 with Retry-After must back off at least that long, not the 1s base."""
    from unittest.mock import MagicMock

    import youtube.analytics as a

    resp429 = MagicMock(status_code=429, headers={"Retry-After": "7"})
    resp200 = MagicMock(status_code=200, headers={})
    resp200.json.return_value = {"rows": []}
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[resp429, resp200])

    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    with (
        patch("youtube.analytics._http.client", return_value=mock_client),
        patch("youtube.analytics.consume", new=AsyncMock()),
        patch("youtube.analytics._classify_error", return_value=("rateLimitExceeded", True)),
        patch("asyncio.sleep", new=fake_sleep),
    ):
        result = await a._fetch_report("tok", {})

    assert result == {"rows": []}
    assert slept and slept[0] >= 7  # honored Retry-After, not the ~1s exponential base
