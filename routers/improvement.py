import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import creator_key, limiter
from models import Creator, ImprovementBrief, ImprovementBriefStatus, Video, VideoMetrics

router = APIRouter(prefix="/creators", tags=["improvement"])
logger = logging.getLogger(__name__)


# NOTE: deliberately NOT subclassed from TaskQueuedOut. The debounce path
# returns no task at all when a brief is already in flight, so `task_id`
# must be ``str | None`` — incompatible with TaskQueuedOut's `str`. The
# duplication is the lesser evil vs an LSP violation. (Issue 108)
class BriefQueuedOut(BaseModel):
    status: str
    task_id: str | None
    stream_url: str | None = None  # Issue 92: SSE endpoint; None on debounce-collapse


class ImprovementBriefOut(BaseModel):
    status: str  # pending | ready | failed | none
    brief: str | None
    requested_at: str | None
    completed_at: str | None
    error: str | None


@router.post(
    "/me/improvement-brief",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BriefQueuedOut,
)
@limiter.limit("10/hour", key_func=creator_key)
async def start_improvement_brief(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue an improvement-brief build for the current creator. Returns 202.

    The brief is a ~120s Claude + web_search call — too long for the request path
    (it can exceed a load-balancer timeout), so it runs in a Celery task and the
    GET handler polls the stored row. Mirrors the DNA-build 202 + poll precedent.
    """
    # Cheap, good-UX guards kept on the POST so a brand-new creator gets an
    # immediate, honest 400 instead of a pending job that can only fail later.
    if not creator.channel_id:
        raise HTTPException(status_code=400, detail="Channel not connected")

    # "Has any VideoMetrics for THIS creator" — scoped to the creator (the prior
    # unscoped query was a SEV-0 cross-creator leak, Issue 33). The full analytics
    # build happens in the worker; this is just a cheap existence check.
    has_metrics = await session.scalar(
        select(VideoMetrics.video_id)
        .join(Video, VideoMetrics.video_id == Video.id)
        .where(Video.creator_id == creator.id)
        .limit(1)
    )
    if has_metrics is None:
        raise HTTPException(
            status_code=400,
            detail="Not enough data yet — link some videos first.",
        )

    # One row per creator. Debounce: an in-flight build returns 202 without
    # re-enqueuing, so repeated clicks collapse onto the same job.
    row = await session.scalar(
        select(ImprovementBrief).where(ImprovementBrief.creator_id == creator.id)
    )
    if row is not None and row.status == ImprovementBriefStatus.pending:
        # Debounced: the in-flight task's owner key was stamped on its original
        # enqueue, so the client can re-subscribe to the same stream_url.
        stream_url = f"/tasks/{row.job_id}/events" if row.job_id else None
        return {"status": "pending", "task_id": row.job_id, "stream_url": stream_url}

    if row is None:
        row = ImprovementBrief(creator_id=creator.id)
        session.add(row)
    row.status = ImprovementBriefStatus.pending
    row.requested_at = datetime.now(UTC)
    row.brief_text = None
    row.error = None
    row.completed_at = None
    row.job_id = None
    await session.commit()

    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import generate_improvement_brief as generate_improvement_brief_task

    task = generate_improvement_brief_task.delay(str(creator.id))
    row.job_id = task.id
    await session.commit()

    # Wave-3 Fix B: stamp ownership for the SSE stream. Failure here is
    # observational — the brief is already enqueued and the row carries
    # the job_id, so the user can still poll the result. We fail open
    # (log + return stream_url=None) instead of 500-ing and leaving the
    # row in an inconsistent state. Same posture progress.aemit takes.
    stream_url = f"/tasks/{task.id}/events"
    try:
        await progress.aset_owner(task.id, str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning(
            "improvement_brief aset_owner failed (Redis down?) task=%s err=%s",
            task.id,
            exc,
        )
        stream_url = None

    logger.info("Improvement brief queued for creator %s (task %s)", creator.id, task.id)

    return {
        "status": "pending",
        "task_id": task.id,
        "stream_url": stream_url,
    }


@router.get("/me/improvement-brief", response_model=ImprovementBriefOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_improvement_brief(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the stored improvement brief for the current creator (poll target)."""
    row = await session.scalar(
        select(ImprovementBrief).where(ImprovementBrief.creator_id == creator.id)
    )
    if row is None:
        return {
            "status": "none",
            "brief": None,
            "requested_at": None,
            "completed_at": None,
            "error": None,
        }
    return {
        "status": row.status.value,
        "brief": row.brief_text,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "error": row.error,
    }
