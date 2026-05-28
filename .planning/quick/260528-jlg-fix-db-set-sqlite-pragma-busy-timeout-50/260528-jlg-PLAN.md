---
phase: 260528-jlg
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/database.py
  - tests/test_database.py
autonomous: true
requirements:
  - QUICK-260528-JLG
must_haves:
  truths:
    - "Every SQLite connection has busy_timeout=5000ms set"
    - "Writer contention waits up to 5s instead of returning SQLITE_BUSY immediately"
    - "Existing WAL + foreign_keys pragmas remain set"
  artifacts:
    - path: "app/database.py"
      provides: "set_sqlite_pragma event listener now issues PRAGMA busy_timeout=5000"
      contains: "busy_timeout=5000"
    - path: "tests/test_database.py"
      provides: "Regression test asserting busy_timeout pragma value"
      contains: "test_busy_timeout_pragma_is_set_to_5000"
  key_links:
    - from: "app/database.py:set_sqlite_pragma"
      to: "every pooled SQLite connection"
      via: "SQLAlchemy 'connect' event listener"
      pattern: "PRAGMA busy_timeout=5000"
---

<objective>
Set `PRAGMA busy_timeout=5000` on every SQLite connection so writer contention
waits up to 5s for the lock instead of immediately returning SQLITE_BUSY.
Resolves the Sunday weekly-maintenance "database is locked" error reported on
the NAS.

Purpose: WAL mode is already enabled but `busy_timeout` defaults to 0, so any
two writers racing during the weekly cron return SQLITE_BUSY at once.
Output: One added pragma line + one regression test.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@app/database.py
@tests/test_database.py
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add busy_timeout pragma + regression test</name>
  <files>app/database.py, tests/test_database.py</files>
  <behavior>
    - After engine init, querying `PRAGMA busy_timeout` returns 5000.
    - Existing journal_mode=WAL and foreign_keys=ON assertions still pass
      (the two existing tests `test_wal_mode_active` and
      `test_foreign_keys_enabled` must remain green).
  </behavior>
  <action>
    Step 1 (RED — write test first):
    Append to `tests/test_database.py`:
    ```python
    def test_busy_timeout_pragma_is_set_to_5000(test_engine):
        """SQLite busy_timeout is set to 5000ms on every connection.

        Without busy_timeout, writer contention returns SQLITE_BUSY
        immediately. With 5000, the loser waits up to 5s for the lock.
        Fixes the Sunday weekly-maintenance "database is locked" error.
        """
        with test_engine.connect() as conn:
            result = conn.execute(text("PRAGMA busy_timeout"))
            timeout = result.scalar()

        assert timeout == 5000, f"expected busy_timeout=5000, got {timeout!r}"
    ```
    Run `pytest tests/test_database.py::test_busy_timeout_pragma_is_set_to_5000 -x`
    and confirm it FAILS (returns 0, the SQLite default).

    Step 2 (GREEN — minimal production change):
    In `app/database.py`, inside the `set_sqlite_pragma` event listener
    (currently lines 28-34), add EXACTLY ONE line between the existing
    `journal_mode=WAL` and `foreign_keys=ON` calls:
    ```python
    cursor.execute("PRAGMA busy_timeout=5000")  # 5s — waits for lock before erroring
    ```
    The listener should read:
    ```python
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")  # 5s — waits for lock before erroring
    cursor.execute("PRAGMA foreign_keys=ON")
    ```
    Do NOT touch any other code in the file. Do NOT modify `_migrate_add_columns`,
    `init_db`, or the migration block.

    Step 3 (verify GREEN):
    Re-run the new test — it must pass. Then run the surrounding pragma tests
    to confirm no regression.
  </action>
  <verify>
    <automated>pytest tests/test_database.py::test_busy_timeout_pragma_is_set_to_5000 tests/test_database.py::test_wal_mode_active tests/test_database.py::test_foreign_keys_enabled tests/test_database.py::test_init_db_creates_tables -x</automated>
  </verify>
  <done>
    - `tests/test_database.py::test_busy_timeout_pragma_is_set_to_5000` passes.
    - `test_wal_mode_active`, `test_foreign_keys_enabled`,
      `test_init_db_creates_tables` still pass (no regression).
    - `git diff --stat HEAD` shows ONLY `app/database.py` (+1 line) and
      `tests/test_database.py` (+~15 lines for the new test).
    - No other files modified.
  </done>
</task>

</tasks>

<verification>
- `pytest tests/test_database.py -x` — full file green (4 prior + 1 new test).
- Sample regression sanity check: `pytest tests/test_suggestions_service.py -x`
  passes (any DB-using test confirms the pragma listener didn't break engine
  init).
- `git diff --stat HEAD` lists exactly 2 files.
- `grep -n "busy_timeout" app/database.py` shows exactly one occurrence inside
  the `set_sqlite_pragma` listener.
</verification>

<success_criteria>
- All four assertions in <verification> hold.
- Production change is exactly one line: `cursor.execute("PRAGMA busy_timeout=5000")`.
- No changes to migration logic, model registration, or `init_db()` flow.
</success_criteria>

<output>
After completion, create `.planning/quick/260528-jlg-fix-db-set-sqlite-pragma-busy-timeout-50/260528-jlg-01-SUMMARY.md`
</output>
