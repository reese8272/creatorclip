"""
Issue 57 — Automatic refund on terminal ingest failure.

When a Celery ingest-chain task exhausts its retries, refund the minutes that
were deducted for the video. The refund is recorded as a compensating
`MinutePack` row with `reason="refund"` and `pack_id=f"refund:{video_id}"`,
preserving the existing immutable-ledger invariant (no row mutation on either
`MinuteDeduction` or earlier `MinutePack` entries).

Idempotency: keyed on `pack_id="refund:<video_id>"`. A read-then-write check
guards against duplicate `on_failure` invocations within the same task chain.
There is no UNIQUE constraint on `(reason, pack_id)`, so a hard race between
two distinct task instances failing concurrently for the same video could
double-refund — that scenario is not reachable in the current pipeline (the
chain is single-runner per video), and is flagged in `docs/DECISIONS.md`.
"""

import logging
import uuid

from sqlalchemy import select

import db
from billing.ledger import grant_minutes
from models import MinuteDeduction, MinutePack

logger = logging.getLogger(__name__)


def _refund_pack_id(video_id: uuid.UUID) -> str:
    return f"refund:{video_id}"


async def refund_for_video(video_id: uuid.UUID) -> int:
    """Refund the minutes deducted for *video_id*.

    Returns the number of minutes refunded. Returns 0 when:
      - no deduction exists for this video (failure happened pre-deduct), or
      - a refund row already exists (idempotent no-op on retry of on_failure).

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

        pack_id = _refund_pack_id(video_id)
        existing_refund = await session.scalar(
            select(MinutePack.id).where(MinutePack.pack_id == pack_id)
        )
        if existing_refund is not None:
            logger.info("Refund already recorded for video %s", video_id)
            return 0

        await grant_minutes(
            creator_id=deduction.creator_id,
            minutes=deduction.minutes_deducted,
            reason="refund",
            session=session,
            pack_id=pack_id,
            price_cents=0,
        )
        await session.commit()
        logger.info(
            "Refunded %d minutes to creator %s for failed video %s",
            deduction.minutes_deducted,
            deduction.creator_id,
            video_id,
        )
        return deduction.minutes_deducted
