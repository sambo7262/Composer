from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a FastAPI test client with analysis router."""
    from app.main import app
    from app.database import init_db
    init_db()
    return TestClient(app)


class TestAnalysisStart:
    """Tests for POST /api/analysis/start endpoint."""

    @patch("app.routers.api_analysis.run_analysis")
    @patch("app.routers.api_analysis.get_analysis_status")
    def test_start_returns_html_partial(self, mock_status, mock_run, client):
        """POST /api/analysis/start returns analysis banner HTML partial."""
        from app.services.analysis_service import AnalysisStateEnum, AnalysisStatus

        mock_status.return_value = AnalysisStatus(state=AnalysisStateEnum.IDLE)
        response = client.post("/api/analysis/start")
        assert response.status_code == 200
        assert "analysis-banner" in response.text

    @patch("app.routers.api_analysis.run_analysis")
    @patch("app.routers.api_analysis.get_analysis_status")
    def test_start_when_already_running_returns_progress(self, mock_status, mock_run, client):
        """Start when already running returns current progress (no error)."""
        from app.services.analysis_service import AnalysisStateEnum, AnalysisStatus

        mock_status.return_value = AnalysisStatus(
            state=AnalysisStateEnum.RUNNING,
            total_tracks=100,
            analyzed_tracks=50,
        )
        response = client.post("/api/analysis/start")
        assert response.status_code == 200
        assert "analysis-banner" in response.text
        # Should show running state, not error
        assert "Analyzing" in response.text


class TestAnalysisStop:
    """Tests for POST /api/analysis/stop endpoint."""

    @patch("app.routers.api_analysis.stop_analysis")
    @patch("app.routers.api_analysis.get_analysis_status")
    def test_stop_returns_paused_banner(self, mock_status, mock_stop, client):
        """POST /api/analysis/stop returns analysis banner with paused state."""
        from app.services.analysis_service import AnalysisStateEnum, AnalysisStatus

        mock_stop.return_value = None
        mock_status.return_value = AnalysisStatus(
            state=AnalysisStateEnum.PAUSED,
            total_tracks=100,
            analyzed_tracks=30,
        )
        response = client.post("/api/analysis/stop")
        assert response.status_code == 200
        assert "analysis-banner" in response.text
        assert "paused" in response.text.lower()


class TestAnalysisStatus:
    """Tests for GET /api/analysis/status endpoint."""

    @patch("app.routers.api_analysis.get_analysis_status")
    def test_status_returns_progress_html(self, mock_status, client):
        """GET /api/analysis/status returns analysis banner HTML with current progress."""
        from app.services.analysis_service import AnalysisStateEnum, AnalysisStatus

        mock_status.return_value = AnalysisStatus(
            state=AnalysisStateEnum.RUNNING,
            total_tracks=200,
            analyzed_tracks=100,
        )
        response = client.get("/api/analysis/status")
        assert response.status_code == 200
        assert "analysis-banner" in response.text


class TestSyncAutoTrigger:
    """Tests for sync completion triggering analysis."""

    def test_sync_service_imports_trigger(self):
        """sync_service.run_sync has analysis trigger wired."""
        import inspect
        from app.services.sync_service import run_sync

        source = inspect.getsource(run_sync)
        assert "trigger_post_sync_analysis" in source
