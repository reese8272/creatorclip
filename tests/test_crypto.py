"""Unit tests for Fernet encryption helpers — no DB needed."""

import os

import pytest
from cryptography.fernet import Fernet

from crypto import TokenDecryptError, decrypt, encrypt, generate_key


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
    """Encrypt under key A, then rotate: primary=B, previous=A — decrypt must succeed."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    # Encrypt under key A (simulate a token stored before rotation)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key_a)
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY_PREVIOUS", raising=False)

    # Reload settings so the new env vars are picked up
    import importlib

    import config as config_module
    import crypto as crypto_module

    importlib.reload(config_module)
    importlib.reload(crypto_module)

    ciphertext = crypto_module.encrypt("rotate_me")

    # Now rotate: primary = B, previous = A
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key_b)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY_PREVIOUS", key_a)
    importlib.reload(config_module)
    importlib.reload(crypto_module)

    result = crypto_module.decrypt(ciphertext)
    assert result == "rotate_me"


def test_encrypt_after_rotation_uses_primary_key(monkeypatch):
    """After rotation, new encryptions must use the primary key (not the previous key)."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    import importlib

    import config as config_module
    import crypto as crypto_module

    # Rotation in progress: primary=B, previous=A
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key_b)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY_PREVIOUS", key_a)
    importlib.reload(config_module)
    importlib.reload(crypto_module)

    ciphertext = crypto_module.encrypt("fresh_token")

    # Verify the ciphertext is readable with key B alone (no previous key needed)
    fernet_b = Fernet(key_b.encode())
    plaintext = fernet_b.decrypt(ciphertext.encode()).decode()
    assert plaintext == "fresh_token"


def test_decrypt_with_wrong_key_raises_token_decrypt_error(monkeypatch):
    """A ciphertext produced by key A must not be decryptable under key B alone."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    import importlib

    import config as config_module
    import crypto as crypto_module

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key_a)
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY_PREVIOUS", raising=False)
    importlib.reload(config_module)
    importlib.reload(crypto_module)

    ciphertext = crypto_module.encrypt("secret")

    # Switch to key B with no previous key configured
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key_b)
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY_PREVIOUS", raising=False)
    importlib.reload(config_module)
    importlib.reload(crypto_module)

    with pytest.raises(TokenDecryptError):
        crypto_module.decrypt(ciphertext)
