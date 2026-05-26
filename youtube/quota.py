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
"""

import logging
from datetime import UTC, datetime

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

COST_ANALYTICS_REPORT = 1
COST_DATA_CHANNELS = 1
COST_DATA_PLAYLIST_ITEMS = 1
COST_DATA_VIDEOS = 1
COST_DATA_CAPTIONS = 50

# Atomic Lua: check budget before incrementing so we never silently overshoot.
# Returns new total on success, -1 if the call would exceed the daily limit.
_LUA_CONSUME = """
local key   = KEYS[1]
local cost  = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl   = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', key) or 0)
if current + cost > limit then
    return -1
end
local new_total = redis.call('INCRBY', key, cost)
redis.call('EXPIRE', key, ttl)
return new_total
"""

_TTL_SECONDS = 90_000  # 25 hours — auto-expires the day after


def _quota_key() -> str:
    return f"creatorclip:yt_quota:{datetime.now(UTC).strftime('%Y-%m-%d')}"


class QuotaExhaustedError(Exception):
    """Raised when the daily YouTube API quota budget is exhausted."""


async def consume(cost: int) -> None:
    """
    Consume `cost` quota units atomically.
    Raises QuotaExhaustedError if the daily budget would be exceeded.
    """
    async with aioredis.from_url(settings.REDIS_URL, decode_responses=True) as r:
        result = await r.eval(
            _LUA_CONSUME,
            1,
            _quota_key(),
            cost,
            settings.YOUTUBE_QUOTA_DAILY_UNITS,
            _TTL_SECONDS,
        )

    if result == -1:
        raise QuotaExhaustedError(
            f"YouTube quota budget exhausted (limit={settings.YOUTUBE_QUOTA_DAILY_UNITS}/day)"
        )
    logger.debug("YouTube quota: consumed %d units (daily total: %d)", cost, result)


async def remaining() -> int:
    """Return remaining quota units available for today."""
    async with aioredis.from_url(settings.REDIS_URL, decode_responses=True) as r:
        used = await r.get(_quota_key())
    return max(0, settings.YOUTUBE_QUOTA_DAILY_UNITS - int(used or 0))
