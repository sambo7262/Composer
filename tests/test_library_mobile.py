"""Phase 8 Plan 04 Task 2 — Library page mobile-first rewrite (UI-07/UI-08 / D-D1).

Tests the rewritten ``app/templates/pages/library.html`` + 4 new partials:

- ``partials/track_card.html`` — mobile card-per-track item
- ``partials/library_filter_chips.html`` — filter chips row
- ``partials/library_sort_sheet.html`` — bottom-sheet sort dropdown
- ``partials/library_results_wrapper.html`` — single HTMX swap target
  wrapping BOTH the mobile card list AND the md+ wide table so the
  filter / sort / search handlers only need to swap one node.

Also pins the extended ``api_library.list_tracks`` endpoint contract:
- ``?filter=analyzed|unanalyzed|rated|all`` allowlist
- ``?sort=title|artist|added|rating`` allowlist
- Both fall back to defaults on invalid values (per Phase 02
  T-02-05/T-02-07 security pattern)
- Returns ``partials/library_results_wrapper.html`` so a single swap
  covers both breakpoints
"""
from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"


def _read(rel: str) -> str:
    return (TEMPLATES_DIR / rel).read_text()


@pytest.fixture
def client_full(test_engine) -> Generator[TestClient, None, None]:
    """TestClient with full lifespan + every Phase model registered so
    api_library.list_tracks can query Track with filter/sort applied."""
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
        CostMeterBaseline, DiscoveryAdd, DiscoveryCandidate,
        DiscoveryDismissed, MusicBrainzCache, WeeklyCronState,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)
    from app.main import app
    with TestClient(app) as c:
        yield c
    SQLModel.metadata.drop_all(test_engine)


def _seed_tracks(test_engine, items):
    """items: list of dicts with title, artist, album, year, user_rating, energy."""
    from sqlmodel import Session
    from app.models.track import Track

    with Session(test_engine) as session:
        for i, t in enumerate(items, start=1):
            session.add(Track(
                plex_rating_key=f"rk-{i}", title=t["title"],
                artist=t["artist"], album=t.get("album", ""),
                duration_ms=120_000, year=t.get("year"),
                user_rating=t.get("user_rating"),
                energy=t.get("energy"),
                added_at=t.get("added_at"),
            ))
        session.commit()


# ----------------------------------------------------------------------
# library.html — top-level shell invariants
# ----------------------------------------------------------------------

class TestLibraryShell:
    def test_library_uses_dvh_not_screen(self):
        body = _read("pages/library.html")
        # h-screen / 100vh forbidden (Pitfall 17). If any viewport-height
        # utility is used it must be h-dvh / min-h-dvh.
        assert "h-screen" not in body, (
            "library.html must NOT use h-screen — mobile-first conventions "
            "require h-dvh / min-h-dvh"
        )
        assert "100vh" not in body, (
            "library.html must NOT use 100vh — use h-dvh / min-h-dvh instead"
        )

    def test_library_card_list_present(self):
        body = _read("pages/library.html")
        # library.html itself doesn't render track_card directly — it
        # includes library_results_wrapper which in turn includes
        # track_card.html. So check transitively.
        wrapper = _read("partials/library_results_wrapper.html")
        assert "track_card.html" in wrapper, (
            "library_results_wrapper.html must include partials/track_card.html "
            "for the sub-md card list"
        )
        # The page must reach the wrapper.
        assert "library_results_wrapper.html" in body, (
            "pages/library.html must include partials/library_results_wrapper.html"
        )

    def test_existing_sync_banner_still_included(self):
        body = _read("pages/library.html")
        assert "sync_banner.html" in body, (
            "library.html must preserve the sync_banner include (T-08-?? "
            "regression guard — Phase 2 surface still needs to render)"
        )

    def test_existing_analysis_banner_still_included(self):
        body = _read("pages/library.html")
        assert "analysis_banner.html" in body, (
            "library.html must preserve the analysis_banner include"
        )

    def test_search_has_min_44px_height(self):
        body = _read("pages/library.html")
        # The search <input> tag needs min-h-11 or h-11. We do a simple
        # substring check that one of those tokens appears on or near the
        # input element.
        assert "min-h-11" in body or "h-11" in body, (
            "library.html search input must satisfy the 44px tap-target "
            "invariant (Pitfall 17 / UI-04)"
        )

    def test_search_input_uses_alpine_morph_compatible_swap(self):
        body = _read("pages/library.html")
        # The HTMX swap must be outerHTML so library_results_wrapper.html
        # picks up its own id back. Plus the body-level alpine-morph
        # extension covers state preservation across swaps.
        assert 'hx-swap="outerHTML"' in body, (
            "library.html HTMX swap must be outerHTML on the search "
            "input so the wrapper id survives the swap"
        )


# ----------------------------------------------------------------------
# track_card.html — mobile compact-card invariants
# ----------------------------------------------------------------------

class TestTrackCardPartial:
    def test_track_card_has_min_44px_tap_target(self):
        body = _read("partials/track_card.html")
        assert "min-h-11" in body, (
            "track_card.html must mark the primary tappable element "
            "with min-h-11 (44px tap target — Pitfall 17 / UI-04)"
        )


# ----------------------------------------------------------------------
# library_filter_chips.html — UI-07 filter chips
# ----------------------------------------------------------------------

class TestFilterChips:
    def test_filter_chips_render_three_options(self):
        body = _read("partials/library_filter_chips.html")
        for label in ("Analyzed", "Unanalyzed", "Rated"):
            assert label in body, (
                f"library_filter_chips.html missing label: {label}"
            )

    def test_filter_chip_active_state_uses_alpine(self):
        body = _read("partials/library_filter_chips.html")
        # Alpine state: either an x-data on the wrapper or x-model on a
        # hidden input that HTMX picks up.
        assert "x-data" in body or "x-model" in body, (
            "library_filter_chips.html must drive active state via "
            "Alpine x-data/x-model (UI-07 — no inline state in URL)"
        )


# ----------------------------------------------------------------------
# library_sort_sheet.html — UI-07 bottom sheet
# ----------------------------------------------------------------------

class TestSortSheet:
    def test_sort_sheet_is_bottom_sheet(self):
        body = _read("partials/library_sort_sheet.html")
        # Bottom sheet must respect iOS home-indicator gutter.
        assert "safe-area-inset-bottom" in body, (
            "library_sort_sheet.html must use env(safe-area-inset-bottom) "
            "(Pitfall 16 — iOS home-indicator gutter)"
        )
        assert "x-show" in body, (
            "library_sort_sheet.html must use Alpine x-show for the "
            "open/closed state machine"
        )
        # Sort options
        for label in ("Title", "Artist", "Added", "Rating"):
            assert label in body, (
                f"library_sort_sheet.html missing sort option: {label}"
            )


# ----------------------------------------------------------------------
# library_results_wrapper.html — single swap target for both breakpoints
# ----------------------------------------------------------------------

class TestResultsWrapper:
    def test_results_wrapper_has_library_results_id(self):
        body = _read("partials/library_results_wrapper.html")
        assert 'id="library-results"' in body, (
            "library_results_wrapper.html must root at id='library-results' "
            "— the HTMX swap target for filter/sort/search"
        )

    def test_results_wrapper_has_track_card_list_id(self):
        body = _read("partials/library_results_wrapper.html")
        assert 'id="track-card-list"' in body, (
            "library_results_wrapper.html must mark the mobile card list "
            "with id='track-card-list' for the alpine-morph swap target"
        )

    def test_results_wrapper_renders_both_breakpoints(self):
        body = _read("partials/library_results_wrapper.html")
        # Must include BOTH track_card.html (sub-md) AND track_table.html
        # (md+) so a single swap covers both viewports.
        assert "track_card.html" in body
        assert "track_table.html" in body


# ----------------------------------------------------------------------
# /api/library/tracks — extended filter/sort contract
# ----------------------------------------------------------------------

class TestApiLibraryFilterSort:
    def test_api_library_tracks_accepts_filter_rated(
        self, client_full, test_engine,
    ):
        _seed_tracks(test_engine, [
            {"title": "Rated", "artist": "A", "user_rating": 8},
            {"title": "Unrated", "artist": "B", "user_rating": 0},
            {"title": "AlsoUnrated", "artist": "C", "user_rating": None},
        ])
        resp = client_full.get("/api/library/tracks?filter=rated")
        assert resp.status_code == 200
        assert "Rated" in resp.text
        assert "Unrated" not in resp.text
        assert "AlsoUnrated" not in resp.text

    def test_api_library_tracks_accepts_filter_analyzed(
        self, client_full, test_engine,
    ):
        _seed_tracks(test_engine, [
            {"title": "Analyzed", "artist": "A", "energy": 0.5},
            {"title": "Unanalyzed", "artist": "B", "energy": None},
        ])
        resp = client_full.get("/api/library/tracks?filter=analyzed")
        assert resp.status_code == 200
        assert "Analyzed" in resp.text
        assert "Unanalyzed" not in resp.text

    def test_api_library_tracks_accepts_sort_rating(
        self, client_full, test_engine,
    ):
        _seed_tracks(test_engine, [
            {"title": "LowRated", "artist": "A", "user_rating": 2},
            {"title": "HighRated", "artist": "B", "user_rating": 9},
            {"title": "MidRated", "artist": "C", "user_rating": 5},
        ])
        resp = client_full.get("/api/library/tracks?sort=rating")
        assert resp.status_code == 200
        # HighRated should appear before LowRated in the rendered HTML
        # (DESC sort on rating).
        idx_high = resp.text.find("HighRated")
        idx_low = resp.text.find("LowRated")
        assert idx_high > -1 and idx_low > -1
        assert idx_high < idx_low

    def test_api_library_tracks_invalid_filter_falls_back_to_all(
        self, client_full, test_engine,
    ):
        _seed_tracks(test_engine, [
            {"title": "Visible", "artist": "A", "user_rating": 8},
            {"title": "AlsoVisible", "artist": "B", "user_rating": 0},
        ])
        # Allowlist clamp: unknown values fall back to "all" (T-02-05
        # pattern). Pages stay 200, both rows render.
        resp = client_full.get("/api/library/tracks?filter=xss-payload")
        assert resp.status_code == 200
        assert "Visible" in resp.text
        assert "AlsoVisible" in resp.text

    def test_api_library_returns_results_wrapper_partial(
        self, client_full, test_engine,
    ):
        _seed_tracks(test_engine, [
            {"title": "Solo", "artist": "A", "user_rating": 6},
        ])
        resp = client_full.get("/api/library/tracks")
        assert resp.status_code == 200
        # Single swap target: response root must contain library-results id.
        assert 'id="library-results"' in resp.text
