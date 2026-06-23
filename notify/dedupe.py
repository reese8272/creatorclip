"""
Idempotency-key helper for the notification delivery ledger (Issue 243).

``make_dedupe_key(creator_id, event_type, entity_id)`` produces a deterministic
SHA-256 hex string that serves as the UNIQUE key in ``notification_deliveries``
and as the ``Idempotency-Key`` header sent to Resend — providing two independent
layers of deduplication for Celery's at-least-once delivery model.

Key properties:
- Deterministic: same inputs always produce the same output.
- Collision-resistant: SHA-256 (64 hex chars) is within Resend's 256-char limit.
- No PII: the key contains IDs and event-type strings, never email or name.
- Safe for URL inclusion: hex encoding uses only [0-9a-f], no URL-special chars.
"""

import hashlib
import uuid


def make_dedupe_key(
    creator_id: uuid.UUID,
    event_type: str,
    entity_id: str,
) -> str:
    """Return a deterministic SHA-256 hex string for the (creator, event, entity) triple.

    The key is used as:
    - ``notification_deliveries.dedupe_key`` UNIQUE index — the database rejects a
      duplicate INSERT with ``IntegrityError``, making the send task idempotent.
    - The ``Idempotency-Key`` HTTP header passed to Resend — the provider's own
      24-hour dedup window provides a second independent layer of protection.

    Args:
        creator_id: The creator's UUID. Converted to canonical string form before
                    hashing so UUID(x) == uuid.UUID(str(x)) always yields the same key.
        event_type: A short stable identifier for the notification class, e.g.
                    ``"clips_ready"``, ``"dna_built"``, ``"trial_ending"``.
        entity_id: The primary entity driving this notification (e.g. video_id for
                   clips_ready, creator_id string for trial_ending). Use a stable
                   string that does not change between retries. For events with no
                   natural entity, pass the ISO-date string (``"2026-01-01"``) so
                   one email fires per creator per day rather than per retry.

    Returns:
        A 64-character lowercase hex string (SHA-256 digest). Always ≤ Resend's
        256-character idempotency-key limit.

    Example::

        key = make_dedupe_key(creator.id, "clips_ready", str(video_id))
        # → "a3f9b1c2..." (64 hex chars)
    """
    raw = f"{creator_id}:{event_type}:{entity_id}"
    return hashlib.sha256(raw.encode()).hexdigest()
