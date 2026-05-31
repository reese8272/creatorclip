"""
Issue 57 — Automatic refund on terminal ingest failure.

When a Celery ingest-chain task exhausts its retries, refund the minutes that
were deducted for the video. The refund is recorded as a compensating
`MinutePack` row with `reason="refund"` and `pack_id=f"refund:{video_id}"`,
preserving the existing immutable-ledger invariant (no row mutation on either
`MinuteDeduction` or earlier `MinutePack` entries).

Idempotency (Wave-4 Fix 2): the DB-level guarantee is a partial UNIQUE index
on ``minute_packs(pack_id) WHERE reason = 'refund'`` (migration 0013). A
concurrent duplicate refund attempt loses the UNIQUE race and surfaces as an
``IntegrityError`` from ``grant_minutes``'s SAVEPOINT — caught here as a clean
no-op. The previous read-then-write SELECT guard was a TOCTOU race
(``task_acks_late=True`` + worker preemption could deliver two ``on_failure``
callbacks concurrently); the partial UNIQUE closes it structurally.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import db
from billing.ledger import grant_minutes
from models import MinuteDeduction

logger = logging.getLogger(__name__)


def _refund_pack_id(video_id: uuid.UUID) -> str:
    return f"refund:{video_id}"


async def refund_for_video(video_id: uuid.UUID) -> int:
    """Refund the minutes deducted for *video_id*.

    Returns the number of minutes refunded. Returns 0 when:
      - no deduction exists for this video (failure happened pre-deduct), or
      - a concurrent duplicate refund lost the UNIQUE race (idempotent no-op).

    Uses ``AdminSessionLocal`` (BYPASSRLS): refund is a system action — there
    is no per-creator context on the Celery ``on_failure`` callback to set
    ``session.info["creator_id"]``, so an app-role session would have RLS
    silently drop the ``MinuteDeduction`` SELECT to zero rows once the prod
    role split flips. This matches the rest of the worker surface
    (``worker/tasks.py``).
    """
    async with db.AdminSessionLocal() as session:
        deduction = await session.scalar(
            select(MinuteDeduction).where(MinuteDeduction.video_id == video_id)
        )
        if deduction is None:
            logger.info("No deduction to refund for video %s", video_id)
            return 0

        try:
            await grant_minutes(
                creator_id=deduction.creator_id,
                minutes=deduction.minutes_deducted,
                reason="refund",
                session=session,
                pack_id=_refund_pack_id(video_id),
                price_cents=0,
            )
            await session.commit()
        except IntegrityError:
            # Wave-4 Fix 2: the partial UNIQUE index uq_minute_packs_refund_pack_id
            # caught a concurrent duplicate refund. The SAVEPOINT inside
            # grant_minutes already rolled back; clean up the outer transaction
            # and return 0 — idempotent no-op matches deduct_for_video's UNIQUE
            # race handling pattern.
            await session.rollback()
            logger.info(
                "Concurrent refund race no-op for video %s (pack_id UNIQUE caught)",
                video_id,
            )
            return 0

        logger.info(
            "Refunded %d minutes to creator %s for failed video %s",
            deduction.minutes_deducted,
            deduction.creator_id,
            video_id,
        )
        return deduction.minutes_deducted
