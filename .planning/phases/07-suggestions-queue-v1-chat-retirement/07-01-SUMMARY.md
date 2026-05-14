---
phase: 07-suggestions-queue-v1-chat-retirement
plan: 01
subsystem: queue-foundation
tags: [sqlite, sqlmodel, suggestions-queue, event-bus, plex, lifespan-migration, tdd]

# Dependency graph
requires:
  - phase: 05-plex-event-foundation-rating-sync
    provides: TrackPlayedEvent, event_handlers.dispatch_event, asyncio.to_thread D-09 convention, EventLog dedupe
  - phase: 06-vibe-clustering-llm-pipeline
    provides: ManagedPlaylist(kind), MigrationLog gate pattern, plex_playlist_service.create_playlist
  - phase: 06.2-llm-direct-vibe-assignment
    provides: Anthropic prompt cache pattern (consumed by Plan 02, not this plan)
provides:
  - SuggestionsMirror SQLModel table (SUGG-02 source-of-truth for queue contents)
  - app/services/suggestions_service.py module-level singleton with bootstrap_suggestions_queue, drain_track_from_mirror, maybe_schedule_refill, run_phase_07_suggestions_bootstrap
  - ManagedPlaylist(kind='suggestions') row pattern + lifespan migration gate (phase_id='7.0-suggestions-bootstrap')
  - handle_track_played drain branch on the existing Phase 5 event bus (SUGG-03 drain half)
  - EventLog event_type='suggestions_refill_pending' marker as Plan 02 wakeup signal
  - AST static-analysis coverage extended to suggestions_service.py (Phase 5 D-09 carry-forward)
affects: [07-02-suggestions-refill-llm, 07-03-suggestions-page-ui, 07-04-debug-suggestions]

# Tech tracking
tech-stack:
  added: []  # no new libraries — all reuse: sqlmodel, sqlalchemy.text, asyncio.to_thread
  patterns:
    - "Module-level singleton + get_state() (Phase 5 D-08 carry-forward)"
    - "Every Session(get_engine()) wrapped in _*_sync helper called via asyncio.to_thread (D-09)"
    - "Lazy module import + try/except for cross-service event hooks (Phase 6 D-15 pattern)"
    - "MigrationLog(phase_id) gate for one-shot lifespan migrations (Phase 6.1 D-NEW-09 carry-forward)"
    - "EventLog INSERT OR IGNORE with 5-second-bucket dedupe_key (Phase 5 D-07 carry-forward)"
    - "Sentinel-string ratingKey ('') for deferred Plex playlist creation"

key-files:
  created:
    - app/models/suggestions.py
    - app/services/suggestions_service.py
    - tests/test_suggestions_service.py
    - tests/test_suggestions_migration.py
    - app/models/__init__.py
  modified:
    - app/database.py
    - app/main.py
    - app/services/event_handlers.py
    - app/routers/api_setup.py
    - tests/test_event_handlers.py
    - tests/test_api_setup.py
    - tests/test_finalize_integration.py

key-decisions:
  - "Plan 01 defers actual Plex playlist creation to Plan 02 first refill (PlexAPI rejects empty playlists; Plan 01 has no seed tracks). ManagedPlaylist row carries plex_rating_key='' sentinel."
  - "models/__init__.py kept empty (rather than re-exporting SuggestionsMirror) to avoid SQLModel.metadata pollution for Phase 5 test fixtures that only register a subset of tables."
  - "drain branch returns False (no-op) when played track is not in the mirror — no refill marker written in that case so quiet-but-empty queues don't churn."
  - "_create_plex_suggestions_playlist as an internal async indirection: returns None in Plan 01 (deferred); Plan 02 replaces with real plex_playlist_service.create_playlist call."

patterns-established:
  - "Phase 7 service file naming: app/services/suggestions_service.py module-level _status + get_state singleton."
  - "ManagedPlaylist.plex_rating_key='' sentinel means 'Plex playlist not yet created — first writer creates it'."
  - "Lifespan migration ordering: run_phase_07_suggestions_bootstrap comes AFTER run_phase_61_migration and BEFORE get_event_bus."

requirements-completed:
  - SUGG-01
  - SUGG-02
  - SUGG-03

# Metrics
duration: ~50 min
completed: 2026-05-14
---

# Phase 7 Plan 01: Suggestions Queue Foundation Summary

**SuggestionsMirror SQLite table + bootstrap path (dual-caller: wizard finalize + lifespan migration, both gated by MigrationLog) + handle_track_played drain branch wired into the existing Phase 5 event bus, all without any LLM calls or Plex re-fetches.**

## Performance

- **Duration:** ~50 min
- **Started:** 2026-05-14T03:08:00Z (approx)
- **Completed:** 2026-05-14T03:57:26Z
- **Tasks:** 2 (each TDD: RED → GREEN)
- **Commits:** 4 (2 test commits + 2 feat commits)
- **Files created:** 5
- **Files modified:** 7

## Accomplishments

- Added `SuggestionsMirror` SQLModel table with `UNIQUE(track_id)` + foreign keys to Track and Vibe; 0-based `position`, nullable `rationale`/`vibe_id`/`score` (Plan 02 will fill these on first ranking call).
- Built `app/services/suggestions_service.py` as a module-level singleton mirroring `vibe_service`/`sync_service`. Public API: `bootstrap_suggestions_queue`, `drain_track_from_mirror`, `maybe_schedule_refill`, `run_phase_07_suggestions_bootstrap`, `get_state`. Every `Session(get_engine())` lives inside a `_*_sync` helper called via `asyncio.to_thread` (Phase 5 D-09).
- `bootstrap_suggestions_queue()` is idempotent (short-circuits on existing `ManagedPlaylist(kind='suggestions')` row) and is wired into BOTH callers per D-01 and D-02: the wizard's `/api/setup/finalize` (after `reslot_all_rated_tracks` succeeds, before `state.step='done'`) AND the Phase 7 lifespan migration `run_phase_07_suggestions_bootstrap` (gated by `MigrationLog(phase_id='7.0-suggestions-bootstrap')`, inserted in `app/main.py::lifespan` between `run_phase_61_migration` and `get_event_bus`).
- `handle_track_played` extended with a best-effort `try/except` drain hook (mirrors the slot_track hook on `handle_rating_changed`). Phase 5 view_count + last_viewed_at update still commits first; the drain hook runs after and never breaks the primary path. When drain returns True, the threshold gate writes an `EventLog(event_type='suggestions_refill_pending')` marker so Plan 02's refill consumer wakes up.
- AST static-analysis test in `tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async` now walks `suggestions_service.py` as well — Phase 5 D-09 PlexAPI/`asyncio.to_thread` enforcement now covers the new module.

## SuggestionsMirror DDL

```sql
CREATE TABLE suggestionsmirror (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL,                         -- FK -> track(id), UNIQUE
    position INTEGER NOT NULL,                         -- 0-based; lower = top
    added_at VARCHAR NOT NULL,                         -- ISO 8601 UTC
    rationale VARCHAR,                                 -- "Why this track?" (Plan 02 fills)
    vibe_id INTEGER,                                   -- FK -> vibe(id) (Plan 02 fills)
    score FLOAT,                                       -- distance-to-centroid (Plan 02 fills)
    FOREIGN KEY(track_id) REFERENCES track(id),
    FOREIGN KEY(vibe_id) REFERENCES vibe(id),
    UNIQUE(track_id)
);
CREATE INDEX ix_suggestionsmirror_track_id ON suggestionsmirror (track_id);
CREATE INDEX ix_suggestionsmirror_vibe_id ON suggestionsmirror (vibe_id);
```

> SQLModel stores `str` as `VARCHAR` and `Optional[float]` as `FLOAT` in SQLite — these are type-affinity equivalent to `TEXT` and `REAL` respectively (SQLite ignores the type label for storage but uses affinity rules at row-read time).

## suggestions_service public API

```python
SUGGESTIONS_TARGET_SIZE = 30                  # SUGG-01 default
SUGGESTIONS_PLAYLIST_NAME = "Composer · Suggestions"
PHASE_07_MIGRATION_ID = "7.0-suggestions-bootstrap"
DEFERRED_PLEX_RATING_KEY_SENTINEL = ""        # Plan 01 only; Plan 02 replaces

async def bootstrap_suggestions_queue() -> None: ...
async def drain_track_from_mirror(rating_key: str) -> bool: ...
async def maybe_schedule_refill(target: int = SUGGESTIONS_TARGET_SIZE) -> int: ...
async def run_phase_07_suggestions_bootstrap() -> None: ...
async def _create_plex_suggestions_playlist() -> Optional[str]: ...  # internal hook
def get_state() -> SuggestionsServiceStatus: ...
```

## Task Commits

1. **Task 1 RED — failing tests for SuggestionsMirror + bootstrap + migration gate** — `8067181` (`test`)
2. **Task 1 GREEN — SuggestionsMirror model + suggestions_service + Phase 7 lifespan migration** — `65f917d` (`feat`)
3. **Task 2 RED — failing tests for handle_track_played drain branch + wizard finalize bootstrap hook** — `f8942cc` (`test`)
4. **Task 2 GREEN — wire handle_track_played drain + wizard finalize bootstrap hook + scope existing finalize tests to kind='vibe'** — `bcb79fb` (`feat`)

## Files Created/Modified

### Created
- `app/models/suggestions.py` — `SuggestionsMirror` SQLModel table (SUGG-02).
- `app/services/suggestions_service.py` — public API + 7 `_*_sync` DB helpers + 4 async entry points + 1 internal Plex creation indirection (deferred for Plan 01).
- `tests/test_suggestions_service.py` — 12 tests: schema shape, UNIQUE constraint, bootstrap idempotency, drain return values + edge cases, refill threshold gate, AST conformance.
- `tests/test_suggestions_migration.py` — 3 tests: fresh-DB migration writes both rows, gate idempotency, failure leaves gate unset.
- `app/models/__init__.py` — kept EMPTY (deviation from plan; see below).

### Modified
- `app/database.py` — `init_db()` imports `SuggestionsMirror` before `create_all` so the table lands on fresh installs.
- `app/main.py` — `lifespan()` now calls `run_phase_07_suggestions_bootstrap()` between `run_phase_61_migration` and `get_event_bus`.
- `app/services/event_handlers.py` — `handle_track_played` extended with best-effort drain + refill threshold hook (lazy module import + try/except, mirrors `handle_rating_changed`'s slot_track hook).
- `app/routers/api_setup.py` — `finalize()` now awaits `bootstrap_suggestions_queue()` after `reslot_all_rated_tracks` succeeds, wrapped in best-effort try/except so transient Plex issues don't block wizard completion.
- `tests/test_event_handlers.py` — AST test paths extended to include `suggestions_service.py`; new `TestHandleTrackPlayedSuggestionsDrain` class with 3 tests; new `db_with_phase7` fixture.
- `tests/test_api_setup.py` — 2 new finalize-hook tests (`test_finalize_calls_bootstrap_suggestions_queue_after_reslot`, `test_finalize_bootstrap_failure_does_not_break_finalize_response`) + new `client_with_phase7` fixture; one existing test scope-narrowed to `kind='vibe'`.
- `tests/test_finalize_integration.py` — two existing finalize integration tests scope-narrowed to `kind='vibe'` ManagedPlaylist rows because the lifespan migration now inserts a `kind='suggestions'` row at TestClient startup.

## Decisions Made

1. **Plan 01 defers Plex playlist creation (Rule 3 — blocking issue).** PlexAPI rejects `createPlaylist` calls with empty `items` lists (`BadRequest: When no items are included to create the playlist`). Plan 01 has no seed tracks yet (Plan 02 ships the LLM ranking + first batch). The ManagedPlaylist row is registered with `plex_rating_key=''` sentinel. Plan 02 will detect the sentinel, call `plex_playlist_service.create_playlist(plex_url, plex_token, SUGGESTIONS_PLAYLIST_NAME, seed_keys)` with the first batch, and UPDATE the row's `plex_rating_key`. Tests patch the internal `_create_plex_suggestions_playlist` helper rather than `plex_playlist_service.create_playlist` directly. This deviates from the plan's example wording but preserves the plan's INTENT (idempotent bootstrap, one ManagedPlaylist row per install).

2. **`app/models/__init__.py` kept empty (Rule 1 — bug fix).** Plan said to append `from app.models.suggestions import SuggestionsMirror  # noqa: F401`. Doing so caused a test-order-dependent regression: Phase 5 test fixtures that only register a subset of tables would trigger `SQLModel.metadata.create_all` against a metadata graph polluted by SuggestionsMirror's FK to Vibe — Vibe wasn't registered in those fixtures, so create_all failed with `NoReferencedTableError`. The fix: keep the import inside `app/database.py::init_db()` only (which already imports BOTH Vibe AND SuggestionsMirror). Production lifespan calls `init_db()` so the table lands on fresh installs as intended.

3. **Refill marker uses 5-second-bucket dedupe.** `_insert_refill_pending_marker_sync` uses `dedupe_key="suggestions_refill_pending|{bucket}"` with the same 5-second window pattern as Phase 5 D-07. Tight-loop callers (e.g. a skip-bomb scrobble burst) collapse via `INSERT OR IGNORE` at the SQLite UNIQUE layer — Plan 02 only needs to dispatch on the marker, not count it.

4. **Existing finalize tests scope-narrowed to `kind='vibe'` (Rule 1 — test regression).** Previously they asserted `len(ManagedPlaylist) == 3`. With the lifespan migration running at TestClient startup, the count is now 4 (3 vibe + 1 suggestions). Filtering by `kind == 'vibe'` preserves the Phase 6 contract semantics while accommodating the new Phase 7 row.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Plex playlist creation deferred to Plan 02**
- **Found during:** Task 1 (design)
- **Issue:** Plan's example code called `await create_playlist(SUGGESTIONS_PLAYLIST_NAME, [])`. The real `plex_playlist_service.create_playlist` signature is `(plex_url, plex_token, name, rating_keys, vibe_id=None)` and raises `ValueError("No tracks to push")` on empty `rating_keys`. PlexAPI itself rejects empty-items `createPlaylist` calls with `BadRequest`. Plan 01 has no seed tracks (Plan 02 ships the first ranking call).
- **Fix:** Added internal `_create_plex_suggestions_playlist` async indirection that returns `None` (deferred) in Plan 01. Bootstrap detects None and uses `plex_rating_key=''` sentinel. Plan 02 will replace this hook with a real `plex_playlist_service.create_playlist` call using the first batch of suggestions, then update the ManagedPlaylist row.
- **Files modified:** `app/services/suggestions_service.py`, `tests/test_suggestions_service.py`, `tests/test_suggestions_migration.py`
- **Verification:** All 15 Plan 01 service+migration tests pass. The Plex playlist creation is correctly deferred; the DB-side invariant (one ManagedPlaylist row per install) holds.
- **Committed in:** `65f917d` (Task 1 GREEN)

**2. [Rule 1 - Bug] `app/models/__init__.py` kept empty rather than re-exporting SuggestionsMirror**
- **Found during:** Task 1 (regression sweep)
- **Issue:** Plan said to append the SuggestionsMirror re-export to `app/models/__init__.py`. Doing so polluted SQLModel.metadata across test sessions: Phase 5 fixtures that only register Phase 5 tables would then trigger `metadata.create_all` against a graph that included SuggestionsMirror (with FK → Vibe), but Vibe was NOT in metadata for those fixtures → `NoReferencedTableError`.
- **Fix:** Kept `app/models/__init__.py` empty. Production registration happens via `app.database.init_db()` which imports BOTH vibe AND suggestions models before `create_all`. The plan's invariant ("SQLModel.metadata picks up the table before `init_db()` runs `create_all`") is satisfied via the `init_db` import directly.
- **Files modified:** `app/models/__init__.py`, `app/database.py`
- **Verification:** 186-test regression sweep passes (`test_database*`, `test_event_handlers*`, `test_api_setup*`, `test_finalize_integration*`, `test_phase61_migration`, `test_plex_playlist_service*`, `test_pages*`, etc.).
- **Committed in:** `65f917d` (Task 1 GREEN)

**3. [Rule 1 - Bug] Existing finalize tests scope-narrowed to `kind='vibe'`**
- **Found during:** Task 2 (regression sweep)
- **Issue:** `test_post_setup_finalize_creates_vibes_and_playlists`, `test_finalize_integration_creates_three_vibes_and_three_playlists`, and `test_finalize_integration_handles_partial_failure` all asserted a specific ManagedPlaylist row count (3 or 2). With the lifespan migration now running at TestClient startup and inserting a `kind='suggestions'` row, those counts shift by +1.
- **Fix:** Each affected assertion now filters `ManagedPlaylist.kind == 'vibe'` so the Phase 6 contract is unaffected while accommodating the new Phase 7 row. Vibe rows still count == 3 / 2; tests assert the vibe-specific invariant directly.
- **Files modified:** `tests/test_api_setup.py`, `tests/test_finalize_integration.py`
- **Verification:** All 3 affected tests pass + the 2 new Phase 7 finalize-hook tests pass.
- **Committed in:** `bcb79fb` (Task 2 GREEN)

**4. [Rule 1 - Bug] Test schema assertion accepts SQLModel's VARCHAR/FLOAT affinity**
- **Found during:** Task 1 (GREEN — first test run)
- **Issue:** Initial test asserted `column_type == "TEXT"`/`"REAL"`; SQLModel actually emits `VARCHAR`/`FLOAT` for those Python types. SQLite treats them as the same affinity, so this is a labeling issue, not a semantic one.
- **Fix:** Test now accepts `{"TEXT", "VARCHAR"}` and `{"REAL", "FLOAT", "DOUBLE"}` sets.
- **Files modified:** `tests/test_suggestions_service.py`
- **Verification:** `test_suggestions_mirror_table_created` passes.
- **Committed in:** `65f917d` (Task 1 GREEN — minor test-only fix landed with the service code commit)

---

**Total deviations:** 4 auto-fixed (1 blocking, 3 bug fixes).
**Impact on plan:** Plan-1 invariants preserved (idempotent bootstrap, single ManagedPlaylist row per install, drain branch correctly wired, AST coverage extended). Deviations isolate the impedance mismatch between the plan example's `create_playlist(name, [])` call and the real 5-arg signature; the deferral pattern is documented as a sentinel that Plan 02 will replace. No scope creep — all deviations were necessary to make the plan's TDD tests pass on real code.

## TDD Gate Compliance

Plan 07-01 followed RED → GREEN cycles. Git log shows the gate sequence:

1. `8067181` **`test(07-01)`** — RED for Task 1 (SuggestionsMirror, bootstrap, migration gate).
2. `65f917d` **`feat(07-01)`** — GREEN for Task 1 (model + service + lifespan wiring).
3. `f8942cc` **`test(07-01)`** — RED for Task 2 (drain branch + wizard hook + AST).
4. `bcb79fb` **`feat(07-01)`** — GREEN for Task 2.

Both `test(...)` commits preceded their `feat(...)` counterparts. No REFACTOR commits were needed — implementations were minimal and aligned with the plan's contract.

## Issues Encountered

- **Test-order-dependent metadata pollution** when `app/models/__init__.py` re-exports SuggestionsMirror. Resolved by keeping `__init__.py` empty (Deviation 2) — `init_db()` is the canonical registration point.
- **Pre-existing test failures** in `test_chat_service.py`, `test_sync_*.py`, `test_audio_analyzer.py`, `test_analysis_service.py` (12-14 failures depending on import-order). Confirmed pre-existing by `git stash`-ing my changes and re-running: same failures occur. **Out of Plan 01 scope** — logged here for visibility only. Not auto-fixed (Plan 01 SCOPE BOUNDARY rule).

## Threat Flags

None — no new HTTP endpoints, no new network egress, no new auth surface. Phase 7 Plan 01's threat surface is the DB schema addition (SuggestionsMirror with parameterized DELETE — covered by T-07-01-01 mitigation in the plan's threat model). All other plan threats (T-07-01-02..07) carry through unchanged.

## Next Phase Readiness

Plan 02 (Suggestions refill + LLM ranking) has all the prerequisites:

- `SuggestionsMirror` table exists, ready for INSERTs.
- `ManagedPlaylist(kind='suggestions')` row is registered with `plex_rating_key=''` sentinel — Plan 02 detects the sentinel, calls `create_playlist` with the first batch, and updates the row.
- `EventLog(event_type='suggestions_refill_pending')` markers fire from the drain path — Plan 02's refill consumer can poll/subscribe to these or call `maybe_schedule_refill` directly.
- The threshold gate (`maybe_schedule_refill`) accepts a `target` parameter so Plan 02 can pass a configurable target if SUGG-01's "30" default is exposed in settings later.
- AST static-analysis coverage extended to suggestions_service.py — Plan 02 additions land under the same Phase 5 D-09 enforcement.

No blockers. The deferred Plex playlist creation is a documented sentinel pattern, not a hidden hack — Plan 02's first refill call is the natural place to create the actual Plex playlist with a non-empty `items` list.

## Self-Check: PASSED

Verified files exist:
- `app/models/suggestions.py` — FOUND
- `app/services/suggestions_service.py` — FOUND
- `tests/test_suggestions_service.py` — FOUND
- `tests/test_suggestions_migration.py` — FOUND
- `.planning/phases/07-suggestions-queue-v1-chat-retirement/07-01-SUMMARY.md` — FOUND (this file)

Verified commits exist:
- `8067181` test(07-01): add failing tests for SuggestionsMirror + bootstrap + migration gate — FOUND
- `65f917d` feat(07-01): SuggestionsMirror table + bootstrap service + Phase 7 lifespan migration — FOUND
- `f8942cc` test(07-01): add failing tests for handle_track_played drain branch + wizard finalize bootstrap hook — FOUND
- `bcb79fb` feat(07-01): wire handle_track_played drain + wizard finalize bootstrap hook — FOUND

Plan verification suite: `pytest tests/test_suggestions_service.py tests/test_suggestions_migration.py tests/test_event_handlers.py tests/test_api_setup.py` → **63 passed, 1 warning**.

Broader regression sweep (19 test files): **186 passed**.

---
*Phase: 07-suggestions-queue-v1-chat-retirement*
*Completed: 2026-05-14*
