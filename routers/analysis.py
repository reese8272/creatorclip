"""Video performance analysis endpoints (Issues 121, 130, 131)."""

import asyncio
import logging
import re
import uuid as _uuid_mod

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import check_positive_balance
from db import get_session
from flags import require_flag
from limiter import LLM_DAILY_LIMIT, creator_key, limiter
from models import Creator, RetentionCurve, Transcript, Video, VideoMetrics

# Router-level kill switch (Issue 284): every route here queues LLM analysis
# work, so the llm_generation flag gates the whole router with one dependency.
router = APIRouter(
    prefix="/creators",
    tags=["analysis"],
    dependencies=[Depends(require_flag("llm_generation"))],
)
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
    # Issue 125 — user-facing alias for `has_metrics`, populated to the same
    # value. The UI prefers this name; `has_metrics` stays for backward
    # compatibility with the Issue-121 consumer.
    analytics_available: bool


@router.post(
    "/me/video-analysis",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisQueuedOut,
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
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
    await check_positive_balance(creator.id, session)

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
                select(VideoMetrics.video_id).where(VideoMetrics.video_id == video.id).limit(1)
            )
        )

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import generate_video_analysis as generate_video_analysis_task

    task = await asyncio.to_thread(
        generate_video_analysis_task.delay,
        str(creator.id),
        youtube_video_id,
        body.query,
        video_id_str,
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
        "analytics_available": has_metrics,
    }


# ── Hook analyzer (Issue 130) ──────────────────────────────────────────────────


class HookAnalysisOut(BaseModel):
    """Union response for the hook-analysis endpoint.

    Returned 202 when queued (task_id + stream_url populated).
    Returned 200 when no retention data is available (status + message populated).
    """

    task_id: str | None = None
    stream_url: str | None = None
    status: str | None = None
    message: str | None = None


@router.post(
    "/me/videos/{video_id}/hook-analysis",
    response_model=HookAnalysisOut,
)
@limiter.limit("10/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
async def start_hook_analysis(
    request: Request,
    video_id: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Analyze the first-30s hook against the creator's own retention curves.

    Returns 200 + {"status": "no_data"} when no retention curve exists for
    this video. Returns 202 + task_id + stream_url when analysis is queued.
    """
    await check_positive_balance(creator.id, session)

    try:
        vid_uuid = _uuid_mod.UUID(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid video_id format.") from exc

    video = await session.scalar(
        select(Video).where(Video.id == vid_uuid, Video.creator_id == creator.id)
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")

    curve_count = await session.scalar(
        select(func.count(RetentionCurve.id)).where(RetentionCurve.video_id == vid_uuid)
    )
    if not curve_count:
        return JSONResponse(
            {
                "status": "no_data",
                "message": (
                    "Retention data not yet available for this video. "
                    "YouTube Analytics data is typically available 48–72 hours after publication."
                ),
            },
            status_code=200,
        )

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import analyze_hook as analyze_hook_task

    task = await asyncio.to_thread(analyze_hook_task.delay, str(creator.id), str(video.id))

    stream_url: str | None = f"/tasks/{task.id}/events"
    try:
        await progress.aset_owner(task.id, str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning("hook_analysis aset_owner failed task=%s err=%s", task.id, exc)
        stream_url = None

    logger.info(
        "Hook analysis queued creator=%s video=%s task=%s",
        creator.id,
        video.id,
        task.id,
    )
    return JSONResponse(
        {"task_id": task.id, "stream_url": stream_url},
        status_code=202,
    )


# ── Auto chapter markers (Issue 131) ──────────────────────────────────────────


class ChaptersQueuedOut(BaseModel):
    task_id: str
    stream_url: str | None = None


@router.post(
    "/me/videos/{video_id}/chapters",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ChaptersQueuedOut,
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
async def start_chapter_generation(
    request: Request,
    video_id: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate YouTube chapter markers from the transcript and signal timeline.

    Returns 202 + task_id + stream_url. The done event payload contains
    'chapters' (list) and 'description_block' (ready-to-paste string).
    """
    await check_positive_balance(creator.id, session)

    try:
        vid_uuid = _uuid_mod.UUID(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid video_id format.") from exc

    video = await session.scalar(
        select(Video).where(Video.id == vid_uuid, Video.creator_id == creator.id)
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")

    has_transcript = bool(
        await session.scalar(
            select(Transcript.video_id).where(Transcript.video_id == vid_uuid).limit(1)
        )
    )
    if not has_transcript:
        raise HTTPException(
            status_code=400,
            detail="Video has not been transcribed yet — wait for ingestion to complete.",
        )

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import generate_chapters as generate_chapters_task

    task = await asyncio.to_thread(generate_chapters_task.delay, str(creator.id), str(video.id))

    stream_url: str | None = f"/tasks/{task.id}/events"
    try:
        await progress.aset_owner(task.id, str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning("generate_chapters aset_owner failed task=%s err=%s", task.id, exc)
        stream_url = None

    logger.info(
        "Chapter generation queued creator=%s video=%s task=%s",
        creator.id,
        video.id,
        task.id,
    )
    return {"task_id": task.id, "stream_url": stream_url}
