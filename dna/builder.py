"""
DNA builder: rank videos by recency-weighted engagement rate,
extract top/bottom performer patterns for the creator brief.
"""

import logging
import math
import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import (
    AudienceActivity,
    RetentionCurve,
    Signals,
    Transcript,
    Video,
    VideoKind,
    VideoMetrics,
)

logger = logging.getLogger(__name__)

_LAMBDA = math.log(2) / 90  # 90-day half-life
_HOOK_WORDS = 40  # first N words extracted as hook sample


# ── Pure helpers ──────────────────────────────────────────────────────────────


def _recency_weight(published_at: datetime | None) -> float:
    """Exponential recency decay: w = e^(-λ * age_days), λ = ln(2)/90."""
    if published_at is None:
        return 0.5
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    age_days = max(0, (datetime.now(UTC) - published_at).days)
    return math.exp(-_LAMBDA * age_days)


def _hook_text(segments_jsonb: dict) -> str:
    """Extract the first HOOK_WORDS words from transcript segments."""
    words: list[str] = []
    for seg in segments_jsonb.get("segments", []):
        for w in seg.get("words", []):
            words.append(w.get("word", ""))
            if len(words) >= _HOOK_WORDS:
                break
        if len(words) >= _HOOK_WORDS:
            break
    return " ".join(words).strip()


def _best_source_region(retention_rows: list, duration_s: float | None) -> str | None:
    """Return 'first_third' / 'middle' / 'last_third' with highest average retention."""
    if not retention_rows or not duration_s or duration_s <= 0:
        return None
    buckets: dict[str, list[float]] = {"first_third": [], "middle": [], "last_third": []}
    for r in retention_rows:
        frac = r.timestamp_s / duration_s
        if frac < 0.33:
            buckets["first_third"].append(r.audience_watch_ratio)
        elif frac < 0.66:
            buckets["middle"].append(r.audience_watch_ratio)
        else:
            buckets["last_third"].append(r.audience_watch_ratio)
    avgs = {k: (sum(v) / len(v) if v else 0.0) for k, v in buckets.items()}
    return max(avgs, key=lambda k: avgs[k])


def _optimal_clip_len_s(videos: list[dict]) -> float | None:
    """Median avg_view_duration_s of a video set as optimal clip length estimate."""
    durations = [v["avg_view_duration_s"] for v in videos if v.get("avg_view_duration_s")]
    if not durations:
        return None
    durations.sort()
    mid = len(durations) // 2
    if len(durations) % 2:
        return durations[mid]
    return (durations[mid - 1] + durations[mid]) / 2


def _optimal_upload_gap_h(activity_rows: list) -> float | None:
    """Average gap between top-3 activity peaks expressed in hours."""
    if len(activity_rows) < 2:
        return None
    top = sorted(activity_rows, key=lambda r: r.activity_index, reverse=True)[:3]
    times = sorted(r.day_of_week * 24 + r.hour for r in top)
    if len(times) < 2:
        return None
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    return sum(gaps) / len(gaps)


# ── Async data helpers ────────────────────────────────────────────────────────


async def rank_videos(session: AsyncSession, creator_id: uuid.UUID) -> list[dict]:
    """
    Return all metered videos sorted descending by weighted_score
    (engagement_rate × recency_weight).

    Readiness for DNA = a YouTube-side metrics row (views / engagement) exists.
    `ingest_status` (the local clip-engine pipeline state — download, transcribe,
    signals) is intentionally NOT a filter here: a catalog-synced video has
    `ingest_status=pending` forever unless the creator actually clips it, but
    its metrics are perfectly usable for DNA analysis. Requiring `ingest_status=done`
    here was the cause of the Issue 88 bug — the data-gate showed videos and
    the build said insufficient. (Issue 88)
    """
    result = await session.execute(
        select(Video, VideoMetrics)
        .join(VideoMetrics, VideoMetrics.video_id == Video.id)
        .where(
            Video.creator_id == creator_id,
            VideoMetrics.engagement_rate.is_not(None),
        )
        # Bound the fetch to the most-recent N so a huge catalog can't exhaust worker
        # memory; recency-weighted ranking already favors recent content. (Issue B)
        .order_by(Video.published_at.desc().nullslast())
        .limit(settings.DNA_MAX_CANDIDATE_VIDEOS)
    )
    scored: list[dict] = []
    for video, metrics in result.all():
        weight = _recency_weight(video.published_at)
        scored.append(
            {
                "video_id": video.id,
                "youtube_video_id": video.youtube_video_id,
                "title": video.title,
                "kind": video.kind.value,
                "published_at": video.published_at,
                "duration_s": video.duration_s,
                "views": metrics.views,
                "engagement_rate": metrics.engagement_rate or 0.0,
                "avg_view_duration_s": metrics.avg_view_duration_s,
                "recency_weight": weight,
                "weighted_score": (metrics.engagement_rate or 0.0) * weight,
            }
        )
    return sorted(scored, key=lambda v: v["weighted_score"], reverse=True)


async def _enrich_videos(session: AsyncSession, videos: list[dict]) -> None:
    """Attach hook text, signal counts, and retention data to each video dict in-place.

    Batched into 3 IN-queries total, regardless of video count — previously 3 round
    trips per video, an N+1 of up to ~60 queries per build. (Issue B)
    """
    if not videos:
        return
    ids = [v["video_id"] for v in videos]

    transcripts = {
        t.video_id: t
        for t in (
            await session.execute(select(Transcript).where(Transcript.video_id.in_(ids)))
        ).scalars()
    }
    signals_map = {
        s.video_id: s
        for s in (await session.execute(select(Signals).where(Signals.video_id.in_(ids)))).scalars()
    }
    retention: dict[uuid.UUID, list] = {}
    ret_rows = (
        await session.execute(
            select(RetentionCurve)
            .where(RetentionCurve.video_id.in_(ids))
            .order_by(RetentionCurve.video_id, RetentionCurve.timestamp_s)
        )
    ).scalars()
    for r in ret_rows:
        retention.setdefault(r.video_id, []).append(r)

    for v in videos:
        vid_id = v["video_id"]
        transcript = transcripts.get(vid_id)
        v["hook_text"] = _hook_text(transcript.segments_jsonb) if transcript else ""

        signals = signals_map.get(vid_id)
        if signals:
            timeline = signals.timeline_jsonb
            v["energy_spike_count"] = len(timeline.get("energy_spikes", []))
            v["laughter_count"] = len(timeline.get("laughter", []))
        else:
            v["energy_spike_count"] = 0
            v["laughter_count"] = 0

        rows = retention.get(vid_id, [])
        v["retention_spike_times"] = [r.timestamp_s for r in rows if r.is_rewatch_spike]
        v["best_source_region"] = _best_source_region(rows, v.get("duration_s"))


def _video_summary(v: dict) -> dict:
    return {
        "youtube_video_id": v["youtube_video_id"],
        "title": v["title"],
        "kind": v["kind"],
        "published_at": v["published_at"].isoformat() if v.get("published_at") else None,
        "duration_s": v.get("duration_s"),
        "views": v.get("views"),
        "engagement_rate": v.get("engagement_rate"),
        "avg_view_duration_s": v.get("avg_view_duration_s"),
        "recency_weight": round(v["recency_weight"], 4),
        "weighted_score": round(v["weighted_score"], 6),
        "hook_text": v.get("hook_text", ""),
        "retention_spike_times": v.get("retention_spike_times", []),
        "energy_spike_count": v.get("energy_spike_count", 0),
        "laughter_count": v.get("laughter_count", 0),
        "best_source_region": v.get("best_source_region"),
    }


# ── Main entry point ──────────────────────────────────────────────────────────


async def build_patterns(
    session: AsyncSession,
    creator_id: uuid.UUID,
) -> tuple[dict, list[uuid.UUID], list[uuid.UUID], float | None, str | None, float | None]:
    """
    Compute the full DNA patterns for a creator.

    Returns:
        (patterns_dict, top_video_ids, bottom_video_ids,
         optimal_clip_len_s, best_source_region, optimal_upload_gap_h)

    Raises ValueError if below minimum data thresholds.
    """
    ranked = await rank_videos(session, creator_id)

    # Compare against the enum value, not a bare literal, so a VideoKind rename is a
    # loud import error rather than two silently-empty buckets. (Issue B)
    longs = [v for v in ranked if v["kind"] == VideoKind.long.value]
    shorts = [v for v in ranked if v["kind"] == VideoKind.short.value]

    if len(longs) < settings.MIN_VIDEOS_FOR_DNA and len(shorts) < settings.MIN_SHORTS_FOR_DNA:
        # Issue 88 diagnostic: when the build fails the readiness check, log the
        # bucket breakdown so the next "data-gate said 23 but build says 0/0"
        # report is one log line away from the answer (not a code-bisect).
        from observability import log_event

        total_videos = (
            await session.execute(
                select(func.count(Video.id)).where(Video.creator_id == creator_id)
            )
        ).scalar_one()
        metered_videos = (
            await session.execute(
                select(func.count(Video.id))
                .join(VideoMetrics, VideoMetrics.video_id == Video.id)
                .where(
                    Video.creator_id == creator_id,
                    VideoMetrics.engagement_rate.is_not(None),
                )
            )
        ).scalar_one()
        log_event(
            "dna_build_insufficient_data",
            creator_id=str(creator_id),
            total_videos_in_db=total_videos,
            metered_videos=metered_videos,
            ranked_longs=len(longs),
            ranked_shorts=len(shorts),
            min_longs=settings.MIN_VIDEOS_FOR_DNA,
            min_shorts=settings.MIN_SHORTS_FOR_DNA,
        )
        raise ValueError(
            f"Insufficient data for DNA build: {len(longs)} long videos "
            f"(min {settings.MIN_VIDEOS_FOR_DNA}), {len(shorts)} shorts "
            f"(min {settings.MIN_SHORTS_FOR_DNA})."
        )

    def _split(videos: list[dict]) -> tuple[list[dict], list[dict]]:
        if not videos:
            return [], []
        mid = max(1, len(videos) // 2)
        return videos[:mid], videos[mid:]

    top_long, bottom_long = _split(longs)
    top_short, bottom_short = _split(shorts)

    top_all = top_long + top_short
    bottom_all = bottom_long + bottom_short

    await _enrich_videos(session, top_all + bottom_all)

    def _avg(vals: list) -> float | None:
        valid = [x for x in vals if x is not None]
        return sum(valid) / len(valid) if valid else None

    activity_result = await session.execute(
        select(AudienceActivity).where(AudienceActivity.creator_id == creator_id)
    )
    activity_rows = list(activity_result.scalars())
    upload_gap_h = _optimal_upload_gap_h(activity_rows)
    clip_len_s = _optimal_clip_len_s(top_all)

    all_regions = [v.get("best_source_region") for v in top_all if v.get("best_source_region")]
    best_region: str | None = Counter(all_regions).most_common(1)[0][0] if all_regions else None

    patterns = {
        "analysis_timestamp": datetime.now(UTC).isoformat(),
        "videos_analyzed": len(ranked),
        "long_videos_analyzed": len(longs),
        "shorts_analyzed": len(shorts),
        "top_videos": [_video_summary(v) for v in top_all[:10]],
        "bottom_videos": [_video_summary(v) for v in bottom_all[:10]],
        "aggregate": {
            "top_avg_engagement_rate": _avg([v["engagement_rate"] for v in top_all]),
            "bottom_avg_engagement_rate": _avg([v["engagement_rate"] for v in bottom_all]),
            "top_avg_views": _avg([v["views"] for v in top_all if v.get("views")]),
            "bottom_avg_views": _avg([v["views"] for v in bottom_all if v.get("views")]),
            "top_count": len(top_all),
            "bottom_count": len(bottom_all),
        },
        "optimal_clip_len_s": clip_len_s,
        "best_source_region": best_region,
        "optimal_upload_gap_h": upload_gap_h,
    }

    top_ids = [v["video_id"] for v in top_all]
    bottom_ids = [v["video_id"] for v in bottom_all]

    return patterns, top_ids, bottom_ids, clip_len_s, best_region, upload_gap_h
