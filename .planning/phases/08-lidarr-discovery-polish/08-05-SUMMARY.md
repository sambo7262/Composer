---
phase: 08-lidarr-discovery-polish
plan: 05
subsystem: discovery-debug-surface-ops06-guard
tags: [debug-04, debug-05, ops-06, manual-tick, threat-mitigation]
requires:
  - phase 8 plan 01 (DiscoveryAdd schema, WeeklyCronState row, _migrate_add_columns shape, Vibe.color)
  - phase 8 plan 02 (DiscoveryCandidate write path, _get_in_library_mbids_sync helper, artist_discovery_call_weekly, update_weekly_cron_state)
  - phase 8 plan 03 (Vibe color propagation pattern)
  - phase 8 plan 04 (read_active_discover_data dispatch + lifecycle helpers + 5-min Lidarr-status cache)
  - phase 5 D-09 (asyncio.to_thread invariant + AST enforcement extended to api_discovery.py + discovery_service.py)
  - phase 5 D-08 (module-singleton + get_state() pattern for ArtistDiscoveryStatus)
  - phase 5 D-05 (Multipart Form parsing convention; never pydantic.Json[Model] inside Form())
  - 260516-tza (local_time Jinja filter — every new timestamp render piped through it)
provides:
  - app/templates/pages/debug_discovery.html — 5-section diagnostic page + cost panel + recent-LLM calls + manual-tick button
  - app/templates/pages/debug_index.html — /debug/discovery slot unmasked with a working link
  - app/routers/pages.py::read_debug_discovery — GET /debug/discovery handler (delegates to discovery_service)
  - app/routers/api_discovery.py::run_tick_now — POST /api/discovery/run-tick-now (202 spawn, 409 conflict)
  - app/routers/api_discovery.py::tick_state — GET /api/discovery/tick-state (JSON state poll)
  - app/services/discovery_service.py::read_debug_discovery_data — async aggregator across 5 sections + cost panel + recent calls
  - app/services/discovery_service.py::_read_debug_discovery_data_sync — single-Session sync read helper
  - app/services/discovery_service.py::run_manual_weekly_tick — best-effort wrapper around sync_scheduler._weekly_maintenance_tick
  - OPS-06 INVARIANT docstring on _get_in_library_mbids_sync — pinned guard rail for future maintainers
affects:
  - app/routers/api_discovery.py (3 new endpoints: run-tick-now, tick-state, plus the route ordering so literal paths match before the {mb_id} catch-all)
  - app/services/discovery_service.py (appended Plan 05 helpers; existing OPS-06 behavior pinned via docstring)
tech-stack:
  added: []  # all libraries already pinned by Plans 01-04
  patterns:
    - Plan 05 ADDITION-1 BackgroundTask manual-cron trigger — POST returns 202 immediately, BackgroundTask wraps the long-running tick in try/except so the event loop is never crashed by tick failures
    - Module-singleton state gate (read get_state().state == "running") for 409-conflict on concurrent manual triggers
    - JSON state-poll endpoint (/tick-state) for an Alpine 5s page refresh — keeps the debug page server-rendered while allowing in-flight visibility
    - Local-timezone Jinja filter (local_time) on EVERY timestamp render in the new template — no bare ISO strings; consistent with 260516-tza
    - Comment-stripped grep test for templates — strips Jinja {# ... #} before searching so the OPS-06 grep gate doesn't false-positive on inline rationale documenting the constraint
key-files:
  created:
    - app/templates/pages/debug_discovery.html
    - tests/test_pages_debug_discovery.py
    - tests/test_discovery_service_ops06.py
  modified:
    - app/routers/pages.py (added read_debug_discovery route before the /debug index route)
    - app/routers/api_discovery.py (added run-tick-now + tick-state routes BEFORE the {mb_id} catch-all so literal paths match first; new imports: BackgroundTasks, HTTPException, JSONResponse, Response)
    - app/services/discovery_service.py (appended ~210 lines: read_debug_discovery_data, _read_debug_discovery_data_sync, run_manual_weekly_tick; extended OPS-06 docstring annotation on _get_in_library_mbids_sync)
    - app/templates/pages/debug_index.html (replaced "Phase 8 — not yet shipped" placeholder slot with an active anchor entry)
decisions:
  - "Background-task wrapper is run_manual_weekly_tick, NOT sync_scheduler._weekly_maintenance_tick directly. The wrapper does three things the raw scheduler function doesn't: (a) flips _status.state to 'running' BEFORE the tick fires so the 5s page poll can show in-flight state, (b) catches any exception the tick raises and stamps _status.last_error without re-raising (FastAPI BackgroundTask exceptions would otherwise propagate into Starlette middleware and surface as opaque 500s on the NEXT request), (c) resets _status to 'idle' on success. The raw scheduler function is still the source of truth for cron-driven invocations — both code paths converge there."
  - "Route ordering inside api_discovery.py is load-bearing. The literal paths /run-tick-now and /tick-state MUST be registered BEFORE the catch-all /{mb_id}/... routes; otherwise FastAPI matches the catch-all first and tries to treat 'run-tick-now' as an mb_id value, breaking the endpoint. Inline comment in api_discovery.py documents this for future contributors."
  - "Cost panel SUM uses func.coalesce(...) so an empty LLMUsage table returns 0.0 instead of None — the template's '%.4f' format would otherwise crash. The SQLModel scalar-vs-tuple return-shape compat shim handles both SQLAlchemy versions seen in CI (defensive)."
  - "Lidarr connection-test history reads EventLog WHERE source='lidarr'. The Phase 8 codebase does NOT currently write Lidarr test outcomes to EventLog (verified by code search — settings_service.test_lidarr returns a dict synchronously, no EventLog write). The empty-state placeholder ('Not yet wired into EventLog ... see /settings for current state.') is the documented v1 behavior per CONTEXT '/debug/discovery page layout' (acceptable per CONTEXT — DEBUG-04 is best-effort)."
  - "Per-tick funnel counts (listenbrainz_returned / validation_drops / dedup_drops / popularity_drops / kept) are NOT persisted in Plan 02 — Plan 02 SUMMARY 'Pointers for Plan 05' flagged this as planner's choice. Plan 05 chose option (b) was deferred; the page surfaces a friendly placeholder ('Per-tick funnel counts are not persisted in v1 — see logs.') so a future quick task can wire a DiscoveryCounterLog table if observability demand grows."
  - "The /debug/discovery page is plain-HTML per DEBUG-05. The manual-tick button uses Alpine.js for the 5s state poll (consistent with the rest of the Composer UI which uses HTMX + Alpine.js per the locked stack), but the page itself is server-rendered: the button has a <noscript> form fallback so users without JS can still trigger the tick. The DEBUG-05 'copy-friendly' invariant holds — every section renders server-side data into a plain table."
  - "Test ManagedPlaylist seeding helper uses composer_name (not name) field per the actual model. The plan's draft snippet showed name=... which doesn't match the model — fixed during RED. Documented in the test fixture's docstring."
  - "OPS-06 INVARIANT docstring lives BOTH on _get_in_library_mbids_sync (the helper that reads the in-library set) AND in a block comment above the Plan 05 _read_debug_discovery_data_sync helper. Two anchors mean future contributors hit the invariant whether they're editing the dedup path or the debug-page aggregation path."
  - "Defensive grep gate widened from 'api_key' to 'api_key|password|secret' — none of these substrings appear in the rendered template body. The original CLAUDE.md 'Security: API keys must never be exposed in UI or API responses after initial configuration' constraint becomes a template-level assertion enforced by the test suite."
metrics:
  duration_minutes: 30
  completed: 2026-05-17
  tasks_executed: 2
  tests_added: 24  # 16 in test_pages_debug_discovery.py + 8 in test_discovery_service_ops06.py
---

# Phase 8 Plan 05: /debug/discovery + OPS-06 Legacy Playlist Guard

Phase 8 closes with the diagnostic surface + the legacy-playlist invariant
pin. This is the SMALLEST Phase 8 plan (2 tasks, both small) but ships
two foundational things:

1. The plain-HTML `/debug/discovery` page (DEBUG-04 / DEBUG-05) — every
   moving piece of the Phase 8 discovery pipeline now has an audit
   surface: candidate provenance, lifecycle timeline, MusicBrainz query
   trail, Lidarr add history, connection-test log, and a cost panel
   split from `/debug/suggestions`. Plus a manual "Run weekly tick now"
   button so the operator can force a Sunday cron tick out-of-band
   (Plan 05 ADDITION-1 captured from Plan 04 UAT).

2. The OPS-06 legacy-playlist invariant is pinned via docstring + 8
   regression tests. The constraint — never cite a legacy v1
   Plex playlist as a "this artist is in your library" reason — was
   already honored by Plan 02 (`_get_in_library_mbids_sync` reads
   `composer.tracks` only). Plan 05 documents WHY and adds the test
   guard rail so a future refactor doesn't silently violate it.

## What Now Holds

1. **`/debug/discovery` is live, plain-HTML, copy-pasteable.** GET
   `/debug/discovery` returns 200 with seven sections rendered server-
   side (no JS-only content): manual-tick trigger, artist-discovery
   cost panel (all-time, filtered to `purpose LIKE 'discovery_artist_%'`),
   last weekly candidate set with full provenance per artist (mb_id +
   artist + seed_vibe + listeners + popularity_gate + LLM rank +
   factual_hook + LLM rationale + created_at), DiscoveryAdd lifecycle
   timeline (full lifecycle timestamps), recent MusicBrainz queries
   (via MusicBrainzCache cached_at DESC), recent Lidarr `add_artist`
   requests (DiscoveryAdd recent slice), Lidarr connection-test
   history (best-effort from EventLog), and a per-LLM-call detail
   table for cost drill-down.

2. **`/debug` index unmasks the discovery slot.** The legacy "Phase 8 —
   not yet shipped" placeholder is replaced with a working `<a
   href="/debug/discovery">/debug/discovery</a>` entry mirroring the
   other `/debug/*` rows in the same file.

3. **Cost panel splits artist-discovery from `/debug/suggestions`.** The
   SUM is filtered to `purpose LIKE 'discovery_artist_%'` (matches
   `discovery_artist_weekly` success rows + every `_skipped_*` /
   `_cost_locked` / `_error_*` failure breadcrumb Plan 02 writes). The
   `/debug/suggestions` cost panel continues to report the combined
   suggestions + discovery history — both surfaces complement, neither
   double-counts.

4. **Plan 05 ADDITION-1 — manual "Run weekly tick now" button ships.**
   POST `/api/discovery/run-tick-now` invokes the FULL
   `_weekly_maintenance_tick` (prune → suggestions discovery → artist
   discovery → WeeklyCronState stamp) in a FastAPI BackgroundTask so
   the request returns 202 immediately. 409 if the discovery service
   singleton state is already "running" — concurrent manual triggers
   are rejected so the cron doesn't double-run. GET
   `/api/discovery/tick-state` is a small JSON status endpoint the
   page polls every 5s for in-flight visibility (state transitions
   running → idle / error). The button disables itself while running
   and a `<noscript>` form fallback ships so the page is usable
   without JS.

5. **Background-task wrapper captures failures.** `run_manual_weekly_tick`
   wraps `_weekly_maintenance_tick` in try/except. On exception: log
   via `logger.exception`, stamp `_status.state = "error"`,
   `_status.last_error = "{type}: {msg}"`. The BackgroundTask NEVER
   re-raises — Starlette would otherwise propagate the exception into
   the middleware chain and surface as an opaque 500 on the NEXT
   request.

6. **OPS-06 invariant pinned with two anchors.** First anchor:
   `_get_in_library_mbids_sync` carries an explicit OPS-06 INVARIANT
   docstring block documenting that the helper reads ONLY from
   `composer.tracks` and never joins to `ManagedPlaylist` or any
   playlist-derived view. Second anchor: a comment block above the
   Plan 05 `_read_debug_discovery_data_sync` aggregator documenting
   the same invariant with a forward-pointer to
   `is_managed_playlist(rating_key)` for any future
   playlist-aware-dedup code.

7. **OPS-06 regression tested via 8 cases.** Three for
   `is_managed_playlist` dual-marker contract (row exists / row missing
   / corrupt-row pins the documented behavior that
   `is_managed_playlist` only checks the DB-side half). Two for the
   in-library set source (a Track with `plex_artist_mbid` IS in the
   set regardless of which playlist surfaced it; a ManagedPlaylist
   row with no Tracks does NOT contribute). One for the docstring
   annotation. Two for template grep — no Phase 8 surface displays
   "in/from your playlist X" or "found in playlist" as a library
   reason. Both grep tests strip Jinja comments before searching so
   inline rationale documenting the constraint doesn't false-positive.

8. **Every new template timestamp pipes through `local_time`.** Plan
   05 introduces `debug_discovery.html` which renders ~14 distinct
   timestamp values across the 5 sections + recent-calls table. Every
   one goes through `{{ value | local_time }}` per the 260516-tza
   convention — no bare ISO strings reach the user.

## File-by-File Changes

### Created

| File | Purpose |
|------|---------|
| `app/templates/pages/debug_discovery.html` | Plain-HTML diagnostic surface. Manual-tick trigger section (Alpine x-data + 5s setInterval poll + `<noscript>` form fallback) + cost panel + 5 data sections + recent-LLM-calls table. Every timestamp piped through `local_time`. T-08-25 defensive — `api_key`/`password`/`secret` literals never appear. |
| `tests/test_pages_debug_discovery.py` | 16 tests: page 200, h1 contains `/debug/discovery`, 5 section headers present, cost panel artist-only (seeds two LLMUsage rows — one artist + one suggestions — asserts the artist-only sum renders), candidate provenance (factual_hook + LLM rationale), pipeline counts header, lifecycle timeline includes mb_id + lidarr_status, MB queries via cache (mb_id appears), empty state (None recorded.), no-secret-leak grep, pipeline-count breakdown surface, /debug index link unmask, run-tick-now 202, 409 when running, background-task failure captured. |
| `tests/test_discovery_service_ops06.py` | 8 tests: is_managed_playlist dual-marker (3 cases), composer.tracks IS the library signal (positive + negative), OPS-06 INVARIANT docstring annotation grep, template grep no-playlist-as-library-signal, template grep no-found-in-playlist-outside-comments. |

### Modified

| File | Change |
|------|--------|
| `app/routers/pages.py` | Added `read_debug_discovery` GET handler BEFORE the `/debug` index route. Delegates to `discovery_service.read_debug_discovery_data()` for the aggregate and renders `pages/debug_discovery.html`. |
| `app/routers/api_discovery.py` | Added `POST /api/discovery/run-tick-now` (202 spawn / 409 conflict) and `GET /api/discovery/tick-state` (JSON state poll) BEFORE the `/{mb_id}/...` catch-all routes. New imports: `BackgroundTasks`, `HTTPException`, `JSONResponse`, `Response`. Route-ordering rationale documented inline (literal paths must match before the catch-all). |
| `app/services/discovery_service.py` | Appended ~210 lines. New helpers: `_read_debug_discovery_data_sync` (single-Session aggregator), `read_debug_discovery_data` (async accessor + `get_state()` snapshot), `run_manual_weekly_tick` (best-effort wrapper around `sync_scheduler._weekly_maintenance_tick`). Plus an OPS-06 INVARIANT block comment above the Plan 05 aggregator. Extended the existing `_get_in_library_mbids_sync` docstring with the explicit OPS-06 INVARIANT annotation (no logic change — pins the existing behavior). |
| `app/templates/pages/debug_index.html` | Replaced the "Phase 8 — not yet shipped" placeholder `<span>` slot with an active `<a href="/debug/discovery">` link matching the styling of the other 3 `/debug/*` entries in the same file. |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Test fixture used `Vibe(name=...)` without `created_at`**
- **Found during:** Task 1 GREEN regression sweep — first run failed with
  `IntegrityError: NOT NULL constraint failed: vibe.created_at`.
- **Issue:** The `_seed_vibe` helper in `tests/test_pages_debug_discovery.py`
  built a `Vibe` row without `created_at`. The Vibe model has
  `created_at: str = Field(index=True)` — NOT NULL with no default —
  so SQLite rejected the INSERT.
- **Fix:** Added `created_at=datetime.now(timezone.utc).isoformat()` to
  the `_seed_vibe` helper.
- **Files modified:** `tests/test_pages_debug_discovery.py`.
- **Commit:** `2aaed2d` (Task 1 GREEN).

**2. [Rule 1 — Bug] Test fixture used wrong ManagedPlaylist field name**
- **Found during:** Task 2 first GREEN run — 3/8 tests failed with
  `IntegrityError: NOT NULL constraint failed: managedplaylist.kind`.
- **Issue:** The plan's draft test snippet showed
  `ManagedPlaylist(plex_rating_key=..., name=..., vibe_id=...)`. The
  actual model has columns `composer_name`, `kind`, `vibe_id`,
  `plex_rating_key`. `name` does not exist; `kind` is NOT NULL.
- **Fix:** Updated `_seed_managed_playlist` to use `composer_name=name`
  and pass `kind="vibe"` (the Phase 6 default).
- **Files modified:** `tests/test_discovery_service_ops06.py`.
- **Commit:** `72320ff` (Task 2).

**3. [Rule 2 — Missing critical] Template comment leaked forbidden literals**
- **Found during:** Acceptance grep gate after Task 1 GREEN —
  `grep -ic "api_key\|password\|secret" debug_discovery.html` returned 2.
- **Issue:** The Jinja `{# ... #}` block comment at the top of the
  template mentioned `api_key`, `password`, `secret` verbatim while
  documenting the T-08-25 defensive constraint. Jinja strips block
  comments at render time so they NEVER reach the user — the response-
  body test (`test_no_api_key_leaked`) passed because the grep is on
  the rendered HTML. But the file-level acceptance grep gate is on the
  template source, and the plan's gate explicitly says "0 hits".
- **Fix:** Rewrote the comment to drop verbatim mentions of the
  forbidden literals while preserving the intent of the docstring
  (still references T-08-25, still describes the constraint as
  "Lidarr / Anthropic credentials are NEVER rendered").
- **Files modified:** `app/templates/pages/debug_discovery.html`.
- **Commit:** `2aaed2d` (Task 1 GREEN).

## Threat / Pitfall Coverage

- **T-08-25 (Information Disclosure — accidental key render):** Defensive
  template grep test + response-body grep test BOTH pass. No code path
  in `_read_debug_discovery_data_sync` reads a Lidarr or Anthropic
  credential. The Lidarr connection-test history section reads
  `EventLog.raw_payload` truncated to 120 chars; production code does
  not write secrets to EventLog (sanitisation happens upstream per
  Phase 5 D-05).
- **T-08-26 (MusicBrainzCache.payload_json contains MB artist data):**
  Accepted per the threat register. The MB queries section renders only
  the first 120 chars of `payload_json` — MB artist data is fully
  public, no PII.
- **T-08-27 (LLMUsage.purpose used in SQL LIKE):** The `purpose.like(
  "discovery_artist_%")` parameter is a server-controlled string
  written by our own code; not user input. SQLModel parameterises
  through SQLAlchemy bindings — no SQL injection surface.
- **T-08-28 (Repudiation — legacy Plex playlist cited as library source):**
  Mitigated. OPS-06 INVARIANT documented in `_get_in_library_mbids_sync`
  docstring. Grep test `test_no_template_cites_playlist_as_library_signal`
  enforces that no Phase 8 surface displays "in/from your playlist X"
  as a positive library-membership reason. Defensive partial grep
  (`test_no_partial_uses_managedplaylist_name_in_user_facing_string`)
  strips Jinja comments before checking for "found in playlist" so
  inline rationale doesn't false-positive.
- **Pitfall 12 (cross-surface dedup):** `_get_in_library_mbids_sync` is
  unchanged in behavior — it reads ONLY from `composer.tracks`. Plan 02
  shipped the implementation; Plan 05 pins it with a docstring + tests
  so future refactors can't silently violate the contract.

## Phase 8 Verification — Next Step

Run `/gsd-verify-work 8` to walk the goal-backward verification across
all 5 plans' `must_haves` and confirm the phase ship gate (5 success
criteria from `ROADMAP.md` §"Phase 8: Lidarr Discovery + Polish"):

1. `/discover` surfaces taste-aware artists — Plan 04 ✓
2. One-click Lidarr add with metadataProfileId — Plan 01 + 04 ✓
3. Hallucinated names blocked via MusicBrainz lookup — Plan 02 ✓
4. Popularity-bias gate (listener-count + adjacency) — Plan 02 ✓
5. Composer-added artists flow through library_sync →
   trigger_post_sync_analysis → vibe slotting — Plan 02 lifecycle
   hooks + Plan 04 status row ✓

Plus the polish + diagnostic deliverables (Plans 01/03/04/05):

- Library page mobile-first rewrite — Plan 04 ✓
- Two-step Lidarr settings flow — Plan 01 ✓
- Vibe color coding propagated everywhere — Plan 03 ✓
- Home-page weekly LLM cost chip — Plan 03 ✓
- `/debug/discovery` diagnostic page — Plan 05 ✓ (this plan)

## Verification

```bash
cd /Users/Oreo/Projects/Composer && .venv/bin/pytest \
  tests/test_pages_debug_discovery.py \
  tests/test_discovery_service_ops06.py \
  --tb=short
# → 24 / 24 passing

# Broader Phase 8 regression sweep (no Plan 05 regressions):
cd /Users/Oreo/Projects/Composer && .venv/bin/pytest \
  tests/test_pages_debug_discovery.py \
  tests/test_discovery_service_ops06.py \
  tests/test_api_discovery.py \
  tests/test_pages_discover.py \
  tests/test_pages_debug_suggestions.py \
  tests/test_pages.py \
  tests/test_event_handlers.py::TestStaticAnalysis \
  tests/test_discovery_service.py \
  tests/test_discovery_lifecycle_hooks.py \
  tests/test_phase_08_discovery_bootstrap.py \
  tests/test_musicbrainz_client.py \
  tests/test_listenbrainz_client.py \
  --tb=short
# → 146 / 146 passing

# Broad suite (excluding pre-existing unrelated failures documented in
# Plan 04 SUMMARY's "Deferred Issues" — none Plan-05-induced):
cd /Users/Oreo/Projects/Composer && .venv/bin/pytest tests/ \
  --ignore=tests/test_chat_service.py \
  --ignore=tests/test_analysis_service.py \
  --ignore=tests/test_audio_analyzer.py \
  --ignore=tests/test_sync_api.py \
  --ignore=tests/test_sync_service.py \
  --ignore=tests/test_sync_scheduler.py \
  --deselect tests/test_library_api.py::TestStatsEndpoint::test_stats_returns_last_rating_event_at \
  --tb=short -q
# → 827 / 828 passing (1 deselected = pre-existing 260516-tza orphan)
```

Manual smoke on NAS deploy (the user's UAT topology per
`feedback_testing_topology` memory):

1. Visit `/debug/discovery` — expect the 5-section page with the cost
   panel + manual-tick button at the top.
2. Visit `/debug` — expect the `/debug/discovery` link is active (not
   the legacy "not yet shipped" placeholder).
3. Tap "Run weekly tick now" — expect the button disables, the state
   chip flips to `running`, and after the tick completes (~10–30s
   depending on Plex + ListenBrainz + Anthropic round-trips) flips to
   `idle`. Refresh `/debug/discovery` — expect candidate rows in
   section 1 + an LLMUsage row in the cost panel.
4. Without waiting for #3 to finish, tap the button a SECOND time —
   expect a 409 response (Alpine doesn't change state from `running`).
5. Add a test artist via `/discover` (Plan 04 UI). Refresh
   `/debug/discovery` — expect a row in section 2 (DiscoveryAdd
   lifecycle timeline) AND in section 4 (Recent Lidarr add_artist).

## Deferred Issues

Pre-existing failures unrelated to Plan 05 (documented in Plan 04 SUMMARY's
"Deferred Issues"):

1. `tests/test_library_api.py::TestStatsEndpoint::test_stats_returns_last_rating_event_at`
   — asserts raw ISO substring in HTML, but the 260516-tza fix routes
   the rendered timestamp through `local_time` → LA-local. Test needs
   updating to assert the LA-local format. NOT a Plan 05 regression
   (failure existed before Plan 05 commits).
2. Pre-existing test tail flagged across Plan 01/02/04 SUMMARYs:
   `test_analysis_service.py` (Essentia darwin shape drift),
   `test_audio_analyzer.py`, `test_chat_service.py` (Phase 7 retirement
   scaffolding), `test_sync_api.py` (TestClient lifespan ordering),
   `test_sync_service.py` (delta-sync code-path retirement),
   `test_sync_scheduler.py` (APScheduler standalone event-loop).
   Phase 8 production paths converge through `init_db()` lifespan so
   the underlying tables/state are always materialised.

## Self-Check: PASSED

Created files verified to exist:
- `app/templates/pages/debug_discovery.html` — FOUND
- `tests/test_pages_debug_discovery.py` — FOUND
- `tests/test_discovery_service_ops06.py` — FOUND

Modified files verified by `git diff --stat 29aaaf4..HEAD`:
- `app/routers/pages.py` — FOUND (+30 lines)
- `app/routers/api_discovery.py` — FOUND (+76 lines)
- `app/services/discovery_service.py` — FOUND (+222 lines)
- `app/templates/pages/debug_index.html` — FOUND (placeholder swap)

Commits verified:
- `66880cf` (test(08-05): RED — /debug/discovery + index unmask + run-tick-now button) — FOUND
- `2aaed2d` (feat(08-05): GREEN — /debug/discovery + index unmask + run-tick-now button) — FOUND
- `72320ff` (test(08-05): OPS-06 legacy-playlist guard + service docstring) — FOUND

All acceptance grep gates from `08-05-PLAN.md` pass (verified inline
during execution — see Task 1 + Task 2 commit messages for the
specific gate counts).
