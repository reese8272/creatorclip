"""Scheduled-publish endpoints for ClipPublication (Issue 196).

Routes:
  POST   /clips/{clip_id}/publications         — schedule a publish at a chosen time
  GET    /clips/{clip_id}/publications          — list publications for a clip
  POST   /clips/{clip_id}/publications/{id}/confirm   — confirm a scheduled publish
  POST   /clips/{clip_id}/publications/{id}/cancel    — cancel a scheduled or confirmed publish
  GET    /clips/{clip_id}/publications/{id}    — get a single publication's status

All endpoints enforce per-creator isolation: the creator must own the clip.
Honesty constraint: no response or description promises virality.
Pre-audit: uploads always land private; the UI surfaces this via ``privacy_note``.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import creator_key, limiter
from models import (
    AudienceActivity,
    Clip,
    ClipPublication,
    Creator,
    PublishPlatform,
    PublishStatus,
)
from routers._owned import get_owned
from upload_intel.timing import best_upload_windows

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clips", tags=["publications"])

# Statuses that are still mutable (can be confirmed / cancelled).
_MUTABLE_STATUSES = {PublishStatus.scheduled, PublishStatus.confirmed}

# Honesty note surfaced on every response — no virality promise.
_PRIVACY_NOTE = (
    "Pre-audit: clips are uploaded as private. Open YouTube Studio to publish publicly when ready."
)


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class SchedulePublishIn(BaseModel):
    """Request body for POST /clips/{clip_id}/publications."""

    scheduled_at: datetime
    platform: PublishPlatform = PublishPlatform.youtube

    @field_validator("scheduled_at")
    @classmethod
    def must_be_in_future(cls, v: datetime) -> datetime:
        """Reject past datetimes so the Beat sweep always has something to pick up."""
        # Normalise to UTC if tz-aware; reject naive datetimes (ambiguous).
        if v.tzinfo is None:
            raise ValueError("scheduled_at must be a timezone-aware datetime (UTC recommended)")
        now = datetime.now(UTC)
        if v <= now:
            raise ValueError("scheduled_at must be in the future")
        return v


class PublicationOut(BaseModel):
    """Response shape for a single ClipPublication row."""

    id: str
    clip_id: str
    creator_id: str
    task_id: str | None
    youtube_video_id: str | None
    status: str
    error: str | None
    scheduled_at: str | None
    platform: str
    confirmed_at: str | None
    created_at: str
    updated_at: str
    privacy_note: str

    @classmethod
    def from_pub(cls, pub: ClipPublication) -> "PublicationOut":
        return cls(
            id=str(pub.id),
            clip_id=str(pub.clip_id),
            creator_id=str(pub.creator_id),
            task_id=pub.task_id,
            youtube_video_id=pub.youtube_video_id,
            status=pub.status.value,
            error=pub.error,
            scheduled_at=pub.scheduled_at.isoformat() if pub.scheduled_at else None,
            platform=pub.platform.value,
            confirmed_at=pub.confirmed_at.isoformat() if pub.confirmed_at else None,
            created_at=pub.created_at.isoformat(),
            updated_at=pub.updated_at.isoformat(),
            privacy_note=_PRIVACY_NOTE,
        )


class PublicationListOut(BaseModel):
    """Envelope for GET /clips/{clip_id}/publications.

    ``truncated`` was added in Issue 339 — set to True when the hard cap of
    50 rows has been reached and additional publications may exist beyond it.
    Additive / backward-compatible: default is False.
    """

    publications: list[PublicationOut]
    suggested_windows: list[dict]
    privacy_note: str
    truncated: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_owned_clip(
    clip_id: str,
    creator: Creator,
    session: AsyncSession,
) -> Clip:
    """Fetch a Clip and assert it belongs to the requesting creator.

    Raises 404 if the clip does not exist or belongs to another creator
    (per-creator isolation — never leak existence of another creator's clip).
    """
    try:
        cid = uuid.UUID(clip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Clip not found") from None
    return await get_owned(session, Clip, cid, creator.id, detail="Clip not found")


async def _get_owned_publication(
    pub_id: str,
    clip_id: str,
    creator: Creator,
    session: AsyncSession,
) -> ClipPublication:
    """Fetch a ClipPublication and assert ownership through clip parentage.

    Raises 404 if the publication does not exist or belongs to another creator.
    """
    try:
        pid = uuid.UUID(pub_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Publication not found") from None
    await _get_owned_clip(clip_id, creator, session)
    return await get_owned(
        session, ClipPublication, pid, creator.id, detail="Publication not found"
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/{clip_id}/publications", response_model=PublicationOut, status_code=201)
@limiter.limit("30/minute", key_func=creator_key)
async def schedule_publication(
    clip_id: str,
    body: SchedulePublishIn,
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> PublicationOut:
    """Schedule a clip publication at a creator-chosen time.

    The created row starts with ``status=scheduled``. The creator must call
    POST …/confirm before the Beat sweep will enqueue the upload. This two-step
    flow prevents the sweep from immediately firing on a newly-created row before
    the creator has reviewed the scheduled time.

    ``scheduled_at`` is validated to be in the future; the platform must be
    ``youtube`` (only supported platform at this time).
    """
    clip = await _get_owned_clip(clip_id, creator, session)

    now = datetime.now(UTC)
    pub = ClipPublication(
        clip_id=clip.id,
        creator_id=creator.id,
        task_id=None,  # assigned by the Beat sweep when the upload is enqueued
        status=PublishStatus.scheduled,
        scheduled_at=body.scheduled_at,
        platform=body.platform,
        confirmed_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(pub)
    await session.commit()
    await session.refresh(pub)

    logger.info(
        "schedule_publication: creator=%s clip=%s scheduled_at=%s",
        creator.id,
        clip.id,
        body.scheduled_at.isoformat(),
    )
    return PublicationOut.from_pub(pub)


@router.get("/{clip_id}/publications", response_model=PublicationListOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_publications(
    clip_id: str,
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> PublicationListOut:
    """List all publications for a clip, newest first.

    Also returns the creator's top-3 upload-timing windows so the UI can
    pre-populate the scheduled_at picker with data-grounded suggestions.
    """
    clip = await _get_owned_clip(clip_id, creator, session)

    # Issue 339: query limit+1 to detect truncation without a separate COUNT.
    _PUB_LIST_LIMIT = 50
    pubs_result = await session.execute(
        select(ClipPublication)
        .where(ClipPublication.clip_id == clip.id)
        .order_by(ClipPublication.created_at.desc())
        .limit(_PUB_LIST_LIMIT + 1)
    )
    pubs_raw = list(pubs_result.scalars())
    pubs_truncated = len(pubs_raw) > _PUB_LIST_LIMIT
    pubs = pubs_raw[:_PUB_LIST_LIMIT]

    # Fetch audience activity for upload-window suggestions.
    _LIST_LIMIT = 200  # 7*24 = 168 max rows; cap defensively
    activity_result = await session.execute(
        select(AudienceActivity).where(AudienceActivity.creator_id == creator.id).limit(_LIST_LIMIT)
    )
    activity_rows = list(activity_result.scalars())
    windows = best_upload_windows(activity_rows, top_n=3)

    return PublicationListOut(
        publications=[PublicationOut.from_pub(p) for p in pubs],
        suggested_windows=windows,
        privacy_note=_PRIVACY_NOTE,
        truncated=pubs_truncated,
    )


@router.get("/{clip_id}/publications/{pub_id}", response_model=PublicationOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_publication(
    clip_id: str,
    pub_id: str,
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> PublicationOut:
    """Get a single publication's current status."""
    pub = await _get_owned_publication(pub_id, clip_id, creator, session)
    return PublicationOut.from_pub(pub)


@router.post("/{clip_id}/publications/{pub_id}/confirm", response_model=PublicationOut)
@limiter.limit("30/minute", key_func=creator_key)
async def confirm_publication(
    clip_id: str,
    pub_id: str,
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> PublicationOut:
    """Confirm a scheduled publication so the Beat sweep will enqueue it.

    Only rows in ``scheduled`` status may be confirmed; ``confirmed``, ``pending``,
    ``running``, ``done``, and ``failed`` rows return 409 (conflict).
    """
    pub = await _get_owned_publication(pub_id, clip_id, creator, session)

    if pub.status != PublishStatus.scheduled:
        raise HTTPException(
            status_code=409,
            detail=f"Publication is {pub.status.value}; only 'scheduled' publications can be confirmed",
        )

    now = datetime.now(UTC)
    pub.status = PublishStatus.confirmed
    pub.confirmed_at = now
    pub.updated_at = now
    await session.commit()
    await session.refresh(pub)

    logger.info(
        "confirm_publication: creator=%s pub=%s scheduled_at=%s",
        creator.id,
        pub.id,
        pub.scheduled_at.isoformat() if pub.scheduled_at else "immediate",
    )
    return PublicationOut.from_pub(pub)


@router.post("/{clip_id}/publications/{pub_id}/cancel", response_model=PublicationOut)
@limiter.limit("30/minute", key_func=creator_key)
async def cancel_publication(
    clip_id: str,
    pub_id: str,
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> PublicationOut:
    """Cancel a scheduled or confirmed publication.

    Only ``scheduled`` or ``confirmed`` rows can be cancelled (the upload has
    not yet been enqueued). Returns 409 for ``pending``, ``running``, ``done``,
    or ``failed`` rows, as the task is either in flight or already terminal.

    A cancelled publication's status is set to ``failed`` with a descriptive
    error message so the history is preserved and auditable.
    """
    pub = await _get_owned_publication(pub_id, clip_id, creator, session)

    if pub.status not in _MUTABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Publication is {pub.status.value}; "
                "only 'scheduled' or 'confirmed' publications can be cancelled"
            ),
        )

    now = datetime.now(UTC)
    pub.status = PublishStatus.failed
    pub.error = "Cancelled by creator"
    pub.updated_at = now
    await session.commit()
    await session.refresh(pub)

    logger.info(
        "cancel_publication: creator=%s pub=%s",
        creator.id,
        pub.id,
    )
    return PublicationOut.from_pub(pub)
