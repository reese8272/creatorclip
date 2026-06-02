"""Integration tests for Wave 6 Fix A — onboarding_state backfill (migration 0014).

The Issue 98 `create_draft` fix is forward-only. Any creator who confirmed
their DNA under the pre-fix code path has `onboarding_state = 'connected'`
permanently, and the dashboard "Build your Creator DNA" banner stays
visible. Migration 0014 heals them by setting `onboarding_state = 'active'`
where a confirmed DNA row exists.

These tests pin the heal semantics on seeded fixture data:
- Stuck creators (confirmed DNA + connected/awaiting_data) → healed to active
- Legitimate dna_pending creators (rebuild-in-progress) → NOT touched
- Already-active creators → no-op
- Creators with no confirmed DNA → NOT touched
- Re-running the backfill is idempotent

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Creator, CreatorDna, DnaStatus, OnboardingState

pytestmark = pytest.mark.integration


# Canonical backfill SQL — kept in lockstep with
# `alembic/versions/0014_backfill_onboarding_state.py::upgrade`. If you edit
# one, edit the other. The duplication is intentional: the integration test
# verifies the SQL semantics directly, without coupling to alembic's migration
# runner (which has its own correctness guarantees).
_BACKFILL_SQL = text(
    """
    UPDATE creators
    SET onboarding_state = 'active'
    WHERE id IN (
        SELECT DISTINCT creator_id
        FROM creator_dna
        WHERE status = 'confirmed'
    )
    AND onboarding_state IN ('connected', 'awaiting_data')
    """
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession, state: OnboardingState) -> Creator:
    creator = Creator(
        google_sub=f"test_bf_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_bf_{uuid.uuid4().hex[:6]}",
        channel_title="Backfill Test",
        onboarding_state=state,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _seed_dna(
    session: AsyncSession, creator_id: uuid.UUID, status: DnaStatus, version: int = 1
) -> CreatorDna:
    dna = CreatorDna(
        creator_id=creator_id,
        version=version,
        brief_text="test brief",
        patterns_jsonb={},
        top_video_ids_jsonb=[],
        bottom_video_ids_jsonb=[],
        status=status,
    )
    session.add(dna)
    await session.commit()
    return dna


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


@pytest.mark.asyncio
async def test_backfill_heals_connected_with_confirmed_dna(db_session: AsyncSession):
    """The canonical stuck case: creator has confirmed DNA but state stayed
    `connected` because v1 was confirmed under the pre-Issue-98 code path."""
    creator = await _seed_creator(db_session, OnboardingState.connected)
    try:
        await _seed_dna(db_session, creator.id, DnaStatus.confirmed)

        await db_session.execute(_BACKFILL_SQL)
        await db_session.commit()
        await db_session.refresh(creator)

        assert creator.onboarding_state == OnboardingState.active, (
            "Backfill must heal connected creators with confirmed DNA. "
            "This is the Backboard-Media-observed case."
        )
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_backfill_heals_awaiting_data_with_confirmed_dna(db_session: AsyncSession):
    """`awaiting_data` is the other pre-DNA state — same heal applies if a
    confirmed DNA row exists (race-condition recovery)."""
    creator = await _seed_creator(db_session, OnboardingState.awaiting_data)
    try:
        await _seed_dna(db_session, creator.id, DnaStatus.confirmed)

        await db_session.execute(_BACKFILL_SQL)
        await db_session.commit()
        await db_session.refresh(creator)

        assert creator.onboarding_state == OnboardingState.active
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_backfill_does_not_touch_dna_pending(db_session: AsyncSession):
    """`dna_pending` is a legitimate rebuild-in-progress state (older
    confirmed + newer draft). The banner copy for it is correct ('Your DNA
    is ready — confirm your Creator Brief'). MUST NOT be backfilled."""
    creator = await _seed_creator(db_session, OnboardingState.dna_pending)
    try:
        # Confirmed v1 + draft v2 = the rebuild scenario.
        await _seed_dna(db_session, creator.id, DnaStatus.confirmed, version=1)
        await _seed_dna(db_session, creator.id, DnaStatus.draft, version=2)

        await db_session.execute(_BACKFILL_SQL)
        await db_session.commit()
        await db_session.refresh(creator)

        assert creator.onboarding_state == OnboardingState.dna_pending, (
            "Backfill MUST NOT touch dna_pending — it's a valid intermediate "
            "state during a rebuild."
        )
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_backfill_skips_creator_without_confirmed_dna(db_session: AsyncSession):
    """A `connected` creator who has not confirmed any DNA stays `connected` —
    the dashboard banner is correctly directing them to set up."""
    creator = await _seed_creator(db_session, OnboardingState.connected)
    try:
        # Draft only — never confirmed.
        await _seed_dna(db_session, creator.id, DnaStatus.draft)

        await db_session.execute(_BACKFILL_SQL)
        await db_session.commit()
        await db_session.refresh(creator)

        assert creator.onboarding_state == OnboardingState.connected, (
            "Backfill MUST require a confirmed DNA row — a draft-only creator "
            "still legitimately needs to confirm."
        )
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db_session: AsyncSession):
    """Running the backfill twice must not error and must not churn state on
    already-active creators."""
    creator = await _seed_creator(db_session, OnboardingState.connected)
    try:
        await _seed_dna(db_session, creator.id, DnaStatus.confirmed)

        await db_session.execute(_BACKFILL_SQL)
        await db_session.commit()
        await db_session.execute(_BACKFILL_SQL)
        await db_session.commit()
        await db_session.refresh(creator)

        assert creator.onboarding_state == OnboardingState.active
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_backfill_targets_only_stuck_creators_in_mixed_population(
    db_session: AsyncSession,
):
    """End-to-end invariant: in a population mixing every state × every DNA
    shape, only the (connected|awaiting_data) ∧ has-confirmed-DNA rows flip
    to active. Everything else is untouched."""
    stuck = await _seed_creator(db_session, OnboardingState.connected)
    pending = await _seed_creator(db_session, OnboardingState.dna_pending)
    already_active = await _seed_creator(db_session, OnboardingState.active)
    no_dna = await _seed_creator(db_session, OnboardingState.connected)

    try:
        await _seed_dna(db_session, stuck.id, DnaStatus.confirmed)
        await _seed_dna(db_session, pending.id, DnaStatus.confirmed, version=1)
        await _seed_dna(db_session, pending.id, DnaStatus.draft, version=2)
        await _seed_dna(db_session, already_active.id, DnaStatus.confirmed)
        # no_dna: no DNA rows at all

        await db_session.execute(_BACKFILL_SQL)
        await db_session.commit()

        # expire_on_commit=False means the identity map retains stale pre-UPDATE
        # states. Expunge so the verification SELECT re-reads fresh from the DB.
        db_session.expunge_all()

        # Re-read every creator's state.
        states = {
            row.id: row.onboarding_state
            for row in (
                await db_session.execute(
                    select(Creator).where(
                        Creator.id.in_([stuck.id, pending.id, already_active.id, no_dna.id])
                    )
                )
            )
            .scalars()
            .all()
        }

        assert states[stuck.id] == OnboardingState.active  # healed
        assert states[pending.id] == OnboardingState.dna_pending  # untouched
        assert states[already_active.id] == OnboardingState.active  # untouched
        assert states[no_dna.id] == OnboardingState.connected  # untouched
    finally:
        for cid in (stuck.id, pending.id, already_active.id, no_dna.id):
            await _cleanup(db_session, cid)
