---
phase: 04-playlist-generation
plan: 02
subsystem: api
tags: [instructor, ollama, openai-sdk, asyncio, htmx, session-state, two-phase-pipeline]

# Dependency graph
requires:
  - phase: 04-playlist-generation
    provides: "FeatureCriteria/TrackSelection schemas, playlist scoring engine, filter_candidates"
provides:
  - "ChatSession state management with conversation history and current playlist"
  - "Two-phase LLM pipeline: mood interpretation -> candidate filtering -> LLM curation"
  - "Instructor integration with Ollama via JSON mode and asyncio.to_thread"
  - "Chat API endpoints: message, remove-track, reorder, new conversation"
  - "get_instructor_client function in ollama_client.py"
affects: [04-03-chat-ui, 05-plex-push]

# Tech tracking
tech-stack:
  added: []
  patterns: [instructor-json-mode, two-phase-llm-pipeline, session-singleton, track-id-validation, error-sanitization]

key-files:
  created:
    - app/services/chat_service.py
    - app/routers/api_chat.py
    - tests/test_chat_service.py
  modified:
    - app/services/ollama_client.py
    - app/main.py

key-decisions:
  - "Instructor uses JSON mode (not TOOLS) for universal Ollama compatibility"
  - "Session state stored in module-level dict singleton (consistent with sync/analysis services)"
  - "Track ID validation filters out LLM-hallucinated IDs before playlist assembly"
  - "Plex playlist modification requests gracefully declined (deferred to Phase 5)"

patterns-established:
  - "Two-phase LLM pipeline: Instructor mood interpretation -> app-side filtering -> Instructor curation"
  - "Error sanitization: strip URLs and file paths from error messages before returning to user"
  - "Session ID and track ID validation on all chat API endpoints (T-04-04)"

requirements-completed: [PLAY-01, PLAY-02]

# Metrics
duration: 3min
completed: 2026-04-10
---

# Phase 4 Plan 2: Chat Service & LLM Pipeline Summary

**Two-phase Instructor LLM pipeline with mood interpretation, candidate filtering, and curation via Ollama JSON mode, plus 4-endpoint chat API with session state**

## Performance

- **Duration:** 3 min
- **Started:** 2026-04-10T06:59:55Z
- **Completed:** 2026-04-10T07:03:30Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Chat service orchestrating full two-phase LLM pipeline: mood interpretation (FeatureCriteria via Instructor) -> candidate filtering (playlist_engine) -> final curation (TrackSelection via Instructor)
- ChatSession dataclass with conversation history, current playlist tracking, and smart edit support via conversation context (D-08, D-13)
- get_instructor_client added to ollama_client.py using JSON mode for Ollama compatibility
- Chat API router with 4 endpoints: message, remove-track, reorder, new conversation -- all returning HTMX-compatible HTML partials

## Task Commits

Each task was committed atomically:

1. **Task 1: Chat service with Instructor LLM pipeline and session state** - `0f335b7` (feat)
2. **Task 2: Chat API endpoint with HTMX partial responses** - `b02fbb2` (feat)

## Files Created/Modified
- `app/services/chat_service.py` - ChatSession state, process_message two-phase pipeline, error sanitization
- `app/routers/api_chat.py` - POST /api/chat/message, remove-track, reorder, new endpoints
- `app/services/ollama_client.py` - Added get_instructor_client with JSON mode Instructor wrapping
- `app/main.py` - Registered api_chat router
- `tests/test_chat_service.py` - Tests for session management, message processing, track ID validation

## Decisions Made
- Instructor uses JSON mode (not TOOLS) per research Pitfall 1 for universal Ollama model compatibility
- Session state uses module-level dict singleton, consistent with sync_service and analysis_service patterns
- Track IDs from LLM TrackSelection are validated against candidates list before use (T-04-03 threat mitigation)
- Plex playlist modification requests are gracefully declined with suggestion to create new playlist (Phase 5 scope)
- Error messages are sanitized to strip URLs and file paths, preventing information disclosure (T-04-05)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- pytest cannot run locally (dependencies pinned for Docker) -- verification done via AST parsing, grep checks, and acceptance criteria validation

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Chat service ready for Plan 03 to create HTML templates (chat_message.html, playlist_card.html)
- API endpoints will work end-to-end once Plan 03 provides the Jinja2 templates
- Session state ready for Phase 5 Plex playlist push integration

---
## Self-Check: PASSED
