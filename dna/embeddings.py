"""
Store DNA pattern embeddings via Voyage AI → pgvector.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import DnaEmbedding, DnaEmbeddingKind

logger = logging.getLogger(__name__)

_VOYAGE_MAX_TEXT = 2000  # conservative character limit per text


def _client():
    import voyageai

    return voyageai.Client(api_key=settings.VOYAGE_API_KEY)


async def embed_patterns(
    session: AsyncSession,
    creator_id: uuid.UUID,
    patterns: dict,
) -> None:
    """
    Embed top/bottom video title+hook pairs and store as 'pattern' kind embeddings.
    Silently skips if VOYAGE_API_KEY is not configured.
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

    result = _client().embed(texts, model="voyage-3.5", input_type="document")

    for i, embedding in enumerate(result.embeddings):
        session.add(
            DnaEmbedding(
                creator_id=creator_id,
                kind=DnaEmbeddingKind.pattern,
                embedding=embedding,
                ref_jsonb=refs[i],
            )
        )
    await session.commit()
    logger.info("Stored %d DNA pattern embeddings for creator %s", len(texts), creator_id)


async def embed_brief(
    session: AsyncSession,
    creator_id: uuid.UUID,
    brief_text: str,
) -> None:
    """Embed the creator brief text and store as a 'hook' kind embedding."""
    if not settings.VOYAGE_API_KEY:
        return

    result = _client().embed(
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
    await session.commit()
    logger.info("Stored DNA brief embedding for creator %s", creator_id)
