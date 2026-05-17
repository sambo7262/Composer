"""Phase 8 Plan 04 Task 1 — /api/discovery endpoints.

Tests `app/routers/api_discovery.py` + the `discovery_service` helpers it
delegates to:

- ``add_artist_to_lidarr`` (D-D4 / DISC-05) — reads ServiceConfig.extras
  + invokes ``lidarr_client.add_artist`` + inserts DiscoveryAdd.
- ``handle_dismiss_artist`` (D-D5) — writes DiscoveryDismissed (UNIQUE
  on mb_id so re-dismiss is a no-op).
- ``get_lidarr_status_for_add`` lazy poll with 5-min cache (D-C3).
- D-C3 / Pitfall 14 stale-warning chip when a ``searching``/``pending``
  add is >48h old.

Router behaviours:

- POST /api/discovery/{mb_id}/add — invokes lidarr_client with saved
  profiles, inserts DiscoveryAdd, returns status-row partial.
- POST /api/discovery/{mb_id}/dismiss — writes row, returns 200 empty.
- GET  /api/discovery/{mb_id}/status-row — lazy-poll cached 5min.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def client_full(test_engine) -> Generator[TestClient, None, None]:
    """Mirrors the test_api_suggestions client fixture; includes Phase 8
    discovery models so router endpoints touch real tables."""
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
def _clear_lidarr_status_cache():
    """Reset the module-level Lidarr status cache between tests so 5-min
    cache assertions are deterministic."""
    from app.services import discovery_service
    cache = getattr(discovery_service, "_lidarr_status_cache", None)
    if isinstance(cache, dict):
        cache.clear()
    yield
    cache = getattr(discovery_service, "_lidarr_status_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def _save_lidarr_config(test_engine):
    """Helper: persist a Lidarr ServiceConfig row with all 5 extras keys."""
    from app.services.settings_service import save_setting

    with Session(test_engine) as session:
        save_setting(
            session,
            "lidarr",
            "http://lidarr:8686",
            "test-api-key-abc",
            {
                "quality_profile_id": "1",
                "quality_profile_name": "FLAC",
                "metadata_profile_id": "2",
                "metadata_profile_name": "Standard",
                "root_folder_path": "/music",
            },
        )


def _seed_vibe(test_engine, vibe_id=None, name="Chill", color="#3b82f6", is_active=True):
    from app.models.vibe import Vibe

    with Session(test_engine) as session:
        v = Vibe(
            id=vibe_id, name=name, description=None,
            is_active=is_active, color=color,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(v)
        session.commit()
        session.refresh(v)
        return v.id


def _seed_track(test_engine, plex_rating_key="rk-1", title="X", artist="Y"):
    from app.models.track import Track

    with Session(test_engine) as session:
        t = Track(
            plex_rating_key=plex_rating_key, title=title,
            artist=artist, album="Z", duration_ms=120_000,
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


def _seed_candidate(test_engine, mb_id, seed_vibe_id, seed_track_id,
                    artist_name="Test Artist", factual_hook="A factual hook",
                    llm_rationale="An LLM rationale", llm_rank=1):
    from app.models.discovery import DiscoveryCandidate

    with Session(test_engine) as session:
        session.add(DiscoveryCandidate(
            mb_id=mb_id, artist_name=artist_name,
            seed_track_id=seed_track_id, seed_vibe_id=seed_vibe_id,
            mb_listener_count=42_000, popularity_gate_pass=True,
            llm_rank=llm_rank, llm_rationale=llm_rationale,
            factual_hook=factual_hook,
            created_at=datetime.now(timezone.utc).isoformat(),
        ))
        session.commit()


def _seed_dismiss(test_engine, mb_id, artist_name="Test Artist"):
    from app.models.discovery import DiscoveryDismissed

    with Session(test_engine) as session:
        session.add(DiscoveryDismissed(
            mb_id=mb_id, artist_name=artist_name,
            dismissed_at=datetime.now(timezone.utc).isoformat(),
        ))
        session.commit()


def _seed_discovery_add(test_engine, mb_id, lidarr_artist_id=None,
                        added_at=None, lidarr_status="searching",
                        vibe_slotted_at=None, artist_name="Test Artist"):
    from app.models.discovery import DiscoveryAdd

    with Session(test_engine) as session:
        session.add(DiscoveryAdd(
            mb_id=mb_id, artist_name=artist_name,
            added_at=added_at or datetime.now(timezone.utc).isoformat(),
            lidarr_artist_id=lidarr_artist_id, lidarr_status=lidarr_status,
            vibe_slotted_at=vibe_slotted_at,
        ))
        session.commit()


def _seed_weekly_tick(test_engine):
    from app.models.discovery import WeeklyCronState

    with Session(test_engine) as session:
        existing = session.get(WeeklyCronState, 1)
        if existing is None:
            session.add(WeeklyCronState(
                id=1, last_tick_at=datetime.now(timezone.utc).isoformat(),
            ))
        else:
            existing.last_tick_at = datetime.now(timezone.utc).isoformat()
            session.add(existing)
        session.commit()


# ----------------------------------------------------------------------
# Empty-state rendering on /discover
# ----------------------------------------------------------------------

class TestDiscoverEmptyStates:
    def test_discover_renders_empty_when_no_candidates(
        self, client_full, test_engine,
    ):
        """Lidarr configured + vibes exist + first tick run → 'next Sunday'
        copy or the 'try again after next Sunday' copy."""
        _save_lidarr_config(test_engine)
        _seed_vibe(test_engine)
        _seed_weekly_tick(test_engine)

        resp = client_full.get("/discover")
        assert resp.status_code == 200
        # When first tick has run but no candidates, message says "try
        # again after next Sunday".
        assert (
            "try again after next Sunday" in resp.text
            or "Your first discoveries land Sunday at 03:00 UTC" in resp.text
        )

    def test_discover_renders_empty_when_lidarr_unconfigured(
        self, client_full, test_engine,
    ):
        _seed_vibe(test_engine)
        resp = client_full.get("/discover")
        assert resp.status_code == 200
        assert "Configure Lidarr" in resp.text

    def test_discover_renders_empty_when_no_vibes(
        self, client_full, test_engine,
    ):
        _save_lidarr_config(test_engine)
        resp = client_full.get("/discover")
        assert resp.status_code == 200
        assert "Finish the" in resp.text and "setup" in resp.text

    def test_discover_first_tick_not_run_message(
        self, client_full, test_engine,
    ):
        _save_lidarr_config(test_engine)
        _seed_vibe(test_engine)
        # No WeeklyCronState row at all (or last_tick_at=None) → first-tick
        # message.
        resp = client_full.get("/discover")
        assert resp.status_code == 200
        # Message now renders the next Sun 03:00 UTC tick in LA time, e.g.
        # "Saturday May 17 at 8:00 PM PDT" (DST-aware). Pin the prefix + PT marker.
        assert "first discoveries land" in resp.text
        body = resp.text
        assert ("PDT" in body or "PST" in body)
        # Ensure no UTC leakage in the immediate empty-state copy.
        idx = body.index("first discoveries land")
        snippet = body[idx:idx + 200]
        assert "UTC" not in snippet


# ----------------------------------------------------------------------
# Discover renders vibe-grouped sections + dismiss/lifecycle filtering
# ----------------------------------------------------------------------

class TestDiscoverRendering:
    def test_discover_renders_vibe_sections(
        self, client_full, test_engine,
    ):
        _save_lidarr_config(test_engine)
        _seed_weekly_tick(test_engine)
        vibe1 = _seed_vibe(test_engine, name="Energetic", color="#f59e0b")
        vibe2 = _seed_vibe(test_engine, name="Mellow", color="#3b82f6")
        track1 = _seed_track(test_engine, plex_rating_key="rk-1")
        track2 = _seed_track(test_engine, plex_rating_key="rk-2")

        _seed_candidate(
            test_engine, mb_id="mb-1", seed_vibe_id=vibe1,
            seed_track_id=track1, artist_name="Aphex Twin",
            factual_hook="Same label as Boards of Canada",
        )
        _seed_candidate(
            test_engine, mb_id="mb-2", seed_vibe_id=vibe1,
            seed_track_id=track1, artist_name="Autechre",
            factual_hook="Adjacent to Aphex Twin",
        )
        _seed_candidate(
            test_engine, mb_id="mb-3", seed_vibe_id=vibe2,
            seed_track_id=track2, artist_name="Tycho",
            factual_hook="Ambient electronic",
        )

        resp = client_full.get("/discover")
        assert resp.status_code == 200
        body = resp.text
        # 2 vibe section headers — "For your <vibe_name> vibe"
        assert "Energetic" in body
        assert "Mellow" in body
        # Vibe color inline-style on the section header — border-left
        assert "#f59e0b" in body
        assert "#3b82f6" in body
        # Each artist card shows the factual_hook string
        assert "Same label as Boards of Canada" in body
        assert "Adjacent to Aphex Twin" in body
        assert "Ambient electronic" in body

    def test_discover_subtracts_dismissed_at_read_time(
        self, client_full, test_engine,
    ):
        _save_lidarr_config(test_engine)
        _seed_weekly_tick(test_engine)
        vibe = _seed_vibe(test_engine)
        track = _seed_track(test_engine)
        _seed_candidate(
            test_engine, mb_id="X", seed_vibe_id=vibe, seed_track_id=track,
            artist_name="Dismissed Artist", factual_hook="Hook X",
        )
        _seed_dismiss(test_engine, mb_id="X", artist_name="Dismissed Artist")

        resp = client_full.get("/discover")
        assert "Dismissed Artist" not in resp.text
        assert "Hook X" not in resp.text

    def test_discover_hides_completed_adds(
        self, client_full, test_engine,
    ):
        _save_lidarr_config(test_engine)
        _seed_weekly_tick(test_engine)
        vibe = _seed_vibe(test_engine)
        track = _seed_track(test_engine)
        _seed_candidate(
            test_engine, mb_id="COMPLETED", seed_vibe_id=vibe,
            seed_track_id=track, artist_name="Completed Artist",
            factual_hook="Hook COMPLETED",
        )
        _seed_discovery_add(
            test_engine, mb_id="COMPLETED",
            vibe_slotted_at=datetime.now(timezone.utc).isoformat(),
            artist_name="Completed Artist",
        )

        resp = client_full.get("/discover")
        assert "Completed Artist" not in resp.text
        assert "Hook COMPLETED" not in resp.text

    def test_discover_renders_status_row_for_in_flight_adds(
        self, client_full, test_engine,
    ):
        _save_lidarr_config(test_engine)
        _seed_weekly_tick(test_engine)
        vibe = _seed_vibe(test_engine)
        track = _seed_track(test_engine)
        _seed_candidate(
            test_engine, mb_id="IN-FLIGHT", seed_vibe_id=vibe,
            seed_track_id=track, artist_name="In Flight Artist",
            factual_hook="Hook IF",
        )
        # DiscoveryAdd present but vibe_slotted_at is NULL → still
        # in-flight; status row renders instead of (or alongside) the
        # candidate card.
        _seed_discovery_add(
            test_engine, mb_id="IN-FLIGHT", vibe_slotted_at=None,
            artist_name="In Flight Artist",
        )

        resp = client_full.get("/discover")
        # Status-row for the artist appears
        assert "In Flight Artist" in resp.text
        # Status-row partial contains the literal "Lidarr:" prefix
        assert "Lidarr:" in resp.text

    def test_artist_card_tap_to_expand_uses_alpine_xshow(self):
        """Source-level: discover_artist_card.html uses Alpine x-show /
        x-data."""
        from pathlib import Path
        tmpl = Path(__file__).resolve().parent.parent / "app" / "templates" / "partials" / "discover_artist_card.html"
        body = tmpl.read_text()
        assert 'x-data="{ expanded: false }"' in body
        assert '@click="expanded = !expanded"' in body
        assert 'x-show="expanded"' in body


# ----------------------------------------------------------------------
# POST /api/discovery/{mb_id}/add — lidarr_client invocation + DiscoveryAdd
# ----------------------------------------------------------------------

class TestAddToLidarr:
    def test_add_to_lidarr_endpoint_invokes_lidarr_client_with_saved_profiles(
        self, client_full, test_engine, monkeypatch,
    ):
        _save_lidarr_config(test_engine)
        vibe = _seed_vibe(test_engine)
        track = _seed_track(test_engine)
        _seed_candidate(
            test_engine, mb_id="abc-mbid", seed_vibe_id=vibe,
            seed_track_id=track, artist_name="Mocked Artist",
        )

        mock_add = AsyncMock(return_value={"success": True, "lidarr_artist_id": 99})
        from app.services import lidarr_client
        monkeypatch.setattr(lidarr_client, "add_artist", mock_add)

        resp = client_full.post("/api/discovery/abc-mbid/add")
        assert resp.status_code == 200
        mock_add.assert_awaited_once()
        # kwargs reflect the saved-extras shape
        kwargs = mock_add.await_args.kwargs
        assert kwargs["mb_id"] == "abc-mbid"
        assert kwargs["quality_profile_id"] == 1  # int — coerced from "1"
        assert kwargs["metadata_profile_id"] == 2  # int — coerced from "2"
        assert kwargs["root_dir"] == "/music"
        assert kwargs["url"] == "http://lidarr:8686"
        assert kwargs["api_key"] == "test-api-key-abc"

    def test_add_to_lidarr_inserts_discovery_add_row(
        self, client_full, test_engine, monkeypatch,
    ):
        _save_lidarr_config(test_engine)
        vibe = _seed_vibe(test_engine)
        track = _seed_track(test_engine)
        _seed_candidate(
            test_engine, mb_id="add-mbid", seed_vibe_id=vibe,
            seed_track_id=track, artist_name="Inserted Artist",
        )

        from app.services import lidarr_client
        monkeypatch.setattr(
            lidarr_client, "add_artist",
            AsyncMock(return_value={"success": True, "lidarr_artist_id": 42}),
        )

        client_full.post("/api/discovery/add-mbid/add")
        from app.models.discovery import DiscoveryAdd
        with Session(test_engine) as session:
            row = session.exec(
                select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "add-mbid")
            ).first()
            assert row is not None
            assert row.lidarr_artist_id == 42
            assert row.added_at is not None
            assert row.artist_name == "Inserted Artist"

    def test_add_to_lidarr_returns_status_row_partial(
        self, client_full, test_engine, monkeypatch,
    ):
        _save_lidarr_config(test_engine)
        vibe = _seed_vibe(test_engine)
        track = _seed_track(test_engine)
        _seed_candidate(
            test_engine, mb_id="render-mbid", seed_vibe_id=vibe,
            seed_track_id=track, artist_name="Rendered Artist",
        )
        from app.services import lidarr_client
        monkeypatch.setattr(
            lidarr_client, "add_artist",
            AsyncMock(return_value={"success": True, "lidarr_artist_id": 7}),
        )

        resp = client_full.post("/api/discovery/render-mbid/add")
        assert resp.status_code == 200
        body = resp.text
        assert "Rendered Artist" in body
        # Initial post-add status
        assert "Lidarr:" in body
        # Use of "searching" or "Pending" — the initial state string
        assert "searching" in body.lower() or "pending" in body.lower()

    def test_add_to_lidarr_fails_gracefully_when_lidarr_unconfigured(
        self, client_full, test_engine,
    ):
        # No Lidarr settings persisted.
        resp = client_full.post("/api/discovery/anywhere/add")
        assert resp.status_code == 200
        assert "Lidarr not configured" in resp.text


# ----------------------------------------------------------------------
# POST /api/discovery/{mb_id}/dismiss
# ----------------------------------------------------------------------

class TestDismissEndpoint:
    def test_dismiss_endpoint_writes_discoverydismissed(
        self, client_full, test_engine,
    ):
        resp = client_full.post("/api/discovery/xyz-mbid/dismiss")
        assert resp.status_code == 200
        from app.models.discovery import DiscoveryDismissed
        with Session(test_engine) as session:
            row = session.exec(
                select(DiscoveryDismissed).where(
                    DiscoveryDismissed.mb_id == "xyz-mbid"
                )
            ).first()
            assert row is not None
            assert row.dismissed_at is not None

    def test_dismiss_endpoint_returns_empty_200(
        self, client_full, test_engine,
    ):
        resp = client_full.post("/api/discovery/empty-mbid/dismiss")
        assert resp.status_code == 200
        assert resp.text == ""

    def test_dismiss_is_idempotent(self, client_full, test_engine):
        r1 = client_full.post("/api/discovery/dup-mbid/dismiss")
        r2 = client_full.post("/api/discovery/dup-mbid/dismiss")
        assert r1.status_code == 200
        assert r2.status_code == 200
        from app.models.discovery import DiscoveryDismissed
        with Session(test_engine) as session:
            rows = list(session.exec(
                select(DiscoveryDismissed).where(
                    DiscoveryDismissed.mb_id == "dup-mbid"
                )
            ).all())
            assert len(rows) == 1


# ----------------------------------------------------------------------
# GET /api/discovery/{mb_id}/status-row — lazy poll w/ 5-min cache
# ----------------------------------------------------------------------

class TestStatusRowEndpoint:
    def test_get_lidarr_status_endpoint_lazy_polls_with_5min_cache(
        self, client_full, test_engine, monkeypatch,
    ):
        _save_lidarr_config(test_engine)
        _seed_discovery_add(
            test_engine, mb_id="poll-mbid", lidarr_artist_id=1, lidarr_status="searching",
        )

        call_log: list = []

        async def _fake_history(*args, **kwargs):
            call_log.append(("history", args, kwargs))
            return [{"artistId": 1, "eventType": "grabbed"}]

        from app.services import lidarr_client
        monkeypatch.setattr(lidarr_client, "get_recent_history", _fake_history)

        r1 = client_full.get("/api/discovery/poll-mbid/status-row")
        assert r1.status_code == 200
        r2 = client_full.get("/api/discovery/poll-mbid/status-row")
        assert r2.status_code == 200
        # Cache hit: second call MUST NOT trigger another history fetch.
        assert len(call_log) == 1


# ----------------------------------------------------------------------
# Router registration
# ----------------------------------------------------------------------

class TestRouterRegistration:
    def test_api_discovery_router_registered(self, client_full):
        from app.main import app
        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/discovery/{mb_id}/add" in route_paths
        assert "/api/discovery/{mb_id}/dismiss" in route_paths
        assert "/api/discovery/{mb_id}/status-row" in route_paths


# ----------------------------------------------------------------------
# D-C3 / Pitfall 14 stale-warning chip
# ----------------------------------------------------------------------

class TestStaleWarningChip:
    def test_status_row_shows_stale_warning_after_2_days(
        self, client_full, test_engine, monkeypatch,
    ):
        _save_lidarr_config(test_engine)
        # added_at = 3 days ago, lidarr_status="searching"
        three_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=3)
        ).isoformat()
        _seed_discovery_add(
            test_engine, mb_id="stale-mbid", lidarr_artist_id=10,
            added_at=three_days_ago, lidarr_status="searching",
            artist_name="Stale Artist",
        )

        # Monkeypatch the service helper so the status string remains
        # "searching" — the stale chip should still fire.
        from app.services import discovery_service

        async def _fake_status(mb_id, lidarr_artist_id):  # noqa: ARG001
            return "searching"

        monkeypatch.setattr(
            discovery_service, "get_lidarr_status_for_add", _fake_status,
        )

        resp = client_full.get("/api/discovery/stale-mbid/status-row")
        assert resp.status_code == 200
        body = resp.text
        assert "No releases found after 3d" in body
        assert 'data-stale-warning="true"' in body

    def test_status_row_no_warning_when_fresh(
        self, client_full, test_engine, monkeypatch,
    ):
        _save_lidarr_config(test_engine)
        one_hour_ago = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        _seed_discovery_add(
            test_engine, mb_id="fresh-mbid", lidarr_artist_id=11,
            added_at=one_hour_ago, lidarr_status="searching",
            artist_name="Fresh Artist",
        )
        from app.services import discovery_service

        async def _fake_status(mb_id, lidarr_artist_id):  # noqa: ARG001
            return "searching"

        monkeypatch.setattr(
            discovery_service, "get_lidarr_status_for_add", _fake_status,
        )
        resp = client_full.get("/api/discovery/fresh-mbid/status-row")
        assert resp.status_code == 200
        assert 'data-stale-warning="true"' not in resp.text
        assert "No releases found after" not in resp.text

    def test_status_row_no_warning_when_not_searching(
        self, client_full, test_engine, monkeypatch,
    ):
        _save_lidarr_config(test_engine)
        five_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=5)
        ).isoformat()
        _seed_discovery_add(
            test_engine, mb_id="downloading-mbid", lidarr_artist_id=12,
            added_at=five_days_ago, lidarr_status="downloading",
            artist_name="Downloading Artist",
        )
        from app.services import discovery_service

        async def _fake_status(mb_id, lidarr_artist_id):  # noqa: ARG001
            return "downloading"

        monkeypatch.setattr(
            discovery_service, "get_lidarr_status_for_add", _fake_status,
        )
        resp = client_full.get("/api/discovery/downloading-mbid/status-row")
        assert resp.status_code == 200
        # No stale chip because status isn't searching/pending.
        assert 'data-stale-warning="true"' not in resp.text


# ============================================================================
# GET /api/discovery/{mb_id}/top-tracks — UAT-iter lazy-load on expand
# ============================================================================


class TestTopTracksEndpoint:
    def test_top_tracks_renders_list(
        self, client_full, test_engine, monkeypatch,
    ):
        from app.services import listenbrainz_client

        async def _fake_top(mbid, limit=5):
            return [
                {"recording_name": "Karma Police", "release_name": "OK Computer", "recording_mbid": "a"},
                {"recording_name": "Creep", "release_name": "Pablo Honey", "recording_mbid": "b"},
            ]
        monkeypatch.setattr(
            listenbrainz_client, "get_top_recordings_for_artist", _fake_top,
        )

        resp = client_full.get("/api/discovery/some-mbid/top-tracks")
        assert resp.status_code == 200
        body = resp.text
        assert "Karma Police" in body
        assert "OK Computer" in body
        assert "Creep" in body

    def test_top_tracks_empty_response_renders_empty_body(
        self, client_full, test_engine, monkeypatch,
    ):
        """Empty list from ListenBrainz → empty partial (no error, no list)."""
        from app.services import listenbrainz_client

        async def _fake_top(mbid, limit=5):
            return []
        monkeypatch.setattr(
            listenbrainz_client, "get_top_recordings_for_artist", _fake_top,
        )

        resp = client_full.get("/api/discovery/some-mbid/top-tracks")
        assert resp.status_code == 200
        # No <ul> rendered when tracks is empty.
        assert "<ul" not in resp.text

    def test_top_tracks_handler_failure_returns_empty(
        self, client_full, test_engine, monkeypatch,
    ):
        """If the LB helper raises, the route still returns 200 with empty body
        — the UI must never break the page on a third-party flake."""
        from app.services import listenbrainz_client

        async def _boom(mbid, limit=5):
            raise RuntimeError("network down")
        monkeypatch.setattr(
            listenbrainz_client, "get_top_recordings_for_artist", _boom,
        )

        resp = client_full.get("/api/discovery/some-mbid/top-tracks")
        assert resp.status_code == 200
        assert "<ul" not in resp.text
