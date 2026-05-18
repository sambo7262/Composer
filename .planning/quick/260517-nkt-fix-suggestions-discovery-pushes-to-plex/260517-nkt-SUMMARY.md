---
status: complete
quick_id: 260517-nkt
completed_at: 2026-05-17
---

# Quick Task 260517-nkt: fix(suggestions) — refill regression (discovery push to Plex + mirror cap)

## One-liner

Restored the `Composer · Suggestions` rate-feedback loop: `discovery_call_weekly` now pushes new picks to the Plex playlist (it previously wrote to `SuggestionsMirror` only), and the mirror is capped at `SUGGESTIONS_TARGET_SIZE` (30) with oldest-first eviction so `maybe_schedule_refill`'s deficit gate stays meaningful and `sql_refill` keeps firing on play.

## Why this was needed

Two compounding regressions silently killed the on-play refill chain:

**Bug A — `discovery_call_weekly` never pushed to Plex.** `app/services/suggestions_discovery.py:601+` wrote picks to `SuggestionsMirror` (via `_write_discovery_picks_sync`) and reset `plays_since_last_discovery`, but never called `update_playlist_items` or `_materialize_suggestions_plex_playlist`. Result: every weekly discovery inflated the DB mirror without ever appearing in Plexamp.

**Bug B — `maybe_schedule_refill` dead-gated when mirror > target.** `suggestions_service.py:379-401` checks `deficit = max(0, target - current)`; once `current >= 30` (driven up by Bug A's uncapped writes), `deficit=0`, no `refill_mirror_sql`, no `sql_refill` event, no Plex push on play.

NAS confirmed: yesterday's RefillTriggerLog had many `sql_refill` events on play (mirror at target). Today: 0 `sql_refill` despite 3 `track_played` webhooks (11:45, 14:24, 16:46 PDT). User noted: "the queue in the app composer is growing... maybe last-mile action of updating based on what i see" + "this also used to work — so at some point we hit regression".

The product impact is critical because the Suggestions queue exists to drive UNRATED music to the user so they keep rating tracks → vibes refine → next suggestions. A stalled queue silently breaks this loop.

## Locked design (user-confirmed A1 + B1)

- **A1**: `discovery_call_weekly` pushes to Plex — mirrors `refill_mirror_sql`'s push block at `suggestions_service.py:1001-1075`, including the NotFound self-heal (re-materialize if playlist was deleted out from under us)
- **B1**: cap mirror at `SUGGESTIONS_TARGET_SIZE`; evict oldest first by `added_at ASC, id ASC` BEFORE the discovery write; remove evicted keys from Plex AFTER the add

## Changes shipped

### Code
- `app/services/suggestions_service.py` (+168 lines)
  - `_evict_oldest_mirror_rows_sync(evict_count: int) -> list[str]` — opens single Session, captures `Track.plex_rating_key`s for the oldest N rows ordered by `(added_at ASC, id ASC)`, deletes them, returns the captured keys. Returns `[]` for `evict_count <= 0`.
  - `remove_tracks_from_suggestions_playlist(plex_url, plex_token, mp_rating_key, rating_keys_to_remove) -> int` — async wrapper around `playlist.removeItems`, pre-flight `is_managed_playlist` guard (OPS-06 / Pitfall 20), all PlexAPI calls go through `asyncio.to_thread` (Phase 5 Convention #1). Returns 0 for empty input.

- `app/services/suggestions_discovery.py` (+163 lines)
  - In `discovery_call_weekly`: BEFORE the existing `_write_discovery_picks_sync` call — read current mirror count, compute `evict_count = max(0, current + len(new_picks) - SUGGESTIONS_TARGET_SIZE)`, call `_evict_oldest_mirror_rows_sync` → collect evicted Plex keys
  - AFTER the existing write — lookup `ManagedPlaylist(kind='suggestions')`, push new keys via `update_playlist_items` (ADD before REMOVE for user-visible freshness), then remove evicted keys via the new helper. Removal wrapped in its own try/except so a removal failure doesn't undo the add (weekly prune reconciles)
  - Deferred-materialize branch when `mp.plex_rating_key` is empty
  - NotFound self-heal idiom byte-identical to `suggestions_service.py:1036-1043` canonical form (`isinstance NotFound | "notfound" | "not found" | "404"`)
  - Single summary log line: `"discovery_call_weekly Plex sync: added=%d removed=%d (mirror_target=%d)"`

### Tests
- `tests/test_suggestions_service.py` (+306 lines) — 3 unit tests for `_evict_oldest_mirror_rows_sync` (zero/negative noop, deterministic ordering, returns plex_rating_keys) + 3 unit tests for `remove_tracks_from_suggestions_playlist`
- `tests/test_suggestions_discovery.py` (+506 lines) — 6 integration tests: pushes new picks to Plex, evicts oldest when at target, evicts overflow when above target, no-evict below target, self-heals on Plex NotFound, skips Plex push when no managed playlist row exists

## Commits
- `415f997` feat(260517-nkt-01): add _evict_oldest_mirror_rows_sync helper
- `d252a8d` feat(260517-nkt-01): add remove_tracks_from_suggestions_playlist helper
- `4d838cf` fix(suggestions): discovery pushes to Plex + cap SuggestionsMirror at target size (refill regression)

## Verification (all 6 gates pass)
1. **Diff scope** — exactly the 4 files in `files_modified`
2. **No-touch** — `refill_mirror_sql`, `maybe_schedule_refill`, `SUGGESTIONS_TARGET_SIZE = 30`, `prune_suggestions_playlist_to_mirror`, `_write_discovery_picks_sync` byte-identical to baseline
3. **NotFound idiom parity** — 4-part match present, byte-equivalent to canonical form
4. **New tests** — 9/9 pass in executor's worktree
5. **Integrated regression** — 192/192 across 8 modules in the orchestrator's main repo (post n7j + nkt merge)
6. **Phase 5 Convention #1 AST gate** — passes; all new `removeItems`/`fetchItem` calls live inside `asyncio.to_thread`-dispatched sync nested functions

## Pre-existing failures (out of scope, not addressed)
- `tests/test_sync_scheduler.py` has 3 pre-existing failing tests due to missing `discoverystate` table (documented in `.planning/phases/07.1-suggestions-cost-architecture-sql-refill-weekly-discovery/deferred-items.md`)
- `tests/test_suggestions_service.py::TestRefillMirrorSql::test_picks_proportional_to_vibe_share` is a stochastic flake (passes in repeated wider runs)

## Files modified
- `app/services/suggestions_service.py`
- `app/services/suggestions_discovery.py`
- `tests/test_suggestions_service.py`
- `tests/test_suggestions_discovery.py`
