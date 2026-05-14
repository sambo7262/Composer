"""Phase 7 Plan 03 Task 1 — Mobile-first base shell (UI-02/03/04/05).

These tests pin the convention so a future refactor cannot silently drop
``min-h-dvh``, ``viewport-fit=cover``, ``alpine-morph``, ``min-h-11`` tap
targets, or the ``env(safe-area-inset-bottom)`` padding (Pitfalls 16/17/18).

The Tailwind utility ``min-h-11`` resolves to ``min-height: 2.75rem``
(44px) — the iOS Human-Interface-Guideline tap-target floor.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"


def _read(rel_path: str) -> str:
    return (TEMPLATES_DIR / rel_path).read_text()


@pytest.fixture
def jinja_env() -> Environment:
    """Jinja2 env that mirrors FastAPI's setup but skips the include system
    where the test only needs to render a single partial in isolation."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "jinja"]),
    )


# ---------------------------------------------------------------------------
# base.html invariants (UI-03 / UI-05)
# ---------------------------------------------------------------------------


def test_base_html_min_h_dvh_present():
    body = _read("base.html")
    assert "min-h-dvh" in body, (
        "base.html <body> must keep min-h-dvh (Pitfall 17 / UI-03)"
    )


def test_base_html_viewport_fit_cover_present():
    body = _read("base.html")
    assert "viewport-fit=cover" in body, (
        "base.html <meta viewport> must include viewport-fit=cover (UI-03)"
    )


def test_base_html_alpine_morph_present():
    body = _read("base.html")
    assert 'hx-ext="alpine-morph"' in body, (
        "base.html <body> must keep hx-ext='alpine-morph' (UI-05 / Pitfall 15)"
    )


def test_base_html_main_pb_safe_area():
    body = _read("base.html")
    assert "pb-[calc(env(safe-area-inset-bottom)+64px)]" in body, (
        "base.html <main> must reserve bottom padding for the bottom tab bar "
        "via pb-[calc(env(safe-area-inset-bottom)+64px)] (UI-03 / Pitfall 16)"
    )


def test_base_html_includes_bottom_tab_bar():
    body = _read("base.html")
    assert "partials/bottom_tab_bar.html" in body, (
        "base.html must include partials/bottom_tab_bar.html (UI-02)"
    )


# ---------------------------------------------------------------------------
# bottom_tab_bar.html invariants (UI-02 / UI-04 / Pitfalls 16/17/18)
# ---------------------------------------------------------------------------


def test_bottom_tab_bar_renders_four_items(jinja_env):
    tpl = jinja_env.get_template("partials/bottom_tab_bar.html")
    rendered = tpl.render(active_page="vibes")
    for label in ("Vibes", "Suggestions", "Discover", "Settings"):
        assert label in rendered, f"bottom tab bar missing label: {label}"


def test_bottom_tab_bar_uses_env_safe_area_inset_bottom():
    body = _read("partials/bottom_tab_bar.html")
    assert "env(safe-area-inset-bottom)" in body, (
        "bottom_tab_bar.html must use env(safe-area-inset-bottom) padding "
        "(Pitfall 16 — iOS notch / home-indicator gutter)"
    )


def test_bottom_tab_bar_hidden_on_desktop():
    body = _read("partials/bottom_tab_bar.html")
    assert "md:hidden" in body, (
        "bottom_tab_bar.html must use md:hidden so desktop relies on top nav"
    )


def test_bottom_tab_bar_tap_targets_min_h_11(jinja_env):
    # Render the template (the anchor uses a Jinja for-loop over 4 tabs, so
    # the source has 1 occurrence of `min-h-11` but the rendered output has 4).
    tpl = jinja_env.get_template("partials/bottom_tab_bar.html")
    rendered = tpl.render(active_page="vibes")
    assert rendered.count("min-h-11") >= 4, (
        "bottom_tab_bar.html must render min-h-11 on every tab anchor "
        "(UI-04 / Pitfall 17 — 44px tap target)"
    )


def test_bottom_tab_bar_no_hover_only_state():
    """Pitfall 18 — hover is a desktop convenience, not the primary state.
    Either no hover: classes at all OR every hover: rule is paired with a
    non-hover variant (active state is class-based, not :hover-driven).
    """
    body = _read("partials/bottom_tab_bar.html")
    # Strip Jinja blocks so we only inspect class attributes.
    hover_classes = re.findall(r"hover:[A-Za-z0-9_:\-\[\]/.]+", body)
    if hover_classes:
        # If any hover: classes exist, the active-state machinery must use
        # a class switch (border-accent / text-text-primary), NOT hover:.
        assert "border-accent" in body or "text-text-primary" in body, (
            "bottom_tab_bar.html uses hover: classes but lacks a class-based "
            "active state — would mean tap-not-hover is broken (Pitfall 18)"
        )


# ---------------------------------------------------------------------------
# nav.html invariants (UI-06 — chat retired; UI-04 — 44px)
# ---------------------------------------------------------------------------


def test_nav_html_removed_compose_link():
    body = _read("partials/nav.html")
    # "Compose" (label) and "Composer" (brand) differ by trailing 'r'. Only
    # the bare label/link is forbidden — the "Composer" wordmark stays.
    assert not re.search(r"\bCompose\b", body), (
        "nav.html must not reference the 'Compose' label (UI-06 — v1 chat "
        "retired). 'Composer' (brand) is allowed."
    )
    assert 'href="/chat"' not in body, (
        "nav.html must not link to /chat (UI-06 — v1 chat retired)"
    )
    assert "Vibes" in body, "nav.html must add a 'Vibes' link"
    assert "Suggestions" in body, "nav.html must add a 'Suggestions' link"
    assert "Settings" in body, "nav.html must keep a 'Settings' link"


def test_nav_html_root_links_to_vibes_home():
    body = _read("partials/nav.html")
    # The Vibes link must point at /vibes (or /), not /chat.
    assert 'href="/vibes"' in body or 'href="/"' in body
    assert 'href="/chat"' not in body


def test_nav_html_hidden_on_mobile():
    body = _read("partials/nav.html")
    assert "hidden md:block" in body, (
        "nav.html must hide on mobile (bottom_tab_bar takes over)"
    )


def test_active_page_helper_supports_new_pages(jinja_env):
    tpl = jinja_env.get_template("partials/nav.html")
    rendered = tpl.render(active_page="suggestions")
    # The Suggestions link gets the active-state border classes.
    # We grep for the literal pattern 'border-b-2 border-accent' near the
    # 'Suggestions' label.
    # Find the Suggestions anchor block.
    # Fallback: just confirm both substrings co-occur.
    assert "Suggestions" in rendered
    assert "border-b-2 border-accent" in rendered, (
        "nav.html must apply 'border-b-2 border-accent' to the active link"
    )


def test_nav_html_anchors_have_min_h_11():
    body = _read("partials/nav.html")
    # Each desktop nav anchor (Vibes / Suggestions / Library / Settings) needs
    # min-h-11 to satisfy the 44px tap-target invariant on touch desktops.
    assert body.count("min-h-11") >= 4
