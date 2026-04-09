---
phase: 01-foundation-configuration-deployment
plan: 01
subsystem: foundation
tags: [fastapi, sqlmodel, sqlite, fernet, encryption, docker, tailwind, htmx, alpine]

# Dependency graph
requires: []
provides:
  - FastAPI app factory with lifespan, static mount, template config
  - SQLite engine with WAL mode, session factory, init_db
  - ServiceConfig SQLModel table, ServiceConfigResponse masked model
  - Fernet encryption key management and encrypt/decrypt
  - CRUD for service configs with encryption
  - Multi-stage Dockerfile with Tailwind CSS compilation
  - Composer + Ollama docker-compose services with volumes
  - Plex-matching dark theme base template with nav bar
  - Health endpoint at /api/health
  - Welcome page for first-run experience
affects: [01-02, 01-03, 02-library-sync-matching, 03-audio-analysis]

# Tech tracking
tech-stack:
  added: [fastapi, uvicorn, sqlmodel, cryptography, jinja2, htmx, alpine.js, tailwind-css-v4]
  patterns: [lifespan-context-manager, fernet-credential-encryption, masked-response-model, lazy-engine-singleton, multi-stage-docker-build]

key-files:
  created:
    - app/main.py
    - app/config.py
    - app/database.py
    - app/models/settings.py
    - app/services/encryption.py
    - app/services/settings_service.py
    - app/routers/api_health.py
    - app/routers/pages.py
    - app/templates/base.html
    - app/templates/pages/welcome.html
    - app/templates/pages/home.html
    - app/static/css/input.css
    - Dockerfile
    - docker-compose.yml
    - tests/conftest.py
    - tests/test_database.py
    - tests/test_encryption.py
    - tests/test_settings_service.py
    - tests/test_health.py
  modified: []

key-decisions:
  - "Lazy engine singleton pattern for database -- allows test isolation by resetting engine between tests"
  - "TemplateResponse uses new request-first parameter order (Starlette deprecation fix)"
  - "Local development uses Python 3.9 with from __future__ import annotations; Docker targets Python 3.12"

patterns-established:
  - "Lazy database engine: get_engine() creates on first call, reset_engine() for tests"
  - "Fernet credential encryption: get_or_create_key() generates on first run, persists in data volume"
  - "Masked response model: ServiceConfigResponse never contains raw credentials, only credential_set bool"
  - "Test isolation: autouse tmp_data_dir fixture overrides config paths and resets singletons"
  - "TestClient context manager: always use `with TestClient(app) as client` to ensure lifespan runs"

requirements-completed: [CONF-04, CONF-05, CONF-06]

# Metrics
duration: 7min
completed: 2026-04-09
---

# Phase 1 Plan 1: Foundation Summary

**FastAPI app with SQLite WAL database, Fernet credential encryption, Plex-matching dark theme, and Docker multi-stage build with Ollama compose service**

## Performance

- **Duration:** 7 min
- **Started:** 2026-04-09T18:13:51Z
- **Completed:** 2026-04-09T18:21:46Z
- **Tasks:** 2
- **Files modified:** 32

## Accomplishments
- SQLite database with WAL mode, foreign keys, and ServiceConfig table for storing encrypted service credentials
- Fernet encryption layer that generates keys on first run (chmod 0o600), encrypts credentials before storage, and never exposes raw values through ServiceConfigResponse
- FastAPI app factory with lifespan-managed startup (init_db + encryption key generation), health endpoint, and page routing
- Plex-matching dark theme (exact UI-SPEC colors: #e5a00d accent, #1f1f23 surface) with 720px max-width layout and nav bar with gear icon
- Multi-stage Dockerfile with Tailwind CSS compilation and docker-compose with Composer + Ollama services
- 21 passing tests covering database, encryption, settings service, health endpoint, and welcome page

## Task Commits

Each task was committed atomically:

1. **Task 1: Project structure, database, encryption, settings model, and test infrastructure** - `9c86803` (feat)
2. **Task 2: FastAPI app factory, base template with dark theme, health endpoint, Docker files** - `fac7b22` (feat)

## Files Created/Modified
- `requirements.txt` - Python dependencies pinned for Docker (FastAPI, SQLModel, cryptography, etc.)
- `pyproject.toml` - Pytest configuration
- `app/config.py` - App configuration: DATA_DIR, DATABASE_URL, ENCRYPTION_KEY_PATH, APP_PORT
- `app/database.py` - SQLite engine with WAL mode, lazy singleton pattern, session factory
- `app/models/settings.py` - ServiceConfig (DB table) and ServiceConfigResponse (masked API model)
- `app/services/encryption.py` - Fernet key management and CredentialEncryptor class
- `app/services/settings_service.py` - CRUD operations: save/get/upsert settings with encryption
- `app/main.py` - FastAPI app factory with lifespan, static mount, template config, router includes
- `app/routers/api_health.py` - GET /api/health returning status ok + version
- `app/routers/pages.py` - GET / (welcome or home based on config), GET /settings
- `app/templates/base.html` - Base layout with nav bar, dark theme, HTMX + Alpine.js includes
- `app/templates/pages/welcome.html` - First-run welcome page with CTA to settings
- `app/templates/pages/home.html` - Home page placeholder for configured state
- `app/templates/pages/settings.html` - Settings page placeholder for Plan 02
- `app/static/css/input.css` - Tailwind v4 theme with Plex-ecosystem colors
- `app/static/js/htmx.min.js` - Vendored HTMX 2.x
- `app/static/js/alpine.min.js` - Vendored Alpine.js 3.x
- `Dockerfile` - Multi-stage build: base, css-builder (Tailwind CLI), final with HEALTHCHECK
- `docker-compose.yml` - Composer + Ollama services with named volumes and read-only media mount
- `.dockerignore` - Excludes .git, tests, .planning, .env, data from image
- `.gitignore` - Python, venv, IDE, OS, and generated file exclusions
- `tests/conftest.py` - Shared fixtures: tmp_data_dir (autouse), test_engine, test_db, test_encryptor
- `tests/test_database.py` - Tests for table creation, WAL mode, foreign keys
- `tests/test_encryption.py` - Tests for key generation, persistence, encrypt/decrypt roundtrip
- `tests/test_settings_service.py` - Tests for save/get/upsert, masked response, decryption
- `tests/test_health.py` - Tests for health endpoint and welcome page rendering

## Decisions Made
- **Lazy engine singleton:** Database engine created on first `get_engine()` call rather than at module import. Enables test isolation by calling `reset_engine()` to force re-creation with test config paths.
- **TemplateResponse parameter order:** Used new `TemplateResponse(request, name)` signature instead of deprecated `TemplateResponse(name, {"request": request})` to avoid Starlette deprecation warnings.
- **Local Python 3.9 compatibility:** Used `from __future__ import annotations` throughout for type union syntax. Requirements.txt targets Python 3.12 for Docker; local dev works on 3.9 with relaxed version constraints.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Database engine eager initialization prevented test isolation**
- **Found during:** Task 2 (health endpoint integration test)
- **Issue:** Database engine was created at module import time, binding to production DATABASE_URL. Tests overriding config.DATABASE_URL had no effect since engine was already created.
- **Fix:** Changed to lazy singleton pattern: `get_engine()` creates on first call, `reset_engine()` disposes and clears for test config changes.
- **Files modified:** app/database.py, tests/conftest.py
- **Verification:** All 21 tests pass with proper test isolation
- **Committed in:** fac7b22 (Task 2 commit)

**2. [Rule 1 - Bug] TestClient not triggering lifespan without context manager**
- **Found during:** Task 2 (welcome page test)
- **Issue:** `TestClient(app)` without context manager did not reliably trigger lifespan events, so `init_db()` was not called and tables were missing.
- **Fix:** Changed all TestClient usage to `with TestClient(app) as client:` context manager pattern.
- **Files modified:** tests/test_health.py
- **Verification:** Welcome page test passes, no "no such table" errors
- **Committed in:** fac7b22 (Task 2 commit)

**3. [Rule 1 - Bug] Starlette TemplateResponse deprecated parameter order**
- **Found during:** Task 2 (test warnings)
- **Issue:** `TemplateResponse(name, {"request": request})` triggers deprecation warning; new signature is `TemplateResponse(request, name)`.
- **Fix:** Updated all TemplateResponse calls to use request-first parameter order.
- **Files modified:** app/routers/pages.py
- **Verification:** All tests pass with zero warnings
- **Committed in:** fac7b22 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (1 blocking, 2 bugs)
**Impact on plan:** All auto-fixes necessary for correct test execution and deprecation compliance. No scope creep.

## Issues Encountered
- Python 3.12 not available locally (only 3.9.6); worked around with `from __future__ import annotations` and relaxed local pip version constraints. Docker image uses Python 3.12 as specified.

## User Setup Required
None - no external service configuration required.

## Known Stubs
- `app/templates/pages/settings.html` - Placeholder settings page; full implementation in Plan 02 (intentional -- Plan 02 builds the settings forms)

## Next Phase Readiness
- Foundation complete: app factory, database, encryption, templates, and Docker files all in place
- Plan 02 can build settings page forms and test-and-configure flows on top of this foundation
- Plan 03 can add GitHub Actions CI/CD workflow
- All 21 tests passing; test infrastructure with proper isolation established

## Self-Check: PASSED

All 19 key files verified present. Both task commits (9c86803, fac7b22) verified in git history.

---
*Phase: 01-foundation-configuration-deployment*
*Completed: 2026-04-09*
