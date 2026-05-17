---
phase: quick-260517-lyw
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/discovery_service.py
  - app/main.py
  - tests/test_phase_08_1_discovery_dedupe_mb_id.py
  - tests/test_discovery_service.py
autonomous: true
requirements:
  - QUICK-DEDUPE-MBID-WRITE
  - QUICK-DEDUPE-MBID-CLEANUP
must_haves:
  truths:
    - "On /discover, each artist (by mb_id) renders AT MOST ONCE — never twice within a vibe, never across vibes."
    - "After Phase 8.1 migration runs once on the NAS DB, all pre-existing duplicate DiscoveryCandidate rows (same mb_id) are collapsed to a single winner row chosen by lowest llm_rank (ties broken by lowest id)."
    - "After Phase 8.1 stamps complete, the migration is a no-op on every subsequent boot (idempotent via MigrationLog gate)."
    - "Future weekly-cron writes go through in-memory dedup BEFORE _write_discovery_candidates_sync, so the duplicate state cannot re-form."
    - "App startup runs Phase 8 bootstrap FIRST, then Phase 8.1 dedup, then event bus — order matches the existing lifespan pattern."
  artifacts:
    - path: "app/services/discovery_service.py"
      provides: "PHASE_08_1_MIGRATION_ID constant + run_phase_08_1_discovery_dedupe_mb_id() + _dedupe_discovery_candidates_sync() + in-memory dedup block inside artist_discovery_call_weekly"
      contains: "PHASE_08_1_MIGRATION_ID"
    - path: "app/main.py"
      provides: "Lifespan wire-up of run_phase_08_1_discovery_dedupe_mb_id between Phase 8 bootstrap and get_event_bus()"
      contains: "run_phase_08_1_discovery_dedupe_mb_id"
    - path: "tests/test_phase_08_1_discovery_dedupe_mb_id.py"
      provides: "Three migration tests: tiebreak winner, idempotency, zero-duplicates stamp"
      min_lines: 80
    - path: "tests/test_discovery_service.py"
      provides: "Regression test: cross-vibe mb_id dedup in artist_discovery_call_weekly"
      contains: "test_artist_discovery_call_weekly_dedupes_mb_id_across_vibes"
  key_links:
    - from: "app/main.py:lifespan"
      to: "app.services.discovery_service.run_phase_08_1_discovery_dedupe_mb_id"
      via: "await call placed AFTER run_phase_08_discovery_bootstrap and BEFORE get_event_bus()"
      pattern: "await run_phase_08_1_discovery_dedupe_mb_id"
    - from: "app/services/discovery_service.py:artist_discovery_call_weekly"
      to: "_write_discovery_candidates_sync"
      via: "in-memory mb_id dedup applied to valid_picks BEFORE the asyncio.to_thread(_write_discovery_candidates_sync, valid_picks) call"
      pattern: "by_mbid"
    - from: "tests/test_phase_08_1_discovery_dedupe_mb_id.py"
      to: "app.services.discovery_service.run_phase_08_1_discovery_dedupe_mb_id"
      via: "_run_async helper (mirrored from tests/test_phase_08_discovery_bootstrap.py:192-197)"
      pattern: "_run_async\\(run_phase_08_1_discovery_dedupe_mb_id"
---

<objective>
Fix the /discover duplicate-artist-card bug end to end. The same artist (same `mb_id`) is rendering multiple times — both WITHIN a single vibe section and ACROSS vibe sections — because `artist_discovery_call_weekly` loops per active vibe and the LLM picks the same artist once per vibe context, then `_write_discovery_candidates_sync` does a plain `session.add()` with no dedup (per its own docstring, the writer is intentionally insert-only and explicitly defers cleanup to "a future quick task"). This IS that future quick task.

Two-part fix, both required, both this plan:
1. **Write-time dedup** in `artist_discovery_call_weekly` (`app/services/discovery_service.py:~1185`) — collapse `valid_picks` by `mb_id` keeping the lowest `llm_rank` BEFORE writing. Prevents the duplicate state from ever re-forming.
2. **One-shot startup migration** `run_phase_08_1_discovery_dedupe_mb_id` — collapses pre-existing duplicates already on the NAS production DB. Gated by `MigrationLog(phase_id="8.1-discovery-dedupe-mb-id")` so it runs once on next deploy and never again. Mirrors the exact shape of the existing `run_phase_08_discovery_bootstrap` (already in this same file at `:250-304`).

Purpose: Eliminate the cascading ListenBrainz HTTP 429 storm caused by duplicate cards firing the same `hx-trigger="revealed once"` from identical DOM ids (`discover-card-{mb_id}`, `top-tracks-{mb_id}`), and stop showing users the same artist multiple times on /discover.

Output: One PR's worth of edits across 2 source files + 2 test files. NO schema changes, NO template changes, NO UNIQUE constraint (explicitly out of scope — that's a separate heavier migration).
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.planning/STATE.md
@app/services/discovery_service.py
@app/main.py
@app/models/discovery.py
@app/models/vibe.py
@tests/test_phase_08_discovery_bootstrap.py
@tests/conftest.py

<interfaces>
<!-- Key contracts the executor needs. Extracted from the codebase. -->
<!-- The executor should use these directly — no extra codebase exploration required. -->

From `app/services/discovery_service.py` (already exists, REUSE — do not duplicate):

```python
# Top of module — add the new constant right next to this one (~line 40):
PHASE_08_MIGRATION_ID = "8.0-discovery-bootstrap"

# Migration log helpers — already defined at lines 107 and 114. REUSE both:
def _read_migration_log_sync(phase_id: str) -> Optional[MigrationLog]: ...
def _upsert_migration_log_sync(phase_id: str, completed_at: Optional[str]) -> None: ...

# The shape to MIRROR (lines 250-304) — copy this skeleton for the new function:
async def run_phase_08_discovery_bootstrap() -> None:
    existing = await asyncio.to_thread(_read_migration_log_sync, PHASE_08_MIGRATION_ID)
    if existing is not None and existing.completed_at is not None:
        logger.info("Phase 8 discovery bootstrap already complete; skipping.")
        return
    await asyncio.to_thread(_upsert_migration_log_sync, PHASE_08_MIGRATION_ID, None)
    try:
        await asyncio.to_thread(_bootstrap_baseline_sync)
        # ... more sync helpers via asyncio.to_thread
    except Exception:
        logger.exception("Phase 8 discovery bootstrap failed; will retry on next restart.")
        return
    await asyncio.to_thread(
        _upsert_migration_log_sync,
        PHASE_08_MIGRATION_ID,
        datetime.now(timezone.utc).isoformat(),
    )
    logger.info("Phase 8 discovery bootstrap complete.")

# Writer (line 790) — UNCHANGED. Dedup happens at the caller, not here:
def _write_discovery_candidates_sync(records: list) -> int: ...

# Caller (line 984+) — `valid_picks` is built per pick in the for-loop ending
# at line 1185, then immediately consumed by:
written = await asyncio.to_thread(_write_discovery_candidates_sync, valid_picks)  # line 1187-1189
# Dedup goes BETWEEN the for-loop's close (line 1185) and the asyncio.to_thread call.
```

From `app/models/discovery.py` (DiscoveryCandidate — NO model changes):

```python
class DiscoveryCandidate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mb_id: str = Field(index=True)            # indexed but NOT unique — that's the bug surface
    artist_name: str
    seed_track_id: int = Field(foreign_key="track.id", index=True)
    seed_vibe_id: int = Field(foreign_key="vibe.id", index=True)
    mb_listener_count: Optional[int] = None
    popularity_gate_pass: bool = Field(default=False)
    llm_rank: Optional[int] = None             # used as tiebreak winner key
    llm_rationale: Optional[str] = None
    factual_hook: Optional[str] = None
    created_at: str = Field(index=True)
```

From `app/models/vibe.py:206-216` (MigrationLog — already exists, REUSE):

```python
class MigrationLog(SQLModel, table=True):
    phase_id: str = Field(primary_key=True)
    completed_at: Optional[str] = Field(default=None)
```

From `app/main.py:228-268` (lifespan ordering — splice the new await between these two existing lines):

```python
# line 265-266 (existing):
from app.services.discovery_service import run_phase_08_discovery_bootstrap
await run_phase_08_discovery_bootstrap()
# <-- NEW MIGRATION GOES HERE (Task 2) -->
# line 268 (existing):
get_event_bus()
```

From `tests/test_phase_08_discovery_bootstrap.py:192-219` (the test pattern to MIRROR exactly):

```python
def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# fresh_db fixture — defined in tests/conftest.py:131; provides a clean SQLite
# Session, init_db() already run. Use it as the test arg.

def test_bootstrap_stamps_baseline_once(fresh_db):
    # ... arrange ...
    _run_async(run_phase_08_discovery_bootstrap())
    log = fresh_db.exec(
        select(MigrationLog).where(MigrationLog.phase_id == PHASE_08_MIGRATION_ID)
    ).first()
    assert log is not None
    assert log.completed_at is not None
```
</interfaces>

Investigation is DONE. Design is LOCKED (A1 + B1, user confirmed). Do not re-investigate, do not propose alternatives, do not expand scope.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Write-time mb_id dedup in artist_discovery_call_weekly + regression test</name>
  <files>
    app/services/discovery_service.py,
    tests/test_discovery_service.py
  </files>
  <behavior>
    - Test: `test_artist_discovery_call_weekly_dedupes_mb_id_across_vibes` (NEW, add to existing `tests/test_discovery_service.py`).
      Scenario: Mock the LLM batch call so that the SAME `mb_id` (e.g. "mbid-X") appears in `valid_picks` twice — once derived from vibe A with `llm_rank=5`, once from vibe B with `llm_rank=2`. Mock the candidate pool / `cand_index_by_mbid` so both picks pass the `valid_mbids` hallucination filter.
      Assertion: `_write_discovery_candidates_sync` is called with EXACTLY ONE record for `mb_id="mbid-X"`, and that record's `llm_rank == 2` (the lower / better rank wins). Unique mb_ids in the same batch are unaffected (a second unique mb_id with rank=7 must still be present).
    - Test: confirm tiebreak edge — if both copies have the same `llm_rank` (or both are `None`), the FIRST-encountered pick wins (stable iteration order). One pass with `[("X", None), ("X", None)]` keeps one row.
  </behavior>
  <action>
    Two edits, both in this task:

    **Edit A — `app/services/discovery_service.py:~1185`** (inside `artist_discovery_call_weekly`, AFTER the `valid_picks.append({...})` for-loop ends, BEFORE the `written = await asyncio.to_thread(_write_discovery_candidates_sync, valid_picks)` line at ~1187):

    Insert the following block exactly:

    ```python
            # QUICK FIX (260517-lyw): dedupe by mb_id BEFORE write. The same
            # artist can surface from multiple vibe-seed expansions because
            # the LLM picks once per vibe context. Without this, the writer
            # creates N duplicate DiscoveryCandidate rows for the same mb_id,
            # and /discover renders N identical cards firing identical
            # `hx-trigger="revealed once"` requests (ListenBrainz 429 cascade).
            #
            # Tiebreak rule (MUST match _dedupe_discovery_candidates_sync):
            #   1. Lowest COALESCE(llm_rank, 9999) wins.
            #   2. On a tie, stable iteration order wins (first pick kept).
            by_mbid: dict = {}
            for p in valid_picks:
                existing = by_mbid.get(p["mb_id"])
                if existing is None or (
                    (p.get("llm_rank") or 9999) < (existing.get("llm_rank") or 9999)
                ):
                    by_mbid[p["mb_id"]] = p
            valid_picks = list(by_mbid.values())
    ```

    Use 4-space indent consistent with the surrounding function body (which is itself inside the `try:` block — match the local indentation exactly; check the existing `valid_picks.append(...)` indent level and align to it).

    **Edit B — `tests/test_discovery_service.py`** (NEW test appended to end of file or grouped with related artist_discovery_call_weekly tests if any exist):

    Add `test_artist_discovery_call_weekly_dedupes_mb_id_across_vibes`. Mock the LLM call (whichever client `artist_discovery_call_weekly` invokes — patch at the call site within `app.services.discovery_service`) and the candidate-pool setup so that the function reaches the dedup block. Use `unittest.mock.patch` / `monkeypatch` consistent with how other tests in that file mock LLM calls. Use `_write_discovery_candidates_sync` patching (mock the bound name inside the module) to capture the records list. Assert the dedup invariant per the `<behavior>` block above.

    If the existing test file mocks differently, FOLLOW the existing pattern. Don't introduce a new mocking style.

    Do NOT change the writer (`_write_discovery_candidates_sync`). Do NOT add a UNIQUE constraint. Do NOT delete-then-insert. Dedup is purely in-memory at the call site.
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; python -m pytest tests/test_discovery_service.py::test_artist_discovery_call_weekly_dedupes_mb_id_across_vibes -xvs</automated>
  </verify>
  <done>
    - The new dedup block exists in `artist_discovery_call_weekly` at the correct location (between the `valid_picks.append(...)` for-loop and the `asyncio.to_thread(_write_discovery_candidates_sync, valid_picks)` call).
    - The block's tiebreak comment explicitly references `_dedupe_discovery_candidates_sync` so the migration's tiebreak stays in lockstep.
    - `test_artist_discovery_call_weekly_dedupes_mb_id_across_vibes` passes.
    - Full `tests/test_discovery_service.py` regression passes (no other tests break).
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: One-shot Phase 8.1 migration + lifespan wire-up + three migration tests</name>
  <files>
    app/services/discovery_service.py,
    app/main.py,
    tests/test_phase_08_1_discovery_dedupe_mb_id.py
  </files>
  <behavior>
    Three tests in the NEW file `tests/test_phase_08_1_discovery_dedupe_mb_id.py`, mirroring the structure of `tests/test_phase_08_discovery_bootstrap.py` exactly (same imports style, same `_run_async` helper at lines 192-197, same `fresh_db` fixture from `conftest.py:131`):

    1. `test_dedupe_keeps_lowest_llm_rank_and_stamps(fresh_db)`:
       - Pre-seed 4 `DiscoveryCandidate` rows for `mb_id="A"` with `llm_rank` values `[3, 1, 2, None]` (and `seed_track_id`/`seed_vibe_id` fixtures that pass FK constraints — create the parent Track + Vibe rows first OR use FK-free dummy values if the DB doesn't enforce FKs in this test setup; check what `tests/test_phase_08_discovery_bootstrap.py` does and mirror it).
       - Pre-seed 1 unique `DiscoveryCandidate` row for `mb_id="B"` with `llm_rank=5`.
       - Run `_run_async(run_phase_08_1_discovery_dedupe_mb_id())`.
       - Assert: total `DiscoveryCandidate` row count == 2.
       - Assert: the surviving row for `mb_id="A"` has `llm_rank == 1` (lowest, NOT the None row — `None` resolves to 9999 via `COALESCE`).
       - Assert: the surviving row for `mb_id="A"` retains its original `seed_vibe_id` (do NOT mutate it).
       - Assert: the row for `mb_id="B"` is untouched.
       - Assert: a `MigrationLog` row exists with `phase_id == "8.1-discovery-dedupe-mb-id"` and `completed_at is not None`.

    2. `test_dedupe_is_idempotent(fresh_db)`:
       - Same pre-seed as test 1.
       - Run the migration twice via `_run_async`.
       - Assert: second run is a no-op — row count after second run == row count after first run.
       - Assert: the `MigrationLog.completed_at` value is the SAME string after run 1 and run 2 (the upsert short-circuits because the gate is already stamped).

    3. `test_dedupe_handles_zero_duplicates(fresh_db)`:
       - Pre-seed 3 `DiscoveryCandidate` rows with three DISTINCT `mb_id` values (no duplicates).
       - Run the migration once.
       - Assert: all 3 rows still exist.
       - Assert: `MigrationLog(phase_id="8.1-discovery-dedupe-mb-id").completed_at is not None` so subsequent boots short-circuit.
  </behavior>
  <action>
    Three edits, all in this task:

    **Edit A — `app/services/discovery_service.py`** (THREE additions to this file):

    1. Near the existing `PHASE_08_MIGRATION_ID = "8.0-discovery-bootstrap"` constant (line ~40), add immediately below it:

       ```python
       PHASE_08_1_MIGRATION_ID = "8.1-discovery-dedupe-mb-id"
       ```

    2. Add a new sync helper `_dedupe_discovery_candidates_sync()` alongside the other `_bootstrap_*_sync` helpers (place it logically near `_write_discovery_candidates_sync` at line ~790, or near `_bootstrap_baseline_sync` at line ~128 — pick whichever grouping reads better; the existing bootstrap helpers cluster from ~line 128, so adding it next to those keeps the migration code together):

       ```python
       def _dedupe_discovery_candidates_sync() -> int:
           """Collapse duplicate DiscoveryCandidate rows by ``mb_id``.

           For each ``mb_id`` with count > 1, keep the row with the lowest
           ``llm_rank`` (NULLs treated as 9999 via COALESCE), ties broken by
           lowest ``id``. Delete all other rows for that ``mb_id``. Preserves
           ``seed_vibe_id`` on the winner row as-is — lowest rank IS the
           answer; don't second-guess which vibe "should" own the artist.

           Tiebreak rule (MUST match the in-memory dedup at
           ``artist_discovery_call_weekly:~1185``).

           Returns the total number of deleted rows.
           """
           from app.models.discovery import DiscoveryCandidate
           from sqlalchemy import func

           deleted_total = 0
           with Session(get_engine()) as session:
               # Find mb_ids with > 1 row.
               dup_mbids = [
                   row[0] for row in session.exec(
                       select(DiscoveryCandidate.mb_id)
                       .group_by(DiscoveryCandidate.mb_id)
                       .having(func.count(DiscoveryCandidate.id) > 1)
                   ).all()
               ]
               for mbid in dup_mbids:
                   rows = list(session.exec(
                       select(DiscoveryCandidate)
                       .where(DiscoveryCandidate.mb_id == mbid)
                   ).all())
                   # Winner: lowest COALESCE(llm_rank, 9999), ties → lowest id.
                   rows.sort(key=lambda r: ((r.llm_rank if r.llm_rank is not None else 9999), r.id))
                   winner = rows[0]
                   for loser in rows[1:]:
                       session.delete(loser)
                       deleted_total += 1
                   # winner is left untouched (including seed_vibe_id).
               if deleted_total > 0:
                   session.commit()
           return deleted_total
       ```

       Notes for the executor:
       - Use SQLModel ORM (matches the existing `_backfill_vibe_colors_sync` and `_bootstrap_baseline_sync` style — they use `Session(get_engine())` + `session.exec(select(...))`). Don't switch to raw SQL.
       - The `func` import from sqlalchemy may already exist at the top of the module — if so, drop the local import and use the module-level one. If not, the local import inside the function is fine.
       - Do NOT touch `seed_vibe_id` on the winner. The whole point is that lowest rank IS the answer.

    3. Add a new async migration function `run_phase_08_1_discovery_dedupe_mb_id()` immediately AFTER the existing `run_phase_08_discovery_bootstrap()` function (currently ending at line 304):

       ```python
       async def run_phase_08_1_discovery_dedupe_mb_id() -> None:
           """Lifespan one-shot. Gated by ``MigrationLog(phase_id='8.1-discovery-dedupe-mb-id')``.

           Collapses pre-existing duplicate :class:`DiscoveryCandidate` rows
           that accumulated before the write-time dedup in
           :func:`artist_discovery_call_weekly` was added. Mirrors the shape
           of :func:`run_phase_08_discovery_bootstrap` — same gate pattern,
           same failure semantics (leaves ``completed_at`` NULL on exception
           so the next restart retries).
           """
           existing = await asyncio.to_thread(
               _read_migration_log_sync, PHASE_08_1_MIGRATION_ID,
           )
           if existing is not None and existing.completed_at is not None:
               logger.info(
                   "Phase 8.1 dedupe migration already complete "
                   "(completed_at=%s); skipping.",
                   existing.completed_at,
               )
               return

           # In-flight marker — next restart retries on failure.
           await asyncio.to_thread(
               _upsert_migration_log_sync, PHASE_08_1_MIGRATION_ID, None,
           )

           try:
               deleted = await asyncio.to_thread(_dedupe_discovery_candidates_sync)
           except Exception:
               logger.exception(
                   "Phase 8.1 dedupe migration failed; "
                   "will retry on next restart."
               )
               return

           await asyncio.to_thread(
               _upsert_migration_log_sync,
               PHASE_08_1_MIGRATION_ID,
               datetime.now(timezone.utc).isoformat(),
           )
           logger.info(
               "Phase 8.1 dedupe migration complete: "
               "deleted %d duplicate DiscoveryCandidate rows.",
               deleted,
           )
       ```

    **Edit B — `app/main.py:~267`** (between the existing Phase 8 bootstrap call at line 266 and `get_event_bus()` at line 268):

    Insert two lines exactly:

    ```python
        # QUICK FIX (260517-lyw): one-shot collapse of pre-existing duplicate
        # DiscoveryCandidate rows (same mb_id, multiple rows from cross-vibe
        # LLM picks before write-time dedup was added). Idempotent via
        # MigrationLog gate (phase_id='8.1-discovery-dedupe-mb-id').
        from app.services.discovery_service import run_phase_08_1_discovery_dedupe_mb_id
        await run_phase_08_1_discovery_dedupe_mb_id()
    ```

    Ordering MUST be: `run_phase_08_discovery_bootstrap` → `run_phase_08_1_discovery_dedupe_mb_id` → `get_event_bus()`. Match the existing 4-space indent inside `lifespan`.

    **Edit C — `tests/test_phase_08_1_discovery_dedupe_mb_id.py`** (NEW file):

    Create the file. Mirror the header docstring style, imports, and `_run_async` helper from `tests/test_phase_08_discovery_bootstrap.py`. Implement the three tests per the `<behavior>` block above.

    Suggested skeleton (executor: fill in the assertions per the behavior block):

    ```python
    """Phase 8.1 quick fix — one-shot DiscoveryCandidate mb_id dedup migration tests.

    Mirrors tests/test_phase_08_discovery_bootstrap.py structure.
    """
    from __future__ import annotations

    import asyncio
    from datetime import datetime, timezone

    from sqlmodel import select

    from app.models.discovery import DiscoveryCandidate
    from app.models.vibe import MigrationLog
    from app.services.discovery_service import (
        PHASE_08_1_MIGRATION_ID,
        run_phase_08_1_discovery_dedupe_mb_id,
    )


    def _run_async(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


    # ... three tests here ...
    ```

    For pre-seeding rows that have FK references to Track/Vibe, check what `tests/test_phase_08_discovery_bootstrap.py` does (it creates Vibe rows directly via `fresh_db.add(Vibe(...))` — same pattern works for Track if needed). If SQLite FK enforcement is OFF in the test DB (PRAGMA foreign_keys = OFF is SQLite default), the executor MAY skip parent-row creation and use bare integer FKs — but only if the existing bootstrap tests confirm that's the convention.
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; python -m pytest tests/test_phase_08_1_discovery_dedupe_mb_id.py tests/test_phase_08_discovery_bootstrap.py -xvs</automated>
  </verify>
  <done>
    - `PHASE_08_1_MIGRATION_ID = "8.1-discovery-dedupe-mb-id"` constant exists in `app/services/discovery_service.py` near `PHASE_08_MIGRATION_ID`.
    - `_dedupe_discovery_candidates_sync()` function exists and implements lowest-rank-wins with id-tiebreak.
    - `run_phase_08_1_discovery_dedupe_mb_id()` function exists with the gate/in-flight/try/except/stamp pattern matching `run_phase_08_discovery_bootstrap`.
    - `app/main.py` lifespan awaits the new migration AFTER `run_phase_08_discovery_bootstrap` and BEFORE `get_event_bus()`.
    - All three tests in `tests/test_phase_08_1_discovery_dedupe_mb_id.py` pass.
    - `tests/test_phase_08_discovery_bootstrap.py` still passes (lifespan ordering didn't break the existing bootstrap).
  </done>
</task>

<task type="auto">
  <name>Task 3: Full-suite gate + scope diff check</name>
  <files>
    (no new files — verification-only task)
  </files>
  <action>
    Run the verification gates exactly as specified in the task_context. This task exists to enforce them; if any gate fails, fix the underlying issue in Task 1 or Task 2 and re-run.

    1. **Scope check** — confirm the diff touches ONLY the four expected files:

       ```bash
       git diff --stat HEAD | grep -E "discovery_service\.py|app/main\.py|test_phase_08_1_discovery_dedupe_mb_id\.py|test_discovery_service\.py"
       git diff --stat HEAD | tail -1   # confirm "4 files changed" line
       ```

       Expected files (exactly these four, no others):
       - `app/services/discovery_service.py`
       - `app/main.py`
       - `tests/test_phase_08_1_discovery_dedupe_mb_id.py` (new)
       - `tests/test_discovery_service.py`

       If anything else changed (especially `app/templates/partials/discover_artist_card.html`, `app/models/discovery.py`, or any migration .sql file), revert it — those are explicit OUT-OF-SCOPE files per the task context.

    2. **Regression suite** — confirm no collateral damage:

       ```bash
       python -m pytest tests/test_discovery_service.py tests/test_phase_08_discovery_bootstrap.py tests/test_phase_08_1_discovery_dedupe_mb_id.py -x
       ```

       All tests in all three files must pass.

    3. **Manual diff scan** — re-read the two source-file diffs end-to-end. Confirm:
       - The in-memory dedup tiebreak comment in `discovery_service.py:~1185` mentions `_dedupe_discovery_candidates_sync`.
       - The SQL/ORM tiebreak comment in `_dedupe_discovery_candidates_sync` mentions the in-memory dedup at `artist_discovery_call_weekly`.
       - Both tiebreaks use the same rule: lowest `COALESCE(llm_rank, 9999)`, ties → lowest `id` (or stable iteration order for in-memory).
       - The winner row's `seed_vibe_id` is NEVER mutated in the migration.

    4. **Static grep gates** — these must all match exactly (one occurrence each, filter comments first per the planner's "self-invalidating grep gate" rule):

       ```bash
       # New constant exists in discovery_service.py
       grep -v '^#' app/services/discovery_service.py | grep -c 'PHASE_08_1_MIGRATION_ID = "8.1-discovery-dedupe-mb-id"'
       #  -> must be 1

       # New migration is awaited in lifespan
       grep -v '^#' app/main.py | grep -c 'await run_phase_08_1_discovery_dedupe_mb_id'
       #  -> must be 1

       # Write-time dedup block exists
       grep -c 'by_mbid' app/services/discovery_service.py
       #  -> must be >= 4 (dict init + assignment + list build + comment refs)
       ```
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; python -m pytest tests/test_discovery_service.py tests/test_phase_08_discovery_bootstrap.py tests/test_phase_08_1_discovery_dedupe_mb_id.py -x</automated>
  </verify>
  <done>
    - `git diff --stat HEAD` lists EXACTLY 4 files, all in the expected set.
    - No out-of-scope files (template, model, .sql migration) appear in the diff.
    - All tests in the three target test files pass with no failures.
    - The two tiebreak comments cross-reference each other so future edits stay in sync.
  </done>
</task>

</tasks>

<verification>
End-of-plan verification gates (in order):

1. `git diff --stat HEAD` shows EXACTLY: `app/services/discovery_service.py`, `app/main.py`, `tests/test_phase_08_1_discovery_dedupe_mb_id.py` (new), `tests/test_discovery_service.py`. Nothing else.
2. `python -m pytest tests/test_discovery_service.py tests/test_phase_08_discovery_bootstrap.py tests/test_phase_08_1_discovery_dedupe_mb_id.py -x` passes 100%.
3. The new write-time dedup regression test (Task 1) and three new migration tests (Task 2) all pass.
4. The pre-existing `tests/test_phase_08_discovery_bootstrap.py` still passes (proves the lifespan ordering change didn't break the Phase 8 bootstrap gate).
5. Tiebreak rule comments in both code locations cross-reference each other (manual diff scan).
</verification>

<success_criteria>
- After deploy to NAS: on the next container start, `run_phase_08_1_discovery_dedupe_mb_id` runs once, logs `"Phase 8.1 dedupe migration complete: deleted N duplicate DiscoveryCandidate rows."`, and stamps the gate.
- On every subsequent boot, the migration short-circuits with `"Phase 8.1 dedupe migration already complete (completed_at=...); skipping."`.
- The next weekly discovery cron run writes deduped candidates only — no new duplicate rows form because `valid_picks` is collapsed before `_write_discovery_candidates_sync`.
- `/discover` page renders each artist (by `mb_id`) AT MOST ONCE — visually confirmed by the user on NAS UAT.
- The ListenBrainz HTTP 429 cascade observed in NAS logs 2026-05-17 15:06:55–15:07:03 does not recur.
- Diff is contained to 4 files. No schema changes, no UNIQUE constraint, no template edits.
</success_criteria>

<output>
After completion, create `.planning/quick/260517-lyw-fix-discovery-dedupe-artist-candidates-b/260517-lyw-SUMMARY.md` per the standard quick-task summary template.
</output>
