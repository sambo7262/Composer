---
phase: 03-audio-feature-extraction
plan: 02
subsystem: audio-feature-extraction
tags: [analysis-service, background-job, pause-resume, eta-tracking, htmx, api-endpoints]
dependency_graph:
  requires: [03-01]
  provides: [analysis-orchestrator, analysis-api-endpoints, sync-auto-trigger]
  affects: [03-03-PLAN]
tech_stack:
  added: []
  patterns: [state-machine-singleton, rolling-average-eta, htmx-polling-banner, auto-trigger-on-sync]
key_files:
  created:
    - app/services/analysis_service.py
    - app/routers/api_analysis.py
    - app/templates/partials/analysis_banner.html
    - tests/test_analysis_service.py
    - tests/test_analysis_api.py
  modified:
    - app/main.py
    - app/services/sync_service.py
decisions:
  - "Analysis service mirrors sync_service singleton pattern for state tracking"
  - "ETA uses rolling window of 50 track durations for stable estimates"
  - "Files > 100MB skipped per T-03-05 threat mitigation"
  - "Error list capped at 50 entries to bound memory usage"
  - "Auto-trigger wired at both delta and full sync completion paths"
metrics:
  duration: 5min
  completed: "2026-04-09T21:33:53Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 7
---

# Phase 03 Plan 02: Analysis Service + API Endpoints Summary

**One-liner:** Background analysis orchestrator with 5-state machine (idle/running/paused/completed/failed), pause/resume, rolling ETA, REST+HTMX endpoints, and sync auto-trigger wiring.

## What Was Done

### Task 1: Analysis service with state machine, pause/resume, ETA tracking
**Commit:** 21b1e0f

- Created app/services/analysis_service.py with full state machine:
  - AnalysisStateEnum with 5 states (IDLE, RUNNING, PAUSED, COMPLETED, FAILED)
  - AnalysisStatus dataclass with total_tracks, analyzed_tracks, failed_tracks, current_track, rolling avg, errors list
  - eta_display property computing "~Xh Ym remaining" / "~Xm remaining" / "~Xs remaining"
  - run_analysis: queries un-analyzed tracks (WHERE analyzed_at IS NULL AND file_path IS NOT NULL), processes via asyncio.to_thread
  - stop_analysis: sets PAUSED state, loop breaks on next iteration
  - get_analysis_status: returns module-level singleton
  - trigger_post_sync_analysis: auto-trigger hook for sync completion
  - _detect_plex_music_root_sync: auto-detects from common prefix of first 10 file paths
  - _analyze_single_track_sync: per-track analysis with individual DB commits for crash resilience
- T-03-04: Concurrent run_analysis calls rejected (guard on RUNNING state)
- T-03-05: Files > 100MB skipped via os.path.getsize check
- T-03-06: Only track title + generic error in API responses (no full paths)
- Created tests/test_analysis_service.py with 18 unit tests covering all behaviors

### Task 2: Analysis API endpoints + sync auto-trigger wiring
**Commit:** 630eaa7

- Created app/routers/api_analysis.py with 3 endpoints:
  - POST /api/analysis/start: launches background task, returns running banner
  - POST /api/analysis/stop: pauses analysis, returns paused banner
  - GET /api/analysis/status: returns HTMX-polled progress banner with DB stats
- Created app/templates/partials/analysis_banner.html with 5 states (running/paused/failed/completed/idle)
- Registered analysis router in app/main.py
- Wired trigger_post_sync_analysis in sync_service.py at both delta and full sync completion paths (D-01)
- Wrapped trigger in try/except so sync success is never affected by analysis trigger failure
- Created tests/test_analysis_api.py with 5 tests

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Mirror sync_service singleton pattern | Consistent architecture, proven pattern from Phase 2 |
| Rolling window of 50 for ETA | Provides stable ETA estimate without unbounded memory growth |
| Skip files > 100MB | T-03-05 mitigation: prevents DoS via oversized files |
| Cap error list at 50 | Bounded memory for error tracking; most recent errors most relevant |
| Wire trigger at both delta and full sync paths | Delta sync returns early; both paths need the auto-trigger |

## Threat Surface Verification

| Threat ID | Status | Implementation |
|-----------|--------|----------------|
| T-03-04 | Mitigated | run_analysis guards on RUNNING state, rejects concurrent calls |
| T-03-05 | Mitigated | os.path.getsize check before extraction, 100MB limit |
| T-03-06 | Mitigated | Only track title + generic error in AnalysisStatus errors list |
| T-03-07 | Accepted | trigger_post_sync_analysis wrapped in try/except, same trust level |

## Known Stubs

None -- all functions are fully implemented with real logic or proper mocking for tests.

## Verification

- analysis_service.py exports all 6 required functions/classes (verified via grep)
- AnalysisStateEnum has 5 states (verified via grep)
- run_analysis queries WHERE analyzed_at IS NULL AND file_path IS NOT NULL (verified via code)
- stop_analysis sets PAUSED state (verified via code)
- ETA uses rolling 50-track window (verified via code)
- api_analysis.py registered at /api/analysis with start/stop/status (verified via grep)
- analysis_banner.html template created with 5 state branches (verified)
- sync_service.py has trigger_post_sync_analysis at both completion paths (verified via grep)
- Router registered in main.py (verified via grep)
- Tests structurally complete for Docker execution (23 total tests across 2 files)

## Self-Check: PASSED

All 7 files found on disk. Both commits (21b1e0f, 630eaa7) verified in git log.
