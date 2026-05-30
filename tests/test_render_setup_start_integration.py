"""
Integration test for Issue 59 — the render task must cut from the computed
`setup_start_s` (CLIPPING_PRINCIPLE #2), not the fixed peak−window `start_s`
fallback, against a real Postgres.

Marked `integration` (excluded from the default pytest run — see pytest.ini).
The DB-free regression guard lives in tests/test_render.py
(`test_render_start_uses_setup_start_*`); this proves the end-to-end wiring:
the persisted clip's setup_start_s is what reaches render_clip_file.
"""

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    Creator,
    IngestStatus,
    OnboardingState,
    RenderStatus,
    Video,
    VideoKind,
)
from worker.tasks import _render_clip_async

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_clip(session: AsyncSession, *, setup_start_s: float | None) -> Clip:
    creator = Creator(
        google_sub=f"test_render_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_render_{uuid.uuid4().hex[:6]}",
        channel_title="Render Test Channel",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()

    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Render fixture",
        kind=VideoKind.long,
        ingest_status=IngestStatus.done,
        source_uri=f"source/{creator.id}/fixture.mp4",
    )
    session.add(video)
    await session.flush()

    clip = Clip(
        video_id=video.id,
        creator_id=creator.id,
        setup_start_s=setup_start_s,
        start_s=40.0,
        end_s=72.0,
        peak_s=55.0,
        render_status=RenderStatus.pending,
    )
    session.add(clip)
    await session.flush()
    await session.commit()
    return clip


@pytest.mark.asyncio
async def test_render_task_cuts_from_setup_start_s(db_session: AsyncSession):
    clip = await _seed_clip(db_session, setup_start_s=12.0)
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = Path("/tmp/fixture_src.mp4")
    fake_cm.__exit__.return_value = False

    with (
        patch("worker.storage.local_path", return_value=fake_cm),
        patch("worker.storage.upload_file", return_value=f"clips/{clip.id}.mp4"),
        patch("clip_engine.render.render_clip_file") as mock_render,
    ):
        await _render_clip_async(str(clip.id))

    mock_render.assert_called_once()
    kwargs = mock_render.call_args.kwargs
    # The persisted setup_start_s (12.0) must reach the render, NOT start_s (40.0).
    assert kwargs["start_s"] == 12.0
    assert kwargs["end_s"] == 72.0

    await db_session.execute(delete(Clip).where(Clip.id == clip.id))
    await db_session.commit()
