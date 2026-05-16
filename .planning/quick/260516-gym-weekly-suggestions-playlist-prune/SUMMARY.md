---
phase: quick
plan: 260516-gym-weekly-suggestions-playlist-prune
subsystem: suggestions / plex-playlist-sync
tags: [phase-7.1, prune, plex, cron, suggestions, gap-03, d-09]
requires:
  - app/services/plex_playlist_service.py
  - app/services/suggestions_service.py
  - app/services/suggestions_discovery.py
provides:
  - prune_suggestions_playlist_to_mirror
  - PruneResult
  - _weekly_maintenance_tick
  - schedule_weekly_maintenance
affects:
  - Plex Suggestions playlist no longer grows monotonically — weekly drain
    to mirror contents on Sun 03:00 UTC, immediately followed by discovery refill.
tech-stack:
  added: []
  patterns:
    - "_*_sync DB helpers (Phase 5 D-09)"
    - "asyncio.to_thread for every PlexAPI call (D-09 / Pitfall 4)"
    - "int(rating_key) cast at every fetchItem call site (GAP-03)"
    - "Hard-assert kind == 'suggestions' before any Plex mutation"
key-files:
  created:
    - .planning/quick/260516-gym-weekly-suggestions-playlist-prune/PLAN.md
    - .planning/quick/260516-gym-weekly-suggestions-playlist-prune/SUMMARY.md
  modified:
    - app/services/plex_playlist_service.py
    - app/services/sync_scheduler.py
    - app/main.py
    - tests/test_plex_playlist_service.py
    - tests/test_sync_scheduler.py
decisions:
  - "Option (B) — combined cron tick at Sun 03:00 UTC calling prune then discovery"
  - "Reuse job id 'discovery_call_weekly' under schedule_weekly_maintenance for back-compat"
  - "Keep schedule_discovery_call_weekly() function available for the back-compat tests"
  - "Mirror _find_suggestions_managed_playlist_sync into plex_playlist_service (avoid circular import)"
  - "Hard-assert kind == 'suggestions' is belt-and-suspenders defense-in-depth"
  - "Post-remove verify logs warning but does NOT retry (removal retries riskier than additive push)"
metrics:
  duration: ~50min
  completed: "2026-05-16"
---

# Quick Task 260516-gym: Weekly Plex Suggestions playlist prune

One-liner: Sunday 03:00 UTC cron prunes the Composer-managed
`kind='suggestions'` Plex playlist down to the current SuggestionsMirror
contents and then runs `discovery_call_weekly` in the same tick.

## What Was Built

### prune_suggestions_playlist_to_mirror + PruneResult
File: `app/services/plex_playlist_service.py`

- New `PruneResult` pydantic model: `removed`, `still_present_after_remove`,
  `mirror_size`, `final_plex_count`.
- New `async def prune_suggestions_playlist_to_mirror(plex_url, plex_token)`.
- Two new `_*_sync` helpers per D-09:
  - `_find_suggestions_managed_playlist_sync`
  - `_read_suggestions_mirror_rating_keys_sync`

Pipeline:
1. Find the suggestions ManagedPlaylist. Missing → warn + no-op.
2. Hard-assert `mp.kind == 'suggestions'`. Mismatch → raise PermissionError.
3. Skip if `plex_rating_key` is the deferred-sentinel empty string.
4. Read mirror rating-keys (set).
5. Fetch current Plex playlist contents (int-cast at every fetchItem).
6. `to_remove = current_plex - mirror`. Empty → log + no-op (still updates
   `last_pushed_at`).
7. `playlist.removeItems` via `asyncio.to_thread`.
8. Post-remove verify (re-fetch). `still_present` non-empty → warn (no retry).
9. Update `ManagedPlaylist.last_pushed_at` + `track_count`.

### _weekly_maintenance_tick + schedule_weekly_maintenance
File: `app/services/sync_scheduler.py`

- `_weekly_maintenance_tick()` reads Plex creds via
  `suggestions_service._read_plex_creds_sync`, runs prune in a try/except,
  then runs `discovery_call_weekly` unconditionally.
- `schedule_weekly_maintenance()` registers ONE cron at Sun 03:00 UTC
  under job id `discovery_call_weekly` (back-compat with existing tests
  + monitoring).
- D-C2 startup catch-up gate updated — the 10s-delayed catch-up coroutine
  now prunes first (best-effort), then discovers.

`schedule_discovery_call_weekly` kept available for the back-compat
`TestScheduleDiscoveryCallWeekly` tests; production lifespan no longer
calls it.

### Lifespan switchover
File: `app/main.py`

Replaced `schedule_discovery_call_weekly()` with
`schedule_weekly_maintenance()` after `schedule_soft_negative_sweep()`.

## Commits

| Hash    | Message                                                                     |
|---------|-----------------------------------------------------------------------------|
| a04f995 | docs(quick-260516-gym): plan weekly suggestions playlist prune              |
| 0ca416d | feat(quick-260516-gym): add prune_suggestions_playlist_to_mirror            |
| 8ca51dc | feat(quick-260516-gym): wire weekly_maintenance cron (prune then discovery) |
| dcf4838 | feat(quick-260516-gym): lifespan registers schedule_weekly_maintenance      |

## Tests

### New (4 prune + 2 scheduler sub-tests = 6 total assertions across 5 test functions)

`tests/test_plex_playlist_service.py`:
- `test_prune_removes_played_and_rotated_out_tracks` — uses
  `StrictFakePlexServer` to lock in the GAP-03 int-cast invariant.
- `test_prune_preserves_tracks_still_in_mirror` — Plex subset of mirror →
  zero removeItems calls.
- `test_prune_raises_if_kind_is_not_suggestions` — monkeypatches the finder
  to return a `kind='vibe'` row; expects PermissionError before any Plex call.
- `test_prune_is_no_op_when_plex_subset_of_mirror`.

`tests/test_sync_scheduler.py::TestScheduleWeeklyMaintenance`:
- `test_registers_one_cron_job_at_sun_03_utc`.
- `test_evicts_prior_schedule_discovery_call_weekly_registration`.

### Regression — full green on touched modules

```
77 passed in 5.10s
```

Coverage:
- test_plex_playlist_service.py — 19 (15 existing + 4 new)
- test_suggestions_service.py — 27 (existing, unchanged)
- TestDiscoveryCallWeekly — 13 (existing, unchanged)
- TestSuggestionsDiscoveryAstShape — 1 (D-09 AST guard, still passes)
- TestSuggestionsDiscoveryMaxTokensGuard — 1 (max-tokens guard, still passes)
- TestScheduleWeeklyMaintenance — 2 (new)
- TestScheduleDiscoveryCallWeekly — 3 (existing, unchanged)
- TestLifespanRegistersDiscoveryCallWeekly — 1 (passes because the
  combined tick still registers under `discovery_call_weekly` job id)
- TestStartSchedulerCatchUpDiscovery — 4 (existing — catch-up still calls
  `discovery_call_weekly` at the end, just adds a try-wrapped prune before it)
- TestScheduleSoftNegativeSweep — 3 (unchanged)
- TestLifespanRegistersSoftNegativeSweep — 1 (unchanged)

### Pre-existing failures NOT touched

`tests/test_sync_scheduler.py` has three pre-existing test failures unrelated
to this work: `TestStartScheduler::test_triggers_auto_sync_when_no_prior_sync`
(missing `discoverystate` table at init_db time — failed on HEAD before any
of my changes, verified by `git stash` + re-run),
`TestStopScheduler::test_shuts_down_without_error`,
`TestUpdateSyncSchedule::test_updates_running_scheduler`. Out-of-scope per
executor scope boundary rules.

## Deviations from Plan

None — plan executed as written.

## Phase Invariants Preserved

- All `Session(get_engine())` blocks live inside `_*_sync` helpers (Phase 5 D-09).
- All PlexAPI calls wrapped in `asyncio.to_thread` (D-09 / Pitfall 4).
- All `fetchItem` calls cast rating keys to `int` (GAP-03), locked in by
  `test_prune_removes_played_and_rotated_out_tracks` using StrictFakePlexServer.
- `discovery_call_weekly()` signature + behavior unchanged.
- Strict scope safety gate — only `kind='suggestions'` mutated (SELECT
  filter + explicit assertion, tested).
- Phase 7.1 AST guards still pass.

## Self-Check: PASSED

Files created / modified verified on disk; commits verified via `git log`;
77 tests pass in the touched-modules regression run.

## Known Stubs

None.

## Threat Flags

None. The new code path is destructive (removeItems on a Plex playlist),
but it operates exclusively on the Composer-managed `kind='suggestions'`
ManagedPlaylist row, already governed by the OPS-06 / D-27 / Pitfall 20
dual-marker rule. The hard-assert is defense-in-depth on top of the
existing SELECT filter.

## Deferred Items

- Pre-existing pytest failures in `test_sync_scheduler.py`
  (`test_triggers_auto_sync_when_no_prior_sync`,
  `test_shuts_down_without_error`, `test_updates_running_scheduler`) —
  unrelated to this work; verified pre-existing on HEAD via `git stash`.
