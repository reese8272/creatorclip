import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import limiter
from models import Creator, Video, VideoMetrics

router = APIRouter(prefix="/creators", tags=["improvement"])
logger = logging.getLogger(__name__)


class ImprovementBriefOut(BaseModel):
    brief: str


@router.get("/me/improvement-brief", response_model=ImprovementBriefOut)
@limiter.limit("10/hour")
async def get_improvement_brief(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a data + research grounded improvement brief via Claude + web_search."""
    if not creator.channel_id:
        raise HTTPException(status_code=400, detail="Channel not connected")

    # Build analytics summary for the prompt. Scoped to THIS creator only —
    # the prior unscoped query was a SEV-0 cross-creator leak (Issue 33).
    metrics_result = await session.execute(
        select(VideoMetrics)
        .join(Video, VideoMetrics.video_id == Video.id)
        .where(Video.creator_id == creator.id)
        .order_by(VideoMetrics.fetched_at.desc())
        .limit(50)
    )
    all_metrics = list(metrics_result.scalars())
    if not all_metrics:
        raise HTTPException(
            status_code=400,
            detail="Not enough data yet — link some videos first.",
        )
    views_list = [m.views for m in all_metrics if m.views]
    eng_list = [m.engagement_rate for m in all_metrics if m.engagement_rate]
    dur_list = [m.avg_view_duration_s for m in all_metrics if m.avg_view_duration_s]

    def _avg(lst):
        return sum(lst) / len(lst) if lst else None

    analytics = {
        "channel_title": creator.channel_title,
        "videos_in_db": len(all_metrics),
        "avg_views": _avg(views_list),
        "avg_engagement_rate": _avg(eng_list),
        "avg_view_duration_s": _avg(dur_list),
    }

    from dna.profile import get_active

    dna_profile = await get_active(session, creator.id)
    dna_brief = dna_profile.brief_text if dna_profile else None

    from improvement.brief import generate_improvement_brief

    try:
        # 120s Anthropic+web_search call — offload so it never pins the API event
        # loop and stall other concurrent requests on this worker. (Issue 66)
        brief_text = await asyncio.to_thread(
            generate_improvement_brief,
            channel_title=creator.channel_title or "Unknown Channel",
            analytics=analytics,
            dna_brief=dna_brief,
        )
    except Exception as exc:
        logger.error("Improvement brief generation failed: %s", exc)
        raise HTTPException(status_code=502, detail="Brief generation failed — try again.") from exc

    return {"brief": brief_text}
