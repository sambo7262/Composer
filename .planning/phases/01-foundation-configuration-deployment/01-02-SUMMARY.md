---
phase: 01-foundation-configuration-deployment
plan: 02
subsystem: settings-ui
tags: [htmx, jinja2, plexapi, pyarr, openai-sdk, tailwind, fastapi-forms]

# Dependency graph
requires:
  - phase: 01-01
    provides: FastAPI app factory, SQLite database, Fernet encryption, settings service CRUD, base template
provides:
  - Plex connection test client with async wrapping
  - Ollama connection test client via OpenAI SDK
  - Lidarr connection test client via pyarr
  - Settings API routes for test/save/reconfigure/status
  - Full settings page with three service cards
  - HTMX test-and-configure flow with dynamic option selection
  - Credential masking with Reconfigure option
  - Welcome page for first-run experience
  - Progressive setup (Ollama/Lidarr disabled until Plex configured)
  - Nav partial with active page highlighting
affects: [01-03, 02-library-sync-matching, 03-audio-analysis]

# Tech tracking
tech-stack:
  added: [plexapi, pyarr, openai-sdk, pytest-asyncio]
  patterns: [asyncio-to-thread-for-sync-libs, htmx-test-and-configure, service-card-partial-pattern, progressive-setup-gating]

key-files:
  created:
    - app/services/plex_client.py
    - app/services/ollama_client.py
    - app/services/lidarr_client.py
    - app/routers/api_settings.py
    - app/templates/pages/settings.html
    - app/templates/partials/nav.html
    - app/templates/partials/service_card.html
    - app/templates/partials/plex_form.html
    - app/templates/partials/ollama_form.html
    - app/templates/partials/lidarr_form.html
    - app/templates/partials/connection_status.html
    - app/templates/partials/credential_mask.html
    - app/templates/partials/spinner.html
    - tests/test_service_clients.py
    - tests/test_settings_api.py
  modified:
    - app/main.py
    - app/routers/pages.py
    - app/templates/base.html
    - app/templates/pages/welcome.html

key-decisions:
  - "Ollama models list returns string IDs directly (not dicts) since model names are both key and display value"
  - "Connection status partial uses service-specific branching for form fields rather than generic approach for clarity"
  - "Nav extracted to partial and included via Jinja2 include with active_page variable for highlighting"

patterns-established:
  - "HTMX test-and-configure: form hx-post -> connection_status partial -> save form -> service_card replacement"
  - "Service card pattern: id={service}-card on outermost div for HTMX swap targeting across test/save/reconfigure"
  - "Progressive setup: enabled flag passed to service_card controls form visibility vs 'Configure Plex first' notice"
  - "Credential masking: configured services show ******** with Reconfigure button using hx-confirm for destructive action"

requirements-completed: [CONF-01, CONF-02, CONF-03]

# Metrics
duration: 5min
completed: 2026-04-09
---

# Phase 1 Plan 2: Settings Page Summary

**HTMX test-and-configure settings page with Plex/Ollama/Lidarr connection clients, progressive setup gating, and credential masking**

## Performance

- **Duration:** 5 min
- **Started:** 2026-04-09T18:24:05Z
- **Completed:** 2026-04-09T18:29:31Z
- **Tasks:** 2
- **Files modified:** 17

## Accomplishments
- Three async service clients (Plex via PlexAPI, Ollama via OpenAI SDK, Lidarr via pyarr) with error categorization (auth/timeout/generic) and asyncio.to_thread wrapping for sync libraries
- Settings API routes at /api/settings/* handling test/save/reconfigure/status for all three services, returning HTMX partials
- Full settings page with service cards following UI-SPEC exactly: test-and-configure flow, dynamic option dropdowns, credential masking after save
- Welcome page for first-run with no nav bar, progressive setup disabling Ollama/Lidarr until Plex configured
- 37 tests passing (16 new for service clients and API routes, 21 from Plan 01)

## Task Commits

Each task was committed atomically:

1. **Task 1: Service clients and settings API routes** - `2d44277` (test: RED), `87af2d3` (feat: GREEN)
2. **Task 2: Settings page templates, welcome page, and HTMX interactions** - `065fb8c` (feat)

## Files Created/Modified
- `app/services/plex_client.py` - Async Plex connection test returning server name and music libraries
- `app/services/ollama_client.py` - Async Ollama connection test via OpenAI SDK returning model list
- `app/services/lidarr_client.py` - Async Lidarr connection test via pyarr returning quality profiles
- `app/routers/api_settings.py` - API routes for test/save/reconfigure/status at /api/settings/*
- `app/main.py` - Added api_settings router include
- `app/routers/pages.py` - Updated to pass per-service settings and progressive setup flags
- `app/templates/base.html` - Refactored to include nav partial with active_page block
- `app/templates/pages/settings.html` - Full settings page with three service cards
- `app/templates/pages/welcome.html` - Updated with no-nav block override
- `app/templates/partials/nav.html` - Extracted nav with Settings link and active state
- `app/templates/partials/service_card.html` - Card container with id={service}-card for HTMX targeting
- `app/templates/partials/plex_form.html` - Plex URL/token form with Test Connection
- `app/templates/partials/ollama_form.html` - Ollama endpoint URL form
- `app/templates/partials/lidarr_form.html` - Lidarr URL/API key form
- `app/templates/partials/connection_status.html` - Success/error partial with dynamic option selection
- `app/templates/partials/credential_mask.html` - Configured state with masked credentials and Reconfigure
- `app/templates/partials/spinner.html` - HTMX indicator with aria-label
- `tests/test_service_clients.py` - 11 tests for Plex/Ollama/Lidarr clients with mocked externals
- `tests/test_settings_api.py` - 5 integration tests for API endpoints

## Decisions Made
- Used service-specific branching in connection_status.html rather than a fully generic template, since each service has different form field names (library_id vs model vs profile_id)
- Ollama models returned as plain string IDs rather than key/title dicts since model names serve as both identifier and display name
- Nav extracted to Jinja2 partial with active_page variable rather than Alpine.js state for server-side control

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all settings page functionality is fully wired. Service cards show forms, connection testing returns HTMX partials, save persists encrypted credentials, and credential masking displays after save.

## Next Phase Readiness
- Settings page complete: all three service test-and-configure flows operational
- Plan 03 can add GitHub Actions CI/CD workflow
- Phase 2 (library sync) can use the saved Plex configuration to connect and sync
- 37 tests passing with full test infrastructure

## Self-Check: PASSED

All 16 key files verified present. All 3 task commits (2d44277, 87af2d3, 065fb8c) verified in git history.
