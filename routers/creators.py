from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import limiter
from models import Creator
from routers.schemas import (
    CreatorMeOut,
    DataGateOut,
    DnaConfirmOut,
    DnaOut,
    TaskQueuedOut,
)
from youtube.analytics import check_data_gate

router = APIRouter(prefix="/creators", tags=["creators"])


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


@router.post("/me/dna/build", status_code=202, response_model=TaskQueuedOut)
@limiter.limit("120/minute")
async def build_dna(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    """Queue a DNA build for the current creator. Returns a Celery task_id."""
    from worker.tasks import build_dna as build_dna_task

    task = build_dna_task.delay(str(creator.id))
    return {"task_id": task.id, "status": "queued"}


@router.get("/me/dna", response_model=DnaOut)
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
            "message": "No DNA profile yet. POST /creators/me/dna/build to start.",
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
