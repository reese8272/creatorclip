"""
Feedback capture: upvote / downvote / skip / trim / format.
Each action persists to clip_feedback and is used by the preference model.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import limiter
from models import Clip, ClipFeedback, Creator, FeedbackAction

router = APIRouter(prefix="/clips", tags=["review"])
logger = logging.getLogger(__name__)


class FeedbackOut(BaseModel):
    id: str
    action: str


class FeedbackRequest(BaseModel):
    action: FeedbackAction
    trim_start_s: float | None = None
    trim_end_s: float | None = None
    chosen_format: str | None = None

    @field_validator("action", mode="before")
    @classmethod
    def coerce_action(cls, v: str) -> FeedbackAction:
        return FeedbackAction(v)


@router.post("/{clip_id}/feedback", status_code=201, response_model=FeedbackOut)
@limiter.limit("120/minute")
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

    feedback = ClipFeedback(
        clip_id=clip_id,
        creator_id=creator.id,
        action=body.action,
        trim_start_s=body.trim_start_s,
        trim_end_s=body.trim_end_s,
        chosen_format=body.chosen_format,
    )
    session.add(feedback)
    await session.commit()
    await session.refresh(feedback)

    logger.info("feedback: creator=%s clip=%s action=%s", creator.id, clip_id, body.action.value)

    # Retrain the creator's preference model so ranking adapts to this feedback.
    # The task self-debounces (no-op without new trainable labels), so enqueuing
    # on every feedback write is cheap. (Issue 60)
    from worker.tasks import retrain_preference

    retrain_preference.delay(str(creator.id))

    return {"id": str(feedback.id), "action": feedback.action.value}
