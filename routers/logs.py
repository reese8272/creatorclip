"""Read surface for the beta event log (Issue 151).

`/api/logs/me` returns the requesting creator's own recent events. event_logs
carries no RLS policy, so isolation is enforced here at the application layer:
the query is filtered by `creator_id == creator.id`. A cross-creator operator
view (admin-gated) is a deliberate follow-up — for beta, operators query the
event_logs table directly; the per-creator endpoint is what ships behind auth.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from models import Creator, EventLog

router = APIRouter(prefix="/api/logs", tags=["logs"])


class EventLogItemOut(BaseModel):
    at: str
    source: str
    event: str
    level: str
    page: str | None = None
    target: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    extra: dict | None = None


class EventLogListOut(BaseModel):
    events: list[EventLogItemOut]


@router.get("/me", response_model=EventLogListOut)
async def my_events(
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    rows = (
        (
            await session.execute(
                select(EventLog)
                .where(EventLog.creator_id == creator.id)
                .order_by(EventLog.at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    return {
        "events": [
            {
                "at": r.at.isoformat(),
                "source": r.source,
                "event": r.event,
                "level": r.level,
                "page": r.page,
                "target": r.target,
                "status_code": r.status_code,
                "duration_ms": r.duration_ms,
                "extra": r.extra,
            }
            for r in rows
        ]
    }
