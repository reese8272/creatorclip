"""Creator-scoped tools for the Pro chatbot (Issue 152 + Issue 324).

Every executor takes the authenticated ``creator_id`` and an ``AsyncSession``
and filters EVERY query by that id. The model never supplies the creator id —
it is injected by the worker from the session owner — so a creator can only ever
read their own channel. This is the load-bearing isolation guarantee, pinned by
tests/test_chat_isolation_integration.py.

Tool descriptions are prescriptive about WHEN to call (Sonnet/Opus reach for
tools conservatively — see /claude-api shared/tool-use-concepts.md). The schema
list is a module-level constant so it renders byte-identically on every request,
keeping the prompt-cache prefix intact.

Issue 324 adds three clip/outcome tools:
  - ``list_top_clips``: read the creator's top-performing clips by score.
  - ``get_clip_detail``: fetch one clip's score, principle, timing, and outcome.
  - ``suggest_clip_titles``: call the Issue-322 generator (DRY reuse).

No ``creator_id`` parameter appears in any tool schema — the worker injects it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AudienceActivity, Clip, ClipOutcome, RetentionCurve, Video, VideoMetrics
from upload_intel.timing import best_upload_windows, optimal_gap_hours

logger = logging.getLogger(__name__)

# Bounds the model can't exceed regardless of what it asks for.
_MAX_VIDEOS = 25
_DEFAULT_VIDEOS = 10
_MAX_CLIPS = 20
_DEFAULT_CLIPS = 10

# Stable, deterministically-ordered tool schemas (keep order frozen — see module
# docstring). No ``creator_id`` parameter: the worker injects it.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_channel_dna",
        "description": (
            "Get the creator's CreatorClip DNA profile — the plain-language brief of "
            "their channel's style, audience, and patterns. Call this when the question "
            "is about their overall style, what their channel is known for, who their "
            "audience is, or how to stay on-brand."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_recent_videos",
        "description": (
            "List the creator's most recent videos with their key metrics (views, "
            "engagement, average view duration). Call this when the question is about "
            "recent uploads, what's been performing well or poorly lately, or to pick a "
            "video to dig into."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": f"How many recent videos to return (1–{_MAX_VIDEOS}).",
                    "minimum": 1,
                    "maximum": _MAX_VIDEOS,
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_video_performance",
        "description": (
            "Get detailed metrics and a retention summary for ONE specific video in the "
            "creator's catalog, matched by a title fragment or YouTube video id. Call "
            "this when the creator asks why a particular video did well or poorly, or "
            "about its hook/retention."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_query": {
                    "type": "string",
                    "description": "A fragment of the video title, or its 11-char YouTube id.",
                }
            },
            "required": ["video_query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_channel_averages",
        "description": (
            "Get the creator's channel-wide average metrics (avg views, engagement rate, "
            "view duration) across their recent catalog. Call this when you need a "
            "benchmark to compare a single video against, or for an overall health read."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_upload_timing",
        "description": (
            "Get the creator's best upload windows and optimal gap between uploads, "
            "computed from their own audience-activity data. Call this when the question "
            "is about when to post or how often."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # ── Issue 324: clip/outcome tools ─────────────────────────────────────────
    {
        "name": "list_top_clips",
        "description": (
            "List the creator's top-performing clips ranked by fit score, including "
            "each clip's score, timing, named principle, and published outcome if "
            "available. Call this when the creator asks which of their clips performed "
            "best, how their clips rank, or for an overview of their clip catalog."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": f"How many clips to return (1–{_MAX_CLIPS}).",
                    "minimum": 1,
                    "maximum": _MAX_CLIPS,
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_clip_detail",
        "description": (
            "Get the full detail for ONE specific clip: its score, timing, the named "
            "clipping principle the engine cited, and its published outcome (views, "
            "retention) if available. Call this when the creator asks about a specific "
            "clip — why it was selected, how it performed, or what principle it cites."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clip_id": {
                    "type": "string",
                    "description": "The UUID of the clip (from list_top_clips results).",
                }
            },
            "required": ["clip_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "suggest_clip_titles",
        "description": (
            "Generate 5 ranked Short-title candidates + 1–2 hook-rewrite options for a "
            "specific clip, grounded in the creator's DNA and the clip's own transcript. "
            "Call this ONLY when the creator explicitly asks for title suggestions or hook "
            "rewrites for a clip — this triggers a separate AI call and should be used "
            "sparingly, not as a default action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clip_id": {
                    "type": "string",
                    "description": "The UUID of the clip to generate titles for.",
                }
            },
            "required": ["clip_id"],
            "additionalProperties": False,
        },
    },
]

_TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


async def _get_channel_dna(creator_id: uuid.UUID, session: AsyncSession, _inp: dict) -> dict:
    from dna.profile import get_active

    dna = await get_active(session, creator_id)
    if dna is None or not dna.brief_text:
        return {"available": False, "message": "No confirmed DNA profile yet."}
    return {
        "available": True,
        "version": dna.version,
        "brief": dna.brief_text,
        "patterns": dna.patterns_jsonb,
        "optimal_clip_length_s": dna.optimal_clip_len_s,
    }


async def _get_recent_videos(creator_id: uuid.UUID, session: AsyncSession, inp: dict) -> dict:
    raw_limit = inp.get("limit", _DEFAULT_VIDEOS)
    try:
        limit = max(1, min(_MAX_VIDEOS, int(raw_limit)))
    except (TypeError, ValueError):
        limit = _DEFAULT_VIDEOS

    rows = list(
        (
            await session.execute(
                select(Video, VideoMetrics)
                .outerjoin(VideoMetrics, VideoMetrics.video_id == Video.id)
                .where(Video.creator_id == creator_id)
                .order_by(Video.published_at.desc().nulls_last(), Video.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    videos = []
    for video, metrics in rows:
        videos.append(
            {
                "title": video.title,
                "youtube_video_id": video.youtube_video_id,
                "published_at": video.published_at.isoformat() if video.published_at else None,
                "duration_s": video.duration_s,
                "views": metrics.views if metrics else None,
                "engagement_rate": metrics.engagement_rate if metrics else None,
                "avg_view_duration_s": metrics.avg_view_duration_s if metrics else None,
            }
        )
    return {"count": len(videos), "videos": videos}


async def _get_video_performance(creator_id: uuid.UUID, session: AsyncSession, inp: dict) -> dict:
    query = (inp.get("video_query") or "").strip()
    if not query:
        return {"found": False, "message": "No video_query provided."}

    # Match on youtube id first (exact), then a case-insensitive title fragment.
    # Both branches are creator-scoped.
    video = await session.scalar(
        select(Video).where(Video.creator_id == creator_id, Video.youtube_video_id == query)
    )
    if video is None:
        video = await session.scalar(
            select(Video)
            .where(Video.creator_id == creator_id, Video.title.ilike(f"%{query}%"))
            .order_by(Video.published_at.desc().nulls_last())
            .limit(1)
        )
    if video is None:
        return {"found": False, "message": f"No video in your catalog matches '{query}'."}

    metrics = await session.scalar(
        select(VideoMetrics)
        .where(VideoMetrics.video_id == video.id)
        .order_by(VideoMetrics.fetched_at.desc())
        .limit(1)
    )

    retention: dict[str, float | None] | None = None
    if video.duration_s:
        curves = list(
            (
                await session.execute(
                    select(RetentionCurve)
                    .where(RetentionCurve.video_id == video.id)
                    .order_by(RetentionCurve.timestamp_s)
                )
            ).scalars()
        )
        if curves:
            checkpoints: dict[str, float | None] = {
                "at_25pct": None,
                "at_50pct": None,
                "at_75pct": None,
                "at_end": None,
            }
            for c in curves:
                pct = c.timestamp_s / video.duration_s
                ratio = round(c.audience_watch_ratio or 0, 3)
                if checkpoints["at_25pct"] is None and pct >= 0.25:
                    checkpoints["at_25pct"] = ratio
                if checkpoints["at_50pct"] is None and pct >= 0.50:
                    checkpoints["at_50pct"] = ratio
                if checkpoints["at_75pct"] is None and pct >= 0.75:
                    checkpoints["at_75pct"] = ratio
                checkpoints["at_end"] = ratio
            retention = checkpoints

    return {
        "found": True,
        "title": video.title,
        "youtube_video_id": video.youtube_video_id,
        "duration_s": video.duration_s,
        "metrics": (
            {
                "views": metrics.views,
                "watch_time_s": metrics.watch_time_s,
                "avg_view_duration_s": metrics.avg_view_duration_s,
                "engagement_rate": metrics.engagement_rate,
            }
            if metrics
            else None
        ),
        "retention_checkpoints": retention,
    }


async def _get_channel_averages(creator_id: uuid.UUID, session: AsyncSession, _inp: dict) -> dict:
    metrics = list(
        (
            await session.execute(
                select(VideoMetrics)
                .join(Video, VideoMetrics.video_id == Video.id)
                .where(Video.creator_id == creator_id)
                .order_by(VideoMetrics.fetched_at.desc())
                .limit(50)
            )
        ).scalars()
    )
    if not metrics:
        return {"available": False, "message": "No metrics yet — link some videos first."}
    return {
        "available": True,
        "sample_size": len(metrics),
        "avg_views": _avg([m.views for m in metrics if m.views]),
        "avg_engagement_rate": _avg([m.engagement_rate for m in metrics if m.engagement_rate]),
        "avg_view_duration_s": _avg(
            [m.avg_view_duration_s for m in metrics if m.avg_view_duration_s]
        ),
    }


async def _get_upload_timing(creator_id: uuid.UUID, session: AsyncSession, _inp: dict) -> dict:
    rows = list(
        (
            await session.execute(
                select(AudienceActivity).where(AudienceActivity.creator_id == creator_id)
            )
        ).scalars()
    )
    windows = best_upload_windows(rows, top_n=3)
    if not windows:
        return {"available": False, "message": "Not enough audience-activity data yet."}
    return {
        "available": True,
        "best_windows": windows,
        "optimal_gap_hours": optimal_gap_hours(rows),
    }


async def _list_top_clips(creator_id: uuid.UUID, session: AsyncSession, inp: dict) -> dict:
    """Return the creator's top-scoring clips, with outcome data if available.

    Filters by creator_id on every query — the model cannot supply or override
    this value (Issue 324 isolation guarantee).
    """
    raw_limit = inp.get("limit", _DEFAULT_CLIPS)
    try:
        limit = max(1, min(_MAX_CLIPS, int(raw_limit)))
    except (TypeError, ValueError):
        limit = _DEFAULT_CLIPS

    rows = list(
        (
            await session.execute(
                select(Clip, ClipOutcome)
                .outerjoin(ClipOutcome, ClipOutcome.clip_id == Clip.id)
                .where(Clip.creator_id == creator_id)
                .order_by(Clip.score.desc().nulls_last())
                .limit(limit)
            )
        ).all()
    )

    clips_out = []
    for clip, outcome in rows:
        sj = clip.signals_jsonb or {}
        clips_out.append(
            {
                "clip_id": str(clip.id),
                "video_id": str(clip.video_id),
                "score": clip.score,
                "rank": clip.rank,
                "principle": sj.get("principle", ""),
                "reasoning": sj.get("reasoning", ""),
                "start_s": clip.start_s,
                "end_s": clip.end_s,
                "render_status": clip.render_status.value,
                "outcome": (
                    {
                        "views": outcome.views,
                        "retention": outcome.retention,
                        "performed_well": outcome.performed_well,
                    }
                    if outcome
                    else None
                ),
            }
        )
    return {"count": len(clips_out), "clips": clips_out}


async def _get_clip_detail(creator_id: uuid.UUID, session: AsyncSession, inp: dict) -> dict:
    """Return full detail for one specific clip.

    Filters by creator_id to enforce per-creator isolation.
    """
    raw_id = (inp.get("clip_id") or "").strip()
    if not raw_id:
        return {"found": False, "message": "No clip_id provided."}

    try:
        clip_uuid = uuid.UUID(raw_id)
    except ValueError:
        return {"found": False, "message": f"Invalid clip_id format: {raw_id!r}"}

    clip = await session.scalar(
        select(Clip).where(Clip.id == clip_uuid, Clip.creator_id == creator_id)
    )
    if clip is None:
        return {"found": False, "message": "Clip not found in your catalog."}

    outcome = await session.scalar(select(ClipOutcome).where(ClipOutcome.clip_id == clip.id))
    sj = clip.signals_jsonb or {}

    return {
        "found": True,
        "clip_id": str(clip.id),
        "video_id": str(clip.video_id),
        "score": clip.score,
        "rank": clip.rank,
        "principle": sj.get("principle", ""),
        "reasoning": sj.get("reasoning", ""),
        "start_s": clip.start_s,
        "end_s": clip.end_s,
        "peak_s": clip.peak_s,
        "render_status": clip.render_status.value,
        "outcome": (
            {
                "views": outcome.views,
                "retention": outcome.retention,
                "performed_well": outcome.performed_well,
                "fetched_at": outcome.fetched_at.isoformat(),
            }
            if outcome
            else None
        ),
    }


async def _suggest_clip_titles(creator_id: uuid.UUID, session: AsyncSession, inp: dict) -> dict:
    """Generate Short-title candidates for a clip (reuses Issue-322 generator).

    Fetches the clip, its transcript, and the creator's DNA brief from DB (all
    filtered by creator_id), then calls the Issue-322 generator synchronously
    via asyncio.to_thread. Returns the title/hook suggestions as a dict.
    """
    from dna.profile import get_active as _get_active_dna
    from knowledge.clip_titles import generate_clip_title_suggestions
    from knowledge.util import extract_transcript_text
    from models import Creator, Transcript

    raw_id = (inp.get("clip_id") or "").strip()
    if not raw_id:
        return {"available": False, "message": "No clip_id provided."}

    try:
        clip_uuid = uuid.UUID(raw_id)
    except ValueError:
        return {"available": False, "message": f"Invalid clip_id: {raw_id!r}"}

    # Isolation: creator_id filter on clip lookup.
    clip = await session.scalar(
        select(Clip).where(Clip.id == clip_uuid, Clip.creator_id == creator_id)
    )
    if clip is None:
        return {"available": False, "message": "Clip not found in your catalog."}

    # Fetch transcript for the clip's parent video.
    transcript = await session.scalar(
        select(Transcript).where(Transcript.video_id == clip.video_id)
    )
    clip_transcript = extract_transcript_text(
        transcript.segments_jsonb if transcript else None, 1500
    )

    # Fetch creator for channel_title.
    creator_row = await session.get(Creator, creator_id)
    channel_title = (creator_row.channel_title if creator_row else None) or "Your Channel"

    # Fetch DNA brief.
    dna = await _get_active_dna(session, creator_id)
    dna_brief = dna.brief_text if dna else None

    try:
        result, _usage = await asyncio.to_thread(
            generate_clip_title_suggestions,
            channel_title,
            dna_brief,
            clip_transcript,
        )
    except Exception as exc:  # noqa: BLE001 — surface as tool error, not propagate
        logger.warning("suggest_clip_titles tool failed clip=%s err=%s", clip_uuid, exc)
        return {"available": False, "message": "Title generation failed; try again later."}

    return {"available": True, "clip_id": str(clip.id), **result}


_EXECUTORS = {
    "get_channel_dna": _get_channel_dna,
    "get_recent_videos": _get_recent_videos,
    "get_video_performance": _get_video_performance,
    "get_channel_averages": _get_channel_averages,
    "get_upload_timing": _get_upload_timing,
    # Issue 324: clip/outcome tools.
    "list_top_clips": _list_top_clips,
    "get_clip_detail": _get_clip_detail,
    "suggest_clip_titles": _suggest_clip_titles,
}


async def execute_tool(
    name: str, tool_input: dict, creator_id: uuid.UUID, session: AsyncSession
) -> tuple[str, bool]:
    """Run one creator-scoped tool and return ``(result_json, failed)``.

    ``failed`` is True when the tool could not execute successfully — an unknown
    tool name or an executor exception. The caller must set ``is_error: true`` on
    the tool_result block in that case so Claude receives the documented semantic
    signal to recover gracefully (Anthropic tool-use handle-tool-calls docs,
    fetched 2026-06-23). Never raises — errors are surfaced to the model, not
    propagated to the agentic loop. (Issue 222)
    """
    executor = _EXECUTORS.get(name)
    if executor is None:
        logger.warning("chat tool unknown name=%s creator=%s", name, creator_id)
        return json.dumps({"error": f"Unknown tool: {name}"}), True
    try:
        result = await executor(creator_id, session, tool_input or {})
    except Exception as exc:  # noqa: BLE001 — surface to the model, never crash the turn
        logger.warning("chat tool failed name=%s creator=%s err=%s", name, creator_id, exc)
        return json.dumps({"error": "Tool failed to fetch data; try a different question."}), True
    return json.dumps(result, default=str), False
