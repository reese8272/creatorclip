"""
Unit tests for youtube/quota.py.

All Redis calls are patched — no live Redis required.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from youtube.quota import (
    COST_ANALYTICS_REPORT,
    COST_DATA_CAPTIONS,
    COST_DATA_VIDEOS,
    QuotaExhaustedError,
    QuotaSubBudgetExhaustedError,
    _creator_quota_key,
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
    # eval positional layout: script, numkeys, global_key, creator_key, cost, ...
    call_args = mock_redis.eval.call_args
    cost_arg = int(call_args.args[4])
    assert cost_arg == COST_DATA_CAPTIONS


# ── Issue 260: per-creator refresh sub-budget ─────────────────────────────────


@pytest.mark.asyncio
async def test_consume_with_creator_id_passes_creator_key_and_limit():
    """consume(cost, creator_id=...) threads BOTH keys + the per-creator limit."""
    from config import settings

    cid = uuid.uuid4()
    mock_redis = _make_redis_mock(eval_return=10)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        await consume(COST_DATA_VIDEOS, creator_id=cid)

    args = mock_redis.eval.call_args.args
    # script, numkeys(2), global_key, creator_key, cost, global_limit, per_creator_limit, ttl
    assert args[1] == 2
    assert args[2] == _quota_key()
    assert args[3] == _creator_quota_key(cid)
    assert str(cid) in args[3]
    assert int(args[6]) == settings.YOUTUBE_QUOTA_PER_CREATOR_REFRESH_UNITS


@pytest.mark.asyncio
async def test_consume_without_creator_id_uses_empty_creator_key():
    """No creator_id ⇒ the creator arm is empty (global-only, backward compatible)."""
    mock_redis = _make_redis_mock(eval_return=10)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        await consume(COST_DATA_VIDEOS)
    args = mock_redis.eval.call_args.args
    assert args[2] == _quota_key()
    assert args[3] == ""  # empty creator key → Lua skips the sub-budget arm


@pytest.mark.asyncio
async def test_consume_sub_budget_exhausted_raises_subclass():
    """Lua -2 ⇒ QuotaSubBudgetExhaustedError, which is catchable as QuotaExhaustedError."""
    cid = uuid.uuid4()
    mock_redis = _make_redis_mock(eval_return=-2)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        with pytest.raises(QuotaSubBudgetExhaustedError):
            await consume(COST_DATA_VIDEOS, creator_id=cid)
        # subclass relationship — existing `except QuotaExhaustedError` still catches it
        with pytest.raises(QuotaExhaustedError):
            await consume(COST_DATA_VIDEOS, creator_id=cid)


@pytest.mark.asyncio
async def test_consume_global_cap_is_outer_bound_regardless_of_creator():
    """Lua -1 (global exhausted) raises QuotaExhaustedError even with a creator_id."""
    mock_redis = _make_redis_mock(eval_return=-1)
    with (
        patch("youtube.quota.get_redis_client", return_value=mock_redis),
        pytest.raises(QuotaExhaustedError),
    ):
        await consume(COST_DATA_VIDEOS, creator_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_fairness_one_over_budget_creator_does_not_block_another():
    """Creator A over sub-budget (-2) raises; creator B (positive) succeeds."""
    cid_a, cid_b = uuid.uuid4(), uuid.uuid4()

    async def fake_eval(_script, _numkeys, _gkey, ckey, *_rest):
        return -2 if ckey == _creator_quota_key(cid_a) else 25

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=fake_eval)

    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        with pytest.raises(QuotaSubBudgetExhaustedError):
            await consume(COST_DATA_VIDEOS, creator_id=cid_a)
        # B is unaffected — over-budget A did not block it
        await consume(COST_DATA_VIDEOS, creator_id=cid_b)


@pytest.mark.asyncio
async def test_consume_custom_sub_budget_overrides_default():
    """An explicit sub_budget is passed through as the per-creator limit."""
    mock_redis = _make_redis_mock(eval_return=10)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        await consume(COST_DATA_VIDEOS, creator_id=uuid.uuid4(), sub_budget=42)
    assert int(mock_redis.eval.call_args.args[6]) == 42


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


# ── Issue 352 Batch D: videos.insert dedicated daily call bucket ──────────────


@pytest.mark.asyncio
async def test_consume_insert_uses_separate_bucket_at_one_call_each():
    """videos.insert bills 1 call to its OWN daily key — never 100 units of the
    shared read pool (Google 2026-06-01 API update)."""
    from config import settings
    from youtube.quota import _insert_quota_key, consume_insert

    mock_redis = _make_redis_mock(eval_return=1)
    with patch("youtube.quota.get_redis_client", return_value=mock_redis):
        await consume_insert()

    args = mock_redis.eval.call_args.args
    # script, numkeys, insert_key, creator_key(empty), cost, limit, ...
    assert args[2] == _insert_quota_key()
    assert args[2] != _quota_key()  # NOT the shared read-pool counter
    assert args[3] == ""
    assert int(args[4]) == 1  # 1 call, not 100 units
    assert int(args[5]) == settings.YOUTUBE_QUOTA_INSERT_DAILY_CALLS


@pytest.mark.asyncio
async def test_consume_insert_exhausted_raises_quota_error():
    from youtube.quota import consume_insert

    mock_redis = _make_redis_mock(eval_return=-1)
    with (
        patch("youtube.quota.get_redis_client", return_value=mock_redis),
        pytest.raises(QuotaExhaustedError),
    ):
        await consume_insert()


def test_insert_quota_key_is_pt_dated():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from youtube.quota import _insert_quota_key

    key = _insert_quota_key()
    assert key.startswith("creatorclip:yt_quota:insert:")
    assert datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d") in key


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
