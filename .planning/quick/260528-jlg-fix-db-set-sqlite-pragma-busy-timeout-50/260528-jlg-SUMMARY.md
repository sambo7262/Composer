---
status: complete
quick_id: 260528-jlg
completed_at: 2026-05-28
---

# Quick Task 260528-jlg: fix(db) — SQLite PRAGMA busy_timeout=5000

## One-liner
Added `PRAGMA busy_timeout=5000` to the existing pragma listener in `app/database.py`. SQLite writers now wait up to 5s for a contended lock instead of failing instantly with SQLITE_BUSY. Eliminates intermittent "database is locked" errors during the Sunday weekly maintenance tick.

## Why
NAS UAT 2026-05-28: Sunday 03:00 UTC weekly maintenance hit "database/table is locked", required manual re-run. Root cause: WAL mode (already enabled at line 32) separates reader/writer locks but writer-vs-writer contention still fails. Without busy_timeout, SQLite returns SQLITE_BUSY immediately. A concurrent webhook write hitting the DB during the multi-step weekly tick (prune → discovery) was instantly losing the race.

## Changes
- `app/database.py` (+1 line) — `cursor.execute("PRAGMA busy_timeout=5000")` between existing `journal_mode=WAL` and `foreign_keys=ON` calls. Pragma listener fires on every new connection, so the timeout applies to all engine paths (pooled + raw).
- `tests/test_database.py` (+1 test, ~14 lines) — `test_busy_timeout_pragma_is_set_to_5000` mirrors the existing `test_foreign_keys_enabled` pattern.

## Commit
- `906c704` fix(260528-jlg): set SQLite PRAGMA busy_timeout=5000 to eliminate concurrent-write lock errors

## Verification
- 5/5 pass in `tests/test_database.py` (4 existing + 1 new)
- 60/61 pass in `tests/test_suggestions_service.py` + `tests/test_event_handlers.py` — one failure is the pre-existing stochastic flake `test_picks_proportional_to_vibe_share` documented in 260517-nkt's SUMMARY (not a regression)

## Files modified
- `app/database.py`
- `tests/test_database.py`
