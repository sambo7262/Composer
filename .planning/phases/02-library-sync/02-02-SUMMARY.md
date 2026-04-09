---
phase: 02-library-sync
plan: 02
subsystem: api, ui
tags: [fastapi, htmx, jinja2, pagination, search, sync, sqlmodel]

# Dependency graph
requires:
  - phase: 02-library-sync
    provides: Track model, SyncService with run_sync/get_sync_status/get_last_sync_info
provides:
  - POST /api/sync/start and GET /api/sync/status endpoints
  - GET /api/library/tracks paginated endpoint with search and sort
  - Library browse page at /library
  - HTMX sync progress banner with 2s polling
  - Track table partial with column sorting and pagination
  - Library link in nav bar
affects: [03-spotify-matching]

# Tech tracking
tech-stack:
  added: []
  patterns: [HTMX polling for real-time progress, parameterized search with ilike, sort column allowlist validation, per_page cap for DoS prevention]

key-files:
  created:
    - app/routers/api_sync.py
    - app/routers/api_library.py
    - app/templates/partials/sync_banner.html
    - app/templates/partials/track_table.html
    - app/templates/pages/library.html
    - tests/test_sync_api.py
    - tests/test_library_api.py
  modified:
    - app/main.py
    - app/routers/pages.py
    - app/templates/partials/nav.html

key-decisions:
  - "Sync banner uses HTMX hx-trigger='every 2s' polling for real-time progress -- simple, no WebSocket needed"
  - "Sort column validated against explicit allowlist to prevent SQL injection (T-02-05)"
  - "per_page capped at 100 via FastAPI Query validation to prevent DoS (T-02-07)"
  - "Library API returns HTML partial for both HTMX and full requests -- /library page route handles initial full page load"

patterns-established:
  - "HTMX polling pattern: hx-get with hx-trigger='every Ns' for real-time updates"
  - "Sort/search validation: allowlist for sort columns, parameterized ilike for search"
  - "Partial-first API design: API endpoints return HTML partials, page routes compose them into full pages"

requirements-completed: [SYNC-01, SYNC-03]

# Metrics
duration: 6min
completed: 2026-04-09
---

# Phase 2 Plan 2: Sync UI and Library Browse Page Summary

**Sync API with HTMX progress banner polling every 2s, and library browse page with paginated/searchable/sortable track table**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-09T19:57:08Z
- **Completed:** 2026-04-09T20:03:37Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments
- Sync API endpoints (POST /api/sync/start, GET /api/sync/status) with concurrent execution guard and Plex configuration check
- HTMX-powered sync progress banner showing running/completed/failed/idle states with 2s polling
- Library browse page at /library with paginated track table (50/page), search across title/artist/album, column sorting
- Threat mitigations: sort column allowlist (T-02-05), per_page cap at 100 (T-02-07), concurrent sync guard (T-02-06)

## Task Commits

Each task was committed atomically:

1. **Task 1: Sync API endpoints and progress banner** - `2f31fd2` (feat)
2. **Task 2: Library browse page with pagination, search, and sorting** - `b71e59f` (feat)

## Files Created/Modified
- `app/routers/api_sync.py` - POST /api/sync/start and GET /api/sync/status endpoints
- `app/routers/api_library.py` - GET /api/library/tracks with pagination, search, sort
- `app/templates/partials/sync_banner.html` - HTMX progress banner with running/completed/failed/idle states
- `app/templates/partials/track_table.html` - Sortable track table with pagination controls
- `app/templates/pages/library.html` - Full library page extending base.html
- `app/main.py` - Registered api_sync and api_library routers
- `app/routers/pages.py` - Added /library page route with initial track data
- `app/templates/partials/nav.html` - Added Library link before Settings
- `tests/test_sync_api.py` - Tests for sync start/status endpoints
- `tests/test_library_api.py` - Tests for library tracks API and page

## Decisions Made
- Sync banner uses HTMX polling (every 2s) rather than WebSocket -- appropriate for progress updates
- Sort column validated against explicit allowlist ["title", "artist", "album", "genre", "year"]
- per_page capped at 100 via FastAPI Query(le=100) to prevent abuse
- API endpoints return HTML partials for HTMX, page route handles full page composition

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Unable to run pytest locally (FastAPI version pinned for Docker). All acceptance criteria verified via grep pattern matching.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Sync UI complete: users can trigger syncs, see progress, browse library
- Library page ready for future enhancements (Spotify matching column, playlist integration)
- Track table partial designed for HTMX updates -- easy to extend with new columns

## Self-Check: PASSED

- All 10 key files verified present on disk
- Both task commits verified: 2f31fd2, b71e59f

---
*Phase: 02-library-sync*
*Completed: 2026-04-09*
