"""Title suggestion endpoint (Issue 128)."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import check_positive_balance
from db import get_session
from limiter import LLM_DAILY_LIMIT, creator_key, limiter
from models import Creator, Transcript, Video

router = APIRouter(prefix="/creators", tags=["titles"])
logger = logging.getLogger(__name__)


class TitleSuggestionsQueuedOut(BaseModel):
    task_id: str
    stream_url: str | None = None
    video_title: str | None = None


@router.post(
    "/me/videos/{video_id}/titles",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TitleSuggestionsQueuedOut,
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
async def start_title_suggestions(
    request: Request,
    video_id: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a title-suggestion task for a video the creator has ingested.

    Returns 202 + task_id + stream_url. The task streams token events via
    SSE (same pattern as video analysis, Issue 121) and emits a terminal
    ``result`` event containing the top-5 TitleSuggestion objects before
    the final ``done`` event. Results are ephemeral — no DB row is persisted.
    """
    await check_positive_balance(creator.id, session)

    import uuid as _uuid

    try:
        vid_uuid = _uuid.UUID(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid video_id format.") from exc

    video = await session.scalar(
        select(Video).where(
            Video.id == vid_uuid,
            Video.creator_id == creator.id,
        )
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")

    has_transcript = bool(
        await session.scalar(
            select(Transcript.video_id).where(Transcript.video_id == video.id).limit(1)
        )
    )
    if not has_transcript:
        raise HTTPException(
            status_code=400,
            detail="Video has not been transcribed yet — wait for ingestion to complete.",
        )

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import generate_title_suggestions as generate_title_suggestions_task

    task = await asyncio.to_thread(
        generate_title_suggestions_task.delay, str(creator.id), str(video.id)
    )

    stream_url: str | None = f"/tasks/{task.id}/events"
    try:
        await progress.aset_owner(task.id, str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "title_suggestions aset_owner failed task=%s err=%s",
            task.id,
            exc,
        )
        stream_url = None

    logger.info(
        "Title suggestions queued creator=%s video=%s task=%s",
        creator.id,
        video.id,
        task.id,
    )
    return {
        "task_id": task.id,
        "stream_url": stream_url,
        "video_title": video.title,
    }
