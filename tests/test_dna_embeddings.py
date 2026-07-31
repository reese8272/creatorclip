"""Unit tests for dna/embeddings.py::embed_clip_excerpts (Issue 375).

Mocks the Voyage client boundary (``_aembed``) and the DB session — no live
Voyage key or Postgres needed. Covers the two honesty-adjacent behaviours:
(1) silent no-op when Voyage isn't configured, matching ``embed_patterns``'
existing posture, and (2) every stored row carries the caller's ``creator_id``
so the isolation boundary on ``dna_embeddings`` (no other tenant column) holds.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dna.embeddings import embed_clip_excerpts
from models import DnaEmbeddingKind


def _session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_no_op_without_voyage_api_key() -> None:
    session = _session()
    with patch("dna.embeddings.settings.VOYAGE_API_KEY", ""):
        out = await embed_clip_excerpts(session, uuid.uuid4(), {uuid.uuid4(): "some text"})

    assert out == {}
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_no_op_for_empty_input() -> None:
    session = _session()
    with patch("dna.embeddings.settings.VOYAGE_API_KEY", "test-key"):
        out = await embed_clip_excerpts(session, uuid.uuid4(), {})
    assert out == {}
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_skips_clips_with_blank_text_but_embeds_the_rest() -> None:
    session = _session()
    creator_id = uuid.uuid4()
    clip_a, clip_b = uuid.uuid4(), uuid.uuid4()

    fake_result = MagicMock()
    fake_result.embeddings = [[0.1, 0.2, 0.3]]

    with (
        patch("dna.embeddings.settings.VOYAGE_API_KEY", "test-key"),
        patch("dna.embeddings._aembed", new=AsyncMock(return_value=fake_result)) as mocked_embed,
    ):
        out = await embed_clip_excerpts(
            session, creator_id, {clip_a: "real spoken content", clip_b: "   "}
        )

    assert list(out.keys()) == [clip_a]
    assert out[clip_a] == [0.1, 0.2, 0.3]
    mocked_embed.assert_awaited_once()
    called_texts = mocked_embed.await_args.args[0]
    assert called_texts == ["real spoken content"]

    session.add.assert_called_once()
    stored_row = session.add.call_args.args[0]
    assert stored_row.creator_id == creator_id, "every row must carry the caller's creator_id"
    assert stored_row.kind == DnaEmbeddingKind.clip
    assert stored_row.ref_jsonb == {"clip_id": str(clip_a)}
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_false_skips_committing() -> None:
    session = _session()
    creator_id = uuid.uuid4()
    clip_id = uuid.uuid4()
    fake_result = MagicMock()
    fake_result.embeddings = [[1.0]]

    with (
        patch("dna.embeddings.settings.VOYAGE_API_KEY", "test-key"),
        patch("dna.embeddings._aembed", new=AsyncMock(return_value=fake_result)),
    ):
        out = await embed_clip_excerpts(session, creator_id, {clip_id: "text"}, commit=False)

    assert out == {clip_id: [1.0]}
    session.commit.assert_not_awaited()
