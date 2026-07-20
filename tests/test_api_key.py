"""Unit tests for the api_key module (Issue 95).

Covers key generation, hashing, prefix extraction, and the bearer-header
parser. Integration tests for the full dependency + endpoint surface live
in tests/test_api_keys_integration.py and tests/test_clips_ingest_integration.py.
"""

from __future__ import annotations

import hashlib
import re
import uuid

import pytest

from api_key import (
    _LAST_USED_STAMP_INTERVAL,
    _PREFIX,
    _PREFIX_DISPLAY_LEN,
    _extract_bearer,
    display_prefix,
    generate_api_key,
    hash_api_key,
    should_stamp_last_used,
)

# ── Key generation ─────────────────────────────────────────────────────────


def test_generate_api_key_has_canonical_prefix():
    key = generate_api_key()
    assert key.startswith(_PREFIX), (
        "Every generated key must start with the canonical 'ack_' prefix "
        "so users (and ops) can grep-identify AutoClip keys in logs/keyrings."
    )


def test_generate_api_key_url_safe_characters_only():
    key = generate_api_key()
    body = key[len(_PREFIX) :]
    # secrets.token_urlsafe produces [A-Za-z0-9_-]; pin it explicitly so a
    # future swap to base32 / hex couldn't silently change the wire format.
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", body), (
        f"Key body must be URL-safe (no padding, no '/'); got: {body!r}"
    )


def test_generate_api_key_unique_across_calls():
    """1000 keys, zero collisions — sanity check on the secrets module's
    underlying CSPRNG. A failure here would indicate something is very wrong."""
    keys = {generate_api_key() for _ in range(1000)}
    assert len(keys) == 1000, "secrets.token_urlsafe collided 1000 times — broken CSPRNG"


def test_generate_api_key_minimum_entropy():
    """Key body must be at least 32 chars (≈192 bits of entropy from
    secrets.token_urlsafe) so brute-force is infeasible against the
    SHA-256 hash."""
    body = generate_api_key()[len(_PREFIX) :]
    assert len(body) >= 32, (
        f"Key body must be at least 32 chars for sufficient entropy; got {len(body)}"
    )


# ── Hashing ────────────────────────────────────────────────────────────────


def test_hash_api_key_is_deterministic():
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_is_sha256_hex():
    key = generate_api_key()
    h = hash_api_key(key)
    # SHA-256 hex = 64 chars of [0-9a-f]
    assert len(h) == 64
    assert re.fullmatch(r"[0-9a-f]+", h), f"Hash must be lowercase hex; got: {h!r}"
    # And it actually matches what hashlib produces — defends against a
    # future "let me use bcrypt" PR that would silently break verification.
    expected = hashlib.sha256(key.encode("utf-8")).hexdigest()
    assert h == expected


def test_hash_api_key_changes_on_different_input():
    a = generate_api_key()
    b = generate_api_key()
    assert hash_api_key(a) != hash_api_key(b)


# ── Prefix extraction ──────────────────────────────────────────────────────


def test_display_prefix_returns_n_chars_after_canonical_prefix():
    key = generate_api_key()
    prefix = display_prefix(key)
    assert len(prefix) == _PREFIX_DISPLAY_LEN, (
        f"Display prefix must be exactly {_PREFIX_DISPLAY_LEN} chars; got {len(prefix)}"
    )
    # The prefix must match the slice of the raw key — used by the
    # management UI to render `ack_a8b2k3...`
    assert key.startswith(_PREFIX + prefix)


def test_display_prefix_rejects_non_canonical_key():
    """A key that doesn't carry our prefix is a programmer error, not a
    user error — raise ValueError loud rather than returning garbage."""
    with pytest.raises(ValueError, match="prefix"):
        display_prefix("not-our-key-format")


# ── Bearer header parser ───────────────────────────────────────────────────


class _FakeRequest:
    """Minimal stand-in for fastapi.Request supporting the .headers access
    the parser uses. Keeps unit tests free of FastAPI request construction."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_extract_bearer_missing_header_returns_none():
    assert _extract_bearer(_FakeRequest({})) is None


def test_extract_bearer_non_bearer_scheme_returns_none():
    """Basic auth / Digest / random garbage all return None — only Bearer
    is accepted. Mixed case is fine ('Bearer ', 'bearer ', 'BEARER ')."""
    for h in ("Basic abc", "Digest user=x", "garbage"):
        assert _extract_bearer(_FakeRequest({"authorization": h})) is None


def test_extract_bearer_returns_token_for_well_formed_header():
    key = generate_api_key()
    for prefix in ("Bearer", "bearer", "BEARER"):
        assert _extract_bearer(_FakeRequest({"authorization": f"{prefix} {key}"})) == key


def test_extract_bearer_strips_whitespace():
    key = generate_api_key()
    assert _extract_bearer(_FakeRequest({"authorization": f"Bearer  {key}  "})) == key


def test_extract_bearer_empty_token_returns_none():
    """Defends against `Authorization: Bearer ` (literal trailing space, no
    token) — the dependency must 401, not pass an empty-string key
    through to a hash lookup."""
    assert _extract_bearer(_FakeRequest({"authorization": "Bearer "})) is None
    assert _extract_bearer(_FakeRequest({"authorization": "Bearer    "})) is None


# ── last_used_at write throttle (Issue 352) ───────────────────────────────────


def test_should_stamp_last_used_when_never_stamped():
    from datetime import UTC, datetime

    assert should_stamp_last_used(None, datetime.now(UTC)) is True


def test_should_stamp_last_used_throttles_fresh_stamp():
    """A stamp fresher than the interval must NOT trigger another UPDATE —
    the whole point of the throttle is no write per request."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    assert should_stamp_last_used(now - _LAST_USED_STAMP_INTERVAL / 2, now) is False
    # Boundary: exactly one interval old → stale, stamp again.
    assert should_stamp_last_used(now - _LAST_USED_STAMP_INTERVAL, now) is True


# ── Hashing edge cases (Issue 340a) ───────────────────────────────────────────


def test_hash_api_key_unicode_input():
    """hash_api_key encodes via 'utf-8' — a key that contains multi-byte
    Unicode characters must produce a stable hex hash consistent with
    hashlib.sha256(raw.encode('utf-8')). (Issue 340a)"""
    # Contrived key with a non-ASCII char to exercise the encode("utf-8") path
    raw = _PREFIX + "cafétest12345678901234"  # é = U+00E9, 2 bytes in UTF-8
    h = hash_api_key(raw)
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert h == expected
    assert len(h) == 64


# ── Dependency: orphaned CreatorApiKey → 401 (Issue 340a) ─────────────────────


@pytest.mark.asyncio
async def test_get_current_creator_via_api_key_orphaned_row_returns_401():
    """A valid API key whose owning Creator was deleted must return 401, not 500.
    The FK ON DELETE CASCADE makes this structurally impossible in practice, but the
    explicit guard in get_current_creator_via_api_key exists for defence-in-depth
    and must be exercised. (Issue 340a)"""
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import HTTPException

    from api_key import get_current_creator_via_api_key
    from models import CreatorApiKey as CreatorApiKeyModel

    raw_key = generate_api_key()

    # Minimal stand-in that satisfies `request.headers.get(...)` and `.state`
    class _Req:
        headers = {"authorization": f"Bearer {raw_key}"}
        state = type("S", (), {})()

    # Session: CreatorApiKey row found, but Creator.get returns None (deleted)
    mock_row = MagicMock(spec=CreatorApiKeyModel)
    mock_row.creator_id = uuid.uuid4()
    mock_row.revoked_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.get = AsyncMock(return_value=None)  # Creator row gone

    with pytest.raises(HTTPException) as exc:
        await get_current_creator_via_api_key(_Req(), mock_session)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or revoked API key"


# ── Dependency: RLS GUC on the live transaction (Issue 358) ───────────────────


@pytest.mark.asyncio
async def test_get_current_creator_via_api_key_sets_guc_on_no_stamp_path():
    """Regression for Issue 358: the key lookup auto-begins the request
    transaction BEFORE ``session.info['creator_id']`` is set, so db.py's
    after_begin listener emits no GUC. On the no-stamp path (fresh
    last_used_at — the Issue 352 throttle) there is no mid-dependency commit
    to start a fresh transaction either, so the dependency itself must emit
    ``set_config('app.creator_id', ...)`` on the live transaction (mirroring
    auth.py's Issue 344 fix). Without it, enforced RLS returns zero tenant
    rows and check_positive_balance falsely 402s funded creators."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock

    from api_key import get_current_creator_via_api_key
    from models import Creator
    from models import CreatorApiKey as CreatorApiKeyModel

    raw_key = generate_api_key()

    class _Req:
        headers = {"authorization": f"Bearer {raw_key}"}
        state = type("S", (), {})()

    creator_id = uuid.uuid4()
    mock_row = MagicMock(spec=CreatorApiKeyModel)
    mock_row.creator_id = creator_id
    mock_row.revoked_at = None
    mock_row.last_used_at = datetime.now(UTC)  # fresh stamp → no-stamp path

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row

    mock_creator = MagicMock(spec=Creator)
    mock_creator.id = creator_id

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.get = AsyncMock(return_value=mock_creator)
    mock_session.info = {}

    resolved = await get_current_creator_via_api_key(_Req(), mock_session)

    assert resolved is mock_creator
    # Confirms this exercised the no-stamp path — no commit, so no fresh
    # transaction for the after_begin listener to inject the GUC into.
    mock_session.commit.assert_not_awaited()
    assert mock_session.info["creator_id"] == creator_id
    set_config_calls = [
        call
        for call in mock_session.execute.await_args_list
        if "set_config" in str(call.args[0]).lower()
        and "app.creator_id" in str(call.args[0]).lower()
    ]
    assert set_config_calls, (
        "dependency must emit set_config('app.creator_id', ...) on the live "
        "transaction — without it enforced RLS denies every tenant read"
    )
    assert set_config_calls[0].args[1] == {"cid": str(creator_id)}
