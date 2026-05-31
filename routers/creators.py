from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from dna import identity as identity_module
from limiter import limiter
from models import Creator
from youtube.analytics import check_data_gate
from youtube.categories import NICHE_OPTIONS

router = APIRouter(prefix="/creators", tags=["creators"])


class CreatorMeOut(BaseModel):
    id: str
    channel_id: str | None
    channel_title: str | None
    email: str | None
    onboarding_state: str
    created_at: str


class DataGateOut(BaseModel):
    long_form_videos: int
    shorts: int
    long_form_ready: bool
    shorts_ready: bool
    ready: bool


class BuildQueuedOut(BaseModel):
    task_id: str
    status: str
    stream_url: str  # Issue 86: SSE endpoint for live progress events


class CatalogSyncQueuedOut(BaseModel):
    task_id: str
    status: str


class DnaProfileOut(BaseModel):
    id: str
    version: int
    status: str
    brief_text: str | None
    optimal_clip_len_s: float | None
    best_source_region: str | None
    optimal_upload_gap_h: float | None
    created_at: str


class DnaGetOut(BaseModel):
    profile: DnaProfileOut | None
    message: str | None = None


class DnaConfirmOut(BaseModel):
    id: str
    version: int
    status: str


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


def _identity_to_dict(row) -> dict:
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
@limiter.limit("120/minute")
async def get_me(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    return {
        "id": str(creator.id),
        "channel_id": creator.channel_id,
        "channel_title": creator.channel_title,
        "email": creator.email,
        "onboarding_state": creator.onboarding_state.value,
        "created_at": creator.created_at.isoformat(),
    }


@router.get("/me/data-gate", response_model=DataGateOut)
@limiter.limit("120/minute")
async def get_data_gate(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await check_data_gate(session, creator.id)


@router.post("/me/catalog/sync", status_code=202, response_model=CatalogSyncQueuedOut)
@limiter.limit("5/minute")
async def sync_catalog(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    """Pull the creator's YouTube uploads playlist into the videos table.

    Async via Celery — the playlistItems + per-video duration fan-out can
    exceed the LB timeout on large channels. The data-gate poll picks up
    the resulting Video rows. Rate-limited tightly (5/min) because every
    invocation costs YouTube quota. (Issue 87)
    """
    from worker.tasks import sync_channel_catalog

    task = sync_channel_catalog.delay(str(creator.id))
    return {"task_id": task.id, "status": "queued"}


@router.post("/me/dna/build", status_code=202, response_model=BuildQueuedOut)
@limiter.limit("120/minute")
async def build_dna(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    """Queue a DNA build for the current creator. Returns a Celery task_id.

    Also stamps task ownership in Redis so the SSE stream endpoint at
    ``/tasks/{task_id}/events`` (Issue 86) can verify the requesting creator
    owns the task before opening the event stream — prevents cross-creator
    stream attachment via guessed/leaked task ids.
    """
    from worker import progress
    from worker.tasks import build_dna as build_dna_task

    task = build_dna_task.delay(str(creator.id))
    await progress.aset_owner(task.id, str(creator.id))
    return {
        "task_id": task.id,
        "status": "queued",
        "stream_url": f"/tasks/{task.id}/events",
    }


@router.get("/me/dna", response_model=DnaGetOut)
@limiter.limit("120/minute")
async def get_dna(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the active DNA profile (confirmed preferred, falls back to latest draft)."""
    from dna.profile import get_active

    profile = await get_active(session, creator.id)
    if not profile:
        return {
            "profile": None,
            "message": "No Creator DNA yet — build it from the setup screen to unlock personalised scoring.",
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
        }
    }


@router.post("/me/dna/confirm", response_model=DnaConfirmOut)
@limiter.limit("120/minute")
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
    return {"id": str(profile.id), "version": profile.version, "status": profile.status.value}


# ── Identity (Issue 83) ───────────────────────────────────────────────────────


@router.get("/niches", response_model=NichesOut)
@limiter.limit("120/minute")
async def list_niches(request: Request) -> dict:
    """Return the YouTube category multi-select options for the intake form.

    Unauthenticated — the list is stable public data and the onboarding form
    needs it before the session JWT is fully wired in the browser.
    """
    return {"options": list(NICHE_OPTIONS)}


@router.get("/me/identity", response_model=IdentityGetOut)
@limiter.limit("120/minute")
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
@limiter.limit("30/hour")  # intake is rarely updated; keep abusive churn bounded
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
    return _identity_to_dict(row)


@router.get("/me/identity/history", response_model=IdentityHistoryOut)
@limiter.limit("120/minute")
async def get_identity_history(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return identity versions for the current creator, newest first (max 20)."""
    rows = await identity_module.get_history(session, creator.id)
    return {"versions": [_identity_to_dict(r) for r in rows]}
