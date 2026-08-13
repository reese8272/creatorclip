"""Issue 481 — the degraded-transcription flag must SURFACE, not just exist.

Two surfaces:
- GET /videos/{id}/transcript exposes an optional ``degraded`` field (None for
  every pre-481 stored transcript — additive, never required);
- the worker's transcribe stage emits a ``transcript_degraded`` SSE step when
  the normalizer flagged the one-segment fallback.
"""

import contextlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, Transcript, Video
from tests._helpers import owned_lookup_result

# ── endpoint surface ─────────────────────────────────────────────────────────


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id) -> MagicMock:
    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.creator_id = creator_id
    video.duration_s = 120.0
    return video


def _overrides(creator, video, transcript) -> None:
    async def _session():
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, video)
        )
        session.get = AsyncMock(return_value=transcript)
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session


def _get_transcript(client, video):
    try:
        return client.get(f"/videos/{video.id}/transcript", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()


def test_transcript_endpoint_surfaces_degraded_flag(client):
    creator = _creator()
    video = _video(creator.id)
    transcript = MagicMock(spec=Transcript)
    transcript.source = "deepgram"
    transcript.segments_jsonb = {
        "segments": [{"text": "whole video", "start": 0.0, "end": 120.0}],
        "degraded": "no_utterances",
    }
    _overrides(creator, video, transcript)
    resp = _get_transcript(client, video)
    assert resp.status_code == 200
    assert resp.json()["degraded"] == "no_utterances"


def test_transcript_endpoint_old_shape_has_none_degraded(client):
    """Every stored pre-481 transcript lacks the key — the endpoint must serve
    it as null, never KeyError or fabricate a value."""
    creator = _creator()
    video = _video(creator.id)
    transcript = MagicMock(spec=Transcript)
    transcript.source = "deepgram"
    transcript.segments_jsonb = {"segments": [{"text": "hi", "start": 0.0, "end": 2.0}]}
    _overrides(creator, video, transcript)
    resp = _get_transcript(client, video)
    assert resp.status_code == 200
    assert resp.json()["degraded"] is None


# ── worker SSE step ──────────────────────────────────────────────────────────


def _transcribe_async_harness(result: dict) -> list:
    """Drive _transcribe_async with everything but the SSE emitter mocked;
    return the aemit call list."""
    import worker.tasks as tasks

    video = MagicMock()
    video.audio_uri = "s3://bucket/audio.wav"
    video.ingest_status = "running"

    session = AsyncMock()
    # First session.get → the Video row; second → no existing Transcript.
    session.get = AsyncMock(side_effect=[video, None, None])
    session.add = MagicMock()

    @contextlib.asynccontextmanager
    async def fake_tenant_session(_creator_id):
        yield session

    @contextlib.asynccontextmanager
    async def fake_alocal_path(_uri):
        yield "/tmp/audio.wav"

    aemit = AsyncMock()
    with (
        patch.object(tasks.db, "tenant_session", fake_tenant_session),
        patch("worker.storage.alocal_path", fake_alocal_path),
        patch("worker.progress.aemit", aemit),
        patch("worker.tasks._tenant_id_or_raise", AsyncMock(return_value=str(uuid.uuid4()))),
        patch("ingestion.transcribe.transcribe_audio", return_value=result),
    ):
        import asyncio

        asyncio.run(tasks._transcribe_async(str(uuid.uuid4())))
    return aemit.call_args_list


def test_transcribe_async_emits_transcript_degraded_step():
    calls = _transcribe_async_harness(
        {
            "source": "deepgram",
            "segments": [{"start": 0.0, "end": 90.0, "text": "whole video", "words": []}],
            "degraded": "no_utterances",
        }
    )
    degraded = [c for c in calls if c.kwargs.get("label") == "transcript_degraded"]
    assert degraded, f"no transcript_degraded step emitted; labels: {[c.kwargs.get('label') for c in calls]}"
    assert degraded[0].kwargs.get("reason") == "no_utterances"


def test_transcribe_async_no_degraded_step_on_healthy_transcript():
    calls = _transcribe_async_harness(
        {
            "source": "deepgram",
            "segments": [{"start": 0.0, "end": 2.0, "text": "hi", "words": []}],
        }
    )
    assert not any(c.kwargs.get("label") == "transcript_degraded" for c in calls)
