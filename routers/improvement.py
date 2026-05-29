import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from improvement import jobs
from limiter import limiter
from models import Creator, Video, VideoMetrics

router = APIRouter(prefix="/creators", tags=["improvement"])
logger = logging.getLogger(__name__)


@router.post("/me/improvement-brief", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/hour")
async def start_improvement_brief(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Enqueue an improvement brief (Claude + web_search) and return immediately.

    The brief is a ~120s call — too long for a synchronous request behind a proxy
    (Cloudflare 524s at ~100s). It runs on Celery; poll GET for the result. (Issue 75)
    """
    if not creator.channel_id:
        raise HTTPException(status_code=400, detail="Channel not connected")

    # Debounce: don't enqueue a second job (double LLM spend) while one is in flight.
    if await jobs.is_active(str(creator.id)):
        return await jobs.get_status(str(creator.id))

    # Fast-fail before enqueue if there's no data to brief on (scoped to THIS
    # creator — the prior unscoped query was a SEV-0 cross-creator leak, Issue 33).
    has_metrics = (
        await session.execute(
            select(func.count())
            .select_from(VideoMetrics)
            .join(Video, VideoMetrics.video_id == Video.id)
            .where(Video.creator_id == creator.id)
        )
    ).scalar_one()
    if not has_metrics:
        raise HTTPException(status_code=400, detail="Not enough data yet — link some videos first.")

    from worker.tasks import generate_improvement_brief

    await jobs.set_status(str(creator.id), "pending")
    generate_improvement_brief.delay(str(creator.id))
    return {"status": "pending"}


@router.get("/me/improvement-brief")
@limiter.limit("60/hour")
async def get_improvement_brief(
    request: Request,
    creator: Creator = Depends(get_current_creator),
) -> dict:
    """Poll the latest improvement-brief job for this creator.

    Returns {"status": none|pending|running|done|failed} plus "brief" when done or
    "error" when failed. Keyed by creator id, so a creator only ever sees its own.
    """
    return await jobs.get_status(str(creator.id))
