"""Phase 8 Plan 04 Task 1 — /discover page route smoke tests.

The bulk of the page-rendering matrix lives in
``tests/test_api_discovery.py``. This module pins the route shell
(``read_discover``) and the template-source invariants:

- ``app/templates/pages/discover.html`` exists and extends ``base.html``.
- The page includes ``discover_vibe_section`` partials.
- The artist card partial uses Alpine + the hx-post Add/Dismiss buttons
  per D-D3 / D-D5.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"


def _read(rel: str) -> str:
    return (TEMPLATES_DIR / rel).read_text()


def test_discover_page_template_exists():
    assert (TEMPLATES_DIR / "pages" / "discover.html").exists(), (
        "Plan 04 must create app/templates/pages/discover.html "
        "(replaces discover_placeholder.html)"
    )


def test_discover_page_extends_base():
    body = _read("pages/discover.html")
    assert '{% extends "base.html" %}' in body, (
        "pages/discover.html must extend the v2 base shell"
    )


def test_discover_page_sets_active_page():
    body = _read("pages/discover.html")
    assert 'active_page = "discover"' in body, (
        "pages/discover.html must set active_page='discover' so the "
        "bottom tab bar highlights the Discover tab"
    )


def test_discover_page_includes_vibe_section_partial():
    body = _read("pages/discover.html")
    assert "discover_vibe_section" in body, (
        "pages/discover.html must include the per-vibe section partial"
    )


def test_vibe_section_partial_has_border_left_with_vibe_color():
    body = _read("partials/discover_vibe_section.html")
    assert "border-left" in body and "section.vibe.color" in body, (
        "discover_vibe_section.html must render an inline border-left "
        "style driven by section.vibe.color (Tailwind 4 dynamic-hex "
        "constraint — D-E2)"
    )


def test_artist_card_buttons_use_hx_post_add_and_dismiss():
    body = _read("partials/discover_artist_card.html")
    assert 'hx-post="/api/discovery/{{ artist.mb_id }}/add"' in body
    assert 'hx-post="/api/discovery/{{ artist.mb_id }}/dismiss"' in body


def test_artist_card_has_min_44px_tap_targets():
    body = _read("partials/discover_artist_card.html")
    assert body.count("min-h-11") >= 2, (
        "discover_artist_card.html must mark every tappable element "
        "with min-h-11 (44px tap target — Pitfall 17 / UI-04)"
    )


def test_status_row_partial_uses_is_stale_conditional():
    body = _read("partials/discover_status_row.html")
    assert "is_stale" in body, (
        "discover_status_row.html must render the stale-warning chip "
        "conditional on `is_stale` (D-C3 / Pitfall 14)"
    )
    assert 'data-stale-warning="true"' in body, (
        "discover_status_row.html stale chip must set "
        "data-stale-warning='true' for the assertion gate"
    )


def test_status_row_partial_has_stale_warning_message():
    body = _read("partials/discover_status_row.html")
    assert "No releases found after" in body, (
        "discover_status_row.html must render the user-facing "
        "stale-warning chip text 'No releases found after Nd'"
    )
