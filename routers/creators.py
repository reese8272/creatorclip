from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import limiter
from models import Creator
from youtube.analytics import check_data_gate

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


@router.post("/me/dna/build", status_code=202, response_model=BuildQueuedOut)
@limiter.limit("120/minute")
async def build_dna(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    """Queue a DNA build for the current creator. Returns a Celery task_id."""
    from worker.tasks import build_dna as build_dna_task

    task = build_dna_task.delay(str(creator.id))
    return {"task_id": task.id, "status": "queued"}


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
