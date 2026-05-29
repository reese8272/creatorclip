"""
Store DNA pattern embeddings via Voyage AI → pgvector.
"""

import asyncio
import logging
import uuid

import voyageai
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from models import DnaEmbedding, DnaEmbeddingKind

logger = logging.getLogger(__name__)

_VOYAGE_MAX_TEXT = 2000  # conservative character limit per text

_VOYAGE: voyageai.Client | None = None  # lazy singleton; populated on first embed call


def _voyage() -> voyageai.Client:
    global _VOYAGE
    if _VOYAGE is None:
        _VOYAGE = voyageai.Client(api_key=settings.VOYAGE_API_KEY, timeout=30)
    return _VOYAGE


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _embed(texts: list[str], model: str, input_type: str):
    return _voyage().embed(texts, model=model, input_type=input_type)


async def _aembed(texts: list[str], model: str, input_type: str):
    """Async wrapper — Voyage's Python SDK is sync, so offload to a thread so
    the event loop stays responsive during the HTTP round-trip (Issue 38 W1).
    """
    return await asyncio.to_thread(_embed, texts, model, input_type)


async def embed_patterns(
    session: AsyncSession,
    creator_id: uuid.UUID,
    patterns: dict,
    *,
    commit: bool = True,
) -> None:
    """Embed top/bottom video title+hook pairs and store as 'pattern' kind embeddings.

    Silently skips if VOYAGE_API_KEY is not configured.

    Args:
        session: Active async database session.
        creator_id: UUID of the owning creator.
        patterns: DNA pattern dict from the builder.
        commit: When True (default), commit after adding embedding rows.
            Pass ``commit=False`` when the caller manages the transaction and will
            commit after additional writes so all rows land atomically.
    """
    if not settings.VOYAGE_API_KEY:
        logger.warning("VOYAGE_API_KEY not set — skipping DNA pattern embeddings")
        return

    texts: list[str] = []
    refs: list[dict] = []

    for label, key in (("top", "top_videos"), ("bottom", "bottom_videos")):
        for v in patterns.get(key, []):
            text = f"{v.get('title', '')} | {v.get('hook_text', '')}".strip(" |")
            if text:
                texts.append(text[:_VOYAGE_MAX_TEXT])
                refs.append({"youtube_video_id": v.get("youtube_video_id"), "kind": label})

    if not texts:
        return

    result = await _aembed(texts, model="voyage-3.5", input_type="document")

    for i, embedding in enumerate(result.embeddings):
        session.add(
            DnaEmbedding(
                creator_id=creator_id,
                kind=DnaEmbeddingKind.pattern,
                embedding=embedding,
                ref_jsonb=refs[i],
            )
        )
    if commit:
        await session.commit()
    logger.info("Stored %d DNA pattern embeddings for creator %s", len(texts), creator_id)


async def embed_brief(
    session: AsyncSession,
    creator_id: uuid.UUID,
    brief_text: str,
    *,
    commit: bool = True,
) -> None:
    """Embed the creator brief text and store as a 'hook' kind embedding.

    Args:
        session: Active async database session.
        creator_id: UUID of the owning creator.
        brief_text: Plain-language creator brief text to embed.
        commit: When True (default), commit after adding the embedding row.
            Pass ``commit=False`` when the caller manages the transaction and will
            commit after additional writes so all rows land atomically.
    """
    if not settings.VOYAGE_API_KEY:
        return

    result = await _aembed(
        [brief_text[:_VOYAGE_MAX_TEXT]], model="voyage-3.5", input_type="document"
    )
    session.add(
        DnaEmbedding(
            creator_id=creator_id,
            kind=DnaEmbeddingKind.hook,
            embedding=result.embeddings[0],
            ref_jsonb={"source": "brief"},
        )
    )
    if commit:
        await session.commit()
    logger.info("Stored DNA brief embedding for creator %s", creator_id)
