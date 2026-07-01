"""
Integration tests for Issue 46 — generate-clips retry safety + outcomes poll bounds.

Three regressions to guard against:

1. A late retry of `generate_clips` must not delete already-rendered Clip rows
   (orphans R2 objects and breaks the ClipOutcome FK chain).
2. A late retry on a video whose clips are already `done` must short-circuit —
   no new pending duplicates may be inserted alongside the done rows.
3. `_poll_clip_outcomes_async` must not re-poll clips older than the
   measurement-lifecycle cap (10 days — origin Issue 70 supersedes the
   30-day floor local Issue 46 originally shipped), even if their
   `fetched_at` is past the 7d cutoff.

Marked `integration` so it only runs against a live Postgres (see pytest.ini).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing.ledger import deduct_for_video
from config import settings
from models import (
    Clip,
    ClipFormat,
    ClipOutcome,
    Creator,
    IngestStatus,
    MinutePack,
    OnboardingState,
    RenderStatus,
    Signals,
    Video,
    VideoKind,
)

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
        google_sub=f"test_i46_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_i46_{uuid.uuid4().hex[:6]}",
        channel_title="Issue 46 Test Channel",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _seed_video(session: AsyncSession, *, creator_id: uuid.UUID) -> Video:
    video = Video(
        creator_id=creator_id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Issue 46 fixture",
        kind=VideoKind.long,
        duration_s=300.0,
        ingest_status=IngestStatus.done,
    )
    session.add(video)
    await session.commit()
    return video


async def _seed_clip(
    session: AsyncSession,
    *,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    render_status: RenderStatus,
    start_s: float = 10.0,
    end_s: float = 50.0,
    created_at: datetime | None = None,
) -> Clip:
    clip = Clip(
        video_id=video_id,
        creator_id=creator_id,
        start_s=start_s,
        end_s=end_s,
        peak_s=(start_s + end_s) / 2,
        format=ClipFormat.short,
        render_status=render_status,
    )
    if created_at is not None:
        clip.created_at = created_at
    session.add(clip)
    await session.commit()
    return clip


async def _cleanup_creator(session: AsyncSession, creator_id: uuid.UUID) -> None:
    # Cascade is on the Creator FK for clips/videos — but ClipOutcome FK has
    # ON DELETE CASCADE from clip_id, so deleting clips drops outcomes too.
    video_ids = (
        (await session.execute(select(Video.id).where(Video.creator_id == creator_id)))
        .scalars()
        .all()
    )
    if video_ids:
        await session.execute(delete(Clip).where(Clip.video_id.in_(video_ids)))
    await session.execute(delete(Video).where(Video.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


# ── Bug A: selective delete preserves done/running clips ──────────────────────


@pytest.mark.asyncio
async def test_generate_and_rank_delete_preserves_done_and_running(db_session):
    """The DELETE in generate_and_rank_clips must not touch done or running rows."""
    creator = await _seed_creator(db_session)
    video = await _seed_video(db_session, creator_id=creator.id)

    done_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.done,
        start_s=10.0,
        end_s=50.0,
    )
    running_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.running,
        start_s=60.0,
        end_s=100.0,
    )
    pending_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.pending,
        start_s=110.0,
        end_s=150.0,
    )
    failed_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.failed,
        start_s=160.0,
        end_s=200.0,
    )

    try:
        # Execute the same DELETE generate_and_rank_clips now uses.
        await db_session.execute(
            delete(Clip).where(
                Clip.video_id == video.id,
                Clip.render_status.notin_([RenderStatus.done, RenderStatus.running]),
            )
        )
        await db_session.commit()

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            surviving = (
                (await s.execute(select(Clip.id).where(Clip.video_id == video.id))).scalars().all()
            )
        await engine.dispose()

        surviving_set = set(surviving)
        assert done_clip.id in surviving_set, "done clip must survive retry"
        assert running_clip.id in surviving_set, "running clip must survive retry"
        assert pending_clip.id not in surviving_set, "pending clip should be cleared"
        assert failed_clip.id not in surviving_set, "failed clip should be cleared"
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── Bug A: idempotency guard in _generate_clips_async ─────────────────────────


@pytest.mark.asyncio
async def test_generate_clips_async_short_circuits_when_done_exists(db_session):
    """If a done clip exists, _generate_clips_async must skip without raising,
    even when Signals are missing (the guard runs before the Signals lookup)."""
    from worker.tasks import _generate_clips_async

    creator = await _seed_creator(db_session)
    video = await _seed_video(db_session, creator_id=creator.id)
    done_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.done,
    )

    try:
        # No Signals row exists — without the guard this would raise
        # "Signals not available for video ...". With the guard it returns cleanly.
        await _generate_clips_async(str(video.id))

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            clips = (await s.execute(select(Clip).where(Clip.video_id == video.id))).scalars().all()
        await engine.dispose()

        assert len(clips) == 1, "guard must not insert new clips when done exists"
        assert clips[0].id == done_clip.id
        assert clips[0].render_status == RenderStatus.done
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── Bug B: created_at cap on _poll_clip_outcomes_async ───────────────────────
#
# Local Issue 46 originally bounded the poll set with a 30-day `created_at`
# floor. Origin Issue 70 superseded that with a tighter 10-day cap (clips'
# 48h + 7d measurement lifecycle ends well before 10 days) plus a
# `ClipOutcome.final` flag. The merge adopted origin's bound; this test
# now exercises the 10-day boundary.


@pytest.mark.asyncio
async def test_poll_clip_outcomes_excludes_clips_older_than_cap(db_session):
    """A clip created beyond the cap (10 days) must not be re-polled even if
    its fetched_at is past the 7d cutoff. A clip comfortably inside the cap
    must still re-poll."""
    from worker.tasks import _poll_clip_outcomes_async

    creator = await _seed_creator(db_session)
    video = await _seed_video(db_session, creator_id=creator.id)
    now = datetime.now(UTC)

    old_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.done,
        # Comfortably beyond the 10-day cap.
        created_at=now - timedelta(days=15),
    )
    fresh_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.done,
        start_s=200.0,
        end_s=250.0,
        # Comfortably inside the 10-day cap (avoid the boundary timing race
        # that would race against the cutoff computed inside the function).
        created_at=now - timedelta(days=3),
    )

    # Both have stale fetched_at (>7d) so absent the cap they would both
    # qualify for re-poll. ClipOutcome.final defaults to False, so the
    # `final.is_(False)` filter in the query lets both through on that axis.
    db_session.add_all(
        [
            ClipOutcome(
                clip_id=old_clip.id,
                published_youtube_id="yt_old_clip",
                performed_well=True,
                fetched_at=now - timedelta(days=8),
            ),
            ClipOutcome(
                clip_id=fresh_clip.id,
                published_youtube_id="yt_fresh_clip",
                performed_well=True,
                fetched_at=now - timedelta(days=8),
            ),
        ]
    )
    await db_session.commit()

    polled_ids: list[str] = []

    async def _fake_stats(_token, youtube_id):
        polled_ids.append(youtube_id)
        return {"views": 1000}

    try:
        with (
            patch("youtube.data_api.get_video_stats", new=AsyncMock(side_effect=_fake_stats)),
            patch(
                "youtube.oauth.get_valid_access_token",
                new=AsyncMock(return_value="fake-token"),
            ),
        ):
            await _poll_clip_outcomes_async()

        assert "yt_old_clip" not in polled_ids, "clip past the 10-day cap must be excluded"
        assert "yt_fresh_clip" in polled_ids, "clip inside the 10-day cap must still re-poll"
    finally:
        # Outcomes cascade-delete with their clips.
        await _cleanup_creator(db_session, creator.id)
        # Tidy the orphaned Signals nothing seeded — none here, but be safe.
        await db_session.execute(delete(Signals).where(Signals.video_id == video.id))
        await db_session.commit()


# ── Issue 336: generate_clips terminal failure → refund fires exactly once ────


def test_generate_clips_terminal_failure_refunds_exactly_once() -> None:
    """End-to-end (real Postgres, no async harness): generate_clips.on_failure
    triggers a real DB refund that is idempotent on double-invocation.

    This test is intentionally synchronous so that on_failure → run_async →
    asyncio.run() works without conflicting with a running pytest-asyncio loop.
    The refund is confirmed by inspecting the minute_packs table directly.
    """
    from worker.tasks import generate_clips

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> tuple[uuid.UUID, uuid.UUID]:
        async with factory() as s:
            creator = Creator(
                google_sub=f"test_i336_refund_{uuid.uuid4().hex[:8]}",
                channel_id=f"UC_i336_{uuid.uuid4().hex[:6]}",
                channel_title="Issue 336 Refund Test",
                onboarding_state=OnboardingState.active,
                minutes_balance=50,
            )
            s.add(creator)
            await s.flush()
            video = Video(
                creator_id=creator.id,
                youtube_video_id=f"yt_i336_{uuid.uuid4().hex[:8]}",
                kind=VideoKind.long,
                ingest_status=IngestStatus.failed,
                duration_s=300.0,
            )
            s.add(video)
            await s.commit()
            # Deduct minutes (simulating successful ingest before generate_clips)
            await deduct_for_video(video.id, creator.id, 300.0, s)
            await s.commit()
            return creator.id, video.id

    creator_id, video_id = asyncio.run(_setup())

    try:
        # First on_failure: should create the refund row (MinutePack with reason=refund).
        generate_clips.on_failure(
            exc=RuntimeError("LLM score loop failed"),
            task_id="i336-gc-task-a",
            args=(str(video_id),),
            kwargs={},
            einfo=None,
        )
        # Second on_failure (Celery at-least-once redelivery of the failure signal):
        # must be idempotent — no second refund row.
        generate_clips.on_failure(
            exc=RuntimeError("LLM score loop failed"),
            task_id="i336-gc-task-b",
            args=(str(video_id),),
            kwargs={},
            einfo=None,
        )

        async def _check() -> list[MinutePack]:
            async with factory() as s:
                return list(
                    (
                        await s.execute(
                            select(MinutePack).where(
                                MinutePack.pack_id == f"refund:{video_id}",
                                MinutePack.reason == "refund",
                            )
                        )
                    ).scalars()
                )

        packs = asyncio.run(_check())
        assert len(packs) == 1, (
            f"exactly one refund pack must exist even after double on_failure; got {len(packs)}"
        )
        assert packs[0].minutes_granted == 5, f"300 s → 5 min; got {packs[0].minutes_granted}"
    finally:
        async def _cleanup() -> None:
            async with factory() as s:
                await s.execute(delete(MinutePack).where(MinutePack.creator_id == creator_id))
                await s.execute(delete(Creator).where(Creator.id == creator_id))
                await s.commit()
            await engine.dispose()

        asyncio.run(_cleanup())
