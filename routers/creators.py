import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.spend_guard import require_budget
from config import settings
from db import get_session
from dna import identity as identity_module
from dna.onboarding import resolve_setup_step
from flags import require_flag
from limiter import creator_key, limiter
from models import AnalysisMode, Clip, Creator, CreatorIdentity, CreatorStyle
from routers._enqueue import enqueue_stream_task
from routers._schemas import SetupStepOut, TaskQueuedOut
from youtube.analytics import check_data_gate
from youtube.categories import NICHE_OPTIONS, labels_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/creators", tags=["creators"])


class CreatorMeOut(BaseModel):
    id: str
    channel_id: str | None
    channel_title: str | None
    email: str | None
    onboarding_state: str
    # Issue 125 — surfaced so the dashboard knows which intake CTA to render
    # (auto = silent ingest on link; selective / manual = explicit Queue button).
    analysis_mode: str
    created_at: str
    # 2026-06-08 — nested aggregate so the frontend renders next-step
    # guidance from one fetch instead of inferring across 5 endpoints.
    setup: SetupStepOut


class AnalysisModeIn(BaseModel):
    """Body for PATCH /creators/me/analysis-mode (Issue 125)."""

    analysis_mode: AnalysisMode = Field(
        ..., description="Video intake mode: auto, selective, or manual."
    )


class AnalysisModeOut(BaseModel):
    analysis_mode: str


class DataGateOut(BaseModel):
    long_form_videos: int
    shorts: int
    long_form_ready: bool
    shorts_ready: bool
    # Issue 203: how many more published videos of each kind unlock DNA
    # (0 when that kind's threshold is already met).
    remaining_long_form: int
    remaining_shorts: int
    ready: bool


# Issue 108: BuildQueuedOut + CatalogSyncQueuedOut were near-identical
# three-field shapes — both subclass TaskQueuedOut now. Identical wire
# shape; only the type name differs at call sites (kept for docs/OpenAPI).
class BuildQueuedOut(TaskQueuedOut):
    """202 Accepted response for POST /creators/me/dna/build (Issue 86)."""


class CatalogSyncQueuedOut(TaskQueuedOut):
    """202 Accepted response for POST /creators/me/catalog/sync (Issue 92)."""


class DnaProfileOut(BaseModel):
    id: str
    version: int
    status: str
    brief_text: str | None
    optimal_clip_len_s: float | None
    best_source_region: str | None
    optimal_upload_gap_h: float | None
    created_at: str


class StyleNotesOut(BaseModel):
    """Distilled review-feedback style preferences (Issue 371) — surfaced
    verbatim: this text is exactly what scoring injects."""

    text: str
    source_count: int
    updated_at: str


class DnaGetOut(BaseModel):
    profile: DnaProfileOut | None
    message: str | None = None
    style_notes: StyleNotesOut | None = None


class DnaConfirmOut(BaseModel):
    id: str
    version: int
    status: str


# ── Brand Kit (Issue 186) ────────────────────────────────────────────────────


class BrandKitStyleIn(BaseModel):
    """Partial update body for PUT /creators/me/brand-kit.

    All fields are optional — only fields present in the request body are
    written; omitted fields are left unchanged in the existing kit row.
    """

    subtitle: str | None = None
    background: str | None = None
    captions_enabled: bool | None = None
    zoom_on_peak: bool | None = None
    denoise: bool | None = None
    aspect: str | None = None
    # Caption band default for every render (Issue 427): top | middle | bottom.
    caption_position: str | None = None


class BrandKitOut(BaseModel):
    subtitle: str | None
    background: str | None
    captions_enabled: bool
    zoom_on_peak: bool
    denoise: bool
    aspect: str | None
    caption_position: str | None


class BrandKitSuggestionOut(BaseModel):
    """Body returned by GET /creators/me/brand-kit/suggestion (Issue 187)."""

    field: str
    value: object
    count: int
    message: str


class BrandKitSuggestionAcceptIn(BaseModel):
    """Body for POST /creators/me/brand-kit/suggestion/accept (Issue 187)."""

    field: str
    value: object = Field(..., description="The suggested value to adopt as the kit default.")


def _style_row_to_dict(row: CreatorStyle | None) -> dict:
    """Normalise a CreatorStyle row (or None) into a BrandKitOut-compatible dict."""
    style: dict = row.style if row is not None else {}
    return {
        "subtitle": style.get("subtitle"),
        "background": style.get("background"),
        "captions_enabled": bool(style.get("captions_enabled", False)),
        "zoom_on_peak": bool(style.get("zoom_on_peak", False)),
        "denoise": bool(style.get("denoise", False)),
        "aspect": style.get("aspect"),
        "caption_position": style.get("caption_position"),
    }


async def _upsert_style_field(
    session: AsyncSession,
    creator_id: object,
    field: str,
    value: object,
) -> CreatorStyle:
    """Write a single style field into the creator's kit row (upsert).

    Extracted as a helper so both put_brand_kit and suggestion/accept share
    the same upsert logic (DRY).  Per-creator isolation: only touches the row
    whose creator_id matches the caller-supplied creator_id.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(CreatorStyle).where(CreatorStyle.creator_id == creator_id)
    )
    row = result.scalar_one_or_none()

    if row is None:
        row = CreatorStyle(creator_id=creator_id, style={})
        session.add(row)

    row.style = {**row.style, field: value}
    await session.commit()
    return row


@router.get("/me/brand-kit", response_model=BrandKitOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_brand_kit(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the creator's saved brand-kit style defaults.

    Returns zeroed/null defaults when no kit row exists yet — the frontend
    can pre-populate dropdowns without a separate existence check.
    Per-creator isolation: the query filters on the dep-injected creator.id.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(CreatorStyle).where(CreatorStyle.creator_id == creator.id)
    )
    row = result.scalar_one_or_none()
    return _style_row_to_dict(row)


@router.put("/me/brand-kit", response_model=BrandKitOut)
@limiter.limit("60/minute", key_func=creator_key)
async def put_brand_kit(
    request: Request,
    body: BrandKitStyleIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upsert the creator's brand-kit style defaults.

    Only provided (non-None) fields overwrite the stored kit; omitted fields
    are preserved. This means the frontend can send a single-field update
    (e.g. just 'subtitle') without clearing the rest of the kit.
    Per-creator isolation: we only ever read/write the row whose creator_id
    matches the dep-injected creator.id.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(CreatorStyle).where(CreatorStyle.creator_id == creator.id)
    )
    row = result.scalar_one_or_none()

    if row is None:
        row = CreatorStyle(creator_id=creator.id, style={})
        session.add(row)

    # Merge only the explicitly-provided fields.
    updates: dict = {}
    if body.subtitle is not None:
        updates["subtitle"] = body.subtitle
    if body.background is not None:
        updates["background"] = body.background
    if body.captions_enabled is not None:
        updates["captions_enabled"] = body.captions_enabled
    if body.zoom_on_peak is not None:
        updates["zoom_on_peak"] = body.zoom_on_peak
    if body.denoise is not None:
        updates["denoise"] = body.denoise
    if body.aspect is not None:
        updates["aspect"] = body.aspect
    if body.caption_position is not None:
        if body.caption_position not in {"top", "middle", "bottom"}:
            raise HTTPException(
                status_code=422, detail="caption_position must be top|middle|bottom"
            )
        updates["caption_position"] = body.caption_position

    row.style = {**row.style, **updates}
    await session.commit()
    return _style_row_to_dict(row)


# ── Style learning (Issue 187) ──────────────────────────────────────────────


@router.get("/me/brand-kit/suggestion", response_model=BrandKitSuggestionOut)
@limiter.limit("60/minute", key_func=creator_key)
async def get_brand_kit_suggestion(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict | Response:
    """Detect a dominant render style from recent clips and suggest making it the default.

    Queries the last 20 clips for this creator where style_preset IS NOT NULL,
    then uses the mode-detection algorithm in preference.style_learn to find the
    first kit field whose value appears >= STYLE_LEARN_THRESHOLD times.

    Returns 204 (no content) when the history is sparse or no dominant is found.
    Message is framed honestly — no virality language.

    Per-creator isolation: only reads clip rows for the dep-injected creator.id.
    """
    from sqlalchemy import select

    from preference.style_learn import style_suggestion

    result = await session.execute(
        select(Clip.style_preset)
        .where(Clip.creator_id == creator.id)
        .where(Clip.style_preset.isnot(None))
        .order_by(Clip.created_at.desc())
        .limit(20)
    )
    history: list[dict] = [row[0] for row in result.all()]

    suggestion = style_suggestion(history, threshold=settings.STYLE_LEARN_THRESHOLD)
    if suggestion is None:
        return Response(status_code=204)

    field = suggestion["field"]
    value = suggestion["value"]
    count = suggestion["count"]
    message = f"You have used {value!r} for {field} {count} times — make it your default?"
    return {"field": field, "value": value, "count": count, "message": message}


@router.post(
    "/me/brand-kit/suggestion/accept",
    response_model=BrandKitOut,
    status_code=200,
)
@limiter.limit("30/minute", key_func=creator_key)
async def accept_brand_kit_suggestion(
    request: Request,
    body: BrandKitSuggestionAcceptIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Accept a style-learning suggestion and write it into the creator's brand kit.

    Calls the shared _upsert_style_field helper so the upsert logic is not
    duplicated from put_brand_kit.

    Per-creator isolation: _upsert_style_field only touches the row for creator.id.
    """
    row = await _upsert_style_field(session, creator.id, body.field, body.value)
    return _style_row_to_dict(row)


# ── Identity (Issue 83) ───────────────────────────────────────────────────────


class NicheOption(BaseModel):
    id: str
    label: str


class NichesOut(BaseModel):
    options: list[NicheOption]


class IdentityIn(BaseModel):
    # Pydantic validates shape; dna.identity.validate_* enforces semantic rules
    # (length, dedup, known niche ids) so the same rules apply to both the
    # router payload and any future internal callers.
    niches: list[str] = Field(..., min_length=1, max_length=3)
    audience_summary: str = Field(..., min_length=1)
    content_pillars: list[str] | None = None
    tone_tags: list[str] | None = None
    hard_nos: list[str] | None = None
    mission: str | None = None
    style_sample: str | None = None


class IdentityOut(BaseModel):
    version: int
    niches: list[str]
    audience_summary: str
    content_pillars: list[str] | None
    tone_tags: list[str] | None
    hard_nos: list[str] | None
    mission: str | None
    style_sample: str | None
    created_at: str


class IdentityGetOut(BaseModel):
    identity: IdentityOut | None
    conflict: str | None = None  # nudge text from dna/conflict.py; None = no conflict


class IdentityHistoryOut(BaseModel):
    versions: list[IdentityOut]


# ── Chat-driven intake (Issue 96) ──────────────────────────────────────────────
class IdentityChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class IdentityChatIn(BaseModel):
    # The client holds the short intake transcript and sends it each turn; the last
    # turn must be the creator's. Bounded so a client can't replay a huge history.
    history: list[IdentityChatTurn] = Field(..., min_length=1, max_length=40)


class ProposedProfileOut(BaseModel):
    # The validated profile the model proposed — NOT yet written. The creator
    # confirms it via POST /creators/me/identity (which validates again).
    niches: list[str]
    audience_summary: str
    content_pillars: list[str] | None = None
    tone_tags: list[str] | None = None
    hard_nos: list[str] | None = None
    mission: str | None = None


class IdentityChatOut(BaseModel):
    reply: str
    proposal: ProposedProfileOut | None = None


def _identity_to_dict(row: CreatorIdentity) -> dict:
    return {
        "version": row.version,
        "niches": row.niches or [],
        "audience_summary": row.audience_summary,
        "content_pillars": row.content_pillars,
        "tone_tags": row.tone_tags,
        "hard_nos": row.hard_nos,
        "mission": row.mission,
        "style_sample": row.style_sample,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/me", response_model=CreatorMeOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_me(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    setup = await resolve_setup_step(creator, session)
    return {
        "id": str(creator.id),
        "channel_id": creator.channel_id,
        "channel_title": creator.channel_title,
        "email": creator.email,
        "onboarding_state": creator.onboarding_state.value,
        "analysis_mode": creator.analysis_mode.value,
        "created_at": creator.created_at.isoformat(),
        "setup": setup,
    }


@router.patch("/me/analysis-mode", response_model=AnalysisModeOut)
@limiter.limit("60/minute", key_func=creator_key)
async def patch_analysis_mode(
    request: Request,
    body: AnalysisModeIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set the creator's video-intake control mode (Issue 125).

    - auto: linked videos ingest immediately (current implicit default)
    - selective: linked videos sit in the catalog until explicitly queued
    - manual: only creator-uploaded files are processed

    Per-creator isolation is implicit — the dep-injected `creator` IS the row
    we mutate; we never read another creator's id from the body.
    """
    creator.analysis_mode = body.analysis_mode
    session.add(creator)
    await session.commit()
    return {"analysis_mode": creator.analysis_mode.value}


@router.get("/me/data-gate", response_model=DataGateOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_data_gate(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    gate = await check_data_gate(session, creator.id)
    # Issue 203 — funnel visibility on the unlock delta: same fire-and-forget
    # DB-sink pattern as the sibling funnel events in this router (Issue 235),
    # via the bounded strong-ref scheduler (record_event_nowait, Issue 352).
    from event_log import record_event_nowait

    record_event_nowait(
        source="backend",
        event="data_gate_evaluated",
        creator_id=creator.id,
        extra={
            "ready": gate["ready"],
            "long_form_videos": gate["long_form_videos"],
            "shorts": gate["shorts"],
        },
    )
    return gate


@router.post("/me/catalog/sync", status_code=202, response_model=CatalogSyncQueuedOut)
@limiter.limit("5/minute", key_func=creator_key)
async def sync_catalog(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    """Pull the creator's YouTube uploads playlist into the videos table.

    Async via Celery — the playlistItems + per-video duration fan-out can
    exceed the LB timeout on large channels. The data-gate poll picks up
    the resulting Video rows. Rate-limited tightly (5/min) because every
    invocation costs YouTube quota. (Issue 87)
    """
    from observability import log_event
    from worker.tasks import sync_channel_catalog

    task, stream_url = await enqueue_stream_task(
        sync_channel_catalog,
        str(creator.id),
        creator_id=str(creator.id),
        log_label="sync_catalog",
    )

    log_event(
        "catalog_sync_requested",
        creator_id=str(creator.id),
        task_id=task.id,
    )
    # Issue 235 — route to queryable DB sink alongside the file-only log_event.
    from event_log import record_event_nowait

    record_event_nowait(
        source="backend",
        event="catalog_sync_started",
        creator_id=creator.id,
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "stream_url": stream_url,
    }


@router.post("/me/dna/build", status_code=202, response_model=BuildQueuedOut)
@limiter.limit("120/minute", key_func=creator_key)
async def build_dna(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    """Queue a DNA build for the current creator. Returns a Celery task_id.

    Also stamps task ownership in Redis so the SSE stream endpoint at
    ``/tasks/{task_id}/events`` (Issue 86) can verify the requesting creator
    owns the task before opening the event stream — prevents cross-creator
    stream attachment via guessed/leaked task ids.
    """
    from observability import log_event
    from worker.tasks import build_dna as build_dna_task

    task, stream_url = await enqueue_stream_task(
        build_dna_task,
        str(creator.id),
        creator_id=str(creator.id),
        log_label="build_dna",
    )

    log_event(
        "dna_build_requested",
        creator_id=str(creator.id),
        task_id=task.id,
    )
    # Issue 235 — route to queryable DB sink alongside the file-only log_event.
    from event_log import record_event_nowait

    record_event_nowait(
        source="backend",
        event="dna_build_started",
        creator_id=creator.id,
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "stream_url": stream_url,
    }


@router.get("/me/dna", response_model=DnaGetOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_dna(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the active DNA profile (confirmed preferred, falls back to latest draft)."""
    from dna.profile import get_active, get_style_notes

    profile = await get_active(session, creator.id)
    # Issue 371: what the system learned from the creator's review feedback —
    # shown verbatim (it is exactly the text injected into scoring). Present
    # even without a DNA profile: style notes only need feedback to exist.
    style_row = await get_style_notes(session, creator.id)
    style_notes = (
        {
            "text": style_row.notes_text,
            "source_count": style_row.source_count,
            "updated_at": style_row.updated_at.isoformat(),
        }
        if style_row
        else None
    )
    if not profile:
        return {
            "profile": None,
            "message": "No Creator DNA yet — build it from the setup screen to unlock personalised scoring.",
            "style_notes": style_notes,
        }
    return {
        "profile": {
            "id": str(profile.id),
            "version": profile.version,
            "status": profile.status.value,
            "brief_text": profile.brief_text,
            "optimal_clip_len_s": profile.optimal_clip_len_s,
            "best_source_region": profile.best_source_region,
            "optimal_upload_gap_h": profile.optimal_upload_gap_h,
            "created_at": profile.created_at.isoformat(),
        },
        "style_notes": style_notes,
    }


@router.post("/me/dna/confirm", response_model=DnaConfirmOut)
@limiter.limit("120/minute", key_func=creator_key)
async def confirm_dna(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Confirm the latest draft DNA profile, superseding any previously confirmed version."""
    from dna.profile import confirm_draft

    try:
        profile = await confirm_draft(session, creator.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    from observability import log_event

    log_event(
        "dna_confirmed",
        creator_id=str(creator.id),
        dna_id=str(profile.id),
        version=profile.version,
    )
    # Issue 235 — route to queryable DB sink alongside the file-only log_event.
    from event_log import record_event_nowait

    record_event_nowait(
        source="backend",
        event="dna_confirmed",
        creator_id=creator.id,
        extra={"version": profile.version},
    )
    return {"id": str(profile.id), "version": profile.version, "status": profile.status.value}


# ── Identity (Issue 83) ───────────────────────────────────────────────────────


@router.get("/niches", response_model=NichesOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_niches(request: Request) -> dict:
    """Return the YouTube category multi-select options for the intake form.

    Unauthenticated — the list is stable public data and the onboarding form
    needs it before the session JWT is fully wired in the browser.
    """
    return {"options": list(NICHE_OPTIONS)}


@router.get("/me/identity", response_model=IdentityGetOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_identity(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the current stated identity (if any) plus any conflict nudge.

    The conflict nudge is a one-liner the dashboard shows in-place when the
    stated niche and the inferred DNA disagree (per the 2026 honesty pattern —
    surface conflicts, do not silently override).
    """
    from dna.conflict import detect
    from dna.profile import get_active

    current = await identity_module.get_current(session, creator.id)
    body: dict = {"identity": None, "conflict": None}
    if current is not None:
        body["identity"] = _identity_to_dict(current)
        dna = await get_active(session, creator.id)
        nudge = detect(current, dna)
        if nudge is not None:
            body["conflict"] = nudge.message
    return body


@router.post("/me/identity", status_code=201, response_model=IdentityOut)
@limiter.limit(
    "30/hour", key_func=creator_key
)  # intake is rarely updated; keep abusive churn bounded
async def upsert_identity(
    request: Request,
    payload: IdentityIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Save a new identity version. Supersedes the prior current row.

    All validation (length, dedup, known niche ids) is delegated to
    ``dna.identity.validate_*`` so the same rules apply to internal callers.
    """
    try:
        niches = identity_module.validate_niches(payload.niches)
        audience = identity_module.validate_text(
            payload.audience_summary,
            max_chars=identity_module.MAX_AUDIENCE_CHARS,
            label="audience_summary",
        )
        mission = identity_module.validate_optional_text(
            payload.mission,
            max_chars=identity_module.MAX_MISSION_CHARS,
            label="mission",
        )
        style_sample = identity_module.validate_optional_text(
            payload.style_sample,
            max_chars=identity_module.MAX_STYLE_SAMPLE_CHARS,
            label="style_sample",
        )
        pillars = identity_module.validate_list(payload.content_pillars, label="content_pillars")
        tone = identity_module.validate_list(payload.tone_tags, label="tone_tags")
        nos = identity_module.validate_list(payload.hard_nos, label="hard_nos")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    row = await identity_module.upsert_identity(
        session,
        creator.id,
        niches=niches,
        audience_summary=audience,
        content_pillars=pillars,
        tone_tags=tone,
        hard_nos=nos,
        mission=mission,
        style_sample=style_sample,
    )
    # Issue 235 — funnel event: creator provided their stated identity, enabling
    # the DNA build step.  niche_count is the only signal — no PII, no content.
    from event_log import record_event_nowait

    record_event_nowait(
        source="backend",
        event="identity_saved",
        creator_id=creator.id,
        extra={"niche_count": len(niches)},
    )
    return _identity_to_dict(row)


@router.post(
    "/me/identity/chat",
    response_model=IdentityChatOut,
    # Kill switch (Issue 284) + creator spend budget (Issue 290): this is a
    # billed LLM route — stack the same gates as every sibling LLM route.
    dependencies=[Depends(require_flag("llm_generation")), Depends(require_budget)],
)
@limiter.limit("40/hour", key_func=creator_key)
async def identity_chat(
    request: Request,
    body: IdentityChatIn,
    creator: Creator = Depends(get_current_creator),
) -> dict:
    """One turn of the chat-driven intake (Issue 96).

    The client holds the short transcript and posts it each turn; the model asks
    the next question or — once it has enough — returns a validated ``proposal``.
    The proposal is **not written here**: the creator confirms it via
    ``POST /creators/me/identity`` (the same validate-then-write path the wizard
    uses). See ``chat/intake.py`` for the prompt-injection posture. Per-creator by
    construction — the turn reads no other creator's data and writes nothing.
    """
    from chat import intake

    history = [{"role": t.role, "content": t.content} for t in body.history]
    if history[-1]["role"] != "user":
        raise HTTPException(status_code=422, detail="The last message must be from the creator.")
    return await intake.run_intake_turn(creator.id, history)


@router.get("/me/identity/history", response_model=IdentityHistoryOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_identity_history(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return identity versions for the current creator, newest first (max 20)."""
    rows = await identity_module.get_history(session, creator.id)
    return {"versions": [_identity_to_dict(r) for r in rows]}


# ── Channel Fingerprint (Issue 379) ─────────────────────────────────────────────
#
# Two tiers, per the YouTube API Services Policies ToS review recorded in
# docs/DECISIONS.md (2026-07-30):
#   1. Private in-app view — the full DNA (identity + the analytics-derived
#      CreatorDna stats) shown only to the authorizing creator. Permitted by
#      III.E.3.b. That view is served by the existing GET /creators/me/dna,
#      GET /creators/me/identity, and GET /creators/me/insights endpoints —
#      no new data leaves the boundary there.
#   2. Shareable artifact (THIS endpoint) — a creator-triggered export that
#      leaves our boundary once downloaded/copied/posted. Because it leaves
#      the boundary, it may contain ONLY fields that are NOT derived from
#      YouTube Analytics/Data API responses (III.E.2, III.E.3.b): the
#      creator's own self-declared identity (dna/identity.py) and our
#      distillation of the creator's OWN in-app feedback (creator_style_notes,
#      Issue 371). Every field is audited field-by-field in docs/COMPLIANCE.md
#      under "Channel Fingerprint — shareable artifact".
#
# This is a POST, not a GET, and it is not surfaced from any other response
# (GET /creators/me, GET /creators/me/insights, GET /creators/me/identity do
# NOT include these fields) — the ONLY way to obtain this payload is the
# creator explicitly triggering the "Create shareable fingerprint" action in
# the UI. Nothing is persisted or made publicly reachable; the response is
# handed to the calling creator's own authenticated session to copy/download,
# same default-private posture as every other export in this app.

# Verbatim honesty constraint (CLAUDE.md) — must travel WITH the artifact
# since it leaves our UI. Matches the DisclaimerBand copy on Insights/Profile
# so the phrasing is identical everywhere a creator sees it.
FINGERPRINT_HONESTY_LINE = (
    "AutoClip predicts fit with your style and audience — it does not promise virality."
)


class FingerprintShareOut(BaseModel):
    """The shareable Channel Fingerprint (Issue 379).

    Every field is self-declared by the creator or distilled by us from the
    creator's OWN in-app feedback — never from a YouTube Analytics/Data API
    response. See docs/COMPLIANCE.md for the field-by-field provenance audit.
    """

    niches: list[str]
    content_pillars: list[str] | None
    tone_tags: list[str] | None
    mission: str | None
    style_summary: str | None
    honesty_line: str
    generated_at: str


@router.post("/me/fingerprint/share", response_model=FingerprintShareOut)
@limiter.limit("20/hour", key_func=creator_key)
async def share_fingerprint(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Assemble the shareable Channel Fingerprint on explicit creator request.

    Per-creator scoped like every other query here (``creator.id`` from the
    authenticated session). Reads ONLY ``creator_identity`` (self-declared)
    and ``creator_style_notes`` (distilled from the creator's own feedback,
    Issue 371) — deliberately does NOT touch ``CreatorDna`` (analytics-derived)
    or any ``VideoMetrics``/``VideoAnalytics`` table.
    """
    from dna.profile import get_style_notes

    identity = await identity_module.get_current(session, creator.id)
    style_row = await get_style_notes(session, creator.id)

    return {
        "niches": labels_for(identity.niches) if identity and identity.niches else [],
        "content_pillars": identity.content_pillars if identity else None,
        "tone_tags": identity.tone_tags if identity else None,
        "mission": identity.mission if identity else None,
        "style_summary": style_row.notes_text if style_row else None,
        "honesty_line": FINGERPRINT_HONESTY_LINE,
        "generated_at": datetime.now(UTC).isoformat(),
    }
