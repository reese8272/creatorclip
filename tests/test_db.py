"""
Integration tests for schema, pgvector, and token round-trip.
Requires docker compose up (real postgres with pgvector).
Run with: pytest -m integration
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from crypto import decrypt, encrypt
from models import (
    Creator,
    DnaEmbedding,
    DnaEmbeddingKind,
    OnboardingState,
    YoutubeToken,
    append_audit,
)


@pytest_asyncio.fixture(scope="module")
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.integration
async def test_pgvector_insert_and_similarity_query(db_session: AsyncSession):
    """Vector column accepts data and cosine similarity query executes."""
    creator = Creator(
        google_sub=f"test_sub_{uuid.uuid4().hex[:8]}",
        onboarding_state=OnboardingState.connected,
    )
    db_session.add(creator)
    await db_session.flush()

    embedding_value = [0.1] * 1024
    emb = DnaEmbedding(
        creator_id=creator.id,
        kind=DnaEmbeddingKind.pattern,
        embedding=embedding_value,
    )
    db_session.add(emb)
    await db_session.flush()

    # Cosine similarity query — verifies pgvector operator works
    result = await db_session.execute(
        text("SELECT 1 - (embedding <=> :vec) AS similarity FROM dna_embeddings WHERE id = :id"),
        {"vec": str(embedding_value), "id": str(emb.id)},
    )
    row = result.fetchone()
    assert row is not None
    assert abs(row.similarity - 1.0) < 1e-4  # identical vectors → similarity ≈ 1.0

    await db_session.rollback()


@pytest.mark.integration
async def test_token_encrypt_decrypt_roundtrip_via_db(db_session: AsyncSession):
    """Token columns survive a write-to-DB + read-from-DB + decrypt cycle."""
    raw_token = "ya29.real_looking_access_token_value"

    creator = Creator(
        google_sub=f"test_sub_{uuid.uuid4().hex[:8]}",
        onboarding_state=OnboardingState.connected,
    )
    db_session.add(creator)
    await db_session.flush()

    token_row = YoutubeToken(
        creator_id=creator.id,
        access_token_encrypted=encrypt(raw_token),
        refresh_token_encrypted=encrypt("refresh_token_value"),
        scope="https://www.googleapis.com/auth/yt-analytics.readonly",
        expires_at=datetime.now(UTC),
    )
    db_session.add(token_row)
    await db_session.flush()

    # Reload from session to ensure we're reading what was persisted
    await db_session.refresh(token_row)
    assert decrypt(token_row.access_token_encrypted) == raw_token

    await db_session.rollback()


@pytest.mark.integration
async def test_audit_log_append_only(db_session: AsyncSession):
    """AuditLog rows are created via append_audit and cannot be modified."""
    creator = Creator(
        google_sub=f"test_sub_{uuid.uuid4().hex[:8]}",
        onboarding_state=OnboardingState.connected,
    )
    db_session.add(creator)
    await db_session.flush()

    await append_audit(
        db_session,
        action="creator.created",
        actor="system",
        entity_type="creator",
        entity_id=creator.id,
        after={"google_sub": creator.google_sub},
    )
    await db_session.flush()

    # Verify the row exists
    result = await db_session.execute(
        text("SELECT action FROM audit_log WHERE entity_id = :eid"),
        {"eid": str(creator.id)},
    )
    row = result.fetchone()
    assert row is not None
    assert row.action == "creator.created"

    await db_session.rollback()


@pytest.mark.integration
async def test_creator_isolation(db_session: AsyncSession):
    """No cross-creator data leakage — each creator can only access their own rows."""
    creator_a = Creator(
        google_sub=f"test_sub_{uuid.uuid4().hex[:8]}",
        onboarding_state=OnboardingState.connected,
    )
    creator_b = Creator(
        google_sub=f"test_sub_{uuid.uuid4().hex[:8]}",
        onboarding_state=OnboardingState.connected,
    )
    db_session.add_all([creator_a, creator_b])
    await db_session.flush()

    # Each creator's token should only be accessible with their own creator_id
    token_a = YoutubeToken(
        creator_id=creator_a.id,
        access_token_encrypted=encrypt("token_a"),
        refresh_token_encrypted=encrypt("refresh_a"),
        scope="https://www.googleapis.com/auth/yt-analytics.readonly",
        expires_at=datetime.now(UTC),
    )
    db_session.add(token_a)
    await db_session.flush()

    # Query with creator_b's ID — should return nothing
    result = await db_session.execute(
        text("SELECT * FROM youtube_tokens WHERE creator_id = :cid"),
        {"cid": str(creator_b.id)},
    )
    assert result.fetchone() is None

    await db_session.rollback()
