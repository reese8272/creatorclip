"""LLM spend caps + cost-velocity circuit breaker (Issue 290).

Redis MICRODOLLAR (integer µ$) counters, updated post-call from the billing
ledger choke point (``billing.ledger.record_llm_usage``) and enforced
PRE-execution by :func:`require_budget` (FastAPI dependency, creator-scoped
429) and :func:`ensure_within_budget` (top-of-task guard for paid Celery
work). Integer µ$ avoids float drift on INCRBY — the same reason the YouTube
quota counters are integers.

The multi-key Lua is adapted from ``youtube/quota.py``'s ``_LUA_CONSUME``
check-then-increment: here the increment is UNCONDITIONAL (the money is
already spent when the ledger records it — refusing the increment would just
blind the caps), so the "check" arm lives in the pre-execution guards while
the Lua keeps the five counters + returned totals atomic under concurrent
workers.

Counters (all UTC-windowed):
  - per-creator daily   ``creatorclip:spend:{YYYY-MM-DD}:creator:{id}``
  - global daily        ``creatorclip:spend:{YYYY-MM-DD}``
  - global monthly      ``creatorclip:spend:{YYYY-MM}``
  - velocity            5-min fixed buckets ``creatorclip:spend:vel:{epoch//300}``
                        (+ per-creator variant); rolling spend ≈ sum of the
                        last 3 buckets, compared to the per-15-min limits.

Breach semantics (approved 2026-07-02, docs/DECISIONS.md):
  - ≥80% of any cap  → ``spend_cap_warning`` on both telemetry rails, once per
    window (SETNX marker key).
  - 100% per-creator (daily cap or creator velocity) → creator cool-down key
    (TTL ``SPEND_COOLDOWN_TTL_S``) → 429 from ``require_budget``. Creator-
    scoped ONLY — one creator can never trip the global switch.
  - 100% global (daily/monthly) or global velocity → flip the EXISTING
    ``llm_generation`` kill switch off via ``flags.set_flag`` behind a Redis
    SETNX trip-latch (exactly-once under concurrent workers) + a
    ``spend_cap_tripped`` event. No new gate concept.

FAIL-OPEN on any Redis error (warn once per surface — the flags.py posture):
the spend guard being down must never take LLM features down with it.

Manual reset (see docs/RUNBOOKS.md "Spend guard trip & reset"):
``python3 scripts/flags.py enable llm_generation`` + ``redis-cli DEL`` of the
cool-down / trip-latch keys.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis
from fastapi import Depends, HTTPException

from auth import get_current_creator
from config import settings
from models import Creator
from observability import log_event
from youtube._redis import get_redis_client

logger = logging.getLogger(__name__)

MICRO_PER_USD = 1_000_000  # µ$ per USD — integer counters, no float drift

_PREFIX = "creatorclip:spend"
_TRIP_LATCH_KEY = f"{_PREFIX}:trip:llm_generation"

VELOCITY_BUCKET_S = 300  # 5-min fixed buckets
VELOCITY_WINDOW_BUCKETS = 3  # rolling ≈ 15 min = current + previous 2

# TTLs sized to window + margin so counters expire on their own.
_DAILY_TTL_S = 2 * 86_400  # day window + 1-day margin
_MONTHLY_TTL_S = 35 * 86_400  # month window + margin
_VEL_TTL_S = VELOCITY_BUCKET_S * (VELOCITY_WINDOW_BUCKETS + 2)  # window + margin

# Unconditional multi-key increment; returns all five new totals so breach
# checks read a consistent snapshot in one round trip.
# KEYS = [creator_daily, global_daily, global_monthly, vel_global, vel_creator]
# ARGV = [amount_micro, daily_ttl, monthly_ttl, vel_ttl]
_LUA_RECORD = """
local amount      = tonumber(ARGV[1])
local daily_ttl   = tonumber(ARGV[2])
local monthly_ttl = tonumber(ARGV[3])
local vel_ttl     = tonumber(ARGV[4])
local c_daily = redis.call('INCRBY', KEYS[1], amount)
redis.call('EXPIRE', KEYS[1], daily_ttl)
local g_daily = redis.call('INCRBY', KEYS[2], amount)
redis.call('EXPIRE', KEYS[2], daily_ttl)
local g_month = redis.call('INCRBY', KEYS[3], amount)
redis.call('EXPIRE', KEYS[3], monthly_ttl)
local v_g = redis.call('INCRBY', KEYS[4], amount)
redis.call('EXPIRE', KEYS[4], vel_ttl)
local v_c = redis.call('INCRBY', KEYS[5], amount)
redis.call('EXPIRE', KEYS[5], vel_ttl)
return {c_daily, g_daily, g_month, v_g, v_c}
"""

# Honest, actionable creator-facing copy — no stack traces, no virality promises.
_CREATOR_BLOCK_DETAIL = (
    "Your account's daily AI budget has been reached. It resets automatically "
    "(cool-down up to 1 hour; daily budgets reset at midnight UTC). "
    "Try again later or contact support to raise your limit."
)

# Warn-once-per-process on Redis failure, per surface (flags.py posture).
_fail_open_warned: set[str] = set()


class SpendCapExceededError(Exception):
    """Raised by the Celery-task guard when a spend cap blocks paid work."""


def _warn_fail_open(surface: str) -> None:
    if surface not in _fail_open_warned:
        _fail_open_warned.add(surface)
        logger.warning(
            "spend guard %s hit a Redis error — failing OPEN (caps not enforced)",
            surface,
            exc_info=True,
        )


def usd_to_micro(usd: float) -> int:
    """Convert USD to integer microdollars (round-half-even at the µ$)."""
    return int(round(usd * MICRO_PER_USD))


def micro_to_usd(micro: int) -> float:
    return micro / MICRO_PER_USD


def _day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _bucket() -> int:
    return int(time.time() // VELOCITY_BUCKET_S)


def creator_daily_key(creator_id: uuid.UUID | str) -> str:
    return f"{_PREFIX}:{_day()}:creator:{creator_id}"


def global_daily_key() -> str:
    return f"{_PREFIX}:{_day()}"


def global_monthly_key() -> str:
    return f"{_PREFIX}:{_month()}"


def vel_global_key(bucket: int) -> str:
    return f"{_PREFIX}:vel:{bucket}"


def vel_creator_key(bucket: int, creator_id: uuid.UUID | str) -> str:
    return f"{_PREFIX}:vel:{bucket}:creator:{creator_id}"


def cooldown_key(creator_id: uuid.UUID | str) -> str:
    return f"{_PREFIX}:cooldown:creator:{creator_id}"


async def _emit_spend_event(
    event: str,
    creator_id: str | None,
    **fields: object,
) -> None:
    """One spend event on BOTH telemetry rails (log line + durable DB row)."""
    log_event(event, creator_id=creator_id, **fields)
    from event_log import record_event  # lazy — keeps import graph light

    await record_event(
        source="backend",
        event=event,
        level="warning",
        creator_id=creator_id,
        extra=dict(fields),
    )


async def _warn_once(
    r: redis.Redis,
    marker_key: str,
    marker_ttl: int,
    cap_label: str,
    total_micro: int,
    cap_micro: int,
    creator_id: str | None,
) -> None:
    """Emit ``spend_cap_warning`` once per window via a SETNX marker key."""
    created = await r.set(marker_key, "1", nx=True, ex=marker_ttl)
    if not created:
        return
    await _emit_spend_event(
        "spend_cap_warning",
        creator_id,
        cap=cap_label,
        spent_usd=round(micro_to_usd(total_micro), 4),
        cap_usd=micro_to_usd(cap_micro),
    )


async def _flip_llm_flag(reason: str) -> None:
    """Flip the existing ``llm_generation`` kill switch off (no new gate concept)."""
    import db
    from flags import set_flag

    async with db.AdminSessionLocal() as session:
        await set_flag(
            "llm_generation", False, updated_by="spend_guard", reason=reason, session=session
        )


async def record_spend(creator_id: uuid.UUID | str, usd: float) -> None:
    """Post-call spend increment + breach checks. Best-effort — NEVER raises.

    Called from ``billing.ledger.record_llm_usage`` beside the ledger write.
    The money is already spent, so the counters increment unconditionally;
    enforcement happens pre-execution (``require_budget`` /
    ``ensure_within_budget``) and — for global caps — via the
    ``llm_generation`` kill switch flipped here behind a SETNX trip-latch.
    """
    amount = usd_to_micro(usd)
    if amount <= 0:
        return
    try:
        await _record_and_enforce(str(creator_id), amount)
    except Exception:  # noqa: BLE001 — fail-open by design; billing must not break pipelines
        _warn_fail_open("record_spend")


async def _record_and_enforce(creator_id: str, amount: int) -> None:
    r = get_redis_client()
    bucket = _bucket()

    totals = await r.eval(  # type: ignore[misc]  # SDK/stub typing lag (Issue 78c)
        _LUA_RECORD,
        5,
        creator_daily_key(creator_id),
        global_daily_key(),
        global_monthly_key(),
        vel_global_key(bucket),
        vel_creator_key(bucket, creator_id),
        amount,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        _DAILY_TTL_S,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        _MONTHLY_TTL_S,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
        _VEL_TTL_S,  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
    )
    c_daily, g_daily, g_month, v_g, v_c = (int(v) for v in totals)

    # Rolling ≈15-min velocity = current bucket + previous 2 (fixed windows).
    prev = await r.mget(
        vel_global_key(bucket - 1),
        vel_global_key(bucket - 2),
        vel_creator_key(bucket - 1, creator_id),
        vel_creator_key(bucket - 2, creator_id),
    )
    v_g_roll = v_g + sum(int(x or 0) for x in prev[:2])
    v_c_roll = v_c + sum(int(x or 0) for x in prev[2:])

    c_cap = usd_to_micro(settings.SPEND_CAP_CREATOR_DAILY_USD)
    g_cap = usd_to_micro(settings.SPEND_CAP_GLOBAL_DAILY_USD)
    m_cap = usd_to_micro(settings.SPEND_CAP_GLOBAL_MONTHLY_USD)
    vg_cap = usd_to_micro(settings.SPEND_VELOCITY_GLOBAL_USD_PER_15M)
    vc_cap = usd_to_micro(settings.SPEND_VELOCITY_CREATOR_USD_PER_15M)
    warn_ratio = settings.SPEND_WARN_RATIO

    # ── 80% warns — once per window per cap ──────────────────────────────────
    day, month = _day(), _month()
    warn_arms: tuple[tuple[str, int, int, str, int, str | None], ...] = (
        (
            "creator_daily",
            c_daily,
            c_cap,
            f"{_PREFIX}:warn:{day}:creator:{creator_id}",
            _DAILY_TTL_S,
            creator_id,
        ),
        ("global_daily", g_daily, g_cap, f"{_PREFIX}:warn:{day}", _DAILY_TTL_S, None),
        ("global_monthly", g_month, m_cap, f"{_PREFIX}:warn:{month}", _MONTHLY_TTL_S, None),
        ("velocity_global", v_g_roll, vg_cap, f"{_PREFIX}:warn:vel:{bucket}", _VEL_TTL_S, None),
        (
            "velocity_creator",
            v_c_roll,
            vc_cap,
            f"{_PREFIX}:warn:vel:{bucket}:creator:{creator_id}",
            _VEL_TTL_S,
            creator_id,
        ),
    )
    for label, total, cap, marker, ttl, cid in warn_arms:
        if cap > 0 and warn_ratio * cap <= total < cap:
            await _warn_once(r, marker, ttl, label, total, cap, cid)

    # ── 100% per-creator (daily or creator velocity) → cool-down, NEVER the
    # global flag — one creator must not pause the product for everyone. ─────
    breached_creator: str | None = None
    if c_cap > 0 and c_daily >= c_cap:
        breached_creator = "creator_daily"
    elif vc_cap > 0 and v_c_roll >= vc_cap:
        breached_creator = "velocity_creator"
    if breached_creator is not None:
        created = await r.set(
            cooldown_key(creator_id), breached_creator, nx=True, ex=settings.SPEND_COOLDOWN_TTL_S
        )
        if created:
            await _emit_spend_event(
                "spend_cap_tripped",
                creator_id,
                cap=breached_creator,
                scope="creator",
                cooldown_s=settings.SPEND_COOLDOWN_TTL_S,
            )

    # ── 100% global (daily/monthly) or global velocity → kill switch off,
    # exactly once under concurrent workers via a SETNX trip-latch. ──────────
    breached_global: str | None = None
    if g_cap > 0 and g_daily >= g_cap:
        breached_global = "global_daily"
    elif m_cap > 0 and g_month >= m_cap:
        breached_global = "global_monthly"
    elif vg_cap > 0 and v_g_roll >= vg_cap:
        breached_global = "velocity_global"
    if breached_global is not None:
        latch = await r.set(
            _TRIP_LATCH_KEY, breached_global, nx=True, ex=settings.SPEND_COOLDOWN_TTL_S
        )
        if latch:
            reason = (
                f"spend cap tripped: {breached_global} "
                f"(see docs/RUNBOOKS.md 'Spend guard trip & reset')"
            )
            await _flip_llm_flag(reason)
            await _emit_spend_event(
                "spend_cap_tripped",
                None,
                cap=breached_global,
                scope="global",
                flag="llm_generation",
            )


async def creator_block_status(creator_id: uuid.UUID | str) -> tuple[bool, int]:
    """Creator-scoped pre-execution check: ``(blocked, retry_after_s)``.

    Blocked when a cool-down key is active OR the creator's daily counter has
    reached the cap. Global caps are NOT checked here — the tripped
    ``llm_generation`` flag (checked by ``require_flag`` / the task guard)
    already covers them. FAIL-OPEN on Redis errors.
    """
    try:
        r = get_redis_client()
        ttl = await r.ttl(cooldown_key(creator_id))
        if ttl == -1 or (ttl is not None and ttl > 0):
            return True, ttl if ttl > 0 else settings.SPEND_COOLDOWN_TTL_S
        current = int(await r.get(creator_daily_key(creator_id)) or 0)
        if current >= usd_to_micro(settings.SPEND_CAP_CREATOR_DAILY_USD):
            return True, settings.SPEND_COOLDOWN_TTL_S
        return False, 0
    except Exception:  # noqa: BLE001 — fail-open by design (flags.py posture)
        _warn_fail_open("creator_block_status")
        return False, 0


async def require_budget(creator: Creator = Depends(get_current_creator)) -> None:
    """FastAPI dependency: 429 when the creator's spend budget is exhausted.

    Stacked next to ``Depends(require_flag("llm_generation"))`` on LLM routes —
    the flag covers global caps (flipped by the breaker), this covers the
    creator-scoped daily cap + cool-down.
    """
    blocked, retry_after = await creator_block_status(creator.id)
    if blocked:
        raise HTTPException(
            status_code=429,
            detail=_CREATOR_BLOCK_DETAIL,
            headers={"Retry-After": str(retry_after)},
        )


async def ensure_within_budget(creator_id: uuid.UUID | str) -> None:
    """Top-of-task guard for paid Celery pipeline work.

    Mirrors the router gates for work that bypasses HTTP: re-checks the
    ``llm_generation`` kill switch (which the global breaker flips) and the
    creator-scoped budget. Raises :class:`SpendCapExceededError` with safe,
    actionable copy; fail-open on Redis/DB errors (each check fails open
    internally).
    """
    import db
    from flags import block_message, flag_enabled

    if not await flag_enabled("llm_generation", db.AdminSessionLocal):
        raise SpendCapExceededError(block_message("llm_generation"))
    blocked, _ = await creator_block_status(creator_id)
    if blocked:
        raise SpendCapExceededError(_CREATOR_BLOCK_DETAIL)
