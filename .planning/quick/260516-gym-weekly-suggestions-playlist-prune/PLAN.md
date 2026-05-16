---
phase: quick
plan: 260516-gym-weekly-suggestions-playlist-prune
type: quick
autonomous: true
requirements: []
files:
  - app/services/plex_playlist_service.py
  - app/services/sync_scheduler.py
  - app/main.py
  - tests/test_plex_playlist_service.py
  - tests/test_sync_scheduler.py
---

# Weekly Plex Suggestions playlist prune

## Objective

Add a weekly maintenance tick that prunes the Composer-managed
`kind='suggestions'` Plex playlist down to the current `SuggestionsMirror`
contents BEFORE the existing Sunday 03:00 UTC `discovery_call_weekly` runs.

This closes the monotonic-growth gap left by Phase 7.1 — the additive-only
`update_playlist_items` (Pitfall 5) never removes played-and-rotated-out
tracks, so the Plex playlist keeps growing forever. Weekly drain to the
mirror + immediate discovery refill yields ~30 fresh picks every Sunday.

## Non-negotiable safety gate

Only `ManagedPlaylist.kind='suggestions'` may be touched. Hard-assert the
kind in code (belt-and-suspenders even though the SELECT already filters).
Raise `PermissionError` if violated, BEFORE any Plex mutation.

## Phase invariants preserved

- All `Session(get_engine())` blocks live inside `_*_sync` helpers
  (Phase 5 D-09).
- All PlexAPI calls wrapped in `asyncio.to_thread` (Phase 5 D-09 / Pitfall 4).
- All `fetchItem` calls cast rating keys to `int` (GAP-03 — PlexAPI 4.18.1
  naive URL concat bug). Locked in via at least one `StrictFakePlexServer`
  test.
- `discovery_call_weekly()` signature + behavior unchanged (existing
  `TestDiscoveryCallWeekly` 13 tests must keep passing).
- D-C2 startup catch-up gate preserved (and updated to call prune before
  the catch-up discovery).

## Tasks

### Task 1 — `prune_suggestions_playlist_to_mirror` + `PruneResult`
Add to `app/services/plex_playlist_service.py`:
- `PruneResult` (pydantic BaseModel): `removed: list[str]`,
  `still_present_after_remove: list[str]`, `mirror_size: int`,
  `final_plex_count: int`.
- Sync helpers (D-09):
  - `_find_suggestions_managed_playlist_sync() -> ManagedPlaylist | None`
  - `_read_suggestions_mirror_rating_keys_sync() -> set[str]` — joins
    `SuggestionsMirror -> Track` and returns plex_rating_keys.
- Async public `prune_suggestions_playlist_to_mirror(plex_url, plex_token)`:
  1. Read suggestions ManagedPlaylist row. If None → warn + return empty
     result (bootstrap not done yet).
  2. Hard-assert `mp.kind == 'suggestions'`. Raise PermissionError on mismatch.
  3. Skip if `plex_rating_key` is the deferred-sentinel empty string
     (suggestions playlist not materialized yet).
  4. Read mirror rating-keys via `asyncio.to_thread`.
  5. Fetch current Plex playlist contents — `int(playlist_rating_key)` for
     `fetchItem` (GAP-03).
  6. `to_remove = current - mirror`. Empty → no-op + return result.
  7. `playlist.removeItems([fetchItem(int(k)) for k in to_remove])` inside
     a single sync function, all wrapped in `asyncio.to_thread`.
  8. Re-fetch + compute `still_present`. Log warning if non-empty (no retry —
     unlike additive push, removal retries are riskier).
  9. Update `ManagedPlaylist.last_pushed_at` + `track_count` via
     `_update_managed_playlist_sync`.

### Task 2 — Wire the combined weekly maintenance tick
Edit `app/services/sync_scheduler.py`:
- Add `async def _weekly_maintenance_tick()` that:
  1. Best-effort calls `prune_suggestions_playlist_to_mirror(plex_url, token)`
     with a try/except so prune failure does NOT block discovery.
  2. Then calls `discovery_call_weekly()`.
- Add `schedule_weekly_maintenance()` — registers a SINGLE cron job
  (id=`discovery_call_weekly` retained for back-compat with the existing
  `TestLifespanRegistersDiscoveryCallWeekly` test which queries that id)
  at Sun 03:00 UTC pointing at `_weekly_maintenance_tick`.
- Keep `schedule_discovery_call_weekly()` available (back-compat — existing
  `TestScheduleDiscoveryCallWeekly` tests still exercise it directly).
- Update the D-C2 catch-up coroutine to call the prune first, then
  discovery — same try/except pattern as the cron tick.

### Task 3 — Update lifespan registration
Edit `app/main.py`:
- Replace `schedule_discovery_call_weekly()` with
  `schedule_weekly_maintenance()` after `schedule_soft_negative_sweep()`.
- Update lifespan docstring step 8.

### Task 4 — Tests
- 4 prune tests in `tests/test_plex_playlist_service.py`:
  a) `test_prune_removes_played_and_rotated_out_tracks`
  b) `test_prune_preserves_tracks_still_in_mirror` (no-op)
  c) `test_prune_raises_if_kind_is_not_suggestions`
  d) `test_prune_is_no_op_when_plex_subset_of_mirror`
- 1 scheduler test in `tests/test_sync_scheduler.py`:
  e) `test_schedule_weekly_maintenance_registers_one_cron_job`
- At least one prune test uses `StrictFakePlexServer` to lock in GAP-03 int-cast.

## Success criteria

- 5 new tests pass.
- All existing Phase 7.1 AST guards still pass.
- All existing `TestDiscoveryCallWeekly` (13) + `TestScheduleDiscoveryCallWeekly`
  + `TestLifespanRegistersDiscoveryCallWeekly` tests still pass.
- No modifications to pre-existing dirty files in the working tree.
