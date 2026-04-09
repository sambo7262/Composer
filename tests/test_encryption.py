from __future__ import annotations

import os

from app.services.encryption import CredentialEncryptor, get_or_create_key


def test_key_generated_and_saved(tmp_data_dir):
    """Fernet key is generated and saved to disk on first call."""
    key_path = os.path.join(str(tmp_data_dir), ".encryption.key")
    assert not os.path.exists(key_path)

    key = get_or_create_key(key_path)

    assert os.path.exists(key_path)
    assert len(key) > 0


def test_key_reloaded_on_subsequent_calls(tmp_data_dir):
    """Key is reloaded from disk on subsequent calls."""
    key_path = os.path.join(str(tmp_data_dir), ".encryption.key")

    key1 = get_or_create_key(key_path)
    key2 = get_or_create_key(key_path)

    assert key1 == key2


def test_key_file_has_restrictive_permissions(tmp_data_dir):
    """Key file has chmod 0o600 permissions."""
    key_path = os.path.join(str(tmp_data_dir), ".encryption.key")
    get_or_create_key(key_path)

    mode = os.stat(key_path).st_mode & 0o777
    assert mode == 0o600


def test_encrypt_returns_different_string(test_encryptor):
    """encrypt('secret') returns a string != 'secret'."""
    encrypted = test_encryptor.encrypt("secret")
    assert encrypted != "secret"
    assert isinstance(encrypted, str)


def test_encrypt_decrypt_roundtrip(test_encryptor):
    """decrypt(encrypt('secret')) == 'secret'."""
    original = "my-super-secret-token"
    encrypted = test_encryptor.encrypt(original)
    decrypted = test_encryptor.decrypt(encrypted)

    assert decrypted == original


def test_different_plaintexts_produce_different_tokens(test_encryptor):
    """Different inputs produce different encrypted outputs."""
    token1 = test_encryptor.encrypt("secret1")
    token2 = test_encryptor.encrypt("secret2")
    assert token1 != token2
