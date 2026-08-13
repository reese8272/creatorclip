"""Issue 470 integration — trim+confirm leaves NO stale geometry, on real Postgres.

The acceptance round trip the unit lane cannot prove (the confirm CAS, the
JSONB round trip, and the reader endpoints all against the same committed row):

  1. seed creator/video/transcript/clip with a rendered 40 s window
  2. simulate the edit worker landing a middle-cut trim (cleaned_render_uri +
     pending_geometry_jsonb in one commit — as _edit_clip_async now writes)
  3. POST /clean/confirm → effective geometry persisted, pending cleared
  4. GET /clips/{id} duration_s == delivered 20 s (not end_s - origin)
  5. GET /clips/{id}/transcript → only kept words, delivered-time mapped
  6. POST /render → 202 discarded_edits=true and the geometry row is cleared —
     the pre-trim window never comes back silently

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import create_session_token
from clip_engine.edits import geometry_doc
from config import settings
from models import (
    Clip,
    Creator,
    IngestStatus,
    OnboardingState,
    RenderStatus,
    Transcript,
    Video,
    VideoKind,
    VideoOrigin,
)

pytestmark = pytest.mark.integration

# 40 s clip (origin 10 → end 50); the trim keeps [0,10] + [20,30] → 20 s.
_KEEP = [(0.0, 10.0), (20.0, 30.0)]


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _auth_cookie(creator_id: uuid.UUID) -> dict[str, str]:
    return {"cc_session": create_session_token(creator_id)}


async def _seed(session: AsyncSession) -> tuple[Creator, Clip]:
    creator = Creator(
        google_sub=f"test_geom_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_geom_{uuid.uuid4().hex[:6]}",
        channel_title="Geometry Test Channel",
        onboarding_state=OnboardingState.active,
        minutes_balance=200,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Geometry fixture",
        kind=VideoKind.long,
        origin=VideoOrigin.upload,
        ingest_status=IngestStatus.done,
        source_uri="videos/geom_src.mp4",
        duration_s=120.0,
    )
    session.add(video)
    await session.flush()
    session.add(
        Transcript(
            video_id=video.id,
            source="whisperx",
            segments_jsonb={
                "segments": [
                    {
                        "start": 10.0,
                        "end": 50.0,
                        "words": [
                            # video-absolute; clip origin 10 → clip-relative -10
                            {"word": "kept-early", "start": 12.0, "end": 13.0},
                            {"word": "cut-middle", "start": 22.0, "end": 23.0},
                            {"word": "kept-late", "start": 34.0, "end": 35.0},
                        ],
                    }
                ]
            },
        )
    )
    clip = Clip(
        video_id=video.id,
        creator_id=creator.id,
        setup_start_s=10.0,
        start_s=12.0,
        end_s=50.0,
        peak_s=35.0,
        score=0.8,
        signals_jsonb={},
        render_status=RenderStatus.done,
        render_uri=f"clips/{uuid.uuid4().hex}.mp4",
    )
    session.add(clip)
    # Simulate the edit worker landing the trim: URI + geometry, one commit.
    clip.cleaned_render_uri = f"clips/{uuid.uuid4().hex}_edit.mp4"
    clip.pending_geometry_jsonb = geometry_doc(_KEEP)
    await session.commit()
    return creator, clip


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


@pytest.mark.asyncio
async def test_trim_confirm_updates_every_geometry_reader(client, db_session: AsyncSession):
    creator, clip = await _seed(db_session)
    cleaned_uri = clip.cleaned_render_uri
    cookies = _auth_cookie(creator.id)
    try:
        # 3. Confirm bakes the trim in; storage purge is best-effort/off-path.
        with patch("worker.storage.adelete_file", AsyncMock(return_value=None)):
            resp = client.post(f"/clips/{clip.id}/clean/confirm", cookies=cookies)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "swapped"

        row = (
            await db_session.execute(
                # populate_existing: the app wrote via its own connection; without
                # it the seeding session's identity map serves the stale object.
                select(Clip).where(Clip.id == clip.id).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert row.render_uri == cleaned_uri
        assert row.cleaned_render_uri is None
        assert row.pending_geometry_jsonb is None
        assert row.effective_geometry_jsonb is not None
        assert row.effective_geometry_jsonb["duration_s"] == 20.0
        assert row.effective_geometry_jsonb["keep_segments_s"] == [[0.0, 10.0], [20.0, 30.0]]

        # 4. Duration reader: delivered 20 s, never end_s - origin (40 s).
        resp = client.get(f"/clips/{clip.id}", cookies=cookies)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["duration_s"] == 20.0
        assert body["has_baked_edits"] is True

        # 5. Transcript reader: cut word gone, kept words in delivered time.
        resp = client.get(f"/clips/{clip.id}/transcript", cookies=cookies)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["clip_duration_s"] == 20.0
        assert [w["word"] for w in body["words"]] == ["kept-early", "kept-late"]
        assert body["words"][0]["start_s"] == 2.0
        assert body["words"][1]["start_s"] == 14.0  # 24 s original → seg 2 offset 10

        # 6. Re-render regenerates the full window from source — it must say
        # so and clear the baked-edit record, not resurrect the window silently.
        task = MagicMock()
        task.id = "task-geom"
        with (
            patch("worker.tasks.render_clip") as render_task,
            patch("routers.clips.stamp_stream_owner", AsyncMock(return_value="/stream")),
        ):
            render_task.delay.return_value = task
            resp = client.post(f"/clips/{clip.id}/render", cookies=cookies)
        assert resp.status_code == 202, resp.text
        assert resp.json()["discarded_edits"] is True

        row = (
            await db_session.execute(
                # populate_existing: the app wrote via its own connection; without
                # it the seeding session's identity map serves the stale object.
                select(Clip).where(Clip.id == clip.id).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert row.effective_geometry_jsonb is None
        assert row.render_uri is None  # Issue-353 reset still applies
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_confirm_race_loser_does_not_touch_geometry(client, db_session: AsyncSession):
    """A confirm that loses the Issue-468 CAS must leave both geometry columns
    exactly as the winner wrote them."""
    creator, clip = await _seed(db_session)
    cookies = _auth_cookie(creator.id)
    try:
        with patch("worker.storage.adelete_file", AsyncMock(return_value=None)):
            first = client.post(f"/clips/{clip.id}/clean/confirm", cookies=cookies)
            assert first.json()["status"] == "swapped"
            second = client.post(f"/clips/{clip.id}/clean/confirm", cookies=cookies)
        assert second.status_code == 200
        assert second.json()["status"] == "noop"

        row = (
            await db_session.execute(
                # populate_existing: the app wrote via its own connection; without
                # it the seeding session's identity map serves the stale object.
                select(Clip).where(Clip.id == clip.id).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert row.effective_geometry_jsonb is not None
        assert row.effective_geometry_jsonb["duration_s"] == 20.0
        assert row.pending_geometry_jsonb is None
    finally:
        await _cleanup(db_session, creator.id)
