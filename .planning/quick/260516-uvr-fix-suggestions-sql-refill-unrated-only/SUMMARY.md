---
phase: quick
plan: 260516-uvr-fix-suggestions-sql-refill-unrated-only
subsystem: suggestions / sql-refill
tags: [phase-7.1, suggestions, sql, refill, hotfix, unrated, vibe-centroid]
requires:
  - app/services/suggestions_service.py
  - app/services/vibe_service.py
provides:
  - _read_top_n_unrated_for_vibe_sync
affects:
  - SuggestionsMirror — every refill_mirror_sql call now surfaces unrated tracks only
  - 5-star rated tracks no longer appear in Composer · Suggestions
tech-stack:
  added: []
  patterns:
    - "_*_sync DB helpers (Phase 5 D-09)"
    - "Z-score normalization with _STD_FLOOR clamp (matches vibe_service)"
    - "Inline SQL distance arithmetic via bind params (no temp tables)"
key-files:
  created:
    - .planning/quick/260516-uvr-fix-suggestions-sql-refill-unrated-only/SUMMARY.md
  modified:
    - app/services/suggestions_service.py
    - tests/test_suggestions_service.py
decisions:
  - "Z-score normalization mirrors vibe_service._slot_track_inner — same metric"
  - "Pure SQL distance compute with numpy mean/std bind params (no TrackVibe join)"
  - "Keep _read_top_n_for_vibe_sync intact — still callable, no callers in production after this hotfix"
  - "Tests verify rated EXCLUDED + unrated INCLUDED + ordering preserved + same exclusion lists"
---

# Suggestions SQL refill now surfaces UNRATED tracks only

## Problem

NAS UAT observation: every track added to `Composer · Suggestions` by the SQL
refill path was a 5-star rated library track. The Suggestions queue is meant
to be a discovery surface — rated tracks already live in their vibe playlists.

## Root cause

`_read_top_n_for_vibe_sync` (the SQL helper called by `refill_mirror_sql`)
joined `trackvibe → track → vibe`. `TrackVibe` rows are only populated for
rated tracks: `vibe_service._slot_track_inner` returns early when
`user_rating in (None, 0, 0.0)`, and the batch path filters
`Track.user_rating > 0`. Result: the rated-only pool was the source of truth
for refills.

The original Phase 7 `SUGG-04` specified "shortlist of ~50 **unrated**
candidates → Anthropic LLM ranks". Phase 7.1 reworked `SUGG-04` to "ZERO LLM
tokens (SQL query against pre-computed `TrackVibe.distance`)" — trading the
unrated semantic for cost. This hotfix restores the unrated semantic while
keeping the zero-LLM-cost guarantee.

## Fix

New sync helper `_read_top_n_unrated_for_vibe_sync(vibe_id, n)`:

1. Loads ACTIVE vibe centroids; computes mean/std across centroids for
   z-score normalization (matches `vibe_service._slot_track_inner` math).
2. Z-score-normalizes the target vibe's centroid.
3. Runs a single SQL query that:
   - computes squared-euclidean distance in z-score space inline (bind
     params for mean/std/normalized centroid),
   - filters to `user_rating IS NULL OR = 0`,
   - keeps the same exclusion lists as the rated helper (mirror dupes,
     `hard_track` / `hard_artist` with `recovery_pending`, 14-day
     `SuggestionHistory` window),
   - orders by distance ASC, `LIMIT n`.
4. Returns `sqrt(dist_sq)` for display parity with `TrackVibe.distance`.

`refill_mirror_sql` swaps `_read_top_n_for_vibe_sync` →
`_read_top_n_unrated_for_vibe_sync`. Library-share weights
(`_read_vibe_pool_weights_sync`) remain unchanged — they still reflect the
user's taste shape.

## Tests

New `TestUnratedSqlRefill` class (6 tests):

- `test_rated_track_excluded_from_helper` — rated rows absent from result.
- `test_refill_mirror_sql_does_not_surface_rated_tracks` — end-to-end, mixed
  pool, mirror contains only unrated track_ids.
- `test_ordering_by_centroid_distance` — non-degenerate centroids, ordering
  near < mid < far holds.
- `test_excludes_tracks_missing_audio_features` — tracks with NULL features
  are dropped.
- `test_excludes_tracks_in_suggestionsmirror` — dedupe against existing
  mirror.
- `test_inactive_vibe_returns_empty` — archived vibe symmetry with rated
  helper.

All 33 tests in `test_suggestions_service.py` pass (27 pre-existing + 6
new). `test_event_handlers.py` and `test_api_vibes.py` also green (22
tests).

## Deployment

Push lands together with Phase 8 Wave 1. Verify on NAS:

1. Open `Composer · Suggestions` after a few drains.
2. Confirm tracks shown have no rating (0/0.5 stars in Plex) and have not
   been recently played.
3. `/debug/vibes` rationale strings should read "Closest match for {vibe}
   (distance X.XXX)" with distance values in z-score space (typically
   0.2–2.5 range; same units as `TrackVibe.distance`).

## Followups (not in scope)

- The `_read_top_n_for_vibe_sync` rated helper is unused in production after
  this hotfix. Kept callable for the existing
  `test_excludes_archived_vibe_members` unit test and any future
  rated-pool feature. Cleanup deferred.
- If unrated pool runs dry, the deficit stays unfilled (matches existing
  behavior at `refill_mirror_sql` line 805-806). Future enhancement: fall
  back to weekly LLM discovery picks (SUGG-13) if unrated pool < threshold.
