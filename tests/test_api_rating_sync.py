"""Tests for app/routers/api_rating_sync.py — POST /start (EVT-05) and GET /status."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel


@pytest.fixture
def client(test_engine):
    """TestClient with all Phase 5 tables created."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def reset_backfill_status():
    """Reset _backfill_status between tests."""
    try:
        import app.services.backfill_service as bs

        bs._backfill_status = bs.BackfillStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        import app.services.backfill_service as bs

        bs._backfill_status = bs.BackfillStatus()
    except (ImportError, AttributeError):
        pass


class TestResyncStart:
    """Tests for POST /api/rating-sync/start — the 'Resync now' button (EVT-05)."""

    def test_resync_button(self, client):
        """POST /api/rating-sync/start returns 200 with backfill-banner partial."""
        # Mock the actual backfill task so we don't hit Plex
        async def fake_run_backfill():
            return None

        with patch(
            "app.routers.api_rating_sync.run_backfill",
            side_effect=fake_run_backfill,
        ):
            response = client.post("/api/rating-sync/start")

        assert response.status_code == 200
        assert "backfill-banner" in response.text


class TestResyncStatus:
    """Tests for GET /api/rating-sync/status — the HTMX poll endpoint."""

    def test_status_endpoint_returns_partial(self, client):
        """GET /api/rating-sync/status returns 200 with backfill-banner partial."""
        response = client.get("/api/rating-sync/status")

        assert response.status_code == 200
        assert "backfill-banner" in response.text
