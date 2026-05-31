"""Aggregated insights endpoint (Issue 93).

Provides ``/creators/me/insights`` — a single roll-up of channel-level
metrics, top + bottom performers, and DNA stats so the rebuilt insights
page can render with one fetch instead of joining four endpoints in JS.

This is per-creator-scoped at the SQL layer (RLS + explicit
``creator_id == creator.id`` filters); the response carries only data
belonging to the requesting creator.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import limiter
from models import (
    Creator,
    CreatorDna,
    DnaStatus,
    IngestStatus,
    Video,
    VideoKind,
    VideoMetrics,
)

router = APIRouter(prefix="/creators/me/insights", tags=["insights"])
logger = logging.getLogger(__name__)


# ── Response models ─────────────────────────────────────────────────────────


class PerformerOut(BaseModel):
    video_id: str
    youtube_video_id: str
    title: str | None
    kind: str
    views: int | None
    engagement_rate: float | None


class ChannelTotalsOut(BaseModel):
    videos_analyzed: int
    shorts: int
    longs: int
    ingested_done: int
    total_minutes_processed: float


class DnaStatsOut(BaseModel):
    version: int | None
    status: str | None
    optimal_clip_len_s: float | None
    best_source_region: str | None
    optimal_upload_gap_h: float | None


class InsightsOut(BaseModel):
    totals: ChannelTotalsOut
    dna: DnaStatsOut
    top_performers: list[PerformerOut]
    bottom_performers: list[PerformerOut]


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _fetch_performers(
    session: AsyncSession,
    creator_id: uuid.UUID,
    video_ids: list[uuid.UUID],
) -> list[dict]:
    """Resolve a list of Video UUIDs into title + metrics.

    Preserves the input ordering (DNA ranks them by engagement). Videos
    that no longer exist or belong to a different creator are dropped —
    same defensive posture as every other per-creator query.
    """
    if not video_ids:
        return []

    result = await session.execute(
        select(Video, VideoMetrics)
        .outerjoin(VideoMetrics, VideoMetrics.video_id == Video.id)
        .where(
            Video.creator_id == creator_id,
            Video.id.in_(video_ids),
        )
    )
    by_id: dict[uuid.UUID, dict] = {}
    for row in result.all():
        video: Video = row[0]
        metrics: VideoMetrics | None = row[1]
        by_id[video.id] = {
            "video_id": str(video.id),
            "youtube_video_id": video.youtube_video_id,
            "title": video.title,
            "kind": video.kind.value,
            "views": metrics.views if metrics else None,
            "engagement_rate": metrics.engagement_rate if metrics else None,
        }
    return [by_id[vid] for vid in video_ids if vid in by_id]


def _coerce_uuid_list(raw: object) -> list[uuid.UUID]:
    """Turn a JSONB list of strings into a list of UUIDs, silently
    dropping malformed entries. Defensive against historical rows
    whose top/bottom lists were typed differently."""
    if not isinstance(raw, list):
        return []
    out: list[uuid.UUID] = []
    for item in raw:
        try:
            out.append(uuid.UUID(str(item)))
        except (ValueError, TypeError):
            continue
    return out


# ── Endpoint ────────────────────────────────────────────────────────────────


@router.get("", response_model=InsightsOut)
@limiter.limit("60/minute")
async def get_insights(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Channel-level insights for the rebuilt insights page.

    Single fetch; the rebuilt UI renders four panels off this payload.
    Per-creator isolation enforced at every SELECT.
    """
    # ── Channel totals ──────────────────────────────────────────────
    totals_row = (
        await session.execute(
            select(
                func.count(Video.id),
                func.count(func.nullif(Video.kind != VideoKind.short, True)),
                func.count(func.nullif(Video.kind != VideoKind.long, True)),
                func.count(func.nullif(Video.ingest_status != IngestStatus.done, True)),
                func.coalesce(func.sum(Video.duration_s), 0.0),
            ).where(Video.creator_id == creator.id)
        )
    ).one()

    videos_analyzed, shorts, longs, ingested_done, total_secs = totals_row
    totals = {
        "videos_analyzed": int(videos_analyzed or 0),
        "shorts": int(shorts or 0),
        "longs": int(longs or 0),
        "ingested_done": int(ingested_done or 0),
        # Round to 1 decimal — the UI renders this as a single value.
        "total_minutes_processed": round(float(total_secs or 0.0) / 60.0, 1),
    }

    # ── DNA stats (active = latest confirmed, else latest draft) ───
    dna_row = (
        await session.execute(
            select(CreatorDna)
            .where(
                CreatorDna.creator_id == creator.id,
                CreatorDna.status.in_([DnaStatus.confirmed, DnaStatus.draft]),
            )
            .order_by(CreatorDna.version.desc())
        )
    ).scalars().first()
    dna_stats: dict = {
        "version": dna_row.version if dna_row else None,
        "status": dna_row.status.value if dna_row else None,
        "optimal_clip_len_s": dna_row.optimal_clip_len_s if dna_row else None,
        "best_source_region": dna_row.best_source_region if dna_row else None,
        "optimal_upload_gap_h": dna_row.optimal_upload_gap_h if dna_row else None,
    }

    # ── Top + bottom performers (resolved from DNA JSONB) ──────────
    top_ids = _coerce_uuid_list(dna_row.top_video_ids_jsonb if dna_row else None)
    bottom_ids = _coerce_uuid_list(dna_row.bottom_video_ids_jsonb if dna_row else None)
    top_performers = await _fetch_performers(session, creator.id, top_ids)
    bottom_performers = await _fetch_performers(session, creator.id, bottom_ids)

    return {
        "totals": totals,
        "dna": dna_stats,
        "top_performers": top_performers,
        "bottom_performers": bottom_performers,
    }
