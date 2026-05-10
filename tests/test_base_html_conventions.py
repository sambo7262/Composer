"""Phase 6 (D-09) — base.html + input.css project-wide convention tests.

These conventions ship in Plan 01 and apply to every Phase 6+ wizard / debug
surface that follows. Pitfalls 15, 16, 17 are the failure modes these tests
prevent regressing into:

- Pitfall 15: HTMX swaps clobber Alpine state if the body lacks
  ``hx-ext="alpine-morph"``.
- Pitfall 16: iOS Safari ``100vh`` is wrong (it doesn't account for the URL
  bar). ``min-h-dvh`` is correct; ``viewport-fit=cover`` lets the browser
  apply ``env(safe-area-inset-*)`` to elements that opt in.
- Pitfall 17 / UI-04: 44px minimum touch target size; the
  ``--touch-target-min`` token is the documented declaration.

Plain string assertions on the file contents — no HTML parser needed. If
these tests trip in a later plan, restore the corresponding line in
``app/templates/base.html`` or ``app/static/css/input.css``.
"""
from __future__ import annotations

from pathlib import Path

BASE_HTML = Path(__file__).parent.parent / "app" / "templates" / "base.html"
INPUT_CSS = Path(__file__).parent.parent / "app" / "static" / "css" / "input.css"


def test_body_has_alpine_morph():
    """Pitfall 15: <body> must declare hx-ext='alpine-morph' for safe HTMX swaps
    inside Alpine components (refinement-loop cards, etc.)."""
    src = BASE_HTML.read_text()
    assert 'hx-ext="alpine-morph"' in src, (
        'Pitfall 15: <body> must declare hx-ext="alpine-morph" so HTMX swaps '
        "go through the Alpine Morph plugin and preserve x-data state."
    )


def test_body_uses_dvh_not_vh():
    """Pitfall 16: replace min-h-screen (= 100vh) with min-h-dvh (dynamic vh).

    iOS Safari's URL bar collapses on scroll; min-h-screen leaves a 50-100px
    gap at the bottom. min-h-dvh is the Tailwind 4 native utility that picks
    up the correct dynamic viewport height.
    """
    src = BASE_HTML.read_text()
    assert "min-h-dvh" in src, "Pitfall 16: must use min-h-dvh on the body."
    assert "min-h-screen" not in src, (
        "Pitfall 16: replace min-h-screen with min-h-dvh — leftover legacy class."
    )


def test_viewport_meta_includes_fit_cover():
    """Pitfall 16: viewport meta must opt into safe-area inset insets so
    sticky-bottom CTA bars can pad themselves below the iOS home bar via
    env(safe-area-inset-bottom)."""
    src = BASE_HTML.read_text()
    assert "viewport-fit=cover" in src, (
        "Pitfall 16: viewport meta must include viewport-fit=cover so "
        "env(safe-area-inset-bottom) reports a non-zero value on iPhone."
    )


def test_input_css_has_touch_target_token():
    """Pitfall 17 / UI-04: declare --touch-target-min: 44px in @theme.

    The token is the single project-wide source of truth for the minimum
    touch-target size. Tailwind utilities min-h-11 / min-w-11 (which evaluate
    to 44px in v3 / v4 default scales) are applied at use sites.
    """
    src = INPUT_CSS.read_text()
    assert "--touch-target-min: 44px" in src, (
        "Pitfall 17 / UI-04: declare --touch-target-min: 44px in input.css @theme."
    )


def test_input_css_preserves_existing_tokens():
    """Phase 6 must not remove any existing color / font tokens.

    Defensive guard against a future global token reshuffle silently dropping
    the surface / accent palette mid-Phase-6.
    """
    src = INPUT_CSS.read_text()
    for token in (
        "--color-surface-primary",
        "--color-surface-card",
        "--color-surface-elevated",
        "--color-accent",
        "--color-accent-hover",
        "--color-text-primary",
        "--color-text-secondary",
        "--color-text-muted",
        "--color-success",
        "--color-error",
        "--color-border",
        "--color-border-focus",
        "--font-family-sans",
    ):
        assert token in src, f"Phase 6 must not remove existing token {token}"
