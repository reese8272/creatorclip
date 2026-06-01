import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import creator_key, limiter
from models import AudienceActivity, Creator
from upload_intel.timing import best_upload_windows, optimal_gap_hours

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/creators", tags=["upload-intel"])


class UploadWindowOut(BaseModel):
    day_of_week: int
    day_name: str
    hour: int
    label: str
    activity_index: float


class UploadIntelOut(BaseModel):
    best_windows: list[UploadWindowOut]
    optimal_gap_hours: float | None
    data_available: bool


@router.get("/me/upload-intel", response_model=UploadIntelOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_upload_intel(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return best upload windows and estimated optimal gap from audience activity."""
    result = await session.execute(
        select(AudienceActivity).where(AudienceActivity.creator_id == creator.id)
    )
    rows = list(result.scalars())

    windows = best_upload_windows(rows)
    gap_h = optimal_gap_hours(rows)

    return {
        "best_windows": windows,
        "optimal_gap_hours": gap_h,
        "data_available": len(rows) > 0,
    }
