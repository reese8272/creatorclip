"""
Integration tests for Issue 63 — DNA build idempotency + single-confirmed invariant.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from dna.profile import confirm_draft, create_draft
from models import Creator, CreatorDna, DnaStatus, OnboardingState

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
        google_sub=f"test_dna_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_dna_{uuid.uuid4().hex[:6]}",
        channel_title="DNA Test",
        onboarding_state=OnboardingState.dna_pending,
    )
    session.add(creator)
    await session.commit()
    return creator


@pytest.mark.asyncio
async def test_build_dna_redelivery_is_noop(db_session: AsyncSession):
    """Same Celery job_id twice → one draft, and the second run does NOT re-spend."""
    from worker.tasks import _build_dna_async

    creator = await _seed_creator(db_session)
    job_id = f"task_{uuid.uuid4().hex}"

    patterns = {"dummy": True}
    with (
        patch(
            "dna.builder.build_patterns",
            new=AsyncMock(return_value=(patterns, [], [], None, None, None)),
        ),
        patch("dna.brief.generate_brief", return_value=("brief", {})) as mock_brief,
        patch("dna.embeddings.embed_patterns", new=AsyncMock()),
        patch("dna.embeddings.embed_brief", new=AsyncMock()),
    ):
        await _build_dna_async(str(creator.id), job_id)
        await _build_dna_async(str(creator.id), job_id)  # redelivery

    try:
        n = await db_session.scalar(
            select(func.count()).select_from(CreatorDna).where(CreatorDna.creator_id == creator.id)
        )
        assert n == 1  # exactly one draft despite two runs
        assert mock_brief.call_count == 1  # no duplicate paid LLM spend on redelivery
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_build_dna_concurrent_redelivery_builds_once(db_session: AsyncSession):
    """Two concurrent deliveries of the same job_id → one draft, one paid LLM call.

    Regression for Issue 76: the bare check-then-act let two concurrent redeliveries
    both pass the existence check and both run the paid Anthropic+Voyage build before
    colliding. The per-creator advisory lock + under-lock re-check must serialize them
    so the loser short-circuits before any paid call.
    """
    import asyncio

    from worker.tasks import _build_dna_async

    creator = await _seed_creator(db_session)
    job_id = f"task_{uuid.uuid4().hex}"

    patterns = {"dummy": True}
    brief_calls = 0

    def _slow_brief(*_args, **_kwargs):
        # Count invocations; the small sleep widens the race window so a missing
        # lock would let both deliveries enter the paid path.
        nonlocal brief_calls
        brief_calls += 1
        import time

        time.sleep(0.2)
        return "brief", {}

    with (
        patch(
            "dna.builder.build_patterns",
            new=AsyncMock(return_value=(patterns, [], [], None, None, None)),
        ),
        patch("dna.brief.generate_brief", side_effect=_slow_brief),
        patch("dna.embeddings.embed_patterns", new=AsyncMock()),
        patch("dna.embeddings.embed_brief", new=AsyncMock()),
    ):
        await asyncio.gather(
            _build_dna_async(str(creator.id), job_id),
            _build_dna_async(str(creator.id), job_id),
        )

    try:
        n = await db_session.scalar(
            select(func.count()).select_from(CreatorDna).where(CreatorDna.creator_id == creator.id)
        )
        assert n == 1  # exactly one draft despite two concurrent runs
        assert brief_calls == 1  # the loser short-circuited before the paid LLM call
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


async def _seed_creator_with_state(session: AsyncSession, state: OnboardingState) -> Creator:
    creator = Creator(
        google_sub=f"test_dna_state_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_dna_st_{uuid.uuid4().hex[:6]}",
        channel_title="State Test",
        onboarding_state=state,
    )
    session.add(creator)
    await session.commit()
    return creator


# ── Issue 98: onboarding state-machine arc (connected → dna_pending → active) ─


@pytest.mark.asyncio
async def test_create_draft_advances_connected_to_dna_pending(db_session: AsyncSession):
    """Issue 98 root cause: create_draft used to leave onboarding_state alone.
    A fresh creator (state=connected) created a draft, confirm_draft's
    `if state == dna_pending` precondition never matched, state stayed
    `connected` forever, and the dashboard's "Build your DNA" banner showed
    even after the user confirmed. The fix: create_draft bumps connected →
    dna_pending so the canonical arc completes."""
    creator = await _seed_creator_with_state(db_session, OnboardingState.connected)
    try:
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v1",
        )
        await db_session.refresh(creator)
        assert creator.onboarding_state == OnboardingState.dna_pending, (
            "create_draft must bump connected → dna_pending so confirm_draft's "
            "`dna_pending → active` branch is reachable. (Issue 98)"
        )
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_full_state_arc_connected_to_active(db_session: AsyncSession):
    """Full Issue 98 arc: create_draft (connected → dna_pending), then
    confirm_draft (dna_pending → active). End state is `active` — the
    dashboard banner conditional `state !== 'active'` correctly hides."""
    creator = await _seed_creator_with_state(db_session, OnboardingState.connected)
    try:
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v1",
        )
        await db_session.refresh(creator)
        assert creator.onboarding_state == OnboardingState.dna_pending

        await confirm_draft(db_session, creator.id)
        await db_session.refresh(creator)
        assert creator.onboarding_state == OnboardingState.active, (
            "Full arc connected → dna_pending → active must end at `active`. (Issue 98)"
        )
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_create_draft_idempotent_on_state_already_dna_pending(
    db_session: AsyncSession,
):
    """A creator already in `dna_pending` (e.g. a rebuild) stays in
    `dna_pending` — create_draft does not downgrade or churn the state."""
    creator = await _seed_creator_with_state(db_session, OnboardingState.dna_pending)
    try:
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v1",
        )
        await db_session.refresh(creator)
        assert creator.onboarding_state == OnboardingState.dna_pending
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_create_draft_does_not_regress_active_state(db_session: AsyncSession):
    """A creator already `active` (rebuild scenario) MUST NOT regress to
    `dna_pending` — a v2 build/confirm cycle should leave the dashboard's
    `active` state intact between draft creation and confirmation."""
    creator = await _seed_creator_with_state(db_session, OnboardingState.active)
    try:
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v2",
        )
        await db_session.refresh(creator)
        assert creator.onboarding_state == OnboardingState.active, (
            "create_draft MUST NOT downgrade an `active` creator back to "
            "`dna_pending` during a rebuild."
        )
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_confirm_draft_keeps_single_confirmed(db_session: AsyncSession):
    creator = await _seed_creator(db_session)
    try:
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v1",
        )
        first = await confirm_draft(db_session, creator.id)
        assert first.version == 1

        # Double-confirm with no newer draft → idempotent no-op.
        again = await confirm_draft(db_session, creator.id)
        assert again.id == first.id

        # A new draft + confirm supersedes the old one — still exactly one confirmed.
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v2",
        )
        await confirm_draft(db_session, creator.id)

        confirmed = await db_session.scalar(
            select(func.count())
            .select_from(CreatorDna)
            .where(CreatorDna.creator_id == creator.id, CreatorDna.status == DnaStatus.confirmed)
        )
        assert confirmed == 1
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()
