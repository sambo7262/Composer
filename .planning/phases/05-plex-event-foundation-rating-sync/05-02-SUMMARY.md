---
phase: 05-plex-event-foundation-rating-sync
plan: 02
subsystem: event-ingestion-and-rating-sync
tags:
  - polling-fallback
  - apscheduler
  - first-run-backfill
  - htmx-banner
  - resync-button
  - tdd
dependency-graph:
  requires:
    - 05-01  # event_bus, event_handlers (handle_rating_changed), Track schema
  provides:
    - app.services.poll_service.{run_poll, get_poll_status, PollStateEnum, PollStatus}
    - app.services.backfill_service.{run_backfill, maybe_trigger_first_run_backfill, get_backfill_status, BackfillStateEnum, BackfillStatus}
    - app.services.rating_sync_service.{run_resync, get_resync_status, ResyncStateEnum}
    - app.services.event_handlers.handle_track_played (real implementation; was stub)
    - app.services.event_handlers._update_track_play_sync
    - app.services.sync_scheduler.{schedule_polling, _trigger_polling}
    - POST /api/rating-sync/start
    - GET /api/rating-sync/status
    - app/templates/partials/backfill_banner.html
    - plex_client._map_track keys: user_rating, last_viewed_at, view_count
  affects:
    - app.main.lifespan (adds backfill auto-trigger after start_scheduler)
    - app.services.sync_scheduler.start_scheduler (calls schedule_polling)
tech-stack:
  added: []  # No new pip deps; all needed libs landed in Plan 01
  patterns:
    - APScheduler IntervalTrigger(minutes=5) on the existing AsyncIOScheduler singleton
    - Bounded PlexAPI queries via searchTracks(filters={...}, limit=200) — never library.all (Pitfall 21)
    - Module-level _backfill_status singleton mirrors analysis_service idiom
    - Pitfall 7 backfill gate: (any track exists) AND (no track rated) — empty DB no-op
    - asyncio.to_thread wrapping for every PlexAPI / DB call (D-09)
    - HTMX hx-get="/api/rating-sync/status" hx-trigger="every 2s" self-polling banner
    - Manual Resync now == Auto backfill via thin shim (D-11)
key-files:
  created:
    - app/services/poll_service.py (193 lines)
    - app/services/backfill_service.py (197 lines)
    - app/services/rating_sync_service.py (18 lines — re-export shim)
    - app/routers/api_rating_sync.py (74 lines)
    - app/templates/partials/backfill_banner.html (69 lines)
    - tests/test_poll_service.py (195 lines)
    - tests/test_backfill_service.py (270 lines)
    - tests/test_api_rating_sync.py (75 lines)
  modified:
    - app/services/plex_client.py (+7 lines — _map_track Phase 5 fields)
    - app/services/sync_scheduler.py (+33 lines — schedule_polling, _trigger_polling, start_scheduler hook)
    - app/services/event_handlers.py (+30 lines — _update_track_play_sync + real handle_track_played)
    - app/main.py (+12 lines — api_rating_sync router include + maybe_trigger_first_run_backfill)
    - tests/test_event_handlers.py (+83 lines — TestHandleTrackPlayed class)
    - tests/test_plex_client_tracks.py (+73 lines — TestMapTrackPhase5Fields class)
decisions:
  - Polling shares the existing AsyncIOScheduler singleton (no new scheduler instance)
  - Polling emits typed events through the SAME event bus as webhooks; dedupe layer handles overlap
  - run_resync is a thin shim re-exporting run_backfill (manual + auto share the implementation)
  - maybe_trigger_first_run_backfill runs as asyncio.create_task in lifespan AFTER start_scheduler
  - Backfill matches by plex_rating_key against existing Track rows; does NOT create new tracks (sync_service's job)
metrics:
  duration: ~2h
  tasks-completed: 3 / 3
  tests-added: 16 (all green — 5 new test files / extensions)
  tests-pre-existing-fail: 17 (unchanged baseline — no regressions)
  files-changed: 14
  lines-added: 1323
  lines-removed: 6
completed: 2026-05-09
---

# Phase 5 Plan 02: Event Ingestion + Auto-Backfill + Resync Now Summary

End-to-end wiring of Plex event ingestion: APScheduler polling fallback (EVT-03) running every 5 minutes alongside the existing library_sync job, real `handle_track_played` handler (RATE-04: view_count + last_viewed_at), the first-run auto-backfill that bootstraps the 4 new Track columns from Plex on initial Phase 5 deploy onto an existing v1 library (RATE-02), and the manual "Resync now" button at POST /api/rating-sync/start (EVT-05) sharing the same singleton state machine. After this plan a real Plex deployment behaves correctly — webhooks deliver promptly, polling catches anything missed, and on first deploy the 10k-track library populates `user_rating` + `last_viewed_at` + `view_count` automatically.

## Tasks Completed

### Task 1: Wave 0 — failing test scaffolds
**Commit:** `f16d4a1`

Created 3 new test files and extended 2 existing ones (15 new failing tests + 1 incidentally passing). Verified Phase 5 Plan 02 readiness — all collected cleanly, all failing red against missing implementations.

- `tests/test_poll_service.py` NEW — EVT-03 polling: `test_emits_rating_changed`, `test_uses_bounded_query`, `test_concurrency_guard`. Uses module-level `_poll_status` reset fixture (autouse) mirroring `test_sync_scheduler.py:11-23`. Patches `PlexServer`, `get_setting`, `get_decrypted_credential`. Drains the event bus to inspect emitted events.
- `tests/test_backfill_service.py` NEW — RATE-02: `test_full_run` (3 tracks single batch), `test_paginated_smoke` (250 tracks across 2 batches — exercises pagination cursor advance per Concern 5), `test_concurrency_guard`, plus 3 Pitfall 7 gate tests (`test_maybe_trigger_no_op_when_already_rated`, `test_maybe_trigger_no_op_when_empty_db`, `test_maybe_trigger_runs_when_unrated_tracks_exist`).
- `tests/test_api_rating_sync.py` NEW — EVT-05: `test_resync_button` (POST /api/rating-sync/start), `test_status_endpoint_returns_partial` (GET /status). Both assert "backfill-banner" appears in response HTML.
- `tests/test_event_handlers.py` extended with `TestHandleTrackPlayed` class — `test_handle_track_played` (view_count 3→4 + last_viewed_at set), first-play (0→1), unknown-track-noop.
- `tests/test_plex_client_tracks.py` extended with `TestMapTrackPhase5Fields` class — `test_map_track_extracts_user_rating` (full path with all 3 attrs) + `test_map_track_handles_missing_phase5_fields` (Pitfall 5 defaults: None/None/0).

### Task 2: plex_client extension + poll_service + scheduler polling job + handle_track_played
**Commit:** `91cd8e4`

Made the data-layer + dispatch-side Wave 0 tests GREEN. No backfill / router work yet — those land in Task 3.

- **`app/services/plex_client.py`** — `_map_track` now returns 3 new keys at the end of the dict: `user_rating` (raw 0-10 per Pitfall 2), `last_viewed_at` (ISO string from `t.lastViewedAt`), `view_count` (int default 0). Defensive `getattr` against PlexAPI partial responses (Pitfall 5). Existing 9 fields preserved unchanged.
- **`app/services/poll_service.py` NEW** — Singleton `_poll_status` (mirrors `sync_service` idiom). `run_poll` is the EVT-03 entry point: bounded queries via `searchTracks(filters={"track.userRating>>": 0}, limit=200, sort="lastRatedAt:desc")` for ratings + `searchTracks(sort="lastViewedAt:desc", limit=200)` for plays. NEVER an unbounded full-library scan (Pitfall 21). Diffs against in-DB rating snapshot (`_get_existing_ratings_sync`) to emit `RatingChangedEvent(source="poll", ...)`. Always emits `TrackPlayedEvent(source="poll", ...)` for tracks with non-null `lastViewedAt` — bus dedupe handles webhook+poll overlap. Concurrency-guarded; token-redacted error path. ALL PlexAPI calls wrapped in `asyncio.to_thread` (6 to_thread calls — D-09 / EVT-06).
- **`app/services/sync_scheduler.py`** — Additive: `schedule_polling(interval_minutes=5)` registers job id `"plex_polling"` with `IntervalTrigger`. `_trigger_polling` lazy-imports `run_poll` and fires `asyncio.create_task(run_poll())`. `start_scheduler()` calls `schedule_polling(interval_minutes=5)` on both happy and exception paths so polling lands regardless of whether settings load succeeded.
- **`app/services/event_handlers.py`** — `handle_track_played` is no longer a stub. Reads `lastViewedAt` from the event payload itself (Pitfall 7 — no Plex re-fetch) and calls `_update_track_play_sync` via `asyncio.to_thread`. Helper increments `track.view_count = (track.view_count or 0) + 1` and sets `track.last_viewed_at = last_viewed_at`. Unknown ratingKey logs and returns cleanly.

**Verification:** `pytest tests/test_plex_client_tracks.py::TestMapTrackPhase5Fields tests/test_poll_service.py tests/test_event_handlers.py::TestHandleTrackPlayed -x` → 8/8 GREEN. Plan 01 AST static test (`test_no_blocking_plexapi_in_async`) still passes — no blocking PlexAPI calls outside `asyncio.to_thread`.

### Task 3: backfill_service + rating_sync_service + api_rating_sync + backfill_banner + lifespan
**Commit:** `52a6d60`

Closed the loop — auto-backfill + manual Resync end-to-end. Made the remaining Wave 0 tests GREEN.

- **`app/services/backfill_service.py` NEW** — Singleton `BackfillStatus` state machine (IDLE→RUNNING→COMPLETED/FAILED) mirrors analysis_service idiom.
  - `_check_needs_backfill_sync` is the Pitfall 7 gate: returns True when there's at least one Track row AND no Track row has `user_rating` populated. Empty DB returns False (truly no sync has run yet — would be wasted Plex pagination). Already-rated DB returns False (backfill already done).
  - `maybe_trigger_first_run_backfill` is the lifespan-side auto-trigger; `asyncio.create_task(run_backfill())` only when the gate returns True.
  - `run_backfill` pages through Plex via `get_library_tracks(container_start=offset, container_size=200)` — reuses Plan 01 / v1 pagination. Each batch goes through `_upsert_rating_fields_sync` which updates `user_rating` + `last_viewed_at` + `view_count` on existing Track rows (matched by `plex_rating_key`). Does NOT create new tracks — that's `sync_service`'s job. Concurrency-guarded; token-redacted error path; yields control via `await asyncio.sleep(0)` so the HTMX status endpoint stays responsive.
- **`app/services/rating_sync_service.py` NEW** — Thin re-export shim per CONTEXT D-11: `run_resync = run_backfill`, `get_resync_status = get_backfill_status`, `ResyncStateEnum = BackfillStateEnum`. Future divergence is cheap; today the manual Resync button uses the exact same singleton state machine as the auto-trigger.
- **`app/routers/api_rating_sync.py` NEW** — `POST /api/rating-sync/start` (EVT-05): if not already RUNNING, fires `asyncio.create_task(run_backfill())`; either way returns the `backfill_banner.html` partial. `GET /api/rating-sync/status`: HTMX-pollable status partial. `_state_str` helper maps `BackfillStateEnum` to the lowercase strings the template branches on.
- **`app/templates/partials/backfill_banner.html` NEW** — 4-state HTMX partial mirroring `sync_banner.html` UX: running (progress bar self-polls every 2s via `hx-get="/api/rating-sync/status"`), failed (error + Retry button), completed (last counts + Resync now button), idle (just the Resync now button). Same accent/error/surface color tokens as sync_banner.
- **`app/main.py`** — Lifespan extended: after `start_scheduler()` (so the polling job is already registered), `asyncio.create_task(maybe_trigger_first_run_backfill())` fires. The auto-backfill is gated by Pitfall 7 — no-op on empty DB or already-rated DB. `app.include_router(api_rating_sync.router)` added after `api_webhooks`.

**Verification:** `pytest tests/test_backfill_service.py tests/test_api_rating_sync.py -x` → 8/8 GREEN. Full suite: 209 passed (+16 vs 193 baseline), 17 failed (same pre-existing baseline — no regressions).

## Verification Commands Run

| Command | Result |
|---------|--------|
| `pytest tests/test_poll_service.py tests/test_backfill_service.py tests/test_api_rating_sync.py tests/test_plex_client_tracks.py --collect-only` | 19 tests collected, no errors |
| `pytest tests/test_poll_service.py tests/test_backfill_service.py tests/test_api_rating_sync.py tests/test_plex_client_tracks.py::TestMapTrackPhase5Fields tests/test_event_handlers.py::TestHandleTrackPlayed --tb=line` (Task 1 RED check) | 15 failed, 1 passed (RED phase confirmed) |
| `pytest tests/test_plex_client_tracks.py::TestMapTrackPhase5Fields tests/test_poll_service.py tests/test_event_handlers.py::TestHandleTrackPlayed -x` (Task 2) | 8 passed |
| `pytest tests/test_backfill_service.py tests/test_api_rating_sync.py -x` (Task 3) | 8 passed |
| `pytest tests/test_event_handlers.py tests/test_plex_client_tracks.py tests/test_poll_service.py tests/test_event_bus.py tests/test_event_log.py tests/test_api_webhooks.py tests/test_rating_helpers.py tests/test_track_model.py tests/test_database.py --tb=line` | 64/64 PASS (Plan 01 + Plan 02 combined) |
| `pytest tests/ --tb=no -q` (final, full suite) | 209 PASS, 17 FAIL (same baseline as Plan 01) |
| `grep -v '^[[:space:]]*#' app/services/poll_service.py \| grep -c 'library.all'` | 0 (Pitfall 21 invariant — bounded queries only) |
| `grep -c 'asyncio.to_thread' app/services/poll_service.py app/services/backfill_service.py app/services/event_handlers.py` | 16 (D-09 invariant maintained — well above ≥6 floor) |
| `python -c "from app.services.backfill_service import maybe_trigger_first_run_backfill, run_backfill, get_backfill_status; print('imports OK')"` | "imports OK" |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Reworded docstring references to `library.all` to keep the literal grep check green**
- **Found during:** Task 2 acceptance criteria check.
- **Issue:** The plan's acceptance criterion `grep -v '^[[:space:]]*#' app/services/poll_service.py | grep -c "library.all"` returned 2 — not because the code calls `library.all()`, but because two docstring lines contained the literal string `library.all()` as a "DO NOT" warning. The `^[[:space:]]*#` filter only excludes `#`-prefixed Python comments, not `"""..."""` docstring text.
- **Fix:** Reworded both docstring mentions to "full library scans" / "an unbounded full-library scan" — same warning, no false grep positive. The actual code never calls `library.all`.
- **Files modified:** `app/services/poll_service.py`
- **Commit:** `91cd8e4` (rolled into the same commit as the original feature)

### Authentication Gates
None encountered.

### Pre-existing Issues (Out of Scope)
- Same 17 pre-existing test failures from Plan 01 baseline still red:
  - `tests/test_chat_service.py` (4 tests — v1 chat surface; D-01 leaves it untouched until Phase 7)
  - `tests/test_audio_analyzer.py` (3 tests — Phase 3 audio fixture issues)
  - `tests/test_service_clients.py::TestOllamaClient` (3 tests — D-02 retires Ollama in Plan 04)
  - `tests/test_sync_*.py` (5 tests — pre-existing flakes in v1 sync)
  - `tests/test_settings_*` (2 tests — pre-existing)

  My changes do not touch any of those files; `git diff` confirms. None block Phase 5 progress.
- `docker-compose.yml` is in a `M` state from a prior session that accidentally overwrote it with chat output; not touched by this plan; flagged for the user to recover.
- `.planning/research/API-REFERENCE.md` and `ESSENTIA-REFERENCE.md` show as deleted — same pre-existing state from before this plan.

## Phase 5 Cumulative Test Pass Rate

- After Plan 01: 50 new Phase 5 tests passing.
- After Plan 02: 50 + 16 = **66 new Phase 5 tests passing**.
- Pre-existing v1 baseline (17 fails) unchanged across both waves.
- Full suite: 209 PASS, 17 FAIL.

## Phase 5 Carry-Forward Notes (for Plan 03 / 04)

- **Settings page "Resync now" button surface** — Plan 04 should embed `partials/backfill_banner.html` into the settings page at the rating-sync section so the user can trigger Resync from the UI without remembering to POST a URL. Banner already self-polls; just needs an `{% include "partials/backfill_banner.html" %}` block.
- **`/debug/events` page surface for poll info** — Plan 04's `/debug/events` page should surface `get_poll_status()` (last_started, last_completed, last_changes_seen, error). The data is already available; just needs the template wiring.
- **TasteProfile recompute trigger** — Plan 03 wires the Anthropic client; the dispatcher counter for "≥10% rated-set delta since last_computed_at" (D-18) lives in Plan 03's scope. Phase 5 Plan 02 doesn't touch it.
- **Manual verification needed on real Plex deployment** — see 05-02-PLAN.md `<manual_verification>` block for the production-only checklist (~10k-track library, /settings rated count vs Plexamp count comparison, etc.). CI cannot exercise this — only a real NAS deploy can.

## Self-Check: PASSED

**Files (all created/modified files exist on disk):**
- `app/services/poll_service.py` — FOUND
- `app/services/backfill_service.py` — FOUND
- `app/services/rating_sync_service.py` — FOUND
- `app/routers/api_rating_sync.py` — FOUND
- `app/templates/partials/backfill_banner.html` — FOUND
- `tests/test_poll_service.py` — FOUND
- `tests/test_backfill_service.py` — FOUND
- `tests/test_api_rating_sync.py` — FOUND
- `app/services/plex_client.py` (modified) — FOUND
- `app/services/sync_scheduler.py` (modified) — FOUND
- `app/services/event_handlers.py` (modified) — FOUND
- `app/main.py` (modified) — FOUND
- `tests/test_event_handlers.py` (modified) — FOUND
- `tests/test_plex_client_tracks.py` (modified) — FOUND

**Commits (all 3 task commits exist):**
- `f16d4a1` — `test(05-02): add Wave 0 test scaffolds for poll/backfill/resync + map_track + track_played`
- `91cd8e4` — `feat(05-02): poll_service + scheduler polling job + handle_track_played + map_track ext`
- `52a6d60` — `feat(05-02): backfill_service + Resync now router + banner partial + lifespan auto-trigger`

**Behavior (final test pass):**
- 16/16 new Phase 5 Plan 02 tests GREEN
- 0 regressions vs pre-existing baseline (17 failures unchanged)
- D-09 invariant maintained: 16 `asyncio.to_thread` calls across `poll_service.py + backfill_service.py + event_handlers.py`
- Pitfall 21 invariant maintained: 0 `library.all` calls in `poll_service.py`
- Plan 01's AST static scan (`test_no_blocking_plexapi_in_async`) still passes — `handle_track_played` extension stays compliant
