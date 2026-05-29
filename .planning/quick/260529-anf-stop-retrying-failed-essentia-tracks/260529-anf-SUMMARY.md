---
quick_id: 260529-anf
slug: stop-retrying-failed-essentia-tracks
date: 2026-05-29
status: complete
commits:
  - 3ef7240 fix(analysis): stop re-analyzing tracks with a persisted analysis_error
  - aef7edf feat(analysis): persist failed-track visibility + manual retry
  - 398f411 test(analysis): cover skip-failed + retry; fix stale 500MB size-cap test
---

# Quick Task 260529-anf — Summary

## What & why

Essentia extraction failures (corrupt FLAC, `File too large`, etc.) set
`track.analysis_error` but left `analyzed_at = None`. Every "needs analysis"
query filtered only on `analyzed_at IS NULL`, so the same failing tracks were
re-attempted on every run — including the post-sync auto-trigger — forever.
User wanted them to stop retrying while keeping the error log visible.

## Changes

**Stop retrying (migration-free):** redefined "needs analysis" as
`analyzed_at IS NULL AND analysis_error IS NULL` at all four query sites:
- `analysis_service._get_unanalyzed_track_ids` (manual run queue)
- `analysis_service._has_unanalyzed` (post-sync auto-trigger)
- `api_analysis._get_analysis_db_stats` (pending count)
- `pages.py` index banner (pending count)

Reuses the existing `analysis_error` column — **no schema change / no NAS
migration**. The `500 MB` size cap and path remapping were left untouched.

**Keep the log visible:** the banner's "N failed" list was sourced only from the
in-memory `AnalysisStatus` (resets on restart, and wouldn't repopulate now that
failed tracks are skipped). Added `get_failed_tracks()` (DB-backed) and surfaced
it with `error_count` in the idle + completed banner states.

**Manual re-run path:** `clear_analysis_errors()` + `POST /api/analysis/retry-failed`
clear `analysis_error` and re-run analysis; a "Retry failed" button wired into
the banner. Use after deleting/re-adding corrupt files.

## Verification

`pytest tests/test_analysis_service.py tests/test_analysis_api.py` → **26 passed**.
Added 3 tests (skip-failed, retry re-enqueue, get_failed_tracks). Fixed a
pre-existing stale test (`test_skips_oversized_files`) that used 200 MB against
the 500 MB cap — failing since cap-bump commit `da076b2`.

UAT (on NAS, post-deploy): trigger analysis with known-failing tracks present →
they no longer re-run; "N failed" + list persist across restart; "Retry failed"
re-queues them.

## Out of scope / follow-ups
- 500 MB cap left as-is (OOM guardrail; working as designed).
- Pretty Lights 4-track failures are a file/data issue (user deleting & re-adding).
