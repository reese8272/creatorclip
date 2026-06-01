"""
Integration tests for Issue B — DNA build performance.

Proves the enrichment N+1 is gone (one IN-query per table regardless of video
count) and that rank_videos caps the candidate set. Real Postgres.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from dna.builder import build_patterns, rank_videos
from models import (
    Creator,
    IngestStatus,
    OnboardingState,
    RetentionCurve,
    Signals,
    Transcript,
    Video,
    VideoKind,
    VideoMetrics,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession, n_long: int) -> Creator:
    creator = Creator(
        google_sub=f"t_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_{uuid.uuid4().hex[:6]}",
        channel_title="Builder Test",
        onboarding_state=OnboardingState.dna_pending,
    )
    session.add(creator)
    await session.flush()
    now = datetime.now(UTC)
    for i in range(n_long):
        v = Video(
            creator_id=creator.id,
            youtube_video_id=f"v{i:09d}a"[:11],
            title=f"Video {i}",
            kind=VideoKind.long,
            duration_s=600.0,
            ingest_status=IngestStatus.done,
            published_at=now - timedelta(days=i),  # i=0 most recent
        )
        session.add(v)
        await session.flush()
        session.add(
            VideoMetrics(
                video_id=v.id,
                views=1000 + i,
                engagement_rate=0.10 + i * 0.01,
                avg_view_duration_s=120.0,
                fetched_at=now,
            )
        )
        session.add(
            Transcript(
                video_id=v.id,
                source="whisperx",
                segments_jsonb={"segments": [{"words": [{"word": "hello"}, {"word": "world"}]}]},
            )
        )
        session.add(Signals(video_id=v.id, timeline_jsonb={"energy_spikes": [1], "laughter": []}))
        session.add(
            RetentionCurve(
                video_id=v.id, timestamp_s=10.0, audience_watch_ratio=0.9, is_rewatch_spike=True
            )
        )
        session.add(RetentionCurve(video_id=v.id, timestamp_s=500.0, audience_watch_ratio=0.5))
    await session.commit()
    return creator


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(Creator).where(Creator.id == creator_id))  # cascades to children
    await session.commit()


@pytest.mark.asyncio
async def test_build_patterns_batches_enrichment_queries(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr("config.settings.MIN_VIDEOS_FOR_DNA", 2)
    monkeypatch.setattr("config.settings.MIN_SHORTS_FOR_DNA", 99)
    creator = await _seed(db_session, n_long=4)

    seen: list[str] = []

    def _capture(conn, cursor, statement, *a):
        seen.append(statement.lower())

    sync_engine = db_session.bind.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        patterns, *_ = await build_patterns(db_session, creator.id)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)
        await _cleanup(db_session, creator.id)

    def _from(table: str) -> int:
        return sum(1 for s in seen if f"from {table}" in s)

    # One IN-query per enrichment table regardless of the 4 videos (was 4 each → 12).
    assert _from("transcripts") == 1
    assert _from("signals") == 1
    assert _from("retention_curves") == 1
    # Behavior preserved through the batch path.
    assert patterns["long_videos_analyzed"] == 4
    assert all("hook_text" in v for v in patterns["top_videos"])


async def _seed_mixed(session: AsyncSession, n_long: int, n_short: int) -> Creator:
    """Seed a creator with both long-form and Shorts videos."""
    creator = Creator(
        google_sub=f"t_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_{uuid.uuid4().hex[:6]}",
        channel_title="Mixed Test",
        onboarding_state=OnboardingState.dna_pending,
    )
    session.add(creator)
    await session.flush()
    now = datetime.now(UTC)
    for i, (kind, prefix) in enumerate(
        [(VideoKind.long, "L")] * n_long + [(VideoKind.short, "S")] * n_short
    ):
        v = Video(
            creator_id=creator.id,
            youtube_video_id=f"{prefix}{i:09d}"[:11],
            title=f"{kind.value} {i}",
            kind=kind,
            duration_s=600.0 if kind == VideoKind.long else 45.0,
            ingest_status=IngestStatus.done,
            published_at=now - timedelta(days=i),
        )
        session.add(v)
        await session.flush()
        session.add(
            VideoMetrics(
                video_id=v.id,
                views=1000 + i,
                engagement_rate=0.05 + i * 0.001,
                avg_view_duration_s=120.0 if kind == VideoKind.long else 30.0,
                fetched_at=now,
            )
        )
    await session.commit()
    return creator


@pytest.mark.asyncio
async def test_rank_videos_per_type_caps(db_session: AsyncSession, monkeypatch):
    """Issue 120: rank_videos caps longs and shorts independently.

    Seeding 5 longs and 8 shorts with caps of 3 each: should return exactly
    3 longs + 3 shorts = 6 total, sorted by weighted_score descending.
    """
    monkeypatch.setattr("config.settings.DNA_LONGS_CAP", 3)
    monkeypatch.setattr("config.settings.DNA_SHORTS_CAP", 3)
    creator = await _seed_mixed(db_session, n_long=5, n_short=8)
    try:
        ranked = await rank_videos(db_session, creator.id)
        longs = [v for v in ranked if v["kind"] == VideoKind.long.value]
        shorts = [v for v in ranked if v["kind"] == VideoKind.short.value]
        assert len(longs) == 3, f"expected 3 longs, got {len(longs)}"
        assert len(shorts) == 3, f"expected 3 shorts, got {len(shorts)}"
        assert len(ranked) == 6
        # Result must be sorted by weighted_score descending
        scores = [v["weighted_score"] for v in ranked]
        assert scores == sorted(scores, reverse=True)
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_rank_videos_shorts_cap_does_not_bleed_into_longs(
    db_session: AsyncSession, monkeypatch
):
    """A creator with many Shorts doesn't crowd out their long-form signal."""
    monkeypatch.setattr("config.settings.DNA_LONGS_CAP", 5)
    monkeypatch.setattr("config.settings.DNA_SHORTS_CAP", 3)
    creator = await _seed_mixed(db_session, n_long=5, n_short=20)
    try:
        ranked = await rank_videos(db_session, creator.id)
        longs = [v for v in ranked if v["kind"] == VideoKind.long.value]
        shorts = [v for v in ranked if v["kind"] == VideoKind.short.value]
        assert len(longs) == 5  # all 5 longs included despite 20 shorts
        assert len(shorts) == 3  # only 3 most-recent shorts
    finally:
        await _cleanup(db_session, creator.id)
