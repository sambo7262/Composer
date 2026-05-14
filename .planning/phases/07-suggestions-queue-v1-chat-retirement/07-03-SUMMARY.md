---
phase: 07-suggestions-queue-v1-chat-retirement
plan: 03
subsystem: suggestions-page-ui
tags: [htmx, alpine, tailwind, mobile-first, suggestions-queue, v1-chat-retirement, debug-pages, tdd]

# Dependency graph
requires:
  - phase: 06-vibe-clustering-llm-pipeline
    provides: ManagedPlaylist(kind), Vibe model, base.html scaffold (min-h-dvh + viewport-fit=cover + hx-ext alpine-morph), llm_progress_card.html partial
  - phase: 07-suggestions-queue-v1-chat-retirement-plan-01
    provides: SuggestionsMirror table, drain_track_from_mirror, ManagedPlaylist(kind='suggestions') sentinel
  - phase: 07-suggestions-queue-v1-chat-retirement-plan-02
    provides: handle_dismiss_track, refill_suggestions_for_vibe, RefillTriggerLog, NegativeSignal, llm_cost_breaker.get_state, /api/vibes/{id}/find-candidates endpoint, /api/vibes/last-llm-call/progress (suggestions_% match)
provides:
  - Mobile-first base shell — bottom tab bar (Vibes/Suggestions/Discover/Settings) hidden at md+ via Tailwind, top nav (also 4 items) on desktop, min-h-11 (44px) on every tappable, env(safe-area-inset-bottom) gutter, alpine-morph carry-forward
  - /suggestions page (D-08 compact list, D-09 tap-to-expand, D-10 dismiss inside expanded view); empty-state reuses llm_progress_card
  - /vibes landing page (UI-01) with per-vibe cards (track_count + Find candidates CTA below 25)
  - / rewires to /vibes content when Plex configured + vibes exist; legacy chat.html removed from the home() return path
  - POST /api/suggestions/{rating_key}/dismiss endpoint (HTMX outerHTML target — empty 200 body removes the row)
  - GET /chat returns 404; every POST /api/chat/* returns 404 (UI-06 retirement notice body)
  - app/templates/_archived/{chat,chat_message,playlist_card}.html (data preserved, templates moved not deleted)
  - GET /debug index page (DEBUG-05) linking to /debug/events, /debug/vibes, /debug/suggestions, /debug/discovery placeholder
  - GET /debug/suggestions page (DEBUG-03) — cost-breaker state + current queue + last 20 RefillTriggerLog + last 20 LLMUsage(purpose LIKE 'suggestions_%') + last 20 NegativeSignal
  - Settings footer "View diagnostics →" link to /debug
affects: [Phase 8 — /discover route ships there]

# Tech tracking
tech-stack:
  added: []  # all reuse: jinja2, htmx, alpine.js, tailwind, fastapi
  patterns:
    - "Bottom tab bar: fixed-bottom mobile <nav>, md:hidden, env(safe-area-inset-bottom) padding, min-h-11 anchor — every v2 page inherits via base.html include (Phase 7 UI-02/03/04 / Pitfalls 16/17/18)"
    - "Tap-to-expand inline rationale: <li x-data={expanded:false}> + <button @click='expanded=!expanded'> + <div x-show='expanded' x-cloak> — D-09 / replaces v1's swipe-and-modal pattern"
    - "Two-tap dismiss: dismiss button only visible inside the expanded panel; HTMX outerHTML swap to empty body removes the row (D-10 / Pitfall 18 — never accidental from compact-row mis-tap)"
    - "v1 surface retirement: keep router registered, replace every endpoint body with explicit 404 (better than route removal — preserves URL surface as a 'Gone' signal for any external consumer)"
    - "Templates archived under _archived/ rather than deleted — UI-06 deferred-ideas data archival policy, AND keeps git history immediately usable for any future export tooling"
    - "/debug/suggestions reads breaker state from in-process module singleton (llm_cost_breaker.get_state()) rather than aggregating from RefillTriggerLog (singleton == lower-latency, accurate-as-of-last-check_or_raise — same approach Plan 02 used for the settings cost meter)"

key-files:
  created:
    - app/routers/api_suggestions.py
    - app/templates/pages/suggestions.html
    - app/templates/pages/vibes_home.html
    - app/templates/pages/debug_index.html
    - app/templates/pages/debug_suggestions.html
    - app/templates/partials/bottom_tab_bar.html
    - app/templates/partials/suggestions_row.html
    - app/templates/partials/suggestion_expanded.html
    - app/templates/partials/vibe_card.html
    - tests/test_mobile_first_conventions.py
    - tests/test_pages_suggestions.py
    - tests/test_api_suggestions.py
    - tests/test_chat_retirement.py
    - tests/test_pages_debug_suggestions.py
  modified:
    - app/main.py
    - app/routers/api_chat.py
    - app/routers/pages.py
    - app/templates/base.html
    - app/templates/partials/nav.html
    - app/templates/pages/settings.html
  archived:
    - app/templates/pages/chat.html        -> app/templates/_archived/chat.html
    - app/templates/partials/chat_message.html -> app/templates/_archived/chat_message.html
    - app/templates/partials/playlist_card.html -> app/templates/_archived/playlist_card.html

key-decisions:
  - "v1 chat router stays registered with explicit 404 bodies (rather than removed entirely). This signals 'gone' on the documented URL surface — better than 405 (method-not-allowed) or generic FastAPI fallthrough — and preserves the router import path so the model layer (chat_service / playlist data) keeps importing cleanly."
  - "Templates archived under app/templates/_archived/ (file move via `git mv`), not deleted. Honors UI-06 deferred-ideas: 'v1 chat data archival policy — composer.chats and composer.playlist tables stay in DB untouched. If the user wants a future export tool, that's a separate quick task.' The archived templates plus the surviving Python data layer make that future export trivial."
  - "/discover tab in the bottom bar links to /discover even though Phase 8 hasn't shipped that route. Rendering the link now means the user sees the full mental model and the tab bar template needs no changes when /discover lands. The unrouted link will return FastAPI's default 404 — accepted, documented in the partial's comment block."
  - "min-h-11 source-count vs rendered-count: the bottom_tab_bar partial uses a Jinja for-loop over 4 tabs, so the source has only 1 (or 2) literal `min-h-11` strings even though the rendered HTML has 4. test_bottom_tab_bar_tap_targets_min_h_11 was rewritten to render the template before counting — pinning the actual user-visible invariant rather than a source-line count that misleads."
  - "Settings footer keeps the per-surface deep-links (events / vibes) above the new /debug index link, NOT replaces them. Direct deep-links remain useful when triaging a specific subsystem; the /debug index is the discoverability layer for the new /debug/suggestions surface."

patterns-established:
  - "Bottom tab bar pattern (UI-02): fixed bottom inset-x-0 md:hidden, pb-[env(safe-area-inset-bottom)], grid grid-cols-N, min-h-11 anchors, class-based active state (border-t-2 border-accent + text-text-primary)."
  - "Two-tap dismiss pattern (D-10): expand row → dismiss button visible only inside expanded panel; HTMX outerHTML swap targeting #suggestion-row-{rk} with empty 200 body removes the row."
  - "Surface retirement pattern (UI-06): keep router registered, return 404 with retirement-notice body on every endpoint. Preserves URL surface as explicit 'Gone' signal."

requirements-completed:
  - UI-01
  - UI-02
  - UI-03
  - UI-04
  - UI-05
  - UI-06
  - DEBUG-03
  - DEBUG-05

# Metrics
duration: ~75 min
completed: 2026-05-14
---

# Phase 7 Plan 03: Suggestions Page UI + v1 Chat Retirement + Debug Surfaces

**Mobile-first navigational shell (bottom tab bar + h-dvh + safe-area-inset + 44px tap targets), the user-facing /suggestions queue page (D-08 compact list, D-09 tap-to-expand inline rationale, D-10 two-tap dismiss), the v2 vibes home as the landing page (UI-01), the v1 mood-chat surface retired with templates archived (UI-06), and the /debug index + /debug/suggestions diagnostic surfaces (DEBUG-03 / DEBUG-05) — all behind a 59-test suite shipped in 6 commits via TDD RED→GREEN cycles.**

## Performance

- **Duration:** ~75 min
- **Started:** 2026-05-14T05:00:00Z (approx)
- **Completed:** 2026-05-14T06:15:00Z (approx)
- **Tasks:** 3 (each TDD: RED → GREEN; no REFACTOR commits needed)
- **Commits:** 6 (3 test commits + 3 feat commits)
- **Files created:** 9 templates/routers + 5 test files = 14
- **Files modified:** 6
- **Files archived:** 3

## Accomplishments

### Task 1 — Mobile-first base shell (UI-02 / UI-03 / UI-04 / UI-05)

- `app/templates/base.html`: `<main>` now has `pb-[calc(env(safe-area-inset-bottom)+64px)]` reserving space for the fixed mobile tab bar (Pitfall 16). Includes `partials/bottom_tab_bar.html` at the end of `<body>`. `min-h-dvh` + `viewport-fit=cover` + `hx-ext='alpine-morph'` retained from Phase 6.
- `app/templates/partials/bottom_tab_bar.html`: NEW — fixed-bottom mobile nav with 4 tabs (Vibes / Suggestions / Discover / Settings). Hidden at md+ via `md:hidden`. Each anchor has `min-h-11` (44px). Active state uses class switch (`border-t-2 border-accent` + `text-text-primary`), never `:hover`-only (Pitfall 18). `pb-[env(safe-area-inset-bottom)]` clears iOS home-indicator gutter.
- `app/templates/partials/nav.html`: rewritten. v1 "Compose" link removed; "Vibes" + "Suggestions" links added. Hidden on mobile via `hidden md:block` (bottom tab bar takes over). Brand "Composer" linkified to `/`. Every `<a>` has `min-h-11`. Settings gear icon retained for desktop accessibility.

### Task 2 — /suggestions queue UI + vibes home + v1 chat retirement

- `app/templates/pages/suggestions.html`: SuggestionsMirror compact list with empty-state loader (reuses `llm_progress_card.html` per CONTEXT discretion).
- `app/templates/partials/suggestions_row.html`: D-08 compact row — 48px album art (`w-12 h-12`) + title + artist + tiny vibe chip + `min-h-11` tap target + Alpine `@click` expansion (D-09). All Track/Vibe fields rendered with explicit `| e` (T-07-03-01..02).
- `app/templates/partials/suggestion_expanded.html`: inline expanded panel with rationale + `plexamp://` deeplink + Dismiss button. Dismiss uses `hx-post` + `hx-target="#suggestion-row-{rk}"` + `hx-swap="outerHTML"` so the empty 200 response from the dismiss endpoint removes the row (D-10).
- `app/templates/pages/vibes_home.html`: per-vibe card grid (UI-01).
- `app/templates/partials/vibe_card.html`: SUGG-10 — Find candidates CTA shown when `track_count < 25`, posts to `/api/vibes/{id}/find-candidates` (Plan 02 endpoint).
- `app/routers/api_suggestions.py`: NEW — `POST /api/suggestions/{rk}/dismiss` awaits `handle_dismiss_track` (Plan 02). Best-effort try/except so a Plan 02 raise still returns 200 (avoid stuck-row UX).
- `app/routers/pages.py`: `home()` rewires from `chat.html` to `read_vibes_home()`; `/vibes` returns `vibes_home` with `TrackVibe` count per vibe; `/suggestions` reads `SuggestionsMirror` ordered by position; `/chat` returns 404 (UI-06).
- `app/routers/api_chat.py`: every endpoint replaced with a 404 returning a retirement notice. Router stays registered so consumers see explicit 404 (not 405 / fallthrough). Function signatures stripped to bare async defs — bodies do not parse the original request shapes.
- `app/main.py`: registers `api_suggestions`; `api_chat` still imported for the explicit-404 surface.
- Templates archived (data preserved per UI-06 deferred-ideas):
  - `app/templates/_archived/chat.html` (was `pages/chat.html`)
  - `app/templates/_archived/chat_message.html` (was `partials/chat_message.html`)
  - `app/templates/_archived/playlist_card.html` (was `partials/playlist_card.html`)

### Task 3 — /debug index + /debug/suggestions + settings footer link (DEBUG-03 / DEBUG-05)

- `app/templates/pages/debug_index.html`: 4-link list (events, vibes, suggestions, discovery placeholder for Phase 8). Plain HTML, `min-h-11` per anchor (UI-04). Each list item is a card with surface name + one-line description.
- `app/templates/pages/debug_suggestions.html`: 5 sections in render order:
  1. Cost-breaker state (`<pre>` for copy)
  2. Current queue (`<code>` + position + vibe + score + rationale)
  3. Last 20 refill triggers (`<table>`)
  4. Last 20 LLM calls filtered by `purpose LIKE 'suggestions_%'` (`<table>`)
  5. Last 20 negative signals (`<table>`)
  Track / Vibe / artist / rationale all rendered with explicit `| e` for XSS escape (T-07-03-01..03).
- `app/routers/pages.py` adds:
  - `GET /debug` — renders `debug_index.html`. `active_page='settings'` so the bottom tab bar highlights Settings (the parent surface).
  - `GET /debug/suggestions` — reads SuggestionsMirror ORDER BY position; RefillTriggerLog DESC LIMIT 20; LLMUsage WHERE purpose LIKE 'suggestions_%' DESC LIMIT 20; NegativeSignal DESC LIMIT 20; breaker state from `llm_cost_breaker.get_state()`.
- `app/templates/pages/settings.html` footer:
  - New top link: `View diagnostics →` with `href="/debug"` (DEBUG-05).
  - Per-surface event/vibes links retained so direct deep-links still work.

## Bottom tab bar — final shape

Four tabs visible at mobile breakpoint (`md:hidden` flips it off at md+):

| Slug          | Href           | Label         | Notes                                      |
|---------------|----------------|---------------|--------------------------------------------|
| `vibes`       | `/vibes`       | Vibes         | UI-01 landing                              |
| `suggestions` | `/suggestions` | Suggestions   | Phase 7 queue UI                           |
| `discover`    | `/discover`    | Discover      | Phase 8 placeholder — link returns default 404 until shipped |
| `settings`    | `/settings`    | Settings      | Existing surface                            |

Active state via `border-t-2 border-accent` + `text-text-primary` class switch (no `:hover`-only — Pitfall 18). `pb-[env(safe-area-inset-bottom)]` clears the iOS home-indicator gutter.

## /api/suggestions endpoint list

| Method | Path                                  | Purpose                                                  |
|--------|---------------------------------------|----------------------------------------------------------|
| POST   | `/api/suggestions/{rating_key}/dismiss` | D-10 — user dismisses; awaits `handle_dismiss_track` (Plan 02), returns empty 200 (HTMX outerHTML target removes the row) |

## /debug index link list

- `/debug/events` — Plex webhook + poll event log (Phase 5)
- `/debug/vibes` — Vibe centroids + slot-in decisions (Phase 6 / 6.2)
- `/debug/suggestions` — Queue contents + refill triggers + LLM cost (Phase 7)
- `/debug/discovery` — Phase 8 — not yet shipped (rendered as muted placeholder, no link)

## Settings footer link text

> View diagnostics → (`href="/debug"`)

Top-level link to the new `/debug` index. The existing per-surface deep-links ("View event diagnostics →" / "View vibes diagnostics →") are retained below it, so direct deep-links from triage notes still work.

## Task Commits

1. **Task 1 RED — failing mobile-first conventions tests** — `bd19d14` (`test`)
2. **Task 1 GREEN — base shell + bottom tab bar + nav rewire** — `6b0a02e` (`feat`)
3. **Task 2 RED — failing /suggestions + chat retirement tests** — `c6b9134` (`test`)
4. **Task 2 GREEN — /suggestions UI + vibes home + chat retirement** — `b239e3c` (`feat`)
5. **Task 3 RED — failing /debug + settings footer tests** — `e1dda93` (`test`)
6. **Task 3 GREEN — /debug index + /debug/suggestions + footer link** — `bf35c3c` (`feat`)

## Files Created / Modified / Archived

### Created (templates + routers)

- `app/routers/api_suggestions.py` — `POST /api/suggestions/{rk}/dismiss`
- `app/templates/pages/suggestions.html` — SuggestionsMirror compact list page
- `app/templates/pages/vibes_home.html` — UI-01 vibes landing
- `app/templates/pages/debug_index.html` — DEBUG-05 index
- `app/templates/pages/debug_suggestions.html` — DEBUG-03 diagnostic surface
- `app/templates/partials/bottom_tab_bar.html` — UI-02 mobile bottom nav
- `app/templates/partials/suggestions_row.html` — D-08 compact row
- `app/templates/partials/suggestion_expanded.html` — D-09/D-10 expanded panel
- `app/templates/partials/vibe_card.html` — UI-01 / SUGG-10 per-vibe card

### Created (tests)

- `tests/test_mobile_first_conventions.py` — 15 tests
- `tests/test_pages_suggestions.py` — 16 tests
- `tests/test_api_suggestions.py` — 3 tests
- `tests/test_chat_retirement.py` — 10 tests
- `tests/test_pages_debug_suggestions.py` — 9 tests

### Modified

- `app/main.py` — register `api_suggestions` router; comment that `api_chat` is now retired-but-registered for explicit 404 surface
- `app/routers/api_chat.py` — every endpoint body replaced with 404 + retirement notice
- `app/routers/pages.py` — `home()` rewires to `read_vibes_home`; new `/vibes`, `/suggestions`, `/debug`, `/debug/suggestions` routes; `/chat` returns 404
- `app/templates/base.html` — `<main>` adds `pb-[calc(env(safe-area-inset-bottom)+64px)]`; includes `partials/bottom_tab_bar.html`
- `app/templates/partials/nav.html` — rewritten: v1 "Compose" link removed, "Vibes" + "Suggestions" added, `hidden md:block`, `min-h-11` per anchor
- `app/templates/pages/settings.html` — footer prepends `View diagnostics →` link to `/debug`

### Archived (file moves via `git mv`)

- `app/templates/pages/chat.html` → `app/templates/_archived/chat.html`
- `app/templates/partials/chat_message.html` → `app/templates/_archived/chat_message.html`
- `app/templates/partials/playlist_card.html` → `app/templates/_archived/playlist_card.html`

## Decisions Made

1. **v1 chat router stays registered with explicit 404 bodies** (rather than removed entirely). This signals "gone" on the documented URL surface — better than 405 (method-not-allowed) or generic FastAPI fallthrough — and preserves the router import path so the model layer (`chat_service` / `playlist` data) keeps importing cleanly. The retirement-notice body links readers to `/suggestions` and `/vibes`.

2. **Templates archived under `app/templates/_archived/`** (file move via `git mv`), not deleted. Honors UI-06 deferred-ideas: "v1 chat data archival policy — `composer.chats` and `composer.playlist` tables stay in DB untouched. If the user wants a future export tool, that's a separate quick task." The archived templates plus the surviving Python data layer make that future export trivial.

3. **`/discover` tab links to `/discover` even though Phase 8 hasn't shipped that route.** Rendering the link now means the user sees the full mental model and the tab bar template needs no changes when `/discover` lands. The unrouted link will return FastAPI's default 404 — accepted, documented in the partial's comment block.

4. **min-h-11 source-count vs rendered-count.** The bottom_tab_bar partial uses a Jinja `for` loop over 4 tabs, so the source has only 1 (or 2) literal `min-h-11` strings even though the rendered HTML has 4. `test_bottom_tab_bar_tap_targets_min_h_11` was rewritten to render the template before counting — pinning the actual user-visible invariant rather than a source-line count that misleads. The plan's done criterion ("at least 4") was based on a literal-non-loop implementation; the loop preserves the spirit while being DRY.

5. **Settings footer keeps the per-surface deep-links above the new /debug index link**, NOT replaces them. Direct deep-links remain useful when triaging a specific subsystem; the /debug index is the discoverability layer for the new /debug/suggestions surface.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Test seed bug: `_seed_plex_configured` called the wrong function name**

- **Found during:** Task 2 GREEN (first test run)
- **Issue:** Test fixture imported `upsert_service` from `app.services.settings_service`. The actual public function is `save_setting` — there is no `upsert_service` symbol.
- **Fix:** Changed the test fixture to use `save_setting` (the existing settings_service public API).
- **Files modified:** `tests/test_pages_suggestions.py`
- **Verification:** All 30 Task 2 tests pass.
- **Committed in:** `b239e3c` (Task 2 GREEN — caught and fixed in same commit cycle)

**2. [Rule 1 — Bug] Test seed bug: `Vibe` instance went detached when later commits expired it**

- **Found during:** Task 2 GREEN (test_hides_find_candidates_cta_when_count_at_or_above_25)
- **Issue:** The test seeded a Vibe `v` then committed 30 Track + TrackVibe rows in the same Session. After the inner commits, accessing `v.id` raised `DetachedInstanceError` because SQLModel/SQLAlchemy expires committed instances on commit.
- **Fix:** Capture `vibe_id = v.id` immediately after the first commit, then use the captured int for all later assertions and FK references.
- **Files modified:** `tests/test_pages_suggestions.py`
- **Verification:** Test now passes.
- **Committed in:** `b239e3c` (Task 2 GREEN)

**3. [Rule 1 — Bug] Test seed bug: NegativeSignal FK violation**

- **Found during:** Task 3 GREEN (test_renders_recent_negativesignal_rows)
- **Issue:** The test seeded `NegativeSignal(track_id=N, ...)` with hardcoded integers 1..5 without first inserting Track rows with those ids. SQLite enforced the FK and raised `IntegrityError`.
- **Fix:** Seed 5 Track rows first, capture their ids, then seed the 5 NegativeSignal rows referencing those captured ids.
- **Files modified:** `tests/test_pages_debug_suggestions.py`
- **Verification:** Test passes.
- **Committed in:** `bf35c3c` (Task 3 GREEN)

**4. [Rule 1 — Bug] `min-h-11` source-count assertion misled by Jinja loop**

- **Found during:** Task 1 GREEN (test_bottom_tab_bar_tap_targets_min_h_11 first run)
- **Issue:** Initial test asserted `body.count("min-h-11") >= 4` against the raw template source. Source has only 1 literal because the anchor is inside a Jinja for-loop over 4 tabs. The user-visible invariant (4 anchors, all 44px) was satisfied, but the source-count assertion failed.
- **Fix:** Test now renders the template with `active_page='vibes'` and counts the literal in the rendered output. Pins the actual user-visible invariant.
- **Files modified:** `tests/test_mobile_first_conventions.py`
- **Verification:** Test passes; the rendered output has 4 `min-h-11` instances (one per tab).
- **Committed in:** `6b0a02e` (Task 1 GREEN — fix landed alongside the source it tests)

**5. [Rule 1 — Bug] `\bCompose\b` substring assertion tripped on `Composer` brand**

- **Found during:** Task 1 GREEN (test_nav_html_removed_compose_link first run)
- **Issue:** Initial test used `assert "Compose" not in body` to enforce the v1 chat-link removal. The brand wordmark "Composer" contains the substring "Compose", so the assertion failed even after the chat link was removed.
- **Fix:** Test uses a word-boundary regex (`re.search(r"\bCompose\b", body)`) so the assertion fires only on the bare label, not the brand. Belt-and-braces: also asserts no `href="/chat"` link exists.
- **Files modified:** `tests/test_mobile_first_conventions.py`
- **Verification:** Test passes; "Composer" brand stays in nav.html.
- **Committed in:** `6b0a02e` (Task 1 GREEN — fix landed alongside the source it tests)

### Out-of-scope test failures (NOT fixed)

The 4 pre-existing `test_sync_scheduler.py` + `test_sync_api.py` failures observed in Plan 01 + Plan 02 SUMMARY continue to fail. Verified pre-existing by `git stash`-ing my changes and re-running the same selectors — the same 4 failures occur on the pre-Plan-3 baseline. Out of Plan 03 scope per SCOPE BOUNDARY rule. Logged here for visibility only:

- `tests/test_sync_api.py::TestStartSync::test_start_sync_launches_background_task`
- `tests/test_sync_scheduler.py::TestStartScheduler::test_triggers_auto_sync_when_no_prior_sync`
- `tests/test_sync_scheduler.py::TestStopScheduler::test_shuts_down_without_error`
- `tests/test_sync_scheduler.py::TestUpdateSyncSchedule::test_updates_running_scheduler`

## Verification Sweep

```text
$ pytest tests/test_mobile_first_conventions.py \
        tests/test_pages_suggestions.py \
        tests/test_api_suggestions.py \
        tests/test_chat_retirement.py \
        tests/test_pages_debug_suggestions.py \
        tests/test_pages_settings.py
59 passed, 1 warning in 3.71s
```

Broader regression sweep (excluding pre-existing failure suites):

```text
$ pytest tests/ -q --ignore=tests/test_chat_service.py \
                  --ignore=tests/test_audio_analyzer.py \
                  --ignore=tests/test_analysis_service.py \
                  --ignore=tests/test_sync_service.py \
                  --ignore=tests/test_sync_endpoints.py
566 passed, 4 failed (4 pre-existing per Plan 01 / Plan 02 SUMMARY).
```

Static grep gate (verification block from plan):

```text
1. bottom_tab_bar in base.html: 1
2. min-h-11 in bottom_tab_bar (source, looped):  2
3. env(safe-area-inset-bottom):                  2
4. Compose in nav.html (must be 0):              0
5. _archived/ refs in active templates (must be 0): 0
6. plexamp:// in expanded:                       1
7. /debug in settings.html:                      4   (1 new + 3 existing)
8. debug_suggestions sections (3 of):            3
```

## TDD Gate Compliance

Plan 03 followed RED → GREEN cycles. Git log shows the gate sequence per task:

| Task | RED commit | GREEN commit |
|------|-----------|--------------|
| 1 (mobile-first base shell) | `bd19d14` `test(07-03)` | `6b0a02e` `feat(07-03)` |
| 2 (suggestions UI + chat retirement) | `c6b9134` `test(07-03)` | `b239e3c` `feat(07-03)` |
| 3 (debug index + suggestions + footer) | `e1dda93` `test(07-03)` | `bf35c3c` `feat(07-03)` |

Each `test(...)` commit precedes its `feat(...)` counterpart. No REFACTOR commits were needed; all implementations were minimal and aligned with the plan's contract.

## Threat Flags

None — every threat in the plan's `<threat_model>` is mitigated as written:

- T-07-03-01..03 (Jinja2 XSS through Track / Vibe / rationale fields): every render uses explicit `| e`. Verified by grep + visual inspection of `suggestions_row.html`, `suggestion_expanded.html`, `vibe_card.html`, `debug_suggestions.html`.
- T-07-03-04..06: accepted (single-user / Tailscale-only posture, same as Phase 5/6 debug surfaces).
- T-07-03-07: mitigated by Plan 02's cost breaker (5/60s burst limit catches the hold-the-tap DOS scenario).
- T-07-03-08..09 (chat archive integrity): templates moved with `git mv`, NOT deleted. Static grep gate `test_no_template_includes_archive` enforces no live template `{% include %}`s anything from `_archived/`.
- T-07-03-10 (repudiation): NegativeSignal.created_at + RefillTriggerLog visible on /debug/suggestions.
- T-07-03-11..12 (refactor regression): convention tests in `test_mobile_first_conventions.py` pin all the invariants (min-h-dvh, viewport-fit=cover, alpine-morph, env(safe-area-inset-bottom), no hover-only state, Compose label removed).

No new HTTP egress, no new auth surface, no new cookies/sessions.

## Next Phase Readiness

Phase 7 is functionally complete after Plan 03. The user-facing v2 deliverable ships:

- Bottom tab bar + h-dvh + safe-area-inset + 44px tap targets on every v2 screen.
- /vibes is the landing page; /suggestions is the queue; v1 mood-chat is gone (404 + archived templates + data preserved in DB).
- Plan 02's refill pipeline + cost breaker + skip-tracking are accessible from `/debug/suggestions`.
- Settings footer makes the new /debug index discoverable.

Future polish work (NOT blocking Phase 7 sign-off):

- Wire Plex album thumb URL into `suggestions_row.html` (the placeholder `<svg>` icon stays until then).
- Phase 8's `/discover` route — the bottom tab bar already links to it; the route returns FastAPI's default 404 until shipped.
- Tailwind CSS rebuild on the NAS may be needed if any of the new utility classes (`min-h-11`, `pb-[env(safe-area-inset-bottom)]`, `border-t-2 border-accent`) are not in the existing `output.css` — handled by the deploy step, NOT a code change.

## Self-Check: PASSED

Verified files exist:

- `app/routers/api_suggestions.py` — FOUND
- `app/templates/pages/suggestions.html` — FOUND
- `app/templates/pages/vibes_home.html` — FOUND
- `app/templates/pages/debug_index.html` — FOUND
- `app/templates/pages/debug_suggestions.html` — FOUND
- `app/templates/partials/bottom_tab_bar.html` — FOUND
- `app/templates/partials/suggestions_row.html` — FOUND
- `app/templates/partials/suggestion_expanded.html` — FOUND
- `app/templates/partials/vibe_card.html` — FOUND
- `app/templates/_archived/chat.html` — FOUND (file moved, not deleted)
- `app/templates/_archived/chat_message.html` — FOUND
- `app/templates/_archived/playlist_card.html` — FOUND
- `app/templates/pages/chat.html` — NOT FOUND (correct — moved)
- `tests/test_mobile_first_conventions.py` — FOUND
- `tests/test_pages_suggestions.py` — FOUND
- `tests/test_api_suggestions.py` — FOUND
- `tests/test_chat_retirement.py` — FOUND
- `tests/test_pages_debug_suggestions.py` — FOUND

Verified commits exist:

- `bd19d14` `test(07-03)`: failing mobile-first conventions tests — FOUND
- `6b0a02e` `feat(07-03)`: mobile-first base shell — FOUND
- `c6b9134` `test(07-03)`: failing /suggestions + chat retirement tests — FOUND
- `b239e3c` `feat(07-03)`: /suggestions page + vibes home + chat retirement — FOUND
- `e1dda93` `test(07-03)`: failing /debug index + /debug/suggestions tests — FOUND
- `bf35c3c` `feat(07-03)`: /debug index + /debug/suggestions + footer link — FOUND

---
*Phase: 07-suggestions-queue-v1-chat-retirement*
*Completed: 2026-05-14*
