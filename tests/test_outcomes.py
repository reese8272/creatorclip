"""
Unit tests for Issue 13 — clip outcomes loop.

Covers:
- YouTube stats helper (get_video_stats)
- performed_well logic (above/below channel median)
- Beat schedule registration
- 3× sample weight multiplier for performed_well clips (via preference/decay)
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from preference.decay import sample_weight

# ── YouTube stats helper ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_video_stats_returns_views():
    mock_resp = {"items": [{"statistics": {"viewCount": "12345"}}]}
    with patch("youtube.data_api._get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        from youtube.data_api import get_video_stats

        result = await get_video_stats("tok", "abc123")
    assert result["views"] == 12345


@pytest.mark.asyncio
async def test_get_video_stats_empty_when_not_found():
    with patch("youtube.data_api._get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"items": []}
        from youtube.data_api import get_video_stats

        result = await get_video_stats("tok", "missing")
    assert result == {}


# ── performed_well logic ──────────────────────────────────────────────────────


def _outcome(views: int, median: int) -> bool:
    return views >= median


def test_performed_well_true_when_above_median():
    assert _outcome(10000, 5000) is True


def test_performed_well_false_when_below_median():
    assert _outcome(1000, 5000) is False


def test_performed_well_true_at_median():
    assert _outcome(5000, 5000) is True


# ── 3× sample weight for performed_well clips ─────────────────────────────────


def test_sample_weight_multiplied_for_performed_well():
    now = datetime.now(UTC)
    w_positive = sample_weight(now, performed_well=True)
    w_neutral = sample_weight(now, performed_well=None)
    assert w_positive == pytest.approx(w_neutral * 3.0)


def test_sample_weight_not_multiplied_when_false():
    now = datetime.now(UTC)
    w_false = sample_weight(now, performed_well=False)
    w_neutral = sample_weight(now, performed_well=None)
    assert w_false == pytest.approx(w_neutral)


# ── Beat schedule registration ────────────────────────────────────────────────


def test_beat_schedule_has_poll_clip_outcomes():
    from worker.schedule import celery

    assert "poll-clip-outcomes-hourly" in celery.conf.beat_schedule


def test_beat_schedule_task_name():
    from worker.schedule import celery

    entry = celery.conf.beat_schedule["poll-clip-outcomes-hourly"]
    assert entry["task"] == "worker.tasks.poll_clip_outcomes"


def test_beat_schedule_interval_is_one_hour():
    from celery.schedules import timedelta as celery_td

    from worker.schedule import celery

    entry = celery.conf.beat_schedule["poll-clip-outcomes-hourly"]
    assert entry["schedule"] == celery_td(hours=1)


# ── Poll task: 48h / 7d cutoff filtering ──────────────────────────────────────


def _make_outcome(views=None, performed_well=None, hours_old=50):
    o = MagicMock()
    o.published_youtube_id = "ytid123"
    o.views = views
    o.performed_well = performed_well
    o.fetched_at = datetime.now(UTC) - timedelta(hours=hours_old)
    o.clip_id = uuid.uuid4()
    return o


def test_48h_cutoff_candidate_qualifies():
    o = _make_outcome(performed_well=None, hours_old=50)
    cutoff_48h = datetime.now(UTC) - timedelta(hours=48)
    assert o.performed_well is None and o.fetched_at < cutoff_48h


def test_recent_outcome_does_not_qualify_for_48h():
    o = _make_outcome(performed_well=None, hours_old=10)
    cutoff_48h = datetime.now(UTC) - timedelta(hours=48)
    assert not (o.performed_well is None and o.fetched_at < cutoff_48h)


def test_7d_cutoff_triggers_recheck():
    o = _make_outcome(performed_well=True, hours_old=200)  # >8 days
    cutoff_7d = datetime.now(UTC) - timedelta(days=7)
    assert o.fetched_at < cutoff_7d
