"""Tests for the chat service: session management, message processing, track ID validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models.schemas import FeatureCriteria, TrackSelection
from app.services.chat_service import (
    ChatSession,
    _sessions,
    clear_session,
    get_or_create_session,
    process_message,
)


@pytest.fixture(autouse=True)
def clean_sessions():
    """Clear session store before and after each test."""
    _sessions.clear()
    yield
    _sessions.clear()


class TestSessionManagement:
    """Tests for get_or_create_session and clear_session."""

    def test_get_or_create_session_new(self):
        """Creates a new session with a UUID when no ID given."""
        session = get_or_create_session()
        assert session.session_id
        assert session.messages == []
        assert session.current_playlist == []
        assert session.track_count == 20
        assert session.session_id in _sessions

    def test_get_or_create_session_returns_existing(self):
        """Returns the same session object on second call with same ID."""
        session1 = get_or_create_session("test-id-1")
        session1.messages.append({"role": "user", "content": "hello"})

        session2 = get_or_create_session("test-id-1")
        assert session2 is session1
        assert len(session2.messages) == 1

    def test_get_or_create_session_with_new_id(self):
        """Creates a session with a specific ID when provided."""
        session = get_or_create_session("my-custom-id")
        assert session.session_id == "my-custom-id"

    def test_session_tracks_messages(self):
        """Session accumulates messages correctly."""
        session = get_or_create_session("msg-test")
        session.messages.append({"role": "user", "content": "play something chill"})
        session.messages.append({"role": "assistant", "content": "Here is a chill playlist"})

        assert len(session.messages) == 2
        assert session.messages[0]["role"] == "user"
        assert session.messages[1]["role"] == "assistant"

    def test_clear_session(self):
        """Clearing removes the session from the store."""
        get_or_create_session("to-clear")
        assert "to-clear" in _sessions

        clear_session("to-clear")
        assert "to-clear" not in _sessions

    def test_clear_nonexistent_session(self):
        """Clearing a nonexistent session does not raise."""
        clear_session("nonexistent")  # Should not raise


class TestProcessMessage:
    """Tests for process_message with mocked LLM calls."""

    @pytest.fixture
    def mock_criteria(self):
        return FeatureCriteria(
            energy_min=0.3, energy_max=0.6,
            tempo_min=80.0, tempo_max=120.0,
            danceability_min=0.4, danceability_max=0.7,
            valence_min=0.5, valence_max=0.8,
            genres=[], artists=[], exclude_genres=[],
            explanation="Chill vibes with moderate energy",
        )

    @pytest.fixture
    def mock_selection(self):
        return TrackSelection(
            track_ids=[1, 2, 3],
            explanation="Selected tracks for a chill mood",
        )

    @pytest.fixture
    def mock_track(self):
        """Create a mock Track object."""
        track = MagicMock()
        track.id = 1
        track.title = "Test Track"
        track.artist = "Test Artist"
        track.genre = "Electronic"
        track.energy = 0.4
        track.tempo = 100.0
        track.danceability = 0.5
        track.valence = 0.6
        return track

    @pytest.mark.asyncio
    async def test_plex_playlist_request_declined(self):
        """Requests about existing Plex playlists are gracefully declined."""
        db_session = MagicMock()
        result = await process_message("test-sess", "modify my existing playlist", 20, db_session)

        assert "can't modify existing Plex playlists" in result["explanation"]
        assert result["tracks"] == []
        assert result["criteria"] is None

    @pytest.mark.asyncio
    async def test_ollama_not_configured(self):
        """Returns error when Ollama is not configured."""
        db_session = MagicMock()

        with patch(
            "app.services.chat_service.get_instructor_client",
            side_effect=ValueError("Ollama is not configured."),
        ):
            result = await process_message("test-sess", "play something chill", 20, db_session)

        assert result.get("error") is True
        assert "not configured" in result["explanation"].lower()

    @pytest.mark.asyncio
    async def test_process_message_validates_track_ids(self, mock_criteria, mock_track):
        """Track IDs not in candidates are filtered out (T-04-03)."""
        # Selection includes ID 999 which is not a valid candidate
        bad_selection = TrackSelection(
            track_ids=[1, 999, 2],
            explanation="Selected tracks",
        )

        track2 = MagicMock()
        track2.id = 2
        track2.title = "Track Two"

        mock_instructor = MagicMock()
        # First call returns criteria, second returns selection
        mock_instructor.chat.completions.create = MagicMock(
            side_effect=[mock_criteria, bad_selection]
        )

        db_session = MagicMock()

        with patch(
            "app.services.chat_service.get_instructor_client",
            return_value=(mock_instructor, "llama3.1:8b"),
        ), patch(
            "app.services.chat_service.filter_candidates",
            return_value=[(mock_track, 0.1), (track2, 0.2)],
        ), patch(
            "app.services.chat_service.format_candidates_for_llm",
            return_value="1|Test|Artist|Genre|0.4|100|0.5|0.6",
        ), patch(
            "app.services.chat_service.asyncio.to_thread",
            side_effect=[mock_criteria, bad_selection],
        ):
            result = await process_message("test-sess", "chill vibes", 3, db_session)

        # ID 999 should be filtered out
        assert 999 not in [t.id for t in result["tracks"]]
        # Valid IDs 1 and 2 should remain
        valid_ids = [t.id for t in result["tracks"]]
        assert 1 in valid_ids
        assert 2 in valid_ids

    @pytest.mark.asyncio
    async def test_process_message_success(self, mock_criteria, mock_selection, mock_track):
        """Full pipeline succeeds with mocked LLM calls."""
        track2 = MagicMock()
        track2.id = 2
        track2.title = "Track Two"

        track3 = MagicMock()
        track3.id = 3
        track3.title = "Track Three"

        mock_instructor = MagicMock()

        db_session = MagicMock()

        with patch(
            "app.services.chat_service.get_instructor_client",
            return_value=(mock_instructor, "llama3.1:8b"),
        ), patch(
            "app.services.chat_service.filter_candidates",
            return_value=[(mock_track, 0.1), (track2, 0.2), (track3, 0.3)],
        ), patch(
            "app.services.chat_service.format_candidates_for_llm",
            return_value="ID|Title|Artist|Genre|E|T|D|V",
        ), patch(
            "app.services.chat_service.asyncio.to_thread",
            side_effect=[mock_criteria, mock_selection],
        ):
            result = await process_message("test-sess", "energetic workout music", 3, db_session)

        assert len(result["tracks"]) == 3
        assert result["criteria"] is not None
        assert result["session_id"] == "test-sess"
        assert "explanation" in result

    @pytest.mark.asyncio
    async def test_no_candidates_found(self, mock_criteria):
        """Returns helpful message when no tracks match criteria."""
        mock_instructor = MagicMock()
        db_session = MagicMock()

        with patch(
            "app.services.chat_service.get_instructor_client",
            return_value=(mock_instructor, "llama3.1:8b"),
        ), patch(
            "app.services.chat_service.filter_candidates",
            return_value=[],
        ), patch(
            "app.services.chat_service.asyncio.to_thread",
            return_value=mock_criteria,
        ):
            result = await process_message("test-sess", "rare genre music", 20, db_session)

        assert result["tracks"] == []
        assert "couldn't find any matching tracks" in result["explanation"].lower()
