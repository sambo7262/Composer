from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with lifespan."""
    from app.main import app

    with TestClient(app) as c:
        yield c


class TestPlexTestEndpoint:
    """Test POST /api/settings/plex/test."""

    @patch("app.routers.api_settings.test_plex_connection")
    def test_success_returns_connected_html(self, mock_test, client):
        """POST plex/test with successful mock returns 200 with 'Connected' in response."""
        mock_test.return_value = {
            "success": True,
            "server_name": "My Plex",
            "libraries": [{"key": "1", "title": "Music"}],
        }

        response = client.post(
            "/api/settings/plex/test",
            data={"url": "http://plex:32400", "token": "test-token"},
        )

        assert response.status_code == 200
        assert "Connected" in response.text

    @patch("app.routers.api_settings.test_plex_connection")
    def test_failure_returns_error_html(self, mock_test, client):
        """POST plex/test with failing mock returns error message in HTML."""
        mock_test.return_value = {
            "success": False,
            "error": "Could not connect. Check the URL and credentials, then try again.",
        }

        response = client.post(
            "/api/settings/plex/test",
            data={"url": "http://plex:32400", "token": "bad-token"},
        )

        assert response.status_code == 200
        assert "Could not connect" in response.text


class TestPlexSaveEndpoint:
    """Test POST /api/settings/plex/save."""

    @patch("app.routers.api_settings.test_plex_connection")
    def test_save_persists_and_returns_masked_state(self, mock_test, client):
        """POST plex/save persists the setting and returns masked credential HTML."""
        mock_test.return_value = {
            "success": True,
            "server_name": "My Plex",
            "libraries": [{"key": "1", "title": "Music"}],
        }

        response = client.post(
            "/api/settings/plex/save",
            data={
                "url": "http://plex:32400",
                "token": "secret-token",
                "library_id": "1",
            },
        )

        assert response.status_code == 200
        # Should show masked credential, not the raw token
        assert "secret-token" not in response.text
        assert "http://plex:32400" in response.text

    @patch("app.routers.api_settings.test_plex_connection")
    def test_save_resolves_library_name_server_side(self, mock_test, client):
        """POST plex/save no longer requires library_name -- resolves from library_id."""
        mock_test.return_value = {
            "success": True,
            "server_name": "My Plex",
            "libraries": [
                {"key": "1", "title": "Music"},
                {"key": "2", "title": "Audiobooks"},
            ],
        }

        # No library_name in form data -- server resolves it
        response = client.post(
            "/api/settings/plex/save",
            data={
                "url": "http://plex:32400",
                "token": "secret-token",
                "library_id": "2",
            },
        )

        assert response.status_code == 200

    @patch("app.routers.api_settings.test_plex_connection")
    def test_save_no_library_name_form_field_required(self, mock_test, client):
        """Verify library_name is NOT a required form field (D-12 bug fix)."""
        mock_test.return_value = {
            "success": True,
            "server_name": "My Plex",
            "libraries": [{"key": "1", "title": "Music"}],
        }

        # Explicitly do NOT send library_name
        response = client.post(
            "/api/settings/plex/save",
            data={
                "url": "http://plex:32400",
                "token": "secret-token",
                "library_id": "1",
            },
        )

        # Should succeed without library_name
        assert response.status_code == 200


class TestSettingsStatusEndpoint:
    """Test GET /api/settings."""

    def test_returns_all_service_statuses(self, client):
        """GET /api/settings returns JSON list with no raw credentials."""
        response = client.get("/api/settings")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Verify no credential values in response
        for item in data:
            assert "encrypted_credential" not in item
            assert "credential" not in item or item.get("credential") is None


class TestPlexTestErrorHandling:
    """Test error handling for connection test endpoints."""

    @patch("app.routers.api_settings.test_plex_connection")
    def test_plex_test_error_shows_error_html(self, mock_test, client):
        """POST plex/test with error returns error message in HTML."""
        mock_test.return_value = {
            "success": False,
            "error": "Authentication failed. Double-check your token or API key.",
        }

        response = client.post(
            "/api/settings/plex/test",
            data={"url": "http://plex:32400", "token": "wrong"},
        )

        assert response.status_code == 200
        assert "Authentication failed" in response.text
