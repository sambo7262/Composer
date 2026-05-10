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
# Test 8 (Phase 6.1 D-NEW-06): force-k picker REMOVED in favor of textbox stack
# ---------------------------------------------------------------------------
def test_step3_textbox_stack_replaces_force_k_picker():
    """Phase 6.1 supersedes Phase 6 D-13. The force-k picker is gone — Step 3
    opens with a user-led textbox-stack input (3 inputs default, +Add up to 7).
    """
    src = _read("setup_step3", "pages")
    # forced_k must NOT appear — the picker is removed.
    assert 'name="forced_k"' not in src, (
        "setup_step3.html must NOT contain forced_k picker (Phase 6.1 D-NEW-06 "
        "replaces it with the user-led textbox-stack input)"
    )
    # The new textbox-stack input must exist.
    assert 'name="vibe_names"' in src, (
        "setup_step3.html must contain a vibe_names hidden input for the "
        "textbox-stack form (D-NEW-06)"
    )
    assert "Cluster my library" in src, (
        "setup_step3.html must contain the 'Cluster my library' CTA"
    )
    assert "+ Add another" in src, (
        "setup_step3.html must contain the '+ Add another' button"
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


# ===========================================================================
# Phase 6.1 D-NEW-08 — fit-grade chip rendering on vibe_proposal_card.html.
# All tests construct REAL VibeProposal instances (Blocker #1 — no dict
# fixtures).
# ===========================================================================

def _make_vibe_proposal(
    name="Workout",
    description="High energy",
    fit="strong",
    fit_reason=None,
    member_count=87,
    n_seed_tracks=5,
    n_members=87,
):
    """Helper — build a real VibeProposal per Plan 01 schema."""
    from app.services.vibe_clusterer import VibeProposal
    seed_tracks = [
        {"title": f"Track {i}", "artist": f"Artist {i % 5}",
         "rating_key": f"rk_{i}"}
        for i in range(n_seed_tracks)
    ]
    members = [
        {"title": f"Track {i}", "artist": f"Artist {i % 5}",
         "rating_key": f"rk_{i}"}
        for i in range(n_members)
    ]
    return VibeProposal(
        name=name,
        description=description,
        action="new",
        seed_track_indices=list(range(n_members)),
        centroid={"energy": 0.8, "tempo": 130,
                  "danceability": 0.7, "valence": 0.5},
        spread={"energy": 0.1, "tempo": 10.0,
                "danceability": 0.1, "valence": 0.1},
        silhouette=0.42,
        seed_tracks=seed_tracks,
        members=members,
        member_count=member_count,
        fit=fit,
        fit_reason=fit_reason,
    )


def _render_card(proposal):
    from fastapi.templating import Jinja2Templates
    from app.services.vibe_helpers import feature_chip_text
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["feature_chip_text"] = feature_chip_text
    t = templates.get_template("partials/vibe_proposal_card.html")
    return t.render(proposal=proposal, proposal_index=0)


def _render_members_disclosure(proposal):
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="app/templates")
    t = templates.get_template("partials/vibe_members_disclosure.html")
    return t.render(proposal=proposal)


def test_vibe_proposal_card_renders_fit_chip_strong_with_real_proposal():
    p = _make_vibe_proposal(fit="strong", member_count=87)
    html = _render_card(p)
    assert "strong fit" in html
    assert "✓" in html
    assert "text-success" in html
    # Count display uses member_count directly.
    assert "87 tracks" in html


def test_vibe_proposal_card_renders_fit_chip_weak_with_real_proposal():
    p = _make_vibe_proposal(name="Mood", fit="weak", member_count=34)
    html = _render_card(p)
    assert "weak fit" in html
    assert "⚠" in html
    assert "text-accent" in html
    assert "34 tracks" in html


def test_vibe_proposal_card_renders_fit_chip_no_match_with_reason_and_real_proposal():
    p = _make_vibe_proposal(
        name="Ambient", fit="no_match",
        fit_reason="Library has no ambient artists",
        member_count=0, n_seed_tracks=0, n_members=0,
    )
    html = _render_card(p)
    assert "no match" in html
    assert "✗" in html
    assert "text-error" in html
    assert "Library has no ambient artists" in html


def test_vibe_proposal_card_no_fit_chip_when_fit_is_none_with_real_proposal():
    p = _make_vibe_proposal(fit=None)
    html = _render_card(p)
    assert "strong fit" not in html
    assert "weak fit" not in html
    assert "no match" not in html
    # Color tokens specific to the fit chip absent.
    assert "✓ strong fit" not in html
    assert "⚠ weak fit" not in html
    assert "✗ no match" not in html


def test_vibe_proposal_card_uses_member_count_for_fit_chip_display():
    """member_count is the source of truth for the fit-chip count, even when
    len(seed_track_indices) disagrees (e.g. legacy proposal shapes).
    """
    from app.services.vibe_clusterer import VibeProposal
    p = VibeProposal(
        name="X", description="d", action="new",
        seed_track_indices=[1, 2, 3],  # 3
        centroid={"energy": 0.5, "tempo": 100,
                  "danceability": 0.5, "valence": 0.5},
        spread={"energy": 0.1, "tempo": 10.0,
                "danceability": 0.1, "valence": 0.1},
        silhouette=0.4,
        seed_tracks=[],
        members=[
            {"title": f"T{i}", "artist": "A", "rating_key": f"rk_{i}"}
            for i in range(87)
        ],
        member_count=87,
        fit="strong",
    )
    html = _render_card(p)
    # Fit chip count uses member_count=87, NOT len(seed_track_indices)=3.
    # We look for the count next to the fit chip — "87 tracks" must appear,
    # and "3 tracks" (the seed_track_indices length) must NOT.
    assert "87 tracks" in html
    assert " 3 tracks" not in html, (
        f"member_count=87 should win over len(seed_track_indices)=3; "
        f"found ' 3 tracks' in:\n{html}"
    )


def test_vibe_members_disclosure_renders_all_members_when_expanded():
    """Blocker #1 follow-through: the disclosure pulls from proposal.members
    (populated by Plan 01) — NOT an empty list. We assert the member <p>
    elements are present in the static HTML (Alpine x-show hides at runtime
    but the elements exist in the DOM).
    """
    p = _make_vibe_proposal(member_count=30, n_seed_tracks=5, n_members=30)
    html = _render_members_disclosure(p)
    # "Show all 30 tracks" button label uses member_count.
    assert "Show all 30 tracks" in html
    # All 30 member.title strings are present in the expanded <p> elements.
    for i in range(30):
        assert f"Track {i}" in html, (
            f"Member {i} missing from disclosure HTML — "
            f"proposal.members not rendered"
        )
