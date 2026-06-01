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
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from config import settings
from db import get_session
from limiter import creator_key, limiter
from models import (
    Creator,
    CreatorDna,
    CreatorInsight,
    DnaStatus,
    IngestStatus,
    InsightType,
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


class AnalyticsSummaryOut(BaseModel):
    period: str
    videos_in_period: int
    total_views: int
    total_watch_time_h: float
    avg_view_duration_s: float | None
    avg_engagement_rate: float | None
    metrics_available: bool


class InsightOut(BaseModel):
    id: str
    video_id: str | None
    insight_type: str
    title: str | None
    content: str
    dna_version: int | None
    is_saved: bool
    created_at: str


class AnalyzePerformerIn(BaseModel):
    video_id: str
    performer_kind: str  # "top" or "bottom"


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
@limiter.limit("60/minute", key_func=creator_key)
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
    # Issue 104: the prior func.nullif(Video.kind != VideoKind.short, True)
    # pattern always returned 0 because the Python != operator produced a
    # boolean, not a SQL expression, so nullif received a literal True on
    # both branches and count(NULL) = 0.  The ANSI SQL:2003 FILTER clause
    # (COUNT(*) FILTER (WHERE <condition>)) is the idiomatic Postgres fix —
    # fully supported via SQLAlchemy's func.count().filter().
    totals_row = (
        await session.execute(
            select(
                func.count(Video.id).label("videos_analyzed"),
                func.count().filter(Video.kind == VideoKind.short).label("shorts"),
                func.count().filter(Video.kind == VideoKind.long).label("longs"),
                func.count()
                .filter(Video.ingest_status == IngestStatus.done)
                .label("ingested_done"),
                func.coalesce(func.sum(Video.duration_s), 0.0).label("total_secs"),
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
        (
            await session.execute(
                select(CreatorDna)
                .where(
                    CreatorDna.creator_id == creator.id,
                    CreatorDna.status.in_([DnaStatus.confirmed, DnaStatus.draft]),
                )
                .order_by(CreatorDna.version.desc())
            )
        )
        .scalars()
        .first()
    )
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


_PERIOD_DAYS: dict[str, int | None] = {"7d": 7, "28d": 28, "90d": 90, "all": None}


@router.get("/analytics", response_model=AnalyticsSummaryOut)
@limiter.limit("60/minute", key_func=creator_key)
async def get_analytics_summary(
    request: Request,
    period: str = Query(default="28d", pattern="^(7d|28d|90d|all)$"),
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Aggregate YouTube metrics for the creator's videos in the given period.

    Period filters on ``video.published_at``; "all" has no date bound.
    Returns zero-counts when no data is available rather than 404, so the
    dashboard can show an empty state without a separate error path.
    """
    days = _PERIOD_DAYS[period]
    cutoff = datetime.now(UTC) - timedelta(days=days) if days is not None else None

    stmt = (
        select(
            func.count(Video.id).label("cnt"),
            func.coalesce(func.sum(VideoMetrics.views), 0).label("total_views"),
            func.coalesce(func.sum(VideoMetrics.watch_time_s), 0).label("total_watch_s"),
            func.avg(VideoMetrics.avg_view_duration_s).label("avg_dur"),
            func.avg(VideoMetrics.engagement_rate).label("avg_eng"),
        )
        .join(VideoMetrics, VideoMetrics.video_id == Video.id)
        .where(Video.creator_id == creator.id)
    )
    if cutoff is not None:
        stmt = stmt.where(Video.published_at >= cutoff)

    row = (await session.execute(stmt)).one()
    cnt, total_views, total_watch_s, avg_dur, avg_eng = row

    return {
        "period": period,
        "videos_in_period": int(cnt or 0),
        "total_views": int(total_views or 0),
        "total_watch_time_h": round(float(total_watch_s or 0) / 3600, 1),
        "avg_view_duration_s": round(float(avg_dur), 1) if avg_dur is not None else None,
        "avg_engagement_rate": round(float(avg_eng), 4) if avg_eng is not None else None,
        "metrics_available": int(cnt or 0) > 0,
    }


# ── AI per-performer analysis (Issue 117) ────────────────────────────────────

_HAIKU_MODEL = "claude-haiku-4-5-20251001"


def _build_analysis_prompt(
    video_title: str,
    kind: str,
    views: int | None,
    engagement_rate: float | None,
    performer_kind: str,
    dna_brief: str | None,
) -> str:
    views_str = f"{views:,}" if views is not None else "unknown"
    eng_str = f"{(engagement_rate * 100):.1f}%" if engagement_rate is not None else "unknown"
    perf_label = "top performer" if performer_kind == "top" else "underperformer"
    dna_context = f"\n\nCreator DNA summary:\n{dna_brief[:800]}" if dna_brief else ""
    return (
        f'Analyse why "{video_title}" ({kind}) is a {perf_label} for this creator. '
        f"It has {views_str} views and {eng_str} engagement rate.{dna_context}\n\n"
        "In 2-4 sentences: explain the specific factors that made it over- or under-perform "
        "relative to this creator's audience and style. Be concrete and cite the numbers. "
        "Do not promise virality or make guarantees. End with one actionable implication."
    )


@router.post("/analyze-performer", response_model=InsightOut)
@limiter.limit("20/hour", key_func=creator_key)
async def analyze_performer(
    request: Request,
    body: AnalyzePerformerIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate an AI analysis for a top or bottom performer video.

    Uses Haiku 4.5 (fast, low cost). Cached: if an insight for this
    (video, dna_version) already exists, returns it without a new LLM call.
    """
    try:
        video_id = uuid.UUID(body.video_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid video_id") from exc

    video = await session.get(Video, video_id)
    if not video or video.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Video not found")

    metrics_row = (
        (await session.execute(select(VideoMetrics).where(VideoMetrics.video_id == video_id)))
        .scalars()
        .first()
    )

    dna_row = (
        (
            await session.execute(
                select(CreatorDna)
                .where(
                    CreatorDna.creator_id == creator.id,
                    CreatorDna.status.in_([DnaStatus.confirmed, DnaStatus.draft]),
                )
                .order_by(CreatorDna.version.desc())
            )
        )
        .scalars()
        .first()
    )
    dna_version = dna_row.version if dna_row else None

    # Cache check: return existing insight for this video + DNA version
    existing = (
        (
            await session.execute(
                select(CreatorInsight).where(
                    CreatorInsight.creator_id == creator.id,
                    CreatorInsight.video_id == video_id,
                    CreatorInsight.insight_type == InsightType.performer_analysis,
                    CreatorInsight.dna_version == dna_version,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return _insight_to_dict(existing)

    # Build and call Haiku
    prompt = _build_analysis_prompt(
        video_title=video.title or video.youtube_video_id,
        kind=video.kind.value,
        views=metrics_row.views if metrics_row else None,
        engagement_rate=metrics_row.engagement_rate if metrics_row else None,
        performer_kind=body.performer_kind,
        dna_brief=dna_row.brief_text if dna_row else None,
    )

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        msg = await __import__("asyncio").to_thread(
            client.messages.create,
            model=_HAIKU_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        content = msg.content[0].text if msg.content else "Analysis unavailable."
    except Exception as exc:
        logger.warning("performer analysis LLM failed video=%s err=%s", video_id, exc)
        raise HTTPException(
            status_code=503, detail="Analysis service temporarily unavailable"
        ) from exc

    title = f"Why '{video.title or video.youtube_video_id}' {'excelled' if body.performer_kind == 'top' else 'underperformed'}"
    insight = CreatorInsight(
        creator_id=creator.id,
        video_id=video_id,
        insight_type=InsightType.performer_analysis,
        title=title,
        content=content,
        dna_version=dna_version,
        is_saved=False,
    )
    session.add(insight)
    await session.commit()
    await session.refresh(insight)
    return _insight_to_dict(insight)


@router.post("/save/{insight_id}", response_model=InsightOut)
@limiter.limit("60/minute", key_func=creator_key)
async def save_insight(
    request: Request,
    insight_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Toggle the saved state of an insight."""
    insight = await session.get(CreatorInsight, insight_id)
    if not insight or insight.creator_id != creator.id:
        raise HTTPException(status_code=404, detail="Insight not found")
    insight.is_saved = not insight.is_saved
    await session.commit()
    await session.refresh(insight)
    return _insight_to_dict(insight)


@router.get("/saved", response_model=list[InsightOut])
@limiter.limit("60/minute", key_func=creator_key)
async def list_saved_insights(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return all saved insights for the creator, newest first."""
    rows = (
        (
            await session.execute(
                select(CreatorInsight)
                .where(CreatorInsight.creator_id == creator.id, CreatorInsight.is_saved.is_(True))
                .order_by(CreatorInsight.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [_insight_to_dict(r) for r in rows]


def _insight_to_dict(ins: CreatorInsight) -> dict:
    return {
        "id": str(ins.id),
        "video_id": str(ins.video_id) if ins.video_id else None,
        "insight_type": ins.insight_type.value,
        "title": ins.title,
        "content": ins.content,
        "dna_version": ins.dna_version,
        "is_saved": ins.is_saved,
        "created_at": ins.created_at.isoformat(),
    }
