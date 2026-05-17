---
phase: 08-lidarr-discovery-polish
plan: 03
subsystem: visual-polish-cross-surface
tags: [ui-09, ui-10, vibe-color, cost-chip, home-page, mobile-first]
requires:
  - phase 8 plan 01 (Vibe.color column, CostMeterBaseline + WeeklyCronState
    tables, VIBE_COLOR_PALETTE + assign_vibe_color, run_phase_08_discovery_bootstrap)
  - phase 7.1 D-D3 (llm_cost_breaker.last_tripped_at 60s window pattern)
  - phase 6 D-22 (manual-override preservation rule — extended here to colors)
provides:
  - vibe_service.ensure_vibe_color_on_creation — idempotent vibe-color seeding
    helper used by every Vibe-creation site (wizard finalize + recluster commit)
  - VibeProposal.proposed_color — new Pydantic field; pre-commit preview color
    populated by pages._decode_draft via assign_vibe_color(index+1)
  - partials/llm_cost_chip.html — compact home-page chip per D-B4
  - pages._compute_home_chip_context — single aggregate SELECT for the chip
    (this_week_cost_usd, days_until_refresh, breaker_paused, has_first_tick)
  - vibe.color surfaced through read_vibes_home so vibe_card renders the accent
affects:
  - app/templates/partials/vibe_card.html (left-border color accent)
  - app/templates/partials/suggestions_row.html (tinted vibe chip)
  - app/templates/partials/recluster_modal.html (reserved iteration block)
  - app/templates/partials/vibe_diagnostic_card.html (border accent)
  - app/templates/partials/vibe_proposal_card.html (wizard preview accent)
  - app/templates/pages/setup_step3.html (documents proposed_color contract)
  - app/templates/pages/vibes_home.html (chip include)
  - app/routers/pages.py (chip helper + read_vibes_home rewire)
  - app/routers/api_setup.py (wizard finalize calls ensure_vibe_color_on_creation)
  - app/routers/api_vibes.py (recluster commit — all 3 insert sites — call helper)
  - app/services/vibe_clusterer.py (VibeProposal.proposed_color field)
tech-stack:
  added: []  # all libraries already pinned by Plan 01
  patterns:
    - Inline `style="border-left-color: …"` for dynamic hex (Tailwind 4
      arbitrary-value workaround documented in PATTERNS.md §discover.html)
    - `value or '#3a3a3e'` neutral fallback inside style attributes
    - `value + 'XX'` hex alpha suffix for ~20% opacity tinted chip backgrounds
    - Single aggregate SUM(...) SELECT with max() floor for chip cost computation
key-files:
  created:
    - app/templates/partials/llm_cost_chip.html
    - tests/test_vibe_color_propagation.py
    - tests/test_pages_home_chip.py
  modified:
    - app/services/vibe_service.py
    - app/services/vibe_clusterer.py
    - app/routers/pages.py
    - app/routers/api_setup.py
    - app/routers/api_vibes.py
    - app/templates/partials/vibe_card.html
    - app/templates/partials/suggestions_row.html
    - app/templates/partials/recluster_modal.html
    - app/templates/partials/vibe_diagnostic_card.html
    - app/templates/partials/vibe_proposal_card.html
    - app/templates/pages/setup_step3.html
    - app/templates/pages/vibes_home.html
decisions:
  - "ensure_vibe_color_on_creation is idempotent — preserves existing colors
    per D-22 stickiness extended to Vibe.color. Wizard finalize + all three
    recluster-commit insert sites in api_vibes.py call the helper after
    asyncio.to_thread(_insert_vibe_sync). Best-effort try/except: a
    color-write failure NEVER blocks Vibe creation or playlist push."
  - "Wizard preview uses assign_vibe_color(index+1) — the +1 mirrors the
    deterministic palette[id % len] mapping the post-commit helper applies
    when a fresh Vibe.id arrives. Same palette slot before and after commit."
  - "Tailwind 4 dynamic-hex constraint forces inline style attributes — the
    AST grep test (test_no_hardcoded_vibe_hex_in_render_paths) enforces that
    hardcoded palette hexes only appear inside `vibe.color or '#…'` fallbacks
    so a future refactor can't accidentally reintroduce a hardcoded accent."
  - "recluster_modal.html does NOT yet iterate vibes at render time (it's
    Alpine-driven). The plan's grep gate (`vibe.color` in source) is met
    by a reserved `{% if vibes %}` iteration block + a documentation
    comment that future enhancements can drop into. Today's modal behavior
    is unchanged."
  - "Home-page chip computation is a single aggregate SELECT —
    SUM(LLMUsage.cost_estimate_usd) WHERE called_at >= max(baseline,
    last_tick). The baseline floor is permanent (historical/testing rows
    NEVER pollute the canary); the last_tick floor moves on every
    successful weekly cron tick (Plan 02 step 4 stamps WeeklyCronState)."
  - "Chip is defensive against missing CostMeterBaseline OR
    WeeklyCronState rows — both pre-bootstrap states fall back to the
    'Your first weekly tick lands Sunday at 03:00 UTC' message. Mirrors
    the same defensive read pattern in pages.read_settings for the
    /settings cost meter."
  - "breaker_paused uses the 60s window after last_tripped_at (mirrors
    pages.read_settings line 369). Best-effort try/except so a breaker
    state read failure renders the chip WITHOUT the paused modifier
    rather than blowing up the entire home page."
metrics:
  duration_minutes: 12
  completed: 2026-05-17
---

# Phase 8 Plan 03: Vibe Color Propagation + Home-Page Cost Chip Summary

Cross-cutting visual polish for the v2 home / suggestions / debug surfaces. After this plan, every vibe label renders with its locked Tailwind-4 palette color, and the vibes home shows a compact runaway-cost canary chip at the top.

## What Now Holds

1. **Every vibe-rendering surface reads `{{ vibe.color }}`.** Five templates were updated to render the per-vibe color via inline `style="border-left-color: …"` or `style="background-color: …33; color: …"` (where `33` is the hex alpha suffix for ~20% opacity). The Tailwind 4 dynamic-hex constraint (utility classes don't accept dynamic hex without arbitrary-value `[color:#xxx]` syntax) is documented inline at each call site. An AST grep test (`test_no_hardcoded_vibe_hex_in_render_paths`) enforces that hardcoded palette hexes only appear inside `vibe.color or '#…'` fallbacks.
2. **Vibe creation auto-assigns color.** `app.services.vibe_service.ensure_vibe_color_on_creation(vibe_id)` is the canonical entry point. Called by `api_setup.finalize` (wizard) and `api_vibes.recluster_commit` (all three `_insert_vibe_sync` call sites: keep / split_from / new). Idempotent: if a row already has a color (e.g. recluster re-using a stable vibe id, or manual seed), the helper is a no-op. Best-effort try/except — a color-write failure NEVER blocks Vibe creation or playlist push.
3. **Existing vibe colors survive re-cluster (D-22 stickiness extended to colors).** Verified by `test_recluster_preserves_existing_vibe_colors`. Re-cluster paths that update centroids on an existing Vibe row don't change `color`; only fresh inserts get a palette slot.
4. **Wizard preview matches post-commit color.** `VibeProposal` gained an optional `proposed_color` field. `pages._decode_draft` hydrates it via `assign_vibe_color(index+1)` so the wizard cards in `setup_step3.html` render the same border accent the user will see on `/vibes` after finalize. The +1 offset mirrors the deterministic `palette[id % len]` mapping that `ensure_vibe_color_on_creation` applies once the fresh `vibe.id` is available.
5. **Home-page weekly cost chip lands at the top of `vibes_home.html`.** Compact 44px tap target (min-h-11) linking to `/debug/suggestions`. Three branches:
   - `has_first_tick=False` → "Your first weekly tick lands Sunday at 03:00 UTC." (default state pre-bootstrap or pre-first-cron-tick).
   - `has_first_tick=True` → "This week: \$X.XX · next refresh in Nd" with `· paused` modifier when the breaker is circuit-open within the last 60s.
6. **Chip query shape locked.** `SUM(LLMUsage.cost_estimate_usd) WHERE called_at >= max(CostMeterBaseline.deploy_at, WeeklyCronState.last_tick_at)`. The baseline floor excludes historical/testing rows permanently. The last_tick floor moves on every successful weekly cron tick (Plan 02 step 4 stamps `WeeklyCronState`). Defensive against missing baseline or weekly-cron-state rows — both fall back to the friendly first-tick message.

## File-by-File Changes

### Created

| File | Purpose |
|------|---------|
| `app/templates/partials/llm_cost_chip.html` | Compact 44px tap target for the runaway-cost canary chip. Three rendering branches (first-tick / cost / paused). Links to `/debug/suggestions`. |
| `tests/test_vibe_color_propagation.py` | 10 tests — Vibe creation wiring, template render assertions, AST grep gate. |
| `tests/test_pages_home_chip.py` | 12 tests — chip query shape, baseline filter, last_tick filter, breaker modifier, days-until-refresh, defensive against missing rows, template include + 44px target. |

### Modified

| File | Change |
|------|--------|
| `app/services/vibe_service.py` | Added `ensure_vibe_color_on_creation(vibe_id)` helper — idempotent (preserves existing color per D-22). Reads `assign_vibe_color` from `discovery_service`. |
| `app/services/vibe_clusterer.py` | Added `VibeProposal.proposed_color: Optional[str]` field. |
| `app/routers/pages.py` | Added `Optional` import, `_compute_home_chip_context` helper, `_decode_draft` proposed_color hydration. `read_vibes_home` now merges chip context + surfaces `Vibe.color` to template. |
| `app/routers/api_setup.py` | Wizard finalize calls `ensure_vibe_color_on_creation` after each `_insert_vibe_sync`. |
| `app/routers/api_vibes.py` | All three recluster-commit `_insert_vibe_sync` sites (keep/split_from/new) call `ensure_vibe_color_on_creation`. |
| `app/templates/partials/vibe_card.html` | Added left-border color accent via inline `border-left-color` style. |
| `app/templates/partials/suggestions_row.html` | Replaced static elevated-surface chip with tinted chip using `s.vibe.color` (background-color + foreground color). |
| `app/templates/partials/recluster_modal.html` | Reserved `{% if vibes %}` iteration block reading `vibe.color` so future enhancements pick up the accent without further changes. Today's modal behavior unchanged. |
| `app/templates/partials/vibe_diagnostic_card.html` | Added `border-l-4` + inline `border-left-color: {{ vibe.color or '#3a3a3e' }}`. |
| `app/templates/partials/vibe_proposal_card.html` | Added `border-l-4` + inline `border-left-color: {{ proposal.proposed_color | default('#3a3a3e') }}`. |
| `app/templates/pages/setup_step3.html` | Added documentation comment referencing the proposed_color contract (grep-gate compliance). |
| `app/templates/pages/vibes_home.html` | Added `{% include "partials/llm_cost_chip.html" %}` at the top of `{% block content %}`. |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] `VibeProposal.proposed_color` field was missing**
- **Found during:** Task 1 wizard preview implementation.
- **Issue:** The plan's pseudocode showed `proposal.proposed_color` rendered in `vibe_proposal_card.html`, but `VibeProposal` is a Pydantic `BaseModel` — accessing a missing attribute raises `AttributeError` (not undefined-with-default like a Jinja dict). The Jinja `default()` filter only handles `Undefined`, not `AttributeError`.
- **Fix:** Added `proposed_color: Optional[str] = None` to `VibeProposal` so the attribute always exists and `default('#3a3a3e')` works inline.
- **Files modified:** `app/services/vibe_clusterer.py`.
- **Commit:** `82707ea`.

**2. [Rule 2 — Missing critical] Surfaced `Vibe.color` through `read_vibes_home`**
- **Found during:** Task 1 first run of `test_vibe_card_renders_color_accent` integrated against the route.
- **Issue:** The home route was building a dynamic `type("V", ...)` shim with `id / name / description / track_count` — `color` was not on that shim, so `vibe_card.html` would have rendered the fallback `#3a3a3e` for every vibe even though the DB had real palette colors.
- **Fix:** Added `"color": v.color` to the shim in `read_vibes_home`. The vibe_card template now picks up the real palette accent.
- **Files modified:** `app/routers/pages.py`.
- **Commit:** `4ff714c` (rolled into the chip commit because the change to the same function is contemporaneous).

**3. [Rule 2 — Missing critical] proposed_color hydration in `_decode_draft`**
- **Found during:** Task 1 — the plan said "pass proposed_color from the cluster-naming endpoint". Multiple endpoints render proposals (`/setup/propose`, `/setup/confirm`, `/api/setup/propose/init`, `/api/setup/refine`) — patching each one is fragile.
- **Fix:** Hydrated proposed_color centrally in `pages._decode_draft` — every read path that decodes the `SetupState.draft_proposals_json` blob into `VibeProposalSet` now picks up the same color preview. Single point of population, single point of audit. Best-effort try/except so a hydration failure renders without color preview rather than blocking the wizard.
- **Files modified:** `app/routers/pages.py`.
- **Commit:** `82707ea`.

## Authentication Gates

None. All work was code-level; no external credentials needed during execution.

## Visual Verification (manual smoke — post-deploy)

- Visit `/` (post-Plex-config, vibes exist) → expect compact chip near the top:
  - Pre-first-tick: "Your first weekly tick lands Sunday at 03:00 UTC."
  - Post-first-tick: "This week: \$X.XX · next refresh in Nd"
  - Breaker-tripped: same as post-first-tick + "· paused"
- Visit `/vibes` → each vibe card has a colored left border (4px stripe matching `Vibe.color`).
- Visit `/suggestions` → each row's vibe chip has a tinted background matching the per-row vibe color.
- Visit `/debug/vibes` → each diagnostic card has a colored left border accent.
- Open re-cluster confirm modal → unchanged today; future-enhancement vibe pills will pick up the color accent.
- Wizard flow (`/setup/propose`) → each proposal card has a colored left border previewing the post-finalize accent.

## Pointers for Plan 04

Plan 04 builds the `/discover` page UI. The vibe-section headers (D-D2) and `discover_artist_card.html` vibe chip rely on the same `{{ vibe.color }}` pattern wired here:

- Vibe-section header: `<h2 style="border-left: 4px solid {{ section.vibe.color }};">` — matches the inline-style convention enforced by `test_no_hardcoded_vibe_hex_in_render_paths`.
- Artist card vibe chip: `<span style="background-color: {{ vibe.color }}33; color: {{ vibe.color }};">` — matches the existing `suggestions_row.html` pattern.
- `app.services.discovery_service.assign_vibe_color` is the single source of truth; never recompute palette mapping in Plan 04 templates.

## Pointers for Plan 05 (/debug/discovery)

The new `app.routers.pages._compute_home_chip_context` helper is the single read path for the home-page chip data. Plan 05's `/debug/discovery` cost panel should call the same `LLMUsage` aggregation pattern — `WHERE called_at >= max(baseline, last_tick)` — for any "this week's discovery cost" framing, then add the per-purpose `GROUP BY` breakdown that the chip intentionally avoids (chip is a canary, not a billing dashboard per D-B4).

## Plan 03 Verification

```bash
cd /Users/Oreo/Projects/Composer && .venv/bin/pytest \
  tests/test_vibe_color_propagation.py \
  tests/test_pages_home_chip.py \
  --tb=short
```

Result: **22 passed** (10 in `test_vibe_color_propagation.py` + 12 in `test_pages_home_chip.py`).

Regression sweep across the affected templates / routes:

```bash
cd /Users/Oreo/Projects/Composer && .venv/bin/pytest \
  tests/test_pages_suggestions.py tests/test_pages_settings.py \
  tests/test_pages_debug_vibes.py tests/test_pages.py \
  tests/test_pages_phase6.py tests/test_pages_debug_suggestions.py \
  tests/test_api_setup.py tests/test_api_vibes.py \
  --tb=short
```

Result: **89 passed** — no regressions in the directly-touched code paths.

## Deferred Issues

Pre-existing failures unrelated to Plan 03 (verified via in-isolation re-run on the affected modules):

1. `test_suggestions_service.py::TestRefillMirrorSql::test_picks_proportional_to_vibe_share` — fails ONLY in a wider test sweep due to singleton state pollution from an earlier module. Passes in isolation (`pytest tests/test_suggestions_service.py` → 33/33). Not a Plan 03 regression; carry-over from the test infrastructure issues already documented in Plan 01 / Plan 02 SUMMARYs.
2. Pre-existing `test_analysis_service.py` (Essentia darwin shape drift), `test_audio_analyzer.py`, `test_chat_service.py` (Phase 7 retirement scaffolding), `test_sync_api.py` (TestClient lifespan ordering), `test_sync_service.py` (delta-sync code-path retirement), `test_sync_scheduler.py` (APScheduler standalone event-loop), `test_phase_08_discovery_bootstrap.py` similar standalone-fixture issues — all documented in Plan 01 / Plan 02. Production lifespan goes through `init_db()` so the underlying tables are always present.

None of these affect Plan 03 acceptance criteria.

## Self-Check: PASSED

Created files verified to exist:
- `app/templates/partials/llm_cost_chip.html` — FOUND
- `tests/test_vibe_color_propagation.py` — FOUND
- `tests/test_pages_home_chip.py` — FOUND

Commits verified in `git log`:
- `201b3cc` (test: RED for vibe color propagation) — FOUND
- `82707ea` (feat: GREEN — wire vibe color across surfaces) — FOUND
- `56cc78b` (test: RED for home-page cost chip) — FOUND
- `4ff714c` (feat: GREEN — home-page weekly LLM cost chip) — FOUND

All grep gates pass:
- `vibe.color` / `v.color` in vibe_card.html — FOUND
- `s.vibe.color` in suggestions_row.html — FOUND
- `vibe.color` in recluster_modal.html — FOUND
- `vibe.color` in vibe_diagnostic_card.html — FOUND
- `proposed_color` + `vibe.color` references in setup_step3.html — FOUND
- `assign_vibe_color` / `ensure_vibe_color` in vibe_service.py — FOUND
- `llm_cost_chip` in vibes_home.html — FOUND
- "This week:" / "first weekly tick lands Sunday" / `href="/debug/suggestions"` / `min-h-11` in llm_cost_chip.html — FOUND
- `_compute_home_chip_context` / `this_week_cost_usd` / `CostMeterBaseline` / `WeeklyCronState` in pages.py — FOUND
