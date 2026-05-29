"""
Integration test for Issue 43 — source-media purge correctness against a real
Postgres. The retention clock is `ingest_done_at`, NOT `created_at`. An ingest
of an old upload that has not yet completed must NOT be purged.

Marked `integration` so it only runs when explicitly opted in (CI sets the
marker, default pytest run excludes it — see pytest.ini). Requires Alembic
revision `d4e5f6a7b8c9` (Issue 43) applied to the test DB.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Creator, IngestStatus, OnboardingState, Video, VideoKind

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"test_purge_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_purge_{uuid.uuid4().hex[:6]}",
        channel_title="Purge Test Channel",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()
    await session.commit()
    return creator


async def _seed_video(
    session: AsyncSession,
    *,
    creator_id: uuid.UUID,
    ingest_status: IngestStatus,
    ingest_done_at: datetime | None,
    source_uri: str | None,
) -> Video:
    video = Video(
        creator_id=creator_id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Purge fixture",
        kind=VideoKind.long,
        duration_s=300.0,
        source_uri=source_uri,
        ingest_status=ingest_status,
        ingest_done_at=ingest_done_at,
    )
    session.add(video)
    await session.flush()
    await session.commit()
    return video


async def _cleanup_creator(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(Video).where(Video.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


@pytest.mark.asyncio
async def test_purge_respects_ingest_done_at_gate(db_session):
    """Three videos, one cutoff:
    - done 100h ago        → source_uri nulled
    - in-progress 100h ago → source_uri preserved (the Issue 43 bug)
    - done 1h ago          → source_uri preserved (within cutoff)
    """
    from worker.tasks import _purge_stale_source_media_async

    creator = await _seed_creator(db_session)

    now = datetime.now(UTC)
    done_old = await _seed_video(
        db_session,
        creator_id=creator.id,
        ingest_status=IngestStatus.done,
        ingest_done_at=now - timedelta(hours=100),
        source_uri="s3://test/done_old.mp4",
    )
    in_progress_old = await _seed_video(
        db_session,
        creator_id=creator.id,
        ingest_status=IngestStatus.running,
        ingest_done_at=None,
        source_uri="s3://test/in_progress_old.mp4",
    )
    done_recent = await _seed_video(
        db_session,
        creator_id=creator.id,
        ingest_status=IngestStatus.done,
        ingest_done_at=now - timedelta(hours=1),
        source_uri="s3://test/done_recent.mp4",
    )

    try:
        with patch("worker.storage.delete_file"):
            await _purge_stale_source_media_async()

        # Re-read from DB. Use new sessions to avoid stale identity-map state.
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            ids = {done_old.id, in_progress_old.id, done_recent.id}
            rows = (await s.execute(select(Video).where(Video.id.in_(ids)))).scalars().all()
            uris = {v.id: v.source_uri for v in rows}
        await engine.dispose()

        assert uris[done_old.id] is None, "done 100h ago should be purged"
        assert uris[in_progress_old.id] == "s3://test/in_progress_old.mp4", (
            "in-progress old upload must NOT be purged (Issue 43)"
        )
        assert uris[done_recent.id] == "s3://test/done_recent.mp4", (
            "done 1h ago is within cutoff — not purged"
        )
    finally:
        await _cleanup_creator(db_session, creator.id)
