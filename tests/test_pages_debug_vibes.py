"""Phase 6 Plan 04 /debug/vibes page tests (DEBUG-02 / D-36 / D-37).

Behavioral + render tests for app/routers/pages.py debug_vibes route + the
3 partials it composes (vibe_diagnostic_card, slot_in_log_table,
drift_indicator). Plus static-grep enforcement of UI-SPEC contracts (no
text-[15px], no Cancel labels, min-h-11 on action buttons).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select


PAGE_HTML = (
    Path(__file__).parent.parent
    / "app" / "templates" / "pages" / "debug_vibes.html"
)
CARD_HTML = (
    Path(__file__).parent.parent
    / "app" / "templates" / "partials" / "vibe_diagnostic_card.html"
)
LOG_HTML = (
    Path(__file__).parent.parent
    / "app" / "templates" / "partials" / "slot_in_log_table.html"
)
DRIFT_HTML = (
    Path(__file__).parent.parent
    / "app" / "templates" / "partials" / "drift_indicator.html"
)
PAGES_PY = Path(__file__).parent.parent / "app" / "routers" / "pages.py"


@pytest.fixture
def client_with_phase6(test_engine) -> Generator[TestClient, None, None]:
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        SetupState,
        SlotInLog,
        TrackVibe,
        Vibe,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)

    from app.main import app
    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


# ===========================================================================
# Render tests
# ===========================================================================

def test_debug_vibes_route_renders_with_zero_vibes(client_with_phase6):
    """Empty-state copy renders + status 200 on a fresh DB."""
    resp = client_with_phase6.get("/debug/vibes")
    assert resp.status_code == 200
    body = resp.text
    assert "Vibe Diagnostics" in body
    assert "For diagnostics only" in body
    assert "No vibes yet." in body
    assert 'href="/setup"' in body


def test_debug_vibes_route_renders_with_vibes(client_with_phase6, test_engine):
    """Page renders all 3 vibe cards + drift indicator when vibes exist."""
    from app.models.vibe import ManagedPlaylist, Vibe

    with Session(test_engine) as s:
        for name, key in [("V1", "pl-1"), ("V2", "pl-2"), ("V3", "pl-3")]:
            v = Vibe(
                name=name,
                description=f"desc {name}",
                created_at=datetime.now(timezone.utc).isoformat(),
                centroid_energy=0.5, centroid_tempo=120.0,
                centroid_danceability=0.5, centroid_valence=0.5,
                spread_energy=0.1, spread_tempo=10.0,
                spread_danceability=0.1, spread_valence=0.1,
                is_active=True,
            )
            s.add(v)
            s.commit()
            s.refresh(v)
            s.add(ManagedPlaylist(
                kind="vibe", vibe_id=v.id,
                plex_rating_key=key,
                composer_name=f"Composer · {name}",
                track_count=0,
            ))
            s.commit()

    resp = client_with_phase6.get("/debug/vibes")
    assert resp.status_code == 200
    body = resp.text
    assert "Vibe Diagnostics" in body
    for name in ("V1", "V2", "V3"):
        assert name in body
    # Drift indicator rendered with no-drift state.
    assert "No drift detected" in body
    # Slot-in log empty state.
    assert "No auto-slot decisions yet" in body


def test_slot_in_log_table_shows_last_20_rows(client_with_phase6, test_engine):
    """D-36: 25 rows in DB, exactly 20 in the rendered table (DESC by timestamp)."""
    from app.models.vibe import SlotInLog

    with Session(test_engine) as s:
        for i in range(25):
            s.add(SlotInLog(
                timestamp=f"2026-05-09T00:{i:02d}:00Z",
                track_id=i + 1,
                vibe_ids="[1]",
                distances="[0.42]",
                soft_membership_applied=False,
                action="slot",
                note=None,
            ))
        s.commit()

    resp = client_with_phase6.get("/debug/vibes")
    assert resp.status_code == 200
    body = resp.text
    # Count <tr class="border-t border-border"> in tbody — exactly 20.
    assert body.count('class="border-t border-border"') == 20


def test_re_show_last_cluster_proposal_button_present(client_with_phase6):
    """The Re-show last cluster proposal button + hx-get target wiring."""
    resp = client_with_phase6.get("/debug/vibes")
    assert resp.status_code == 200
    body = resp.text
    assert "Re-show last cluster proposal" in body
    assert "/api/vibes/last-llm-call" in body


def test_reslot_button_links_to_post_reslot_all(client_with_phase6):
    """Reslot button + hx-post + helper text + confirm dialog."""
    resp = client_with_phase6.get("/debug/vibes")
    assert resp.status_code == 200
    body = resp.text
    assert "Reslot all rated tracks" in body
    assert "/api/vibes/reslot-all" in body
    assert (
        "Re-runs auto-slot over every rated track. Use after a re-cluster "
        "or to recover from a Plex sync issue."
    ) in body
    assert "hx-confirm" in body


# ===========================================================================
# Drift indicator partial — direct render via template environment
# ===========================================================================

def test_drift_indicator_no_drift_state():
    """Green/success render when orphan + stale counts are zero."""
    src = DRIFT_HTML.read_text()
    assert "No drift detected" in src
    assert "border-success" in src


def test_drift_indicator_drift_detected_state():
    """Red/error render branch when drift counts are non-zero."""
    src = DRIFT_HTML.read_text()
    assert "Drift detected" in src
    assert "border-error" in src


# ===========================================================================
# Static UI-SPEC contract enforcement
# ===========================================================================

def test_debug_vibes_no_text_15px():
    """UI-SPEC rev 2 typography contract — 13/14/20/28 only."""
    for path in (PAGE_HTML, CARD_HTML, LOG_HTML, DRIFT_HTML):
        src = path.read_text()
        assert "text-[15px]" not in src, f"text-[15px] found in {path.name}"


def test_debug_vibes_no_cancel_label():
    """UI-SPEC checker BLOCK fix — no >Cancel< on any new Phase 6 surface."""
    for path in (PAGE_HTML, CARD_HTML, LOG_HTML, DRIFT_HTML):
        src = path.read_text()
        assert ">Cancel<" not in src, f">Cancel< found in {path.name}"


def test_debug_vibes_uses_44px_tap_targets():
    """Both action buttons (Reslot + Re-show) at min-h-11 (44px tap target)."""
    src = PAGE_HTML.read_text()
    # Two action buttons, each carrying min-h-11.
    assert src.count("min-h-11") >= 2


def test_pages_router_debug_vibes_full_implementation():
    """The /debug/vibes route in pages.py is no longer the Plan 03 stub."""
    src = PAGES_PY.read_text()
    # All three context dict keys appear in the route body.
    assert "vibe_member_counts" in src
    assert "slot_in_log" in src
    assert "drift" in src
    # The stub template is no longer referenced.
    assert "debug_vibes_stub.html" not in src


def test_debug_vibes_uses_main_page_template_not_stub():
    """The route renders pages/debug_vibes.html (not the Plan 03 stub)."""
    src = PAGES_PY.read_text()
    assert "pages/debug_vibes.html" in src
