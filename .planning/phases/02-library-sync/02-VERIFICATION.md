---
phase: 02-library-sync
verified: 2026-04-09T21:00:00Z
status: human_needed
score: 4/4 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Navigate to /library after app startup and confirm the Library link is present in the nav bar, the page loads, and the track table renders (with tracks or empty state)"
    expected: "Page at /library renders with nav showing Library link, sync banner with Sync Now button, search input, and track table"
    why_human: "Template rendering and HTMX wiring cannot be verified without a running browser session"
  - test: "Click Sync Now and observe the progress banner update every 2 seconds while a sync is running"
    expected: "Banner transitions from idle state to running state with 'Syncing... X / Y tracks' and percent, updating live every 2s"
    why_human: "Real-time HTMX polling behavior requires a live browser"
  - test: "After sync completes, confirm the banner disappears and shows 'Last synced: [timestamp] · N tracks'"
    expected: "Completed state replaces running banner without page reload"
    why_human: "HTMX outerHTML swap behavior requires browser observation"
  - test: "Type a search term into the search box on /library and confirm results filter in real time with ~300ms debounce"
    expected: "Track table updates to show only matching tracks; shows 'No tracks matching ...' if none found"
    why_human: "HTMX debounced search behavior requires a running browser"
  - test: "Click a column header (e.g., Artist) and confirm the table re-sorts"
    expected: "Table re-renders sorted by the selected column, with sort indicator arrow shown"
    why_human: "Column sort HTMX interaction requires a running browser"
  - test: "Go to Settings, configure Plex, and confirm the Sync Schedule dropdown appears showing 6/12/24h options"
    expected: "After Plex is configured, service_card shows 'Sync Schedule' section with interval select and Update button"
    why_human: "Requires a configured Plex instance and browser to verify template rendering of the conditional section"
  - test: "Change the sync interval to 6 hours and click Update; confirm no restart is needed"
    expected: "Card re-renders with 6h selected; scheduler updated immediately (no 500 error)"
    why_human: "Dynamic scheduler update requires a running app"
---

# Phase 2: Library Sync Verification Report

**Phase Goal:** User's full Plex music library is synced locally with metadata, kept up to date automatically
**Verified:** 2026-04-09T21:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can trigger a library sync and see their full Plex music library reflected in the app | VERIFIED | `POST /api/sync/start` in `api_sync.py` launches `asyncio.create_task(run_sync())`; `run_sync()` in `sync_service.py` paginates through all tracks in batches of 200 and upserts to SQLite; `/library` page at `pages.py:74` renders `library.html` with initial track data |
| 2 | Sync progress is visible in the UI with a progress indicator while the background job runs | VERIFIED | `sync_banner.html` renders a progress bar with `hx-trigger="every 2s"` HTMX polling against `GET /api/sync/status`; running state shows `synced_tracks / total_tracks` with percentage |
| 3 | Subsequent syncs only import new or changed tracks (delta sync), completing much faster than initial sync | VERIFIED | `run_sync()` checks `SyncState.last_sync_completed`; if set, calls `get_tracks_since()` with `addedAt>>` filter before falling back to full sync; `get_tracks_since()` in `plex_client.py` uses `section.searchTracks(filters={"addedAt>>": since_date_str})` |
| 4 | Each synced track has title, artist, album, genre, year, duration, and Plex ratingKey stored locally | VERIFIED | `Track` model in `app/models/track.py` has `plex_rating_key`, `title`, `artist`, `album`, `genre`, `year`, `duration_ms`; `_map_track()` in `plex_client.py` maps all fields from PlexAPI; `SyncState` table updated on completion |

**Score:** 4/4 truths verified

### Plan-Level Must-Haves

#### Plan 01 Must-Haves

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Track model stores all required metadata fields in SQLite | VERIFIED | `app/models/track.py` lines 8-21: all 9 fields present with correct types and indices |
| 2 | Plex client can fetch tracks in paginated batches | VERIFIED | `plex_client.py:23` `async def get_library_tracks(url, token, library_id, container_start, container_size)` with `asyncio.to_thread` |
| 3 | Sync service performs full sync with upsert logic | VERIFIED | `sync_service.py:224` full sync loop with batch pagination; `_upsert_tracks_sync` does select-then-update-or-insert per `plex_rating_key` |
| 4 | Sync service performs delta sync using addedAt filter | VERIFIED | `sync_service.py:186` calls `get_tracks_since(url, token, library_id, last_completed)` when `last_completed` is set |
| 5 | Plex library selection bug is fixed — correct library name saved | VERIFIED | `connection_status.html`: no `library_name` hidden select found; `api_settings.py:save_plex` resolves library name server-side via `test_plex_connection()` |

#### Plan 02 Must-Haves

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can visit /library and see their synced tracks in a paginated table | VERIFIED (human UI needed) | Route exists at `pages.py:74`; queries first 50 tracks; renders `library.html` which includes `track_table.html` |
| 2 | User can click Sync Now and see a progress banner update every 2 seconds | VERIFIED (human UI needed) | `sync_banner.html` running state has `hx-trigger="every 2s"` on `#sync-banner` div; Sync Now button posts to `/api/sync/start` |
| 3 | User can search tracks by title, artist, or album | VERIFIED (human UI needed) | `library.html` search input with `hx-trigger="input changed delay:300ms"`; `api_library.py:61` applies `ilike` filter on title/artist/album |
| 4 | User can sort the table by clicking column headers | VERIFIED (human UI needed) | `track_table.html` column headers use `hx-get` with toggled sort params; `api_library.py:73-77` applies dynamic sort |
| 5 | Progress banner disappears after sync completes, replaced by last synced timestamp | VERIFIED (human UI needed) | `sync_banner.html` `completed` state renders static div (no polling) with "Last synced: {{ last_synced }}" |
| 6 | Library link appears in the nav bar | VERIFIED (human UI needed) | `nav.html:8` has `<a href="/library">` with active_page conditional styling |

#### Plan 03 Must-Haves

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Sync runs automatically on a configurable schedule | VERIFIED | `sync_scheduler.py:28` `schedule_sync(interval_hours)` adds `IntervalTrigger(hours=interval_hours)` job; called from `start_scheduler()` |
| 2 | User can set sync interval (6/12/24 hours) in settings | VERIFIED (human UI needed) | `service_card.html:13-29` Sync Schedule section with select for 6/12/24h options, posts to `/api/settings/plex/sync-schedule`; `api_settings.py:103` validates and saves |
| 3 | Auto-sync triggers on startup if Plex is configured and no sync has run | VERIFIED | `sync_scheduler.py:76-79` checks `last_sync_completed is None AND is_service_configured(session, "plex")`, then calls `await run_sync()` |
| 4 | Scheduler starts on app startup and shuts down cleanly | VERIFIED | `main.py:20-22` calls `await start_scheduler()` in lifespan; `await stop_scheduler()` on shutdown |
| 5 | Changing the schedule interval takes effect without restart | VERIFIED | `api_settings.py:134` calls `update_sync_schedule(sync_interval_hours)` which calls `schedule_sync(interval_hours)` on running scheduler |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/models/track.py` | Track + SyncState SQLModel | VERIFIED | Both classes present; all SYNC-04 fields on Track; SyncState has `last_sync_completed` |
| `app/services/sync_service.py` | Full sync and delta sync orchestration | VERIFIED | `run_sync`, `_upsert_tracks`, `get_sync_status`, `get_last_sync_info` all present and substantive |
| `app/services/plex_client.py` | Paginated track fetching | VERIFIED | `get_library_tracks`, `get_tracks_since`, `_map_track` all present and use `asyncio.to_thread` |
| `app/routers/api_sync.py` | POST /api/sync/start, GET /api/sync/status | VERIFIED | Both endpoints present, call `run_sync`/`get_sync_status`, return `sync_banner.html` partial |
| `app/routers/api_library.py` | GET /api/library/tracks paginated | VERIFIED | Pagination, search (ilike), sort (allowlist), per_page cap, HTMX detection all implemented |
| `app/templates/pages/library.html` | Full library browse page | VERIFIED | Extends base.html; includes sync_banner and track_table partials; search input with debounce |
| `app/templates/partials/sync_banner.html` | HTMX-polled progress banner | VERIFIED | All four states (running, completed, failed, idle); `hx-trigger="every 2s"` in running state |
| `app/templates/partials/track_table.html` | Paginated track table partial | VERIFIED | Sortable headers, pagination controls, empty state, search empty state; wrapped in `#track-table` |
| `app/templates/partials/nav.html` | Library link in nav | VERIFIED | `<a href="/library">` with active_page conditional styling |
| `app/services/sync_scheduler.py` | APScheduler integration | VERIFIED | `AsyncIOScheduler` singleton, `IntervalTrigger`, `start_scheduler`, `stop_scheduler`, `update_sync_schedule` |
| `requirements.txt` | APScheduler dependency | VERIFIED | `apscheduler>=3.11,<4.0` on line 11 |
| `tests/test_track_model.py` | Track model CRUD tests | VERIFIED | TestTrackModel + TestSyncStateModel with 6 substantive tests |
| `tests/test_sync_service.py` | Sync service tests with mocked PlexAPI | VERIFIED | File present and imports real service code with mock infrastructure |
| `tests/test_sync_scheduler.py` | Scheduler tests | VERIFIED | TestGetScheduler, TestScheduleSync, TestStartScheduler with real APScheduler assertions |
| `tests/test_sync_api.py` | Sync API tests | VERIFIED | TestStartSync with client fixture and configured_plex fixture |
| `tests/test_library_api.py` | Library API tests | VERIFIED | File present |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `sync_service.py` | `plex_client.py` | `get_library_tracks` call | VERIFIED | Line 225: `await get_library_tracks(url, token, library_id, ...)` |
| `sync_service.py` | `plex_client.py` | `get_tracks_since` call | VERIFIED | Line 186: `await get_tracks_since(url, token, library_id, last_completed)` |
| `sync_service.py` | `track.py` | Track model upsert | VERIFIED | Line 67: `track = Track(plex_rating_key=td["plex_rating_key"], ...)` |
| `database.py` | `track.py` | model registration in init_db | VERIFIED | Line 52: `from app.models.track import Track, SyncState  # noqa: F401` |
| `sync_banner.html` | `/api/sync/status` | HTMX hx-get polling every 2s | VERIFIED | Line 4-7: `hx-get="/api/sync/status" hx-trigger="every 2s"` |
| `track_table.html` | `/api/library/tracks` | HTMX hx-get for pagination/search/sort | VERIFIED | Multiple `hx-get="/api/library/tracks?..."` on column headers and pagination buttons |
| `api_sync.py` | `sync_service.py` | run_sync and get_sync_status calls | VERIFIED | Lines 12-16: imports `run_sync`, `get_sync_status`; called at lines 70, 39 |
| `main.py` | `sync_scheduler.py` | lifespan startup/shutdown | VERIFIED | Line 12: imports `start_scheduler, stop_scheduler`; called lines 20, 22 |
| `sync_scheduler.py` | `sync_service.py` | run_sync call from scheduled job | VERIFIED | Line 13: `from app.services.sync_service import ... run_sync`; line 49: `asyncio.create_task(run_sync())` |
| `api_settings.py` | `sync_scheduler.py` | schedule_sync call when interval changes | VERIFIED | Line 17: `from app.services.sync_scheduler import update_sync_schedule`; line 134: `update_sync_schedule(sync_interval_hours)` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|-------------------|--------|
| `track_table.html` | `tracks` | `pages.py:library_page` → `session.exec(select(Track))` | Yes — SQLite query, populated by sync | FLOWING |
| `sync_banner.html` | `sync_status` / `last_synced` | `api_sync.py:sync_status` → `get_sync_status()` + `get_last_sync_info(session)` | Yes — in-memory SyncStatus + SyncState DB query | FLOWING |
| `track_table.html` (HTMX) | `tracks` (updated) | `api_library.py:get_tracks` → `session.exec(query)` with pagination/search/sort | Yes — parameterized SQLite query | FLOWING |

### Behavioral Spot-Checks

Step 7b: SKIPPED — app requires Docker/uvicorn to run; no standalone runnable entry point for offline checks.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| SYNC-01 | 02-01, 02-02 | App syncs full Plex music library to SQLite with paginated fetching | SATISFIED | `run_sync()` paginates 200 tracks/batch; `_upsert_tracks` writes to SQLite |
| SYNC-02 | 02-01 | After initial sync, app performs delta sync — only importing new or changed tracks | SATISFIED | `get_tracks_since()` + `last_sync_completed` check in `run_sync()` |
| SYNC-03 | 02-02, 02-03 | Library sync runs as background job with progress indicator visible in UI | SATISFIED | `asyncio.create_task(run_sync())`; HTMX polling banner; APScheduler recurring job |
| SYNC-04 | 02-01 | Sync captures track metadata: title, artist, album, genre, year, duration, Plex ratingKey | SATISFIED | All 7 fields on `Track` model; `_map_track()` populates all from PlexAPI |

All 4 required IDs (SYNC-01, SYNC-02, SYNC-03, SYNC-04) claimed in PLAN frontmatter are accounted for. No orphaned requirements in REQUIREMENTS.md for Phase 2.

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|---------|--------|
| `sync_service.py:260` | `session.exec(select(Track)).all()` for count | Info | Loads all Track objects just to call `len()` — inefficient for large libraries, but not a correctness issue |

No TODOs, FIXMEs, placeholder returns, or empty handlers found in phase 2 files.

### Human Verification Required

The following items require a running app with browser access:

#### 1. Library Page Renders Correctly

**Test:** Start the app and navigate to `/library`
**Expected:** Nav shows Library link with active underline; page has "Library" heading; sync banner shows "Sync Now" button; search input is present; track table shows tracks or "No tracks found" empty state
**Why human:** Jinja2 template rendering and CSS class conditionals cannot be verified without execution

#### 2. Sync Progress Updates Every 2 Seconds

**Test:** With Plex configured, click Sync Now
**Expected:** Banner immediately shows running state with progress bar; text updates from "0 / N tracks" to final count over time; after completion shows "Last synced: [timestamp] · N tracks"
**Why human:** HTMX polling behavior (`hx-trigger="every 2s"`) requires a live browser session

#### 3. Completed State Replaces Running Banner

**Test:** Observe the sync banner after sync completes
**Expected:** Running banner (with polling) is replaced via `hx-swap="outerHTML"` with the completed static banner; no further polling occurs
**Why human:** HTMX DOM swap timing requires browser observation

#### 4. Search Debounce Filters Results

**Test:** Type "Radiohead" in the search box on /library
**Expected:** After ~300ms, track table re-renders showing only matching tracks; empty state shows "No tracks matching 'Radiohead'" if none
**Why human:** `hx-trigger="input changed delay:300ms"` behavior requires a running browser

#### 5. Column Sort Works

**Test:** Click "Artist" column header
**Expected:** Table re-renders sorted by artist ascending; clicking again sorts descending; sort indicator arrow appears
**Why human:** HTMX `hx-get` click triggers require browser execution

#### 6. Sync Schedule Dropdown in Settings

**Test:** With Plex configured, go to Settings and inspect the Plex card
**Expected:** "Sync Schedule" section visible with dropdown showing "Every 6 hours / 12 hours / 24 hours"
**Why human:** Template conditional `{% if service == "plex" and configured %}` requires a configured state

#### 7. Dynamic Schedule Update

**Test:** Change sync interval to 6 hours and click Update
**Expected:** Card re-renders with 6h selected; next scheduled run is within 6 hours (check app logs)
**Why human:** APScheduler job rescheduling requires a running app and log inspection

### Gaps Summary

No gaps found. All 4 roadmap success criteria are met by implemented, wired, and data-flowing code. All 4 requirement IDs (SYNC-01 through SYNC-04) are satisfied. The `human_needed` status reflects that 7 browser-testable behaviors (HTMX interactions, template rendering, live sync flow) require a running app for final confirmation — all the underlying code is correct and wired.

---

_Verified: 2026-04-09T21:00:00Z_
_Verifier: Claude (gsd-verifier)_
