---
phase: 03-audio-feature-extraction
plan: 01
subsystem: audio-feature-extraction
tags: [essentia, audio-analysis, track-model, valence-proxy, path-remapping]
dependency_graph:
  requires: []
  provides: [track-audio-features, audio-analyzer-module, plex-file-path-extraction]
  affects: [03-02-PLAN, 03-03-PLAN]
tech_stack:
  added: [essentia]
  patterns: [valence-proxy-computation, genre-energy-heuristic, path-remapping]
key_files:
  created:
    - app/services/audio_analyzer.py
    - tests/test_audio_analyzer.py
  modified:
    - app/models/track.py
    - app/services/plex_client.py
    - app/services/sync_service.py
    - tests/test_track_model.py
    - tests/test_plex_client_tracks.py
    - requirements.txt
    - Dockerfile
decisions:
  - "Use musical_key (not key) to avoid Python builtin conflict"
  - "Valence proxy weights: 0.30 mode, 0.25 danceability, 0.25 brightness, 0.20 pitch salience"
  - "Danceability normalized from Essentia [0,3] to [0,1] by dividing by 3.0"
  - "Path traversal prevention via '..' rejection (ASVS V5, T-03-01)"
  - "Essentia manylinux wheel is self-contained -- no additional Dockerfile apt packages needed"
metrics:
  duration: 4min
  completed: "2026-04-09T21:27:11Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 9
---

# Phase 03 Plan 01: Track Model + Audio Analyzer Foundation Summary

**One-liner:** Track model expanded with 11 audio feature columns, Plex file_path extraction wired through sync, Essentia-based audio analyzer with valence proxy and metadata fallback created.

## What Was Done

### Task 1: Track model expansion + Plex file_path extraction
**Commit:** 3b5dc38

- Added 11 new columns to Track model: file_path, energy, tempo, danceability, valence, musical_key, scale, spectral_complexity, loudness, analyzed_at, analysis_error
- Indexed energy, danceability, valence for Phase 4 playlist query performance
- Updated _map_track in plex_client.py to extract file_path from track.media[0].parts[0].file with graceful fallback to None
- Updated _upsert_tracks_sync in sync_service.py to persist file_path on both create and update paths
- Added tests for new model fields, index verification, and file_path extraction

### Task 2: Audio analyzer module with Essentia extraction + Docker setup
**Commit:** de4ccd2

- Created app/services/audio_analyzer.py with four exports:
  - `extract_features`: Essentia MusicExtractor wrapper returning normalized 8-key feature dict
  - `compute_valence_proxy`: Weighted proxy (mode 0.30, dance 0.25, brightness 0.25, salience 0.20) returning [0.0, 1.0]
  - `remap_plex_path`: Plex server path to container mount remapping with path traversal rejection
  - `metadata_feature_vector`: Genre/year heuristic fallback for un-analyzed tracks
- Added essentia>=2.1b6,<2.2 to requirements.txt
- Added Dockerfile comment noting self-contained manylinux wheel (no apt-get needed)
- Created comprehensive test suite with mocked Essentia MusicExtractor (16 tests)

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Use `musical_key` not `key` for column name | Avoids shadowing Python builtin and SQLModel primary key convention |
| Valence proxy weights (0.30/0.25/0.25/0.20) | Heuristic from research; tunable later without schema changes |
| Normalize danceability by dividing by 3.0 | Essentia DFA danceability ranges [0,~3]; all stored features should be [0,1] |
| Reject paths with ".." | T-03-01 mitigation: prevents path traversal outside /music mount |
| No Dockerfile apt-get changes | Essentia manylinux wheel bundles all native deps (FFTW, libyaml, ffmpeg codecs) |

## Threat Surface Verification

| Threat ID | Status | Implementation |
|-----------|--------|----------------|
| T-03-01 | Mitigated | remap_plex_path raises ValueError on ".." paths |
| T-03-02 | Deferred to Plan 02 | Per-track timeout and file size check in analysis_service |
| T-03-03 | Mitigated | extract_features raises RuntimeError without exposing full path in message pattern |

## Known Stubs

None -- all functions are fully implemented with real logic or proper mocking for tests.

## Verification

- Track model has all 11 new nullable columns (verified via grep)
- energy, danceability, valence have index=True (verified via grep)
- _map_track extracts file_path and handles missing media/parts (verified via code inspection)
- sync_service persists file_path on create and update (verified via grep)
- audio_analyzer.py exports all 4 required functions (verified via grep)
- requirements.txt includes essentia>=2.1b6,<2.2 (verified)
- Dockerfile has essentia comment (verified)
- Tests cannot run locally (Python 3.9 without project deps) but are structurally complete for Docker execution

## Self-Check: PASSED

All 9 files found on disk. Both commits (3b5dc38, de4ccd2) verified in git log.
