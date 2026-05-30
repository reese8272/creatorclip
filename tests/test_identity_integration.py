"""Integration tests for Issue 83 — Creator stated identity against real Postgres.

Marker: ``integration`` so only the integration-tests CI lane runs them
(default ``pytest -q`` excludes per pytest.ini). Covers the load-bearing
invariants that mock-based tests can't reach:

- Append-only: a second POST creates a NEW row and stamps the old one's
  ``superseded_at`` (the partial unique index is the DB-level backstop).
- Per-creator isolation: GETs only return the requesting creator's identity.
- Version ordering: history endpoint returns newest first.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from dna import identity as identity_module
from models import Creator, CreatorIdentity, OnboardingState


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.database_migration_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"test-identity-{uuid.uuid4()}",
        channel_title="IdentityTestChannel",
        email=f"identity-{uuid.uuid4()}@example.com",
        onboarding_state=OnboardingState.connected,
    )
    session.add(creator)
    await session.commit()
    await session.refresh(creator)
    return creator


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    if not creator_ids:
        return
    # Identity rows cascade-delete with the creator.
    await session.execute(Creator.__table__.delete().where(Creator.id.in_(creator_ids)))
    await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_creates_first_version(db_session):
    creator = await _seed_creator(db_session)
    try:
        row = await identity_module.upsert_identity(
            db_session,
            creator.id,
            niches=["27"],
            audience_summary="College students learning to invest.",
        )
        assert row.version == 1
        assert row.superseded_at is None
        assert row.niches == ["27"]
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_supersedes_previous_and_increments_version(db_session):
    """Second POST → version 2, current row swaps, version 1 stays in history
    with superseded_at stamped."""
    creator = await _seed_creator(db_session)
    try:
        first = await identity_module.upsert_identity(
            db_session,
            creator.id,
            niches=["27"],
            audience_summary="v1 audience",
        )
        second = await identity_module.upsert_identity(
            db_session,
            creator.id,
            niches=["20"],
            audience_summary="v2 audience",
        )

        # Re-read to get final state.
        rows = (
            (
                await db_session.execute(
                    select(CreatorIdentity)
                    .where(CreatorIdentity.creator_id == creator.id)
                    .order_by(CreatorIdentity.version.asc())
                )
            )
            .scalars()
            .all()
        )
        assert [r.version for r in rows] == [1, 2]
        assert rows[0].superseded_at is not None  # v1 is now historical
        assert rows[1].superseded_at is None  # v2 is current
        assert first.id != second.id
        assert second.niches == ["20"]
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_current_returns_only_unsuperseded(db_session):
    creator = await _seed_creator(db_session)
    try:
        await identity_module.upsert_identity(
            db_session,
            creator.id,
            niches=["27"],
            audience_summary="v1",
        )
        await identity_module.upsert_identity(
            db_session,
            creator.id,
            niches=["20"],
            audience_summary="v2",
        )
        current = await identity_module.get_current(db_session, creator.id)
        assert current is not None
        assert current.version == 2
        assert current.audience_summary == "v2"
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_history_returns_newest_first(db_session):
    creator = await _seed_creator(db_session)
    try:
        for i in range(3):
            await identity_module.upsert_identity(
                db_session,
                creator.id,
                niches=["27"],
                audience_summary=f"version {i + 1}",
            )
        history = await identity_module.get_history(db_session, creator.id)
        assert [r.version for r in history] == [3, 2, 1]
        # Only v3 is current.
        assert history[0].superseded_at is None
        assert all(r.superseded_at is not None for r in history[1:])
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_isolation_across_creators(db_session):
    """get_current for creator A must not return creator B's row.
    Load-bearing per the per-creator isolation rule."""
    creator_a = await _seed_creator(db_session)
    creator_b = await _seed_creator(db_session)
    try:
        await identity_module.upsert_identity(
            db_session,
            creator_a.id,
            niches=["27"],
            audience_summary="A's audience",
        )
        await identity_module.upsert_identity(
            db_session,
            creator_b.id,
            niches=["20"],
            audience_summary="B's audience",
        )

        a_current = await identity_module.get_current(db_session, creator_a.id)
        b_current = await identity_module.get_current(db_session, creator_b.id)
        assert a_current is not None and a_current.audience_summary == "A's audience"
        assert b_current is not None and b_current.audience_summary == "B's audience"
        assert a_current.id != b_current.id
        assert a_current.creator_id == creator_a.id
        assert b_current.creator_id == creator_b.id
    finally:
        await _cleanup(db_session, [creator_a.id, creator_b.id])
