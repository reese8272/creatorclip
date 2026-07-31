"""Integration tests for GET /creators/me/insights/originality (Issue 375).

Marker: integration — needs real Postgres (+ pgvector). Not runnable in this
sandbox (no live Postgres/Voyage) — field and enum names below were verified
against ``models.py`` and ``config.py`` directly rather than by execution; run
this file in CI to actually exercise it.

Mirrors ``tests/test_lift_integration.py``'s pattern (seed helper + explicit
cleanup + per-creator isolation as the load-bearing case), since
``DnaEmbedding`` — like ``ClipOutcome`` — is the kind of table where a filter
regression could silently leak another creator's data.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import create_session_token
from config import settings
from main import app
from models import (
    Clip,
    ClipFormat,
    Creator,
    DnaEmbedding,
    IngestStatus,
    OnboardingState,
    Transcript,
    Video,
    VideoKind,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _auth_cookie(creator_id: uuid.UUID) -> dict[str, str]:
    return {"cc_session": create_session_token(creator_id)}


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"test_orig_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_orig_{uuid.uuid4().hex[:6]}",
        channel_title="Originality Test",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _seed_clip_with_transcript(
    session: AsyncSession,
    creator_id: uuid.UUID,
    *,
    text: str,
    start_s: float = 10.0,
    end_s: float = 40.0,
) -> Clip:
    video = Video(
        creator_id=creator_id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:11]}",
        title="src",
        kind=VideoKind.long,
        duration_s=600.0,
        ingest_status=IngestStatus.done,
    )
    session.add(video)
    await session.flush()

    session.add(
        Transcript(
            video_id=video.id,
            source="whisperx",
            segments_jsonb={"segments": [{"start": start_s, "end": end_s, "text": text}]},
        )
    )

    clip = Clip(
        video_id=video.id,
        creator_id=creator_id,
        start_s=start_s,
        end_s=end_s,
        peak_s=start_s + 5.0,
        setup_start_s=start_s,
        format=ClipFormat.short,
        signals_jsonb={"principle": "clip-the-setup"},
    )
    session.add(clip)
    await session.commit()
    await session.refresh(clip)
    return clip


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(DnaEmbedding).where(DnaEmbedding.creator_id == creator_id))
    await session.execute(delete(Video).where(Video.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


def _fake_embed_result(vector: list[float]):
    result = AsyncMock()
    result.embeddings = [vector]
    return result


@pytest.mark.asyncio
async def test_originality_with_fewer_than_two_clips_is_unchecked_not_flagged(client, db_session):
    """Zero or one recent clip: honestly "couldn't check", not a false all-clear."""
    creator = await _seed_creator(db_session)
    try:
        resp = client.get("/creators/me/insights/originality", cookies=_auth_cookie(creator.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["checked"] is False
        assert data["flagged"] is False
        assert data["message"] is None
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_originality_per_creator_isolation(client, db_session):
    """Creator A's advisory must never be influenced by creator B's clips —
    embeddings carry no isolation boundary except creator_id (Issue 375)."""
    creator_a = await _seed_creator(db_session)
    creator_b = await _seed_creator(db_session)
    try:
        for _ in range(4):
            await _seed_clip_with_transcript(db_session, creator_b.id, text="repeated content here")

        with patch(
            "routers.insights.embed_clip_excerpts",
            new=AsyncMock(return_value={}),
        ):
            resp = client.get(
                "/creators/me/insights/originality", cookies=_auth_cookie(creator_a.id)
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["window"] == 0, "creator A must not see creator B's clips at all"
    finally:
        await _cleanup(db_session, creator_a.id)
        await _cleanup(db_session, creator_b.id)
