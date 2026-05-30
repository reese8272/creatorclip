"""
Unit tests for youtube/quota.py.

All Redis calls are patched — no live Redis required.
"""

from unittest.mock import AsyncMock, patch

import pytest

from youtube.quota import (
    COST_ANALYTICS_REPORT,
    COST_DATA_CAPTIONS,
    COST_DATA_VIDEOS,
    QuotaExhaustedError,
    _quota_key,
    consume,
    remaining,
)


def _make_redis_mock(eval_return: int = 500, get_return: str | None = "500") -> AsyncMock:
    """Return a mock async Redis client that eval() and get() return the given values."""
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=eval_return)
    mock_redis.get = AsyncMock(return_value=get_return)
    return mock_redis


@pytest.mark.asyncio
async def test_consume_success_does_not_raise():
    mock_redis = _make_redis_mock(eval_return=100)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        await consume(COST_ANALYTICS_REPORT)
    mock_redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_consume_exhausted_raises_quota_error():
    mock_redis = _make_redis_mock(eval_return=-1)
    with (
        patch("youtube.quota.get_redis_client", return_value=mock_redis),
        pytest.raises(QuotaExhaustedError),
    ):
        await consume(COST_ANALYTICS_REPORT)


@pytest.mark.asyncio
async def test_consume_captions_cost_passed_to_lua():
    mock_redis = _make_redis_mock(eval_return=50)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        await consume(COST_DATA_CAPTIONS)
    call_args = mock_redis.eval.call_args
    cost_arg = int(call_args.args[3])
    assert cost_arg == COST_DATA_CAPTIONS


@pytest.mark.asyncio
async def test_remaining_calculates_from_redis():
    mock_redis = _make_redis_mock(get_return="3000")
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        from config import settings

        left = await remaining()
    assert left == settings.YOUTUBE_QUOTA_DAILY_UNITS - 3000


@pytest.mark.asyncio
async def test_remaining_when_no_key_returns_full_budget():
    mock_redis = _make_redis_mock(get_return=None)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        from config import settings

        left = await remaining()
    assert left == settings.YOUTUBE_QUOTA_DAILY_UNITS


@pytest.mark.asyncio
async def test_remaining_never_negative():
    mock_redis = _make_redis_mock(get_return="99999")
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        left = await remaining()
    assert left == 0


def test_quota_key_contains_today():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    key = _quota_key()
    # Keyed by the Pacific date (Google's quota reset zone), not UTC. (Issue 76)
    today_pt = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    assert today_pt in key
    assert key.startswith("creatorclip:yt_quota:")


def test_cost_constants_are_correct():
    assert COST_ANALYTICS_REPORT == 1
    assert COST_DATA_VIDEOS == 1
    assert COST_DATA_CAPTIONS == 50


# ── Issue 45: singleton tests ─────────────────────────────────────────────────


def test_quota_redis_singleton():
    """Two get_redis_client() calls must return the exact same client instance."""
    import youtube._redis as redis_module

    # Reset the singleton so the test is deterministic regardless of import order.
    original = redis_module._REDIS_CLIENT
    redis_module._REDIS_CLIENT = None
    try:
        from youtube._redis import get_redis_client

        client_a = get_redis_client()
        client_b = get_redis_client()
        assert client_a is client_b, "get_redis_client() must return the same instance each call"
    finally:
        # Restore original state so other tests in the suite are unaffected.
        redis_module._REDIS_CLIENT = original


# ── Issue 55: consume raises (does not silent-allow) when Redis is unreachable ─


@pytest.mark.asyncio
async def test_consume_raises_when_redis_unreachable():
    """consume() must propagate (not swallow) a Redis ConnectionError."""
    import redis.exceptions

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=redis.exceptions.ConnectionError("Connection refused"))

    with (
        patch("youtube.quota.get_redis_client", return_value=mock_redis),
        pytest.raises(redis.exceptions.ConnectionError),
    ):
        await consume(COST_ANALYTICS_REPORT)

    # Confirm the Redis client actually raised — not that we got a QuotaExhaustedError silently.
    mock_redis.eval.assert_awaited_once()


def test_quota_key_uses_pacific_not_utc(monkeypatch):
    """Issue 76: the daily key must roll over on Google's Pacific reset, not UTC.

    Pins an instant that is still 'yesterday' in PT but already 'tomorrow' in UTC;
    a UTC-keyed implementation would produce the Jan-2 key 7-8h early and hand out
    spent budget.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import youtube.quota as q

    pt = ZoneInfo("America/Los_Angeles")
    fixed = datetime(2026, 1, 1, 19, 0, tzinfo=pt)  # == 2026-01-02 03:00 UTC

    class _DT:
        @staticmethod
        def now(tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(q, "datetime", _DT)
    assert q._quota_key().endswith("2026-01-01")  # PT date, not UTC's 2026-01-02
