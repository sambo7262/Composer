"""Phase 6 Plan 04 settings page tests (D-20, D-37, VIBE-11).

Static-grep tests for the Vibes section additions to settings.html and the
new recluster_modal.html partial. UI-SPEC checker BLOCK fix is enforced:
the dismiss button reads "Keep current vibes" (NOT "Cancel") and the
Re-cluster button uses bg-accent (NOT bg-error — non-destructive at this
point because refinement loop on /setup/propose can still bail).

Plus one render test against the live FastAPI app to verify GET /settings
returns 200 with all new elements present.
"""
from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel


SETTINGS_HTML = Path(__file__).parent.parent / "app" / "templates" / "pages" / "settings.html"
MODAL_HTML = (
    Path(__file__).parent.parent
    / "app" / "templates" / "partials" / "recluster_modal.html"
)


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
# settings.html — Vibes section + dual diagnostics footer
# ===========================================================================

def test_settings_includes_vibes_section_heading():
    """UI-SPEC §Settings page extensions: Vibes section heading."""
    src = SETTINGS_HTML.read_text()
    assert "Vibes" in src
    assert 'text-[20px] font-semibold text-text-primary' in src
    # The Vibes heading must use h2.
    assert "<h2 class=\"text-[20px] font-semibold text-text-primary mb-3\">Vibes</h2>" in src


def test_settings_includes_recluster_button_with_dispatch():
    """The Re-cluster button dispatches the Alpine open-recluster event."""
    src = SETTINGS_HTML.read_text()
    assert "Re-cluster vibes" in src
    assert "$dispatch('open-recluster')" in src


def test_settings_includes_run_wizard_link():
    """The Run setup wizard again link posts to /api/setup/reset."""
    src = SETTINGS_HTML.read_text()
    assert "Run setup wizard again →" in src
    assert 'href="/setup"' in src
    assert 'hx-post="/api/setup/reset"' in src
    assert 'hx-trigger="click"' in src


def test_settings_footer_has_dual_diagnostics_links():
    """D-37 dual diagnostics footer (events + vibes)."""
    src = SETTINGS_HTML.read_text()
    assert "View event diagnostics →" in src
    assert "View vibes diagnostics →" in src
    # Phase 5 plain "View diagnostics →" should be gone.
    assert ">View diagnostics →<" not in src


def test_settings_includes_recluster_modal_partial():
    """The settings page includes the recluster_modal partial."""
    src = SETTINGS_HTML.read_text()
    assert '{% include "partials/recluster_modal.html" %}' in src


# ===========================================================================
# recluster_modal.html — UI-SPEC contract enforcement
# ===========================================================================

def test_recluster_modal_dismiss_button_says_keep_current_vibes_not_cancel():
    """UI-SPEC checker BLOCK fix: dismiss button is "Keep current vibes" (NOT Cancel)."""
    src = MODAL_HTML.read_text()
    assert "Keep current vibes" in src
    assert ">Cancel<" not in src


def test_recluster_modal_confirm_button_uses_bg_accent_not_bg_error():
    """The Re-cluster confirm button is non-destructive at this point — bg-accent NOT bg-error."""
    src = MODAL_HTML.read_text()
    assert "bg-accent" in src
    assert "bg-error" not in src


def test_recluster_modal_alpine_x_show_dismiss_paths():
    """Alpine x-show / x-data; Esc + backdrop-click + button all dismiss; auto-focus the dismiss button."""
    src = MODAL_HTML.read_text()
    assert "x-show" in src
    assert "x-data" in src
    assert "@keydown.escape" in src
    assert "@click.self" in src
    assert 'x-ref="keep"' in src
    # auto-focus on open via $nextTick + $refs.keep
    assert "$nextTick" in src
    assert "$refs.keep" in src


def test_recluster_modal_buttons_have_min_h_11():
    """Pitfall 17 / UI-04 — both buttons (Keep + Re-cluster) at 44px tap target."""
    src = MODAL_HTML.read_text()
    # Should appear at least twice (one per button).
    assert src.count("min-h-11") >= 2


def test_recluster_modal_button_gap_is_16px():
    """UI-SPEC Spacing exception #3 — gap-4 (16px) between adjacent dismiss/primary."""
    src = MODAL_HTML.read_text()
    assert "gap-4" in src


def test_recluster_modal_no_text_15px():
    """UI-SPEC rev 2 typography contract — only 13/14/20/28; no text-[15px]."""
    src = MODAL_HTML.read_text()
    assert "text-[15px]" not in src


def test_settings_no_text_15px_in_new_content():
    """UI-SPEC rev 2: no text-[15px] in the Phase 6 Plan 04 settings additions."""
    src = SETTINGS_HTML.read_text()
    # Vibes section and modal include were the only Plan 04 additions.
    # We assert no text-[15px] anywhere in the file (Phase 5 inherited).
    assert "text-[15px]" not in src


def test_settings_no_cancel_label():
    """UI-SPEC checker BLOCK fix — no >Cancel< label anywhere on the settings page."""
    src = SETTINGS_HTML.read_text()
    assert ">Cancel<" not in src


# ===========================================================================
# Live render test (TestClient against the real FastAPI app)
# ===========================================================================

def test_settings_page_renders_with_all_new_elements(client_with_phase6):
    """GET /settings returns 200 + the rendered HTML contains all new strings."""
    resp = client_with_phase6.get("/settings")
    assert resp.status_code == 200
    body = resp.text
    assert "Re-cluster vibes" in body
    assert "Run setup wizard again" in body
    assert "View event diagnostics" in body
    assert "View vibes diagnostics" in body
    # The included recluster_modal renders inline.
    assert "Keep current vibes" in body
    assert "Re-cluster your vibes?" in body
