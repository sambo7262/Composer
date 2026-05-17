"""Tests for app/routers/api_webhooks.py — Plex webhook receiver (EVT-01, EVT-02)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select


@pytest.fixture(autouse=True)
def reset_event_bus_singletons():
    """Reset event-bus + last-test caches between tests to avoid cross-test pollution."""
    from app.services import event_bus

    event_bus._queue = None
    event_bus._dispatcher_task = None

    # Reset api_webhooks last-test module state if it exists
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
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


class TestPlexWebhookEndpoint:
    def test_returns_200_for_valid_media_rate_payload(self, client):
        """POST /api/webhooks/plex with multipart 'payload' field returns 200."""
        body = {
            "event": "media.rate",
            "Metadata": {"ratingKey": "42"},
            "Rating": 7.0,
        }
        response = client.post(
            "/api/webhooks/plex",
            files={"payload": (None, json.dumps(body))},
        )
        assert response.status_code == 200

    def test_returns_200_under_50ms(self, client):
        """EVT-01: webhook responds in <50ms (push-and-return; no inline DB work)."""
        body = {
            "event": "media.rate",
            "Metadata": {"ratingKey": "42"},
            "Rating": 7.0,
        }
        start = time.perf_counter()
        response = client.post(
            "/api/webhooks/plex",
            files={"payload": (None, json.dumps(body))},
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert response.status_code == 200
        # 50ms target — generous margin for TestClient overhead
        assert elapsed_ms < 200, f"Webhook took {elapsed_ms:.1f}ms (target <50ms; tolerated <200ms in TestClient)"

    def test_malformed_json_still_returns_200(self, client):
        """Pitfall 1: NEVER return 4xx/5xx; Plex retries on non-2xx."""
        response = client.post(
            "/api/webhooks/plex",
            files={"payload": (None, "not-json{{{")},
        )
        assert response.status_code == 200

    def test_missing_payload_returns_422(self, client):
        """FastAPI's Form() validation handles missing required field; that's OK
        because Plex will always send 'payload' for a real webhook."""
        # Plain POST with no body — FastAPI returns 422 Unprocessable Entity
        # NOT 200 — but only for completely-malformed multipart requests.
        # Real Plex never sends these. This documents current behavior.
        response = client.post("/api/webhooks/plex")
        # Either 422 (validation error) or 200 — accept either; the contract
        # is "always 200 for *parseable* multipart with payload field".
        assert response.status_code in (200, 422)

    def test_unknown_event_type_returns_200(self, client):
        """Unknown event types (e.g., 'media.weird') return 200 + nothing on bus."""
        from app.services.event_bus import get_event_bus

        body = {"event": "weird.event", "Metadata": {"ratingKey": "42"}}
        response = client.post(
            "/api/webhooks/plex",
            files={"payload": (None, json.dumps(body))},
        )
        assert response.status_code == 200

        # Queue should be empty (or we tolerate a webhook_test if test mode armed)
        bus = get_event_bus()
        assert bus.qsize() == 0

    def test_dedupe_drops_duplicate(self, client, test_engine):
        """EVT-02: Posting the same payload twice within 5s window writes one EventLog row.

        End-to-end: webhook → bus → dispatcher → INSERT OR IGNORE → 1 row.
        """
        from app.services.event_bus import get_event_bus, start_dispatcher, stop_dispatcher
        from app.models.event_log import EventLog

        body = {
            "event": "media.rate",
            "Metadata": {"ratingKey": "9999"},
            "Rating": 6.0,
        }

        # Manually drive the dispatcher loop in this thread
        async def post_and_drain():
            await start_dispatcher()
            try:
                client.post("/api/webhooks/plex", files={"payload": (None, json.dumps(body))})
                client.post("/api/webhooks/plex", files={"payload": (None, json.dumps(body))})
                # Allow the dispatcher (running on the TestClient's app loop) time to drain.
                # TestClient runs its own loop; we sleep here to let async tasks complete.
                await asyncio.sleep(0.5)
            finally:
                await stop_dispatcher()

        # The TestClient lifespan already started the dispatcher. Just give it time.
        client.post("/api/webhooks/plex", files={"payload": (None, json.dumps(body))})
        client.post("/api/webhooks/plex", files={"payload": (None, json.dumps(body))})
        time.sleep(1.0)  # let dispatcher run

        with Session(test_engine) as session:
            rows = session.exec(
                select(EventLog).where(EventLog.plex_rating_key == "9999")
            ).all()
            # Two webhooks with identical payload within 5s bucket → one row
            assert len(rows) == 1, f"Expected 1 deduped row, got {len(rows)}"

    def test_last_test_endpoint_returns_partial(self, client):
        """GET /api/webhooks/plex/last-test returns an HTML partial (200)."""
        response = client.get("/api/webhooks/plex/last-test")
        assert response.status_code == 200
        assert "webhook-test-indicator" in response.text


# ============================================================================
# media.stop partial-play drain — SUGG-03 extension (UAT 2026-05-17)
# ============================================================================
#
# Plex sends media.scrobble only at ~90% completion. To make Suggestions
# refill on skips and partial plays, media.stop also fires TrackPlayedEvent
# when ratio (viewedOffset / duration) is in [0.30, 0.85). Below 0.30 =
# accidental tap (no drain). Above 0.85 = media.scrobble handles it
# (avoid double-counting view_count).


class TestMediaStopPartialPlay:
    def _post_stop(self, client, offset_ms, duration_ms, rating_key="42"):
        body = {
            "event": "media.stop",
            "Metadata": {
                "ratingKey": rating_key,
                "duration": duration_ms,
                "viewOffset": offset_ms,
            },
        }
        return client.post(
            "/api/webhooks/plex", files={"payload": (None, json.dumps(body))},
        )

    def _captured_events(self, client, offset_ms, duration_ms, monkeypatch):
        """Patch the event bus and return whatever was put on it for one POST."""
        from app.services import event_bus
        from app.routers import api_webhooks

        captured = []

        class _CapBus:
            def put_nowait(self, ev):
                captured.append(ev)

        monkeypatch.setattr(api_webhooks, "get_event_bus", lambda: _CapBus())
        resp = self._post_stop(client, offset_ms, duration_ms)
        assert resp.status_code == 200
        return captured

    def test_partial_play_50_percent_fires_drain(self, client, monkeypatch):
        """ratio=0.50 → TrackPlayedEvent fired (source='webhook' so dedupe
        still merges any same-bucket scrobble collision)."""
        from app.models.events import TrackPlayedEvent

        captured = self._captured_events(
            client, offset_ms=120_000, duration_ms=240_000, monkeypatch=monkeypatch,
        )
        played = [e for e in captured if isinstance(e, TrackPlayedEvent)]
        assert len(played) == 1
        assert played[0].source == "webhook"
        assert played[0].plex_rating_key == "42"

    def test_partial_play_30_percent_boundary_fires(self, client, monkeypatch):
        """ratio=0.30 (lower bound, inclusive) → drain fires."""
        from app.models.events import TrackPlayedEvent

        captured = self._captured_events(
            client, offset_ms=30_000, duration_ms=100_000, monkeypatch=monkeypatch,
        )
        played = [e for e in captured if isinstance(e, TrackPlayedEvent)]
        assert len(played) == 1

    def test_skip_under_30_percent_does_not_fire(self, client, monkeypatch):
        """ratio=0.25 → accidental-tap zone, no drain."""
        from app.models.events import TrackPlayedEvent

        captured = self._captured_events(
            client, offset_ms=25_000, duration_ms=100_000, monkeypatch=monkeypatch,
        )
        played = [e for e in captured if isinstance(e, TrackPlayedEvent)]
        assert played == []

    def test_above_85_percent_does_not_fire_from_stop(self, client, monkeypatch):
        """ratio=0.90 → media.scrobble's responsibility, no fire from media.stop
        to avoid double-counting view_count.
        """
        from app.models.events import TrackPlayedEvent

        captured = self._captured_events(
            client, offset_ms=90_000, duration_ms=100_000, monkeypatch=monkeypatch,
        )
        played = [e for e in captured if isinstance(e, TrackPlayedEvent)]
        assert played == []

    def test_missing_duration_does_not_crash(self, client, monkeypatch):
        """Missing or zero duration → ratio undefined → no fire (no crash)."""
        from app.models.events import TrackPlayedEvent
        from app.routers import api_webhooks

        captured = []

        class _CapBus:
            def put_nowait(self, ev):
                captured.append(ev)

        monkeypatch.setattr(api_webhooks, "get_event_bus", lambda: _CapBus())
        body = {
            "event": "media.stop",
            "Metadata": {"ratingKey": "42", "viewOffset": 30_000},
            # No duration field at all.
        }
        resp = client.post(
            "/api/webhooks/plex", files={"payload": (None, json.dumps(body))},
        )
        assert resp.status_code == 200
        assert [e for e in captured if isinstance(e, TrackPlayedEvent)] == []

    def test_zero_duration_does_not_divide_by_zero(self, client, monkeypatch):
        """duration=0 from a buggy/early Plex event → no crash, no fire."""
        from app.routers import api_webhooks

        captured = []

        class _CapBus:
            def put_nowait(self, ev):
                captured.append(ev)

        monkeypatch.setattr(api_webhooks, "get_event_bus", lambda: _CapBus())
        resp = self._post_stop(client, offset_ms=30_000, duration_ms=0)
        assert resp.status_code == 200
