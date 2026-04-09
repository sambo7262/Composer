from __future__ import annotations

from app.models.settings import ServiceConfigResponse
from app.services.settings_service import (
    get_all_settings,
    get_decrypted_credential,
    get_setting,
    is_service_configured,
    save_setting,
)


def test_save_setting_stores_encrypted_credential(test_db):
    """save_setting stores an encrypted credential in the database."""
    result = save_setting(test_db, "plex", "http://plex:32400", "my-token")

    assert result.service_name == "plex"
    assert result.encrypted_credential != "my-token"
    assert result.is_configured is True
    assert result.updated_at is not None


def test_get_setting_returns_masked_response(test_db):
    """get_setting returns ServiceConfigResponse with credential_set=True but no raw credential."""
    save_setting(test_db, "plex", "http://plex:32400", "my-token")

    response = get_setting(test_db, "plex")

    assert response is not None
    assert isinstance(response, ServiceConfigResponse)
    assert response.credential_set is True
    assert response.service_name == "plex"
    assert response.url == "http://plex:32400"
    # Verify the response model does NOT have an encrypted_credential field
    assert not hasattr(response, "encrypted_credential")


def test_get_setting_nonexistent_returns_none(test_db):
    """get_setting for non-existent service returns None."""
    response = get_setting(test_db, "nonexistent")
    assert response is None


def test_save_setting_upserts(test_db):
    """save_setting with same service_name updates rather than duplicates."""
    save_setting(test_db, "plex", "http://old:32400", "old-token")
    save_setting(test_db, "plex", "http://new:32400", "new-token")

    response = get_setting(test_db, "plex")
    assert response is not None
    assert response.url == "http://new:32400"

    # Verify only one record exists
    all_settings = get_all_settings(test_db)
    plex_settings = [s for s in all_settings if s.service_name == "plex"]
    assert len(plex_settings) == 1


def test_get_decrypted_credential(test_db):
    """get_decrypted_credential returns the original plaintext credential."""
    save_setting(test_db, "plex", "http://plex:32400", "my-secret-token")

    decrypted = get_decrypted_credential(test_db, "plex")
    assert decrypted == "my-secret-token"


def test_get_decrypted_credential_nonexistent(test_db):
    """get_decrypted_credential returns None for non-existent service."""
    result = get_decrypted_credential(test_db, "nonexistent")
    assert result is None


def test_get_all_settings(test_db):
    """get_all_settings returns all configured services."""
    save_setting(test_db, "plex", "http://plex:32400", "token1")
    save_setting(test_db, "ollama", "http://ollama:11434", "ollama")

    all_settings = get_all_settings(test_db)
    assert len(all_settings) == 2
    names = {s.service_name for s in all_settings}
    assert names == {"plex", "ollama"}


def test_is_service_configured_true(test_db):
    """is_service_configured returns True for a configured service."""
    save_setting(test_db, "plex", "http://plex:32400", "token")
    assert is_service_configured(test_db, "plex") is True


def test_is_service_configured_false(test_db):
    """is_service_configured returns False for an unconfigured service."""
    assert is_service_configured(test_db, "plex") is False


def test_save_setting_with_extra_config(test_db):
    """save_setting stores extra_config as JSON."""
    save_setting(
        test_db, "plex", "http://plex:32400", "token",
        extra_config={"library_id": "1"}
    )
    response = get_setting(test_db, "plex")
    assert response is not None
    assert response.extra_config == {"library_id": "1"}
