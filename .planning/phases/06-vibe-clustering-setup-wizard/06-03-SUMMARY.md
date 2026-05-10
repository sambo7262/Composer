---
phase: 06-vibe-clustering-setup-wizard
plan: 03
subsystem: wizard-ui
tags: [htmx, alpine-morph, fastapi-form, jinja2, mobile-first, htmx-morph-swap, sticky-cta, sqlmodel, asyncio-semaphore, dual-marker]

# Dependency graph
requires:
  - phase: 06-vibe-clustering-setup-wizard
    plan: 01
    provides: |
      Vibe / TrackVibe / ManagedPlaylist / SetupState SQLModel tables;
      Track.pending_slot_in column + indexes; base.html alpine-morph + min-h-dvh +
      viewport-fit conventions; app/services/vibe_helpers.feature_chip_text helper.
  - phase: 06-vibe-clustering-setup-wizard
    plan: 02
    provides: |
      vibe_clusterer.initial_cluster_proposal / refine_proposals (D-32, D-34);
      VibeProposal + VibeProposalSet pydantic schemas (D-03 / D-32);
      plex_playlist_service.create_playlist (D-24 / D-27 dual-marker);
      vibe_service.slot_track (D-15 hot path; not used by Plan 03 finalize but
      activates automatically once Plan 03 ships the first Vibe rows).
  - phase: 05-plex-event-foundation-rating-sync
    provides: |
      Annotated[str, Form()] + json.loads convention (D-05 / FastAPI #10997);
      ServiceConfig + get_decrypted_credential pattern;
      LLMUsage table for refinement-turn cost circuit breaker.

provides:
  - app/routers/api_setup.py — 8 endpoints (step1/submit, rated-count, step2/submit,
    propose/init, refine, finalize, finalize/status, reset) with SetupState
    lifecycle + idempotent finalize retry support
  - app/routers/pages.py — GET / auto-redirect to /setup (D-07 / WIZ-01); 5 wizard
    page renderers (/setup, /setup/webhook, /setup/propose, /setup/confirm,
    /setup/done); /debug/vibes Plan 04 stub
  - app/main.py — api_setup router registered BEFORE pages.router; feature_chip_text
    registered as Jinja2 global
  - 5 wizard pages (setup_step1..4 + setup_done) with UI-SPEC copy verbatim
  - 14 wizard partials (wizard_layout, wizard_step_indicator, wizard_sticky_cta,
    cold_start_panel, vibe_proposal_card, vibe_name_input, feature_chip,
    seed_track_row, vibe_members_disclosure, refinement_input,
    proposal_cards_swap, refine_error, rated_count, push_to_plex_banner)
  - Module-level _finalize_status (FinalizeStatus dataclass) for the
    push_to_plex_banner state machine (mirrors backfill_service)
  - Three new test files: test_api_setup.py (10 tests), test_pages_phase6.py
    (4 tests), test_setup_templates.py (17 static-grep tests),
    test_finalize_integration.py (6 tests)
affects: [06-04, 07-01, 07-02, 08-01]

# Tech tracking
tech-stack:
  added: []  # No new libraries — pure UI surface over Plan 02's services
  patterns:
    - "Lazy-import shim for monkeypatchable async services (initial_cluster_proposal / refine_proposals / create_playlist) — mirrors taste_profile_service / vibe_clusterer module-local re-export pattern"
    - "Single-row id=1 SetupState with session.merge upsert for wizard step lifecycle (mirrors TasteProfile cache pattern)"
    - "FinalizeStatus dataclass module-level singleton for HTMX poll-based state-machine banners (mirrors BackfillStatus shape)"
    - "alpine-morph swap target convention: id=\"proposal-cards\" container + hx-swap=\"morph:innerHTML\" — preserves Alpine x-data state on each refinement-turn re-render (Pitfall 15 / D-09)"
    - "Sticky-bottom CTA composition: pb-[calc(16px+env(safe-area-inset-bottom))] + min-h-12 (Pitfall 16, 17 / UI-04)"
    - "AST walker test for include_router ordering (api_setup MUST be registered before pages.router so /setup pages aren't caught by pages.py wildcards)"

key-files:
  created:
    - "app/routers/api_setup.py — 8 endpoints + SetupState lifecycle + finalize semaphore=1 + lazy-import shims (~410 lines)"
    - "app/templates/pages/setup_step1.html — Step 1 rating-source confirm with cold-start gate at <30 + degraded-mode message at 30-49"
    - "app/templates/pages/setup_step2.html — Step 2 webhook config (reuses /api/settings/webhook/section partial via HTMX load-trigger)"
    - "app/templates/pages/setup_step3.html — THE flagship UX: initial proposal + force-k picker (initial only) + refinement input + Looks good sticky CTA"
    - "app/templates/pages/setup_step4.html — Final review: read-only proposal cards + Push to Plex sticky CTA"
    - "app/templates/pages/setup_done.html — You're all set + link to / + /debug/vibes"
    - "app/templates/pages/debug_vibes_stub.html — Plan 04 stub so Step 5 link doesn't 404"
    - "app/templates/partials/wizard_layout.html — wraps each /setup/* page; renders step indicator + content + sticky CTA blocks"
    - "app/templates/partials/wizard_step_indicator.html — 5 dots with current in accent ring"
    - "app/templates/partials/wizard_sticky_cta.html — fixed-bottom CTA with safe-area-inset + min-h-12"
    - "app/templates/partials/cold_start_panel.html — <30 rated copy + Refresh count button"
    - "app/templates/partials/vibe_proposal_card.html — name input + description input + chip + 5 seed tracks + members disclosure + silhouette line"
    - "app/templates/partials/vibe_name_input.html — Alpine click-to-edit name"
    - "app/templates/partials/feature_chip.html — text-only chip rendered from feature_chip_text(centroid)"
    - "app/templates/partials/seed_track_row.html — single-line Title — Artist row with truncate"
    - "app/templates/partials/vibe_members_disclosure.html — Alpine x-collapse expanding scrollable member list"
    - "app/templates/partials/refinement_input.html — textarea + Refine button + turn counter (D-04 / D-12)"
    - "app/templates/partials/proposal_cards_swap.html — inner-HTML morph-swap content for #proposal-cards"
    - "app/templates/partials/push_to_plex_banner.html — running / failed / completed / idle state machine (D-25 / VIBE-04)"
    - "app/templates/partials/refine_error.html — LLM-failure banner with retry copy"
    - "app/templates/partials/rated_count.html — single-span partial for cold-start Refresh count swap"
    - "tests/test_api_setup.py — 10 endpoint behavioral tests"
    - "tests/test_pages_phase6.py — 4 tests (D-07 redirect on/off + AST walker for include_router ordering)"
    - "tests/test_setup_templates.py — 17 static-grep tests over the wizard pages + partials (UI-SPEC contract)"
    - "tests/test_finalize_integration.py — 6 end-to-end tests (push banner state machine + finalize creates 3 vibes/playlists + partial-failure semantics + semaphore=1 verification)"
  modified:
    - "app/routers/pages.py — home() now redirects to /setup when Plex configured AND Vibe.count==0 AND rated_count>=1; added 5 setup page routes + /debug/vibes stub"
    - "app/main.py — api_setup imported and include_router'd BEFORE pages; feature_chip_text registered as Jinja2 global"

key-decisions:
  - "Combined Task 1 (router + main.py wiring) and Task 2 (wizard templates) into a single feat commit because the api_setup router renders the same templates Task 2 ships — both must land for tests to pass. Task 2's static-grep tests committed separately."
  - "Used asyncio.to_thread for SQLModel sync helpers (_insert_vibe_sync, _link_managed_to_vibe_sync, _insert_trackvibes_sync) inside the finalize loop — keeps the FastAPI event loop unblocked while still allowing the create_playlist call to await its inner asyncio.to_thread. Mirrors the inner-def pattern that Plan 02's plex_playlist_service uses (which the AST walker now approves)."
  - "Idempotent retry path: finalize endpoint short-circuits the Vibe + ManagedPlaylist insert if a ManagedPlaylist already links to a Vibe with the same id. The retry button on the failed banner POSTs back to the same /api/setup/finalize endpoint."
  - "Refinement-turn cap (D-04) is server-side: SetupState.refinement_turn_count is read on every /refine call. A malicious browser cannot bypass by clearing local state. After cap=10, the LLM is NEVER called again for that wizard session."
  - "The textarea user message is NEVER echoed back to the page (T-06-03-06 mitigation against XSS via morph-swap). Only the LLM-derived VibeProposalSet (vibe names, descriptions) is rendered, and Jinja2 auto-escape is on."
  - "Force-k picker (D-13) hidden after the first refinement turn — the dropdown only renders when refinement_turn_count == 0. After that, k changes happen via natural-language refinement only (e.g., 'give me 6 vibes' prompts the LLM)."

patterns-established:
  - "Lazy-import shim per service in routers — keeps the router monkeypatchable from tests AND avoids circular imports. Pattern: `async def initial_cluster_proposal(*args, **kwargs): from app.services.vibe_clusterer import initial_cluster_proposal as _fn; return await _fn(*args, **kwargs)`. Tests patch the router-local name; production code transparently delegates."
  - "FinalizeStatus state-machine dataclass at module level: idle / running / completed / failed + created/total counters. Banner partial branches on `state` string. Mirrors the BackfillStatus pattern; reusable for any future long-running mutation in Phase 7+."
  - "alpine-morph swap target convention: place id=\"proposal-cards\" on a wrapper div in the page template; have the form's hx-target point to it with hx-swap=\"morph:innerHTML\". Server returns ONLY the inner content (the partial proposal_cards_swap.html). Each card's Alpine x-data state survives the swap (click-to-edit name doesn't lose focus on refine)."
  - "Wizard layout block-inheritance: pages extend partials/wizard_layout.html which extends base.html. Layout supplies wizard_content + wizard_sticky_cta blocks; pages fill them. Sticky CTA is rendered AFTER the content div so it sits above the page chrome regardless of scroll position."

requirements-completed:
  - VIBE-01
  - VIBE-04
  - VIBE-07
  - VIBE-08
  - WIZ-01
  - WIZ-02
  - WIZ-03
  - WIZ-04
  - WIZ-05
  - WIZ-06
  - WIZ-07

# Metrics
duration: ~35min
completed: 2026-05-09
---

# Phase 6 Plan 03: Wizard + Conversational Refinement Loop Summary

**Multi-page HTMX wizard at /setup with the user-flagged "core of the app" — Step 3's conversational refinement loop where users iterate with the LLM ("merge X and Y", "add a pre-workout playlist", "rename Z") until vibes feel right, then commit via "Looks good" → Step 4 review → "Push to Plex" → Step 5 done — all rendered mobile-first portrait with sticky-bottom CTAs, alpine-morph card swaps preserving Alpine state, and 14 reusable partials.**

## Performance

- **Duration:** ~35 min (3 commits, 4 test files, 21 new source files)
- **Started:** 2026-05-09 (worktree branch worktree-agent-a7c5c8b4c3b31c18b from a711fe7)
- **Completed:** 2026-05-09
- **Tasks:** 3 / 3 complete
- **Files created:** 25 (1 router + 5 pages + 14 partials + 1 stub page + 4 test files)
- **Files modified:** 2 (app/main.py, app/routers/pages.py)

## Accomplishments

- **The 5-step wizard backbone is live.** A user with 30+ rated tracks visits / and is auto-redirected to /setup (D-07 / WIZ-01). They walk through Step 1 (rating-source confirm) → Step 2 (webhook config — conditional, skipped if already configured per D-06) → Step 3 (initial cluster proposal + conversational refinement) → Step 4 (final review) → Step 5 (done). Each step is its own URL — refresh-safe, back/forward works, SetupState.step persists. State machine is rating_source → webhook | proposing → confirming → done.

- **Step 3 — the flagship UX.** The initial proposal renders N (3-7) vibe cards (force-k picker dropdown shown ONLY on initial run per D-13). Each card has click-to-edit name, click-to-edit description, a feature chip rendered from `feature_chip_text(centroid)`, 5 closest seed tracks, a "Show all N tracks" disclosure (Alpine x-collapse), and a silhouette score footer. Below the cards: textarea + Refine button + turn counter ("Refinement N of 10"). Submit flows POST to /api/setup/refine with `Annotated[str, Form()]` body (D-05 — NEVER `pydantic.Json[Model]` inside `Form()`); the response is the proposal-cards inner HTML which HTMX swaps into #proposal-cards via `hx-swap="morph:innerHTML"`. Each card's Alpine x-data state survives the swap (Pitfall 15) — name input doesn't lose focus when the new cards land. Refinement-turn counter shows amber accent at 8-9 of 10, "save what you have or start over" at 10. Hard cap at 10 is server-side (SetupState.refinement_turn_count) — a malicious browser cannot bypass.

- **Step 4 + finalize push the wizard's user-facing GOAL into Plex.** "Push to Plex" POSTs to /api/setup/finalize; the endpoint reads SetupState.draft_proposals_json, deserializes as VibeProposalSet, then loops over every non-dropped proposal SEQUENTIALLY under `asyncio.Semaphore(1)` (D-25 — single concurrent Plex mutation). For each proposal: INSERT Vibe row → call `plex_playlist_service.create_playlist("Composer · {name}", member_keys, vibe_id)` → link the just-created ManagedPlaylist row's vibe_id to the new Vibe.id → INSERT TrackVibe(assigned_by="cluster") rows. Every playlist title carries the "Composer · " prefix (D-24 / VIBE-04 — the title-side dual marker); every persisted ManagedPlaylist row carries kind="vibe" + vibe_id pointer (D-27 — the DB-side dual marker). On any failure mid-loop, SetupState.step is left at "confirming" (NOT done) and the failed banner returns "Created N of M playlists. Retry the rest?" — the Retry button POSTs back to /api/setup/finalize and the idempotent path skips already-linked vibes.

- **All UI-SPEC contracts honored.** No Phase 6 NEW page or partial uses `text-[15px]` (rev 2 typography contract — only 13/14/20/28 sizes). No button labeled "Cancel" anywhere (UI-SPEC checker BLOCK — Plan 04 ships the dismiss "Keep current vibes"). Sticky-bottom CTA uses `env(safe-area-inset-bottom)` + `min-h-12` (Pitfall 16, 17 / UI-04). Step 4's per-vibe summary line uses `text-[14px]` Body (rev 2 fix). Done page links to / AND /debug/vibes.

- **Mobile-first verification.** All 6 wizard URLs render 200 OK end-to-end (smoke-tested with TestClient). No `min-h-screen` anywhere on Phase 6 surfaces (base.html ships `min-h-dvh` per Plan 01). Every CTA across the wizard pages + partials has `min-h-11` or `min-h-12` (44px tap target — verified by `test_button_min_h_on_every_cta`).

- **All Phase 5 + Plan 01 + Plan 02 invariants preserved.** The AST sklearn-allowlist tripwire (`test_sklearn_only_in_clusterer_module`) still green. The async-PlexAPI walker (`test_no_blocking_plexapi_in_async`) still green — the new finalize loop's `asyncio.to_thread` wrappers around inner sync functions match the Plan 02 inner-def idiom the walker approves. Phase 5's `test_no_sklearn_import` (taste_profile_service-specific) still green. All 88 prior tests pass alongside the 37 new ones.

## Task Commits

Each task was committed atomically:

1. **Task 1 + 2 (combined): wizard router api_setup + 5 setup pages + 12 partials + GET / auto-redirect** — `19cd43d` (feat)
2. **Task 2: static template tests for wizard pages + partials (UI-SPEC contract)** — `211275b` (test)
3. **Task 3: finalize integration tests + push_to_plex_banner state-machine contract** — `f2d714b` (test)

_Combined commit rationale (Task 1 + Task 2): The api_setup router renders the same templates Task 2 ships, so both had to land in a single commit for the Task 1 endpoint behavioral tests to pass. Task 2's static-grep contract tests were committed separately to keep the test work distinct from the implementation._

## Files Created/Modified

### Created — Source

- **`app/routers/api_setup.py`** (~410 lines) — 8 endpoints (`POST /step1/submit`, `GET /rated-count`, `POST /step2/submit`, `POST /propose/init`, `POST /refine`, `POST /finalize`, `GET /finalize/status`, `POST /reset`); helper functions `_get_or_create_setup_state`, `_is_webhook_already_configured`, `_count_rated_tracks`, `_latest_llm_call_id`, `_render_proposal_cards`; `FinalizeStatus` dataclass + module-level `_finalize_status` singleton; lazy-import shims for `initial_cluster_proposal` / `refine_proposals` / `create_playlist`. Module docstring documents the D-05 / FastAPI #10997 + Pitfall 15 alpine-morph invariants.
- **`app/templates/pages/setup_step1.html`** — Confirm rated tracks; cold-start panel <30; degraded-mode 30-49; sticky CTA "Continue to webhook setup".
- **`app/templates/pages/setup_step2.html`** — Configure Plex webhooks; reuses `/api/settings/webhook/section` partial via `hx-trigger="load"`; "Skip — set up later" link; sticky CTA "Continue to your vibes".
- **`app/templates/pages/setup_step3.html`** — Find your vibes; force-k picker (initial only); `id="proposal-cards"` morph-swap target; auto-load on empty draft; sticky CTA "Looks good" → /setup/confirm.
- **`app/templates/pages/setup_step4.html`** — Review your vibes; per-vibe Composer · {name} — N tracks line; Back to refine link; sticky CTA "Push to Plex" → /api/setup/finalize.
- **`app/templates/pages/setup_done.html`** — You're all set; Open vibes home → / link; View vibes diagnostics → /debug/vibes link.
- **`app/templates/pages/debug_vibes_stub.html`** — Plan 04 stub so the /debug/vibes link doesn't 404.
- **`app/templates/partials/wizard_layout.html`** — Wraps each /setup/* page with step indicator + content + sticky CTA blocks; extends base.html with empty nav block.
- **`app/templates/partials/wizard_step_indicator.html`** — 5 dots, current in accent ring; "Step N of 5 · {label}".
- **`app/templates/partials/wizard_sticky_cta.html`** — fixed-bottom + `pb-[calc(16px+env(safe-area-inset-bottom))]` + min-h-12; configurable cta_label / hx_post / form_id / button_type via Jinja2 with-blocks.
- **`app/templates/partials/cold_start_panel.html`** — <30 rated message + Refresh count button (HTMX swap into #rated-count-display).
- **`app/templates/partials/vibe_proposal_card.html`** — composes vibe_name_input + description input + feature_chip + 5 seed_track_row + vibe_members_disclosure + silhouette score line.
- **`app/templates/partials/vibe_name_input.html`** — Alpine click-to-edit name (h3 ↔ input toggle on click/blur/Enter).
- **`app/templates/partials/feature_chip.html`** — single-span text-only chip rendered from `chip_text` (call site does `{% set chip_text = feature_chip_text(proposal.centroid) %}`).
- **`app/templates/partials/seed_track_row.html`** — single-line Title — Artist with truncate.
- **`app/templates/partials/vibe_members_disclosure.html`** — Alpine x-collapse expanding scrollable member list.
- **`app/templates/partials/refinement_input.html`** — textarea + Refine button + turn counter; `hx-post="/api/setup/refine"` + `hx-target="#proposal-cards"` + `hx-swap="morph:innerHTML"`.
- **`app/templates/partials/proposal_cards_swap.html`** — inner-HTML morph-swap content (iterates VibeProposalSet → vibe_proposal_card.html); also renders refinement_input below + cap warning if turn=10.
- **`app/templates/partials/push_to_plex_banner.html`** — 4-state machine (running with every-2s poll → failed with Retry button → completed with Continue → /setup/done → idle blank).
- **`app/templates/partials/refine_error.html`** — LLM-failure banner with retry copy + retains prior cards.
- **`app/templates/partials/rated_count.html`** — single-span partial for cold-start Refresh count HTMX swap target.

### Created — Tests

- **`tests/test_api_setup.py`** — 10 endpoint behavioral tests using TestClient + monkeypatch on the lazy-import shims.
- **`tests/test_pages_phase6.py`** — 4 tests (D-07 redirect with rated_track + no Vibe; no redirect with Vibe; no redirect with no rated; AST walker asserting `api_setup` `include_router` precedes `pages` in app/main.py).
- **`tests/test_setup_templates.py`** — 17 static-grep tests over the wizard pages + partials covering UI-SPEC contract (extends wizard_layout, morph-swap target on Step 3, no text-[15px], no Cancel labels, sticky CTA safe-area + min-h-12, refinement_input post target, vibe card x-data, force-k initial only, cold-start <30 guard, copy verbatim, Step 4 text-[14px] not 15, done links to /debug/vibes, every CTA min-h-11/12, feature_chip_text Jinja2 global, push banner 3-state machine + every-2s poll).
- **`tests/test_finalize_integration.py`** — 6 end-to-end tests (push banner state-machine contract + finalize creates 3 Vibe/ManagedPlaylist/TrackVibe rows + handles 3rd-call failure leaving 2 ManagedPlaylist rows + step != done + observed-concurrent-count never exceeds 1).

### Modified

- **`app/routers/pages.py`** — Added `from app.models.vibe import SetupState, Vibe`; `from fastapi.responses import RedirectResponse`. Inside `home()`: after `plex_configured` check, count rated tracks + count vibes; if `vibe_count == 0 and rated_count >= 1`, return `RedirectResponse("/setup", status_code=302)`. Appended 5 wizard page handlers (`setup_step1` / `setup_step2` / `setup_step3` / `setup_step4` / `setup_done`) + `/debug/vibes` stub + helpers `_get_or_init_setup_state`, `_count_rated`, `_decode_draft`.
- **`app/main.py`** — Added `api_setup` to the routers import tuple; `app.include_router(api_setup.router)` BEFORE `app.include_router(pages.router)`; registered `feature_chip_text` as `templates.env.globals["feature_chip_text"]` immediately after the `templates = Jinja2Templates(...)` line.

## Decisions Made

See `key-decisions` in frontmatter. Notable:

1. **Combined Task 1 + Task 2 into a single implementation commit** because the api_setup router renders templates Task 2 ships — they cannot land separately without breaking Task 1's tests. Task 2's static-grep tests committed separately to keep the test work distinct from the implementation.
2. **`asyncio.to_thread` wrappers in finalize loop** for the Vibe / ManagedPlaylist / TrackVibe inserts — keeps the FastAPI event loop unblocked while still letting `create_playlist` await its own inner `asyncio.to_thread`. Mirrors Plan 02's inner-def pattern that the AST walker approves.
3. **Refinement-turn cap is server-side** (SetupState.refinement_turn_count). A malicious browser cannot bypass by clearing local state. Cap = 10 (D-04).
4. **Force-k picker hidden after first refinement turn** (D-13) — the dropdown only renders when refinement_turn_count == 0.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] api_setup.py module docstring referenced `pydantic.Json[Model]` literally — false-positive grep match.**
- **Found during:** Task 3 final verification grep `grep -c "pydantic.Json\[" app/routers/api_setup.py` returned 1 (expected 0 per plan acceptance criteria).
- **Issue:** The module docstring originally read "NEVER pydantic.Json[Model]" — meant as a warning comment, but it tripped the literal grep check the plan uses to assert the rule.
- **Fix:** Rephrased the docstring to "NEVER use pydantic-Json inside Form()" without the bracket characters.
- **Files modified:** `app/routers/api_setup.py` (module docstring only).
- **Verification:** `grep -c "pydantic.Json\["` now returns 0; behavior unchanged.
- **Committed in:** `f2d714b` (bundled into Task 3 commit).

**2. [Rule 2 — Missing critical] Skip-link `<a href="...">` had no `min-h-11` — failed `test_button_min_h_on_every_cta` indirectly via the broader 44px tap-target contract.**
- **Found during:** Task 1 implementation; the plan's UI-SPEC requires every tappable element to be ≥44×44px.
- **Issue:** The Step 2 "Skip — set up later in Settings" link was originally `class="block text-[13px] text-text-secondary underline mt-4"` — no min-h-11 → not 44px tall on mobile.
- **Fix:** Added `min-h-11 inline-flex items-center` to the Skip link, the Back-to-refine link, and the View-vibes-diagnostics link.
- **Files modified:** `app/templates/pages/setup_step2.html`, `app/templates/pages/setup_step4.html`, `app/templates/pages/setup_done.html`.
- **Verification:** All 17 setup_templates static tests pass; mobile tap-target contract satisfied for every link.
- **Committed in:** `19cd43d` (Task 1 commit).

**3. [Rule 3 — Blocking] Step 3 sticky CTA originally used a form POST to /setup/confirm but the plan-described pattern was hx-post — turned out a plain `action="/setup/confirm" method="get"` is simpler and refresh-safe.**
- **Found during:** Task 1 implementation while mapping the "Looks good" CTA flow.
- **Issue:** The "Looks good" button is a navigation, not a server mutation — Step 4 just renders the existing draft for review before the actual finalize. An hx-post wrapping was over-complex.
- **Fix:** `<form id="looks-good-form" action="/setup/confirm" method="get">` — plain HTML form GET; the sticky CTA partial accepts `form_id` so its submit button correctly links to this form.
- **Files modified:** `app/templates/pages/setup_step3.html`.
- **Verification:** End-to-end TestClient smoke test confirms /setup → /setup/propose → /setup/confirm → /setup/done flow.
- **Committed in:** `19cd43d` (Task 1 commit).

---

**Total deviations:** 3 auto-fixed (1 Rule 1 bug, 1 Rule 2 missing critical, 1 Rule 3 blocking).
**Impact on plan:** All three deviations are minor surface-polishing — none change the wizard's behavior, scope, or contract. The grep-style verification rule is now satisfied without weakening the actual code-level invariant.

## Issues Encountered

None blocking. The TDD cycle for each task was straightforward:

- **Task 1 RED:** All 14 tests fail with 404 (no router) and AST error (no `api_setup` in main.py).
- **Task 1 GREEN:** Built api_setup.py + minimal templates + main.py wiring; 14/14 pass on first integration.
- **Task 2 GREEN-direct:** Static-grep tests passed on the templates I'd already built in Task 1 (combined commit rationale).
- **Task 3 RED → GREEN:** Behavioral integration tests passed first time; the finalize endpoint already had idempotent retry handling (planned for Task 3 but built into Task 1's `/finalize` for completeness).

## TDD Gate Compliance

This plan's frontmatter has `type: execute` (not `tdd`), so plan-level RED→GREEN→REFACTOR is not enforced. Each task individually had `tdd="true"`:

- **Task 1:** RED (test_api_setup + test_pages_phase6 → 14 failures: 404 / no module / AST mismatch) → GREEN (api_setup.py + pages.py edits + main.py edits + minimum templates → 14/14 pass).
- **Task 2:** GREEN-direct (templates already shipped in Task 1's commit; static-grep tests added to assert the contract). The "RED would have failed" stage is the missing test file — once added, it passed against the shipped templates.
- **Task 3:** RED (test_finalize_integration → 6 failures: AttributeError on `_finalize_status` for the running-state grep, etc.) → GREEN (already-shipped api_setup.py finalize endpoint passed all 6 tests on first run).

All commits use `feat(06-03)` or `test(06-03)` prefix per project convention.

## Verification Run

Final Plan 03 + Phase 6 + Phase 5 invariant suite:

```
pytest tests/test_api_setup.py tests/test_pages_phase6.py tests/test_setup_templates.py \
       tests/test_finalize_integration.py tests/test_event_handlers.py \
       tests/test_taste_profile_service.py tests/test_database.py tests/test_database_phase6.py \
       tests/test_base_html_conventions.py tests/test_vibe_helpers.py tests/test_vibe_clusterer.py \
       tests/test_plex_playlist_service.py tests/test_vibe_service.py \
       tests/test_event_handlers_phase6.py tests/test_pages.py
=> 112 passed, 1 warning in 9.32s
```

AST tripwire still green:

```
pytest tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async \
       tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module \
       tests/test_taste_profile_service.py::test_no_sklearn_import
=> 3 passed
```

Mobile-first verification (UI-SPEC §"Mobile-First Verification Checklist"):

```
$ grep -c "min-h-screen" app/templates/pages/setup_*.html app/templates/partials/wizard_*.html
0     # legacy class gone

$ grep -c "text-\[15px\]" app/templates/pages/setup_*.html app/templates/partials/wizard_*.html ...
0     # rev 2 typography honored

$ grep -c ">Cancel<" app/templates/pages/setup_*.html app/templates/partials/wizard_*.html
0     # checker BLOCK satisfied
```

End-to-end render smoke test (TestClient against the live FastAPI app):

```
GET /setup -> 200
GET /setup/webhook -> 200
GET /setup/propose -> 200
GET /setup/confirm -> 200
GET /setup/done -> 200
GET /debug/vibes -> 200
```

## Plan Verification Greps (per plan acceptance criteria)

```
grep -c "@router.post\|@router.get" app/routers/api_setup.py        => 8 (≥7 ✓)
grep -c "from app.services.vibe_clusterer\|..." app/routers/api_setup.py => 6 (≥3 ✓)
grep -c 'Annotated\[str, Form()\]' app/routers/api_setup.py         => 2 (≥1 ✓)
grep -c "pydantic.Json\[" app/routers/api_setup.py                  => 0 (=0 ✓)
grep -c "HX-Redirect" app/routers/api_setup.py                      => 4 (≥4 ✓)
grep -c "asyncio.Semaphore(1)" app/routers/api_setup.py             => 2 (≥1 ✓)
grep -c "from app.routers import api_setup" app/main.py             => 1 (=1 via import block) ✓
grep -c "app.include_router(api_setup" app/main.py                  => 1 ✓
grep -c "/setup" app/routers/pages.py                               => 13 (≥5 ✓)
grep -c 'RedirectResponse("/setup"' app/routers/pages.py            => 1 (=1 ✓)
grep -c "/debug/vibes" app/routers/pages.py                         => 1 ✓
grep -c "feature_chip_text" app/main.py                             => 2 (≥1 ✓)
grep -c "Continue to webhook setup" app/templates/pages/setup_step1.html => 1 ✓
grep -c "Continue to your vibes" app/templates/pages/setup_step2.html    => 1 ✓
grep -c "Looks good" app/templates/pages/setup_step3.html                => 1 ✓
grep -c "Push to Plex" app/templates/pages/setup_step4.html              => 1 ✓
grep -c "/debug/vibes" app/templates/pages/setup_done.html               => 1 ✓
grep -c "morph:innerHTML" app/templates/pages/setup_step3.html           => 2 (≥1 ✓)
grep -c 'id="proposal-cards"' app/templates/pages/setup_step3.html       => 1 ✓
grep -c "env(safe-area-inset-bottom)" app/templates/partials/wizard_sticky_cta.html => 1 ✓
grep -c "_finalize_status\|FinalizeStatus" app/routers/api_setup.py     => 7 (≥2 ✓)
grep -c "every 2s" app/templates/partials/push_to_plex_banner.html      => 1 ✓
```

## Phase 7 Readiness — what Plan 04 + downstream phases inherit

- **Wizard backbone is complete.** Plan 04's "Run setup wizard again" link can simply POST to `/api/setup/reset` and the user re-enters the wizard at /setup. Plan 04's re-cluster button can reuse `/api/setup/propose/init` with a different commit endpoint (or `/api/setup/refine` with `recluster_mode=True` if desired).
- **Push to Plex is proven.** Plan 04's re-cluster commit can reuse the same finalize loop pattern (semaphore=1; INSERT Vibe → create_playlist → link ManagedPlaylist.vibe_id → INSERT TrackVibe), differing only by archiving stale Vibes (D-21 / D-22 manual-stickiness preservation).
- **/debug/vibes stub is in place.** Plan 04 just needs to extend the existing handler with the actual diagnostic data (Vibe rows, member counts, silhouette scores, recent EventLog rows for slot/unslot activity).
- **vibe_service.slot_track is dormant but ready.** Once Plan 03's finalize ships the first Vibe rows, the always-on RatingChanged → slot_track hot path activates automatically (Plan 02 wired it). New ratings will slot into matching vibes within seconds — exactly the "live in Plex" promise on /setup/done.
- **The user-facing GOAL of Phase 6 is reachable end-to-end.** A fresh user with 30+ rated tracks can complete the wizard from / → /setup → /setup/webhook (or skipped) → /setup/propose (with refinement loop) → /setup/confirm → /setup/done and end up with N (3-7) Composer · {name} playlists in Plex, each populated via the initial slot-in pass.

## Confirmation: Cost Circuit Breaker Engaged

The Phase 5 LLMUsage scaffolding (50/day quota inherited via the AnthropicClient docstring DESIGN NOTE) IS engaged for all `vibe_clustering_*` purpose strings:

- `vibe_clustering_initial` — single call per /setup/propose/init (idempotent — re-renders from cache if draft already exists).
- `vibe_clustering_refine` — at most 10 calls per wizard session (D-04 cap, server-side enforced via SetupState.refinement_turn_count).
- `vibe_clustering_recluster` — Plan 04 will use this purpose; Plan 03 currently passes `recluster_mode=False` everywhere.

`SetupState.last_llm_call_id` is set on every /propose/init and /refine call (`_latest_llm_call_id(session, "vibe_clustering_")`) — feeds the future "Re-show last cluster proposal" diagnostic on /debug/vibes (D-36 / Plan 04).

## Confirmation: Dual-Marker Rule (D-27 / OPS-06)

Every Plex playlist Composer creates via `/api/setup/finalize` has BOTH markers:

1. **Title-side:** `"Composer · " + proposal.name` — `plex_playlist_service.create_playlist` raises ValueError if the name doesn't start with `"Composer · "` (Plan 02 enforced).
2. **DB-side:** A `ManagedPlaylist(kind="vibe", vibe_id=..., plex_rating_key=...)` row inserted via `_insert_managed_playlist_sync` (Plan 02) inside the create_playlist call. We then link `vibe_id` after the new Vibe is INSERT'd. UNIQUE(plex_rating_key) at the SQLite layer (Plan 01) prevents duplicates.

The dual-marker is verified at write time (Plan 02's `is_managed_playlist`) and inherited by all subsequent mutations. Plan 03 introduces no new write paths that bypass the dual-marker check.

## Self-Check: PASSED

Files referenced in this SUMMARY were verified to exist immediately after writing:

- `app/routers/api_setup.py` — FOUND (`grep -c "@router" app/routers/api_setup.py` = 8)
- `app/templates/pages/setup_step1.html` — FOUND
- `app/templates/pages/setup_step2.html` — FOUND
- `app/templates/pages/setup_step3.html` — FOUND
- `app/templates/pages/setup_step4.html` — FOUND
- `app/templates/pages/setup_done.html` — FOUND
- `app/templates/pages/debug_vibes_stub.html` — FOUND
- `app/templates/partials/wizard_layout.html` — FOUND
- `app/templates/partials/wizard_step_indicator.html` — FOUND
- `app/templates/partials/wizard_sticky_cta.html` — FOUND
- `app/templates/partials/cold_start_panel.html` — FOUND
- `app/templates/partials/vibe_proposal_card.html` — FOUND
- `app/templates/partials/vibe_name_input.html` — FOUND
- `app/templates/partials/feature_chip.html` — FOUND
- `app/templates/partials/seed_track_row.html` — FOUND
- `app/templates/partials/vibe_members_disclosure.html` — FOUND
- `app/templates/partials/refinement_input.html` — FOUND
- `app/templates/partials/proposal_cards_swap.html` — FOUND
- `app/templates/partials/push_to_plex_banner.html` — FOUND
- `app/templates/partials/refine_error.html` — FOUND
- `app/templates/partials/rated_count.html` — FOUND
- `tests/test_api_setup.py` — FOUND (10 tests)
- `tests/test_pages_phase6.py` — FOUND (4 tests)
- `tests/test_setup_templates.py` — FOUND (17 tests)
- `tests/test_finalize_integration.py` — FOUND (6 tests)

Commits referenced in this SUMMARY were verified via `git log --oneline`:

- `19cd43d` — FOUND (Task 1 + Task 2 templates combined)
- `211275b` — FOUND (Task 2 static template tests)
- `f2d714b` — FOUND (Task 3 finalize integration tests)
