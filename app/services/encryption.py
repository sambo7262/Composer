from __future__ import annotations

import os

from cryptography.fernet import Fernet

from app import config

# Module-level singleton
_encryptor: CredentialEncryptor | None = None


def get_or_create_key(key_path: str) -> bytes:
    """Load encryption key from file, or generate one on first run."""
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key)
    os.chmod(key_path, 0o600)
    return key


class CredentialEncryptor:
    """Fernet-based credential encryption and decryption."""

    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        """Encrypt a plaintext string, returning a URL-safe base64 token."""
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Decrypt a Fernet token back to plaintext."""
        return self._fernet.decrypt(token.encode()).decode()


def get_encryptor() -> CredentialEncryptor:
    """Return a singleton CredentialEncryptor, creating the key on first call."""
    global _encryptor
    if _encryptor is None:
        key = get_or_create_key(config.ENCRYPTION_KEY_PATH)
        _encryptor = CredentialEncryptor(key)
    return _encryptor
