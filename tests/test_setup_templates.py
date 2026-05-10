"""Phase 6 Plan 03 wizard template static tests (UI-SPEC §"Component Inventory").

File-content grep tests over the 5 wizard pages + 10+ wizard partials.
Verifies:
- Every wizard page extends partials/wizard_layout.html (or base.html for done).
- Step 3 uses morph:innerHTML on #proposal-cards (Pitfall 15 / D-09).
- No NEW Phase 6 surface uses text-[15px] (UI-SPEC rev 2 typography contract).
- No button labeled "Cancel" anywhere (UI-SPEC checker BLOCK).
- Sticky CTA partial uses env(safe-area-inset-bottom) + min-h-12.
- Refinement input posts to /api/setup/refine with morph:innerHTML.
- Vibe proposal card has Alpine x-data state.
- Force-k picker shown only when refinement_turn_count == 0 (D-13).
- Cold-start panel rendered only when n_rated < 30.
- Step 1 / 2 / 3 / 4 / done copy contains exact UI-SPEC strings.
- Every primary CTA has min-h-11 or min-h-12 (44px tap target — Pitfall 17).
"""
from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"

# Pages to validate (NEW in Phase 6 Plan 03).
WIZARD_PAGES = [
    "setup_step1",
    "setup_step2",
    "setup_step3",
    "setup_step4",
    "setup_done",
]

# Partials to validate (NEW in Phase 6 Plan 03).
WIZARD_PARTIALS = [
    "wizard_layout",
    "wizard_step_indicator",
    "wizard_sticky_cta",
    "cold_start_panel",
    "vibe_proposal_card",
    "vibe_name_input",
    "feature_chip",
    "seed_track_row",
    "vibe_members_disclosure",
    "refinement_input",
    "proposal_cards_swap",
    "rated_count",
    "refine_error",
    "push_to_plex_banner",
]


def _read(name: str, dir_: str) -> str:
    p = TEMPLATES_DIR / dir_ / f"{name}.html"
    if not p.exists():
        return ""
    return p.read_text()


# ---------------------------------------------------------------------------
# Test 1: every wizard page extends wizard_layout (or base for done)
# ---------------------------------------------------------------------------
def test_all_wizard_pages_extend_wizard_layout():
    """Each setup_step{1..4}.html extends wizard_layout; setup_done extends base."""
    for n in ("setup_step1", "setup_step2", "setup_step3", "setup_step4"):
        src = _read(n, "pages")
        assert src, f"pages/{n}.html missing"
        assert (
            'extends "partials/wizard_layout.html"' in src
            or "extends 'partials/wizard_layout.html'" in src
        ), f"pages/{n}.html must extend partials/wizard_layout.html. Got first line: {src[:120]!r}"

    done = _read("setup_done", "pages")
    assert done, "pages/setup_done.html missing"
    assert (
        'extends "base.html"' in done
        or "extends 'base.html'" in done
        or 'extends "partials/wizard_layout.html"' in done
    ), "pages/setup_done.html must extend base.html or wizard_layout.html"


# ---------------------------------------------------------------------------
# Test 2: setup_step3 uses morph swap on #proposal-cards (Pitfall 15 / D-09)
# ---------------------------------------------------------------------------
def test_setup_step3_uses_morph_swap_target():
    src = _read("setup_step3", "pages")
    assert 'id="proposal-cards"' in src, (
        "pages/setup_step3.html must declare id=\"proposal-cards\" as morph-swap target"
    )
    assert "morph:innerHTML" in src, (
        "pages/setup_step3.html must reference morph:innerHTML somewhere "
        "(Pitfall 15 / D-09 alpine-morph swap)"
    )


# ---------------------------------------------------------------------------
# Test 3: NO Phase 6 NEW surface uses text-[15px] (UI-SPEC rev 2)
# ---------------------------------------------------------------------------
def test_no_phase6_template_uses_text_15px():
    violations = []
    for n in WIZARD_PAGES:
        if "text-[15px]" in _read(n, "pages"):
            violations.append(f"pages/{n}.html")
    for n in WIZARD_PARTIALS:
        if "text-[15px]" in _read(n, "partials"):
            violations.append(f"partials/{n}.html")
    assert violations == [], (
        f"UI-SPEC rev 2: no Phase 6 NEW surface may use text-[15px]: "
        f"{violations}. Use one of [13/14/20/28]."
    )


# ---------------------------------------------------------------------------
# Test 4: NO button labeled "Cancel" (UI-SPEC checker BLOCK)
# ---------------------------------------------------------------------------
def test_no_wizard_button_labeled_cancel():
    violations = []
    for n in WIZARD_PAGES:
        if ">Cancel<" in _read(n, "pages"):
            violations.append(f"pages/{n}.html")
    for n in WIZARD_PARTIALS:
        if ">Cancel<" in _read(n, "partials"):
            violations.append(f"partials/{n}.html")
    assert violations == [], (
        f"UI-SPEC checker BLOCK: no Phase 6 surface may label a button "
        f"'Cancel' (use 'Keep current vibes' from Plan 04 dismiss instead): "
        f"{violations}"
    )


# ---------------------------------------------------------------------------
# Test 5: sticky CTA partial uses safe-area-inset + 44px tap target
# ---------------------------------------------------------------------------
def test_sticky_cta_partial_uses_safe_area_inset_and_min_height():
    src = _read("wizard_sticky_cta", "partials")
    assert "env(safe-area-inset-bottom)" in src, (
        "wizard_sticky_cta.html must use env(safe-area-inset-bottom) (Pitfall 16)"
    )
    assert ("min-h-12" in src) or ("min-h-11" in src), (
        "wizard_sticky_cta.html must use min-h-12 or min-h-11 (Pitfall 17 / UI-04 — 44px tap target)"
    )


# ---------------------------------------------------------------------------
# Test 6: refinement_input posts to /api/setup/refine + morph swap
# ---------------------------------------------------------------------------
def test_refinement_input_post_target():
    src = _read("refinement_input", "partials")
    assert 'hx-post="/api/setup/refine"' in src
    assert 'hx-target="#proposal-cards"' in src
    assert 'hx-swap="morph:innerHTML"' in src


# ---------------------------------------------------------------------------
# Test 7: vibe_proposal_card has Alpine x-data (click-to-edit name + disclosure)
# ---------------------------------------------------------------------------
def test_vibe_proposal_card_has_alpine_state():
    src = _read("vibe_proposal_card", "partials")
    assert "x-data" in src, (
        "vibe_proposal_card.html must contain Alpine x-data (click-to-edit + disclosure require state)"
    )


# ---------------------------------------------------------------------------
# Test 8: force-k picker only on initial run (refinement_turn_count == 0; D-13)
# ---------------------------------------------------------------------------
def test_step3_force_k_picker_initial_only():
    src = _read("setup_step3", "pages")
    # The block must be guarded by `(refinement_turn_count or 0) == 0` (D-13).
    assert (
        "(refinement_turn_count or 0) == 0" in src
        or "refinement_turn_count == 0" in src
        or "not refinement_turn_count" in src
    ), (
        "setup_step3.html must only show the force-k picker on the initial run "
        "(D-13: hide after first refinement turn)"
    )
    # And it must mention forced_k.
    assert 'name="forced_k"' in src, (
        "setup_step3.html force-k picker must POST forced_k field"
    )


# ---------------------------------------------------------------------------
# Test 9: cold_start panel only when n_rated < 30
# ---------------------------------------------------------------------------
def test_cold_start_panel_visible_below_30():
    src = _read("setup_step1", "pages")
    assert "n_rated < 30" in src, (
        "setup_step1.html must guard cold_start_panel inclusion with n_rated < 30"
    )


# ---------------------------------------------------------------------------
# Test 10: setup_step1 contains "Continue to webhook setup"
# ---------------------------------------------------------------------------
def test_step1_uses_continue_to_webhook_setup_copy():
    src = _read("setup_step1", "pages")
    assert "Continue to webhook setup" in src, (
        "setup_step1.html must contain UI-SPEC verbatim: 'Continue to webhook setup'"
    )


# ---------------------------------------------------------------------------
# Test 11: setup_step2 contains "Continue to your vibes"
# ---------------------------------------------------------------------------
def test_step2_uses_continue_to_your_vibes_copy():
    src = _read("setup_step2", "pages")
    assert "Continue to your vibes" in src


# ---------------------------------------------------------------------------
# Test 12: setup_step3 contains "Looks good"
# ---------------------------------------------------------------------------
def test_step3_uses_looks_good_cta():
    src = _read("setup_step3", "pages")
    assert "Looks good" in src


# ---------------------------------------------------------------------------
# Test 13: setup_step4 per-vibe summary uses text-[14px] (NOT text-[15px])
# ---------------------------------------------------------------------------
def test_step4_per_vibe_summary_uses_text_14px():
    src = _read("setup_step4", "pages")
    # Per-vibe line must use Body 14px.
    assert "text-[14px]" in src, (
        "setup_step4.html per-vibe summary must use text-[14px] Body class "
        "(UI-SPEC rev 2 BLOCK fix)"
    )
    assert "text-[15px]" not in src, (
        "setup_step4.html must NOT use text-[15px] (UI-SPEC rev 2 BLOCK fix)"
    )


# ---------------------------------------------------------------------------
# Test 14: setup_done links to /debug/vibes
# ---------------------------------------------------------------------------
def test_done_links_to_debug_vibes():
    src = _read("setup_done", "pages")
    assert 'href="/debug/vibes"' in src, (
        "setup_done.html must link to /debug/vibes (Plan 04 'View vibes diagnostics')"
    )


# ---------------------------------------------------------------------------
# Test 15: every CTA across pages + partials has min-h-11 or min-h-12
# ---------------------------------------------------------------------------
def test_button_min_h_on_every_cta():
    """For every page or partial that contains a primary CTA-style button,
    at least one min-h-11 or min-h-12 occurrence must be present.

    Heuristic: any file with a `<button` tag MUST have min-h-11/12.
    """
    files_with_button: list[str] = []
    for n in WIZARD_PAGES:
        src = _read(n, "pages")
        if "<button" in src:
            files_with_button.append(("pages", n, src))
    for n in WIZARD_PARTIALS:
        src = _read(n, "partials")
        if "<button" in src:
            files_with_button.append(("partials", n, src))

    violations = []
    for dir_, n, src in files_with_button:
        if ("min-h-11" not in src) and ("min-h-12" not in src):
            violations.append(f"{dir_}/{n}.html")
    assert violations == [], (
        f"Every wizard surface with a <button> must include min-h-11 or "
        f"min-h-12 (Pitfall 17 / UI-04 — 44px tap target): {violations}"
    )


# ---------------------------------------------------------------------------
# Test 16: feature_chip_text registered as Jinja2 global in main.py
# ---------------------------------------------------------------------------
def test_feature_chip_text_registered_as_jinja_global():
    main_path = Path(__file__).parent.parent / "app" / "main.py"
    src = main_path.read_text()
    assert "feature_chip_text" in src, (
        "app/main.py must register feature_chip_text as a Jinja2 global so "
        "vibe_proposal_card.html can call it"
    )


# ---------------------------------------------------------------------------
# Test 17: push_to_plex_banner has 3+ states + HTMX 2s poll on running
# ---------------------------------------------------------------------------
def test_push_banner_has_state_machine_and_poll():
    src = _read("push_to_plex_banner", "partials")
    assert src, "partials/push_to_plex_banner.html missing"
    assert 'state == "running"' in src
    assert 'state == "failed"' in src
    assert 'state == "completed"' in src
    assert "every 2s" in src
    assert "Pushing…" in src
    assert "live in Plex" in src
    assert "Retry the rest" in src
