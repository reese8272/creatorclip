from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from config import settings


class TokenDecryptError(Exception):
    """Raised when a stored token cannot be decrypted with any current key.

    The exception message is safe to log — it never includes ciphertext or key material.
    """


def _fernet() -> MultiFernet:
    """Build a MultiFernet from the primary key and (optionally) the previous key.

    MultiFernet.encrypt() always uses the first (primary) key.
    MultiFernet.decrypt() tries each key in order, so tokens encrypted under the
    previous key remain readable during a zero-downtime rotation window.
    """
    primary = Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())
    if settings.TOKEN_ENCRYPTION_KEY_PREVIOUS:
        previous = Fernet(settings.TOKEN_ENCRYPTION_KEY_PREVIOUS.encode())
        return MultiFernet([primary, previous])
    return MultiFernet([primary])


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string with the primary Fernet key."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted token.

    Raises:
        TokenDecryptError: If the ciphertext cannot be decrypted by any configured key.
    """
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenDecryptError("Token could not be decrypted — key mismatch or corrupt data") from exc


def generate_key() -> str:
    """Generate a new Fernet key. Run once to populate TOKEN_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode()
