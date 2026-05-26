"""Unit tests for Fernet encryption helpers — no DB needed."""

from crypto import decrypt, encrypt, generate_key


def test_encrypt_decrypt_roundtrip():
    plaintext = "ya29.some_real_looking_oauth_token"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_encrypt_produces_different_ciphertext_each_time():
    token = "same_token"
    # Fernet uses a random IV — two encryptions of the same value differ
    assert encrypt(token) != encrypt(token)


def test_generate_key_is_valid_fernet_key():
    from cryptography.fernet import Fernet

    key = generate_key()
    # Will raise if key is malformed
    f = Fernet(key.encode())
    assert f.decrypt(f.encrypt(b"test")) == b"test"


def test_encrypt_empty_string():
    assert decrypt(encrypt("")) == ""


def test_decrypt_rejects_invalid_ciphertext():
    import pytest

    with pytest.raises(Exception, match=".*"):
        decrypt("not_valid_fernet_ciphertext")
