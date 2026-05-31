import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_key import get_current_creator_via_api_key
from auth import get_current_creator
from billing.ledger import check_balance_for_minutes, check_positive_balance, video_minutes
from config import settings
from db import get_session
from limiter import limiter
from models import Clip, Creator, IngestStatus, RenderStatus, Signals, Transcript, Video, VideoKind
from worker.storage import upload_file
from youtube.data_api import classify_video_kind
from youtube.ingest import probe_duration_s

router = APIRouter(prefix="/videos", tags=["clips"])
clips_router = APIRouter(prefix="/clips", tags=["clips"])
logger = logging.getLogger(__name__)


class ClipOut(BaseModel):
    id: str
    video_id: str
    setup_start_s: float | None
    start_s: float
    end_s: float
    peak_s: float | None
    score: float | None
    rank: int | None
    principle: str
    reasoning: str
    render_status: str
    render_uri: str | None


class ClipListOut(BaseModel):
    clips: list[ClipOut]


class RenderQueuedOut(BaseModel):
    task_id: str
    status: str
    stream_url: str | None = (
        None  # Issue 92: SSE endpoint for render progress. Wave-5 Fix 1 — Optional: None on Redis aset_owner failure.
    )


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


@router.post("/{video_id}/clips/generate", response_model=ClipListOut)
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


@router.get("/{video_id}/clips", response_model=ClipListOut)
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


@clips_router.post("/{clip_id}/render", status_code=202, response_model=RenderQueuedOut)
@limiter.limit("20/hour")
async def render_clip(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a render job for the clip. Returns task_id."""
    await check_positive_balance(creator.id, session)

    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.render_status == RenderStatus.running:
        raise HTTPException(status_code=409, detail="Render already in progress")

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import render_clip as render_task

    task = render_task.delay(str(clip_id))
    # Issue 92: use clip_id (not task.id) as the SSE stream key — the worker
    # task emits to task:{clip_id}:events for the same deterministic-lookup
    # reason as the upload chain (the frontend already has clip_id in URL).
    # Wave-5 Fix 1: same fail-open posture as the other aset_owner sites —
    # a Redis blip returns stream_url=None instead of 500-ing the request.
    # The render task is already enqueued and will run.
    stream_url: str | None = f"/tasks/{clip_id}/events"
    try:
        await progress.aset_owner(str(clip_id), str(creator.id))
    except _redis_pkg.RedisError as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "render aset_owner failed (Redis down?) clip_id=%s err=%s",
            clip_id,
            exc,
        )
        stream_url = None

    return {
        "task_id": task.id,
        "status": "queued",
        "stream_url": stream_url,
    }


# ── Issue 95: OBS companion app ingest endpoint ───────────────────────────


class ClipIngestedOut(BaseModel):
    """Response shape for POST /clips/ingest. Mirrors VideoLinkedOut so the
    companion app sees the same surface as a browser upload."""

    video_id: str
    status: str
    stream_url: str | None = None


def _obs_clip_youtube_id() -> str:
    """Synthetic video ID for OBS-sourced clips.

    The schema's UNIQUE(creator_id, youtube_video_id) constraint demands a
    non-null value here; real YouTube IDs are 11 chars of [A-Za-z0-9_-].
    A 16-char ``obs-<12-hex>`` synthetic ID is structurally distinguishable
    from a real YT ID (different length + prefix) and collision-free by
    construction. Fits inside the schema's ``String(32)``.
    """
    return f"obs-{uuid.uuid4().hex[:12]}"


@clips_router.post("/ingest", status_code=202, response_model=ClipIngestedOut)
@limiter.limit("20/hour")
async def ingest_clip(
    request: Request,
    file: UploadFile = File(...),
    clip_name: str | None = Form(default=None),
    creator: Creator = Depends(get_current_creator_via_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Accept a clip upload from the OBS companion app via API-key auth.

    This is the API-key counterpart to ``/videos/upload``. The companion
    app authenticates with ``Authorization: Bearer <api_key>``; same
    streaming-upload + ffprobe + balance-check + R2-PUT + start_pipeline
    flow as ``upload_video`` but without the YouTube ID (synthetic ID
    generated server-side) and without the dedup-existing-row check (each
    OBS clip is a fresh synthetic ID).

    Per-creator isolation: the bearer-auth dependency resolves the owning
    Creator and sets ``session.info["creator_id"]`` so RLS gates downstream
    queries (Issue 79). The Video row's ``creator_id`` is set from the
    same resolved Creator, never from any client-supplied identifier.
    """
    await check_positive_balance(creator.id, session)

    max_bytes = settings.UPLOAD_MAX_MB * 1024 * 1024
    chunk_size = 1 * 1024 * 1024  # 1 MB chunks — same as /videos/upload

    suffix = Path(file.filename or "clip.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    bytes_received = 0
    try:
        with tmp_path.open("wb") as fh:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                bytes_received += len(chunk)
                if bytes_received > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds {settings.UPLOAD_MAX_MB} MB limit",
                    )
                fh.write(chunk)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise

    duration_s = await asyncio.to_thread(probe_duration_s, tmp_path)
    kind = classify_video_kind(duration_s) if duration_s is not None else VideoKind.long

    if duration_s is not None:
        try:
            await check_balance_for_minutes(creator.id, video_minutes(duration_s), session)
        except HTTPException:
            tmp_path.unlink(missing_ok=True)
            raise

    youtube_video_id = _obs_clip_youtube_id()
    try:
        key = f"source/{creator.id}/{youtube_video_id}{suffix}"
        source_uri = await asyncio.to_thread(upload_file, tmp_path, key)
    finally:
        tmp_path.unlink(missing_ok=True)

    video = Video(
        creator_id=creator.id,
        youtube_video_id=youtube_video_id,
        title=clip_name or f"OBS clip {youtube_video_id}",
        kind=kind,
        duration_s=duration_s,
        source_uri=source_uri,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    await session.commit()
    await session.refresh(video)

    # SSE ownership stamp — same fail-open pattern as /videos/upload.
    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import start_pipeline

    stream_url: str | None = f"/tasks/{video.id}/events"
    try:
        await progress.aset_owner(str(video.id), str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "ingest aset_owner failed (Redis down?) video_id=%s err=%s",
            video.id,
            exc,
        )
        stream_url = None

    start_pipeline(str(video.id))

    from observability import log_event

    log_event(
        "clip_ingested",
        creator_id=str(creator.id),
        video_id=str(video.id),
        synthetic_youtube_id=youtube_video_id,
        kind=kind.value,
        duration_s=duration_s,
        bytes_received=bytes_received,
        clip_name=clip_name,
    )

    return {
        "video_id": str(video.id),
        "status": video.ingest_status.value,
        "stream_url": stream_url,
    }


@clips_router.get("/{clip_id}", response_model=ClipOut)
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
