"""
Integration tests for Issue 52 — worker pipeline.

The seven Celery async functions in `worker/tasks.py` (`_ingest_async`,
`_transcribe_async`, `_signals_async`, `_render_clip_async`,
`_generate_clips_async`, `_build_dna_async`, `_poll_clip_outcomes_async`)
previously had no end-to-end coverage. `test_pipeline_trigger.py` only
asserts registration / chaining; this file pins the load-bearing behaviors
the issue's acceptance criteria call out.

Marked `integration` — runs against live Postgres (see pytest.ini).
Storage (R2 / boto3) and external SDKs (Anthropic, Voyage, YouTube Data
API, ffmpeg, WhisperX) are mocked at their entry points; the established
codebase pattern is to never hit real external services from tests.
"""

import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    ClipFormat,
    ClipOutcome,
    Creator,
    CreatorDna,
    IngestStatus,
    MinuteDeduction,
    OnboardingState,
    RenderStatus,
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


@pytest_asyncio.fixture(autouse=True)
async def _reset_admin_pool():
    """Clear the shared module admin-engine pool around each test.

    `_poll_clip_outcomes_async` takes a *session-level* `pg_advisory_lock` on the
    module-level `db.admin_engine` (pool_size=5). pytest-asyncio runs each test in
    its own event loop, so a lock left on a pooled backend by a prior test (e.g.
    `test_poll_outcomes_bound_integration`, whose unlock coroutine can be torn down
    with its loop) survives into the next loop — the next poll then gets
    `acquired=False` and silently skips, leaving `performed_well` unset. Disposing
    the admin pool closes those backends so Postgres releases any leaked advisory
    lock; fresh backends start clean. Issue 143.
    """
    import db

    await db.admin_engine.dispose()
    yield
    await db.admin_engine.dispose()


# ── Seeding helpers ───────────────────────────────────────────────────────────


async def _seed_creator(
    session: AsyncSession, *, balance: int = 100, sub_prefix: str = "i52"
) -> Creator:
    creator = Creator(
        google_sub=f"test_{sub_prefix}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_{sub_prefix}_{uuid.uuid4().hex[:6]}",
        channel_title=f"Issue 52 {sub_prefix}",
        onboarding_state=OnboardingState.active,
        minutes_balance=balance,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _seed_video(
    session: AsyncSession,
    *,
    creator_id: uuid.UUID,
    source_uri: str | None = "s3://test/source.mp4",
    duration_s: float | None = None,
    ingest_status: IngestStatus = IngestStatus.running,
) -> Video:
    video = Video(
        creator_id=creator_id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Issue 52 fixture",
        kind=VideoKind.long,
        duration_s=duration_s,
        source_uri=source_uri,
        ingest_status=ingest_status,
    )
    session.add(video)
    await session.commit()
    return video


async def _seed_clip(
    session: AsyncSession,
    *,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    render_status: RenderStatus = RenderStatus.pending,
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


async def _seed_signals_and_transcript(session: AsyncSession, video_id: uuid.UUID) -> None:
    session.add(Signals(video_id=video_id, timeline_jsonb={"events": [], "duration_s": 300.0}))
    session.add(
        Transcript(
            video_id=video_id,
            source="whisperx",
            segments_jsonb={"source": "whisperx", "segments": []},
        )
    )
    await session.commit()


async def _seed_video_metrics(session: AsyncSession, video_id: uuid.UUID, views: int) -> None:
    session.add(
        VideoMetrics(
            video_id=video_id,
            views=views,
            watch_time_s=views * 10,
            engagement_rate=0.1,
            fetched_at=datetime.now(UTC),
        )
    )
    await session.commit()


async def _cleanup_creator(session: AsyncSession, creator_id: uuid.UUID) -> None:
    video_ids = (
        (await session.execute(select(Video.id).where(Video.creator_id == creator_id)))
        .scalars()
        .all()
    )
    if video_ids:
        await session.execute(delete(Clip).where(Clip.video_id.in_(video_ids)))
    await session.execute(delete(Video).where(Video.creator_id == creator_id))
    await session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


@asynccontextmanager
async def _dummy_local_path(_source_uri):
    """Stand-in for worker.storage.alocal_path — yields a real (empty) temp file."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
        yield Path(tmp.name)


# ── AC1: _ingest_async deducts minutes exactly once ───────────────────────────


@pytest.mark.asyncio
async def test_ingest_async_deducts_minutes_exactly_once(db_session):
    """Two invocations (simulating Celery at-least-once) → 1 deduction, 1 charge."""
    from worker.tasks import _ingest_async

    creator = await _seed_creator(db_session, balance=100, sub_prefix="ingest")
    video = await _seed_video(
        db_session,
        creator_id=creator.id,
        source_uri="s3://test/source.mp4",
    )

    def _touch_wav(_src, wav_path):
        Path(wav_path).touch()

    try:
        with (
            patch("worker.storage.alocal_path", _dummy_local_path),
            patch("youtube.ingest.probe_duration_s", return_value=300.0),
            patch("youtube.ingest.extract_audio_wav", side_effect=_touch_wav),
            patch(
                "worker.storage.upload_file",
                return_value=f"s3://test/audio/{video.id}.wav",
            ),
        ):
            await _ingest_async(str(video.id))
            await _ingest_async(str(video.id))  # Celery at-least-once replay

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            balance = await s.scalar(
                select(Creator.minutes_balance).where(Creator.id == creator.id)
            )
            n_deductions = await s.scalar(
                select(func.count(MinuteDeduction.id)).where(MinuteDeduction.video_id == video.id)
            )
            persisted = await s.get(Video, video.id)
            audio_uri = persisted.source_uri
            duration_persisted = persisted.duration_s
        await engine.dispose()

        # 300 s → ceil(300/60) = 5 min charged once
        assert n_deductions == 1
        assert balance == 95
        assert audio_uri.startswith("s3://test/audio/")
        assert duration_persisted == 300.0
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── AC2: _render_clip_async retried twice → no duplicates ─────────────────────


@pytest.mark.asyncio
async def test_render_clip_async_retried_does_not_duplicate(db_session):
    """Three invocations on the same clip → 1 row, render_uri set, status=done."""
    from worker.tasks import _render_clip_async

    creator = await _seed_creator(db_session, sub_prefix="render")
    video = await _seed_video(db_session, creator_id=creator.id, duration_s=300.0)
    clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.pending,
    )

    def _touch_clip(*_a, **_kw):
        # render_clip_file writes to its out_path argument; spec is `out_path` second-positional
        # for our purposes the mock can no-op; upload_file is patched separately.
        return None

    try:
        with (
            patch("worker.storage.alocal_path", _dummy_local_path),
            patch("clip_engine.render.render_clip_file", side_effect=_touch_clip),
            patch(
                "worker.storage.upload_file",
                return_value=f"s3://test/clips/{clip.id}.mp4",
            ),
        ):
            await _render_clip_async(str(clip.id))
            await _render_clip_async(str(clip.id))
            await _render_clip_async(str(clip.id))

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            clips = (await s.execute(select(Clip).where(Clip.video_id == video.id))).scalars().all()
        await engine.dispose()

        assert len(clips) == 1
        assert clips[0].id == clip.id
        assert clips[0].render_status == RenderStatus.done
        assert clips[0].render_uri == f"s3://test/clips/{clip.id}.mp4"
    finally:
        await _cleanup_creator(db_session, creator.id)


@pytest.mark.asyncio
async def test_render_video_clips_downloads_source_once(db_session):
    """The batch render fetches the source from R2 exactly ONCE for N clips and
    renders them all to done — the redundant per-clip download is gone."""
    from worker.tasks import _render_video_clips_async

    creator = await _seed_creator(db_session, sub_prefix="batchrender")
    video = await _seed_video(db_session, creator_id=creator.id, duration_s=300.0)
    clip_a = await _seed_clip(
        db_session, video_id=video.id, creator_id=creator.id, start_s=10.0, end_s=50.0
    )
    clip_b = await _seed_clip(
        db_session, video_id=video.id, creator_id=creator.id, start_s=60.0, end_s=100.0
    )

    download_calls = 0

    @asynccontextmanager
    async def _counting_local_path(_source_uri):
        nonlocal download_calls
        download_calls += 1
        with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
            yield Path(tmp.name)

    try:
        with (
            patch("worker.storage.alocal_path", _counting_local_path),
            patch("clip_engine.render.render_clip_file", side_effect=lambda *_a, **_k: None),
            patch("worker.storage.upload_file", side_effect=lambda _p, key: f"s3://test/{key}"),
        ):
            await _render_video_clips_async(str(video.id), [str(clip_a.id), str(clip_b.id)])

        # The whole point of the batch task: one source download, two clips.
        assert download_calls == 1

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            clips = (
                (await s.execute(select(Clip).where(Clip.video_id == video.id))).scalars().all()
            )
        await engine.dispose()

        assert len(clips) == 2
        assert {c.render_status for c in clips} == {RenderStatus.done}
        assert all(c.render_uri for c in clips)
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── AC3: generate_clips retry after partial success preserves done ────────────


@pytest.mark.asyncio
async def test_generate_clips_async_retry_preserves_done_clip(db_session):
    """A retry on a video that already has a done clip is a no-op — done clip survives,
    no duplicate pending rows are inserted (idempotency guard from Issue 46)."""
    from worker.tasks import _generate_clips_async

    creator = await _seed_creator(db_session, sub_prefix="genretry")
    video = await _seed_video(db_session, creator_id=creator.id, duration_s=300.0)
    await _seed_signals_and_transcript(db_session, video.id)
    done_clip = await _seed_clip(
        db_session,
        video_id=video.id,
        creator_id=creator.id,
        render_status=RenderStatus.done,
        start_s=10.0,
        end_s=50.0,
    )

    try:
        await _generate_clips_async(str(video.id))

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            clips = (await s.execute(select(Clip).where(Clip.video_id == video.id))).scalars().all()
        await engine.dispose()

        assert len(clips) == 1
        assert clips[0].id == done_clip.id
        assert clips[0].render_status == RenderStatus.done
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── AC4: poll_clip_outcomes uses per-creator median, not global ───────────────


@pytest.mark.asyncio
async def test_poll_clip_outcomes_uses_per_creator_median(db_session):
    """Two creators, same fetched views (100). Creator A median=500 → False;
    Creator B median=20 → True. A global-median computation would yield identical
    labels — the asymmetry proves per-creator scoping."""
    from worker.tasks import _poll_clip_outcomes_async

    creator_a = await _seed_creator(db_session, sub_prefix="medA")
    creator_b = await _seed_creator(db_session, sub_prefix="medB")
    now = datetime.now(UTC)

    # Creator A: three videos with views 100/500/900 → median 500
    a_vid = await _seed_video(db_session, creator_id=creator_a.id, duration_s=300.0)
    await _seed_video_metrics(db_session, a_vid.id, 100)
    a_vid2 = await _seed_video(db_session, creator_id=creator_a.id, duration_s=300.0)
    await _seed_video_metrics(db_session, a_vid2.id, 500)
    a_vid3 = await _seed_video(db_session, creator_id=creator_a.id, duration_s=300.0)
    await _seed_video_metrics(db_session, a_vid3.id, 900)

    # Creator B: three videos with views 10/20/30 → median 20
    b_vid = await _seed_video(db_session, creator_id=creator_b.id, duration_s=300.0)
    await _seed_video_metrics(db_session, b_vid.id, 10)
    b_vid2 = await _seed_video(db_session, creator_id=creator_b.id, duration_s=300.0)
    await _seed_video_metrics(db_session, b_vid2.id, 20)
    b_vid3 = await _seed_video(db_session, creator_id=creator_b.id, duration_s=300.0)
    await _seed_video_metrics(db_session, b_vid3.id, 30)

    # One clip + ClipOutcome per creator, both with stale fetched_at and no label yet.
    a_clip = await _seed_clip(
        db_session,
        video_id=a_vid.id,
        creator_id=creator_a.id,
        render_status=RenderStatus.done,
    )
    b_clip = await _seed_clip(
        db_session,
        video_id=b_vid.id,
        creator_id=creator_b.id,
        render_status=RenderStatus.done,
    )
    db_session.add_all(
        [
            ClipOutcome(
                clip_id=a_clip.id,
                published_youtube_id="yt_clip_a",
                performed_well=None,
                fetched_at=now - timedelta(hours=50),
            ),
            ClipOutcome(
                clip_id=b_clip.id,
                published_youtube_id="yt_clip_b",
                performed_well=None,
                fetched_at=now - timedelta(hours=50),
            ),
        ]
    )
    await db_session.commit()

    async def _fake_stats(_token, _yt_id):
        return {"views": 100}

    try:
        with (
            patch(
                "youtube.data_api.get_video_stats",
                new=AsyncMock(side_effect=_fake_stats),
            ),
            patch(
                "youtube.oauth.get_valid_access_token",
                new=AsyncMock(return_value="fake-token"),
            ),
        ):
            await _poll_clip_outcomes_async()

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            a_outcome = await s.get(ClipOutcome, a_clip.id)
            b_outcome = await s.get(ClipOutcome, b_clip.id)
        await engine.dispose()

        # 100 < median(500) → A is False
        assert a_outcome.performed_well is False
        # 100 >= median(20) → B is True. (Global median over all 6 = 65 would give
        # both clips True — the divergence proves per-creator scoping.)
        assert b_outcome.performed_well is True
    finally:
        await _cleanup_creator(db_session, creator_a.id)
        await _cleanup_creator(db_session, creator_b.id)


# ── AC5: build_dna below threshold → ValueError surfaces without retry ────────


@pytest.mark.asyncio
async def test_build_dna_below_threshold_raises_without_retry(db_session):
    """Creator with no videos → build_patterns raises ValueError. _build_dna_async
    surfaces the ValueError; the task wrapper at worker/tasks.py:184-196 catches
    `except ValueError: raise` and bypasses `self.retry` by inspection — so the
    contract here is "ValueError propagates" + "no CreatorDna draft is created".

    Note on calling `_build_dna_async` directly (vs. `build_dna.apply`): the task
    wrapper calls `run_async(...)` which falls back to `asyncio.run()` when no
    worker loop is installed. Calling `asyncio.run` from inside an already-running
    pytest-asyncio event loop raises RuntimeError. Direct-call matches the pattern
    in `tests/test_dna_build_idempotency.py`.
    """
    from worker.tasks import _build_dna_async

    creator = await _seed_creator(db_session, sub_prefix="dna")

    try:
        with pytest.raises(ValueError, match="Insufficient data"):
            await _build_dna_async(str(creator.id))

        # No draft row was created.
        n_dna = await db_session.scalar(
            select(func.count(CreatorDna.id)).where(CreatorDna.creator_id == creator.id)
        )
        assert n_dna == 0
    finally:
        await _cleanup_creator(db_session, creator.id)
