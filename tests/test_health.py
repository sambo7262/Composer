from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_200(tmp_data_dir):
    """GET /api/health returns 200 with status ok."""
    with TestClient(app) as client:
        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


def test_root_renders_welcome_when_unconfigured(tmp_data_dir):
    """GET / returns 200 with welcome page when Plex is not configured."""
    with TestClient(app) as client:
        response = client.get("/")

        assert response.status_code == 200
        assert "Welcome to Composer" in response.text
