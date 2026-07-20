"""API key authentication for the OBS companion app (Issue 95).

The companion app uploads clips to /clips/ingest with::

    Authorization: Bearer <raw_api_key>

We never store the raw key — only a SHA-256 hex hash. The raw key is
shown to the user ONCE at creation time and is gone from our server
immediately after. A short prefix is kept in the row for the
management UI so the user can identify a key without copying the
secret.

Threat model: raw keys are 32 url-safe characters (~192 bits of
entropy) which is well beyond brute-force feasibility for SHA-256,
so we do not salt — salting buys nothing against keys that already
have full entropy and would block the constant-time lookup pattern.

Use ``get_current_creator_via_api_key`` as a FastAPI dependency on
endpoints that must accept the bearer header instead of the session
cookie. Per-creator isolation works the same way as the session
path: ``session.info["creator_id"]`` is set so the after_begin RLS
listener (Issue 79) gates every downstream query.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_session
from models import Creator, CreatorApiKey

# ── Constants ───────────────────────────────────────────────────────────────

_PREFIX = "ack_"  # "AutoClip Key" — easy to grep in user logs/keyrings
_RAW_LEN = 32  # url-safe chars after the prefix
_PREFIX_DISPLAY_LEN = 8  # how many post-prefix chars to keep for UI display

# last_used_at means "roughly when last used" (management-UI freshness), so an
# UPDATE + commit on every authenticated request is pure write amplification.
# Only stamp when the current value is missing or older than this interval.
_LAST_USED_STAMP_INTERVAL = timedelta(minutes=5)


# ── Generation + hashing ────────────────────────────────────────────────────


def generate_api_key() -> str:
    """Return a fresh raw API key. Shown to the user ONCE — never persisted."""
    return _PREFIX + secrets.token_urlsafe(_RAW_LEN)[:_RAW_LEN]


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex of the raw key. Stable across processes; constant-time
    comparison is not needed here because lookups happen by hash on an
    indexed column."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def should_stamp_last_used(last_used_at: datetime | None, now: datetime) -> bool:
    """True when ``last_used_at`` should be rewritten: never stamped yet, or
    stale by at least ``_LAST_USED_STAMP_INTERVAL``. Keeps the column's
    'roughly when last used' semantics without a write per request."""
    return last_used_at is None or (now - last_used_at) >= _LAST_USED_STAMP_INTERVAL


def display_prefix(raw_key: str) -> str:
    """Return the first ``_PREFIX_DISPLAY_LEN`` chars after the ``ack_``
    prefix. Stored in the row so the management UI can render
    ``ack_a8b2k3...`` without ever needing the raw key again."""
    if not raw_key.startswith(_PREFIX):
        raise ValueError("API key missing canonical prefix")
    return raw_key[len(_PREFIX) : len(_PREFIX) + _PREFIX_DISPLAY_LEN]


# ── FastAPI dependency ──────────────────────────────────────────────────────


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth[len("bearer ") :].strip() or None


async def get_current_creator_via_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Creator:
    """Resolve the Creator owner of the bearer-presented API key.

    Raises 401 if the header is missing, malformed, refers to a hash
    we don't know, or the matching row has been revoked. Updates
    ``last_used_at`` so the management UI can show key freshness —
    throttled to once per ``_LAST_USED_STAMP_INTERVAL`` to avoid an
    UPDATE per request (Issue 352).
    Sets ``session.info["creator_id"]`` so RLS gates downstream queries
    (Issue 79).
    """
    raw_key = _extract_bearer(request)
    if not raw_key or not raw_key.startswith(_PREFIX):
        raise HTTPException(status_code=401, detail="Missing or malformed API key")

    key_hash = hash_api_key(raw_key)
    row = (
        await session.execute(
            select(CreatorApiKey).where(
                CreatorApiKey.key_hash == key_hash,
                CreatorApiKey.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    creator = await session.get(Creator, row.creator_id)
    if creator is None:
        # FK CASCADE means this is structurally impossible; raise 401
        # rather than 500 to avoid leaking schema state.
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    now = datetime.now(UTC)
    if should_stamp_last_used(row.last_used_at, now):
        row.last_used_at = now
        await session.commit()

    session.info["creator_id"] = creator.id
    # Issue 358 (mirrors auth.py's Issue 344 fix): the key lookup above already
    # auto-began this request's transaction, so `after_begin` fired before
    # `session.info["creator_id"]` was set and emitted no GUC. On the no-stamp
    # path (Issue 352 throttle) there is no intervening commit to start a fresh
    # transaction, so downstream tenant queries would run with `app.creator_id`
    # unset — enforced RLS then returns zero rows and check_positive_balance
    # falsely 402s funded creators. Set the GUC on the live transaction now;
    # `is_local=true` clears it at commit, matching the listener's semantics.
    await session.execute(
        text("SELECT set_config('app.creator_id', :cid, true)"),
        {"cid": str(creator.id)},
    )
    # Stash on request.state so creator_key() in limiter.py can read the
    # already-resolved identity without re-decoding the bearer token. (Issue 104)
    request.state.creator_id = creator.id
    return creator
