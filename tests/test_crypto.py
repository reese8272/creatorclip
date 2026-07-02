"""Unit tests for Fernet encryption helpers — no DB needed."""

import pytest
from cryptography.fernet import Fernet

from crypto import TokenDecryptError, _fernet, decrypt, encrypt, generate_key


@pytest.fixture(autouse=True)
def _clear_fernet_cache():
    """_fernet() is an lru_cache singleton (Issue 352) — tests here mutate the
    live settings keys, so the cache must be cleared around every test to keep
    key changes visible and to avoid leaking a temp key into other modules."""
    _fernet.cache_clear()
    yield
    _fernet.cache_clear()


# ── Config / key-shape boundary tests (Issue 340a) ───────────────────────────


def test_config_requires_token_encryption_key():
    """TOKEN_ENCRYPTION_KEY is declared as a bare `str` with no default —
    pydantic-settings raises ValidationError at startup if it is unset.
    This test pins that the field stays required so a future refactor that
    adds a default (e.g. '' or None) is caught. (Issue 340a)"""
    from pydantic_core import PydanticUndefined

    from config import Settings

    field = Settings.model_fields.get("TOKEN_ENCRYPTION_KEY")
    assert field is not None, "TOKEN_ENCRYPTION_KEY must be declared in Settings"
    assert field.default is PydanticUndefined, (
        "TOKEN_ENCRYPTION_KEY must have NO default — pydantic fails fast if unset"
    )


def test_malformed_fernet_key_raises_value_error(monkeypatch):
    """A TOKEN_ENCRYPTION_KEY that is not valid Fernet material raises ValueError
    from Fernet() inside _fernet(). This is a CONFIGURATION error, not a data
    error — it correctly surfaces as ValueError (not TokenDecryptError). Pinning
    this boundary so any future 'wrap everything in TokenDecryptError' PR is a
    deliberate, discussed change. (Issue 340a)"""
    from config import settings

    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "not_a_valid_fernet_key")
    with pytest.raises(ValueError):
        encrypt("anything")


def test_previous_key_empty_string_treated_as_no_previous_key(monkeypatch):
    """TOKEN_ENCRYPTION_KEY_PREVIOUS='' is falsy — the truthiness check
    `if settings.TOKEN_ENCRYPTION_KEY_PREVIOUS:` skips it, treating '' identically
    to None. Tokens encrypted with only the primary key must decrypt correctly
    in both states. (Issue 340a)"""
    from config import settings

    key_primary = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key_primary)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY_PREVIOUS", "")  # falsy

    ciphertext = encrypt("edge_case_data")
    assert decrypt(ciphertext) == "edge_case_data"

    # Verify None produces identical behaviour (the '' ≡ None equivalence)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY_PREVIOUS", None)
    _fernet.cache_clear()
    assert decrypt(ciphertext) == "edge_case_data"


def test_encrypt_decrypt_roundtrip():
    plaintext = "ya29.some_real_looking_oauth_token"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_encrypt_produces_different_ciphertext_each_time():
    token = "same_token"
    # Fernet uses a random IV — two encryptions of the same value differ
    assert encrypt(token) != encrypt(token)


def test_generate_key_is_valid_fernet_key():
    key = generate_key()
    # Will raise if key is malformed
    f = Fernet(key.encode())
    assert f.decrypt(f.encrypt(b"test")) == b"test"


def test_encrypt_empty_string():
    assert decrypt(encrypt("")) == ""


def test_decrypt_rejects_invalid_ciphertext_raises_token_decrypt_error():
    """Garbage ciphertext must raise TokenDecryptError, not raw InvalidToken."""
    with pytest.raises(TokenDecryptError):
        decrypt("not_valid_fernet_ciphertext")


def test_token_decrypt_error_message_does_not_contain_ciphertext():
    """TokenDecryptError messages must be safe to log — no ciphertext leakage."""
    ciphertext = "super_secret_ciphertext_must_not_appear_in_error"
    with pytest.raises(TokenDecryptError) as exc_info:
        decrypt(ciphertext)
    assert ciphertext not in str(exc_info.value)


def test_decrypt_with_previous_key(monkeypatch):
    """Encrypt under key A, then rotate: primary=B, previous=A — decrypt must succeed.

    `_fernet()` is an lru_cache singleton (Issue 352), so a runtime key rotation
    must call `_fernet.cache_clear()` for the new keys to take effect — mirroring
    the process restart a real rotation performs.
    """
    from config import settings

    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    # Encrypt under key A (simulate a token stored before rotation)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key_a)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY_PREVIOUS", "")
    ciphertext = encrypt("rotate_me")

    # Rotate: primary = B, previous = A
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key_b)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY_PREVIOUS", key_a)
    _fernet.cache_clear()
    assert decrypt(ciphertext) == "rotate_me"


def test_encrypt_after_rotation_uses_primary_key(monkeypatch):
    """After rotation, new encryptions must use the primary key (not the previous key)."""
    from config import settings

    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key_b)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY_PREVIOUS", key_a)

    ciphertext = encrypt("fresh_token")

    # Ciphertext is readable with key B alone (no previous key needed)
    fernet_b = Fernet(key_b.encode())
    assert fernet_b.decrypt(ciphertext.encode()).decode() == "fresh_token"


def test_decrypt_with_wrong_key_raises_token_decrypt_error(monkeypatch):
    """A ciphertext produced by key A must not be decryptable under key B alone."""
    from config import settings

    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key_a)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY_PREVIOUS", "")
    ciphertext = encrypt("secret")

    # Switch to key B with no previous key configured
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key_b)
    _fernet.cache_clear()

    with pytest.raises(TokenDecryptError):
        decrypt(ciphertext)


# ── Fernet singleton behaviour (Issue 352 Batch A) ────────────────────────────


def test_fernet_is_cached_singleton():
    """encrypt()/decrypt() must reuse ONE MultiFernet instance per process."""
    assert _fernet() is _fernet()


def test_fernet_cache_clear_rebuilds_instance():
    """cache_clear() is the documented hook for key rotation in tests."""
    first = _fernet()
    _fernet.cache_clear()
    assert _fernet() is not first
