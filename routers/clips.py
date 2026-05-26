import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from config import settings
from db import get_session
from limiter import limiter
from models import Clip, Creator, IngestStatus, RenderStatus, Signals, Transcript, Video

router = APIRouter(prefix="/videos", tags=["clips"])
clips_router = APIRouter(prefix="/clips", tags=["clips"])
logger = logging.getLogger(__name__)


def _clip_response(clip: Clip) -> dict:
    sj = clip.signals_jsonb or {}
    return {
        "id": str(clip.id),
        "video_id": str(clip.video_id),
        "setup_start_s": clip.setup_start_s,
        "start_s": clip.start_s,
        "end_s": clip.end_s,
        "peak_s": clip.peak_s,
        "score": clip.score,
        "rank": clip.rank,
        "principle": sj.get("principle", ""),
        "reasoning": sj.get("reasoning", ""),
        "render_status": clip.render_status.value,
        "render_uri": clip.render_uri,
    }


@router.post("/{video_id}/clips/generate")
@limiter.limit("10/hour")
async def generate_clips(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Extract, score, and rank clip candidates for a fully-ingested video."""
    video = await session.get(Video, video_id)
    if not video or video.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.ingest_status != IngestStatus.done:
        raise HTTPException(status_code=400, detail="Video is not fully ingested yet")

    signals = await session.get(Signals, video_id)
    if not signals:
        raise HTTPException(status_code=400, detail="Signals not available for this video")

    transcript = await session.get(Transcript, video_id)
    transcript_segments = transcript.segments_jsonb.get("segments", []) if transcript else []

    from dna.profile import get_active

    dna_profile = await get_active(session, creator.id)
    dna_brief = dna_profile.brief_text if dna_profile else None

    from clip_engine.ranking import generate_and_rank_clips

    clips = await generate_and_rank_clips(
        session=session,
        video_id=video_id,
        creator_id=creator.id,
        timeline=signals.timeline_jsonb,
        dna_brief=dna_brief,
        transcript_segments=transcript_segments,
        max_candidates=settings.CLIPS_PER_VIDEO_DEFAULT,
    )

    return {"clips": [_clip_response(c) for c in clips]}


@router.get("/{video_id}/clips")
@limiter.limit("120/minute")
async def list_clips(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return ranked clips for a video."""
    video = await session.get(Video, video_id)
    if not video or video.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Video not found")

    result = await session.execute(
        select(Clip)
        .where(Clip.video_id == video_id, Clip.creator_id == creator.id)
        .order_by(Clip.rank)
    )
    clips = list(result.scalars())
    return {"clips": [_clip_response(c) for c in clips]}


# ── Clip-level actions ────────────────────────────────────────────────────────


@clips_router.post("/{clip_id}/render", status_code=202)
@limiter.limit("20/hour")
async def render_clip(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a render job for the clip. Returns task_id."""
    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.render_status == RenderStatus.running:
        raise HTTPException(status_code=409, detail="Render already in progress")

    from worker.tasks import render_clip as render_task

    task = render_task.delay(str(clip_id))
    return {"task_id": task.id, "status": "queued"}


@clips_router.get("/{clip_id}")
@limiter.limit("120/minute")
async def get_clip(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a single clip by ID."""
    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    return _clip_response(clip)
