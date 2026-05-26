import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.tiers import check_video_limit, increment_video_usage
from config import settings
from db import get_session
from limiter import limiter
from models import Creator, IngestStatus, Video, VideoKind
from worker.storage import upload_file
from worker.tasks import start_pipeline

router = APIRouter(prefix="/videos", tags=["videos"])


@router.get("")
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


@router.post("/link")
@limiter.limit("120/minute")
async def link_video(
    request: Request,
    youtube_video_id: str = Form(...),
    creator: Creator = Depends(check_video_limit),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Register a YouTube video by ID. Does not download any content."""
    existing = await session.execute(
        select(Video).where(
            Video.creator_id == creator.id,
            Video.youtube_video_id == youtube_video_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Video already registered")

    video = Video(
        creator_id=creator.id,
        youtube_video_id=youtube_video_id,
        kind=VideoKind.long,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    await increment_video_usage(session, creator.id)
    await session.commit()
    await session.refresh(video)
    return {"video_id": str(video.id), "status": video.ingest_status.value}


@router.post("/upload")
@limiter.limit("120/minute")
async def upload_video(
    request: Request,
    youtube_video_id: str = Form(...),
    file: UploadFile = File(...),
    creator: Creator = Depends(check_video_limit),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upload a video file and start the ingest pipeline."""
    max_bytes = settings.UPLOAD_MAX_MB * 1024 * 1024
    contents = await file.read(max_bytes + 1)
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413, detail=f"File exceeds {settings.UPLOAD_MAX_MB} MB limit"
        )

    existing = await session.execute(
        select(Video).where(
            Video.creator_id == creator.id,
            Video.youtube_video_id == youtube_video_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Video already registered")

    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        key = f"source/{creator.id}/{youtube_video_id}{suffix}"
        source_uri = upload_file(tmp_path, key)
    finally:
        tmp_path.unlink(missing_ok=True)

    video = Video(
        creator_id=creator.id,
        youtube_video_id=youtube_video_id,
        kind=VideoKind.long,
        source_uri=source_uri,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    await increment_video_usage(session, creator.id)
    await session.commit()
    await session.refresh(video)

    start_pipeline(str(video.id))

    return {"video_id": str(video.id), "status": video.ingest_status.value}


@router.get("/{video_id}/status")
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
