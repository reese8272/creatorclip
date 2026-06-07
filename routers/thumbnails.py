"""Thumbnail pattern analysis + concept generator endpoints (Issue 129)."""

import json
import logging
import uuid as _uuid_mod

import redis.asyncio as aredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from config import settings
from db import get_session
from limiter import creator_key, limiter
from models import Creator, CreatorDna, DnaStatus, Transcript, Video

router = APIRouter(prefix="/creators", tags=["thumbnails"])
logger = logging.getLogger(__name__)

# Module-level Redis singleton for pattern cache.
_aio_redis: aredis.Redis | None = None


def _get_redis() -> aredis.Redis:
    global _aio_redis
    if _aio_redis is None:
        _aio_redis = aredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    return _aio_redis


class ThumbnailPatternsOut(BaseModel):
    face_present: str
    dominant_emotions: list[str]
    text_overlay_style: str
    typical_colors: str
    composition_pattern: str
    channel_thumbnail_signature: str
    cached: bool = False


class ThumbnailConceptsQueuedOut(BaseModel):
    task_id: str
    stream_url: str | None = None


@router.get(
    "/me/thumbnail-patterns",
    response_model=ThumbnailPatternsOut,
)
async def get_thumbnail_patterns(
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Analyze the creator's top-performing thumbnails and extract visual patterns.

    Results are Redis-cached for 24 hours (key: thumbnail_patterns:{creator_id}).
    Uses Claude multimodal to extract face presence, emotion, text overlay style,
    colors, and composition patterns. No new YouTube API scopes needed — thumbnail
    URLs are public at i.ytimg.com/vi/{id}/hqdefault.jpg.
    """
    import asyncio

    from knowledge.thumbnails import (
        PATTERNS_CACHE_KEY_PREFIX,
        PATTERNS_CACHE_TTL,
        analyze_thumbnail_patterns,
    )

    cache_key = f"{PATTERNS_CACHE_KEY_PREFIX}{creator.id}"

    try:
        redis = _get_redis()
        cached_raw = await redis.get(cache_key)
        if cached_raw:
            data = json.loads(cached_raw)
            data["cached"] = True
            return data
    except Exception as exc:
        logger.warning("thumbnail_patterns cache read failed creator=%s err=%s", creator.id, exc)

    dna = await session.scalar(
        select(CreatorDna).where(
            CreatorDna.creator_id == creator.id,
            CreatorDna.status == DnaStatus.confirmed,
        )
    )
    if dna is None:
        raise HTTPException(
            status_code=400,
            detail="DNA profile not yet built or confirmed — complete onboarding first.",
        )

    top_ids: list[str] = dna.top_video_ids_jsonb or []
    if not top_ids:
        raise HTTPException(
            status_code=400,
            detail="No top-performing videos in DNA profile.",
        )

    # Resolve internal Video UUIDs → youtube_video_id
    try:
        uuid_list = [_uuid_mod.UUID(vid_id) for vid_id in top_ids[:10]]
    except ValueError:
        uuid_list = []

    youtube_ids = list(
        (
            await session.execute(
                select(Video.youtube_video_id).where(
                    Video.id.in_(uuid_list),
                    Video.creator_id == creator.id,
                    Video.youtube_video_id.isnot(None),
                )
            )
        ).scalars()
    )

    if not youtube_ids:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve YouTube video IDs for pattern analysis.",
        )

    patterns = await asyncio.to_thread(
        analyze_thumbnail_patterns,
        youtube_ids,
        creator.channel_title or "Unknown Channel",
    )

    try:
        redis = _get_redis()
        await redis.setex(cache_key, PATTERNS_CACHE_TTL, json.dumps(patterns))
    except Exception as exc:
        logger.warning("thumbnail_patterns cache write failed creator=%s err=%s", creator.id, exc)

    patterns["cached"] = False
    return patterns


@router.post(
    "/me/videos/{video_id}/thumbnail-concepts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ThumbnailConceptsQueuedOut,
)
@limiter.limit("10/hour", key_func=creator_key)
async def start_thumbnail_concepts(
    request: Request,
    video_id: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a thumbnail-concept generation task for a transcribed video.

    Returns 202 + task_id + stream_url. The Celery task streams token events
    and emits a terminal ``done`` event containing the concept objects.
    Results are ephemeral — no DB row is persisted (same pattern as Issue 128).
    """
    try:
        vid_uuid = _uuid_mod.UUID(video_id)
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
    from worker.tasks import generate_thumbnail_concepts as generate_thumbnail_concepts_task

    task = generate_thumbnail_concepts_task.delay(str(creator.id), str(video.id))

    stream_url: str | None = f"/tasks/{task.id}/events"
    try:
        await progress.aset_owner(task.id, str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "thumbnail_concepts aset_owner failed task=%s err=%s",
            task.id,
            exc,
        )
        stream_url = None

    logger.info(
        "Thumbnail concepts queued creator=%s video=%s task=%s",
        creator.id,
        video.id,
        task.id,
    )
    return {
        "task_id": task.id,
        "stream_url": stream_url,
    }
