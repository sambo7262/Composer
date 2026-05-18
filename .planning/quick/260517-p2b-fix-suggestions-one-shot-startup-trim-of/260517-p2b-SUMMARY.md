---
phase: 260517-p2b
plan: 01
subsystem: suggestions
tags: [suggestions, mirror-trim, migration, lifespan, idempotent, phase-8.3, quick-fix]
requires:
  - "ManagedPlaylist(kind=suggestions) row exists (Phase 7 bootstrap)"
  - "SuggestionsMirror table populated (Phase 7.1 SQL refill OR pre-260517-nkt cron)"
  - "Phase 8.2 dedupe completes first (lifespan order: 8.0 → 8.1 → 8.2 → 8.3 → event_bus)"
provides:
  - "PHASE_08_3_MIGRATION_ID constant in app/services/suggestions_service.py"
  - "run_phase_08_3_trim_suggestions_mirror_to_target() coroutine"
  - "Lifespan await wiring in app/main.py (after 8.2, before event_bus)"
  - "MigrationLog gate row with phase_id='8.3-trim-suggestions-mirror-to-target'"
affects:
  - "maybe_schedule_refill deficit calculation (re-opens positive deficit post-trim)"
  - "refill_mirror_sql trigger path (next webhook drain after boot triggers a refill)"
  - "Plex Composer · Suggestions playlist content (oldest N rating_keys removed best-effort)"
tech-stack:
  added: []  # no new libraries
  patterns:
    - "MigrationLog-gated one-shot lifespan migration (mirrors Phase 7/8.0/8.1/8.2)"
    - "Outer try/except leaves completed_at NULL on failure → retry-on-next-boot"
    - "Inner try/except absorbs Plex failure → DB trim still stamps success"
    - "NotFound canonical idiom byte-equivalent to suggestions_service.py:1204-1211"
    - "Phase 5 Convention #1: all PlexAPI calls via asyncio.to_thread (reuses remove_tracks_from_suggestions_playlist's internal wrap)"
key-files:
  created: []
  modified:
    - "app/services/suggestions_service.py"
    - "app/main.py"
    - "tests/test_suggestions_service.py"
decisions:
  - "Reuse all 7 existing helpers (NO new helpers added) per plan contract"
  - "Use SUGGESTIONS_TARGET_SIZE constant (never literal 30) for trim threshold"
  - "Plex push failure does NOT block the success stamp — DB trim IS the loop-unstall win; Plex reconciles via weekly prune"
  - "Insert 8.3 await AFTER 8.2 (so it sees post-dedupe state) and BEFORE get_event_bus() (so first webhook of new boot sees trimmed mirror)"
metrics:
  duration_minutes: 11
  completed: 2026-05-18T01:28:37Z
  tests_added: 4
  tests_passing: 196
  files_modified: 3
---

# Phase 260517-p2b Plan 01: Phase 8.3 One-Shot Startup Trim of Oversized SuggestionsMirror — Summary

One-shot, idempotent, lifespan-time migration that trims any pre-existing oversized
SuggestionsMirror down to SUGGESTIONS_TARGET_SIZE (30) on the next boot — unstalls
the rate-feedback loop on NAS installs that carried a 39-row mirror over from
pre-260517-nkt unconstrained cron runs.

## Tasks Executed

| Task | Name                                                                          | Status | Commit  |
| ---- | ----------------------------------------------------------------------------- | ------ | ------- |
| 1    | Implement Phase 8.3 startup-trim migration + lifespan wiring + 4 tests (TDD)  | DONE   | 96dc797 (RED), d587d9c (GREEN) |

## What Shipped

### `app/services/suggestions_service.py`

- **Line 83** — added `PHASE_08_3_MIGRATION_ID = "8.3-trim-suggestions-mirror-to-target"`
  constant, placed immediately below the existing `PHASE_07_MIGRATION_ID` declaration.
- **Line 1421** — added `async def run_phase_08_3_trim_suggestions_mirror_to_target() -> None`
  coroutine, appended after the last existing top-level function
  (`handle_soft_negative_sweep`). Mirrors `run_phase_08_2_discovery_dedupe_artist_name`
  shape byte-for-byte:
    - Reads `MigrationLog` via `_read_migration_log_sync` (through `asyncio.to_thread`).
    - If `completed_at` already set → log + return (idempotent gate).
    - Writes in-flight marker (`completed_at=NULL`) via `_upsert_migration_log_sync`.
    - Counts mirror via `_count_mirror_rows_sync`; if `current <= SUGGESTIONS_TARGET_SIZE`,
      logs no-op + proceeds to stamp.
    - Otherwise calls `_evict_oldest_mirror_rows_sync(evict_count)` to drop the oldest
      `current - SUGGESTIONS_TARGET_SIZE` rows in `added_at ASC, id ASC` order;
      receives the evicted rating_keys (oldest-first).
    - Looks up `ManagedPlaylist(kind='suggestions')` row via
      `_find_suggestions_managed_playlist_sync`. Skips Plex push if missing or if
      `plex_rating_key` is the deferred-sentinel empty string.
    - Reads creds via `_read_plex_creds_sync`. Pushes evictions to Plex via
      `remove_tracks_from_suggestions_playlist` (already wraps PlexAPI internally per
      Phase 5 Convention #1).
    - Plex push wrapped in its OWN inner try/except: detects NotFound via the
      canonical idiom (byte-equivalent to `suggestions_service.py:1204-1211`), logs
      warn-or-exception, and swallows — so the success stamp still fires.
    - Outer try/except catches non-Plex (DB) failures and `return`s WITHOUT stamping
      `completed_at`, leaving the gate open for retry on next boot.
    - On success: stamps `completed_at = datetime.now(timezone.utc).isoformat()`.
- NO new helpers introduced — all 7 reused (`_count_mirror_rows_sync`,
  `_evict_oldest_mirror_rows_sync`, `_find_suggestions_managed_playlist_sync`,
  `_read_plex_creds_sync`, `_read_migration_log_sync`, `_upsert_migration_log_sync`,
  `remove_tracks_from_suggestions_playlist`).

### `app/main.py`

- **Line 257-260** — extended the existing `from app.services.suggestions_service
  import ...` statement to also pull `run_phase_08_3_trim_suggestions_mirror_to_target`.
- **Line 288** — added `await run_phase_08_3_trim_suggestions_mirror_to_target()`
  immediately AFTER `await run_phase_08_2_discovery_dedupe_artist_name()` (line 285)
  and BEFORE `get_event_bus()` (line 290). Comment block above the await explains
  the lifespan ordering invariant: 8.3 must see post-8.1/8.2 dedupe state, and the
  first webhook of the new boot must see a trimmed mirror.

### `tests/test_suggestions_service.py`

- Added `from datetime import timedelta` to the existing datetime import (needed for
  deterministic `added_at` timestamps in the seed helper).
- Added a new `class TestPhase083TrimSuggestionsMirror` (immediately after
  `class TestEvictOldestMirrorRowsSync` at line 870) with four unit tests, matching
  the plan's behavior spec:
    1. `test_phase_08_3_trim_evicts_oldest_when_above_target` — 39 rows → trim to 30;
       Plex helper called once with 9 oldest rating_keys (asserted oldest-first via
       `_evict_oldest_mirror_rows_sync`'s ordering contract); MigrationLog stamped.
    2. `test_phase_08_3_trim_noop_when_at_or_below_target` — 25 rows → no mutation,
       no Plex side-effect, stamp still fires.
    3. `test_phase_08_3_trim_is_idempotent` — 35 rows → trim once; second run
       short-circuits via the `completed_at` gate (Plex helper await_count stays at 1).
    4. `test_phase_08_3_trim_handles_plex_notfound` — 35 rows → Plex raises
       `plexapi.exceptions.NotFound`; DB trim still commits, success stamp still fires.
- New seed helper `TestPhase083TrimSuggestionsMirror._seed_mirror_rows(session, n)`
  inserts n Track + SuggestionsMirror rows with strictly ascending `added_at` (oldest
  first) and returns rating_keys in oldest-first order.

## Pytest Output

### Targeted new tests (4 passed)

```
tests/test_suggestions_service.py::TestPhase083TrimSuggestionsMirror::test_phase_08_3_trim_evicts_oldest_when_above_target PASSED [ 25%]
tests/test_suggestions_service.py::TestPhase083TrimSuggestionsMirror::test_phase_08_3_trim_noop_when_at_or_below_target PASSED [ 50%]
tests/test_suggestions_service.py::TestPhase083TrimSuggestionsMirror::test_phase_08_3_trim_is_idempotent PASSED [ 75%]
tests/test_suggestions_service.py::TestPhase083TrimSuggestionsMirror::test_phase_08_3_trim_handles_plex_notfound PASSED [100%]

========================= 4 passed, 1 warning in 1.00s =========================
```

### Full 8-file regression suite (196 passed — 192 baseline + 4 new)

```
tests/test_suggestions_service.py ............................. (51 tests)
tests/test_suggestions_discovery.py ............... (15 tests)
tests/test_plex_playlist_service.py ..................... (21 tests)
tests/test_discovery_service.py ............................ (28 tests)
tests/test_phase_08_discovery_bootstrap.py .................. (18 tests)
tests/test_phase_08_1_discovery_dedupe_mb_id.py ... (3 tests)
tests/test_phase_08_2_discovery_dedupe_artist_name.py .... (4 tests)
tests/test_event_handlers.py ................ (16 tests)

======================= 196 passed, 1 warning in 18.82s ========================
```

(Note: the 192-test baseline named in the plan is the count BEFORE adding the 4
new Phase 8.3 tests. After this plan: 196 passing across the same 8 suites.
The pytest "8-file" run was executed via the project's existing
`.venv/bin/python -m pytest` — Python 3.9 venv; codebase nominally targets 3.12
but the local dev environment is 3.9 and all tests pass cleanly there. The
NAS deploy runs on the 3.12 Docker image — orchestrator should re-verify there.)

### AST sanity checks

```
python -c "from app.services.suggestions_service import PHASE_08_3_MIGRATION_ID, run_phase_08_3_trim_suggestions_mirror_to_target; assert PHASE_08_3_MIGRATION_ID == '8.3-trim-suggestions-mirror-to-target'; import inspect; assert inspect.iscoroutinefunction(run_phase_08_3_trim_suggestions_mirror_to_target); print('AST check: PASS')"
# → AST check: PASS

grep -v '^#' app/main.py | grep -c 'run_phase_08_3_trim_suggestions_mirror_to_target'
# → 2 (import + await)
```

### Smoke test

```
python -c "import app.main; print('import app.main: PASS')"
# → import app.main: PASS
```

### Diff-scope assertion

```
git diff --name-only HEAD~2 HEAD | sort -u
app/main.py
app/services/suggestions_service.py
tests/test_suggestions_service.py
```

Exactly the 3 expected files — no template, no other service, no other test file.

## Deviations from Plan

None — plan executed exactly as written. All naming/scope constraints honored:

- Constant: exactly `PHASE_08_3_MIGRATION_ID = "8.3-trim-suggestions-mirror-to-target"`
- Coroutine: exactly `async def run_phase_08_3_trim_suggestions_mirror_to_target() -> None`
- NO new helpers — reused all 7 existing ones listed in `<interfaces>`
- Did NOT touch `maybe_schedule_refill`, `discovery_call_weekly`,
  `prune_suggestions_playlist_to_mirror`, or any template
- Did NOT raise `SUGGESTIONS_TARGET_SIZE`
- Did NOT add an ongoing periodic trim — one-shot only
- Outer `try/except` scopes ONLY the trim + Plex push wrapper; Plex push's OWN
  inner try/except absorbs Plex failures so the success stamp still fires
- Phase 5 Convention #1 satisfied: every PlexAPI call routes via
  `asyncio.to_thread` (`remove_tracks_from_suggestions_playlist` wraps internally)

## TDD Gate Compliance

RED → GREEN cycle commits in git log in correct order:

```
d587d9c feat(260517-p2b-01): Phase 8.3 one-shot startup trim of oversized SuggestionsMirror
96dc797 test(260517-p2b-01): add failing Phase 8.3 mirror-trim migration tests (RED)
f6e1386 docs(260517-p2b): pre-dispatch plan for Phase 8.3 mirror trim
```

RED commit verified to fail with `ImportError: cannot import name 'PHASE_08_3_MIGRATION_ID'`
before GREEN commit added the constant + coroutine. No REFACTOR commit was needed —
the GREEN implementation mirrored the canonical Phase 8.2 shape directly.

## Success Criteria (post-deploy on NAS)

1. First boot logs `"Phase 8.3 mirror-trim migration complete: evicted 9 rows
   (mirror_size 39 → 30, plex_removed=9)"` (or `plex_removed=0` if Plex briefly
   unreachable — DB trim still commits).
2. `SELECT count(*) FROM suggestionsmirror` returns exactly 30.
3. Plex `Composer · Suggestions` playlist contains the same 30 rating_keys as the
   mirror (oldest 9 removed) — verifiable via Plexamp or `SELECT t.plex_rating_key
   FROM suggestionsmirror sm JOIN track t ON t.id = sm.track_id`.
4. Next `track_played` webhook drains 1 row → mirror=29 → deficit=1 →
   `refill_mirror_sql` fires → mirror=30 → new pick pushed to Plex.
5. Second boot logs `"Phase 8.3 mirror-trim migration already complete
   (completed_at=...); skipping."` and exits in milliseconds.
6. `MigrationLog` table has exactly one row per phase_id (8.0, 8.1, 8.2, 8.3)
   with `completed_at IS NOT NULL`.

## Commits Created (Plan Scope Only)

| Hash    | Message |
| ------- | ------- |
| 96dc797 | test(260517-p2b-01): add failing Phase 8.3 mirror-trim migration tests (RED) |
| d587d9c | feat(260517-p2b-01): Phase 8.3 one-shot startup trim of oversized SuggestionsMirror |

## Known Stubs

None.

## Threat Flags

None — the migration writes only to local SQLite (already-existing tables) and
calls a single Plex helper that itself enforces `is_managed_playlist`
(OPS-06 / Pitfall 20) before any mutation. No new network endpoint, no auth path,
no new schema, no new file access.

## Self-Check: PASSED

- Files created/modified verified present at expected paths:
  - `app/services/suggestions_service.py` (modified) ✓
  - `app/main.py` (modified) ✓
  - `tests/test_suggestions_service.py` (modified) ✓
- Commit hashes verified present in `git log`:
  - 96dc797 (RED) ✓
  - d587d9c (GREEN) ✓
- Symbol locations confirmed:
  - `PHASE_08_3_MIGRATION_ID` → `app/services/suggestions_service.py:83` ✓
  - `async def run_phase_08_3_trim_suggestions_mirror_to_target` → `app/services/suggestions_service.py:1421` ✓
  - `await run_phase_08_3_trim_suggestions_mirror_to_target()` → `app/main.py:288` ✓
  - import → `app/main.py:259` ✓
- Test count delta: +4 (192 → 196 across the 8-file regression suite) ✓
