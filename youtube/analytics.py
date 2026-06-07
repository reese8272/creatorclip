"""
YouTube Analytics API v2 helpers.

_fetch_report() is the single mockable HTTP boundary. All DB sync operations
run inside the provided AsyncSession; the caller is responsible for committing.
"""

import asyncio
import contextlib
import logging
import random
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import (
    AudienceActivity,
    Creator,
    Demographics,
    IngestStatus,
    RetentionCurve,
    Video,
    VideoKind,
    VideoMetrics,
)
from youtube import _http
from youtube.data_api import _classify_error, get_videos_metadata, list_channel_videos
from youtube.errors import YouTubeAuthError, retry_after_seconds
from youtube.quota import COST_ANALYTICS_REPORT, consume

logger = logging.getLogger(__name__)

_ANALYTICS_V2 = "https://youtubeanalytics.googleapis.com/v2/reports"
_MAX_RETRIES = 4


async def _fetch_report(access_token: str, params: dict) -> dict:
    await consume(COST_ANALYTICS_REPORT)
    headers = {"Authorization": f"Bearer {access_token}"}
    delay = 1.0

    for attempt in range(_MAX_RETRIES):
        # Shared timeout-bounded client, reused across calls/retries (Issue 72).
        # Issue 88: Analytics endpoint occasionally exceeds the 60s read
        # timeout under load. httpx raises ReadTimeout / ConnectError, which
        # the original retry loop didn't catch (it only handled HTTP error
        # codes). Catch httpx.RequestError as transient → backoff + retry,
        # so a single slow report doesn't abort the whole catalog sync.
        try:
            resp = await _http.client().get(_ANALYTICS_V2, headers=headers, params=params)
        except httpx.RequestError as exc:
            if attempt < _MAX_RETRIES - 1:
                jitter = random.uniform(0, delay * 0.3)
                await asyncio.sleep(delay + jitter)
                delay *= 2
                continue
            logger.warning(
                "YouTube Analytics API request error after %d retries: %r",
                _MAX_RETRIES,
                exc,
            )
            raise

        if resp.status_code < 400:
            return resp.json()

        if resp.status_code in (401, 403, 429):
            reason, is_transient = _classify_error(resp)
            if not is_transient:
                raise YouTubeAuthError(reason, resp.status_code)
            if attempt < _MAX_RETRIES - 1:
                jitter = random.uniform(0, delay * 0.3)
                base = delay + jitter
                # Honor a server-stated Retry-After (Google sends it on 429). (Issue A)
                retry_after = retry_after_seconds(resp)
                sleep_s = max(retry_after, base) if retry_after is not None else base
                await asyncio.sleep(sleep_s)
                delay *= 2
                continue
            logger.warning(
                "YouTube Analytics API returned %s (reason=%s) after %d retries",
                resp.status_code,
                reason,
                _MAX_RETRIES,
            )
        elif resp.status_code >= 500 and attempt < _MAX_RETRIES - 1:
            # 5xx is transient for this idempotent GET — back off and retry (axis E).
            jitter = random.uniform(0, delay * 0.3)
            await asyncio.sleep(delay + jitter)
            delay *= 2
            continue

        resp.raise_for_status()
        return resp.json()

    resp.raise_for_status()
    return {}  # unreachable


def _parse_report(response: dict) -> list[dict]:
    headers = [col["name"] for col in response.get("columnHeaders", [])]
    return [dict(zip(headers, row, strict=False)) for row in response.get("rows", []) or []]


async def fetch_video_metrics(access_token: str, video_id: str, channel_id: str) -> dict | None:
    params = {
        "ids": f"channel=={channel_id}",
        "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
        "dimensions": "video",
        "filters": f"video=={video_id}",
        "startDate": "2000-01-01",
        "endDate": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    data = await _fetch_report(access_token, params)
    rows = _parse_report(data)
    if not rows:
        return None
    row = rows[0]
    return {
        "views": row.get("views"),
        "watch_time_s": int((row.get("estimatedMinutesWatched") or 0) * 60),
        "avg_view_duration_s": row.get("averageViewDuration"),
        "engagement_rate": (row.get("averageViewPercentage") or 0) / 100.0,
    }


async def fetch_retention_curve(
    access_token: str, video_id: str, channel_id: str, duration_s: float
) -> list[dict]:
    params = {
        "ids": f"channel=={channel_id}",
        "metrics": "audienceWatchRatio,relativeRetentionPerformance",
        "dimensions": "elapsedVideoTimeRatio",
        "filters": f"video=={video_id}",
        "startDate": "2000-01-01",
        "endDate": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    data = await _fetch_report(access_token, params)
    return [
        {
            "timestamp_s": (row.get("elapsedVideoTimeRatio") or 0.0) * duration_s,
            "audience_watch_ratio": row.get("audienceWatchRatio") or 0.0,
            "relative_retention_performance": row.get("relativeRetentionPerformance"),
        }
        for row in _parse_report(data)
    ]


# YouTube's public Analytics API doesn't expose hour-of-day breakdowns —
# only day-of-week views. Audience-activity rows therefore carry a fixed
# sentinel hour (12, midday) so the schema stays consistent for downstream
# `upload_intel` consumers; replace with real hourly data when YT exposes
# it or when we layer in our own hour-level views via the Data API. Docs:
# https://developers.google.com/youtube/analytics/dimsmets/dims (Issue 108)
_HOUR_UNAVAILABLE_SENTINEL = 12


async def fetch_audience_activity(access_token: str, channel_id: str) -> list[dict]:
    """Aggregate per-day-of-week activity index. Hour-level data not in public API."""
    params = {
        "ids": f"channel=={channel_id}",
        "metrics": "views",
        "dimensions": "day",
        "startDate": "2000-01-01",
        "endDate": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    data = await _fetch_report(access_token, params)

    day_totals: dict[int, float] = {}
    for row in _parse_report(data):
        date_str = row.get("day", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            dow = (dt.weekday() + 1) % 7  # Python Mon=0 → Sunday-anchored: Sun=0
        except ValueError:
            continue
        day_totals[dow] = day_totals.get(dow, 0.0) + float(row.get("views") or 0)

    if not day_totals:
        return []

    max_views = max(day_totals.values()) or 1.0
    return [
        {
            "day_of_week": dow,
            "hour": _HOUR_UNAVAILABLE_SENTINEL,
            "activity_index": views / max_views,
        }
        for dow, views in day_totals.items()
    ]


async def fetch_demographics(access_token: str, channel_id: str) -> dict:
    params = {
        "ids": f"channel=={channel_id}",
        "metrics": "viewerPercentage",
        "dimensions": "ageGroup,gender",
        "startDate": "2000-01-01",
        "endDate": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    data = await _fetch_report(access_token, params)
    return {"rows": _parse_report(data)}


# ── DB sync helpers ───────────────────────────────────────────────────────────


async def sync_video_catalog(session: AsyncSession, creator: Creator, access_token: str) -> None:
    """Upsert Video rows from the uploads playlist. Skips existing rows."""
    playlist_items = await list_channel_videos(access_token)
    if not playlist_items:
        return

    video_id_map = {item["video_id"]: item for item in playlist_items}
    all_ids = list(video_id_map.keys())

    duration_map: dict[str, dict] = {}
    for i in range(0, len(all_ids), 50):
        for m in await get_videos_metadata(access_token, all_ids[i : i + 50]):
            duration_map[m["video_id"]] = m

    existing_result = await session.execute(
        select(Video.youtube_video_id).where(Video.creator_id == creator.id)
    )
    existing_ids = {row[0] for row in existing_result}

    for video_id, item in video_id_map.items():
        if video_id in existing_ids:
            continue
        meta = duration_map.get(video_id, {})
        published_at: datetime | None = None
        if item.get("published_at"):
            with contextlib.suppress(ValueError):
                published_at = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        session.add(
            Video(
                creator_id=creator.id,
                youtube_video_id=video_id,
                title=item.get("title"),
                kind=meta.get("kind", VideoKind.long),
                published_at=published_at,
                duration_s=meta.get("duration_s"),
                ingest_status=IngestStatus.pending,
            )
        )


async def sync_video_analytics(
    session: AsyncSession,
    video: Video,
    creator: Creator,
    access_token: str,
) -> None:
    """Fetch and upsert VideoMetrics and RetentionCurve for one video."""
    if not creator.channel_id:
        logger.warning("Creator %s has no channel_id; skipping analytics", creator.id)
        return

    metrics_data = await fetch_video_metrics(
        access_token, video.youtube_video_id, creator.channel_id
    )
    now = datetime.now(UTC)
    if metrics_data:
        existing = await session.get(VideoMetrics, video.id)
        if existing:
            existing.views = metrics_data["views"]
            existing.watch_time_s = metrics_data["watch_time_s"]
            existing.avg_view_duration_s = metrics_data["avg_view_duration_s"]
            existing.engagement_rate = metrics_data["engagement_rate"]
            existing.fetched_at = now
        else:
            session.add(VideoMetrics(video_id=video.id, fetched_at=now, **metrics_data))

    duration_s = video.duration_s or 0.0
    if duration_s > 0:
        retention = await fetch_retention_curve(
            access_token, video.youtube_video_id, creator.channel_id, duration_s
        )
        await session.execute(delete(RetentionCurve).where(RetentionCurve.video_id == video.id))
        for point in retention:
            session.add(RetentionCurve(video_id=video.id, **point))


async def sync_audience_data(session: AsyncSession, creator: Creator, access_token: str) -> None:
    """Fetch and upsert AudienceActivity and Demographics for the channel."""
    if not creator.channel_id:
        return

    now = datetime.now(UTC)
    for row in await fetch_audience_activity(access_token, creator.channel_id):
        existing = await session.get(
            AudienceActivity, (creator.id, row["day_of_week"], row["hour"])
        )
        if existing:
            existing.activity_index = row["activity_index"]
            existing.fetched_at = now
        else:
            session.add(
                AudienceActivity(
                    creator_id=creator.id,
                    day_of_week=row["day_of_week"],
                    hour=row["hour"],
                    activity_index=row["activity_index"],
                    fetched_at=now,
                )
            )

    demo_data = await fetch_demographics(access_token, creator.channel_id)
    existing_demo = await session.get(Demographics, creator.id)
    if existing_demo:
        existing_demo.payload_jsonb = demo_data
        existing_demo.fetched_at = now
    else:
        session.add(Demographics(creator_id=creator.id, payload_jsonb=demo_data, fetched_at=now))


async def check_data_gate(session: AsyncSession, creator_id: uuid.UUID) -> dict:
    """Return per-kind counts of videos READY for DNA analysis.

    "Ready" = the video has a VideoMetrics row with a non-null engagement_rate.
    This is the same predicate `dna.builder.rank_videos` uses — display and
    business logic share the source of truth so the gate cannot disagree with
    the build. (Issue 88: pre-fix, this counted every Video row regardless of
    metrics presence, so the gate said "ready" while the build said "0/0".)
    """
    long_result = await session.execute(
        select(func.count(Video.id))
        .join(VideoMetrics, VideoMetrics.video_id == Video.id)
        .where(
            Video.creator_id == creator_id,
            Video.kind == VideoKind.long,
            VideoMetrics.engagement_rate.is_not(None),
        )
    )
    long_count: int = long_result.scalar_one()

    short_result = await session.execute(
        select(func.count(Video.id))
        .join(VideoMetrics, VideoMetrics.video_id == Video.id)
        .where(
            Video.creator_id == creator_id,
            Video.kind == VideoKind.short,
            VideoMetrics.engagement_rate.is_not(None),
        )
    )
    short_count: int = short_result.scalar_one()

    # `ready` matches `dna.builder.build_patterns`: the build raises only when
    # BOTH buckets fall below their min — i.e. EITHER bucket above its min is
    # enough to build. Pre-Issue-88, this used AND, so a creator with 10 longs
    # + 0 shorts (or vice-versa) saw "not ready" in onboarding while the build
    # would have succeeded. Same shape as the original Issue 88 bug —
    # surfaced by the targeted display-vs-filter assessment. (Issue 88)
    return {
        "long_form_videos": long_count,
        "shorts": short_count,
        "long_form_ready": long_count >= settings.MIN_VIDEOS_FOR_DNA,
        "shorts_ready": short_count >= settings.MIN_SHORTS_FOR_DNA,
        "ready": (
            long_count >= settings.MIN_VIDEOS_FOR_DNA or short_count >= settings.MIN_SHORTS_FOR_DNA
        ),
    }
