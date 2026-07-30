"""Video-level style review (Issue 370).

The standalone Review tool lets a creator watch ANY of their videos and record
what they like/dislike about the style — a valence plus structured tags and/or
a free-text note. This is the video-level counterpart of the per-clip
``POST /clips/{clip_id}/feedback``; rows feed the style distiller (Issue 371).
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import creator_key, limiter
from models import Creator, Video, VideoFeedback, VideoSentiment
from routers._owned import get_owned

router = APIRouter(prefix="/videos", tags=["review"])
logger = logging.getLogger(__name__)

_NOTE_MAX_LEN = 2000
# Tags later feed an LLM prompt (Issue 371) — cap count and length here, unlike
# the clip endpoint which predates the distiller. Membership is deliberately
# not enforced (taxonomy iterates without deploy lockstep, matching clips).
_MAX_TAGS = 10
_MAX_TAG_LEN = 64


class VideoFeedbackRequest(BaseModel):
    sentiment: VideoSentiment
    feedback_tags: list[str] | None = None
    feedback_note: str | None = Field(default=None, max_length=_NOTE_MAX_LEN)

    @field_validator("sentiment", mode="before")
    @classmethod
    def coerce_sentiment(cls, v: str) -> VideoSentiment:
        return VideoSentiment(v)

    @field_validator("feedback_tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > _MAX_TAGS:
            raise ValueError(f"at most {_MAX_TAGS} tags per review")
        for tag in v:
            if not tag or len(tag) > _MAX_TAG_LEN:
                raise ValueError(f"each tag must be 1-{_MAX_TAG_LEN} characters")
        return v

    @model_validator(mode="after")
    def require_substance(self) -> "VideoFeedbackRequest":
        # A bare like/dislike carries no style signal for the distiller —
        # require at least one tag or a note.
        if not self.feedback_tags and not (self.feedback_note or "").strip():
            raise ValueError("provide at least one tag or a note — what about the style?")
        return self


class VideoFeedbackOut(BaseModel):
    id: str
    video_id: str
    sentiment: str
    feedback_tags: list[str] | None
    feedback_note: str | None
    created_at: str


class VideoFeedbackListOut(BaseModel):
    items: list[VideoFeedbackOut]


def _feedback_response(row: VideoFeedback) -> dict:
    return {
        "id": str(row.id),
        "video_id": str(row.video_id),
        "sentiment": row.sentiment.value,
        "feedback_tags": row.feedback_tags,
        "feedback_note": row.feedback_note,
        "created_at": row.created_at.isoformat(),
    }


@router.post("/{video_id}/feedback", status_code=201, response_model=VideoFeedbackOut)
@limiter.limit("120/minute", key_func=creator_key)
async def submit_video_feedback(
    request: Request,
    video_id: uuid.UUID,
    body: VideoFeedbackRequest,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record a video-level style review. Pure DB write — no LLM in-path
    (the distiller runs async and self-debounces), so no flag/budget deps."""
    await get_owned(session, Video, video_id, creator.id, detail="Video not found")

    row = VideoFeedback(
        creator_id=creator.id,
        video_id=video_id,
        sentiment=body.sentiment,
        feedback_tags=body.feedback_tags or None,
        feedback_note=(body.feedback_note or "").strip() or None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    from observability import log_event

    log_event(
        "video_feedback_submitted",
        creator_id=str(creator.id),
        video_id=str(video_id),
        sentiment=body.sentiment.value,
    )

    return _feedback_response(row)


@router.get("/{video_id}/feedback", response_model=VideoFeedbackListOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_video_feedback(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The creator's own style notes for this video, newest first (cap 50)."""
    await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    result = await session.execute(
        select(VideoFeedback)
        .where(VideoFeedback.video_id == video_id, VideoFeedback.creator_id == creator.id)
        .order_by(VideoFeedback.created_at.desc())
        .limit(50)
    )
    return {"items": [_feedback_response(r) for r in result.scalars()]}
