"""
Minute balance management.

All mutations use an atomic UPDATE … WHERE … RETURNING pattern so concurrent
Celery tasks cannot double-spend or over-grant without a DB-level race.

Deductions are additionally guarded by the MinuteDeduction ledger's UNIQUE(video_id)
constraint — Celery's at-least-once delivery (task_acks_late=True) can re-invoke an
ingest task; the constraint prevents a second deduction from inserting (Issue 34).
"""

import logging
import math
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import event, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from config import settings
from models import Creator, MinuteDeduction, MinutePack, Usage

logger = logging.getLogger(__name__)

_MTOK = 1_000_000  # tokens per million (for cost calculation)

# session.info key holding balance_low notifications staged by deduct_for_video,
# drained by the after_commit listener below (Issue 352 Batch B).
_PENDING_BALANCE_LOW_KEY = "billing_pending_balance_low"


@event.listens_for(SyncSession, "after_commit")
def _send_balance_low_after_commit(sync_session: SyncSession) -> None:
    """Enqueue staged balance_low notifications only once the transaction commits.

    deduct_for_video stages (creator_id, video_id) pairs in ``session.info``
    instead of enqueuing directly — enqueuing before the caller's commit could
    notify for a deduction that a later rollback undoes (transactional-outbox
    violation). Class-level listener so no per-call listener bookkeeping is
    needed; sessions with nothing staged pay one dict lookup.
    """
    pending: list[tuple[str, str]] | None = sync_session.info.pop(_PENDING_BALANCE_LOW_KEY, None)
    if not pending:
        return
    for creator_id, video_id in pending:
        try:
            from worker.tasks import send_notification

            send_notification.delay(creator_id, "balance_low", video_id, {})
        except Exception as notify_exc:  # noqa: BLE001 — notification is best-effort
            logger.warning(
                "balance_low notification failed for creator %s video %s: %s",
                creator_id,
                video_id,
                notify_exc,
            )


@event.listens_for(SyncSession, "after_rollback")
def _discard_balance_low_on_rollback(sync_session: SyncSession) -> None:
    """A rolled-back deduction never persisted — drop anything staged for it."""
    sync_session.info.pop(_PENDING_BALANCE_LOW_KEY, None)


def video_minutes(duration_s: float) -> int:
    """Round a video duration up to the nearest whole minute (minimum 1)."""
    return max(1, math.ceil(duration_s / 60))


async def increment_usage(
    session: AsyncSession,
    creator_id: uuid.UUID,
    period: str,
    tokens_in: int,
    tokens_out: int,
    cost_estimate_usd: float,
) -> None:
    """Atomically accumulate LLM token counts + cost for a creator in a billing period.

    Uses a PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE`` (upsert) — no read-modify-
    write — so concurrent LLM calls for the same creator never race or lose counts.

    ``period`` format: 'YYYY-MM' (e.g. '2026-06'). Callers derive it from
    ``datetime.now(UTC).strftime('%Y-%m')``.

    Caller is responsible for committing the outer transaction.  This helper runs
    inside a ``begin_nested()`` savepoint so a upsert failure never silently corrupts
    the outer transaction — the exception propagates to the caller.
    """
    stmt = (
        pg_insert(Usage)
        .values(
            creator_id=creator_id,
            period=period,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_estimate=Decimal(str(cost_estimate_usd)),
        )
        .on_conflict_do_update(
            constraint="uq_usage_creator_period",
            set_={
                "tokens_in": Usage.tokens_in + tokens_in,
                "tokens_out": Usage.tokens_out + tokens_out,
                "cost_estimate": Usage.cost_estimate + Decimal(str(cost_estimate_usd)),
            },
        )
    )
    async with session.begin_nested():
        await session.execute(stmt)

    logger.debug(
        "usage increment creator=%s period=%s in=%d out=%d cost=%.6f",
        creator_id,
        period,
        tokens_in,
        tokens_out,
        cost_estimate_usd,
    )


def _estimate_cost_usd(
    tokens_in: int,
    tokens_out: int,
    cost_per_mtok_in: float,
    cost_per_mtok_out: float,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_write_multiplier: float | None = None,
) -> float:
    """Compute USD cost from token counts and per-million-token rates.

    The Anthropic SDK's ``usage.input_tokens`` is the UNCACHED remainder only
    (total prompt = input + cache_creation + cache_read), so cached tokens must
    be priced separately or they bill at 0× (Issue: cache-token under-bill,
    OFF_COURSE_BUGS 2026-06-24):
      - cache READS bill at ``COST_CACHE_READ_MULTIPLIER`` (0.1×) of the input rate,
      - cache WRITES bill at ``cache_write_multiplier`` of the input rate
        (5-min-TTL default 1.25×; pass 2.0 for ttl:"1h" callers like scoring).
    """
    if cache_write_multiplier is None:
        cache_write_multiplier = settings.COST_CACHE_WRITE_MULTIPLIER
    return (
        tokens_in * cost_per_mtok_in
        + tokens_out * cost_per_mtok_out
        + cache_read_tokens * cost_per_mtok_in * settings.COST_CACHE_READ_MULTIPLIER
        + cache_creation_tokens * cost_per_mtok_in * cache_write_multiplier
    ) / _MTOK


def _model_tier(cost_per_mtok_in: float) -> str:
    """Low-cardinality model label for the cost counter, inferred from the rate.

    ``record_llm_usage`` callers pass per-MTok rates, not model ids, so the
    metric labels by price-book tier — exact enough for the cost-by-model
    dashboard without touching every call site. (Issue 291)
    """
    if cost_per_mtok_in == settings.COST_PER_MTOK_IN_SONNET:
        return "sonnet-tier"
    if cost_per_mtok_in == settings.COST_PER_MTOK_IN_HAIKU:
        return "haiku-tier"
    if cost_per_mtok_in == settings.COST_PER_MTOK_IN_OPUS:
        return "opus-tier"
    return "other"


async def record_llm_usage(
    creator_id: uuid.UUID,
    usage: dict,
    cost_per_mtok_in: float,
    cost_per_mtok_out: float,
) -> None:
    """Open a short-lived admin session to write LLM usage to the cost ledger.

    Designed for callers that need to log usage AFTER closing their primary
    session (e.g. Celery tasks that run LLM calls outside a DB context).
    Uses ``AdminSessionLocal`` so it bypasses RLS (consistent with how tasks
    read creator data — cross-tenant admin path). Best-effort: logs and
    returns on any failure to avoid disrupting the pipeline.

    ``usage`` is the dict returned by ``worker.anthropic_stream.stream_and_emit``
    (keys: input_tokens, output_tokens, cache_read, cache_creation).

    This is also the choke point for the spend guard (Issue 290) and the
    ``llm_cost_usd_total`` counter (Issue 291) — every billed LLM call flows
    through here, so both rails see the exact USD the ledger persists.
    """
    import db as _db

    tokens_in = usage.get("input_tokens", 0)
    tokens_out = usage.get("output_tokens", 0)
    cost = _estimate_cost_usd(
        tokens_in,
        tokens_out,
        cost_per_mtok_in,
        cost_per_mtok_out,
        cache_read_tokens=usage.get("cache_read", 0),
        cache_creation_tokens=usage.get("cache_creation", 0),
    )
    try:
        # Issue 291: Prometheus/OTel cost counter — same USD as the ledger write.
        from observability import record_llm_cost

        record_llm_cost("anthropic", _model_tier(cost_per_mtok_in), cost)
        # Issue 290: spend-guard counters + breach checks (internally no-raise;
        # the outer try is a structural backstop — billing must never break a
        # pipeline).
        from billing import spend_guard

        await spend_guard.record_spend(creator_id, cost)
    except Exception:  # noqa: BLE001 — best-effort; never block pipeline
        logger.warning("spend-guard/cost-metric hook failed (swallowed)", exc_info=True)
    period = datetime.now(UTC).strftime("%Y-%m")
    try:
        async with _db.AdminSessionLocal() as session:
            await increment_usage(session, creator_id, period, tokens_in, tokens_out, cost)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort; never block pipeline
        logger.warning("record_llm_usage failed creator=%s: %s", creator_id, exc)


async def get_balance(creator_id: uuid.UUID, session: AsyncSession) -> int:
    balance = await session.scalar(select(Creator.minutes_balance).where(Creator.id == creator_id))
    if balance is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    return balance


async def grant_minutes(
    creator_id: uuid.UUID,
    minutes: int,
    reason: str,
    session: AsyncSession,
    *,
    pack_id: str = "grant",
    stripe_session_id: str | None = None,
    price_cents: int = 0,
) -> None:
    """Idempotently add minutes to a creator's balance and record the grant.

    Money-credit idempotency mirrors deduct_for_video (Issue 64): when
    ``stripe_session_id`` is set it is the idempotency key (UNIQUE on MinutePack).
    Stripe delivers ``checkout.session.completed`` at-least-once and can deliver it
    concurrently; a fast-path check plus a SAVEPOINT with IntegrityError-catch makes
    a duplicate delivery a clean no-op instead of an uncaught 500. Non-keyed grants
    (free trial, manual; stripe_session_id=None) are one-shot and unaffected.

    Both the MinutePack INSERT and the balance UPDATE occur inside a SAVEPOINT —
    either both land or neither does. Caller commits the outer transaction.
    """
    # Fast-path: already granted for this Stripe session? Skip without a SAVEPOINT.
    if stripe_session_id is not None:
        existing = await session.scalar(
            select(MinutePack.id).where(MinutePack.stripe_session_id == stripe_session_id)
        )
        if existing is not None:
            logger.info("billing grant skip (already granted) session=%s", stripe_session_id)
            return

    try:
        async with session.begin_nested():
            session.add(
                MinutePack(
                    creator_id=creator_id,
                    pack_id=pack_id,
                    minutes_granted=minutes,
                    price_cents=price_cents,
                    stripe_session_id=stripe_session_id,
                    reason=reason,
                    granted_at=datetime.now(UTC),
                )
            )
            await session.flush()  # forces the INSERT — surfaces UNIQUE conflicts now
            await session.execute(
                update(Creator)
                .where(Creator.id == creator_id)
                .values(minutes_balance=Creator.minutes_balance + minutes)
            )
    except IntegrityError:
        if stripe_session_id is None:
            # Non-keyed grant (free trial / manual): there is no UNIQUE to race on, so
            # an IntegrityError here is a *real* fault (e.g. the creator row is gone),
            # not a duplicate delivery. Don't swallow it — that would silently drop a
            # legitimate grant (a new beta user getting 0 trial minutes). (Issue 76)
            raise
        # Concurrent duplicate delivery won the UNIQUE(stripe_session_id) race — no-op.
        logger.info("billing grant race skip session=%s", stripe_session_id)
        return

    logger.info("billing grant creator=%s minutes=%d reason=%s", creator_id, minutes, reason)


async def deduct_for_video(
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    duration_s: float,
    session: AsyncSession,
) -> int:
    """Idempotently deduct minutes for a video.

    The UNIQUE(video_id) constraint on MinuteDeduction is the idempotency key.
    Returns minutes deducted on the first call; 0 on every subsequent call for the
    same video_id (whether sequential or concurrent).

    Raises HTTPException(402) if the balance is insufficient — the deduction
    record is rolled back via SAVEPOINT, so a 402 leaves the ledger clean.

    Both the deduction-record INSERT and the balance UPDATE occur inside a
    SAVEPOINT (session.begin_nested) — either both succeed or neither does.
    Caller is responsible for committing the outer transaction.
    """
    minutes = video_minutes(duration_s)

    # Fast-path: already charged? Skip without opening a SAVEPOINT.
    existing = await session.scalar(
        select(MinuteDeduction.id).where(MinuteDeduction.video_id == video_id)
    )
    if existing is not None:
        return 0

    try:
        async with session.begin_nested():
            session.add(
                MinuteDeduction(
                    video_id=video_id,
                    creator_id=creator_id,
                    minutes_deducted=minutes,
                    duration_s=duration_s,
                )
            )
            await session.flush()  # forces the INSERT — surfaces UNIQUE conflicts now

            result = await session.execute(
                update(Creator)
                .where(Creator.id == creator_id, Creator.minutes_balance >= minutes)
                .values(minutes_balance=Creator.minutes_balance - minutes)
                .returning(Creator.minutes_balance)
            )
            row = result.fetchone()
            if row is None:
                # Insufficient balance — SAVEPOINT rolls back on exception, undoing the INSERT.
                raise HTTPException(
                    status_code=402,
                    detail=(
                        "Insufficient minutes balance. "
                        "Purchase a pack at /pricing to continue processing."
                    ),
                )
    except IntegrityError:
        # Concurrent retry won the UNIQUE(video_id) race — idempotent skip.
        return 0

    remaining = row[0]
    logger.info(
        "billing deduct video=%s creator=%s minutes=%d remaining=%d",
        video_id,
        creator_id,
        minutes,
        remaining,
    )

    # Trigger 6: balance-low notification (Issue 244; enqueue moved post-commit
    # in Issue 352 Batch B). Fires only when the post-deduct balance is at or
    # below the threshold. Staged in session.info and enqueued by the
    # after_commit listener above — never before the outer transaction commits,
    # so a rollback after this function returns cannot send a notification for
    # a deduction that never persisted. entity_id = str(video_id) so the dedupe
    # key prevents duplicate notifications for the same video (at-least-once
    # Celery delivery), while still allowing a new notification on the next
    # video that crosses the threshold.
    from config import settings

    if remaining <= settings.LOW_BALANCE_THRESHOLD_MINUTES:
        session.info.setdefault(_PENDING_BALANCE_LOW_KEY, []).append(
            (str(creator_id), str(video_id))
        )

    return minutes


async def _trial_expired(creator_id: uuid.UUID, session: AsyncSession) -> bool:
    """Issue 126 — true iff the creator has a `trial_ends_at` that's already
    in the past. NULL `trial_ends_at` (legacy creators) is treated as "no
    trial" — they were never on one, so the 402 falls back to the generic
    "purchase a pack" copy."""
    row = await session.scalar(select(Creator.trial_ends_at).where(Creator.id == creator_id))
    if row is None:
        return False
    if row.tzinfo is None:
        row = row.replace(tzinfo=UTC)
    return row < datetime.now(UTC)


def _trial_ended_402_detail() -> str:
    """Standalone so the structural test in test_issue_126.py can pin the copy
    without importing the route handlers."""
    return "Your free trial has ended. Add minutes at /pricing to continue."


async def check_positive_balance(creator_id: uuid.UUID, session: AsyncSession) -> None:
    """Pre-flight guard: raises 402 if the creator has no minutes remaining.

    Used where the operation does not deduct minutes itself (e.g. /clips/render)
    — the gate is a usage floor, not a per-call cost check. For deducting paths
    (e.g. /videos/upload) use ``check_balance_for_minutes`` instead.

    Issue 126: differentiated copy when the trial expired AND balance hit zero,
    so the next action ("buy a pack") is unambiguous instead of generic.
    """
    balance = await get_balance(creator_id, session)
    if balance <= 0:
        if await _trial_expired(creator_id, session):
            detail = _trial_ended_402_detail()
        else:
            detail = "No minutes remaining. Purchase a pack at /pricing to process videos."
        raise HTTPException(status_code=402, detail=detail)


async def check_balance_for_minutes(
    creator_id: uuid.UUID, minutes_needed: int, session: AsyncSession
) -> None:
    """Pre-flight guard: raises 402 if the creator can't cover *minutes_needed*.

    Mirrors the predicate ``deduct_for_video`` enforces internally
    (``balance >= minutes``) — see ``billing/ledger.py:144``. Without this
    pre-check the upload path silently 402s inside the Celery task, leaving the
    user with a "failed" video and no actionable message. (Issue 89 — SEV-1
    spawned by Issue 88's display-vs-filter audit.)

    The user-facing 402 surfaces the concrete gap (needed vs. available) so the
    response copy is actionable rather than generic "Insufficient balance".
    Issue 126: when balance has hit zero AND the trial has expired, override
    with the trial-ended copy so the user knows trial is the reason, not
    mid-use exhaustion.
    """
    balance = await get_balance(creator_id, session)
    if balance < minutes_needed:
        if balance <= 0 and await _trial_expired(creator_id, session):
            detail = _trial_ended_402_detail()
        else:
            detail = (
                f"This video needs {minutes_needed} minutes; you have {balance}. "
                f"Purchase a pack at /pricing to continue."
            )
        raise HTTPException(status_code=402, detail=detail)
