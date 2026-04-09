"""Tests for the sync API endpoints (POST /api/sync/start, GET /api/sync/status)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.services.sync_service import SyncStateEnum, SyncStatus


@pytest.fixture
def client(test_engine):
    """Create a test client with fresh database."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def configured_plex(test_engine):
    """Set up Plex as configured in the database."""
    from app.models.settings import ServiceConfig
    from app.services.encryption import get_encryptor

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        encryptor = get_encryptor()
        config = ServiceConfig(
            service_name="plex",
            url="http://plex:32400",
            encrypted_credential=encryptor.encrypt("test-token"),
            extra_config='{"library_id": "1", "library_name": "Music"}',
        )
        session.add(config)
        session.commit()


class TestStartSync:
    """Tests for POST /api/sync/start."""

    def test_start_sync_returns_200_with_banner(self, client, configured_plex):
        """Starting a sync returns 200 with sync banner HTML."""
        with patch("app.routers.api_sync.run_sync") as mock_run:
            with patch(
                "app.routers.api_sync.get_sync_status",
                return_value=SyncStatus(state=SyncStateEnum.IDLE),
            ):
                response = client.post("/api/sync/start")

        assert response.status_code == 200
        assert "sync-banner" in response.text

    def test_start_sync_when_already_running(self, client, configured_plex):
        """If sync is already running, return current progress (not an error)."""
        running_status = SyncStatus(
            state=SyncStateEnum.RUNNING,
            total_tracks=100,
            synced_tracks=42,
        )
        with patch(
            "app.routers.api_sync.get_sync_status",
            return_value=running_status,
        ):
            response = client.post("/api/sync/start")

        assert response.status_code == 200
        assert "sync-banner" in response.text
        assert "Syncing..." in response.text

    def test_start_sync_plex_not_configured(self, client):
        """If Plex is not configured, return error banner."""
        with patch(
            "app.routers.api_sync.get_sync_status",
            return_value=SyncStatus(state=SyncStateEnum.IDLE),
        ):
            response = client.post("/api/sync/start")

        assert response.status_code == 200
        assert "sync-banner" in response.text
        assert "not configured" in response.text.lower()

    def test_start_sync_launches_background_task(self, client, configured_plex):
        """Sync start should launch run_sync as a background task."""
        with patch("app.routers.api_sync.asyncio") as mock_asyncio:
            with patch(
                "app.routers.api_sync.get_sync_status",
                return_value=SyncStatus(state=SyncStateEnum.IDLE),
            ):
                response = client.post("/api/sync/start")

        assert response.status_code == 200
        mock_asyncio.create_task.assert_called_once()


class TestSyncStatus:
    """Tests for GET /api/sync/status."""

    def test_status_returns_current_state(self, client, configured_plex):
        """GET /api/sync/status returns current sync status as HTML partial."""
        with patch(
            "app.routers.api_sync.get_sync_status",
            return_value=SyncStatus(
                state=SyncStateEnum.COMPLETED,
                total_tracks=500,
                synced_tracks=500,
                last_synced="2026-04-09T12:00:00Z",
            ),
        ):
            response = client.get("/api/sync/status")

        assert response.status_code == 200
        assert "sync-banner" in response.text
        assert "Sync Now" in response.text

    def test_status_running_shows_progress(self, client, configured_plex):
        """When sync is running, status shows progress bar with polling."""
        with patch(
            "app.routers.api_sync.get_sync_status",
            return_value=SyncStatus(
                state=SyncStateEnum.RUNNING,
                total_tracks=1000,
                synced_tracks=250,
            ),
        ):
            response = client.get("/api/sync/status")

        assert response.status_code == 200
        assert "every 2s" in response.text
        assert "Syncing..." in response.text

    def test_status_idle_no_history(self, client):
        """When idle with no sync history, show prompt to sync."""
        with patch(
            "app.routers.api_sync.get_sync_status",
            return_value=SyncStatus(state=SyncStateEnum.IDLE),
        ):
            response = client.get("/api/sync/status")

        assert response.status_code == 200
        assert "sync-banner" in response.text
        assert "No sync has been performed yet" in response.text

    def test_status_failed_shows_error(self, client, configured_plex):
        """When sync failed, show error with retry button."""
        with patch(
            "app.routers.api_sync.get_sync_status",
            return_value=SyncStatus(
                state=SyncStateEnum.FAILED,
                error="Connection refused",
            ),
        ):
            response = client.get("/api/sync/status")

        assert response.status_code == 200
        assert "sync-banner" in response.text
        assert "Retry" in response.text
