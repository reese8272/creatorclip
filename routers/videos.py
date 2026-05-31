import asyncio
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import check_positive_balance
from config import settings
from db import get_session
from limiter import limiter
from models import Creator, IngestStatus, Video, VideoKind
from worker.storage import upload_file
from worker.tasks import start_pipeline
from youtube.data_api import classify_video_kind, get_videos_metadata
from youtube.ingest import probe_duration_s
from youtube.oauth import get_valid_access_token

router = APIRouter(prefix="/videos", tags=["videos"])


class VideoListItemOut(BaseModel):
    id: str
    youtube_video_id: str
    title: str | None
    kind: str
    ingest_status: str
    duration_s: float | None
    created_at: str


class VideoLinkedOut(BaseModel):
    video_id: str
    status: str


class VideoStatusOut(BaseModel):
    video_id: str
    youtube_video_id: str
    ingest_status: str
    source_uri: str | None
    captions_available: bool


# YouTube video IDs are exactly 11 chars of [A-Za-z0-9_-]. Validate before the value
# is interpolated into a storage key, so `../` or `/` can't reshape the object path. (Issue 73)
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _validate_youtube_id(youtube_video_id: str) -> None:
    if not _YT_ID_RE.match(youtube_video_id):
        raise HTTPException(status_code=422, detail="Invalid youtube_video_id")


@router.get("", response_model=list[VideoListItemOut])
@limiter.limit("120/minute")
async def list_videos(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """List all videos for the current creator, newest first."""
    result = await session.execute(
        select(Video).where(Video.creator_id == creator.id).order_by(Video.created_at.desc())
    )
    videos = list(result.scalars())
    return [
        {
            "id": str(v.id),
            "youtube_video_id": v.youtube_video_id,
            "title": v.title,
            "kind": v.kind.value,
            "ingest_status": v.ingest_status.value,
            "duration_s": v.duration_s,
            "created_at": v.created_at.isoformat(),
        }
        for v in videos
    ]


@router.post("/link", response_model=VideoLinkedOut)
@limiter.limit("120/minute")
async def link_video(
    request: Request,
    youtube_video_id: str = Form(...),
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Register a YouTube video by ID. Does not download any content."""
    _validate_youtube_id(youtube_video_id)
    existing = await session.execute(
        select(Video).where(
            Video.creator_id == creator.id,
            Video.youtube_video_id == youtube_video_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Video already registered")

    # Resolve kind+duration from YouTube so a manually-linked Short is not
    # mis-bucketed as long-form (Issue 87). Falls back to long+unknown on
    # ANY error — better to register the video than to block the user; the
    # next catalog/analytics sync repairs the row when YT is reachable again.
    kind = VideoKind.long
    duration_s: float | None = None
    try:
        access_token = await get_valid_access_token(creator.id, session)
        metadata = await get_videos_metadata(access_token, [youtube_video_id])
        if metadata:
            kind = metadata[0]["kind"]
            duration_s = metadata[0]["duration_s"]
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "link_video: could not resolve kind for %s: %s", youtube_video_id, exc
        )

    video = Video(
        creator_id=creator.id,
        youtube_video_id=youtube_video_id,
        kind=kind,
        duration_s=duration_s,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    await session.commit()
    await session.refresh(video)

    from observability import log_event

    log_event(
        "video_linked",
        creator_id=str(creator.id),
        video_id=str(video.id),
        youtube_video_id=youtube_video_id,
        kind=kind.value,
        duration_s=duration_s,
    )
    return {"video_id": str(video.id), "status": video.ingest_status.value}


@router.post("/upload", response_model=VideoLinkedOut)
@limiter.limit("120/minute")
async def upload_video(
    request: Request,
    youtube_video_id: str = Form(...),
    file: UploadFile = File(...),
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upload a video file and start the ingest pipeline.

    Streams to a temp file in 1 MB chunks, enforcing the size limit without
    loading the full upload into memory.
    """
    _validate_youtube_id(youtube_video_id)
    await check_positive_balance(creator.id, session)

    max_bytes = settings.UPLOAD_MAX_MB * 1024 * 1024
    # 1 MB read chunks — balances syscall overhead against per-request heap cost.
    chunk_size = 1 * 1024 * 1024

    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
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
                    # Abort immediately; partial file is cleaned up in finally.
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds {settings.UPLOAD_MAX_MB} MB limit",
                    )
                fh.write(chunk)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise

    existing = await session.execute(
        select(Video).where(
            Video.creator_id == creator.id,
            Video.youtube_video_id == youtube_video_id,
        )
    )
    if existing.scalar_one_or_none():
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="Video already registered")

    # Probe duration from the uploaded file BEFORE the R2 PUT so we never
    # store an unknown-kind row even if upload fails partway through.
    # ffprobe is a header read (caps at 30s in youtube/ingest.py). (Issue 87)
    duration_s = await asyncio.to_thread(probe_duration_s, tmp_path)
    kind = classify_video_kind(duration_s) if duration_s is not None else VideoKind.long

    try:
        key = f"source/{creator.id}/{youtube_video_id}{suffix}"
        # Offload the (possibly multi-hundred-MB) R2 PUT / disk copy so it never
        # blocks the API event loop and stalls other requests. (Issue 67)
        source_uri = await asyncio.to_thread(upload_file, tmp_path, key)
    finally:
        tmp_path.unlink(missing_ok=True)

    video = Video(
        creator_id=creator.id,
        youtube_video_id=youtube_video_id,
        kind=kind,
        duration_s=duration_s,
        source_uri=source_uri,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    await session.commit()
    await session.refresh(video)

    start_pipeline(str(video.id))

    from observability import log_event

    log_event(
        "video_uploaded",
        creator_id=str(creator.id),
        video_id=str(video.id),
        youtube_video_id=youtube_video_id,
        kind=kind.value,
        duration_s=duration_s,
        bytes_received=bytes_received,
    )

    return {"video_id": str(video.id), "status": video.ingest_status.value}


@router.get("/{video_id}/status", response_model=VideoStatusOut)
@limiter.limit("120/minute")
async def get_video_status(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    video = await session.get(Video, video_id)
    if not video or video.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Video not found")
    return {
        "video_id": str(video.id),
        "youtube_video_id": video.youtube_video_id,
        "ingest_status": video.ingest_status.value,
        "source_uri": video.source_uri,
        "captions_available": video.captions_available,
    }
