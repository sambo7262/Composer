from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetLibraryTracks:
    """Tests for plex_client.get_library_tracks."""

    @patch("app.services.plex_client.asyncio.to_thread")
    def test_returns_tracks_and_total(self, mock_to_thread):
        """get_library_tracks returns (list[dict], total_count) with correct mapping."""
        from app.services.plex_client import get_library_tracks

        # Mock PlexServer and section
        mock_section = MagicMock()
        mock_section.totalSize = 1

        mock_genre = MagicMock()
        mock_genre.tag = "Rock"
        mock_track = MagicMock()
        mock_track.ratingKey = 12345
        mock_track.title = "Test Song"
        mock_track.grandparentTitle = "Test Artist"
        mock_track.parentTitle = "Test Album"
        mock_track.genres = [mock_genre]
        mock_track.year = 2020
        mock_track.duration = 240000
        mock_track.addedAt = datetime(2024, 1, 15, 12, 0, 0)
        mock_track.updatedAt = datetime(2024, 1, 15, 12, 0, 0)

        mock_plex = MagicMock()

        # to_thread is called multiple times: PlexServer, sectionByID, searchTracks
        def to_thread_side_effect(func, *args, **kwargs):
            if args and args[0] == "http://plex:32400":
                return mock_plex
            result = func(*args, **kwargs) if callable(func) else func
            return result

        mock_to_thread.side_effect = [
            mock_plex,  # PlexServer
            mock_section,  # sectionByID
            [mock_track],  # searchTracks
        ]

        result = asyncio.get_event_loop().run_until_complete(
            get_library_tracks("http://plex:32400", "token", "1")
        )

        tracks, total = result
        assert total == 1
        assert len(tracks) == 1
        assert tracks[0]["plex_rating_key"] == "12345"
        assert tracks[0]["title"] == "Test Song"
        assert tracks[0]["artist"] == "Test Artist"
        assert tracks[0]["album"] == "Test Album"
        assert tracks[0]["genre"] == "Rock"
        assert tracks[0]["year"] == 2020
        assert tracks[0]["duration_ms"] == 240000

    @patch("app.services.plex_client.asyncio.to_thread")
    def test_uses_asyncio_to_thread(self, mock_to_thread):
        """get_library_tracks wraps PlexAPI calls in asyncio.to_thread."""
        from app.services.plex_client import get_library_tracks

        mock_section = MagicMock()
        mock_section.totalSize = 0
        mock_plex = MagicMock()

        mock_to_thread.side_effect = [mock_plex, mock_section, []]

        asyncio.get_event_loop().run_until_complete(
            get_library_tracks("http://plex:32400", "token", "1")
        )

        # Should have called to_thread at least 3 times (PlexServer, sectionByID, searchTracks)
        assert mock_to_thread.call_count >= 3


class TestGetTracksSince:
    """Tests for plex_client.get_tracks_since."""

    @patch("app.services.plex_client.asyncio.to_thread")
    def test_filters_by_added_at(self, mock_to_thread):
        """get_tracks_since filters by addedAt for delta sync."""
        from app.services.plex_client import get_tracks_since

        mock_section = MagicMock()
        mock_plex = MagicMock()

        mock_track = MagicMock()
        mock_track.ratingKey = 99
        mock_track.title = "New Song"
        mock_track.grandparentTitle = "New Artist"
        mock_track.parentTitle = "New Album"
        mock_track.genres = []
        mock_track.year = 2024
        mock_track.duration = 180000
        mock_track.addedAt = datetime(2024, 6, 1)
        mock_track.updatedAt = None

        mock_to_thread.side_effect = [mock_plex, mock_section, [mock_track]]

        tracks, count = asyncio.get_event_loop().run_until_complete(
            get_tracks_since("http://plex:32400", "token", "1", "2024-05-01")
        )

        assert count == 1
        assert tracks[0]["plex_rating_key"] == "99"
        assert tracks[0]["title"] == "New Song"


class TestMapTrackFilePath:
    """Tests for file_path extraction in _map_track."""

    def test_extracts_file_path_from_media_parts(self):
        """_map_track extracts file_path from track.media[0].parts[0].file."""
        from app.services.plex_client import _map_track

        mock_part = MagicMock()
        mock_part.file = "/data/Music/Artist/Album/song.flac"
        mock_media = MagicMock()
        mock_media.parts = [mock_part]

        mock_track = MagicMock()
        mock_track.ratingKey = 100
        mock_track.title = "Song"
        mock_track.grandparentTitle = "Artist"
        mock_track.parentTitle = "Album"
        mock_track.genres = []
        mock_track.year = 2020
        mock_track.duration = 200000
        mock_track.addedAt = None
        mock_track.updatedAt = None
        mock_track.media = [mock_media]

        result = _map_track(mock_track)
        assert result["file_path"] == "/data/Music/Artist/Album/song.flac"

    def test_file_path_none_when_no_media(self):
        """_map_track returns file_path=None when media is empty."""
        from app.services.plex_client import _map_track

        mock_track = MagicMock()
        mock_track.ratingKey = 101
        mock_track.title = "No Media"
        mock_track.grandparentTitle = "Artist"
        mock_track.parentTitle = "Album"
        mock_track.genres = []
        mock_track.year = 2020
        mock_track.duration = 200000
        mock_track.addedAt = None
        mock_track.updatedAt = None
        mock_track.media = []

        result = _map_track(mock_track)
        assert result["file_path"] is None

    def test_file_path_none_when_no_parts(self):
        """_map_track returns file_path=None when parts is empty."""
        from app.services.plex_client import _map_track

        mock_media = MagicMock()
        mock_media.parts = []

        mock_track = MagicMock()
        mock_track.ratingKey = 102
        mock_track.title = "No Parts"
        mock_track.grandparentTitle = "Artist"
        mock_track.parentTitle = "Album"
        mock_track.genres = []
        mock_track.year = 2020
        mock_track.duration = 200000
        mock_track.addedAt = None
        mock_track.updatedAt = None
        mock_track.media = [mock_media]

        result = _map_track(mock_track)
        assert result["file_path"] is None
