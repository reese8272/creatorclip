"""
YouTube API daily quota tracking via Redis.

YouTube projects share a single 10,000 unit/day quota (resets midnight PT).
We track consumed units in Redis and refuse new calls once the configured budget
is exhausted, so the Beat analytics refresh degrades gracefully rather than
burning every unit and leaving interactive flows with nothing.

Cost reference (Google official documentation, 2026):
  Analytics API report query:    1 unit
  Data API channels.list:        1 unit
  Data API playlistItems.list:   1 unit
  Data API videos.list:          1 unit
  Data API captions.list:        50 units

videos.insert is NOT part of the shared pool: since Google's 2026-06-01 API
update it bills to its OWN daily bucket (~100 calls/day, 1 call each) — see
``consume_insert()``. Verified 2026-07-02 against
https://developers.google.com/youtube/v3/determine_quota_cost.
"""

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings
from youtube._redis import get_redis_client

# Google resets the shared project quota at midnight Pacific (per the module
# docstring). Key the daily counter by the PT date so it rolls over with Google's,
# not ~7-8h early on the UTC date (which would hand out spent budget → 403). (Issue 76)
_QUOTA_RESET_TZ = ZoneInfo("America/Los_Angeles")

logger = logging.getLogger(__name__)

COST_ANALYTICS_REPORT = 1
COST_DATA_CHANNELS = 1
COST_DATA_PLAYLIST_ITEMS = 1
COST_DATA_VIDEOS = 1
COST_DATA_CAPTIONS = 50

# Atomic two-key Lua: check the global budget and (optionally) a per-creator
# refresh sub-budget BEFORE incrementing either, so the check-then-incr stays
# atomic across BOTH counters (no TOCTOU race between the two). (Issue 76 +
# Issue 260). KEYS=[global_key, creator_key], ARGV=[cost, global_limit,
# per_creator_limit, ttl]. creator_key is an empty string when no sub-budget
# applies (interactive/onboarding path) — then only the global arm runs,
# preserving the original single-key behaviour exactly.
#   Returns:  global new total on success
#            -1 if the global cap would be exceeded (outer bound)
#            -2 if the per-creator sub-budget would be exceeded
_LUA_CONSUME = """
local gkey   = KEYS[1]
local ckey   = KEYS[2]
local cost   = tonumber(ARGV[1])
local glimit = tonumber(ARGV[2])
local climit = tonumber(ARGV[3])
local ttl    = tonumber(ARGV[4])
local gcur = tonumber(redis.call('GET', gkey) or 0)
if gcur + cost > glimit then
    return -1
end
if ckey ~= '' then
    local ccur = tonumber(redis.call('GET', ckey) or 0)
    if ccur + cost > climit then
        return -2
    end
end
local new_total = redis.call('INCRBY', gkey, cost)
redis.call('EXPIRE', gkey, ttl)
if ckey ~= '' then
    redis.call('INCRBY', ckey, cost)
    redis.call('EXPIRE', ckey, ttl)
end
return new_total
"""

_TTL_SECONDS = 90_000  # 25 hours — auto-expires the day after


def _quota_key() -> str:
    return f"creatorclip:yt_quota:{datetime.now(_QUOTA_RESET_TZ).strftime('%Y-%m-%d')}"


def _creator_quota_key(creator_id: uuid.UUID) -> str:
    """Per-creator/day refresh sub-budget key, PT-date-anchored (Issue 76 invariant)."""
    pt_date = datetime.now(_QUOTA_RESET_TZ).strftime("%Y-%m-%d")
    return f"creatorclip:yt_quota:{pt_date}:creator:{creator_id}"


def _insert_quota_key() -> str:
    """videos.insert daily CALL counter — a bucket separate from the shared read
    pool since Google's 2026-06-01 update (1 call each, ~100 calls/day)."""
    pt_date = datetime.now(_QUOTA_RESET_TZ).strftime("%Y-%m-%d")
    return f"creatorclip:yt_quota:insert:{pt_date}"


class QuotaExhaustedError(Exception):
    """Raised when the daily YouTube API quota budget is exhausted."""


class QuotaSubBudgetExhaustedError(QuotaExhaustedError):
    """Raised when a creator's per-day refresh sub-budget is exhausted.

    Subclasses QuotaExhaustedError so callers that only `except QuotaExhaustedError`
    keep working, while callers that want to skip one creator and continue the
    fan-out (rather than stop the whole run) can catch this case distinctly.
    """


async def consume(
    cost: int,
    *,
    creator_id: uuid.UUID | None = None,
    sub_budget: int | None = None,
) -> None:
    """
    Consume `cost` quota units atomically against the global daily budget.

    When `creator_id` is supplied (the non-interactive Beat refresh path), also
    enforce a per-creator/day refresh sub-budget so one creator's fan-out cannot
    drain the interactive pool. When `creator_id` is None (interactive /
    onboarding), only the global pool is checked — backward compatible with all
    existing callers.

    Raises:
        QuotaSubBudgetExhaustedError: the per-creator sub-budget would be exceeded.
        QuotaExhaustedError: the global daily budget would be exceeded.
    """
    r = get_redis_client()
    creator_key = _creator_quota_key(creator_id) if creator_id is not None else ""
    per_creator_limit = (
        sub_budget if sub_budget is not None else settings.YOUTUBE_QUOTA_PER_CREATOR_REFRESH_UNITS
    )
    result = await r.eval(  # type: ignore[misc]  # SDK/stub typing lag (Issue 78c)
        _LUA_CONSUME,
        2,
        _quota_key(),
        creator_key,
        cost,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        settings.YOUTUBE_QUOTA_DAILY_UNITS,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        per_creator_limit,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        _TTL_SECONDS,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
    )

    if result == -2:
        raise QuotaSubBudgetExhaustedError(
            f"YouTube refresh sub-budget exhausted for creator {creator_id} "
            f"(limit={per_creator_limit}/day)"
        )
    if result == -1:
        raise QuotaExhaustedError(
            f"YouTube quota budget exhausted (limit={settings.YOUTUBE_QUOTA_DAILY_UNITS}/day)"
        )
    logger.debug("YouTube quota: consumed %d units (daily total: %d)", cost, result)


async def consume_insert() -> None:
    """Consume one ``videos.insert`` call from the dedicated daily insert bucket.

    Uploads never debit the shared read pool (``consume()``): since Google's
    2026-06-01 API update, ``videos.insert`` bills to its own ~100-calls/day
    bucket at 1 call each (developers.google.com/youtube/v3/determine_quota_cost,
    verified 2026-07-02). Reuses ``_LUA_CONSUME`` with an empty creator key so
    the check-then-incr stays atomic; Google's own quotaExceeded 403 remains
    the hard enforcer.

    Raises:
        QuotaExhaustedError: the daily videos.insert call budget would be exceeded.
    """
    r = get_redis_client()
    result = await r.eval(  # type: ignore[misc]  # SDK/stub typing lag (Issue 78c)
        _LUA_CONSUME,
        2,
        _insert_quota_key(),
        "",
        1,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        settings.YOUTUBE_QUOTA_INSERT_DAILY_CALLS,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        0,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        _TTL_SECONDS,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
    )
    if result == -1:
        raise QuotaExhaustedError(
            "YouTube videos.insert daily call budget exhausted "
            f"(limit={settings.YOUTUBE_QUOTA_INSERT_DAILY_CALLS} uploads/day)"
        )
    logger.debug("YouTube insert quota: consumed 1 call (daily total: %d)", result)


async def remaining() -> int:
    """Return remaining quota units available for today."""
    r = get_redis_client()
    used = await r.get(_quota_key())
    return max(0, settings.YOUTUBE_QUOTA_DAILY_UNITS - int(used or 0))
