"""SEV1 #3 — single-flight guard around the billed thumbnail-pattern LLM call.

Tests the extracted helper ``_compute_patterns_single_flight`` directly (no
limiter / Depends / DB needed):
  - concurrency: two simultaneous callers run the expensive compute ONCE
    (real Redis, available locally + in CI);
  - fail-open: a Redis outage on lock acquisition still returns a result.
"""

import asyncio
import threading
import uuid

import pytest
import redis.asyncio as aredis

from routers.thumbnails import _compute_patterns_single_flight, _get_redis

_PATTERNS = {
    "face_present": "always",
    "dominant_emotions": ["surprise"],
    "text_overlay_style": "bold_caps",
    "typical_colors": "warm",
    "composition_pattern": "centered",
    "channel_thumbnail_signature": "high-contrast face + caps",
}


@pytest.mark.asyncio
async def test_single_flight_runs_compute_once_under_concurrency():
    """Two concurrent first-hits with the same cache/lock id must fire the
    multimodal compute exactly once; the loser reads the holder's cached result."""
    redis = _get_redis()
    uniq = uuid.uuid4().hex[:12]
    cache_key = f"test:thumb_patterns:{uniq}"
    lock_id = f"creator-{uniq}"
    # Clean slate.
    await redis.delete(cache_key, f"thumbnail-patterns-lock:{lock_id}")

    calls = []
    lock = threading.Lock()

    async def _compute():
        with lock:
            calls.append(1)
        # Simulate the multimodal round trip — awaited on the loop (Issue 82a).
        await asyncio.sleep(0.6)
        return dict(_PATTERNS)

    async def _run():
        return await _compute_patterns_single_flight(
            redis,
            lock_id=lock_id,
            cache_key=cache_key,
            compute=_compute,
            cache_ttl=30,
        )

    try:
        a, b = await asyncio.gather(_run(), _run())
        assert len(calls) == 1, "compute must run exactly once under single-flight"
        # Both callers get the same patterns; one computed, one read from cache.
        assert a["face_present"] == "always"
        assert b["face_present"] == "always"
        assert {a["cached"], b["cached"]} == {True, False}
    finally:
        await redis.delete(cache_key, f"thumbnail-patterns-lock:{lock_id}")


@pytest.mark.asyncio
async def test_single_flight_fails_open_on_redis_error():
    """A Redis outage on lock acquisition (and cache write) must NOT 500 — the
    compute still runs and the result is returned (rate limit bounds exposure)."""
    from unittest.mock import AsyncMock, MagicMock

    redis = MagicMock()
    redis.set = AsyncMock(side_effect=aredis.RedisError("down"))
    redis.setex = AsyncMock(side_effect=aredis.RedisError("down"))
    redis.eval = AsyncMock(side_effect=aredis.RedisError("down"))

    calls = []

    async def _compute():
        calls.append(1)
        return dict(_PATTERNS)

    result = await _compute_patterns_single_flight(
        redis,
        lock_id="creator-x",
        cache_key="k",
        compute=_compute,
        cache_ttl=30,
    )
    assert len(calls) == 1
    assert result["face_present"] == "always"
    assert result["cached"] is False
