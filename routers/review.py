"""
Feedback capture: upvote / downvote / skip / trim / format.
Each action persists to clip_feedback and is used by the preference model.
"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import creator_key, limiter
from models import Clip, ClipFeedback, Creator, FeedbackAction

router = APIRouter(prefix="/clips", tags=["review"])
logger = logging.getLogger(__name__)

# Issue 235 — the three actions that constitute a "keep" (the activation event).
# upvote = explicit approval; trim = editorial keep; format = deliberate render.
# downvote and skip are rejections; they are not activation signals.
_KEEP_ACTIONS: frozenset[FeedbackAction] = frozenset(
    {FeedbackAction.upvote, FeedbackAction.trim, FeedbackAction.format}
)


class FeedbackOut(BaseModel):
    id: str
    action: str


class FeedbackRequest(BaseModel):
    action: FeedbackAction
    trim_start_s: float | None = None
    trim_end_s: float | None = None
    chosen_format: str | None = None
    # Issue 118: structured multi-select feedback tags.
    # Approve tags: "titles_fit_style", "editing_matches_pace", "good_hook", "right_length"
    # Deny tags:   "editing_mismatch", "off_brand_topic", "bad_hook", "wrong_length"
    feedback_tags: list[str] | None = None
    # Free-text "Other" note captured alongside tags.
    feedback_note: str | None = None

    @field_validator("action", mode="before")
    @classmethod
    def coerce_action(cls, v: str) -> FeedbackAction:
        return FeedbackAction(v)


async def _is_first_keep(session: AsyncSession, creator_id: uuid.UUID) -> bool:
    """Return True if this creator has never previously submitted a keep action.

    A "keep" is any of {upvote, trim, format} — the three actions that signal
    "this clip is good enough to use."  The check is idempotent: if a prior
    keep already exists in clip_feedback, we do not fire clip_kept again.

    Issue 235 — activation event idempotency guard.
    """
    result = await session.execute(
        select(
            exists().where(
                ClipFeedback.creator_id == creator_id,
                ClipFeedback.action.in_(list(_KEEP_ACTIONS)),
            )
        )
    )
    return not result.scalar()


@router.post("/{clip_id}/feedback", status_code=201, response_model=FeedbackOut)
@limiter.limit("120/minute", key_func=creator_key)
async def submit_feedback(
    request: Request,
    clip_id: uuid.UUID,
    body: FeedbackRequest,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record a feedback action for a clip."""
    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")

    # Issue 235 — check idempotency BEFORE the commit so the new row is not
    # included in the existence query (the session hasn't flushed yet).
    is_activation = body.action in _KEEP_ACTIONS and await _is_first_keep(
        session, creator.id
    )

    feedback = ClipFeedback(
        clip_id=clip_id,
        creator_id=creator.id,
        action=body.action,
        trim_start_s=body.trim_start_s,
        trim_end_s=body.trim_end_s,
        chosen_format=body.chosen_format,
        feedback_tags=body.feedback_tags or None,
        feedback_note=body.feedback_note or None,
    )
    session.add(feedback)
    await session.commit()
    await session.refresh(feedback)

    from observability import log_event

    log_event(
        "clip_feedback_submitted",
        creator_id=str(creator.id),
        clip_id=str(clip_id),
        action=body.action.value,
    )

    # Issue 235 — emit clip_kept (ACTIVATION EVENT) on first keep per creator.
    # Best-effort: record_event never raises; a telemetry failure must not block
    # the response.  Scheduled on the running event loop via ensure_future.
    if is_activation:
        from event_log import record_event

        asyncio.ensure_future(
            record_event(
                source="backend",
                event="clip_kept",
                creator_id=creator.id,
                extra={"action": body.action.value},
            )
        )
        logger.info(
            "clip_kept activation event: creator=%s clip=%s action=%s",
            creator.id,
            clip_id,
            body.action.value,
        )

    # Retrain the creator's preference model so ranking adapts to this feedback.
    # The task self-debounces (no-op without new trainable labels), so enqueuing
    # on every feedback write is cheap. (Issue 60)
    from worker.tasks import retrain_preference

    await asyncio.to_thread(retrain_preference.delay, str(creator.id))

    return {"id": str(feedback.id), "action": feedback.action.value}
