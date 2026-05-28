"""
Unit tests for youtube/quota.py.

All Redis calls are patched — no live Redis required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_redis(eval_return: int = 500, get_return: str | None = "500"):
    """Return a mock async Redis client that eval() and get() return the given values."""
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=eval_return)
    mock_redis.get = AsyncMock(return_value=get_return)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_redis)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, mock_redis


@pytest.mark.asyncio
async def test_consume_success_does_not_raise():
    ctx, mock_redis = _make_redis(eval_return=100)
    with patch("youtube.quota.aioredis.from_url", return_value=ctx):
        await consume(COST_ANALYTICS_REPORT)
    mock_redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_consume_exhausted_raises_quota_error():
    ctx, _ = _make_redis(eval_return=-1)
    with (
        patch("youtube.quota.aioredis.from_url", return_value=ctx),
        pytest.raises(QuotaExhaustedError),
    ):
        await consume(COST_ANALYTICS_REPORT)


@pytest.mark.asyncio
async def test_consume_captions_cost_passed_to_lua():
    ctx, mock_redis = _make_redis(eval_return=50)
    with patch("youtube.quota.aioredis.from_url", return_value=ctx):
        await consume(COST_DATA_CAPTIONS)
    call_args = mock_redis.eval.call_args
    cost_arg = int(call_args.args[3])
    assert cost_arg == COST_DATA_CAPTIONS


@pytest.mark.asyncio
async def test_remaining_calculates_from_redis():
    ctx, _ = _make_redis(get_return="3000")
    with patch("youtube.quota.aioredis.from_url", return_value=ctx):
        from config import settings

        left = await remaining()
    assert left == settings.YOUTUBE_QUOTA_DAILY_UNITS - 3000


@pytest.mark.asyncio
async def test_remaining_when_no_key_returns_full_budget():
    ctx, _ = _make_redis(get_return=None)
    with patch("youtube.quota.aioredis.from_url", return_value=ctx):
        from config import settings

        left = await remaining()
    assert left == settings.YOUTUBE_QUOTA_DAILY_UNITS


@pytest.mark.asyncio
async def test_remaining_never_negative():
    ctx, _ = _make_redis(get_return="99999")
    with patch("youtube.quota.aioredis.from_url", return_value=ctx):
        left = await remaining()
    assert left == 0


def test_quota_key_contains_today():
    from datetime import UTC, datetime

    key = _quota_key()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert today in key
    assert key.startswith("creatorclip:yt_quota:")


def test_cost_constants_are_correct():
    assert COST_ANALYTICS_REPORT == 1
    assert COST_DATA_VIDEOS == 1
    assert COST_DATA_CAPTIONS == 50
