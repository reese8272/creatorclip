"""Thumbnail pattern analysis + concept generator endpoints (Issue 129)."""

import asyncio
import contextlib
import json
import logging
import uuid as _uuid_mod
from collections.abc import Awaitable, Callable

import redis.asyncio as aredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import check_positive_balance
from config import settings
from db import get_session
from flags import require_flag
from limiter import BRIEF_DAILY_LIMIT, LLM_DAILY_LIMIT, creator_key, limiter
from models import Creator, CreatorDna, DnaStatus, Transcript, Video

router = APIRouter(prefix="/creators", tags=["thumbnails"])
logger = logging.getLogger(__name__)

# Module-level Redis singleton for pattern cache.
_aio_redis: aredis.Redis | None = None

# Single-flight lock around the billed multimodal pattern analysis (SEV1 #3).
# Without it, N concurrent first-hits (or a degraded cache) each fire a separate
# vision call. TTL must comfortably exceed one analyze_thumbnail_patterns round
# trip (the underlying Anthropic client uses a 120s timeout); waiters poll the
# cache briefly, then fall through fail-open so a stuck holder never blocks them.
_PATTERNS_LOCK_TTL_S = 130
_PATTERNS_WAIT_COUNT = 3
_PATTERNS_WAIT_SLEEP_S = 0.4
# Compare-and-delete so we never release a lock another request now holds.
_LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


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


async def _read_patterns_cache(redis: aredis.Redis, cache_key: str) -> dict | None:
    """Return the cached patterns dict (with cached=True) or None on miss/error.

    Fail-open: any Redis or decode error is treated as a miss so a degraded cache
    never 500s the endpoint."""
    try:
        raw = await redis.get(cache_key)
    except aredis.RedisError as exc:
        logger.warning("thumbnail_patterns cache read failed err=%s", exc)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    data["cached"] = True
    return data


async def _compute_patterns_single_flight(
    redis: aredis.Redis,
    *,
    lock_id: object,
    cache_key: str,
    compute: Callable[[], Awaitable[dict]],
    cache_ttl: int,
) -> dict:
    """Run ``compute`` under a per-creator single-flight lock (SEV1 #3).

    Only the lock holder runs the (billed, multimodal) ``compute``; concurrent
    callers poll the cache briefly for the holder's result, then fall through
    and compute themselves so a stuck holder never blocks them. Fully fail-open
    on Redis errors — the rate limit still bounds total exposure. ``compute`` is
    a zero-arg async callable awaited on the loop (AsyncAnthropic — Issue 82a);
    it returns the patterns dict.
    """
    lock_key = f"thumbnail-patterns-lock:{lock_id}"
    lock_token = str(_uuid_mod.uuid4())
    try:
        acquired = await redis.set(lock_key, lock_token, nx=True, ex=_PATTERNS_LOCK_TTL_S)
    except aredis.RedisError as exc:
        logger.warning("thumbnail_patterns lock acquire failed id=%s err=%s", lock_id, exc)
        acquired = True

    if not acquired:
        for _ in range(_PATTERNS_WAIT_COUNT):
            await asyncio.sleep(_PATTERNS_WAIT_SLEEP_S)
            cached = await _read_patterns_cache(redis, cache_key)
            if cached is not None:
                return cached
        # Holder still working — proceed rather than hang the request.

    try:
        patterns = await compute()
        try:
            await redis.setex(cache_key, cache_ttl, json.dumps(patterns))
        except aredis.RedisError as exc:
            logger.warning("thumbnail_patterns cache write failed id=%s err=%s", lock_id, exc)
    finally:
        # Best-effort release; if Redis is unreachable the lock expires via TTL.
        if acquired:
            with contextlib.suppress(aredis.RedisError):
                await redis.eval(_LUA_RELEASE_LOCK, 1, lock_key, lock_token)  # type: ignore[misc]  # SDK/stub typing lag (Issue 78c)

    patterns["cached"] = False
    return patterns


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
@limiter.limit("10/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
@limiter.limit(BRIEF_DAILY_LIMIT, key_func=creator_key)
async def get_thumbnail_patterns(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Analyze the creator's top-performing thumbnails and extract visual patterns.

    Results are Redis-cached for 24 hours (key: thumbnail_patterns:{creator_id}).
    Uses Claude multimodal to extract face presence, emotion, text overlay style,
    colors, and composition patterns. No new YouTube API scopes needed — thumbnail
    URLs are public at i.ytimg.com/vi/{id}/hqdefault.jpg.

    Rate-limited to 10/hour per creator and guarded by a per-creator single-flight
    lock so concurrent first-hits (or a degraded cache) cannot fan out into
    multiple billed multimodal calls (SEV1 #3).
    """
    await check_positive_balance(creator.id, session)

    from knowledge.thumbnails import (
        PATTERNS_CACHE_KEY_PREFIX,
        PATTERNS_CACHE_TTL,
        analyze_thumbnail_patterns,
    )

    cache_key = f"{PATTERNS_CACHE_KEY_PREFIX}{creator.id}"

    redis = _get_redis()
    cached = await _read_patterns_cache(redis, cache_key)
    if cached is not None:
        return cached

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

    youtube_ids: list[str] = [
        uid
        for uid in (
            await session.execute(
                select(Video.youtube_video_id).where(
                    Video.id.in_(uuid_list),
                    Video.creator_id == creator.id,
                    Video.youtube_video_id.isnot(None),
                )
            )
        ).scalars()
        if uid is not None
    ]

    if not youtube_ids:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve YouTube video IDs for pattern analysis.",
        )

    channel_title = creator.channel_title or "Unknown Channel"
    return await _compute_patterns_single_flight(
        redis,
        lock_id=creator.id,
        cache_key=cache_key,
        compute=lambda: analyze_thumbnail_patterns(youtube_ids, channel_title),  # coroutine
        cache_ttl=PATTERNS_CACHE_TTL,
    )


@router.post(
    "/me/videos/{video_id}/thumbnail-concepts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ThumbnailConceptsQueuedOut,
    # Kill switch (Issue 284): 503 when the llm_generation flag is off.
    dependencies=[Depends(require_flag("llm_generation"))],
)
@limiter.limit("10/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
@limiter.limit(BRIEF_DAILY_LIMIT, key_func=creator_key)
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
    await check_positive_balance(creator.id, session)

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

    task = await asyncio.to_thread(
        generate_thumbnail_concepts_task.delay, str(creator.id), str(video.id)
    )

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
