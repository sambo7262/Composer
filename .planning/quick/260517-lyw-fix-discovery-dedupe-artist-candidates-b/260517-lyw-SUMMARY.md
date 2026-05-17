---
phase: quick-260517-lyw
plan: 01
subsystem: discovery
tags: [discovery, lidarr, listenbrainz, dedupe, migration, sqlmodel, asyncio]

# Dependency graph
requires:
  - phase: 08-lidarr-discovery
    provides: DiscoveryCandidate model, run_phase_08_discovery_bootstrap migration gate pattern, artist_discovery_call_weekly LLM pipeline, MigrationLog table
provides:
  - PHASE_08_1_MIGRATION_ID constant ("8.1-discovery-dedupe-mb-id")
  - _dedupe_discovery_candidates_sync ORM helper (lowest llm_rank wins, id tiebreak)
  - run_phase_08_1_discovery_dedupe_mb_id lifespan one-shot migration
  - In-memory by_mbid dedup block at write boundary in artist_discovery_call_weekly
  - Lifespan wire-up between run_phase_08_discovery_bootstrap and get_event_bus()
affects: [phase-08, phase-09, /discover page, ListenBrainz rate limiting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "MigrationLog-gated lifespan one-shot (mirrors Phase 6.1, 7.0, 7.1, 8.0 shape)"
    - "In-memory dedup at write boundary, NOT a DB UNIQUE constraint (lighter migration)"
    - "Tiebreak lockstep: in-memory + SQL paths share COALESCE(llm_rank, 9999) rule with cross-reference comments"

key-files:
  created:
    - tests/test_phase_08_1_discovery_dedupe_mb_id.py
  modified:
    - app/services/discovery_service.py
    - app/main.py
    - tests/test_discovery_service.py

key-decisions:
  - "Tiebreak rule MUST match between in-memory dedup and SQL migration — both COALESCE(llm_rank, 9999) ASC, then stable order / id ASC. Cross-reference comments point at each other to keep future edits in sync."
  - "Migration preserves the winner row's seed_vibe_id as-is — lowest llm_rank IS the answer; do not second-guess which vibe should own the artist."
  - "No DB UNIQUE constraint on DiscoveryCandidate.mb_id — explicit out-of-scope per plan (deferred to a separate heavier migration). Two-layer defense (write-time + migration) is sufficient."
  - "Migration helper uses SQLModel ORM (Session + session.exec + select), matching existing _bootstrap_*_sync style — NOT raw SQL."

patterns-established:
  - "Cross-reference comments between two dedup paths: when correctness depends on two code locations sharing a rule, each comment must name the other."
  - "Migration ordering in lifespan: run_phase_08_discovery_bootstrap → run_phase_08_1_discovery_dedupe_mb_id → get_event_bus()."

requirements-completed:
  - QUICK-DEDUPE-MBID-WRITE
  - QUICK-DEDUPE-MBID-CLEANUP

# Metrics
duration: ~10min
completed: 2026-05-17
---

# Quick Task 260517-lyw: Fix /discover duplicate-artist-card bug Summary

**Two-layer mb_id dedup for DiscoveryCandidate — in-memory collapse before write at artist_discovery_call_weekly + one-shot Phase 8.1 lifespan migration to clean pre-existing duplicates already on the NAS DB.**

## Performance

- **Duration:** ~10 min (commit window 15:56 → 16:06 PDT)
- **Started:** 2026-05-17T22:56:53Z (plan dispatch commit)
- **Completed:** 2026-05-17T23:14:37Z
- **Tasks:** 3 (2 TDD + 1 verification)
- **Files modified:** 4 (1 new, 3 modified)
- **Commits in plan:** 4 (2x TDD test→fix/feat pairs)

## Accomplishments

- Eliminated the duplicate-artist-card root cause that fired identical `hx-trigger="revealed once"` requests for the same `mb_id` (the trigger for the ListenBrainz HTTP 429 cascade observed in NAS logs 2026-05-17 15:06:55-15:07:03).
- Future weekly cron writes go through the in-memory `by_mbid` reducer BEFORE `_write_discovery_candidates_sync`, so the duplicate state cannot re-form.
- Pre-existing duplicate rows on the NAS DB will collapse to single winners (lowest `llm_rank`, NULL → 9999 via COALESCE, id tiebreak) on the next container start, then the migration becomes a permanent no-op via `MigrationLog(phase_id="8.1-discovery-dedupe-mb-id")`.

## Task Commits

Each task was committed atomically (TDD: test → implementation):

1. **Task 1 RED — failing dedup regression tests** — `f048379` (test)
2. **Task 1 GREEN — in-memory by_mbid collapse in artist_discovery_call_weekly** — `acf230e` (fix)
3. **Task 2 RED — failing Phase 8.1 migration tests** — `504ea6c` (test)
4. **Task 2 GREEN — constant + helper + migration + lifespan wire-up** — `8f8f739` (feat)

Task 3 (verification gates) required no code changes — all gates passed on first run.

## Files Created/Modified

- **`app/services/discovery_service.py`** (+118 lines)
  - Added `PHASE_08_1_MIGRATION_ID = "8.1-discovery-dedupe-mb-id"` next to existing `PHASE_08_MIGRATION_ID`.
  - Added `_dedupe_discovery_candidates_sync()` sync ORM helper next to `_bootstrap_baseline_sync` (returns count of deleted rows).
  - Added `async def run_phase_08_1_discovery_dedupe_mb_id()` immediately after `run_phase_08_discovery_bootstrap` (gate → in-flight marker → try/except → stamp pattern).
  - Inserted in-memory `by_mbid` reducer block inside `artist_discovery_call_weekly` between the LLM hallucination filter loop and `_write_discovery_candidates_sync`.
- **`app/main.py`** (+6 lines)
  - Lifespan now awaits `run_phase_08_1_discovery_dedupe_mb_id()` AFTER `run_phase_08_discovery_bootstrap` and BEFORE `get_event_bus()`.
- **`tests/test_phase_08_1_discovery_dedupe_mb_id.py`** (NEW, 193 lines)
  - `test_dedupe_keeps_lowest_llm_rank_and_stamps`: 4 rows for mb_id='A' with ranks [3,1,2,None] collapse to one (rank=1 wins, NULL becomes 9999), unique mb_id='B' untouched, gate stamped.
  - `test_dedupe_is_idempotent`: second run short-circuits via the gate; row count + completed_at stamp both unchanged.
  - `test_dedupe_handles_zero_duplicates`: no duplicates pre-existing → rows preserved AND gate still stamped so subsequent boots short-circuit.
- **`tests/test_discovery_service.py`** (+210 lines)
  - `test_artist_discovery_call_weekly_dedupes_mb_id_across_vibes`: same mb_id surfaces from two vibe seeds + LLM returns it twice with ranks 5 and 2, plus a unique mb_id with rank 7 → writer receives exactly 2 records with the lower rank winning.
  - `test_artist_discovery_call_weekly_dedupe_tiebreak_keeps_first`: equal-rank duplicates collapse to one record, first-seen wins (stable iteration order).

## Decisions Made

- **Tiebreak rule lockstep:** Both dedup paths use `lowest COALESCE(llm_rank, 9999) ASC, then id ASC (or stable iteration order)`. The in-memory comment at `artist_discovery_call_weekly:~1293` explicitly names `_dedupe_discovery_candidates_sync`; the migration helper's docstring at `discovery_service.py:260-262` explicitly names `artist_discovery_call_weekly ~line 1185`. Future edits to either side must update both.
- **Migration uses SQLModel ORM** (matching `_bootstrap_*_sync` style), NOT raw SQL — keeps the existing migration shape consistent.
- **Winner's `seed_vibe_id` is never mutated** — the lowest-rank pick already encodes the right vibe context.
- **No DB UNIQUE constraint** — explicit out-of-scope per the plan; two-layer defense (write-time dedup + one-shot migration) is sufficient for the bug class.

## Deviations from Plan

None - plan executed exactly as written. All three Task 3 verification gates passed on first run:

- **Scope gate:** `git diff --stat HEAD~4 HEAD` shows EXACTLY 4 files, all in the expected set (`app/services/discovery_service.py`, `app/main.py`, `tests/test_phase_08_1_discovery_dedupe_mb_id.py`, `tests/test_discovery_service.py`). No template files, no model changes, no `.sql` migration files touched.
- **Static grep gates:** `PHASE_08_1_MIGRATION_ID = "8.1-discovery-dedupe-mb-id"` occurrence count == 1; `await run_phase_08_1_discovery_dedupe_mb_id` occurrence count == 1; `by_mbid` occurrence count == 6 (>= 4 required).
- **Cross-reference gates:** Both tiebreak comments explicitly name each other.

## Issues Encountered

None during planned work. The 14 pre-existing failures in the full suite (`tests/test_analysis_service.py`, `tests/test_audio_analyzer.py`, `tests/test_chat_service.py`, `tests/test_sync_api.py`, `tests/test_sync_scheduler.py`, `tests/test_sync_service.py`) reproduce against the base commit `224a0b3` — confirmed unrelated to this plan, no overlap with files modified in this work.

## Test Output (regression suite gate from `<constraints>`)

```
$ python -m pytest tests/test_discovery_service.py tests/test_phase_08_discovery_bootstrap.py tests/test_phase_08_1_discovery_dedupe_mb_id.py -x
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/Oreo/Projects/Composer/.claude/worktrees/agent-aaa748cbdccb1fe5d
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.2.0
collected 48 items

tests/test_discovery_service.py ...........................              [ 56%]
tests/test_phase_08_discovery_bootstrap.py ..................            [ 93%]
tests/test_phase_08_1_discovery_dedupe_mb_id.py ...                      [100%]

======================== 48 passed, 1 warning in 3.60s =========================
```

48/48 targeted tests pass:
- 27 in `test_discovery_service.py` (existing 25 + 2 new regression tests for write-time dedup).
- 18 in `test_phase_08_discovery_bootstrap.py` (existing — unchanged behavior, proves the lifespan reordering did not break the Phase 8 bootstrap gate).
- 3 in `test_phase_08_1_discovery_dedupe_mb_id.py` (new — migration winner, idempotency, zero-duplicates).

## User Setup Required

None - no external service configuration required. Migration runs automatically on the next container restart.

## Next Phase Readiness

- Ready for NAS deploy. On the next container start:
  1. `run_phase_08_discovery_bootstrap` runs (idempotent — already complete on NAS).
  2. `run_phase_08_1_discovery_dedupe_mb_id` runs ONCE, logs `"Phase 8.1 dedupe migration complete: deleted N duplicate DiscoveryCandidate rows."`, stamps the gate.
  3. Every subsequent boot, the migration short-circuits with `"Phase 8.1 dedupe migration already complete (completed_at=...); skipping."`.
- `/discover` page renders each artist (by `mb_id`) AT MOST ONCE.
- The next weekly cron tick (`artist_discovery_call_weekly`) writes deduped candidates — no new duplicate rows form.

## Self-Check: PASSED

Verified all claims:

- File `app/services/discovery_service.py` exists with `PHASE_08_1_MIGRATION_ID` (4 occurrences via docstring + use sites), `_dedupe_discovery_candidates_sync`, `run_phase_08_1_discovery_dedupe_mb_id`, and `by_mbid` block (6 occurrences).
- File `app/main.py` exists with `await run_phase_08_1_discovery_dedupe_mb_id()` (2 occurrences: import + await).
- File `tests/test_phase_08_1_discovery_dedupe_mb_id.py` exists (193 lines, 3 tests).
- File `tests/test_discovery_service.py` exists with both new regression tests.
- Commits exist in `git log --all`:
  - `f048379` test(260517-lyw-01) — RED for Task 1
  - `acf230e` fix(260517-lyw-01) — GREEN for Task 1
  - `504ea6c` test(260517-lyw-02) — RED for Task 2
  - `8f8f739` feat(260517-lyw-02) — GREEN for Task 2

---
*Quick task: 260517-lyw*
*Completed: 2026-05-17*
