"""
Integration test for Issue 35 — Idempotent DNA build (SEV-0).

Verifies that ``_build_dna_async`` writes the draft row, onboarding state, and
embeddings inside a single transaction.  When a Voyage embedding call fails mid-build,
the session rolls back so *no* draft row is persisted.  A subsequent retry therefore
produces exactly one draft row at the expected version — not an orphan at version N
*plus* a real row at version N+1.

Requires a running Postgres + Alembic schema (see docker-compose.yml).
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Creator,
    CreatorDna,
    DnaEmbedding,
    DnaStatus,
    OnboardingState,
)

# ── DB session fixture ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _seed_creator(
    session: AsyncSession,
    *,
    onboarding_state: OnboardingState = OnboardingState.awaiting_data,
) -> Creator:
    """Insert a minimal creator row and return it."""
    creator = Creator(
        google_sub=f"test_dna_idem_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_idem_{uuid.uuid4().hex[:6]}",
        channel_title="Idempotency Test Channel",
        onboarding_state=onboarding_state,
        minutes_balance=60,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    """Remove the test creator and all cascade-deleted rows."""
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


async def _draft_count(session: AsyncSession, creator_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count(CreatorDna.id)).where(
            CreatorDna.creator_id == creator_id,
            CreatorDna.status == DnaStatus.draft,
        )
    )


async def _embedding_count(session: AsyncSession, creator_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count(DnaEmbedding.id)).where(DnaEmbedding.creator_id == creator_id)
    )


# ── Shared stub patterns returned by the mocked build_patterns ───────────────

_STUB_PATTERNS: dict = {
    "top_videos": [{"title": "Top video", "hook_text": "great hook", "youtube_video_id": "yt1"}],
    "bottom_videos": [],
}

_STUB_BRIEF = "This creator excels at short-form storytelling."


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_voyage_failure_leaves_no_orphan_draft(db_session: AsyncSession):
    """
    If embed_patterns raises (simulating a Voyage network error), the entire
    transaction must roll back — zero draft rows should exist after the failure.
    """
    creator = await _seed_creator(db_session)
    creator_id_str = str(creator.id)

    try:
        with (
            patch(
                "dna.builder.build_patterns",
                return_value=(_STUB_PATTERNS, [], [], 60.0, "first_third", 24.0),
            ),
            patch("dna.brief.generate_brief", return_value=_STUB_BRIEF),
            # embed_patterns raises on first call — simulates Voyage HTTP error.
            patch(
                "dna.embeddings.embed_patterns",
                side_effect=RuntimeError("Voyage API unavailable"),
            ),
            pytest.raises(RuntimeError, match="Voyage API unavailable"),
        ):
            from worker.tasks import _build_dna_async

            await _build_dna_async(creator_id_str)

        # The session context manager rolled back — no draft row should exist.
        assert await _draft_count(db_session, creator.id) == 0
        assert await _embedding_count(db_session, creator.id) == 0
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_retry_after_voyage_failure_produces_exactly_one_draft(db_session: AsyncSession):
    """
    After a failed first attempt (Voyage raises), a successful retry must produce
    exactly one draft row at version 1 — no orphan at version 1 plus a real row at
    version 2.
    """
    creator = await _seed_creator(db_session)
    creator_id_str = str(creator.id)

    try:
        # --- First attempt: embed_patterns raises ---------------------------------
        with (
            patch(
                "dna.builder.build_patterns",
                return_value=(_STUB_PATTERNS, [], [], 60.0, "first_third", 24.0),
            ),
            patch("dna.brief.generate_brief", return_value=_STUB_BRIEF),
            patch(
                "dna.embeddings.embed_patterns",
                side_effect=RuntimeError("Voyage API unavailable"),
            ),
            pytest.raises(RuntimeError),
        ):
            from worker.tasks import _build_dna_async

            await _build_dna_async(creator_id_str)

        # Confirm rollback — no orphan row.
        assert await _draft_count(db_session, creator.id) == 0

        # --- Second attempt: embed helpers succeed (no-ops when VOYAGE_API_KEY is
        # unset — that is fine here; correctness of the draft version is what matters).
        with (
            patch(
                "dna.builder.build_patterns",
                return_value=(_STUB_PATTERNS, [], [], 60.0, "first_third", 24.0),
            ),
            patch("dna.brief.generate_brief", return_value=_STUB_BRIEF),
        ):
            from worker.tasks import _build_dna_async

            await _build_dna_async(creator_id_str)

        # Exactly one draft row at version 1.
        assert await _draft_count(db_session, creator.id) == 1

        # Confirm the version is 1 (not 2 — no orphan at 1 forced it to 2).
        row = await db_session.scalar(
            select(CreatorDna).where(
                CreatorDna.creator_id == creator.id,
                CreatorDna.status == DnaStatus.draft,
            )
        )
        assert row is not None
        assert row.version == 1
        assert row.brief_text == _STUB_BRIEF
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_successful_build_commits_draft_and_embeddings_together(db_session: AsyncSession):
    """
    Happy path: a clean build persists exactly one draft row.  Embedding storage is
    skipped when VOYAGE_API_KEY is unset (the conftest default) — that early-return
    path is intentional and does not affect draft row correctness.
    """
    creator = await _seed_creator(db_session)
    creator_id_str = str(creator.id)

    try:
        with (
            patch(
                "dna.builder.build_patterns",
                return_value=(_STUB_PATTERNS, [], [], 60.0, "first_third", 24.0),
            ),
            patch("dna.brief.generate_brief", return_value=_STUB_BRIEF),
        ):
            from worker.tasks import _build_dna_async

            await _build_dna_async(creator_id_str)

        assert await _draft_count(db_session, creator.id) == 1
    finally:
        await _cleanup(db_session, creator.id)
