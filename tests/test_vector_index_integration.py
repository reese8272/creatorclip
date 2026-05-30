"""
Integration test for Issue 65 — the HNSW vector index and the clip_feedback FK
index exist with the correct method/op class after migration `0006`.

Marked `integration` (excluded from the default run — see pytest.ini). Requires
Alembic revision `f6a7b8c9d0e1` applied to the test DB.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _indexdef(session: AsyncSession, name: str) -> str | None:
    return await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}
    )


@pytest.mark.asyncio
async def test_hnsw_vector_index_present_with_cosine_ops(db_session: AsyncSession):
    indexdef = await _indexdef(db_session, "ix_dna_embeddings_hnsw")
    assert indexdef is not None, "HNSW index missing — migration 0006 not applied"
    # Must be an HNSW index over the cosine op class (matches the `<=>` query).
    assert "USING hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef


@pytest.mark.asyncio
async def test_clip_feedback_creator_id_indexed(db_session: AsyncSession):
    indexdef = await _indexdef(db_session, "ix_clip_feedback_creator_id")
    assert indexdef is not None, "clip_feedback.creator_id index missing"
    assert "clip_feedback" in indexdef and "creator_id" in indexdef
