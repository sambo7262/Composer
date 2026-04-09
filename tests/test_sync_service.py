from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select

from app.models.track import SyncState, Track


@pytest.fixture
def sync_db(test_engine):
    """Create tables including Track and SyncState, yield session, drop after."""
    from app.models.settings import ServiceConfig  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


def _make_mock_setting(library_id="1"):
    """Create a mock ServiceConfigResponse for Plex."""
    setting = MagicMock()
    setting.url = "http://plex:32400"
    setting.extra_config = {"library_id": library_id}
    return setting


def _make_track_dict(key="1", title="Song", artist="Artist"):
    """Create a sample track dict matching plex_client output format."""
    return {
        "plex_rating_key": key,
        "title": title,
        "artist": artist,
        "album": "Album",
        "genre": "Rock",
        "year": 2020,
        "duration_ms": 240000,
        "added_at": "2024-01-15T12:00:00",
        "updated_at": "2024-01-15T12:00:00",
    }


def _run_async(coro):
    """Helper to run async coroutines in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _reset_sync_status():
    """Reset the module-level sync status to IDLE."""
    import app.services.sync_service as svc
    svc._sync_status = svc.SyncStatus()


class TestRunSync:
    """Tests for run_sync main entry point."""

    def setup_method(self):
        _reset_sync_status()

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_tracks_since")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    @patch("app.services.sync_service._update_sync_state_sync")
    @patch("app.services.sync_service._set_sync_started_sync")
    @patch("app.services.sync_service._get_last_sync_completed_sync")
    @patch("app.services.sync_service._upsert_tracks_sync")
    def test_full_sync_fetches_in_batches(
        self, mock_upsert, mock_last_sync, mock_set_started,
        mock_update_state, mock_setting, mock_credential,
        mock_tracks_since, mock_library_tracks,
    ):
        """run_sync fetches all tracks in batches of 200, upserting each batch."""
        mock_setting.return_value = _make_mock_setting()
        mock_credential.return_value = "test-token"
        mock_last_sync.return_value = None  # No previous sync -> full sync

        # Simulate 2 batches: 200 tracks then 50 tracks
        batch1 = [_make_track_dict(key=str(i)) for i in range(200)]
        batch2 = [_make_track_dict(key=str(i)) for i in range(200, 250)]
        mock_library_tracks.side_effect = [
            (batch1, 250),  # First batch
            (batch2, 250),  # Second batch
        ]

        _run_async(run_sync())

        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.COMPLETED
        assert status.synced_tracks == 250
        assert status.total_tracks == 250
        assert mock_upsert.call_count == 2

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_tracks_since")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    @patch("app.services.sync_service._update_sync_state_sync")
    @patch("app.services.sync_service._set_sync_started_sync")
    @patch("app.services.sync_service._get_last_sync_completed_sync")
    @patch("app.services.sync_service._upsert_tracks_sync")
    def test_updates_sync_status_as_batches_complete(
        self, mock_upsert, mock_last_sync, mock_set_started,
        mock_update_state, mock_setting, mock_credential,
        mock_tracks_since, mock_library_tracks,
    ):
        """run_sync updates SyncStatus in-memory as batches complete."""
        mock_setting.return_value = _make_mock_setting()
        mock_credential.return_value = "test-token"
        mock_last_sync.return_value = None

        batch = [_make_track_dict(key=str(i)) for i in range(50)]
        mock_library_tracks.return_value = (batch, 50)

        _run_async(run_sync())

        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.COMPLETED
        assert status.synced_tracks == 50
        assert status.last_synced is not None

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_tracks_since")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    @patch("app.services.sync_service._update_sync_state_sync")
    @patch("app.services.sync_service._set_sync_started_sync")
    @patch("app.services.sync_service._get_last_sync_completed_sync")
    @patch("app.services.sync_service._upsert_tracks_sync")
    def test_delta_sync_when_last_sync_exists(
        self, mock_upsert, mock_last_sync, mock_set_started,
        mock_update_state, mock_setting, mock_credential,
        mock_tracks_since, mock_library_tracks,
    ):
        """run_sync performs delta sync when last_sync_completed exists."""
        mock_setting.return_value = _make_mock_setting()
        mock_credential.return_value = "test-token"
        mock_last_sync.return_value = "2024-01-15T00:00:00"

        delta_tracks = [_make_track_dict(key="new1"), _make_track_dict(key="new2")]
        mock_tracks_since.return_value = (delta_tracks, 2)
        mock_library_tracks.return_value = ([], 500)  # Total count check

        _run_async(run_sync())

        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.COMPLETED
        assert status.synced_tracks == 2
        mock_tracks_since.assert_called_once()

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_tracks_since")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    @patch("app.services.sync_service._update_sync_state_sync")
    @patch("app.services.sync_service._set_sync_started_sync")
    @patch("app.services.sync_service._get_last_sync_completed_sync")
    @patch("app.services.sync_service._upsert_tracks_sync")
    def test_fallback_to_full_sync_when_delta_returns_zero(
        self, mock_upsert, mock_last_sync, mock_set_started,
        mock_update_state, mock_setting, mock_credential,
        mock_tracks_since, mock_library_tracks,
    ):
        """run_sync falls back to full sync when delta returns 0 but library has tracks."""
        mock_setting.return_value = _make_mock_setting()
        mock_credential.return_value = "test-token"
        mock_last_sync.return_value = "2024-01-15T00:00:00"

        # Delta returns 0 tracks
        mock_tracks_since.return_value = ([], 0)

        # Full sync: library has 10 tracks
        full_tracks = [_make_track_dict(key=str(i)) for i in range(10)]
        mock_library_tracks.side_effect = [
            ([], 10),       # Total count check (delta fallback detection)
            (full_tracks, 10),  # Full sync batch
        ]

        _run_async(run_sync())

        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.COMPLETED
        assert status.synced_tracks == 10

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_tracks_since")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    @patch("app.services.sync_service._update_sync_state_sync")
    @patch("app.services.sync_service._set_sync_started_sync")
    @patch("app.services.sync_service._get_last_sync_completed_sync")
    def test_sets_failed_on_exception(
        self, mock_last_sync, mock_set_started,
        mock_update_state, mock_setting, mock_credential,
        mock_tracks_since, mock_library_tracks,
    ):
        """run_sync sets state to FAILED and stores error on exception."""
        mock_setting.return_value = _make_mock_setting()
        mock_credential.return_value = "test-token"
        mock_last_sync.return_value = None
        mock_library_tracks.side_effect = ConnectionError("Connection refused")

        _run_async(run_sync())

        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.FAILED
        assert "Connection refused" in status.error

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_tracks_since")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    @patch("app.services.sync_service._update_sync_state_sync")
    @patch("app.services.sync_service._set_sync_started_sync")
    @patch("app.services.sync_service._get_last_sync_completed_sync")
    def test_prevents_concurrent_execution(
        self, mock_last_sync, mock_set_started,
        mock_update_state, mock_setting, mock_credential,
        mock_tracks_since, mock_library_tracks,
    ):
        """run_sync rejects if already RUNNING (T-02-03)."""
        from app.services.sync_service import SyncStateEnum, SyncStatus
        import app.services.sync_service as svc

        svc._sync_status = SyncStatus(state=SyncStateEnum.RUNNING)

        _run_async(run_sync())

        # Should still be RUNNING (not reset), and no Plex calls made
        assert svc._sync_status.state == SyncStateEnum.RUNNING
        mock_setting.assert_not_called()

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    @patch("app.services.sync_service._update_sync_state_sync")
    @patch("app.services.sync_service._set_sync_started_sync")
    @patch("app.services.sync_service._get_last_sync_completed_sync")
    def test_error_message_sanitizes_token(
        self, mock_last_sync, mock_set_started,
        mock_update_state, mock_setting, mock_credential,
        mock_library_tracks,
    ):
        """run_sync sanitizes Plex token from error messages (T-02-02)."""
        mock_setting.return_value = _make_mock_setting()
        mock_credential.return_value = "super-secret-token-123"
        mock_last_sync.return_value = None
        mock_library_tracks.side_effect = Exception(
            "Failed to connect with token super-secret-token-123"
        )

        _run_async(run_sync())

        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.FAILED
        assert "super-secret-token-123" not in status.error
        assert "[REDACTED]" in status.error


class TestUpsertTracks:
    """Tests for _upsert_tracks batch upsert logic."""

    def test_inserts_new_tracks(self, sync_db):
        """upsert_tracks inserts new tracks when they don't exist."""
        from app.services.sync_service import _upsert_tracks_sync

        tracks = [_make_track_dict(key="100"), _make_track_dict(key="101")]
        _upsert_tracks_sync(tracks)

        # Query from the same engine used by the service
        from app.database import get_engine
        with Session(get_engine()) as session:
            result = session.exec(select(Track)).all()
            assert len(result) == 2
            keys = {t.plex_rating_key for t in result}
            assert "100" in keys
            assert "101" in keys

    def test_updates_existing_tracks(self, sync_db):
        """upsert_tracks updates existing tracks matched by plex_rating_key."""
        from app.services.sync_service import _upsert_tracks_sync

        # Insert initial track
        tracks = [_make_track_dict(key="200", title="Original")]
        _upsert_tracks_sync(tracks)

        # Update the same track
        updated = [_make_track_dict(key="200", title="Updated")]
        _upsert_tracks_sync(updated)

        from app.database import get_engine
        with Session(get_engine()) as session:
            result = session.exec(select(Track)).all()
            assert len(result) == 1
            assert result[0].title == "Updated"


class TestGetSyncStatus:
    """Tests for get_sync_status."""

    def setup_method(self):
        _reset_sync_status()

    def test_returns_current_status(self):
        """get_sync_status returns current SyncStatus with correct state."""
        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.IDLE
        assert status.total_tracks == 0
        assert status.synced_tracks == 0


class TestGetLastSyncInfo:
    """Tests for get_last_sync_info."""

    def test_returns_sync_info(self, sync_db):
        """get_last_sync_info returns last_sync_completed and track counts."""
        from app.services.sync_service import get_last_sync_info

        # Add a SyncState record
        state = SyncState(
            last_sync_completed="2024-01-16T08:05:00",
            total_tracks=100,
        )
        sync_db.add(state)
        sync_db.commit()

        info = get_last_sync_info(sync_db)
        assert info["last_sync_completed"] == "2024-01-16T08:05:00"
        assert info["total_tracks"] == 100

    def test_returns_none_when_no_sync(self, sync_db):
        """get_last_sync_info returns None for last_sync_completed when no sync done."""
        from app.services.sync_service import get_last_sync_info

        info = get_last_sync_info(sync_db)
        assert info["last_sync_completed"] is None
        assert info["total_tracks"] == 0


# Import run_sync here so patches work correctly
from app.services.sync_service import run_sync
