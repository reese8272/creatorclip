"""CRUD for the creator_identity table (Issue 83).

Append-only versioning: ``upsert_identity`` always creates a NEW row at
version max+1 and stamps ``superseded_at`` on the prior current row inside
one transaction. The partial unique index ``uq_one_current_identity_per_creator``
is the DB-level backstop — if two writes race, the second one's INSERT will
fail with IntegrityError, and the caller can retry (the standard recover-and-retry
pattern, same shape as ``dna/profile.py:confirm_draft``).

This module is the ONLY supported way to write rows in this table. Tests can
poke directly via the session for setup convenience.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import CreatorIdentity

logger = logging.getLogger(__name__)


async def get_current(session: AsyncSession, creator_id: uuid.UUID) -> CreatorIdentity | None:
    """Return the current (non-superseded) identity row for a creator, or None."""
    result = await session.execute(
        select(CreatorIdentity).where(
            CreatorIdentity.creator_id == creator_id,
            CreatorIdentity.superseded_at.is_(None),
        )
    )
    return result.scalars().first()


async def get_history(
    session: AsyncSession, creator_id: uuid.UUID, limit: int = 20
) -> list[CreatorIdentity]:
    """Return identity versions for a creator, newest first (capped at ``limit``)."""
    result = await session.execute(
        select(CreatorIdentity)
        .where(CreatorIdentity.creator_id == creator_id)
        .order_by(CreatorIdentity.version.desc())
        .limit(limit)
    )
    return list(result.scalars())


async def upsert_identity(
    session: AsyncSession,
    creator_id: uuid.UUID,
    *,
    niches: list[str],
    audience_summary: str,
    content_pillars: list[str] | None = None,
    tone_tags: list[str] | None = None,
    hard_nos: list[str] | None = None,
    mission: str | None = None,
    style_sample: str | None = None,
) -> CreatorIdentity:
    """Create a new identity version, superseding the current one (if any).

    Both the supersede UPDATE and the new INSERT run in a single transaction
    so the partial unique index never sees two concurrent "current" rows.

    Returns the freshly-committed new row.
    """
    now = datetime.now(UTC)

    # Lock the creator's identity rows FOR UPDATE so a concurrent upsert
    # serializes — without this, two writers could compute the same max+1
    # version and both insert successfully (the (creator_id, version) UNIQUE
    # would then refuse one, which is fine, but we'd waste an LLM-driven
    # supersede on the wrong row).
    rows = (
        (
            await session.execute(
                select(CreatorIdentity)
                .where(CreatorIdentity.creator_id == creator_id)
                .order_by(CreatorIdentity.version.desc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    max_version = rows[0].version if rows else 0
    current = next((r for r in rows if r.superseded_at is None), None)

    if current is not None:
        # Stamp the prior current row first — the partial unique index is
        # non-deferrable, so the INSERT below would fail if we left two
        # rows with superseded_at IS NULL even transiently.
        await session.execute(
            update(CreatorIdentity)
            .where(CreatorIdentity.id == current.id)
            .values(superseded_at=now)
        )
        await session.flush()

    new_row = CreatorIdentity(
        creator_id=creator_id,
        version=max_version + 1,
        niches=niches,
        audience_summary=audience_summary,
        content_pillars=content_pillars,
        tone_tags=tone_tags,
        hard_nos=hard_nos,
        mission=mission,
        style_sample=style_sample,
        created_at=now,
        superseded_at=None,
    )
    session.add(new_row)

    try:
        await session.commit()
    except IntegrityError as exc:
        # A concurrent upsert won the partial-unique race. Roll back and
        # re-fetch the current row — that's now the authoritative state.
        await session.rollback()
        logger.info(
            "identity upsert lost partial-unique race for creator %s (%s) — returning current",
            creator_id,
            exc.__class__.__name__,
        )
        existing = await get_current(session, creator_id)
        if existing is None:
            raise
        return existing

    await session.refresh(new_row)
    logger.info("identity v%d saved for creator %s", new_row.version, creator_id)
    return new_row


def format_for_prompt(identity: CreatorIdentity | None) -> str | None:
    """Render the identity as a compact Markdown block for an LLM system prompt.

    Returns ``None`` if no identity (caller should skip the block entirely
    rather than inject "(no identity)" which would hurt cache hit-rate by
    making the block volatile).
    """
    if identity is None:
        return None

    from youtube.categories import labels_for

    parts: list[str] = ["CREATOR-STATED IDENTITY (the creator's own words):"]
    if identity.niches:
        niche_labels = labels_for(identity.niches)
        if niche_labels:
            parts.append(f"- Niche(s): {', '.join(niche_labels)}")
    parts.append(f"- Audience: {identity.audience_summary}")
    if identity.content_pillars:
        parts.append(f"- Content pillars: {', '.join(identity.content_pillars)}")
    if identity.tone_tags:
        parts.append(f"- Tone / personality: {', '.join(identity.tone_tags)}")
    if identity.hard_nos:
        parts.append(f"- Will NOT do: {', '.join(identity.hard_nos)}")
    if identity.mission:
        parts.append(f"- Mission: {identity.mission}")
    if identity.style_sample:
        # Trim aggressively — the sample is mostly useful as tone-calibration
        # for the LLM, not as a corpus. 600 chars ≈ 100 words ≈ 1 paragraph.
        sample = identity.style_sample.strip()
        if len(sample) > 600:
            sample = sample[:600].rsplit(" ", 1)[0] + "…"
        parts.append(f"- Style sample: {sample}")
    return "\n".join(parts)


# Validation helpers used by the router's Pydantic layer ──────────────────────


_MAX_AUDIENCE_CHARS = 600
_MAX_MISSION_CHARS = 400
_MAX_STYLE_SAMPLE_CHARS = 4000
_MAX_LIST_ITEMS = 10
_MAX_LIST_ITEM_CHARS = 80


def validate_niches(niches: list[str]) -> list[str]:
    """Validate and normalize niche ids. Raises ValueError on invalid input."""
    from youtube.categories import NICHE_IDS

    if not isinstance(niches, list) or not niches:
        raise ValueError("at least one niche is required")
    if len(niches) > 3:
        raise ValueError("at most 3 niches allowed")
    unknown = [n for n in niches if n not in NICHE_IDS]
    if unknown:
        raise ValueError(f"unknown niche id(s): {unknown}")
    # Dedup, preserve order. `dict.fromkeys` is the idiomatic 3.7+ order-
    # preserving dedup; clearer than the set.add walrus trick that fooled mypy.
    return list(dict.fromkeys(niches))


def validate_text(value: str, *, max_chars: int, label: str) -> str:
    """Strip + length-check a free-text field."""
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{label} must not be empty")
    if len(value) > max_chars:
        raise ValueError(f"{label} must be {max_chars} characters or fewer")
    return value


def validate_optional_text(value: str | None, *, max_chars: int, label: str) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > max_chars:
        raise ValueError(f"{label} must be {max_chars} characters or fewer")
    return value


def validate_list(value: list[str] | None, *, label: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    cleaned = [v.strip() for v in value if isinstance(v, str) and v.strip()]
    if not cleaned:
        return None
    if len(cleaned) > _MAX_LIST_ITEMS:
        raise ValueError(f"{label} may have at most {_MAX_LIST_ITEMS} entries")
    for item in cleaned:
        if len(item) > _MAX_LIST_ITEM_CHARS:
            raise ValueError(f"{label} entries must be {_MAX_LIST_ITEM_CHARS} characters or fewer")
    # Dedup, preserve order (see validate_niches for why dict.fromkeys).
    return list(dict.fromkeys(cleaned))


# Re-export the limits so the router's Pydantic models share one source of truth.
MAX_AUDIENCE_CHARS = _MAX_AUDIENCE_CHARS
MAX_MISSION_CHARS = _MAX_MISSION_CHARS
MAX_STYLE_SAMPLE_CHARS = _MAX_STYLE_SAMPLE_CHARS


__all__ = [
    "MAX_AUDIENCE_CHARS",
    "MAX_MISSION_CHARS",
    "MAX_STYLE_SAMPLE_CHARS",
    "format_for_prompt",
    "get_current",
    "get_history",
    "upsert_identity",
    "validate_list",
    "validate_niches",
    "validate_optional_text",
    "validate_text",
]
