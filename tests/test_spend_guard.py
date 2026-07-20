"""Spend guard (Issues 290+291) — 80/20 unit suite.

All Redis is faked at the client boundary (the youtube/quota test pattern):
no live Redis, no DB. Covers the µ$ math, warn-once, the creator 429 +
cool-down, the exactly-once global trip latch, the rolling velocity window,
fail-open on Redis errors, the require_budget dependency, and the
ledger-hook-never-raises contract.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from billing import spend_guard
from billing.ledger import record_llm_usage
from billing.spend_guard import (
    SpendCapExceededError,
    cooldown_key,
    creator_block_status,
    creator_daily_key,
    ensure_within_budget,
    micro_to_usd,
    record_spend,
    require_budget,
    usd_to_micro,
)
from config import settings

CID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _FakeRedis:
    """Duck-typed async Redis: preset eval/mget answers + real SETNX semantics."""

    def __init__(
        self,
        totals: list[int] | None = None,
        prev: dict[str, int] | None = None,
        ttl: int = -2,
    ) -> None:
        self.totals = totals or [0, 0, 0, 0, 0]
        self.prev = prev or {}
        self.store: dict[str, Any] = {}
        self._ttl = ttl
        self.set_calls: list[tuple[str, Any, dict[str, Any]]] = []

    async def eval(self, script: str, numkeys: int, *args: Any) -> list[int]:
        return self.totals

    async def mget(self, *keys: str) -> list[int | None]:
        return [self.prev.get(k) for k in keys]

    async def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> Any:
        self.set_calls.append((key, value, {"nx": nx, "ex": ex}))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def ttl(self, key: str) -> int:
        return self._ttl if key in self.store else -2

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture(autouse=True)
def _pin_caps():
    """Pin cap settings so .env overrides can't skew the assertions."""
    with (
        patch.object(settings, "SPEND_CAP_CREATOR_DAILY_USD", 5.00),
        patch.object(settings, "SPEND_CAP_GLOBAL_DAILY_USD", 50.00),
        patch.object(settings, "SPEND_CAP_GLOBAL_MONTHLY_USD", 400.00),
        patch.object(settings, "SPEND_WARN_RATIO", 0.80),
        patch.object(settings, "SPEND_VELOCITY_GLOBAL_USD_PER_15M", 5.00),
        patch.object(settings, "SPEND_VELOCITY_CREATOR_USD_PER_15M", 1.00),
        patch.object(settings, "SPEND_COOLDOWN_TTL_S", 3600),
    ):
        yield


def _patched(r: _FakeRedis):
    """Patch the Redis client + both event sinks + the flag flipper."""
    return (
        patch("billing.spend_guard.get_redis_client", return_value=r),
        patch("billing.spend_guard._emit_spend_event", new=AsyncMock()),
        patch("billing.spend_guard._flip_llm_flag", new=AsyncMock()),
    )


# ── µ$ math ────────────────────────────────────────────────────────────────────


def test_micro_math_roundtrip() -> None:
    assert usd_to_micro(5.00) == 5_000_000
    assert usd_to_micro(0.0105) == 10_500
    assert micro_to_usd(usd_to_micro(0.0105)) == pytest.approx(0.0105)
    # sub-µ$ costs round to an integer (no float drift on INCRBY)
    assert isinstance(usd_to_micro(0.0000004), int)


async def test_record_spend_zero_or_negative_is_noop() -> None:
    r = _FakeRedis()
    p1, p2, p3 = _patched(r)
    with p1, p2, p3:
        await record_spend(CID, 0.0)
        await record_spend(CID, -1.0)
    assert r.set_calls == []


# ── Warn-once at 80% ───────────────────────────────────────────────────────────


async def test_warn_fires_once_per_window() -> None:
    # creator daily at exactly 80% of $5.00 = $4.00; everything else tiny
    r = _FakeRedis(totals=[4_000_000, 100, 100, 100, 100])
    p1, p2, p3 = _patched(r)
    with p1, p2 as mock_emit, p3:
        await record_spend(CID, 0.01)
        await record_spend(CID, 0.01)  # marker key already set → silent
    warns = [c for c in mock_emit.await_args_list if c.args[0] == "spend_cap_warning"]
    assert len(warns) == 1
    assert warns[0].kwargs["cap"] == "creator_daily"


# ── 100% per-creator → cool-down + 429 ─────────────────────────────────────────


async def test_creator_cap_sets_cooldown_and_blocks_with_429() -> None:
    r = _FakeRedis(totals=[5_000_000, 100, 100, 100, 100])
    p1, p2, p3 = _patched(r)
    with p1, p2 as mock_emit, p3 as mock_flip:
        await record_spend(CID, 0.01)
        assert cooldown_key(CID) in r.store
        trip = [c for c in mock_emit.await_args_list if c.args[0] == "spend_cap_tripped"]
        assert len(trip) == 1 and trip[0].kwargs["scope"] == "creator"
        mock_flip.assert_not_awaited()  # creator breach NEVER trips the global flag

        # require_budget now 429s with Retry-After
        r._ttl = 1800
        creator = type("C", (), {"id": CID})()
        with pytest.raises(HTTPException) as exc:
            await require_budget(creator)
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "1800"


async def test_creator_daily_counter_at_cap_blocks_without_cooldown_key() -> None:
    r = _FakeRedis()
    r.store[creator_daily_key(CID)] = str(usd_to_micro(5.00))
    with patch("billing.spend_guard.get_redis_client", return_value=r):
        blocked, retry_after = await creator_block_status(CID)
    assert blocked and retry_after == 3600


# ── 100% global → kill switch, exactly once ────────────────────────────────────


async def test_global_trip_flips_flag_exactly_once() -> None:
    r = _FakeRedis(totals=[100, usd_to_micro(50.00), 100, 100, 100])
    p1, p2, p3 = _patched(r)
    with p1, p2 as mock_emit, p3 as mock_flip:
        await record_spend(CID, 0.01)
        await record_spend(CID, 0.01)  # concurrent worker: latch already held
    mock_flip.assert_awaited_once()
    assert "global_daily" in mock_flip.await_args.args[0]
    trips = [c for c in mock_emit.await_args_list if c.args[0] == "spend_cap_tripped"]
    assert len(trips) == 1 and trips[0].kwargs["scope"] == "global"


async def test_failed_flag_flip_releases_latch_so_next_call_retries() -> None:
    """2026-07-20 assessment: a latch set before a FAILED flip must be released
    — otherwise the breach goes unenforced for the full latch TTL while every
    worker sees the latch held. The next record_spend must re-attempt the flip."""
    r = _FakeRedis(totals=[100, usd_to_micro(50.00), 100, 100, 100])
    p1, p2, _ = _patched(r)
    flip = AsyncMock(side_effect=[RuntimeError("db down"), None])
    with p1, p2, patch("billing.spend_guard._flip_llm_flag", new=flip):
        await record_spend(CID, 0.01)  # flip fails → latch released (fail-open, no raise)
        assert spend_guard._TRIP_LATCH_KEY not in r.store
        await record_spend(CID, 0.01)  # retry: latch re-acquired, flip succeeds
    assert flip.await_count == 2
    assert spend_guard._TRIP_LATCH_KEY in r.store  # latch held after successful flip


async def test_velocity_rolling_window_sums_last_three_buckets() -> None:
    # current bucket $2.00 + two previous buckets $2.00 + $1.50 = $5.50 ≥ $5/15m
    bucket = spend_guard._bucket()
    prev = {
        spend_guard.vel_global_key(bucket - 1): usd_to_micro(2.00),
        spend_guard.vel_global_key(bucket - 2): usd_to_micro(1.50),
    }
    r = _FakeRedis(totals=[100, 100, 100, usd_to_micro(2.00), 100], prev=prev)
    p1, p2, p3 = _patched(r)
    with p1, p2, p3 as mock_flip:
        await record_spend(CID, 0.01)
    mock_flip.assert_awaited_once()
    assert "velocity_global" in mock_flip.await_args.args[0]


# ── Fail-open ──────────────────────────────────────────────────────────────────


async def test_record_spend_fails_open_on_redis_error() -> None:
    boom = AsyncMock()
    boom.eval = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch("billing.spend_guard.get_redis_client", return_value=boom):
        await record_spend(CID, 1.0)  # must not raise


async def test_block_status_fails_open_on_redis_error() -> None:
    boom = AsyncMock()
    boom.ttl = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch("billing.spend_guard.get_redis_client", return_value=boom):
        assert await creator_block_status(CID) == (False, 0)


# ── require_budget pass + Celery-task guard ────────────────────────────────────


async def test_require_budget_passes_when_under_cap() -> None:
    r = _FakeRedis()  # no cool-down key, counter absent (0)
    creator = type("C", (), {"id": CID})()
    with patch("billing.spend_guard.get_redis_client", return_value=r):
        await require_budget(creator)  # must not raise


async def test_ensure_within_budget_raises_when_flag_off_or_blocked() -> None:
    with (
        patch("flags.flag_enabled", new=AsyncMock(return_value=False)),
        pytest.raises(SpendCapExceededError),
    ):
        await ensure_within_budget(CID)
    with (
        patch("flags.flag_enabled", new=AsyncMock(return_value=True)),
        patch("billing.spend_guard.creator_block_status", new=AsyncMock(return_value=(True, 60))),
        pytest.raises(SpendCapExceededError),
    ):
        await ensure_within_budget(CID)
    with (
        patch("flags.flag_enabled", new=AsyncMock(return_value=True)),
        patch("billing.spend_guard.creator_block_status", new=AsyncMock(return_value=(False, 0))),
    ):
        await ensure_within_budget(CID)  # must not raise


# ── Ledger hook never raises (Issue 290/291 backstop) ──────────────────────────


async def test_record_llm_usage_survives_spend_hook_failure() -> None:
    """A broken spend hook must never break the billing write (or the pipeline)."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("billing.spend_guard.record_spend", new=AsyncMock(side_effect=RuntimeError("x"))),
        patch("billing.ledger.increment_usage", new=AsyncMock()) as mock_inc,
        patch("db.AdminSessionLocal", return_value=mock_session),
    ):
        await record_llm_usage(
            CID,
            {"input_tokens": 1000, "output_tokens": 500, "cache_read": 0, "cache_creation": 0},
            3.0,
            15.0,
        )
    mock_inc.assert_awaited_once()  # ledger write still happened
