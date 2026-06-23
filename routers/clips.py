import asyncio
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_key import get_current_creator_via_api_key
from auth import get_current_creator
from billing.ledger import check_balance_for_minutes, check_positive_balance, video_minutes
from config import settings
from db import get_session
from limiter import creator_key, limiter
from models import Clip, Creator, IngestStatus, RenderStatus, Signals, Transcript, Video, VideoKind
from routers._schemas import EmptyState, NextActionOut, TaskQueuedOut, build_envelope_state
from worker.storage import presigned_download_url, upload_file
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
    # Issue 134 — populated after POST /clean lands; cleared on confirm.
    cleaned_render_uri: str | None = None


class ClipListOut(BaseModel):
    """Clip-list envelope. ``state`` / ``message`` / ``next_action`` were added
    2026-06-08 (DECISIONS) so GET ``/videos/{id}/clips`` matches the empty-
    state contract used by ``/videos`` and ``/insights/saved``.

    POST ``/videos/{id}/clips/generate`` returns the same shape — clips it
    just produced are always ``populated`` and the empty-state fields stay
    ``None``.
    """

    clips: list[ClipOut]
    state: EmptyState = "populated"
    message: str | None = None
    next_action: NextActionOut | None = None


class RenderQueuedOut(TaskQueuedOut):
    """202 Accepted response for POST /clips/{id}/render (Issue 92)."""


class RenderStyleIn(BaseModel):
    """Optional style parameters for styled renders (Issue 119 + Issue 133).

    All fields are optional. Omitting a field means "keep the existing value"
    (or the default on first render). This lets the UI send only changed fields.
    """

    # "bold_pop" | "gradient_slide" | "minimal" | null — see clip_engine/captions.py.
    # (Issue-119 legacy keys white_large/yellow_impact/captions_sm were drawtext
    # placeholders that drew empty text; removed in Issue 133.)
    subtitle: str | None = None
    background: str | None = None  # "blur" | "black" | null
    captions_enabled: bool | None = None
    zoom_on_peak: bool | None = None  # opt-in punch-in at peak (Issue 184)
    denoise: bool | None = None  # opt-in noise reduction (Issue 185)
    aspect: Literal["9:16", "1:1", "16:9"] | None = None  # export preset (Issue 182)


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
        "cleaned_render_uri": clip.cleaned_render_uri,
    }


@router.post("/{video_id}/clips/generate", response_model=ClipListOut)
@limiter.limit("10/hour", key_func=creator_key)
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
@limiter.limit("120/minute", key_func=creator_key)
async def list_clips(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return ranked clips for a video.

    Empty-state copy distinguishes "video still ingesting → wait" from
    "ingest done but no clips generated yet → run /generate". This is
    information the frontend would otherwise have to fetch separately.
    """
    video = await session.get(Video, video_id)
    if not video or video.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Video not found")

    # Hard cap to prevent unbounded scans as clip counts grow. (Issue 76)
    _LIST_LIMIT = 100
    result = await session.execute(
        select(Clip)
        .where(Clip.video_id == video_id, Clip.creator_id == creator.id)
        .order_by(Clip.rank)
        .limit(_LIST_LIMIT)
    )
    clips = list(result.scalars())
    items = [_clip_response(c) for c in clips]
    state = build_envelope_state(len(items))
    message: str | None = None
    next_action: dict | None = None
    if state == "empty_initial":
        if video.ingest_status != IngestStatus.done:
            message = "This video is still ingesting — clips appear once analysis finishes."
        else:
            message = "No clips yet — run analysis to extract setup-first candidates."
            next_action = {
                "label": "Generate clips",
                "action_type": "navigate",
                "url": f"/videos/{video_id}/clips/generate",
            }
    return {
        "clips": items,
        "state": state,
        "message": message,
        "next_action": next_action,
    }


# ── Clip-level actions ────────────────────────────────────────────────────────


@clips_router.post("/{clip_id}/render", status_code=202, response_model=RenderQueuedOut)
@limiter.limit("20/hour", key_func=creator_key)
async def render_clip(
    request: Request,
    clip_id: uuid.UUID,
    body: RenderStyleIn | None = None,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a render job for the clip. Returns task_id.

    Accepts an optional style body (Issue 119). When present, persists the
    chosen style on the clip and passes it to the render task so the worker
    can apply subtitle / background filters.
    """
    await check_positive_balance(creator.id, session)

    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.render_status == RenderStatus.running:
        raise HTTPException(status_code=409, detail="Render already in progress")

    # Persist style choice before enqueuing so the worker task reads it fresh.
    if body is not None:
        existing = clip.style_preset or {}
        merged: dict = {**existing}
        if body.subtitle is not None:
            merged["subtitle"] = body.subtitle
        if body.background is not None:
            merged["background"] = body.background
        if body.captions_enabled is not None:
            merged["captions_enabled"] = body.captions_enabled
        if body.zoom_on_peak is not None:
            merged["zoom_on_peak"] = body.zoom_on_peak
        if body.denoise is not None:
            merged["denoise"] = body.denoise
        if body.aspect is not None:
            merged["aspect"] = body.aspect
        clip.style_preset = merged or None
        await session.commit()

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import render_clip as render_task

    # Audit fix (scale-checklist B): `.delay()` is sync Redis I/O; offload from
    # the request loop so a slow Redis doesn't stall every concurrent handler.
    task = await asyncio.to_thread(render_task.delay, str(clip_id))
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
        logger.warning(
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


# ── Issue 134: filler-word + silence removal (clean pass) ─────────────────


class CleanPreviewCut(BaseModel):
    """One cut range in the preview response. UI renders the corresponding
    transcript words as strikethrough."""

    start_s: float
    end_s: float
    reason: str  # "filler" | "silence"
    word: str | None = None


class CleanPreviewOut(BaseModel):
    """Response shape for GET /clips/{id}/clean-preview (Issue 134)."""

    clip_id: str
    clip_duration_s: float
    cuts: list[CleanPreviewCut]
    percent_removed: float
    warning: str | None = None


class CleanQueuedOut(TaskQueuedOut):
    """202 Accepted response for POST /clips/{id}/clean (Issue 134)."""


class CleanConfirmOut(BaseModel):
    """200 OK response for POST /clips/{id}/clean/confirm (Issue 134)."""

    clip_id: str
    render_uri: str | None
    cleaned_render_uri: str | None  # always None after a successful swap
    status: str  # "swapped" | "noop"


def _clip_clean_cuts(
    clip: Clip, transcript: Transcript | None
) -> tuple[list[CleanPreviewCut], float, float]:
    """Compute the cleaning cut list for ``clip`` from ``transcript``. Returns
    ``(cuts, percent_removed, clip_duration_s)``. Pure function so the preview
    endpoint and the test surface share one code path."""
    from clip_engine.filler import (
        detect_cut_segments,
        merge_adjacent_cuts,
        percent_removed,
    )

    clip_origin_s = clip.setup_start_s if clip.setup_start_s is not None else clip.start_s
    clip_duration_s = clip.end_s - clip_origin_s
    if not transcript or not isinstance(transcript.segments_jsonb, dict):
        return [], 0.0, clip_duration_s
    segments = transcript.segments_jsonb.get("segments") or []
    words_clip_relative: list[dict] = []
    for seg in segments:
        for w in seg.get("words") or []:
            w_start = float(w.get("start", 0.0))
            w_end = float(w.get("end", 0.0))
            if w_end <= clip_origin_s or w_start >= clip.end_s:
                continue
            words_clip_relative.append(
                {
                    "word": w.get("word", ""),
                    "start": w_start - clip_origin_s,
                    "end": w_end - clip_origin_s,
                }
            )
    cuts = detect_cut_segments(
        words_clip_relative,
        clip_start_s=0.0,
        clip_end_s=clip_duration_s,
        silence_threshold_ms=settings.SILENCE_REMOVAL_THRESHOLD_MS,
        silence_tail_ms=settings.SILENCE_TAIL_MS,
        flank_gap_ms=settings.FILLER_TIER2_FLANK_GAP_MS,
        tier2_max_duration_ms=settings.FILLER_TIER2_MAX_DURATION_MS,
    )
    pct = percent_removed(merge_adjacent_cuts(cuts), clip_duration_s)
    preview = [
        CleanPreviewCut(start_s=c.start_s, end_s=c.end_s, reason=c.reason, word=c.word)
        for c in cuts
    ]
    return preview, pct, clip_duration_s


@clips_router.get("/{clip_id}/clean-preview", response_model=CleanPreviewOut)
@limiter.limit("60/hour", key_func=creator_key)
async def clean_preview(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the cut list that ``POST /clips/{id}/clean`` would produce.
    No render is triggered — this is the cheap preview endpoint that drives
    the transcript-strikethrough UI (Issue 134).
    """
    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    transcript = await session.get(Transcript, clip.video_id)
    preview, pct, dur = _clip_clean_cuts(clip, transcript)
    warning: str | None = None
    if pct >= 30.0:
        warning = f"This removes {pct:.0f}% of your clip."
    return {
        "clip_id": str(clip_id),
        "clip_duration_s": dur,
        "cuts": [c.model_dump() for c in preview],
        "percent_removed": pct,
        "warning": warning,
    }


@clips_router.post("/{clip_id}/clean", status_code=202, response_model=CleanQueuedOut)
@limiter.limit("20/hour", key_func=creator_key)
async def clean_clip(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a clean pass — re-render the existing clip with filler words and
    long silences removed. Returns 202 + ``task_id`` + ``stream_url``. The
    original ``render_uri`` is preserved; the result lands in
    ``cleaned_render_uri`` so the UI can offer both side-by-side until
    ``POST /clean/confirm`` swaps them (Issue 134)."""
    await check_positive_balance(creator.id, session)

    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not clip.render_uri:
        raise HTTPException(status_code=400, detail="Clip has not been rendered yet")
    # Issue-135 audit fix: clean and edit share cleaned_render_uri; running one
    # while the other is pending would silently no-op in the worker (the
    # idempotency probe at tasks.py:874 / :1006 short-circuits) — drop the
    # user's work without an error. Surface the conflict here so the UI can
    # prompt to confirm or discard the pending artifact first.
    if clip.cleaned_render_uri:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pending_clean_or_edit",
                "message": "Confirm or discard the pending cleaned/edited version first.",
            },
        )

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import clean_clip as clean_task

    task = await asyncio.to_thread(clean_task.delay, str(clip_id))
    stream_url: str | None = f"/tasks/{clip_id}/events"
    try:
        await progress.aset_owner(str(clip_id), str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "clean aset_owner failed (Redis down?) clip_id=%s err=%s",
            clip_id,
            exc,
        )
        stream_url = None

    return {
        "task_id": task.id,
        "status": "queued",
        "stream_url": stream_url,
    }


@clips_router.post(
    "/{clip_id}/clean/confirm",
    status_code=200,
    response_model=CleanConfirmOut,
)
@limiter.limit("60/hour", key_func=creator_key)
async def clean_confirm(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Atomically swap the cleaned render into ``render_uri`` and clear
    ``cleaned_render_uri``. The original mp4 falls under the existing R2
    lifecycle prefix (no new cleanup code needed). Idempotent: if the swap
    has already happened (``cleaned_render_uri`` is null) the endpoint
    returns 200 with ``status="noop"`` so router-retry is safe (Issue 134)."""
    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not clip.cleaned_render_uri:
        return {
            "clip_id": str(clip_id),
            "render_uri": clip.render_uri,
            "cleaned_render_uri": None,
            "status": "noop",
        }
    clip.render_uri = clip.cleaned_render_uri
    clip.cleaned_render_uri = None
    await session.commit()
    return {
        "clip_id": str(clip_id),
        "render_uri": clip.render_uri,
        "cleaned_render_uri": None,
        "status": "swapped",
    }


# ── Issue 135: text-based transcript editor ─────────────────────────────


class TranscriptWord(BaseModel):
    """One word in the clip-windowed transcript pane."""

    word: str
    start_s: float
    end_s: float
    index: int


class ClipTranscriptOut(BaseModel):
    """Response for GET /clips/{id}/transcript (Issue 135)."""

    clip_id: str
    clip_duration_s: float
    words: list[TranscriptWord]


class CutSegmentIn(BaseModel):
    """One user-selected cut range, clip-relative seconds."""

    start_s: float
    end_s: float


class CutsIn(BaseModel):
    """Request body for POST /clips/{id}/cuts."""

    segments: list[CutSegmentIn]


class CutsQueuedOut(TaskQueuedOut):
    """202 Accepted response for POST /clips/{id}/cuts."""


@clips_router.get(
    "/{clip_id}/transcript",
    response_model=ClipTranscriptOut,
)
@limiter.limit("60/hour", key_func=creator_key)
async def clip_transcript(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the clip-windowed transcript word array for the editor pane
    (Issue 135). Word timestamps are normalised to clip-relative seconds so
    the frontend doesn't need to know about the source-video timebase."""
    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    transcript = await session.get(Transcript, clip.video_id)
    clip_origin_s = clip.setup_start_s if clip.setup_start_s is not None else clip.start_s
    clip_duration_s = clip.end_s - clip_origin_s
    words: list[TranscriptWord] = []
    if transcript and isinstance(transcript.segments_jsonb, dict):
        idx = 0
        for seg in transcript.segments_jsonb.get("segments") or []:
            for w in seg.get("words") or []:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                if w_end <= clip_origin_s or w_start >= clip.end_s:
                    continue
                words.append(
                    TranscriptWord(
                        word=str(w.get("word", "")),
                        start_s=max(0.0, w_start - clip_origin_s),
                        end_s=min(clip_duration_s, w_end - clip_origin_s),
                        index=idx,
                    )
                )
                idx += 1
    return {
        "clip_id": str(clip_id),
        "clip_duration_s": clip_duration_s,
        "words": [w.model_dump() for w in words],
    }


@clips_router.post(
    "/{clip_id}/cuts",
    status_code=202,
    response_model=CutsQueuedOut,
)
@limiter.limit("20/hour", key_func=creator_key)
async def submit_cuts(
    request: Request,
    clip_id: uuid.UUID,
    body: CutsIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Accept a user-supplied list of cut segments from the text-based editor
    and queue a re-render (Issue 135). The result lands in
    ``Clip.cleaned_render_uri`` so the existing
    ``POST /clips/{id}/clean/confirm`` swap path applies — same UX flow as
    Issue 134's filler-removal pass.

    Validation (HTTP 422 on any violation): bounds, no overlap, no NaN,
    ≥5 s kept, ≤85 % removed. Sub-frame keep ranges are floored upstream by
    the validator; the worker re-validates as a defensive belt-and-suspenders.
    """
    from clip_engine.edits import CutValidationError, validate_user_cuts

    await check_positive_balance(creator.id, session)

    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not clip.render_uri:
        raise HTTPException(status_code=400, detail="Clip has not been rendered yet")
    # Issue-135 audit fix — mirror /clean: refuse if a pending cleaned/edited
    # artifact already exists, else the worker idempotency probe at
    # tasks.py:1006 silently drops this edit.
    if clip.cleaned_render_uri:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pending_clean_or_edit",
                "message": "Confirm or discard the pending cleaned/edited version first.",
            },
        )
    clip_origin_s = clip.setup_start_s if clip.setup_start_s is not None else clip.start_s
    clip_duration_s = clip.end_s - clip_origin_s

    try:
        validate_user_cuts(
            [(s.start_s, s.end_s) for s in body.segments],
            clip_duration_s=clip_duration_s,
        )
    except CutValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import edit_clip as edit_task

    payload = [[s.start_s, s.end_s] for s in body.segments]
    task = await asyncio.to_thread(edit_task.delay, str(clip_id), payload)
    stream_url: str | None = f"/tasks/{clip_id}/events"
    try:
        await progress.aset_owner(str(clip_id), str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "edit aset_owner failed (Redis down?) clip_id=%s err=%s",
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

    Entropy: 12 hex chars = 48 bits ≈ 2^48. Birthday-bound 50% collision
    probability at ~16M rows; uniqueness is per-creator (schema constraint
    UNIQUE(creator_id, youtube_video_id)) so the real ceiling is per-creator
    upload count — astronomically safe at the PRD's 10k-clips-per-creator
    target. If a future migration drops the per-creator scope, widen to 16
    hex chars (still inside ``String(32)``). (Issue 108)
    """
    return f"obs-{uuid.uuid4().hex[:12]}"


@clips_router.post("/ingest", status_code=202, response_model=ClipIngestedOut)
@limiter.limit("20/hour", key_func=creator_key)
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
    # Issue 104: wrap the entire post-NamedTemporaryFile block in a single
    # try/finally so the temp file is always cleaned up — including on
    # non-HTTPException paths (OSError on disk-full, CancelledError on client
    # disconnect) that the original per-block unlinks couldn't cover.
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

        duration_s = await asyncio.to_thread(probe_duration_s, tmp_path)
        kind = classify_video_kind(duration_s) if duration_s is not None else VideoKind.long

        if duration_s is not None:
            await check_balance_for_minutes(creator.id, video_minutes(duration_s), session)

        youtube_video_id = _obs_clip_youtube_id()
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

    # Audit fix (scale-checklist B): start_pipeline calls apply_async() inline —
    # sync Redis I/O on the request loop. Offload.
    await asyncio.to_thread(start_pipeline, str(video.id))

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
@limiter.limit("120/minute", key_func=creator_key)
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


@clips_router.get("/{clip_id}/download", response_model=None)
@limiter.limit("60/minute", key_func=creator_key)
async def download_clip(
    request: Request,
    clip_id: uuid.UUID,
    variant: Literal["original", "cleaned"] = "original",
    disposition: Literal["attachment", "inline"] = "attachment",
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | FileResponse:
    """Serve a rendered clip (Issue 182).

    ``disposition=attachment`` forces a download; ``inline`` backs the in-app
    player ``<video>`` (Issue-182 playback fix). ``variant=cleaned`` serves the
    filler-removed re-render when present. Per-creator isolation: another
    creator's clip — or a missing id — returns 404, never the bytes.

    Prod (R2): 302-redirects to a short-lived presigned GET URL so bandwidth is
    offloaded to object storage. Dev (local disk): streams the file directly.
    """
    clip = await session.get(Clip, clip_id)
    if not clip or clip.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Clip not found")

    uri = clip.cleaned_render_uri if variant == "cleaned" else clip.render_uri
    if not uri:
        raise HTTPException(status_code=404, detail="Clip not yet rendered")

    suffix = "-cleaned" if variant == "cleaned" else ""
    filename = f"clip-{clip_id}{suffix}.mp4"

    presigned = presigned_download_url(uri, filename=filename, disposition=disposition)
    if presigned is not None:
        return RedirectResponse(url=presigned, status_code=302)
    # Local-disk dev: stream the file straight from disk.
    path = Path(uri)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip file not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
        content_disposition_type=disposition,
    )
