"""Seed a realistic creator for the load test (tests/perf/run.sh).

Idempotent: keyed on a FIXED creator UUID so the locustfile can mint a token for
it without any handoff. Re-running wipes and recreates that creator's rows only.

Empty tables hide N+1 queries and serialization cost, so this fills the hot read
paths the load test exercises: videos (+ metrics + retention), clips, a confirmed
DNA profile (+ pgvector embeddings), audience activity (upload-intel), and a
minute balance (billing). Run inside the app image, pointed at Postgres directly.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import db
from models import (
    AudienceActivity,
    Clip,
    ClipFormat,
    Creator,
    CreatorDna,
    DnaEmbedding,
    DnaEmbeddingKind,
    DnaStatus,
    IngestStatus,
    MinutePack,
    OnboardingState,
    RenderStatus,
    RetentionCurve,
    Video,
    VideoKind,
    VideoMetrics,
)

# Fixed so the locustfile needs no creator-id handoff. Keep in sync with run.sh.
PERF_CREATOR_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ff")
_GOOGLE_SUB = "perf-seed-creator"

N_LONG = 12  # > MIN_VIDEOS_FOR_DNA (10)
N_SHORT = 6  # > MIN_SHORTS_FOR_DNA (5)
CLIPS_PER_VIDEO = 6


async def _wipe(session: AsyncSession) -> None:
    """Delete the perf creator (cascades to its children) for a clean re-seed."""
    await session.execute(sa.delete(Creator).where(Creator.id == PERF_CREATOR_ID))
    await session.commit()


async def _seed() -> None:
    now = datetime.now(UTC)
    async with db.AsyncSessionLocal() as session:
        await _wipe(session)

        session.add(
            Creator(
                id=PERF_CREATOR_ID,
                google_sub=_GOOGLE_SUB,
                channel_id="UCperfseed00000000000000",
                channel_title="Perf Seed Channel",
                email="perf@example.com",
                onboarding_state=OnboardingState.active,
                minutes_balance=100_000,  # never the bottleneck for read load
                created_at=now,
                last_analytics_refreshed_at=now,
            )
        )
        await session.flush()

        for i in range(N_LONG + N_SHORT):
            is_short = i >= N_LONG
            video_id = uuid.uuid4()
            duration = 60.0 if is_short else 600.0
            session.add(
                Video(
                    id=video_id,
                    creator_id=PERF_CREATOR_ID,
                    youtube_video_id=f"vid{i:08d}"[:11],
                    title=f"Perf video {i}",
                    kind=VideoKind.short if is_short else VideoKind.long,
                    published_at=now - timedelta(days=i),
                    duration_s=duration,
                    captions_available=True,
                    ingest_status=IngestStatus.done,
                    created_at=now - timedelta(days=i),
                    ingest_done_at=now - timedelta(days=i),
                )
            )
            session.add(
                VideoMetrics(
                    video_id=video_id,
                    views=10_000 + i * 137,
                    watch_time_s=500_000 + i * 1000,
                    avg_view_duration_s=duration * 0.45,
                    engagement_rate=0.06 + (i % 5) * 0.01,
                    fetched_at=now,
                )
            )
            for t in range(0, 10):
                session.add(
                    RetentionCurve(
                        video_id=video_id,
                        timestamp_s=duration * t / 10.0,
                        audience_watch_ratio=max(0.2, 1.0 - t * 0.07),
                        relative_retention_performance=1.0 - t * 0.03,
                        is_rewatch_spike=(t == 3),
                    )
                )
            for c in range(CLIPS_PER_VIDEO):
                peak = duration * (0.2 + 0.1 * c)
                session.add(
                    Clip(
                        video_id=video_id,
                        creator_id=PERF_CREATOR_ID,
                        setup_start_s=max(0.0, peak - 30.0),
                        start_s=max(0.0, peak - 30.0),
                        end_s=peak + 15.0,
                        peak_s=peak,
                        score=0.9 - c * 0.05,
                        dna_match=0.8 - c * 0.04,
                        signals_jsonb={"laughter": 0.7, "loudness_db": -12.0},
                        format=ClipFormat.short,
                        render_status=RenderStatus.done if c == 0 else RenderStatus.pending,
                        rank=c + 1,
                        created_at=now,
                    )
                )

        # Confirmed DNA profile + pgvector embeddings (similarity read path).
        session.add(
            CreatorDna(
                creator_id=PERF_CREATOR_ID,
                version=1,
                brief_text="High-energy explainer style; strong cold opens; ~35s optimal clip.",
                patterns_jsonb={"hook": "question", "pacing": "fast"},
                top_video_ids_jsonb=[],
                bottom_video_ids_jsonb=[],
                optimal_clip_len_s=35.0,
                best_source_region="opening",
                optimal_upload_gap_h=48.0,
                status=DnaStatus.confirmed,
                created_at=now,
            )
        )
        for kind in (DnaEmbeddingKind.pattern, DnaEmbeddingKind.hook, DnaEmbeddingKind.clip):
            session.add(
                DnaEmbedding(
                    creator_id=PERF_CREATOR_ID,
                    kind=kind,
                    embedding=[0.01 * (j % 100) for j in range(1024)],
                    ref_jsonb={"note": kind.value},
                )
            )

        # Audience activity grid (upload-intel read path).
        for dow in range(7):
            for hour in range(0, 24, 3):
                session.add(
                    AudienceActivity(
                        creator_id=PERF_CREATOR_ID,
                        day_of_week=dow,
                        hour=hour,
                        activity_index=0.3 + ((dow + hour) % 7) * 0.1,
                        fetched_at=now,
                    )
                )

        session.add(
            MinutePack(
                creator_id=PERF_CREATOR_ID,
                pack_id="perf",
                minutes_granted=100_000,
                price_cents=0,
                reason="trial",
                granted_at=now,
            )
        )

        await session.commit()

    await db.engine.dispose()
    print(f"SEEDED creator {PERF_CREATOR_ID}")


if __name__ == "__main__":
    asyncio.run(_seed())
