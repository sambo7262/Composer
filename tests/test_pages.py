"""Tests for app/routers/pages.py — /debug/events page (DEBUG-01) + library-at-a-glance render
on /settings (Concern 1 / ROADMAP SC-5) and on /debug/events itself.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel


@pytest.fixture(autouse=True)
def reset_event_bus_singletons():
    """Reset event-bus + last-test caches between tests."""
    from app.services import event_bus

    event_bus._queue = None
    event_bus._dispatcher_task = None
    try:
        import app.routers.api_webhooks as wh

        wh._last_test_received_at = None
        wh._last_test_payload = None
    except Exception:
        pass
    yield
    event_bus._queue = None
    event_bus._dispatcher_task = None


@pytest.fixture
def client(test_engine):
    """TestClient with all Phase 5 tables registered."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def db_with_event_logs(test_engine):
    """Insert 3 EventLog rows including one with a handler_error to test highlighting."""
    from app.models.event_log import EventLog
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        session.add(
            EventLog(
                source="webhook",
                event_type="rating_changed",
                plex_rating_key="42",
                dedupe_key="abc123def4567890",
                received_at="2026-05-08T10:00:00+00:00",
                processed_at="2026-05-08T10:00:00.500000+00:00",
            )
        )
        session.add(
            EventLog(
                source="poll",
                event_type="rating_changed",
                plex_rating_key="43",
                dedupe_key="bbb123def4567890",
                received_at="2026-05-08T10:01:00+00:00",
                processed_at="2026-05-08T10:01:00.500000+00:00",
            )
        )
        session.add(
            EventLog(
                source="webhook",
                event_type="track_played",
                plex_rating_key="44",
                dedupe_key="ccc123def4567890",
                received_at="2026-05-08T10:02:00+00:00",
                processed_at=None,
                handler_error="ValueError: bad rating",
            )
        )
        session.commit()
    yield
    SQLModel.metadata.drop_all(test_engine)


class TestDebugEventsPage:
    def test_debug_events_page(self, client, db_with_event_logs):
        """DEBUG-01: /debug/events returns 200 and renders last 50 EventLog rows."""
        response = client.get("/debug/events")
        assert response.status_code == 200
        # Verify the inserted rows render
        assert "received_at" in response.text.lower() or "received" in response.text
        assert "Queue depth" in response.text
        assert "Webhook URL" in response.text
        # Specific dedupe key prefix from the seeded rows
        assert "abc123de" in response.text or "abc123def" in response.text
        # The error row should highlight via bg-error/5
        assert "bg-error/5" in response.text

    def test_debug_events_empty(self, client):
        """Empty EventLog renders the 'No events yet' alternative content."""
        response = client.get("/debug/events")
        assert response.status_code == 200
        # Empty-state assertions — verify the alternative content path actually renders.
        assert "No events yet" in response.text  # exact copy from debug_events.html
        # Verify the static page chrome still renders.
        assert "Queue depth" in response.text
        assert "Webhook URL" in response.text
        # No event-row error highlighting class shows up when there are zero events.
        assert "bg-error/5" not in response.text


class TestLibraryAtAGlanceRender:
    """Concern 1 / ROADMAP SC-5: /settings AND /debug/events both embed library_stats."""

    def test_settings_renders_library_at_a_glance(self, client):
        """/settings includes the HTMX loader for /api/library/stats above service cards."""
        response = client.get("/settings")
        assert response.status_code == 200
        # The settings page must contain the polling endpoint reference.
        assert "/api/library/stats" in response.text

    def test_debug_events_renders_library_at_a_glance(self, client):
        """/debug/events includes the HTMX loader for /api/library/stats at the top."""
        response = client.get("/debug/events")
        assert response.status_code == 200
        assert "/api/library/stats" in response.text
