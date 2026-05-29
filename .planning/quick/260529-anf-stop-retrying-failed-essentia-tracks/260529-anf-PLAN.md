---
quick_id: 260529-anf
slug: stop-retrying-failed-essentia-tracks
date: 2026-05-29
status: complete
---

# Quick Task 260529-anf: Stop re-analyzing permanently-failed Essentia tracks

## Problem

A track whose Essentia feature extraction fails (e.g. corrupt FLAC →
`Could not find stream information`, or `File too large`) sets
`track.analysis_error` but leaves `track.analyzed_at = None`
(`app/services/analysis_service.py:179`). Every "needs analysis" query filters
only on `analyzed_at IS NULL`, so the same failing tracks are re-attempted on
**every** analysis run — including the post-sync auto-trigger — failing
identically forever.

Separately, the "N failed" list in the banner is sourced from the **in-memory**
`AnalysisStatus` object, which resets on process restart and (after this fix)
won't repopulate because failed tracks are skipped. So persistent failures would
become invisible.

## Approach (migration-free)

Reuse the existing `analysis_error` column. Define "needs analysis" as
`analyzed_at IS NULL AND analysis_error IS NULL`. Failed tracks keep their
`analysis_error` (the log) and drop out of the work queue. A "Retry failed"
action clears `analysis_error` to re-enqueue them (used after deleting/re-adding
corrupt files). No schema change → no NAS migration.

Do NOT touch the 500 MB size cap or path remapping.

## Tasks

### Task 1 — Stop retrying failed tracks
- `app/services/analysis_service.py`: add `Track.analysis_error.is_(None)` to
  `_get_unanalyzed_track_ids` (~:210) and `_has_unanalyzed` (~:330).
- `app/routers/api_analysis.py`: add same filter to `unanalyzed_count`
  in `_get_analysis_db_stats` (~:36).
- `app/routers/pages.py`: add same filter to `unanalyzed_count` (~:585).
- verify: a track with `analysis_error` set is excluded from pending count and
  not picked up by a new run.
- done: failed tracks no longer re-run; `error_count` still reports them.

### Task 2 — Persist failed-track visibility in the banner
- `app/services/analysis_service.py`: add `get_failed_tracks(limit=50)` →
  `[{track, error}]` from DB rows where `analysis_error IS NOT NULL`.
- `app/routers/api_analysis.py`: include `failed_tracks_db` in
  `_get_analysis_db_stats` (flows to start/stop/status banners).
- `app/routers/pages.py`: pass `error_count` + `failed_tracks_db` to the index
  banner context.
- `app/templates/partials/analysis_banner.html`: in idle + completed states,
  show "{{ error_count }} failed" expandable list from `failed_tracks_db` when
  `error_count > 0`.
- done: failed tracks remain visible after restart.

### Task 3 — Manual retry endpoint + button
- `app/routers/api_analysis.py`: `POST /api/analysis/retry-failed` — clear
  `analysis_error` on all failed tracks, trigger `run_analysis()` if idle,
  return the banner partial.
- `app/templates/partials/analysis_banner.html`: "Retry failed" button next to
  the failed count in idle + completed states.
- done: user can re-enqueue failed tracks from the UI.

## Out of scope
- 500 MB size cap (working as designed — OOM guardrail).
- Path remapping logic.
- Schema/column additions.
