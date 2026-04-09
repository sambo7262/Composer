from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.mark.asyncio
class TestPlexClient:
    """Test Plex connection client with mocked PlexServer."""

    @patch("app.services.plex_client.PlexServer")
    async def test_success_returns_server_name_and_libraries(self, mock_plex_cls):
        """test_plex_connection returns success dict with libraries filtered by artist type."""
        from app.services.plex_client import test_plex_connection

        # Mock PlexServer instance
        mock_server = MagicMock()
        mock_server.friendlyName = "My Plex Server"

        # Create mock library sections - one artist, one movie
        music_section = MagicMock()
        music_section.type = "artist"
        music_section.key = "1"
        music_section.title = "Music"

        movie_section = MagicMock()
        movie_section.type = "movie"
        movie_section.key = "2"
        movie_section.title = "Movies"

        mock_server.library.sections.return_value = [music_section, movie_section]
        mock_plex_cls.return_value = mock_server

        result = await test_plex_connection("http://plex:32400", "test-token")

        assert result["success"] is True
        assert result["server_name"] == "My Plex Server"
        assert len(result["libraries"]) == 1
        assert result["libraries"][0]["title"] == "Music"
        assert result["libraries"][0]["key"] == "1"

    @patch("app.services.plex_client.PlexServer")
    async def test_bad_credentials_returns_auth_error(self, mock_plex_cls):
        """test_plex_connection with 401 returns auth error message."""
        from app.services.plex_client import test_plex_connection

        mock_plex_cls.side_effect = Exception("(401) Unauthorized")

        result = await test_plex_connection("http://plex:32400", "bad-token")

        assert result["success"] is False
        assert "Authentication failed" in result["error"]

    @patch("app.services.plex_client.PlexServer")
    async def test_timeout_returns_timeout_error(self, mock_plex_cls):
        """test_plex_connection with timeout returns timeout error message."""
        from app.services.plex_client import test_plex_connection

        mock_plex_cls.side_effect = Exception("Connection timed out")

        result = await test_plex_connection("http://plex:32400", "token")

        assert result["success"] is False
        assert "timed out" in result["error"]

    @patch("app.services.plex_client.PlexServer")
    async def test_generic_error_returns_generic_message(self, mock_plex_cls):
        """test_plex_connection with unknown error returns generic message."""
        from app.services.plex_client import test_plex_connection

        mock_plex_cls.side_effect = Exception("Something weird happened")

        result = await test_plex_connection("http://plex:32400", "token")

        assert result["success"] is False
        assert "Could not connect" in result["error"]


@pytest.mark.asyncio
class TestOllamaClient:
    """Test Ollama connection client with mocked OpenAI SDK."""

    @patch("app.services.ollama_client.OpenAI")
    async def test_success_returns_model_list(self, mock_openai_cls):
        """test_ollama_connection returns list of model IDs."""
        from app.services.ollama_client import test_ollama_connection

        mock_client = MagicMock()
        mock_model_1 = MagicMock()
        mock_model_1.id = "llama3:latest"
        mock_model_2 = MagicMock()
        mock_model_2.id = "mistral:latest"

        mock_response = MagicMock()
        mock_response.data = [mock_model_1, mock_model_2]
        mock_client.models.list.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = await test_ollama_connection("http://ollama:11434")

        assert result["success"] is True
        assert result["models"] == ["llama3:latest", "mistral:latest"]
        mock_openai_cls.assert_called_once_with(
            base_url="http://ollama:11434/v1", api_key="ollama"
        )

    @patch("app.services.ollama_client.OpenAI")
    async def test_timeout_returns_timeout_error(self, mock_openai_cls):
        """test_ollama_connection with timeout returns timeout message."""
        from app.services.ollama_client import test_ollama_connection

        mock_openai_cls.side_effect = Exception("Connection timeout")

        result = await test_ollama_connection("http://ollama:11434")

        assert result["success"] is False
        assert "timed out" in result["error"]

    @patch("app.services.ollama_client.OpenAI")
    async def test_generic_error_returns_generic_message(self, mock_openai_cls):
        """test_ollama_connection with unknown error returns generic message."""
        from app.services.ollama_client import test_ollama_connection

        mock_openai_cls.side_effect = Exception("Weird error")

        result = await test_ollama_connection("http://ollama:11434")

        assert result["success"] is False
        assert "Could not connect" in result["error"]


@pytest.mark.asyncio
class TestLidarrClient:
    """Test Lidarr connection client with mocked LidarrAPI."""

    @patch("app.services.lidarr_client.LidarrAPI")
    async def test_success_returns_quality_profiles(self, mock_lidarr_cls):
        """test_lidarr_connection returns quality profiles with id and name."""
        from app.services.lidarr_client import test_lidarr_connection

        mock_lidarr = MagicMock()
        mock_lidarr.get_quality_profile.return_value = [
            {"id": 1, "name": "Lossless"},
            {"id": 2, "name": "Standard"},
        ]
        mock_lidarr_cls.return_value = mock_lidarr

        result = await test_lidarr_connection("http://lidarr:8686", "api-key-123")

        assert result["success"] is True
        assert len(result["profiles"]) == 2
        assert result["profiles"][0] == {"id": 1, "name": "Lossless"}

    @patch("app.services.lidarr_client.LidarrAPI")
    async def test_bad_credentials_returns_auth_error(self, mock_lidarr_cls):
        """test_lidarr_connection with 401 returns auth error."""
        from app.services.lidarr_client import test_lidarr_connection

        mock_lidarr_cls.side_effect = Exception("401 Unauthorized")

        result = await test_lidarr_connection("http://lidarr:8686", "bad-key")

        assert result["success"] is False
        assert "Authentication failed" in result["error"]

    @patch("app.services.lidarr_client.LidarrAPI")
    async def test_timeout_returns_timeout_error(self, mock_lidarr_cls):
        """test_lidarr_connection with timeout returns timeout message."""
        from app.services.lidarr_client import test_lidarr_connection

        mock_lidarr_cls.side_effect = Exception("Connection timed out")

        result = await test_lidarr_connection("http://lidarr:8686", "key")

        assert result["success"] is False
        assert "timed out" in result["error"]

    @patch("app.services.lidarr_client.LidarrAPI")
    async def test_generic_error_returns_generic_message(self, mock_lidarr_cls):
        """test_lidarr_connection with unknown error returns generic message."""
        from app.services.lidarr_client import test_lidarr_connection

        mock_lidarr_cls.side_effect = Exception("Something broke")

        result = await test_lidarr_connection("http://lidarr:8686", "key")

        assert result["success"] is False
        assert "Could not connect" in result["error"]
