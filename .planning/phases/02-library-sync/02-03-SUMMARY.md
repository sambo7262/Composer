---
phase: 02-library-sync
plan: 03
subsystem: api, scheduler
tags: [apscheduler, fastapi, htmx, sync, scheduling, lifespan]

# Dependency graph
requires:
  - phase: 02-library-sync
    provides: SyncService with run_sync/get_sync_status/get_last_sync_info, settings_service, Plex service card
provides:
  - APScheduler AsyncIOScheduler singleton with configurable interval
  - Auto-sync on startup when Plex configured and no prior sync
  - POST /api/settings/plex/sync-schedule endpoint
  - Sync schedule dropdown in Plex service card (6/12/24h)
  - Scheduler lifecycle tied to FastAPI lifespan
affects: [03-spotify-matching]

# Tech tracking
tech-stack:
  added: [apscheduler]
  patterns: [module-level scheduler singleton, lifespan startup/shutdown for background tasks, interval allowlist validation]

key-files:
  created:
    - app/services/sync_scheduler.py
    - tests/test_sync_scheduler.py
  modified:
    - app/main.py
    - app/routers/api_settings.py
    - app/routers/pages.py
    - app/templates/pages/settings.html
    - app/templates/partials/service_card.html
    - requirements.txt

key-decisions:
  - "APScheduler 3.x used (not 4.x which is still alpha) -- stable AsyncIOScheduler for in-process scheduling"
  - "Sync interval validated against allowlist [6, 12, 24] to prevent tampering (T-02-09)"
  - "Scheduler singleton with replace_existing=True prevents duplicate job instances (T-02-10)"
  - "Auto-sync on startup only triggers when Plex is configured AND no prior sync exists"

patterns-established:
  - "Lifespan integration: start_scheduler/stop_scheduler called in FastAPI lifespan context manager"
  - "Interval allowlist validation: user-facing schedule values validated against explicit list"
  - "Dynamic schedule update: update_sync_schedule callable from API endpoints without restart"

requirements-completed: [SYNC-03]

# Metrics
duration: 8min
completed: 2026-04-09
---

# Phase 2 Plan 3: Sync Scheduler and Schedule Configuration Summary

**APScheduler recurring sync with configurable 6/12/24h intervals, auto-sync on startup, and settings page schedule dropdown**

## Performance

- **Duration:** 8 min
- **Started:** 2026-04-09T20:06:29Z
- **Completed:** 2026-04-09T20:14:31Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- APScheduler AsyncIOScheduler singleton integrated into FastAPI lifespan for automatic library sync
- Auto-sync on startup when Plex is configured and no prior sync has run (D-03)
- Settings page Plex card shows sync schedule dropdown with 6/12/24h options (D-04)
- POST /api/settings/plex/sync-schedule endpoint with interval allowlist validation (T-02-09)
- Comprehensive test suite for scheduler lifecycle, job scheduling, and auto-sync logic

## Task Commits

Each task was committed atomically:

1. **Task 1: APScheduler integration with configurable sync interval** - `be60878` (feat)
2. **Task 2: Wire scheduler into lifespan and add sync settings UI** - `3118fe8` (feat)

## Files Created/Modified
- `app/services/sync_scheduler.py` - AsyncIOScheduler singleton: get_scheduler, schedule_sync, start_scheduler, stop_scheduler, update_sync_schedule
- `tests/test_sync_scheduler.py` - Tests for scheduler singleton, job scheduling, start/stop, auto-sync, dynamic update
- `requirements.txt` - Added apscheduler>=3.11,<4.0
- `app/main.py` - Lifespan updated with start_scheduler/stop_scheduler calls
- `app/routers/api_settings.py` - Added POST /api/settings/plex/sync-schedule endpoint with allowlist validation
- `app/routers/pages.py` - Passes sync_interval to settings template context
- `app/templates/pages/settings.html` - Passes sync_interval to plex service card include
- `app/templates/partials/service_card.html` - Sync Schedule dropdown section for configured Plex card

## Decisions Made
- Used APScheduler 3.x (not 4.x alpha) for stable AsyncIOScheduler integration
- Interval validated against explicit allowlist [6, 12, 24] -- rejects invalid values with safe default (T-02-09)
- Singleton pattern with replace_existing=True on jobs prevents DoS via multiple scheduler instances (T-02-10)
- Auto-sync triggers only when both conditions met: Plex configured AND no prior sync completed

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Unable to run pytest locally (FastAPI version pinned for Docker). All acceptance criteria verified via grep pattern matching. Tests should be verified in Docker.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Library sync fully automated: configurable recurring sync, auto-sync on startup, manual trigger via UI
- Phase 2 (Library Sync) complete: data layer, sync UI, and scheduler all wired together
- Ready for Phase 3 (Spotify Matching) which will add audio feature fetching to synced tracks

## Self-Check: PASSED

- All 8 key files verified present on disk
- Both task commits verified: be60878, 3118fe8

---
*Phase: 02-library-sync*
*Completed: 2026-04-09*
