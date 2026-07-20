"""Issue 361 (races) — uq_summaries_active integration tests (real Postgres).

The partial unique index (migration 0046) allows at most ONE in-flight
(pending/running) recap per video — the DB-level backstop for create_summary's
check-then-insert. done/failed rows leave the predicate so a later re-render
stays possible. Marked `integration` (excluded from the default run).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Creator,
    OnboardingState,
    RenderStatus,
    Summary,
    SummaryStatus,
    Video,
    VideoKind,
    VideoOrigin,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[Creator, Video]:
    creator = Creator(
        google_sub=f"test_sumrace_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_sumrace_{uuid.uuid4().hex[:6]}",
        channel_title="Summary Race Test",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Summary race fixture",
        kind=VideoKind.long,
        origin=VideoOrigin.upload,
        source_uri=f"source/{creator.id}/x.mp4",
    )
    session.add(video)
    await session.commit()
    return creator, video


def _summary(creator: Creator, video: Video) -> Summary:
    return Summary(
        creator_id=creator.id,
        video_id=video.id,
        target_duration_s=600,
        segments=[],
        status=SummaryStatus.ready,
        render_status=RenderStatus.pending,
    )


@pytest.mark.asyncio
async def test_second_active_summary_rejected_then_allowed_after_done(
    db_session: AsyncSession,
):
    creator, video = await _seed(db_session)
    creator_id = creator.id
    try:
        first = _summary(creator, video)
        db_session.add(first)
        await db_session.commit()
        first_id = first.id

        # A second in-flight recap for the same video violates the partial index.
        db_session.add(_summary(creator, video))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

        # rollback() expires ORM state regardless of expire_on_commit — re-fetch
        # before mutating, or the attribute set lazy-loads synchronously
        # (MissingGreenlet under the async session).
        first = await db_session.get(Summary, first_id)
        assert first is not None

        # Once the first render leaves pending/running, a re-render is allowed.
        first.render_status = RenderStatus.done
        await db_session.commit()
        db_session.add(_summary(creator, video))
        await db_session.commit()
    finally:
        await db_session.rollback()
        await db_session.execute(delete(Summary).where(Summary.creator_id == creator_id))
        await db_session.commit()
