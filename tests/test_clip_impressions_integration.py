"""Integration tests for the clip-impression log (Issue 202).

Requires real Postgres with the RLS roles (docker-compose dev / integration CI),
so it is marked `integration` and deselected from the default unit lane. Verifies:
  1. an impression row captures (clip_id, rank, shown_at) per creator, and
  2. per-creator isolation holds — creator B cannot read creator A's impressions
     under the non-BYPASSRLS app role (the tenant_isolation policy from migration 0037).

Mirrors tests/test_rls_isolation_integration.py's role-switch strategy.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    ClipFormat,
    ClipImpression,
    Creator,
    IngestStatus,
    OnboardingState,
    RenderStatus,
    Video,
    VideoKind,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def admin_engine():
    eng = create_async_engine(settings.database_migration_url, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(admin_engine):
    factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed_creator_with_impression(session: AsyncSession, label: str):
    creator = Creator(
        google_sub=f"test_imp_{label}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_imp_{label}_{uuid.uuid4().hex[:6]}",
        channel_title=f"Impression Test {label}",
        onboarding_state=OnboardingState.active,
        minutes_balance=100,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="impression fixture",
        kind=VideoKind.long,
        duration_s=120.0,
        ingest_status=IngestStatus.done,
    )
    session.add(video)
    await session.flush()
    clip = Clip(
        video_id=video.id,
        creator_id=creator.id,
        start_s=10.0,
        end_s=50.0,
        peak_s=30.0,
        rank=1,
        format=ClipFormat.short,
        render_status=RenderStatus.done,
    )
    session.add(clip)
    await session.flush()
    imp = ClipImpression(creator_id=creator.id, clip_id=clip.id, rank=1, shown_at=datetime.now(UTC))
    session.add(imp)
    await session.commit()
    return creator, clip, imp


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    for model in (ClipImpression, Clip, Video):
        await session.execute(delete(model).where(model.creator_id.in_(creator_ids)))
    await session.execute(delete(Creator).where(Creator.id.in_(creator_ids)))
    await session.commit()


@pytest.mark.asyncio
async def test_impression_captures_fields(db_session):
    creator, clip, imp = await _seed_creator_with_impression(db_session, "fields")
    try:
        row = (
            await db_session.execute(
                select(ClipImpression).where(ClipImpression.creator_id == creator.id)
            )
        ).scalar_one()
        assert row.clip_id == clip.id
        assert row.rank == 1
        assert row.shown_at is not None
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.asyncio
async def test_impression_isolation_blocks_cross_tenant(admin_engine, db_session):
    """Under the creatorclip_app role with creator A's GUC set, an unfiltered scan of
    clip_impressions must never return creator B's rows (tenant_isolation RLS)."""
    creator_a, _, _ = await _seed_creator_with_impression(db_session, "A")
    creator_b, _, _ = await _seed_creator_with_impression(db_session, "B")
    try:
        factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            await s.execute(
                text("SELECT set_config('app.creator_id', :cid, true)"),
                {"cid": str(creator_a.id)},
            )
            rows = (await s.execute(text("SELECT creator_id FROM clip_impressions"))).all()
            visible = {r[0] for r in rows}
            assert creator_b.id not in visible, "RLS leak: creator B impressions visible to A"
    finally:
        await _cleanup(db_session, [creator_a.id, creator_b.id])
