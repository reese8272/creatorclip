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

REDIS-ERROR POSTURE — SPLIT, and deliberately so (Issue 522, DECISIONS 2026-08-17):

  - The pre-execution CHECKS (``creator_block_status`` → ``require_budget`` /
    ``ensure_within_budget``) fail **CLOSED**. This is a money control: an
    unverifiable budget must not authorise paid work. Creators see a 503 with
    can't-verify copy, not the cap-reached copy — the cap was never read.
  - ``record_spend`` fails **OPEN**. It is post-call accounting; the money is
    already spent when the ledger records it, so refusing here would break the
    pipeline after the cost was incurred and protect nothing.

Warned once per surface per process, naming which arm fired. The companion
availability control (``limiter.py``) degrades OPEN on the same outage — same
event, opposite correct answer.

Manual reset (see docs/RUNBOOKS.md "Spend guard trip & reset"):
``python3 scripts/flags.py enable llm_generation`` + ``redis-cli DEL`` of the
cool-down / trip-latch keys.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import NamedTuple

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

# Issue 522 — the CAN'T-VERIFY case needs its own words. Reusing the copy above
# when Redis is unreachable tells every creator their budget is exhausted when it
# is not: a false statement to the user, which this project's honesty constraint
# forbids just as firmly as a virality claim. It is also unactionable — "try again
# later" is right, "raise your limit" is not.
_BUDGET_UNAVAILABLE_DETAIL = (
    "We can't check your AI budget right now, so AI features are paused for a few "
    "minutes. Nothing was charged. Please try again shortly."
)

# Retry-After (seconds) advertised with the can't-verify 503. Short on purpose:
# the condition is a Redis blip, not a spend window, so the client should come
# back quickly rather than wait out a cool-down it is not actually in.
_UNAVAILABLE_RETRY_AFTER_S = 30

# Warn-once-per-process on Redis failure, per surface (flags.py posture).
_degraded_warned: set[str] = set()


class BudgetStatus(NamedTuple):
    """Result of a pre-execution budget check.

    ``reason`` is what lets the callers tell a real cap breach apart from an
    unverifiable one, so each can pick honest copy and an honest status code.
    Three fields rather than the old ``(blocked, retry_after)`` pair on purpose
    (Issue 522): a stale two-tuple mock now fails loudly at ``.reason`` instead of
    silently satisfying the new contract with the old meaning.
    """

    blocked: bool
    retry_after_s: int
    reason: str  # "ok" | "cap_reached" | "unavailable"


class SpendCapExceededError(Exception):
    """Raised by the Celery-task guard when a spend cap blocks paid work."""


def _warn_degraded(surface: str, *, failing_closed: bool) -> None:
    """Warn once per surface per process that a Redis error changed behaviour.

    Issue 522 split the posture: the money control fails CLOSED (this module),
    the availability control degrades open (``limiter.py``). The log line says
    which arm fired so an operator reading it knows whether spend was left
    unenforced or paid work was refused.
    """
    if surface not in _degraded_warned:
        _degraded_warned.add(surface)
        logger.warning(
            "spend guard %s hit a Redis error — failing %s (%s)",
            surface,
            "CLOSED" if failing_closed else "OPEN",
            "paid work refused until Redis recovers"
            if failing_closed
            else "caps not enforced; cost already incurred",
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
        # Issue 522 deliberately did NOT flip this arm closed. This is post-call
        # ACCOUNTING: the money was already spent when the ledger recorded it, so
        # refusing here would break the pipeline after the cost was incurred and
        # protect nothing. Fail-closed belongs on the pre-execution checks below.
        _warn_degraded("record_spend", failing_closed=False)


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
            try:
                await _flip_llm_flag(reason)
                await _emit_spend_event(
                    "spend_cap_tripped",
                    None,
                    cap=breached_global,
                    scope="global",
                    flag="llm_generation",
                )
            except Exception:
                # A latch with no flip would silence the breaker for the full
                # cool-down TTL while the breach keeps burning. Release it so
                # the next record_spend re-attempts the flip; set_flag is an
                # idempotent upsert, so a rare double-flip under the race is
                # harmless. record_spend's catch-all still fails open.
                await r.delete(_TRIP_LATCH_KEY)
                raise


async def creator_block_status(creator_id: uuid.UUID | str) -> BudgetStatus:
    """Creator-scoped pre-execution check: ``(blocked, retry_after_s, reason)``.

    Blocked when a cool-down key is active OR the creator's daily counter has
    reached the cap. Global caps are NOT checked here — the tripped
    ``llm_generation`` flag (checked by ``require_flag`` / the task guard)
    already covers them.

    **FAILS CLOSED on Redis errors** (Issue 522, posture decided in
    ``docs/DECISIONS.md`` 2026-08-17). This is a MONEY control: an unverifiable
    budget must not authorise paid work, because the failure mode of guessing
    "unblocked" is unbounded spend with no ceiling anyone can see. The
    availability control (``limiter.py``) takes the opposite arm and degrades
    open — same outage, different correct answer.

    Reaching this arm at all requires the bounded socket timeouts on
    ``youtube/_redis.py``'s client: against a wedged-but-connected Redis a hang
    is not an exception, and an ``await`` that never returns can never fail
    closed.
    """
    try:
        r = get_redis_client()
        ttl = await r.ttl(cooldown_key(creator_id))
        if ttl == -1 or (ttl is not None and ttl > 0):
            retry = ttl if ttl > 0 else settings.SPEND_COOLDOWN_TTL_S
            return BudgetStatus(True, retry, "cap_reached")
        current = int(await r.get(creator_daily_key(creator_id)) or 0)
        if current >= usd_to_micro(settings.SPEND_CAP_CREATOR_DAILY_USD):
            return BudgetStatus(True, settings.SPEND_COOLDOWN_TTL_S, "cap_reached")
        return BudgetStatus(False, 0, "ok")
    except Exception:  # noqa: BLE001 — fail-CLOSED by design (Issue 522)
        _warn_degraded("creator_block_status", failing_closed=True)
        return BudgetStatus(True, _UNAVAILABLE_RETRY_AFTER_S, "unavailable")


async def require_budget(creator: Creator = Depends(get_current_creator)) -> None:
    """FastAPI dependency: refuse paid work when the budget is spent or unknown.

    Stacked next to ``Depends(require_flag("llm_generation"))`` on LLM routes —
    the flag covers global caps (flipped by the breaker), this covers the
    creator-scoped daily cap + cool-down.

    Two distinct refusals (Issue 522), because they are not the same event:

    * **429** — the cap really was reached. The creator can act on it.
    * **503** — the cap could not be checked. Reusing 429 here would claim the
      creator is rate-limited when they are not, and the copy would tell them a
      budget was exhausted that was never read.
    """
    status = await creator_block_status(creator.id)
    if not status.blocked:
        return
    unavailable = status.reason == "unavailable"
    raise HTTPException(
        status_code=503 if unavailable else 429,
        detail=_BUDGET_UNAVAILABLE_DETAIL if unavailable else _CREATOR_BLOCK_DETAIL,
        headers={"Retry-After": str(status.retry_after_s)},
    )


async def ensure_within_budget(creator_id: uuid.UUID | str) -> None:
    """Top-of-task guard for paid Celery pipeline work.

    Mirrors the router gates for work that bypasses HTTP: re-checks the
    ``llm_generation`` kill switch (which the global breaker flips) and the
    creator-scoped budget. Raises :class:`SpendCapExceededError` with safe,
    actionable copy.

    The flag check still fails OPEN to its env default (``flags.py``, DB-backed);
    the budget check fails CLOSED (Issue 522). That split is deliberate — the
    flag is an availability switch, the budget is a money control.
    """
    import db
    from flags import block_message, flag_enabled

    if not await flag_enabled("llm_generation", db.AdminSessionLocal):
        raise SpendCapExceededError(block_message("llm_generation"))
    status = await creator_block_status(creator_id)
    if status.blocked:
        raise SpendCapExceededError(
            _BUDGET_UNAVAILABLE_DETAIL if status.reason == "unavailable" else _CREATOR_BLOCK_DETAIL
        )
