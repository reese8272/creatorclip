"""Video performance analysis endpoint (Issue 121)."""

import logging
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import creator_key, limiter
from models import Creator, Video, VideoMetrics

router = APIRouter(prefix="/creators", tags=["analysis"])
logger = logging.getLogger(__name__)

# Accepts a bare 11-char ID, youtu.be/ID, or youtube.com/watch?v=ID
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_URL_PATTERNS = [
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"/shorts/([A-Za-z0-9_-]{11})"),
]


def _extract_video_id(raw: str) -> str | None:
    raw = raw.strip()
    if _YT_ID_RE.match(raw):
        return raw
    for pattern in _YT_URL_PATTERNS:
        m = pattern.search(raw)
        if m:
            return m.group(1)
    return None


class AnalysisRequest(BaseModel):
    youtube_url: str = Field(..., description="YouTube video URL or 11-char video ID")
    query: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Your question about why the video performed the way it did",
    )


class AnalysisQueuedOut(BaseModel):
    task_id: str
    stream_url: str | None = None
    video_title: str | None = None
    has_metrics: bool


@router.post(
    "/me/video-analysis",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisQueuedOut,
)
@limiter.limit("20/hour", key_func=creator_key)
async def start_video_analysis(
    request: Request,
    body: AnalysisRequest = Body(...),
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a video performance analysis. Returns task_id + stream_url for SSE.

    The analysis runs in a Celery task (mirrors the improvement-brief 202+SSE
    pattern from Issue 78d/92). The creator's DNA + any available metrics for
    the video feed the prompt; videos not yet in the catalog get a
    metadata-only analysis.
    """
    if not creator.channel_id:
        raise HTTPException(status_code=400, detail="Channel not connected")

    youtube_video_id = _extract_video_id(body.youtube_url)
    if not youtube_video_id:
        raise HTTPException(
            status_code=422,
            detail="Could not extract a valid YouTube video ID from the URL.",
        )

    # Check if this video is already in the creator's catalog — richer context
    # when it is (metrics, retention curve, transcript). Per-creator isolation
    # enforced here and in the worker task.
    video = await session.scalar(
        select(Video).where(
            Video.creator_id == creator.id,
            Video.youtube_video_id == youtube_video_id,
        )
    )
    video_id_str: str | None = None
    video_title: str | None = None
    has_metrics = False

    if video:
        video_id_str = str(video.id)
        video_title = video.title
        has_metrics = bool(
            await session.scalar(
                select(VideoMetrics.video_id)
                .where(VideoMetrics.video_id == video.id)
                .limit(1)
            )
        )

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import generate_video_analysis as generate_video_analysis_task

    task = generate_video_analysis_task.delay(
        str(creator.id), youtube_video_id, body.query, video_id_str
    )

    stream_url: str | None = f"/tasks/{task.id}/events"
    try:
        await progress.aset_owner(task.id, str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "video_analysis aset_owner failed task=%s err=%s",
            task.id,
            exc,
        )
        stream_url = None

    logger.info(
        "Video analysis queued creator=%s video=%s has_metrics=%s task=%s",
        creator.id,
        youtube_video_id,
        has_metrics,
        task.id,
    )
    return {
        "task_id": task.id,
        "stream_url": stream_url,
        "video_title": video_title,
        "has_metrics": has_metrics,
    }
