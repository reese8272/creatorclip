import asyncio
import logging
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from preference.model import PreferenceScorer

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_key import get_current_creator_via_api_key
from auth import get_current_creator
from billing.ledger import check_balance_for_minutes, check_positive_balance, video_minutes
from billing.spend_guard import require_budget
from config import settings
from db import get_session
from flags import require_flag
from limiter import BRIEF_DAILY_LIMIT, LLM_DAILY_LIMIT, RENDER_DAILY_LIMIT, creator_key, limiter
from models import (
    Clip,
    ClipImpression,
    Creator,
    CreatorStyle,
    IngestStatus,
    RenderStatus,
    Signals,
    Summary,
    SummaryStatus,
    Transcript,
    Video,
    VideoKind,
    VideoOrigin,
)
from observability import record_llm_tokens
from routers._owned import get_owned
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


class PersonalizationStatus(BaseModel):
    """Honest personalization-status surface (Issue 216).

    Communicates to the creator whether the preference model is active for their
    account, how many training labels they have, and the current threshold. Below
    ``PERSONALIZATION_THRESHOLD_LABELS`` the reranker falls back to DNA + signals
    and ``active`` is ``False``; at/above the threshold ``active`` is ``True`` and
    ``weight`` is the current ramp value.

    Placed on the list envelope (not per-ClipOut) to avoid O(N) scorer reads per
    request. The float ``weight`` is retained for API consumers; the UI surfaces
    only ``labels`` / ``threshold`` (human-readable progress).
    """

    active: bool
    labels: int
    threshold: int
    weight: float


class ClipListOut(BaseModel):
    """Clip-list envelope. ``state`` / ``message`` / ``next_action`` were added
    2026-06-08 (DECISIONS) so GET ``/videos/{id}/clips`` matches the empty-
    state contract used by ``/videos`` and ``/insights/saved``.

    POST ``/videos/{id}/clips/generate`` returns the same shape — clips it
    just produced are always ``populated`` and the empty-state fields stay
    ``None``.

    ``personalization`` was added in Issue 216 to surface honest cold-start status
    so creators know whether ranking is personalized to their feedback or still
    using DNA + signals.

    ``skip_reason`` was added in Issue 217 — when ``clips`` is empty and
    ``state=="empty_initial"``, this carries a principle-grounded code string
    (from ``clip_engine/candidates.py``) so the frontend can render an honest
    "why not clipped" explanation instead of a generic empty state.
    The label is sourced from ``CLIPPING_PRINCIPLES.md``; no virality language.

    ``truncated`` was added in Issue 339 — set to True when the response has
    been hard-capped at ``_LIST_LIMIT`` and additional clips exist beyond that
    cap. Additive / backward-compatible: default is False.
    """

    clips: list[ClipOut]
    state: EmptyState = "populated"
    message: str | None = None
    next_action: NextActionOut | None = None
    personalization: PersonalizationStatus | None = None
    skip_reason: str | None = None
    skip_reason_label: str | None = None
    truncated: bool = False


class VideoClipCount(BaseModel):
    """Per-video clip count row returned by GET /videos/clips/counts (Issue 213)."""

    video_id: str
    total: int
    rendered: int


class ClipCountsOut(BaseModel):
    """Batched clip-count response — one query for all the creator's videos.

    Replaces the N+1 useQueries pattern in Dashboard.tsx (OCB-2).
    """

    counts: list[VideoClipCount]


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


@router.get("/clips/counts", response_model=ClipCountsOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_clip_counts(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return clip totals and rendered counts for ALL of the creator's videos in
    one query (Issue 213).

    This endpoint replaces the N+1 useQueries pattern on the Dashboard (OCB-2).
    Placed at /videos/clips/counts — BEFORE /{video_id}/clips in the file so the
    router matches the literal path segment 'clips' before treating it as a UUID.

    Per-creator isolation: the WHERE clause joins through Video.creator_id so a
    creator can only see counts for their own videos.
    """
    stmt = (
        select(
            Clip.video_id.label("video_id"),
            func.count().label("total"),
            func.sum(case((Clip.render_status == RenderStatus.done, 1), else_=0)).label("rendered"),
        )
        .join(Video, Clip.video_id == Video.id)
        .where(Video.creator_id == creator.id)
        .group_by(Clip.video_id)
    )
    result = await session.execute(stmt)
    rows = result.all()
    counts = [
        VideoClipCount(
            video_id=str(row.video_id),
            total=row.total,
            rendered=row.rendered,
        )
        for row in rows
    ]
    return {"counts": counts}


@router.post(
    "/{video_id}/clips/generate",
    response_model=ClipListOut,
    # Kill switch (Issue 284) + spend breaker (Issue 290): 503 when the
    # llm_generation flag is off, 429 during a creator spend cool-down.
    dependencies=[Depends(require_flag("llm_generation")), Depends(require_budget)],
)
@limiter.limit("10/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
async def generate_clips(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Extract, score, and rank clip candidates for a fully-ingested video."""
    await check_positive_balance(creator.id, session)

    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
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

    import db
    from clip_engine.ranking import load_existing_clips, persist_ranked_clips, score_and_rank

    # Idempotency guard (Issue 61) runs BEFORE scoring so a repeat call never
    # burns LLM tokens; persist_ranked_clips re-checks it before inserting.
    existing = await load_existing_clips(session, video_id, creator.id)
    if existing:
        return {"clips": [_clip_response(c) for c in existing]}

    timeline = signals.timeline_jsonb

    # Issue 82b (pool starvation): release the request-scoped DB connection
    # BEFORE the per-candidate LLM scoring round-trip (30–120 s). The session
    # is closed here — get_session's context manager makes the later double
    # close a no-op — and persistence reacquires a fresh session below.
    await session.close()

    ranked = await score_and_rank(
        video_id=video_id,
        creator_id=creator.id,
        timeline=timeline,
        dna_brief=dna_brief,
        transcript_segments=transcript_segments,
        max_candidates=settings.CLIPS_PER_VIDEO_DEFAULT,
        ledger_session_factory=db.AsyncSessionLocal,
    )
    if not ranked:
        return {"clips": []}

    async with db.AsyncSessionLocal() as persist_session:
        # RLS (Issue 79): the after_begin listener stamps the per-transaction
        # `app.creator_id` GUC from session.info — it MUST be set before the
        # first query on this reacquired session, or per-creator isolation
        # silently disappears on the persist path.
        persist_session.info["creator_id"] = creator.id
        clips = await persist_ranked_clips(persist_session, video_id, creator.id, ranked)
        return {"clips": [_clip_response(c) for c in clips]}


def _build_personalization_status(scorer: "PreferenceScorer | None") -> PersonalizationStatus:
    """Compute PersonalizationStatus from a loaded scorer (or None).

    Called once per list_clips request — a single scorer read, not N reads.
    None → active=False, labels=0; scorer present → read label_count and compute
    preference_weight to determine whether the threshold has been crossed.
    """
    from preference.model import preference_weight

    threshold = settings.PERSONALIZATION_THRESHOLD_LABELS
    if scorer is None:
        return PersonalizationStatus(active=False, labels=0, threshold=threshold, weight=0.0)
    label_count = scorer.label_count
    weight = preference_weight(label_count)
    return PersonalizationStatus(
        active=label_count >= threshold,
        labels=label_count,
        threshold=threshold,
        weight=weight,
    )


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

    The ``personalization`` envelope field (Issue 216) surfaces honest cold-start
    status so creators know whether ranking reflects their feedback or falls back
    to DNA + signals.

    The ``skip_reason`` / ``skip_reason_label`` fields (Issue 217) carry an honest,
    principle-grounded explanation when the video produced zero clips so the creator
    understands *why* — never a raw score, never virality language.
    """
    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")

    # Hard cap to prevent unbounded scans as clip counts grow. (Issue 76)
    # Issue 339: query for _LIST_LIMIT+1 so we can distinguish "exactly 100
    # clips" from "100+ clips" without a separate COUNT query.
    _LIST_LIMIT = 100
    result = await session.execute(
        select(Clip)
        .where(Clip.video_id == video_id, Clip.creator_id == creator.id)
        .order_by(Clip.rank)
        .limit(_LIST_LIMIT + 1)
    )
    clips_raw = list(result.scalars())
    truncated = len(clips_raw) > _LIST_LIMIT
    clips = clips_raw[:_LIST_LIMIT]

    # Impression/position log (Issue 202): record what rank each clip was shown at,
    # and when, so later counterfactual/IPS evaluation is possible (it cannot be
    # reconstructed retroactively). Best-effort — a logging failure must never break
    # the listing. Per-creator isolation holds (creator_id stamped; RLS-gated table).
    if clips:
        from datetime import UTC, datetime

        shown_at = datetime.now(UTC)
        try:
            session.add_all(
                [
                    ClipImpression(
                        creator_id=creator.id,
                        clip_id=c.id,
                        rank=c.rank if c.rank is not None else 0,
                        shown_at=shown_at,
                    )
                    for c in clips
                ]
            )
            await session.commit()
        except Exception:  # noqa: BLE001 — telemetry must never break the read path
            await session.rollback()
            logger.warning("impression logging failed for video %s", video_id)

    items = [_clip_response(c) for c in clips]
    state = build_envelope_state(len(items))
    message: str | None = None
    next_action: dict | None = None
    skip_reason: str | None = None
    skip_reason_label_text: str | None = None

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
            # Issue 217: derive the principal reason this video has no clips so the
            # creator sees an honest, principle-grounded "why not" explanation.
            # source_unavailable when the video has no stored media (origin=link with
            # no upload); otherwise consult the signal timeline if one exists.
            from clip_engine.candidates import derive_skip_reason, skip_reason_label

            source_available = bool(video.source_uri)
            signals_row = await session.get(Signals, video_id)
            timeline = signals_row.timeline_jsonb if signals_row else {}
            skip_reason = derive_skip_reason(
                timeline=timeline,
                source_available=source_available,
            )
            skip_reason_label_text = skip_reason_label(skip_reason) if skip_reason else None

    from preference.train import load_latest

    scorer = await load_latest(session, creator.id)
    personalization = _build_personalization_status(scorer)

    return {
        "clips": items,
        "state": state,
        "message": message,
        "next_action": next_action,
        "personalization": personalization.model_dump(),
        "skip_reason": skip_reason,
        "skip_reason_label": skip_reason_label_text,
        "truncated": truncated,
    }


# ── Clip-level actions ────────────────────────────────────────────────────────


@clips_router.post(
    "/{clip_id}/render",
    status_code=202,
    response_model=RenderQueuedOut,
    # Kill switch (Issue 284): 503 when the render_intake flag is off.
    dependencies=[Depends(require_flag("render_intake")), Depends(require_budget)],
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(RENDER_DAILY_LIMIT, key_func=creator_key)
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

    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
    if clip.render_status == RenderStatus.running:
        from worker.tasks import ais_render_stale

        # Issue 359: a worker SIGKILL (OOM/deploy) after the `running` commit
        # skips every `failed` write, leaving the row `running` forever — this
        # 409 then blocks recovery permanently. When the render-start marker
        # shows the run exceeded the Celery hard time limit (or never existed),
        # allow the re-render override; a fresh running render keeps the 409.
        # The worker's Issue-336 guard tolerates re-rendering a `running` clip.
        if not await ais_render_stale(str(clip_id)):
            raise HTTPException(status_code=409, detail="Render already in progress")
        logger.warning(
            "render override: clip %s stuck in stale running state — re-rendering", clip_id
        )

    # Persist style choice before enqueuing so the worker task reads it fresh.
    # Issue 186: start from the creator's brand-kit defaults so omitted fields
    # fall back to the kit rather than None, then apply the per-clip overrides.
    if body is not None:
        kit_result = await session.execute(
            select(CreatorStyle).where(CreatorStyle.creator_id == creator.id)
        )
        kit_db_row: CreatorStyle | None = kit_result.scalar_one_or_none()
        kit_style: dict = (kit_db_row.style or {}) if isinstance(kit_db_row, CreatorStyle) else {}
        # Per-clip stored preset overrides the kit; the request body overrides both.
        merged: dict = {**kit_style, **(clip.style_preset or {})}
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

    # Issue 353: a render request on a `done` clip is an explicit re-render —
    # reset the render state HERE (the endpoint owns intent) so the worker's
    # done-with-render_uri redelivery guard doesn't no-op it. Clearing
    # render_uri also unmounts the stale player so the fresh render is fetched.
    # Applied with or without a style body (a plain retry also re-renders),
    # in the same transaction as the merged style so both persist atomically.
    # Snapshot first (Issue 359c) so a failed enqueue below can restore the
    # watchable render instead of leaving the clip stripped with no task coming.
    reset_applied = clip.render_status == RenderStatus.done
    prior_render_uri = clip.render_uri
    if reset_applied:
        clip.render_status = RenderStatus.pending
        clip.render_uri = None
    await session.commit()

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import render_clip as render_task

    # Audit fix (scale-checklist B): `.delay()` is sync Redis I/O; offload from
    # the request loop so a slow Redis doesn't stall every concurrent handler.
    # Issue 359c: a broker throw here used to leave the Issue-353 reset
    # committed with NO task enqueued — destroying the previous render_uri of a
    # `done` clip. No task exists when `.delay()` raises, so restoring the
    # snapshot is race-free; the reset-commit-before-enqueue ordering that the
    # worker's redelivery guard depends on is preserved on the success path.
    try:
        task = await asyncio.to_thread(render_task.delay, str(clip_id))
    except Exception as exc:
        if reset_applied:
            clip.render_status = RenderStatus.done
            clip.render_uri = prior_render_uri
            await session.commit()
        logger.error("render enqueue failed for clip %s: %s", clip_id, exc)
        raise HTTPException(
            status_code=503, detail="Could not queue the render — please try again."
        ) from exc
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
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
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


@clips_router.post(
    "/{clip_id}/clean",
    status_code=202,
    response_model=CleanQueuedOut,
    # Kill switch (Issue 284): 503 when the render_intake flag is off.
    dependencies=[Depends(require_flag("render_intake")), Depends(require_budget)],
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(RENDER_DAILY_LIMIT, key_func=creator_key)
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

    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
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
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
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
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
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
    # Kill switch (Issue 284): 503 when the render_intake flag is off.
    dependencies=[Depends(require_flag("render_intake")), Depends(require_budget)],
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(RENDER_DAILY_LIMIT, key_func=creator_key)
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

    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
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
@limiter.limit(RENDER_DAILY_LIMIT, key_func=creator_key)
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
    # Issue 82b: end the read-only transaction so the pooled connection is not
    # held across the client-paced streaming loop below. Later queries auto-begin
    # a new transaction; the after_begin listener restamps the RLS GUC from
    # session.info (set by the API-key auth dependency).
    await session.commit()

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
        # Issue 82b: release the pooled connection before the (possibly
        # multi-hundred-MB) R2 PUT. The Video insert below reacquires one.
        await session.commit()

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
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
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
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")

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


# ── Issue 322: Per-clip Short-title + hook-rewrite suggestions ────────────────


class TitleSuggestionsOut(BaseModel):
    """Response for POST /clips/{clip_id}/title-suggestions."""

    titles: list[dict]
    hook_rewrites: list[dict]
    disclaimer: str


@clips_router.post(
    "/{clip_id}/title-suggestions",
    response_model=TitleSuggestionsOut,
    # Kill switch (Issue 284): 503 when the llm_generation flag is off.
    dependencies=[Depends(require_flag("llm_generation")), Depends(require_budget)],
)
@limiter.limit("10/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
@limiter.limit(BRIEF_DAILY_LIMIT, key_func=creator_key)
async def get_clip_title_suggestions(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate per-clip Short-title candidates + hook-rewrite options (Issue 322).

    Grounded in the creator's DNA brief (cached prefix) + the clip's own
    transcript excerpt (uncached). Returns 5 ranked title candidates and 1–2
    hook rewrites. The honesty disclaimer is always appended by Python.

    Per-creator isolation: the clip's creator_id must match the authenticated
    creator. Usage is logged to the billing ledger.
    """
    from anthropic import APIConnectionError, APIStatusError, RateLimitError

    from billing.ledger import record_llm_usage
    from dna.profile import get_active as _get_active_dna
    from knowledge.clip_titles import generate_clip_title_suggestions
    from knowledge.util import extract_transcript_text

    await check_positive_balance(creator.id, session)

    # Fetch clip with isolation check.
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")

    # Fetch transcript for the clip's parent video.
    transcript = await session.scalar(
        select(Transcript).where(Transcript.video_id == clip.video_id)
    )
    clip_transcript = extract_transcript_text(
        transcript.segments_jsonb if transcript else None, 1500
    )

    # Fetch the creator's DNA brief.
    dna = await _get_active_dna(session, creator.id)
    dna_brief = dna.brief_text if dna else None

    channel_title = creator.channel_title or "Your Channel"

    try:
        result, usage = await generate_clip_title_suggestions(
            channel_title,
            dna_brief,
            clip_transcript,
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error(
            "clip_title_suggestions route LLM error clip=%s exc_type=%s",
            clip_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="AI title suggestions temporarily unavailable. Try again shortly.",
        ) from exc
    except ValueError as exc:
        logger.error("clip_title_suggestions parse error clip=%s err=%s", clip_id, exc)
        raise HTTPException(
            status_code=502, detail="AI returned an unexpected response format."
        ) from exc

    record_llm_tokens(
        provider="anthropic",
        model=settings.ANTHROPIC_MODEL_CLIP_TITLES,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read"],
        cache_creation_tokens=usage["cache_creation"],
    )
    await record_llm_usage(
        creator.id,
        usage,
        settings.COST_PER_MTOK_IN_SONNET,
        settings.COST_PER_MTOK_OUT_SONNET,
    )
    logger.info(
        "clip_title_suggestions done creator=%s clip=%s titles=%d",
        creator.id,
        clip_id,
        len(result.get("titles", [])),
    )
    return result


# ── Issue 323: Per-clip caption-hook / thumbnail-text concepts ────────────────


class CaptionHooksOut(BaseModel):
    """Response for POST /clips/{clip_id}/caption-hooks."""

    options: list[dict]
    disclaimer: str


@clips_router.post(
    "/{clip_id}/caption-hooks",
    response_model=CaptionHooksOut,
    # Kill switch (Issue 284): 503 when the llm_generation flag is off.
    dependencies=[Depends(require_flag("llm_generation")), Depends(require_budget)],
)
@limiter.limit("10/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
@limiter.limit(BRIEF_DAILY_LIMIT, key_func=creator_key)
async def get_clip_caption_hooks(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate per-clip caption-hook / thumbnail overlay-text options (Issue 323).

    Grounded in the creator's DNA brief + the clip's opening transcript hook.
    Returns 3–5 short overlay-text options. The honesty disclaimer is always
    appended by Python. Per-creator isolation enforced.
    """
    from anthropic import APIConnectionError, APIStatusError, RateLimitError

    from billing.ledger import record_llm_usage
    from dna.profile import get_active as _get_active_dna
    from knowledge.clip_captions import generate_clip_caption_hooks
    from knowledge.util import extract_transcript_text

    await check_positive_balance(creator.id, session)

    # Fetch clip with isolation check.
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")

    # Fetch opening transcript hook for the clip's parent video.
    transcript = await session.scalar(
        select(Transcript).where(Transcript.video_id == clip.video_id)
    )
    clip_hook = extract_transcript_text(transcript.segments_jsonb if transcript else None, 800)

    dna = await _get_active_dna(session, creator.id)
    dna_brief = dna.brief_text if dna else None
    channel_title = creator.channel_title or "Your Channel"

    try:
        result, usage = await generate_clip_caption_hooks(
            channel_title,
            dna_brief,
            clip_hook,
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error(
            "clip_caption_hooks route LLM error clip=%s exc_type=%s",
            clip_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="AI caption suggestions temporarily unavailable. Try again shortly.",
        ) from exc
    except ValueError as exc:
        logger.error("clip_caption_hooks parse error clip=%s err=%s", clip_id, exc)
        raise HTTPException(
            status_code=502, detail="AI returned an unexpected response format."
        ) from exc

    record_llm_tokens(
        provider="anthropic",
        model=settings.ANTHROPIC_MODEL_CLIP_CAPTIONS,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read"],
        cache_creation_tokens=usage["cache_creation"],
    )
    await record_llm_usage(
        creator.id,
        usage,
        settings.COST_PER_MTOK_IN_SONNET,
        settings.COST_PER_MTOK_OUT_SONNET,
    )
    logger.info(
        "clip_caption_hooks done creator=%s clip=%s options=%d",
        creator.id,
        clip_id,
        len(result.get("options", [])),
    )
    return result


# ── Issue 325: "Explain this clip" — Why-This-Clip LLM narrative ─────────────


class ClipExplanationOut(BaseModel):
    """Response for POST /clips/{clip_id}/explanation."""

    explanation: str
    cited_principle: str
    disclaimer: str


@clips_router.post(
    "/{clip_id}/explanation",
    response_model=ClipExplanationOut,
    # Kill switch (Issue 284): 503 when the llm_generation flag is off.
    dependencies=[Depends(require_flag("llm_generation")), Depends(require_budget)],
)
@limiter.limit("10/hour", key_func=creator_key)
@limiter.limit(LLM_DAILY_LIMIT, key_func=creator_key)
@limiter.limit(BRIEF_DAILY_LIMIT, key_func=creator_key)
async def get_clip_explanation(
    request: Request,
    clip_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a plain-language Why-This-Clip explanation (Issue 325).

    Returns a 2–4 sentence DNA-grounded explanation that explicitly cites one
    named principle from docs/CLIPPING_PRINCIPLES.md. The honesty disclaimer is
    always appended by Python. Per-creator isolation enforced.
    """
    from anthropic import APIConnectionError, APIStatusError, RateLimitError

    from billing.ledger import record_llm_usage
    from dna.profile import get_active as _get_active_dna
    from knowledge.clip_explain import generate_clip_explanation
    from knowledge.util import extract_transcript_text

    await check_positive_balance(creator.id, session)

    # Fetch clip with isolation check.
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")

    # Fetch transcript.
    transcript = await session.scalar(
        select(Transcript).where(Transcript.video_id == clip.video_id)
    )
    clip_transcript = extract_transcript_text(
        transcript.segments_jsonb if transcript else None, 1200
    )

    dna = await _get_active_dna(session, creator.id)
    dna_brief = dna.brief_text if dna else None
    channel_title = creator.channel_title or "Your Channel"

    # Pull principle + score from signals_jsonb (populated by the clip engine).
    signals = clip.signals_jsonb or {}
    clip_principle = signals.get("principle", "Audience-fit over generic virality")
    clip_score = clip.score

    try:
        result, usage = await generate_clip_explanation(
            channel_title,
            dna_brief,
            clip_principle,
            clip_score,
            clip.start_s,
            clip.end_s,
            clip_transcript,
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error(
            "clip_explain route LLM error clip=%s exc_type=%s",
            clip_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="AI explanation temporarily unavailable. Try again shortly.",
        ) from exc
    except ValueError as exc:
        logger.error("clip_explain parse error clip=%s err=%s", clip_id, exc)
        raise HTTPException(
            status_code=502, detail="AI returned an unexpected response format."
        ) from exc

    record_llm_tokens(
        provider="anthropic",
        model=settings.ANTHROPIC_MODEL_CLIP_EXPLAIN,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read"],
        cache_creation_tokens=usage["cache_creation"],
    )
    await record_llm_usage(
        creator.id,
        usage,
        settings.COST_PER_MTOK_IN_SONNET,
        settings.COST_PER_MTOK_OUT_SONNET,
    )
    logger.info(
        "clip_explain done creator=%s clip=%s principle=%s",
        creator.id,
        clip_id,
        result.get("cited_principle", ""),
    )
    return result


# ── Issue 192: stream-VOD recap summaries — API front door ────────────────────

summaries_router = APIRouter(prefix="/summaries", tags=["summaries"])


class SummarySegmentOut(BaseModel):
    """One chronological recap segment (the shape persisted to ``summaries.segments``).

    ``principle`` is an exact named principle from docs/CLIPPING_PRINCIPLES.md —
    the same contract as clips. ``score`` is this creator's DNA-fit score for
    the segment; the UI headlines the honest tier, never the raw number.
    """

    start_s: float
    end_s: float
    score: float
    principle: str
    rationale: str


class SummaryOut(BaseModel):
    """A stream-VOD recap Summary row (Issue 190) with its selected segments."""

    id: str
    video_id: str
    status: str
    render_status: str
    target_duration_s: int
    segments: list[SummarySegmentOut]
    render_uri: str | None
    created_at: str


class SummaryListOut(BaseModel):
    """Envelope for GET /videos/{video_id}/summaries."""

    summaries: list[SummaryOut]


class SummaryQueuedOut(BaseModel):
    """202 Accepted response for POST /videos/{video_id}/summaries.

    Mirrors TaskQueuedOut's fail-open ``stream_url`` posture but keys on
    ``summary_id`` — the SSE stream and the resource share the same id, so the
    client never needs a separate task id.
    """

    summary_id: str
    status: str
    stream_url: str | None = None


def _summary_response(summary: Summary) -> dict:
    return {
        "id": str(summary.id),
        "video_id": str(summary.video_id),
        "status": summary.status.value,
        "render_status": summary.render_status.value,
        "target_duration_s": summary.target_duration_s,
        "segments": summary.segments or [],
        "render_uri": summary.render_uri,
        "created_at": summary.created_at.isoformat() if summary.created_at else "",
    }


async def _active_summary(
    session: AsyncSession, video_id: uuid.UUID, creator_id: uuid.UUID
) -> Summary | None:
    """The video's newest in-flight (pending/running, not failed) recap, if any.

    Used twice by ``create_summary``: the idempotency probe before insert, and
    the winner re-select when the ``uq_summaries_active`` partial unique index
    (migration 0046) rejects the loser of a concurrent double-POST (Issue 361).
    """
    return await session.scalar(
        select(Summary)
        .where(
            Summary.video_id == video_id,
            Summary.creator_id == creator_id,
            Summary.render_status.in_([RenderStatus.pending, RenderStatus.running]),
            Summary.status != SummaryStatus.failed,
        )
        .order_by(Summary.created_at.desc())
        .limit(1)
    )


@router.post(
    "/{video_id}/summaries",
    status_code=202,
    response_model=SummaryQueuedOut,
    # Kill switch (Issue 284): 503 when the render_intake flag is off.
    dependencies=[Depends(require_flag("render_intake"))],
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(RENDER_DAILY_LIMIT, key_func=creator_key)
async def create_summary(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Select recap segments for a video and queue the 16:9 recap render.

    Selection (``clip_engine.summary_select.select_recap_segments``) is pure and
    runs in-request from the video's already-scored clips — segment starts use
    ``setup_start_s`` so the recap clips the setup, not the aftermath. The heavy
    render happens in the ``render_summary`` Celery task (Issue 191).

    Idempotent: an existing Summary whose render is still pending/running is
    returned as-is instead of duplicating the job. Per-creator isolation: the
    video must belong to the authenticated creator, else 404.
    """
    await check_positive_balance(creator.id, session)

    video = await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    if video.origin != VideoOrigin.upload:
        raise HTTPException(
            status_code=400,
            detail=(
                "Recaps need your uploaded source file — we never download your "
                "video from YouTube (per their Terms of Service). Upload the "
                "original file to create a recap."
            ),
        )
    if video.ingest_status != IngestStatus.done:
        raise HTTPException(status_code=400, detail="Video is not fully ingested yet")
    if not video.source_uri:
        raise HTTPException(
            status_code=409,
            detail=(
                "Source media expired (72-hour retention) — re-upload the video to create a recap."
            ),
        )

    # Idempotency: a summary whose render is still in flight is THE recap for
    # this video — return it rather than enqueue a duplicate render.
    existing = await _active_summary(session, video_id, creator.id)
    if existing:
        return {
            "summary_id": str(existing.id),
            "status": "queued",
            "stream_url": f"/tasks/{existing.id}/events",
        }

    # Candidates: the video's existing scored clips (principle + rationale from
    # signals_jsonb — same source as _clip_response). Setup-start rule: the
    # segment begins at the setup, not the peak's aftermath.
    result = await session.execute(
        select(Clip).where(Clip.video_id == video_id, Clip.creator_id == creator.id)
    )
    candidates: list[dict] = []
    for clip in result.scalars():
        if clip.score is None:
            continue
        sj = clip.signals_jsonb or {}
        start_s = clip.setup_start_s if clip.setup_start_s is not None else clip.start_s
        candidates.append(
            {
                "start_s": float(start_s),
                "end_s": float(clip.end_s),
                "score": float(clip.score),
                "principle": sj.get("principle", ""),
                "rationale": sj.get("reasoning", ""),
            }
        )

    # Chapter boundaries (when a signal timeline exists) demote segments that
    # straddle a chapter mid-segment — parse_chapters()['chapters'] shape.
    chapters: list[dict] | None = None
    signals = await session.get(Signals, video_id)
    if signals and video.duration_s:
        from knowledge.chapters import find_chapter_boundaries

        boundaries = find_chapter_boundaries(signals.timeline_jsonb, float(video.duration_s))
        chapters = [{"timestamp_s": b} for b in boundaries]

    from clip_engine.summary_select import select_recap_segments

    segments = select_recap_segments(
        candidates,
        budget_s=float(settings.RECAP_TARGET_DURATION_MAX_S),
        chapters=chapters,
    )
    if not segments:
        raise HTTPException(
            status_code=422,
            detail=(
                "Not enough scored material yet — generate clips for this video "
                "first, then request a recap."
            ),
        )

    from dna.profile import get_active

    dna_profile = await get_active(session, creator.id)

    summary = Summary(
        creator_id=creator.id,
        video_id=video_id,
        target_duration_s=settings.RECAP_TARGET_DURATION_MAX_S,
        segments=segments,
        dna_version=dna_profile.version if dna_profile else None,
        status=SummaryStatus.ready,
    )
    session.add(summary)
    try:
        await session.commit()
    except IntegrityError:
        # Double-click race (Issue 361): both requests passed the in-flight probe;
        # the loser violates the uq_summaries_active partial unique index at
        # commit. Same pattern as videos.py link/upload — rollback, then return
        # the winner so exactly ONE render_summary job is ever enqueued.
        await session.rollback()
        winner = await _active_summary(session, video_id, creator.id)
        if winner is None:
            # The winner left the pending/running window inside the race — a
            # clean conflict beats a duplicate render; the client re-fetches.
            raise HTTPException(
                status_code=409,
                detail="A recap for this video was just created — refresh to see it.",
            ) from None
        return {
            "summary_id": str(winner.id),
            "status": "queued",
            "stream_url": f"/tasks/{winner.id}/events",
        }
    await session.refresh(summary)

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import render_summary as render_summary_task

    # `.delay()` is sync Redis I/O; offload from the request loop (scale-checklist B).
    await asyncio.to_thread(render_summary_task.delay, str(summary.id))
    # summary_id is the SSE stream key (the worker emits to task:{summary_id}:events).
    # Fail-open posture (Wave-5 Fix 1): a Redis blip returns stream_url=None
    # instead of 500-ing — the render is already enqueued and will run.
    stream_url: str | None = f"/tasks/{summary.id}/events"
    try:
        await progress.aset_owner(str(summary.id), str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "recap aset_owner failed (Redis down?) summary_id=%s err=%s",
            summary.id,
            exc,
        )
        stream_url = None

    return {
        "summary_id": str(summary.id),
        "status": "queued",
        "stream_url": stream_url,
    }


@router.get("/{video_id}/summaries", response_model=SummaryListOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_summaries(
    request: Request,
    video_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List the video's recap summaries, newest first. Creator-scoped: another
    creator's video — or a missing id — returns 404."""
    await get_owned(session, Video, video_id, creator.id, detail="Video not found")
    result = await session.execute(
        select(Summary)
        .where(Summary.video_id == video_id, Summary.creator_id == creator.id)
        .order_by(Summary.created_at.desc())
    )
    return {"summaries": [_summary_response(s) for s in result.scalars()]}


@summaries_router.get("/{summary_id}", response_model=SummaryOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_summary(
    request: Request,
    summary_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a single recap summary with its segments. Per-creator isolation:
    a foreign creator's summary returns 404, never 403."""
    summary = await get_owned(session, Summary, summary_id, creator.id, detail="Summary not found")
    return _summary_response(summary)


@summaries_router.get("/{summary_id}/download", response_model=None)
@limiter.limit("60/minute", key_func=creator_key)
async def download_summary(
    request: Request,
    summary_id: uuid.UUID,
    disposition: Literal["attachment", "inline"] = "attachment",
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | FileResponse:
    """Serve a rendered recap — the ``download_clip`` contract, verbatim.

    ``disposition=attachment`` forces a download; ``inline`` backs the in-app
    16:9 player ``<video>``. 404 until the render lands. Prod (R2): 302 to a
    short-lived presigned GET URL; dev (local disk): streams the file.
    """
    summary = await get_owned(session, Summary, summary_id, creator.id, detail="Summary not found")
    if not summary.render_uri:
        raise HTTPException(status_code=404, detail="Recap not yet rendered")

    filename = f"recap-{summary_id}.mp4"

    presigned = presigned_download_url(
        summary.render_uri, filename=filename, disposition=disposition
    )
    if presigned is not None:
        return RedirectResponse(url=presigned, status_code=302)
    # Local-disk dev: stream the file straight from disk.
    path = Path(summary.render_uri)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recap file not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
        content_disposition_type=disposition,
    )
