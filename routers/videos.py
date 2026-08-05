import asyncio
import logging
import re
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import check_balance_for_minutes, check_positive_balance, video_minutes
from config import settings
from db import get_session
from limiter import creator_key, limiter
from models import Creator, IngestStatus, OnboardingState, Transcript, Video, VideoKind, VideoOrigin
from routers._enqueue import stamp_stream_owner
from routers._owned import get_owned
from routers._schemas import EmptyState, NextActionOut, build_envelope_state
from worker.storage import (
    MultipartUploadNotFound,
    StorageError,
    aabort_multipart_upload,
    acomplete_multipart_upload,
    acreate_multipart_upload,
    adelete_file,
    ahead_object,
    alist_upload_parts,
    aread_bytes,
    presign_upload_part,
    presigned_download_url,
    upload_file,
)
from worker.tasks import start_pipeline
from youtube.data_api import classify_video_kind, get_videos_metadata
from youtube.ingest import probe_duration_s
from youtube.oauth import get_valid_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/videos", tags=["videos"])


class VideoListItemOut(BaseModel):
    id: str
    youtube_video_id: str | None
    title: str | None
    kind: str
    ingest_status: str
    # Populated only when ingest_status == "failed": a short, creator-safe reason
    # so the dashboard can explain the failure instead of a bare badge. Defaults to
    # None so the catalog endpoint (which reuses this model, never failed) is unaffected.
    failure_reason: str | None = None
    duration_s: float | None
    created_at: str
    # Issue 139: provenance lets the dashboard tell a clip-trackable upload from
    # a linked video that still needs its source file. ``clippable`` is the
    # derived convenience flag (True only when stored media exists).
    origin: str
    clippable: bool
    # Issue 387. A BOOLEAN, never the URI: poster_uri is an internal storage key
    # and the bytes are served by the authed endpoint below. Defaults to False so
    # the catalog endpoint, which reuses this model, is unaffected — the same
    # trick `failure_reason` uses above.
    has_poster: bool = False
    # Issue 392. Also a BOOLEAN — same reasoning. Lets the editor decide between
    # fetching the waveform and drawing the labelled flat track WITHOUT a
    # speculative 404 on every open.
    has_peaks: bool = False


class VideoListOut(BaseModel):
    """Envelope for the videos list (DECISIONS 2026-06-08).

    ``message`` + ``next_action`` are populated only when ``videos`` is empty;
    the frontend uses them to render guided empty-state copy instead of a
    hardcoded blank screen.
    """

    videos: list[VideoListItemOut]
    state: EmptyState
    message: str | None = None
    next_action: NextActionOut | None = None


class CatalogListOut(BaseModel):
    """Paginated catalog (origin=catalog) videos for the in-app channel browser (Issue 310).

    These are the creator's own synced channel videos (upserted by sync_video_catalog),
    hidden from GET /videos. ``clippable`` is always false here — promoting one via POST
    /videos/link adopts it (origin catalog -> link) and the source file must still be
    uploaded (we never download from YouTube, per ToS).
    """

    videos: list[VideoListItemOut]  # reuse existing item model
    total: int  # total catalog rows for this creator (drives pagination UI)
    limit: int
    offset: int


class VideoLinkedOut(BaseModel):
    video_id: str
    status: str
    stream_url: str | None = (
        None  # Issue 92: SSE for the upload ingest pipeline; None on /videos/link (no pipeline)
    )


class VideoStatusOut(BaseModel):
    video_id: str
    youtube_video_id: str | None
    ingest_status: str
    source_uri: str | None
    captions_available: bool


class QueuedOut(BaseModel):
    video_id: str
    status: str
    queued: bool


class UploadConfigOut(BaseModel):
    """Transport selector for the SPA uploader (Issue 395): ``multipart`` =
    presigned direct-to-R2; ``proxy`` = legacy streamed POST (local-disk dev)."""

    mode: Literal["multipart", "proxy"]
    part_size_bytes: int  # 0 in proxy mode (no chunking)
    max_file_bytes: int


class UploadCreateIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    content_type: str | None = None
    youtube_video_id: str | None = None
    # Client-read (HTMLVideoElement metadata) — advisory for the balance
    # pre-check only, never persisted; the worker re-probes at ingest.
    duration_s: float | None = Field(default=None, gt=0)


class UploadCreateOut(BaseModel):
    upload_id: str
    key: str
    part_size_bytes: int


class UploadPartPresignIn(BaseModel):
    key: str


class UploadPartPresignOut(BaseModel):
    url: str
    expires_in: int


class UploadPartOut(BaseModel):
    part_number: int
    size: int
    etag: str


class UploadPartsOut(BaseModel):
    parts: list[UploadPartOut]


class UploadCompletePart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=256)


class UploadCompleteIn(BaseModel):
    key: str
    parts: list[UploadCompletePart] = Field(min_length=1, max_length=10_000)
    youtube_video_id: str | None = None
    duration_s: float | None = Field(default=None, gt=0)  # advisory only — never persisted


class UploadAbortIn(BaseModel):
    key: str


class UploadAbortedOut(BaseModel):
    aborted: bool


# YouTube video IDs are exactly 11 chars of [A-Za-z0-9_-]. Validate before the value
# is interpolated into a storage key, so `../` or `/` can't reshape the object path. (Issue 73)
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _validate_youtube_id(youtube_video_id: str) -> None:
    if not _YT_ID_RE.match(youtube_video_id):
        raise HTTPException(status_code=422, detail="Invalid youtube_video_id")


# Whitelisted source-container suffixes for browser uploads (Issue 395). A bounded
# set keeps the storage-key shape fully validatable in _validate_upload_key.
_UPLOAD_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
# One fixed part size per upload trivially satisfies R2's all-parts-equal-except-last
# rule and gives 10,000 x 25 MiB ≈ 244 GB of headroom, far above UPLOAD_MAX_FILE_GB.
_UPLOAD_PART_SIZE_BYTES = 25 * 1024 * 1024
_UPLOAD_PRESIGN_EXPIRES_S = 900


def _require_multipart_mode() -> None:
    if settings.STORAGE_BACKEND != "r2":
        raise HTTPException(
            status_code=409, detail="Direct upload not available on this deployment"
        )


def _validate_upload_key(key: str, creator_id: uuid.UUID) -> None:
    """403 unless key is ``source/{this creator}/{yt-id | uuid4-hex}{whitelisted suffix}``.

    Signature issuance is the only write path to the bucket, so a foreign or
    malformed key is refused outright (403) rather than 404-masked: it is a
    request to sign someone else's namespace, not a lookup miss. The token
    alternatives are exactly the two shapes the create endpoint mints. (Issue 395)
    """
    token = r"(?:[A-Za-z0-9_-]{11}|[0-9a-f]{32})"
    suffixes = "|".join(re.escape(s) for s in _UPLOAD_SUFFIXES)
    if not re.fullmatch(rf"source/{re.escape(str(creator_id))}/{token}(?:{suffixes})", key):
        raise HTTPException(status_code=403, detail="Upload key does not belong to this creator")


@router.get("", response_model=VideoListOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_videos(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List all videos for the current creator, newest first.

    Excludes catalog-only rows (``origin == catalog``) — those are DNA/analytics
    references upserted by ``sync_video_catalog`` and have no stored media.
    Surfacing them would show "N pending forever" on the dashboard and have the
    polling loop hammer ``/status`` for rows that will never transition.
    Linked rows (``origin == link``) ARE shown — they were previously hidden by
    the old ``source_uri IS NULL`` heuristic, the SEV1 fixed in Issue 139 — but
    carry ``clippable: false`` so the UI offers an "upload your source file"
    affordance instead of a doomed clip CTA (we never download from YouTube).

    Returns a ``VideoListOut`` envelope (DECISIONS 2026-06-08). When the list
    is empty the response carries a ``message`` + ``next_action`` keyed to the
    creator's ``onboarding_state`` so the dashboard can render a guided
    empty hero instead of a blank table.
    """
    # Hard cap to prevent unbounded scans as creator libraries grow. (Issue 76)
    _LIST_LIMIT = 100
    result = await session.execute(
        select(Video)
        .where(Video.creator_id == creator.id, Video.origin != VideoOrigin.catalog)
        .order_by(Video.created_at.desc())
        .limit(_LIST_LIMIT)
    )
    videos = list(result.scalars())
    items = [
        {
            "id": str(v.id),
            "youtube_video_id": v.youtube_video_id,
            "title": v.title,
            "kind": v.kind.value,
            "ingest_status": v.ingest_status.value,
            "failure_reason": v.failure_reason,
            "duration_s": v.duration_s,
            "created_at": v.created_at.isoformat(),
            "origin": v.origin.value,
            # Only uploaded videos carry stored media and can run the pipeline.
            "clippable": v.source_uri is not None,
            "has_poster": v.poster_uri is not None,
            "has_peaks": v.peaks_uri is not None,
        }
        for v in videos
    ]
    state = build_envelope_state(len(items))
    message: str | None = None
    next_action: NextActionOut | None = None
    if state == "empty_initial":
        if creator.onboarding_state == OnboardingState.connected:
            message = "Link your first video to see AutoClip work — it learns your style from your own content, not a generic virality score."
            next_action = NextActionOut(
                label="Link a video",
                action_type="open_form",
                url="/app/dashboard",  # Issue 235/161: was /static/index.html#link-form
            )
        else:
            message = (
                "Connect your YouTube channel so AutoClip can analyse your style and audience."
            )
            next_action = NextActionOut(
                label="Connect YouTube",
                action_type="navigate",
                url="/auth/login",
            )
    return {
        "videos": items,
        "state": state,
        "message": message,
        "next_action": next_action,
    }


_CATALOG_PAGE_DEFAULT = 50
_CATALOG_PAGE_MAX = 100


@router.get("/catalog", response_model=CatalogListOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_catalog(
    request: Request,
    limit: int = _CATALOG_PAGE_DEFAULT,
    offset: int = 0,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List this creator's synced channel videos (origin=catalog), paginated, newest first.

    These are the DNA/analytics reference rows upserted by ``sync_video_catalog`` and
    hidden from ``GET /videos``. The in-app channel browser (Issue 310) surfaces them so a
    creator can promote ("Clip this") one of their own published videos without pasting a
    URL. Promotion adopts the row via ``POST /videos/link`` (origin catalog -> link); the
    source file must still be uploaded — we never download from YouTube (ToS).

    Offset pagination (DECISIONS 2026-06-24): channels are bounded and append-mostly, so
    offset matches the existing ``_LIST_LIMIT`` house pattern without cursor complexity.
    """
    limit = max(1, min(limit, _CATALOG_PAGE_MAX))
    offset = max(0, offset)
    # Per-creator isolation enforced on BOTH the count and the page query.
    base = select(Video).where(Video.creator_id == creator.id, Video.origin == VideoOrigin.catalog)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    result = await session.execute(
        base.order_by(Video.created_at.desc()).limit(limit).offset(offset)
    )
    videos = list(result.scalars())
    items = [
        {
            "id": str(v.id),
            "youtube_video_id": v.youtube_video_id,
            "title": v.title,
            "kind": v.kind.value,
            "ingest_status": v.ingest_status.value,
            "duration_s": v.duration_s,
            "created_at": v.created_at.isoformat(),
            "origin": v.origin.value,
            # Catalog rows have no stored source media -> always false.
            "clippable": v.source_uri is not None,
        }
        for v in videos
    ]
    return {"videos": items, "total": total, "limit": limit, "offset": offset}


@router.post("/link", response_model=VideoLinkedOut)
@limiter.limit("120/minute", key_func=creator_key)
async def link_video(
    request: Request,
    youtube_video_id: str = Form(...),
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Register a YouTube video by ID. Does not download any content."""
    _validate_youtube_id(youtube_video_id)
    existing_video = (
        await session.execute(
            select(Video).where(
                Video.creator_id == creator.id,
                Video.youtube_video_id == youtube_video_id,
            )
        )
    ).scalar_one_or_none()
    if existing_video is not None:
        # A catalog row was synced for DNA/analytics and is hidden from the clip
        # work-list (origin != catalog). "Linking" one of your own channel videos
        # should ADOPT that row into the clip pipeline, not 409 — otherwise a
        # creator whose channel is synced sees 0 clippable videos and has no way
        # to promote one. Flipping origin → link surfaces it in /videos with the
        # honest "upload the source file" path (we never download from YouTube).
        if existing_video.origin == VideoOrigin.catalog:
            existing_video.origin = VideoOrigin.link
            await session.commit()
            await session.refresh(existing_video)

            from observability import log_event

            log_event(
                "video_linked",
                creator_id=str(creator.id),
                video_id=str(existing_video.id),
                youtube_video_id=youtube_video_id,
                adopted_from_catalog=True,
            )
            return {
                "video_id": str(existing_video.id),
                "status": existing_video.ingest_status.value,
            }
        # Already a link/upload row — a genuine duplicate.
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
        logger.warning("link_video: could not resolve kind for %s: %s", youtube_video_id, exc)

    video = Video(
        creator_id=creator.id,
        youtube_video_id=youtube_video_id,
        kind=kind,
        duration_s=duration_s,
        origin=VideoOrigin.link,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    try:
        await session.commit()
    except IntegrityError:
        # Double-submit race (Issue 352 Batch C): both requests passed the
        # SELECT above; the loser violates UNIQUE(creator_id, youtube_video_id)
        # at commit. Same clean 409 as the check-first path — never a raw 500.
        await session.rollback()
        raise HTTPException(status_code=409, detail="Video already registered") from None
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
@limiter.limit("120/minute", key_func=creator_key)
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    youtube_video_id: str | None = Form(None),
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upload a video file and start the ingest pipeline.

    Streams to a temp file in 1 MB chunks, enforcing the size limit without
    loading the full upload into memory.

    ``youtube_video_id`` is optional (Issue 317). When supplied it associates the
    upload with one of the creator's published videos so its later performance
    feeds the outcome loop; when omitted the upload is a standalone raw file
    (e.g. an OBS recording or unpublished cut) with no YouTube tie. The raw file
    is always the source media — we never download from YouTube.
    """
    if youtube_video_id is not None:
        _validate_youtube_id(youtube_video_id)
    await check_positive_balance(creator.id, session)
    # Issue 82b: end the read-only transaction so the pooled connection is not
    # held across the client-paced streaming loop below. Later queries auto-begin
    # a new transaction; the after_begin listener restamps the RLS GUC from
    # session.info (set by get_current_creator).
    await session.commit()

    max_bytes = settings.UPLOAD_MAX_MB * 1024 * 1024

    # Issue 232 — early Content-Length rejection: clients that send an honest
    # Content-Length header above the limit are rejected here, before the temp
    # file is opened and streaming begins. Saves I/O and temp-file creation for
    # obviously-oversize uploads. The chunk-loop 413 (below) remains the
    # authoritative guard for clients that omit or lie about Content-Length.
    cl_header = request.headers.get("content-length")
    if cl_header is not None:
        try:
            cl_bytes = int(cl_header)
        except ValueError:
            cl_bytes = 0
        if cl_bytes > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds {settings.UPLOAD_MAX_MB} MB limit",
            )

    # 1 MB read chunks — balances syscall overhead against per-request heap cost.
    chunk_size = 1 * 1024 * 1024

    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    bytes_received = 0
    # Issue 104: single outer try/finally guarantees the temp file is cleaned
    # up on ALL exit paths — including non-HTTPException paths such as OSError
    # (disk full during R2 PUT) and CancelledError (client disconnect) that the
    # original per-block unlinks could not cover.
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

        # Dedupe only applies when the upload is associated with a published
        # video. Standalone uploads (youtube_video_id is None) are distinct by
        # design — a creator may upload many un-associated raw files.
        if youtube_video_id is not None:
            existing = await session.execute(
                select(Video).where(
                    Video.creator_id == creator.id,
                    Video.youtube_video_id == youtube_video_id,
                )
            )
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="Video already registered")

        # Probe duration from the uploaded file BEFORE the R2 PUT so we never
        # store an unknown-kind row even if upload fails partway through.
        # ffprobe is a header read (caps at 30s in youtube/ingest.py). (Issue 87)
        duration_s = await asyncio.to_thread(probe_duration_s, tmp_path)
        kind = classify_video_kind(duration_s) if duration_s is not None else VideoKind.long

        # Issue 89 — duration-aware balance pre-check.
        # The upfront `check_positive_balance` at line ~163 prevents the full
        # streamed upload for 0-balance creators (saves disk I/O). Now that we
        # know the actual duration, enforce the predicate `deduct_for_video`
        # uses internally (balance >= minutes), so a low-balance creator
        # uploading a long video gets an actionable 402 BEFORE the R2 PUT
        # rather than a silent post-upload failed status. (Skipped when probe
        # fails — unknown-duration uploads fall through to the legacy path
        # and surface the same generic failure as before.)
        if duration_s is not None:
            await check_balance_for_minutes(creator.id, video_minutes(duration_s), session)

        # The object path needs a stable, collision-free token. Use the YouTube
        # id when associated; otherwise a fresh uuid for the standalone upload.
        # The full key is persisted in ``source_uri``, so the token is always
        # recoverable from the row even when youtube_video_id is NULL.
        storage_token = youtube_video_id or uuid.uuid4().hex
        key = f"source/{creator.id}/{storage_token}{suffix}"
        # Issue 82b: release the pooled connection before the R2 PUT — the
        # dedupe/balance reads above began a transaction that would otherwise
        # be held for the full upload. The Video insert below reacquires one.
        await session.commit()
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
        origin=VideoOrigin.upload,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    try:
        await session.commit()
    except IntegrityError:
        # Double-submit race (Issue 352 Batch C): both uploads passed the dedupe
        # SELECT; the loser violates UNIQUE(creator_id, youtube_video_id) at
        # commit → clean 409, not a raw 500. Standalone uploads
        # (youtube_video_id IS NULL) never conflict — NULLs are distinct.
        await session.rollback()
        raise HTTPException(status_code=409, detail="Video already registered") from None
    await session.refresh(video)

    # Issue 92: stamp ownership for the upload chain's SSE stream. The worker
    # tasks (_ingest_async, _transcribe_async, _signals_async,
    # _generate_clips_async) all emit to task:{video.id}:events — using
    # video_id as the stream key keeps the client lookup deterministic across
    # the chain without piping the Celery chain id through every stage.
    #
    # Wave-4 Fix 1: on a Redis blip the Video row is already committed and
    # start_pipeline() runs next, so the actual ingest still happens. The user
    # loses live progress; they don't lose the upload.
    stream_url = await stamp_stream_owner(str(video.id), str(creator.id), log_label="upload")

    # Audit fix (scale-checklist B): start_pipeline runs apply_async() inline.
    await asyncio.to_thread(start_pipeline, str(video.id))

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

    return {
        "video_id": str(video.id),
        "status": video.ingest_status.value,
        "stream_url": stream_url,
    }


# ── Presigned direct-to-R2 multipart upload (Issue 395) ───────────────────────
#
# The browser uploads parts straight to R2; these endpoints only mint presigned
# URLs and register completion. Sessions are stateless server-side: the key is
# validated against the authenticated creator's namespace on every call, R2
# binds uploadId to key, the Video row is created only at /complete, and R2
# auto-aborts abandoned multipart uploads after 7 days.


@router.get("/uploads/config", response_model=UploadConfigOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_upload_config(
    request: Request,
    creator: Creator = Depends(get_current_creator),
) -> dict:
    """Which upload transport this deployment supports (Issue 395)."""
    if settings.STORAGE_BACKEND == "r2":
        return {
            "mode": "multipart",
            "part_size_bytes": _UPLOAD_PART_SIZE_BYTES,
            "max_file_bytes": settings.UPLOAD_MAX_FILE_GB * 1024**3,
        }
    return {
        "mode": "proxy",
        "part_size_bytes": 0,
        "max_file_bytes": settings.UPLOAD_MAX_MB * 1024 * 1024,
    }


@router.post("/uploads", status_code=201, response_model=UploadCreateOut)
@limiter.limit("60/hour", key_func=creator_key)
async def create_upload(
    request: Request,
    body: UploadCreateIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Start a presigned multipart upload: mint the storage key + R2 UploadId.

    The declared size is the presign-time abuse gate (the real quota gate is the
    minutes balance, enforced authoritatively at ingest); the actual object size
    is re-verified with a HEAD at /complete. The advisory dedupe here fails fast
    before gigabytes move — the authoritative dedupe is /complete's insert.
    """
    _require_multipart_mode()
    if body.youtube_video_id is not None:
        _validate_youtube_id(body.youtube_video_id)
    suffix = Path(body.filename).suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type — use one of: {', '.join(_UPLOAD_SUFFIXES)}",
        )
    if body.size_bytes > settings.UPLOAD_MAX_FILE_GB * 1024**3:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.UPLOAD_MAX_FILE_GB} GB limit",
        )
    await check_positive_balance(creator.id, session)
    if body.duration_s is not None:
        await check_balance_for_minutes(creator.id, video_minutes(body.duration_s), session)
    if body.youtube_video_id is not None:
        existing = await session.execute(
            select(Video).where(
                Video.creator_id == creator.id,
                Video.youtube_video_id == body.youtube_video_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Video already registered")

    storage_token = body.youtube_video_id or uuid.uuid4().hex
    key = f"source/{creator.id}/{storage_token}{suffix}"
    # Issue 82b idiom: release the pooled connection before the storage round-trip.
    await session.commit()
    try:
        upload_id = await acreate_multipart_upload(key, body.content_type)
    except StorageError:
        logger.warning("create_multipart_upload failed for key=%s", key, exc_info=True)
        raise HTTPException(status_code=502, detail="Storage error — try again") from None

    from observability import log_event

    log_event(
        "upload_multipart_created",
        creator_id=str(creator.id),
        key=key,
        declared_bytes=body.size_bytes,
        declared_duration_s=body.duration_s,
    )
    return {"upload_id": upload_id, "key": key, "part_size_bytes": _UPLOAD_PART_SIZE_BYTES}


@router.post(
    "/uploads/{upload_id}/parts/{part_number}/presign", response_model=UploadPartPresignOut
)
@limiter.limit("1200/minute", key_func=creator_key)
async def presign_upload_part_url(
    request: Request,
    upload_id: str,
    part_number: int,
    body: UploadPartPresignIn,
    creator: Creator = Depends(get_current_creator),
) -> dict:
    """Presigned PUT URL for one part. Signing is local-only (no storage call),
    hence the generous limit — a gigabit uploader on a 20 GB file signs ~350/min.
    No extra headers are signed: the browser sends the raw part body only.
    """
    _require_multipart_mode()
    _validate_upload_key(body.key, creator.id)
    if not 1 <= part_number <= 10_000:
        raise HTTPException(status_code=422, detail="part_number must be between 1 and 10000")
    url = presign_upload_part(body.key, upload_id, part_number, expires_s=_UPLOAD_PRESIGN_EXPIRES_S)
    return {"url": url, "expires_in": _UPLOAD_PRESIGN_EXPIRES_S}


@router.get("/uploads/{upload_id}/parts", response_model=UploadPartsOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_uploaded_parts(
    request: Request,
    upload_id: str,
    key: str,
    creator: Creator = Depends(get_current_creator),
) -> dict:
    """Parts already uploaded — the client's resume primitive after a reload."""
    _require_multipart_mode()
    _validate_upload_key(key, creator.id)
    try:
        parts = await alist_upload_parts(key, upload_id)
    except MultipartUploadNotFound:
        raise HTTPException(status_code=404, detail="Upload not found or expired") from None
    except StorageError:
        raise HTTPException(status_code=502, detail="Storage error — try again") from None
    return {"parts": parts}


@router.post("/uploads/{upload_id}/complete", response_model=VideoLinkedOut)
@limiter.limit("60/hour", key_func=creator_key)
async def complete_upload(
    request: Request,
    upload_id: str,
    body: UploadCompleteIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Assemble the parts, register the Video row, start the ingest pipeline.

    Ordering is deliberate: dedupe/balance rejections come BEFORE the
    CompleteMultipartUpload call, so a 409/402 leaves the multipart session
    intact — after a top-up the client re-calls complete without re-uploading
    anything. Retry-safe: a NoSuchUpload whose object already has a row for
    this creator re-returns that row (a double-complete is a lost response,
    publications-confirm style). The IntegrityError race 409s WITHOUT deleting
    the object: an associated upload's key is deterministic (token = the
    YouTube id), so the winning row may point at this exact object.
    """
    _require_multipart_mode()
    _validate_upload_key(body.key, creator.id)
    if body.youtube_video_id is not None:
        _validate_youtube_id(body.youtube_video_id)
        existing = await session.execute(
            select(Video).where(
                Video.creator_id == creator.id,
                Video.youtube_video_id == body.youtube_video_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Video already registered")
    await check_positive_balance(creator.id, session)
    if body.duration_s is not None:
        await check_balance_for_minutes(creator.id, video_minutes(body.duration_s), session)

    source_uri = f"s3://{settings.R2_BUCKET}/{body.key}"
    # Issue 82b idiom: release the pooled connection before the storage round-trips.
    await session.commit()
    try:
        await acomplete_multipart_upload(body.key, upload_id, [p.model_dump() for p in body.parts])
    except MultipartUploadNotFound:
        existing = await session.execute(
            select(Video).where(
                Video.creator_id == creator.id,
                Video.source_uri == source_uri,
            )
        )
        video = existing.scalar_one_or_none()
        if video is not None:
            return {
                "video_id": str(video.id),
                "status": video.ingest_status.value,
                "stream_url": None,
            }
        raise HTTPException(status_code=404, detail="Upload not found or expired") from None
    except StorageError:
        logger.warning("complete_multipart_upload failed for key=%s", body.key, exc_info=True)
        raise HTTPException(status_code=502, detail="Storage error — try again") from None

    head = await ahead_object(body.key)
    if head is None:
        raise HTTPException(status_code=502, detail="Storage error — try again")
    if head["size"] > settings.UPLOAD_MAX_FILE_GB * 1024**3:
        await adelete_file(source_uri)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.UPLOAD_MAX_FILE_GB} GB limit",
        )

    # Kind is provisional (advisory client duration); duration_s stays None so the
    # worker's authoritative probe fills both at ingest (worker/tasks.py).
    kind = classify_video_kind(body.duration_s) if body.duration_s is not None else VideoKind.long
    video = Video(
        creator_id=creator.id,
        youtube_video_id=body.youtube_video_id,
        kind=kind,
        duration_s=None,
        source_uri=source_uri,
        origin=VideoOrigin.upload,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Video already registered") from None
    await session.refresh(video)

    stream_url = await stamp_stream_owner(str(video.id), str(creator.id), log_label="upload")
    await asyncio.to_thread(start_pipeline, str(video.id))

    from observability import log_event

    log_event(
        "video_uploaded",
        creator_id=str(creator.id),
        video_id=str(video.id),
        youtube_video_id=body.youtube_video_id,
        kind=kind.value,
        duration_s=body.duration_s,
        bytes_received=head["size"],
        transport="multipart",
    )
    return {
        "video_id": str(video.id),
        "status": video.ingest_status.value,
        "stream_url": stream_url,
    }


@router.post("/uploads/{upload_id}/abort", response_model=UploadAbortedOut)
@limiter.limit("120/minute", key_func=creator_key)
async def abort_upload(
    request: Request,
    upload_id: str,
    body: UploadAbortIn,
    creator: Creator = Depends(get_current_creator),
) -> dict:
    """Cancel an in-flight multipart upload. Idempotent — an already-gone
    uploadId still reports aborted (R2 auto-aborts abandoned ones at 7 days)."""
    _require_multipart_mode()
    _validate_upload_key(body.key, creator.id)
    try:
        await aabort_multipart_upload(body.key, upload_id)
    except MultipartUploadNotFound:
        pass
    except StorageError:
        raise HTTPException(status_code=502, detail="Storage error — try again") from None
    return {"aborted": True}


@router.post("/{video_id}/queue", status_code=202, response_model=QueuedOut)
@limiter.limit("60/hour", key_func=creator_key)
async def queue_video_for_analysis(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Issue 125 — explicit per-video pipeline trigger.

    In selective/manual mode the dashboard surfaces this as a "Queue for
    analysis" CTA on every catalog video sitting at ingest_status=pending.
    In auto mode it's also useful as a manual retry path when something
    stalled. Idempotent: if the pipeline is already running or done, returns
    the current status without re-queuing.
    """
    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    # Issue 139: the ingest pipeline needs stored source media — we never
    # download from YouTube (ToS). A linked/catalog row has no source_uri, so
    # queuing it would fail ingest with a confusing error. Reject up front with
    # an actionable 409 pointing the creator at the upload path instead.
    if video.source_uri is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This video has no source file to analyse. Upload the original "
                "video file (e.g. from Google Takeout) to generate clips."
            ),
        )
    if video.ingest_status not in (IngestStatus.pending, IngestStatus.failed):
        # Running or done — nothing to do; return current state so the caller can
        # refresh its row without seeing a 4xx.
        return {"video_id": str(video.id), "status": video.ingest_status.value, "queued": False}

    if video.ingest_status == IngestStatus.failed:
        # Manual retry of a failed video (the "please try again" path the dashboard
        # now surfaces — Issue: stale-failed-jobs 2026-06-29). Reset to pending so
        # the pipeline re-runs and the row leaves the terminal 'failed' state instead
        # of lingering as clutter. Re-deduction is idempotent via MinuteDeduction's
        # UNIQUE(video_id), so a retry never double-charges.
        video.ingest_status = IngestStatus.pending
        video.failure_reason = None
        await session.commit()

    # Issue 313: stamp ownership BEFORE start_pipeline, exactly as the upload
    # path does (see queue_video_upload above). start_pipeline emits stage
    # progress to task:{video.id}:events; the SSE endpoint (GET
    # /tasks/{id}/events) refuses any stream whose owner key is unset, so
    # without this the dashboard's live stage-progress bar silently 404s on
    # the "Queue for analysis" CTA. The stream URL is discarded — this
    # response carries no stream_url; the row still refreshes via polling.
    await stamp_stream_owner(str(video.id), str(creator.id), log_label="queue")

    await asyncio.to_thread(start_pipeline, str(video.id))
    return {"video_id": str(video.id), "status": video.ingest_status.value, "queued": True}


@router.get("/{video_id}/status", response_model=VideoStatusOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_video_status(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    return {
        "video_id": str(video.id),
        "youtube_video_id": video.youtube_video_id,
        "ingest_status": video.ingest_status.value,
        "source_uri": video.source_uri,
        "captions_available": video.captions_available,
    }


# ── Issue 372: full-source editing backbone ──────────────────────────────────


@router.get("/{video_id}/poster", response_model=None)
# 300/minute, NOT the 60/minute download tier. 60 is one request per deliberate
# user action; a poster is one request per ROW, and a cold dashboard paints up to
# _LIST_LIMIT of them, so 60 would 429 the first page load.
@limiter.limit("300/minute", key_func=creator_key)
async def get_video_poster(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Serve the poster still for a video (Issue 387).

    A SAME-ORIGIN BYTE PROXY, deliberately diverging from the presigned-302 used
    by /clips/{id}/download. Three reasons, all specific:

    1. CSP. `_build_csp` in main.py already had to widen `media-src` to the R2
       origin because browsers re-run CSP against redirect targets — a 302 does
       not launder the origin. Doing the same for images would permanently widen
       `img-src` to the whole R2 account for a 50 KB file.
    2. Caching. The dashboard refetches /videos every 5s while work is in flight.
       A presigned URL's signature changes per generation, so returning one per
       row would cache-bust every poster on every poll.
    3. Presigned URLs are bearer tokens; N per response puts N capability tokens
       into the SPA's query cache and any captured response body.

    The cost objection is real and answered by size: we refuse to proxy clips
    because they are 10-50 MB and need Range support. A poster is ~50 KB, and the
    cache headers below make repeat paints free.
    """
    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    if not video.poster_uri:
        raise HTTPException(status_code=404, detail="No poster frame for this video")
    data = await aread_bytes(video.poster_uri)
    if data is None:
        raise HTTPException(status_code=404, detail="Poster frame not found")
    return Response(
        content=data,
        media_type="image/jpeg",
        # `private` is required: these bytes are per-creator and must never enter
        # a shared or CDN cache. Immutable because the key is deterministic and
        # only changes on re-ingest.
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


@router.get("/{video_id}/peaks", response_model=None)
# 60/minute, NOT the poster's 300: a waveform is one request per deliberate
# editor open, not one per dashboard row.
@limiter.limit("60/minute", key_func=creator_key)
async def get_video_peaks(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Serve the waveform envelope for a video (Issue 392).

    A same-origin byte proxy for the same reasons as `/poster` above — chiefly
    that a presigned URL is a bearer token and its rotating signature would
    cache-bust the artifact on every poll.

    404 here is NORMAL and the client is built for it: it means either the video
    predates Issue 392 and its audio was purged before the backfill reached it,
    or the audio could not be decoded. The editor draws an explicitly-labelled
    flat track in that case. It must never invent amplitude — drawing something
    plausible over a creator's own audio is exactly the defect this issue exists
    to remove.
    """
    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    if not video.peaks_uri:
        raise HTTPException(status_code=404, detail="No waveform for this video")
    # A 22-minute source is ~330 KB and the cap is 2 MB, so the default read
    # ceiling in `aread_bytes` already bounds this; MAX_PAIRS bounds it at source.
    data = await aread_bytes(video.peaks_uri)
    if data is None:
        raise HTTPException(status_code=404, detail="Waveform not found")
    return Response(
        content=data,
        media_type="application/json",
        # `private` is required: these bytes are per-creator and must never enter
        # a shared or CDN cache. Immutable because the key is deterministic and
        # only changes on re-ingest.
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


@router.get("/{video_id}/stream", response_model=None)
@limiter.limit("60/minute", key_func=creator_key)
async def stream_video(
    request: Request,
    video_id: uuid.UUID,
    disposition: Literal["inline", "attachment"] = "inline",
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | FileResponse:
    """Serve the SOURCE video for the long-form editor (Issue 372).

    ``inline`` (default) backs the in-app ``<video>``. Prod (R2): 302-redirects
    to a short-lived presigned GET — object storage serves Range requests, so
    seeking works. Dev (local disk): FileResponse, which answers Range with
    206 natively. A purged source (retention window elapsed) returns the
    structured 409 ``source_expired`` detail so the SPA renders the re-upload
    CTA instead of a dead player.
    """
    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    if not video.source_uri:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_expired",
                "message": (
                    f"Source media expired ({settings.SOURCE_MEDIA_RETENTION_HOURS}-hour "
                    "retention) — re-upload the video to edit the full source."
                ),
            },
        )

    # Keep the original container suffix so media type inference is honest
    # (uploads may be .mov/.mkv/.webm, not just .mp4).
    suffix = Path(video.source_uri).suffix or ".mp4"
    filename = f"source-{video_id}{suffix}"

    presigned = presigned_download_url(video.source_uri, filename=filename, disposition=disposition)
    if presigned is not None:
        return RedirectResponse(url=presigned, status_code=302)
    path = Path(video.source_uri)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")
    return FileResponse(path, filename=filename, content_disposition_type=disposition)


class TranscriptSegmentOut(BaseModel):
    text: str
    start_s: float
    end_s: float
    index: int


class VideoTranscriptOut(BaseModel):
    video_id: str
    duration_s: float | None
    source: str | None
    segments: list[TranscriptSegmentOut]
    state: EmptyState
    message: str | None = None


@router.get("/{video_id}/transcript", response_model=VideoTranscriptOut)
@limiter.limit("120/minute", key_func=creator_key)
async def video_transcript(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> VideoTranscriptOut:
    """Full video-level transcript for the long-form editor (Issue 372).

    Segment-granular — no nested word arrays (a two-hour video's word payload
    is megabytes, and search + click-to-seek only need segment bounds; a
    ``include_words`` param can be added later if word snapping is wanted).
    Times are video-relative seconds, as stored. A video that hasn't been
    transcribed yet returns 200 with an ``empty_initial`` envelope, not 404 —
    404 stays reserved for a missing/foreign video. 120/minute (vs the
    clip-transcript's 60/hour): pure DB read, refetched on video switches.
    """
    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    transcript = await session.get(Transcript, video_id)

    segments: list[TranscriptSegmentOut] = []
    raw = (transcript.segments_jsonb or {}).get("segments", []) if transcript else []
    for i, seg in enumerate(raw):
        try:
            segments.append(
                TranscriptSegmentOut(
                    text=str(seg.get("text", "")),
                    start_s=float(seg["start"]),
                    end_s=float(seg["end"]),
                    index=i,
                )
            )
        except (KeyError, TypeError, ValueError):
            # A malformed segment must not take down the whole panel.
            continue

    duration_s = (
        video.duration_s
        if video.duration_s is not None
        else (segments[-1].end_s if segments else None)
    )
    if not segments:
        return VideoTranscriptOut(
            video_id=str(video_id),
            duration_s=duration_s,
            source=transcript.source if transcript else None,
            segments=[],
            state="empty_initial",
            message="This video hasn't been transcribed yet — transcripts are generated during analysis.",
        )
    return VideoTranscriptOut(
        video_id=str(video_id),
        duration_s=duration_s,
        source=transcript.source if transcript else None,
        segments=segments,
        state="populated",
    )
