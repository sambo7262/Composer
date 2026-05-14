---
quick_id: 260514-cdl
type: hotfix
phase_context: Phase 7 (Suggestions Queue + v1 Chat Retirement) — production deadlock
requirements: [HOTFIX-260514-CDL]
status: complete
completed_at: 2026-05-14
commit: 8594589
files_modified:
  - app/services/event_handlers.py
  - tests/test_event_handlers.py
key_files:
  modified:
    - path: app/services/event_handlers.py
      change: dropped `if removed:` gate in handle_track_played (lines 252-264 → 252-272); refill is now always awaited after drain. 13-line CDL hotfix comment added explaining the deadlock and the layer at which steady-state no-churn intent is now preserved.
    - path: tests/test_event_handlers.py
      change: updated docstring + tail comment on test_no_op_when_track_not_in_mirror_revised (assertions unchanged — deficit-guard short-circuit makes the existing assert_not_called still hold); added new regression test test_empty_mirror_track_not_in_mirror_still_schedules_refill that proves the bootstrap deadlock cannot recur.
decisions:
  - "Fix lives at the handler layer, not the service layer: dropping the gate in event_handlers.py keeps maybe_schedule_refill's deficit guard as the single source of truth for the no-churn invariant — DRY, one place to reason about churn vs bootstrap"
  - "Sanity-checked the regression test via `git stash` of event_handlers.py: pre-fix the new test FAILS (mock_maybe.await_count == 0), post-fix PASSES — proves the test genuinely guards the bug, not a tautology"
metrics:
  duration: ~6 min
  tasks: 1
  files: 2
  tests_added: 1
  tests_modified: 1 (docstring/comment only — assertions preserved)
---

# Quick Task 260514-cdl: Fix Phase 7 Suggestions Bootstrap Deadlock — Summary

**One-liner:** Drop the `if removed:` gate in `handle_track_played` so a played track on an empty `SuggestionsMirror` still triggers `maybe_schedule_refill`, materializing the `Composer · Suggestions` Plex playlist on first play after deploy.

## Root Cause (one-liner)

The original `if removed: await maybe_schedule_refill()` in `handle_track_played` short-circuited every play on a fresh deploy because `drain_track_from_mirror` returned `False` on an empty `SuggestionsMirror` — and `refill_suggestions_queue` is the only code path that creates the `Composer · Suggestions` Plex playlist (the `mp.plex_rating_key == ''` branch). Result: the bootstrap-via-play path needed a refill to bootstrap the mirror, but the only refill trigger required a track to already be in the mirror. NAS UAT 2026-05-14 confirmed: 4 webhooks delivered (200 OK), `today_calls=0`, `/debug/suggestions` empty across all sections.

## Three Changes (single atomic commit `8594589`)

### 1. `app/services/event_handlers.py` — drop the gate

**Before** (lines 252-264):
```python
removed = await suggestions_service.drain_track_from_mirror(
    event.plex_rating_key
)
if removed:
    await suggestions_service.maybe_schedule_refill()
```

**After** (lines 252-272):
```python
# CDL hotfix (260514): drop the `if removed:` gate. … (13-line comment) …
await suggestions_service.drain_track_from_mirror(
    event.plex_rating_key
)
await suggestions_service.maybe_schedule_refill()
```

The `removed = ` assignment is dropped (no longer needed for control flow). The deficit check inside `maybe_schedule_refill` (`suggestions_service.py:362-364`) preserves the steady-state "no churn" intent at the correct layer:
- Empty mirror → deficit=30 → `refill_suggestions_queue` fires (bootstrap unblocked)
- Mirror at target → deficit=0 → short-circuit (no churn — original intent preserved)

### 2. `tests/test_event_handlers.py` — `test_no_op_when_track_not_in_mirror_revised` docstring + comment update

Assertions UNCHANGED. Updated the docstring + tail comment to reflect that the no-churn guarantee is now enforced at the deficit-guard layer (one level deeper) instead of the now-removed `if removed:` gate. The test still passes because the test seeds 30 mirror rows → `deficit=0` → `maybe_schedule_refill` short-circuits before the `refill_suggestions_queue` mock is reached.

### 3. `tests/test_event_handlers.py` — NEW regression test

`test_empty_mirror_track_not_in_mirror_still_schedules_refill` — placed inside `TestHandleTrackPlayedSuggestionsDrain`, between `test_no_op_when_track_not_in_mirror_revised` and `test_drain_failure_does_not_break_rating_update`. Mocks `maybe_schedule_refill` directly and asserts `assert_awaited_once()` on a fresh, empty-mirror DB. Pre-fix → FAILS (`await_count == 0` because the gate suppresses the call). Post-fix → PASSES.

## Verification

| Check                                                                                          | Result                                            |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `pytest tests/test_event_handlers.py::TestHandleTrackPlayedSuggestionsDrain -v`                | 4 passed                                          |
| `pytest tests/test_event_handlers.py -v` (full file smoke)                                     | 14 passed (incl. `test_no_blocking_plexapi_in_async` AST guard) |
| `grep -nE "^\s*if\s+removed\s*:" app/services/event_handlers.py`                               | empty (gate removed; only the explanatory comment remains) |
| `grep -n "await suggestions_service.maybe_schedule_refill" app/services/event_handlers.py`     | line 267 (unconditional)                          |
| `grep "test_empty_mirror_track_not_in_mirror_still_schedules_refill" tests/test_event_handlers.py` | 1 match (test added)                              |
| Sanity check: `git stash` event_handlers.py + run new test alone                               | FAILED (`AssertionError: Expected mock to have been awaited once. Awaited 0 times.`) — restored after |

## Test Counts (before vs after)

- `TestHandleTrackPlayedSuggestionsDrain`: 3 tests → 4 tests (added bootstrap regression)
- `test_event_handlers.py` total: 13 tests → 14 tests
- All passing.

## Phase 5 Conventions Compliance

- **D-09 / Pitfall 4 (PlexAPI sync wrapped in `asyncio.to_thread`):** No new PlexAPI calls introduced. `drain_track_from_mirror` and `maybe_schedule_refill` are async coroutines that internally route DB work through `asyncio.to_thread`. The static AST guard `test_no_blocking_plexapi_in_async` still passes.
- **D-06 (event types):** `TrackPlayedEvent` discriminator usage unchanged.
- **D-15 / Pitfall 2 (`userRating` raw 0-10):** untouched.
- **D-07 (EventLog dedupe):** untouched.

## Deviations from Plan

None — plan executed exactly as written. The sanity-check step in the plan's `<done>` block (git-stash to confirm the new test fails pre-fix) was performed and confirmed.

## Deploy Note

On the next NAS push, the first `TrackPlayed` webhook after deploy will:
1. Increment `view_count` and update `last_viewed_at` (Phase 5 RATE-04 — unchanged behavior).
2. Call `drain_track_from_mirror` (returns `False` on empty mirror — no harm done).
3. Call `maybe_schedule_refill` UNCONDITIONALLY (the fix). Deficit=30 (target=30, current=0) so `refill_suggestions_queue` fires.
4. Inside `refill_suggestions_queue`, the `mp.plex_rating_key == ''` branch creates the `Composer · Suggestions` Plex playlist and seeds the mirror.
5. NAS UAT signal: `/debug/suggestions` shows the seeded mirror; `today_calls` increments; the playlist appears in Plex.

In steady state (mirror at target=30) the deficit guard inside `maybe_schedule_refill` short-circuits before any LLM call — so the Pitfall 11 cost circuit breaker semantics and the original "no churn on every play" intent are preserved.

## Self-Check: PASSED

- File `app/services/event_handlers.py`: FOUND, modified at lines 252-272 (gate dropped + 13-line CDL hotfix comment)
- File `tests/test_event_handlers.py`: FOUND, contains `test_empty_mirror_track_not_in_mirror_still_schedules_refill`
- Commit `8594589`: FOUND in git log
- All 4 `TestHandleTrackPlayedSuggestionsDrain` tests pass; full `test_event_handlers.py` (14 tests) green.
- Pre-fix sanity check (git-stash) confirmed the new regression test fails without the source change.
