"""
Unit tests for Issue 68 — blocking Voyage calls are offloaded off the event loop.

DB-free: embed_patterns/embed_brief issue no DB queries (only session.add), so a
MagicMock session is legitimate here — no query result is faked. The worker-side
transcribe/brief offloads are exercised behavior-preservingly by the existing
pipeline tests (test_ingest, test_dna_build_idempotency).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import dna.embeddings as emb


def _offload_recorder():
    calls: list = []

    async def _to_thread(fn, *args, **kwargs):
        calls.append(fn)
        return fn(*args, **kwargs)

    return calls, _to_thread


@pytest.mark.asyncio
async def test_embed_patterns_offloads_voyage_call(monkeypatch):
    monkeypatch.setattr(emb.settings, "VOYAGE_API_KEY", "test-key")
    fake_result = MagicMock()
    fake_result.embeddings = [[0.0] * 1024]
    calls, fake_to_thread = _offload_recorder()

    session = MagicMock()
    session.commit = AsyncMock()
    with (
        patch.object(emb, "_embed", return_value=fake_result) as m_embed,
        patch("asyncio.to_thread", new=fake_to_thread),
    ):
        await emb.embed_patterns(
            session,
            uuid.uuid4(),
            {
                "top_videos": [{"title": "t", "hook_text": "h", "youtube_video_id": "x"}],
                "bottom_videos": [],
            },
            commit=False,
        )

    # The sync Voyage call must have been offloaded, not run on the loop.
    assert m_embed in calls
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_embed_brief_offloads_voyage_call(monkeypatch):
    monkeypatch.setattr(emb.settings, "VOYAGE_API_KEY", "test-key")
    fake_result = MagicMock()
    fake_result.embeddings = [[0.0] * 1024]
    calls, fake_to_thread = _offload_recorder()

    session = MagicMock()
    session.commit = AsyncMock()
    with (
        patch.object(emb, "_embed", return_value=fake_result) as m_embed,
        patch("asyncio.to_thread", new=fake_to_thread),
    ):
        await emb.embed_brief(session, uuid.uuid4(), "my brief text", commit=False)

    assert m_embed in calls
    session.add.assert_called_once()
