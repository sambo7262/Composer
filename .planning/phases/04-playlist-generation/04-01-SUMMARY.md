---
phase: 04-playlist-generation
plan: 01
subsystem: api
tags: [pydantic, instructor, sqlmodel, scoring, euclidean-distance, essentia]

# Dependency graph
requires:
  - phase: 03-audio-analysis
    provides: "Track model with audio features (energy, tempo, danceability, valence)"
provides:
  - "FeatureCriteria and TrackSelection Pydantic schemas for Instructor LLM I/O"
  - "Playlist and PlaylistTrack SQLModel tables for history (D-17)"
  - "Playlist scoring engine with weighted Euclidean distance and metadata fallback"
  - "Compact pipe-delimited format for LLM candidate context"
  - "Nav bar Compose link and / route serving chat.html"
affects: [04-02-chat-service, 04-03-chat-ui, 05-plex-push]

# Tech tracking
tech-stack:
  added: [instructor>=1.14]
  patterns: [weighted-euclidean-scoring, energy-normalization-0.3, metadata-fallback, pipe-delimited-llm-format]

key-files:
  created:
    - app/models/schemas.py
    - app/models/playlist.py
    - app/services/playlist_engine.py
    - tests/test_schemas.py
    - tests/test_playlist_engine.py
  modified:
    - app/database.py
    - app/templates/partials/nav.html
    - app/routers/pages.py
    - requirements.txt

key-decisions:
  - "Energy normalization uses 0.3 divisor (Essentia spectral_rms range) not 1.0"
  - "Metadata fallback adds 0.05 penalty to prefer analyzed tracks over heuristic estimates"
  - "Brand text 'Composer' made non-navigational span; Compose link added as primary nav item"

patterns-established:
  - "Scoring pattern: normalize features to [0,1], compute weighted Euclidean distance to criteria midpoint, 2x penalty outside range"
  - "LLM context format: pipe-delimited compact text with ? for missing values, capped at 300 tracks"

requirements-completed: [PLAY-03, PLAY-04]

# Metrics
duration: 4min
completed: 2026-04-10
---

# Phase 4 Plan 1: Playlist Data Foundation Summary

**Weighted Euclidean scoring engine with Pydantic schemas for Instructor LLM I/O, playlist history models, and energy normalization for Essentia spectral_rms**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-10T06:53:43Z
- **Completed:** 2026-04-10T06:58:05Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments
- FeatureCriteria and TrackSelection Pydantic schemas with field constraints and model_validator for min<=max enforcement
- Playlist scoring engine with weighted Euclidean distance, energy normalization (0.3 divisor), tempo normalization, out-of-range penalty, and metadata fallback
- Playlist/PlaylistTrack SQLModel tables registered in database init for Phase 5 history
- Nav bar updated with Compose as primary link, / route now serves chat.html when Plex configured

## Task Commits

Each task was committed atomically:

1. **Task 1: Playlist models, Pydantic schemas, nav update, Instructor dependency** - `92398de` (feat)
2. **Task 2: Playlist scoring engine with metadata fallback** - `1fa906a` (feat)

## Files Created/Modified
- `app/models/schemas.py` - FeatureCriteria and TrackSelection Pydantic models for Instructor
- `app/models/playlist.py` - Playlist and PlaylistTrack SQLModel tables for history
- `app/services/playlist_engine.py` - score_track, filter_candidates, format_candidates_for_llm
- `app/database.py` - Added Playlist/PlaylistTrack imports to init_db
- `app/templates/partials/nav.html` - Added Compose link, brand text non-navigational
- `app/routers/pages.py` - / route renders chat.html with active_page=compose
- `requirements.txt` - Added instructor>=1.14,<2.0
- `tests/test_schemas.py` - Schema validation tests
- `tests/test_playlist_engine.py` - Scoring engine tests

## Decisions Made
- Energy normalization uses 0.3 divisor matching Essentia spectral_rms output range (not 0-1)
- Metadata fallback adds small 0.05 penalty to prefer properly analyzed tracks
- Brand text "Composer" made into non-navigational span; separate "Compose" nav link added for the compose page
- Passing ollama_configured to chat.html template context for conditional UI rendering

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- pytest cannot run locally (dependencies pinned for Docker) -- verification done via grep checks, manual logic tracing, and Python import tests for Pydantic schemas

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Schemas ready for Plan 02 chat service to use with Instructor
- Scoring engine ready for Plan 02 to call filter_candidates and format_candidates_for_llm
- Playlist models ready for Plan 02 to save generated playlists
- chat.html template needed from Plan 03 (route already points to it)

---
## Self-Check: PASSED

All 9 files verified present. Both commits (92398de, 1fa906a) verified in git log.

---
*Phase: 04-playlist-generation*
*Completed: 2026-04-10*
