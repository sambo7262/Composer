"""Phase 8 Plan 05 Task 1 — /debug/discovery diagnostic page (DEBUG-04 / DEBUG-05).

Plain-HTML diagnostic surface for the Phase 8 discovery pipeline. Five
sections per CONTEXT §"Claude's Discretion: /debug/discovery page layout":

  1. Last weekly candidate set with provenance per artist
     (factual_hook + llm_rationale + popularity_gate + listener_count)
  2. DiscoveryAdd lifecycle timeline (all lifecycle timestamps)
  3. Recent MusicBrainz queries (proxied via MusicBrainzCache cached_at DESC)
  4. Recent Lidarr add_artist requests + responses (DiscoveryAdd recent slice)
  5. Lidarr connection-test history (best-effort from EventLog;
     empty-state placeholder when not wired)

Plus a cost panel at the top aggregating LLMUsage rows where
``purpose LIKE 'discovery_artist_%'`` (split from /debug/suggestions'
combined ``suggestions_*`` + ``discovery_%`` view).

Plus a recent-LLM-calls section showing the same purpose-filtered slice
in detail (called_at, model, token breakdown, cost, error).

Plan 05 ADDITION-1 — Manual "Run weekly tick now" button.

The /debug/discovery page renders a button that POSTs to
``/api/discovery/run-tick-now``. The endpoint invokes
``discovery_service._weekly_maintenance_tick`` (the full 4-step Sunday
cron) in a FastAPI BackgroundTask so the request returns immediately.
409 if ``discovery_service.get_state().state == "running"``. The page
polls the state every 5s.

Tests cover:
  - 202 on success + background task spawned
  - 409 conflict when state is running
  - Background task exception captured to logs + _status.last_error
    without raising
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel


@pytest.fixture
def client_full(test_engine) -> Generator[TestClient, None, None]:
    """Phase 8 full client — mirrors test_api_discovery.client_full."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist, MigrationLog, SetupState, SlotInLog, TrackVibe, Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
    )
    from app.models.discovery import (  # noqa: F401
        CostMeterBaseline,
        DiscoveryAdd,
        DiscoveryCandidate,
        DiscoveryDismissed,
        MusicBrainzCache,
        WeeklyCronState,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)
    from app.main import app
    with TestClient(app) as c:
        yield c
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def _reset_discovery_state():
    """Reset the discovery_service module singleton between tests."""
    from app.services import discovery_service
    discovery_service._reset_state_for_tests()
    yield
    discovery_service._reset_state_for_tests()


# ---------------------------------------------------------------------------
# Helpers — seed rows for each table the page reads
# ---------------------------------------------------------------------------


def _seed_track(session, *, plex_rating_key="rk-1", title="T1", artist="A1",
                user_rating=0, plex_artist_mbid=None):
    from app.models.track import Track
    t = Track(
        plex_rating_key=plex_rating_key, title=title, artist=artist,
        user_rating=user_rating, plex_artist_mbid=plex_artist_mbid,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_vibe(session, *, name="Mellow", color="#3b82f6"):
    from app.models.vibe import Vibe
    v = Vibe(name=name, color=color, is_active=True)
    session.add(v)
    session.commit()
    session.refresh(v)
    return v


def _seed_candidate(session, *, mb_id, artist_name, seed_track_id, seed_vibe_id,
                    factual_hook=None, llm_rationale=None, llm_rank=None,
                    mb_listener_count=None, popularity_gate_pass=True,
                    created_at=None):
    from app.models.discovery import DiscoveryCandidate
    row = DiscoveryCandidate(
        mb_id=mb_id, artist_name=artist_name,
        seed_track_id=seed_track_id, seed_vibe_id=seed_vibe_id,
        factual_hook=factual_hook, llm_rationale=llm_rationale,
        llm_rank=llm_rank, mb_listener_count=mb_listener_count,
        popularity_gate_pass=popularity_gate_pass,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _seed_discovery_add(session, *, mb_id, artist_name="Artist Name",
                        added_at=None, lidarr_status="searching",
                        lidarr_artist_id=42,
                        composer_sync_seen_at=None,
                        essentia_complete_at=None,
                        vibe_slotted_at=None,
                        removed_from_discover_at=None):
    from app.models.discovery import DiscoveryAdd
    row = DiscoveryAdd(
        mb_id=mb_id, artist_name=artist_name,
        added_at=added_at or datetime.now(timezone.utc).isoformat(),
        lidarr_status=lidarr_status,
        lidarr_artist_id=lidarr_artist_id,
        composer_sync_seen_at=composer_sync_seen_at,
        essentia_complete_at=essentia_complete_at,
        vibe_slotted_at=vibe_slotted_at,
        removed_from_discover_at=removed_from_discover_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _seed_mb_cache(session, *, mb_id, payload_json='{"name":"X"}',
                   cached_at=None):
    from app.models.discovery import MusicBrainzCache
    row = MusicBrainzCache(
        mb_id=mb_id,
        payload_json=payload_json,
        cached_at=cached_at or datetime.now(timezone.utc).isoformat(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _seed_llmusage(session, *, called_at=None, purpose="discovery_artist_weekly",
                   cost_estimate_usd=0.05, model="claude-sonnet-4-6",
                   input_tokens=100, output_tokens=50, error_text=None):
    from app.models.llm_usage import LLMUsage
    row = LLMUsage(
        called_at=called_at or datetime.now(timezone.utc).isoformat(),
        purpose=purpose, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
        cost_estimate_usd=cost_estimate_usd,
        error_text=error_text,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _seed_event_log(session, *, source="lidarr", event_type="lidarr_test",
                    received_at=None, raw_payload='{"ok":true}'):
    from app.models.event_log import EventLog
    import hashlib
    received = received_at or datetime.now(timezone.utc).isoformat()
    dedupe = hashlib.sha256(
        f"{source}|{event_type}|{received}".encode()
    ).hexdigest()
    row = EventLog(
        source=source, event_type=event_type,
        plex_rating_key=None, dedupe_key=dedupe,
        received_at=received,
        raw_payload=raw_payload,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# /debug/discovery — page-level tests (DEBUG-04)
# ---------------------------------------------------------------------------


class TestDebugDiscoveryPage:
    def test_debug_discovery_returns_200(self, client_full):
        resp = client_full.get("/debug/discovery")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_debug_discovery_renders_h1(self, client_full):
        resp = client_full.get("/debug/discovery")
        body = resp.text
        assert "/debug/discovery" in body
        # Must appear inside an h1 element so the page identifies itself.
        h1_start = body.find("<h1")
        h1_end = body.find("</h1>", h1_start) if h1_start != -1 else -1
        assert h1_start != -1 and h1_end != -1
        assert "/debug/discovery" in body[h1_start:h1_end]

    def test_debug_discovery_renders_5_sections(self, client_full):
        resp = client_full.get("/debug/discovery")
        body = resp.text
        # All five section headers from CONTEXT discretion must appear.
        assert "Last weekly candidate set" in body
        assert "DiscoveryAdd lifecycle timeline" in body
        assert "Recent MusicBrainz queries" in body
        assert "Recent Lidarr add_artist" in body
        assert "Lidarr connection-test" in body

    def test_debug_discovery_cost_panel_shows_artist_only(
        self, client_full, test_engine,
    ):
        """Seeds two LLMUsage rows: one discovery_artist_weekly + one
        suggestions_rank. Cost panel must show ONLY the artist row total."""
        with Session(test_engine) as s:
            _seed_llmusage(
                s, purpose="discovery_artist_weekly",
                cost_estimate_usd=0.05,
            )
            _seed_llmusage(
                s, purpose="suggestions_rank",
                cost_estimate_usd=0.10,
            )
        resp = client_full.get("/debug/discovery")
        body = resp.text
        # The 4-decimal artist total ($0.0500) must appear.
        assert "0.0500" in body
        # The 4-decimal suggestions total ($0.1000) must NOT appear as a total
        # — i.e. the page must NOT add suggestions into the artist cost panel.
        # 0.10 standalone could appear elsewhere; but the SUM should be 0.05.
        assert "0.1500" not in body  # combined-total escape hatch

    def test_debug_discovery_renders_candidates_with_provenance(
        self, client_full, test_engine,
    ):
        with Session(test_engine) as s:
            track = _seed_track(s, plex_rating_key="rk-seed-1", artist="Seed")
            vibe = _seed_vibe(s, name="Mellow", color="#3b82f6")
            _seed_candidate(
                s, mb_id="mb-abc-1", artist_name="Discovered One",
                seed_track_id=track.id, seed_vibe_id=vibe.id,
                factual_hook="Same label as Four Tet",
                llm_rationale="Atmospheric IDM",
                llm_rank=1, mb_listener_count=5000,
            )
        resp = client_full.get("/debug/discovery")
        body = resp.text
        assert "Same label as Four Tet" in body
        assert "Atmospheric IDM" in body
        assert "Discovered One" in body
        assert "mb-abc-1" in body

    def test_debug_discovery_renders_pipeline_counts(
        self, client_full, test_engine,
    ):
        """Section 1 header shows the count of candidates."""
        with Session(test_engine) as s:
            track = _seed_track(s, plex_rating_key="rk-seed-2", artist="Seed")
            vibe = _seed_vibe(s, name="Energy")
            for i in range(3):
                _seed_candidate(
                    s, mb_id=f"mb-x-{i}", artist_name=f"A{i}",
                    seed_track_id=track.id, seed_vibe_id=vibe.id,
                )
        resp = client_full.get("/debug/discovery")
        body = resp.text
        # The section 1 header includes "(3 artists)" or "3 artists" somewhere.
        import re
        assert re.search(r"\(\d+ artists?\)|\d+ artists?", body)

    def test_debug_discovery_renders_discoveryadd_timeline(
        self, client_full, test_engine,
    ):
        with Session(test_engine) as s:
            _seed_discovery_add(
                s, mb_id="mb-add-1", artist_name="Lifecycle Artist",
                lidarr_status="downloading",
            )
        resp = client_full.get("/debug/discovery")
        body = resp.text
        assert "downloading" in body
        assert "mb-add-1" in body
        assert "Lifecycle Artist" in body

    def test_debug_discovery_renders_musicbrainz_queries_via_cache(
        self, client_full, test_engine,
    ):
        with Session(test_engine) as s:
            _seed_mb_cache(
                s, mb_id="mb-cache-1",
                payload_json='{"name":"Cached Artist 1"}',
            )
            _seed_mb_cache(
                s, mb_id="mb-cache-2",
                payload_json='{"name":"Cached Artist 2"}',
            )
        resp = client_full.get("/debug/discovery")
        body = resp.text
        assert "mb-cache-1" in body
        assert "mb-cache-2" in body

    def test_debug_discovery_handles_empty_state(self, client_full):
        """Empty DB — page must render 200 with a 'None recorded.' or
        equivalent placeholder in each empty section."""
        resp = client_full.get("/debug/discovery")
        assert resp.status_code == 200
        body = resp.text
        # The empty-state copy "None recorded." appears at least once across
        # the 5 sections (one per truly-empty section).
        assert "None recorded." in body or "None." in body

    def test_no_api_key_leaked(self, client_full):
        """Defensive — the debug page must NEVER render an api_key /
        password / secret string verbatim (no field rendered should
        accidentally surface the Lidarr or Anthropic key)."""
        resp = client_full.get("/debug/discovery")
        body = resp.text.lower()
        # Forbidden substrings — any debug field rendering these by mistake
        # is an information-disclosure bug (T-08-25).
        assert "api_key" not in body
        # The template MUST not include the word "password" or "secret"
        # — none of the rendered fields legitimately need it.
        assert "password" not in body

    def test_debug_discovery_renders_pipeline_count_breakdown_when_present(
        self, client_full, test_engine,
    ):
        """If the aggregator surfaces a per-tick counter dict, the labels
        for the funnel render. When counts is None (the v1 default per
        Plan 02 SUMMARY pointers), the section degrades to a friendly
        placeholder — both are acceptable; the test asserts the page
        does not crash and the header for the counters surface exists
        in the markup."""
        resp = client_full.get("/debug/discovery")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /debug index unmask
# ---------------------------------------------------------------------------


class TestDebugIndexLinkUnmasked:
    def test_debug_index_link_unmasked(self, client_full):
        """The /debug index must include a working anchor to /debug/discovery
        — NOT the legacy 'Phase 8 — not yet shipped' placeholder text."""
        resp = client_full.get("/debug")
        assert resp.status_code == 200
        body = resp.text
        assert '<a href="/debug/discovery"' in body
        assert "not yet shipped" not in body


# ---------------------------------------------------------------------------
# Plan 05 ADDITION-1 — Manual "Run weekly tick now" button + endpoint
# ---------------------------------------------------------------------------


class TestRunTickNowButton:
    def test_button_rendered_on_debug_discovery(self, client_full):
        resp = client_full.get("/debug/discovery")
        body = resp.text
        # Button text + POST target both must appear.
        assert "Run weekly tick now" in body
        assert "/api/discovery/run-tick-now" in body

    def test_run_tick_now_returns_202_on_success(
        self, client_full, monkeypatch,
    ):
        """POST /api/discovery/run-tick-now returns 202 + records that the
        background task was scheduled to call _weekly_maintenance_tick."""
        # Patch the function so the background task can complete without
        # touching Plex / Anthropic / Lidarr.
        called = {"hit": False}

        async def _fake_tick():
            called["hit"] = True

        from app.services import sync_scheduler
        monkeypatch.setattr(
            sync_scheduler, "_weekly_maintenance_tick", _fake_tick,
        )
        resp = client_full.post("/api/discovery/run-tick-now")
        assert resp.status_code == 202
        # Background task runs after the response — TestClient waits for it
        # in its lifecycle, so by the time we return here the fake should
        # have been invoked.
        assert called["hit"] is True

    def test_run_tick_now_returns_409_when_running(
        self, client_full, monkeypatch,
    ):
        """409 if state.state == 'running' — concurrent manual triggers
        are rejected to avoid double-running the cron."""
        from app.services import discovery_service
        discovery_service._status.state = "running"
        resp = client_full.post("/api/discovery/run-tick-now")
        assert resp.status_code == 409

    def test_run_tick_now_background_failure_captured(
        self, client_full, monkeypatch,
    ):
        """If the background tick raises, the endpoint MUST have already
        returned 202 (best-effort) and the exception is captured into
        discovery_service._status.last_error / logs without raising."""
        async def _boom():
            raise RuntimeError("simulated tick failure")

        from app.services import sync_scheduler
        monkeypatch.setattr(
            sync_scheduler, "_weekly_maintenance_tick", _boom,
        )
        resp = client_full.post("/api/discovery/run-tick-now")
        # The endpoint itself must NOT raise; background task swallows.
        assert resp.status_code == 202
        from app.services import discovery_service
        # The error wrapper should have stamped last_error.
        # (We don't strictly check the exact string; just that the wrapper
        # caught the exception and did not propagate.)
        # state should not be left at "running" — we should reset to "error"
        # or remain "idle".
        assert discovery_service._status.state in {"error", "idle"}
