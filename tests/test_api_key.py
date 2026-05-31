"""Unit tests for the api_key module (Issue 95).

Covers key generation, hashing, prefix extraction, and the bearer-header
parser. Integration tests for the full dependency + endpoint surface live
in tests/test_api_keys_integration.py and tests/test_clips_ingest_integration.py.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from api_key import (
    _PREFIX,
    _PREFIX_DISPLAY_LEN,
    _extract_bearer,
    display_prefix,
    generate_api_key,
    hash_api_key,
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
