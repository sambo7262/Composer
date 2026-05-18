---
phase: 260517-nkt-fix-suggestions-discovery-pushes-to-plex
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/suggestions_service.py
  - app/services/suggestions_discovery.py
  - tests/test_suggestions_service.py
  - tests/test_suggestions_discovery.py
autonomous: true
requirements:
  - BUG-A-discovery-no-plex-push
  - BUG-B-mirror-cap-regression
tags:
  - suggestions
  - discovery
  - plex
  - regression-fix

must_haves:
  truths:
    - "Discovery cron writes new picks into SuggestionsMirror AND pushes them to the Plex `Composer · Suggestions` playlist (Bug A fix)."
    - "After every discovery run, the mirror row count is <= SUGGESTIONS_TARGET_SIZE (30); discovery never inflates the mirror above target (Bug B fix)."
    - "When a discovery write would push the mirror above target, the oldest rows (by added_at ASC, id ASC) are evicted from SuggestionsMirror AND removed from the Plex playlist before/around the add."
    - "Self-heal works: if Plex returns NotFound on the discovery push, `_materialize_suggestions_plex_playlist` is invoked with the full current mirror set (mirrors commit 719c6b7 / refill_mirror_sql:1036-1043 idiom EXACTLY)."
    - "When no `ManagedPlaylist(kind='suggestions')` row exists, discovery logs a warning and skips the Plex push without raising (matches refill_mirror_sql:1004-1008 behavior)."
    - "`maybe_schedule_refill`'s deficit gate is UNCHANGED; once mirror cap holds, the play-driven sql_refill path becomes meaningful again (no regression in refill_mirror_sql)."
    - "All Plex calls inside the new async helpers are wrapped in `asyncio.to_thread` (Phase 5 Convention #1 — enforced by existing static AST test)."
  artifacts:
    - path: "app/services/suggestions_service.py"
      provides: "Two new exports: `_evict_oldest_mirror_rows_sync(evict_count) -> list[str]` (sync) and `async def remove_tracks_from_suggestions_playlist(plex_url, plex_token, mp_rating_key, rating_keys_to_remove) -> int`"
      contains: "_evict_oldest_mirror_rows_sync"
    - path: "app/services/suggestions_discovery.py"
      provides: "discovery_call_weekly now caps mirror BEFORE write and pushes new picks to Plex AFTER write, with optional removal of evicted rating keys"
      contains: "remove_tracks_from_suggestions_playlist"
    - path: "tests/test_suggestions_service.py"
      provides: "3 new tests for `_evict_oldest_mirror_rows_sync` (noop, ordering, returned keys)"
    - path: "tests/test_suggestions_discovery.py"
      provides: "6 new tests for discovery → Plex push + cap behavior (push, evict-at-target, evict-overflow, no-evict-below-target, NotFound self-heal, no-managed-playlist skip)"
  key_links:
    - from: "app/services/suggestions_discovery.py::discovery_call_weekly"
      to: "app/services/suggestions_service.py::_evict_oldest_mirror_rows_sync"
      via: "asyncio.to_thread(_evict_oldest_mirror_rows_sync, evict_count) BEFORE _write_discovery_picks_sync"
      pattern: "asyncio\\.to_thread\\(\\s*_evict_oldest_mirror_rows_sync"
    - from: "app/services/suggestions_discovery.py::discovery_call_weekly"
      to: "app/services/plex_playlist_service.py::update_playlist_items"
      via: "await update_playlist_items(...) AFTER _write_discovery_picks_sync, ADD-first (additive, new picks only)"
      pattern: "await\\s+update_playlist_items\\("
    - from: "app/services/suggestions_discovery.py::discovery_call_weekly"
      to: "app/services/suggestions_service.py::remove_tracks_from_suggestions_playlist"
      via: "await remove_tracks_from_suggestions_playlist(...) AFTER successful add, wrapped in its own try/except (removal failure must not undo the add — weekly prune reconciles)"
      pattern: "await\\s+remove_tracks_from_suggestions_playlist\\("
    - from: "app/services/suggestions_service.py::remove_tracks_from_suggestions_playlist"
      to: "plexapi.PlexServer.fetchItem().removeItems()"
      via: "asyncio.to_thread(_remove_items_sync) — Phase 5 Convention #1; pre-flight `is_managed_playlist(mp_rating_key)` guard (OPS-06 / Pitfall 20)"
      pattern: "asyncio\\.to_thread\\("
---

<objective>
Fix two compounding regressions in the suggestions refill chain:

- **Bug A (discovery → no Plex push):** `discovery_call_weekly` writes picks into `SuggestionsMirror` but never pushes them to the Plex `Composer · Suggestions` playlist. The user's Plex playlist drifts increasingly stale relative to the DB.
- **Bug B (mirror cap missing):** with Bug A pumping uncapped picks into the mirror, the mirror count exceeds `SUGGESTIONS_TARGET_SIZE=30`, which makes `maybe_schedule_refill`'s `deficit = max(0, target - current)` resolve to 0 on every play, so `sql_refill` (the play-driven refill) never fires.

**Locked design (user confirmed A1 + B1):**
- A1 — mirror `refill_mirror_sql`'s Plex-push block (suggestions_service.py:1001-1075) inside `discovery_call_weekly` AFTER `_write_discovery_picks_sync`.
- B1 — BEFORE `_write_discovery_picks_sync`, evict oldest mirror rows so post-write count stays at `SUGGESTIONS_TARGET_SIZE`. Remove the same evicted rating keys from Plex AFTER the add succeeds.

Purpose: restore the play-driven refill loop. Once the mirror holds at 30, `maybe_schedule_refill`'s deficit gate becomes meaningful again on every play, sql_refill fires, and the Plex playlist refreshes with fresh picks on the user's listening cadence.

Output:
- `app/services/suggestions_service.py` — two new helpers (`_evict_oldest_mirror_rows_sync`, `remove_tracks_from_suggestions_playlist`).
- `app/services/suggestions_discovery.py` — `discovery_call_weekly` wraps `_write_discovery_picks_sync` with cap-before-write + Plex push after.
- 9 new tests (3 unit + 6 integration), zero regressions in adjacent test modules.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.planning/STATE.md

<!-- Source files — focused line ranges only. -->
@app/services/suggestions_service.py  <!-- Canonical Plex-push idiom: lines 1001-1075. Existing helpers to reuse: _find_suggestions_managed_playlist_sync (line 111), _read_plex_creds_sync (line 503), _count_mirror_rows_sync (line 155), _materialize_suggestions_plex_playlist (line 240). Constant: SUGGESTIONS_TARGET_SIZE = 30 (line 75). DO NOT modify refill_mirror_sql or maybe_schedule_refill. -->
@app/services/suggestions_discovery.py  <!-- _write_discovery_picks_sync at line 510; discovery_call_weekly at line 601. Wrap the existing _write_discovery_picks_sync call (line ~902-908) with cap-before + Plex-push-after; do NOT modify _write_discovery_picks_sync itself. -->
@app/services/plex_playlist_service.py  <!-- update_playlist_items (line 227, additive semantics, is_managed_playlist guard at line 246) — call as the ADD path. _remove_items pattern (line 568-574) — mirror this for the new removal helper. prune_suggestions_playlist_to_mirror (line 565-633) — has the 260517-l84 self-heal pattern; reference but DO NOT modify. -->
@app/models/suggestions.py  <!-- SuggestionsMirror schema. UNIQUE(track_id) at line 41. added_at is ISO 8601 string (line 43) — lexicographic ORDER BY is correct because all writers use datetime.now(timezone.utc).isoformat(). -->

<interfaces>
<!-- Key signatures the executor will use directly — no codebase exploration needed. -->

From app/services/suggestions_service.py (existing — DO NOT modify, just reuse):
```python
SUGGESTIONS_TARGET_SIZE = 30  # line 75

def _find_suggestions_managed_playlist_sync() -> Optional[ManagedPlaylist]: ...  # line 111
def _count_mirror_rows_sync() -> int: ...  # line 155
def _read_plex_creds_sync() -> Tuple[str, str]: ...  # line 503
async def _materialize_suggestions_plex_playlist(
    plex_url: str, plex_token: str, seed_rating_keys: List[str],
) -> Optional[str]: ...  # line 240
```

From app/services/plex_playlist_service.py (existing — DO NOT modify, just call):
```python
async def update_playlist_items(
    plex_url: str, plex_token: str,
    playlist_rating_key: str, desired_rating_keys: List[str],
) -> ReconcileResult: ...  # line 227 — ADDITIVE, raises PermissionError if not managed
def is_managed_playlist(playlist_rating_key: str) -> bool: ...
```

From app/models/suggestions.py (read-only):
```python
class SuggestionsMirror(SQLModel, table=True):
    id: Optional[int]
    track_id: int  # UNIQUE — FK to track.id
    position: int
    added_at: str   # ISO 8601 UTC, lexicographically sortable
    # ... rationale, vibe_id, score
```

From app/models/track.py (Track.plex_rating_key is a string column on the `track` table).

New signatures this plan adds to app/services/suggestions_service.py:
```python
def _evict_oldest_mirror_rows_sync(evict_count: int) -> list[str]:
    """Return list of evicted rows' Track.plex_rating_key, oldest-first."""

async def remove_tracks_from_suggestions_playlist(
    plex_url: str,
    plex_token: str,
    mp_rating_key: str,
    rating_keys_to_remove: list[str],
) -> int:
    """Remove rating keys from a Composer-managed playlist. Returns count removed."""
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add `_evict_oldest_mirror_rows_sync` + 3 unit tests</name>
  <files>app/services/suggestions_service.py, tests/test_suggestions_service.py</files>
  <behavior>
    Tests added to tests/test_suggestions_service.py (use existing `db_phase7` fixture pattern — see how `_count_mirror_rows_sync` is exercised elsewhere in this file):

    - `test_evict_oldest_zero_or_negative_count_is_noop` — seed 3 mirror rows; call `_evict_oldest_mirror_rows_sync(0)` and `_evict_oldest_mirror_rows_sync(-5)`; assert `_count_mirror_rows_sync() == 3` after each call and the returned list is `[]`.
    - `test_evict_oldest_orders_by_added_at_then_id` — seed 5 mirror rows with INTENTIONALLY mixed insertion order vs. `added_at` (e.g., insert in id order 1..5 but stamp `added_at` like `"2025-01-05"`, `"2025-01-01"`, `"2025-01-03"`, `"2025-01-02"`, `"2025-01-04"`); call with `evict_count=3`; assert the 2 surviving rows have the 2 newest `added_at` values (`"2025-01-05"` and `"2025-01-04"`). Tie-breaker on `added_at` is `id ASC` — exercise that path by adding two rows sharing one `added_at` and assert deterministic eviction of the lower id first.
    - `test_evict_oldest_returns_plex_rating_keys_of_evicted_rows` — seed 4 Track rows with known `plex_rating_key` strings (e.g., `"RK_A"`, `"RK_B"`, `"RK_C"`, `"RK_D"`) and 4 mirror rows pointing at them with stamped `added_at` ascending; call with `evict_count=2`; assert returned list equals `["RK_A", "RK_B"]` (the 2 oldest, in oldest-first order).
  </behavior>
  <action>
Add `_evict_oldest_mirror_rows_sync(evict_count: int) -> list[str]` near `_count_mirror_rows_sync` (~line 155 in app/services/suggestions_service.py — keep it co-located with the other mirror helpers).

Implementation contract (executor MUST honor):

1. Signature: `def _evict_oldest_mirror_rows_sync(evict_count: int) -> list[str]:` — pure sync; caller wraps in `asyncio.to_thread`.
2. Guard: if `evict_count <= 0`, return `[]` immediately (no DB session opened).
3. Open `Session(get_engine())` (mirror `_count_mirror_rows_sync` and `_delete_mirror_row_sync` patterns above).
4. SELECT first to capture keys, DELETE second (two statements in the same transaction, one commit):
   - `SELECT t.plex_rating_key FROM suggestionsmirror sm JOIN track t ON sm.track_id = t.id ORDER BY sm.added_at ASC, sm.id ASC LIMIT :n` — bind `n=evict_count`.
   - Materialize results into `evicted_keys: list[str]` preserving query order (so the returned list is deterministically oldest-first).
   - `DELETE FROM suggestionsmirror WHERE id IN (SELECT id FROM suggestionsmirror ORDER BY added_at ASC, id ASC LIMIT :n)` — same `n`. Use the subquery form so SQLite's ORDER BY + LIMIT semantics on DELETE are well-defined.
5. `session.commit()`; return `evicted_keys`.

Use `sqlalchemy.text(...)` for the raw SQL (same convention as `_delete_mirror_row_sync` and `_write_discovery_picks_sync`).

Docstring requirements (verbatim concepts — wording is yours):
- "Evict the oldest `evict_count` rows from SuggestionsMirror; return the corresponding Track.plex_rating_key values oldest-first so the caller can remove them from the Plex playlist."
- "Ordering: ORDER BY sm.added_at ASC, sm.id ASC. `added_at` is an ISO 8601 UTC string (model line 43), all writers use `datetime.now(timezone.utc).isoformat()`, so lexicographic comparison is correct — DO NOT cast to datetime."
- "Concurrency note: this helper plus `_write_discovery_picks_sync` run in two separate transactions, which is acceptable because discovery is the sole writer to SuggestionsMirror under the weekly cron path (refill_mirror_sql is the other writer, but it only fires on play, and the weekly cron runs at a quiet hour). UNIQUE(track_id) at model line 41 makes any accidental collision a hard error, not silent corruption."

Then add the three tests described in `<behavior>` to tests/test_suggestions_service.py. Seed data using the existing fixture and helper patterns already in that file — DO NOT introduce new fixture infrastructure. Reuse the `Session(get_engine())` + `session.add(...)` + `session.commit()` pattern visible elsewhere in this test file (see how `SuggestionsMirror` and `Track` are seeded in adjacent test functions).

RED-GREEN: write all 3 tests FIRST, confirm they fail with `AttributeError`/`ImportError` (helper does not exist), then add the helper, then confirm tests pass.
  </action>
  <verify>
<automated>cd /Users/Oreo/Projects/Composer && python -m pytest tests/test_suggestions_service.py -k "evict_oldest" -x -v</automated>
  </verify>
  <done>
- `_evict_oldest_mirror_rows_sync` exists in `app/services/suggestions_service.py` with the contract above (docstring, guard, SELECT-then-DELETE, commit, oldest-first return).
- 3 new tests in `tests/test_suggestions_service.py` all pass.
- `python -m pytest tests/test_suggestions_service.py -x` (full module) shows no new failures versus baseline (i.e., no regression in existing tests).
- `grep -n "SUGGESTIONS_TARGET_SIZE" app/services/suggestions_service.py` confirms the constant is unchanged (still `= 30` at line 75) — this task touches helpers, not the constant.
- `grep -nE "def (maybe_schedule_refill|refill_mirror_sql|prune_suggestions_playlist_to_mirror)" app/services/suggestions_service.py` shows those functions exist and are byte-identical to baseline (use `git diff` to confirm).
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Add `remove_tracks_from_suggestions_playlist` (async Plex helper)</name>
  <files>app/services/suggestions_service.py, tests/test_suggestions_service.py</files>
  <behavior>
    Tests added to tests/test_suggestions_service.py (use the existing monkeypatch/mock patterns for PlexServer that this file already uses — search the file for `PlexServer` / `monkeypatch` / `MagicMock` to find the established stub style):

    - `test_remove_tracks_empty_list_returns_zero_no_plex_call` — call `await remove_tracks_from_suggestions_playlist(url, token, "12345", [])`; assert returned `int == 0` AND no `PlexServer` instance was constructed (mock spy on the import).
    - `test_remove_tracks_raises_permission_error_when_not_managed` — monkeypatch `is_managed_playlist` to return False; call with non-empty list; assert `PermissionError` is raised AND no `PlexServer` was constructed (guard fires before any Plex call).
    - `test_remove_tracks_calls_removeItems_via_to_thread` — monkeypatch `is_managed_playlist` to True; monkeypatch `PlexServer` to a stub whose `fetchItem` returns a stub playlist with `items()` and `removeItems()`; assert `removeItems` called once with a list of items matching the rating keys passed in, and the helper returns the post-removal item count.
  </behavior>
  <action>
Add `async def remove_tracks_from_suggestions_playlist(...)` immediately after `_materialize_suggestions_plex_playlist` (~line 240 in app/services/suggestions_service.py — keeps the async Plex helpers grouped).

Signature:
```python
async def remove_tracks_from_suggestions_playlist(
    plex_url: str,
    plex_token: str,
    mp_rating_key: str,
    rating_keys_to_remove: list[str],
) -> int:
```

Implementation contract:

1. **Early return**: if `not rating_keys_to_remove`, return `0` immediately — do NOT construct PlexServer.
2. **Pre-flight guard** (mirrors `update_playlist_items:246` EXACTLY):
   ```python
   from app.services.plex_playlist_service import is_managed_playlist
   if not is_managed_playlist(mp_rating_key):
       raise PermissionError(
           f"OPS-06 / Pitfall 20: playlist {mp_rating_key} is not "
           f"Composer-managed (no ManagedPlaylist row); refusing to mutate."
       )
   ```
3. **Build the sync remove function** (mirrors `plex_playlist_service.py` `_remove_items` at line 568-574 EXACTLY):
   ```python
   from plexapi.server import PlexServer
   playlist_key_int = int(mp_rating_key)  # avoid PlexAPI URL-concat bug — same fix as update_playlist_items:260

   def _remove_items_sync() -> int:
       plex = PlexServer(plex_url, plex_token, timeout=30)
       playlist = plex.fetchItem(playlist_key_int)
       items = [plex.fetchItem(int(k)) for k in rating_keys_to_remove]
       playlist.removeItems(items)
       return len(playlist.items())
   ```
4. **Dispatch via `asyncio.to_thread`** (Phase 5 Convention #1):
   ```python
   return await asyncio.to_thread(_remove_items_sync)
   ```
5. **NotFound handling is the caller's concern** — DO NOT catch here. The caller (discovery_call_weekly in Task 3) has its own self-heal path that re-materializes the playlist; swallowing NotFound here would hide the signal.

Docstring requirements:
- "Remove rating keys from the Composer-managed Suggestions Plex playlist. Returns the playlist's item count AFTER removal."
- "Pre-flight `is_managed_playlist(mp_rating_key)` (OPS-06 / Pitfall 20) — raises PermissionError if False. Returns 0 immediately for an empty `rating_keys_to_remove` list."
- "All PlexAPI calls dispatched via `asyncio.to_thread` per Phase 5 Convention #1. NotFound on the playlist is intentionally NOT caught — the caller owns self-heal."
- "Helper exists as a counterpart to `update_playlist_items` (additive) so discovery's evict-then-add flow can prune the Plex playlist symmetrically with the SuggestionsMirror cap."

RED-GREEN: write all 3 tests FIRST, confirm they fail (`ImportError` / `AttributeError`), then add the helper, confirm tests pass.

Use the EXACT same `PlexServer(plex_url, plex_token, timeout=30)` + `int(playlist_rating_key)` constructor + `playlist.fetchItem(int(k))` per-item loop pattern as `plex_playlist_service.py:262-265, 567-573` so the static AST test catches drift uniformly.
  </action>
  <verify>
<automated>cd /Users/Oreo/Projects/Composer && python -m pytest tests/test_suggestions_service.py -k "remove_tracks" -x -v && python -m pytest tests/test_event_handlers.py::test_no_blocking_plexapi_in_async -x -v</automated>
  </verify>
  <done>
- `remove_tracks_from_suggestions_playlist` exists in `app/services/suggestions_service.py` with the contract above (empty-list early return, `is_managed_playlist` guard, `asyncio.to_thread` wrap of the PlexServer interaction).
- 3 new `test_remove_tracks_*` tests pass.
- The Phase 5 Convention #1 static AST test (`tests/test_event_handlers.py::test_no_blocking_plexapi_in_async`) STILL passes — confirms no naked `playlist.removeItems(...)` inside any `async def` (the new helper goes through `asyncio.to_thread`).
- `python -m pytest tests/test_suggestions_service.py -x` (full module) shows no new failures.
- `git diff app/services/suggestions_service.py` shows additions only — no edits to `refill_mirror_sql`, `maybe_schedule_refill`, or `SUGGESTIONS_TARGET_SIZE`.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Wire discovery_call_weekly — cap-before-write + Plex-push-after</name>
  <files>app/services/suggestions_discovery.py, tests/test_suggestions_discovery.py</files>
  <behavior>
    Six new tests in tests/test_suggestions_discovery.py (use the existing `_make_fake_anthropic_client` / `db_with_phase7` / `monkeypatch` patterns visible at the top of this file):

    - `test_discovery_pushes_new_picks_to_plex` — pre-seed empty mirror + a `ManagedPlaylist(kind='suggestions')` row with a non-empty `plex_rating_key`; fake LLM returns 3 valid picks; monkeypatch `update_playlist_items` (a `MagicMock` capturing args) and `remove_tracks_from_suggestions_playlist` (also mocked); run `await discovery_call_weekly()`; assert `update_playlist_items` called EXACTLY ONCE with the 3 new picks' plex_rating_keys (as a list, order matches `_write_discovery_picks_sync` pick order).
    - `test_discovery_evicts_oldest_when_mirror_at_target` — pre-seed 30 mirror rows with strictly ascending `added_at`; fake LLM returns 3 picks NOT already in the mirror; run discovery; assert post-run `_count_mirror_rows_sync() == 30` (3 oldest evicted, 3 new added); assert mocked `remove_tracks_from_suggestions_playlist` called with EXACTLY the 3 oldest evicted rating keys.
    - `test_discovery_evicts_overflow_when_mirror_above_target` — pre-seed 40 rows (simulating accumulated Bug A inflation); fake LLM returns 5 picks; assert evict computation `40 + 5 - 30 = 15`; post-run `_count_mirror_rows_sync() == 30`; mocked remove called with the 15 oldest rating keys.
    - `test_discovery_does_not_evict_when_mirror_below_target` — pre-seed 20 rows; fake LLM returns 5; assert post-run count `== 25`; mocked remove either NOT called OR called with empty list (assert whichever the implementation naturally does — pick one and freeze it in the test).
    - `test_discovery_self_heals_on_plex_notfound` — pre-seed mirror with rows + a ManagedPlaylist row with non-empty `plex_rating_key`; fake LLM returns 3 picks; monkeypatch `update_playlist_items` to raise `plexapi.exceptions.NotFound("404 ...")`; monkeypatch `_materialize_suggestions_plex_playlist` to a `MagicMock`; assert `_materialize_suggestions_plex_playlist` called once with `plex_url`, `plex_token`, and the FULL current mirror rating keys (post-write — i.e., the seeded rows minus evicted plus the 3 new picks).
    - `test_discovery_skips_plex_push_when_no_managed_playlist_row` — NO `ManagedPlaylist(kind='suggestions')` row seeded; fake LLM returns 3 picks; mock `update_playlist_items` (assert NOT called); run discovery; assert it completes successfully (no exception), `_count_mirror_rows_sync()` reflects the new picks were written to the mirror, and the warning log is emitted (`caplog.text` contains a substring like `"no ManagedPlaylist"` or `"Skipping Plex push"`).
  </behavior>
  <action>
Modify `discovery_call_weekly` in `app/services/suggestions_discovery.py` (function starts at line 601; the existing `_write_discovery_picks_sync` call is at line ~902-908 inside `if valid_picks:`).

**Imports**: add to the top of `suggestions_discovery.py` (next to existing imports from `suggestions_service`):
```python
from app.services.suggestions_service import (
    SUGGESTIONS_TARGET_SIZE,
    _count_mirror_rows_sync,
    _evict_oldest_mirror_rows_sync,
    _find_suggestions_managed_playlist_sync,
    _materialize_suggestions_plex_playlist,
    _read_plex_creds_sync,
    remove_tracks_from_suggestions_playlist,
)
from app.services.plex_playlist_service import update_playlist_items
```
(Several of these may already be imported — keep imports tidy, no duplicates.)

**Modify ONLY the `if valid_picks:` block** (currently lines ~901-908). Replace with the structure below. DO NOT touch anything outside this block — not the hallucination filter above, not the `_reset_plays_since_discovery_sync` / status update / success log below.

```python
if valid_picks:
    # --- Part 2 (Bug B fix): cap mirror BEFORE write. ---
    current_count = await asyncio.to_thread(_count_mirror_rows_sync)
    evict_count = max(0, current_count + len(valid_picks) - SUGGESTIONS_TARGET_SIZE)
    evicted_keys: list[str] = await asyncio.to_thread(
        _evict_oldest_mirror_rows_sync, evict_count,
    )

    # --- Existing write (unchanged). ---
    await asyncio.to_thread(
        _write_discovery_picks_sync,
        valid_picks,
        len(candidates),
        latency_ms,
        0.0,
    )

    # --- Part 1 (Bug A fix): push new picks to Plex AFTER write. ---
    # Mirror refill_mirror_sql's Plex-push block (suggestions_service.py:1001-1075)
    # EXACTLY — same ManagedPlaylist lookup, same defer-to-materialize branch,
    # same NotFound self-heal idiom (commit 719c6b7 / 260517-l84).
    new_keys = [p.plex_rating_key for p in valid_picks]  # OR equivalent — Pick dataclass shape; use the same attribute refill_mirror_sql:1002 uses.
    mp = await asyncio.to_thread(_find_suggestions_managed_playlist_sync)
    added_ok = False
    if mp is None:
        logger.warning(
            "discovery_call_weekly: no ManagedPlaylist(kind=suggestions) "
            "row found; bootstrap was likely skipped. Skipping Plex push."
        )
    elif not mp.plex_rating_key:
        # Deferred-materialize branch (mirrors refill_mirror_sql:1009-1015).
        plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)
        await _materialize_suggestions_plex_playlist(
            plex_url, plex_token, new_keys,
        )
        added_ok = True  # materialize created the playlist with these tracks
    else:
        plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)
        try:
            await update_playlist_items(
                plex_url, plex_token, mp.plex_rating_key, new_keys,
            )
            added_ok = True
        except Exception as exc:
            # Self-heal — CANONICAL idiom from refill_mirror_sql:1036-1043.
            err_str = (type(exc).__name__ + " " + str(exc)).lower()
            from plexapi import exceptions as _plexex
            is_not_found = (
                isinstance(exc, _plexex.NotFound)
                or "notfound" in err_str
                or "not found" in err_str
                or "404" in err_str
            )
            if is_not_found and not isinstance(exc, PermissionError):
                logger.warning(
                    "discovery_call_weekly: Plex playlist rk=%s vanished "
                    "(likely user-deleted); re-materializing from full "
                    "current mirror.",
                    mp.plex_rating_key,
                )
                # Re-materialize from the FULL current mirror (post-write,
                # post-evict) — same shape as refill_mirror_sql:1051-1054
                # but seeded with all current mirror keys, not just the
                # new picks, because the user deleted the playlist and we
                # want to rebuild the whole queue.
                try:
                    full_mirror_keys = await asyncio.to_thread(
                        _read_all_mirror_rating_keys_sync,
                    )
                    await _materialize_suggestions_plex_playlist(
                        plex_url, plex_token, full_mirror_keys,
                    )
                except Exception:
                    logger.exception(
                        "discovery_call_weekly: re-materialize after "
                        "vanish-detect ALSO failed; mirror state is "
                        "the truth — next refill will retry."
                    )
            else:
                logger.exception(
                    "discovery_call_weekly: Plex update_playlist_items "
                    "failed (not a vanish); mirror state is the "
                    "truth — Plex will reconcile on next weekly prune."
                )

    # --- Part 2 (Bug B fix continued): remove evicted keys from Plex AFTER add. ---
    # Order matters (gotcha #10): ADD first (user sees fresh tracks immediately),
    # then REMOVE evicted (user sees old tracks disappear). Wrap removal in its
    # own try/except so a removal failure doesn't undo the add — the weekly
    # prune (prune_suggestions_playlist_to_mirror) reconciles.
    if added_ok and evicted_keys and mp is not None and mp.plex_rating_key:
        try:
            removed_count = await remove_tracks_from_suggestions_playlist(
                plex_url, plex_token, mp.plex_rating_key, evicted_keys,
            )
        except Exception:
            logger.exception(
                "discovery_call_weekly: remove evicted keys failed; "
                "weekly prune will reconcile.",
            )
            removed_count = 0
    else:
        removed_count = 0

    logger.info(
        "discovery_call_weekly Plex sync: added=%d removed=%d "
        "(mirror_target=%d)",
        len(new_keys) if added_ok else 0,
        removed_count,
        SUGGESTIONS_TARGET_SIZE,
    )
```

**Note on `_read_all_mirror_rating_keys_sync`**: this helper is needed for the vanish-detect re-materialize branch and may not already exist. If it doesn't, add it next to `_count_mirror_rows_sync` in `app/services/suggestions_service.py` as part of Task 3 (it's tiny — `SELECT t.plex_rating_key FROM suggestionsmirror sm JOIN track t ON sm.track_id = t.id ORDER BY sm.added_at ASC, sm.id ASC` returning `list[str]`). If a similar helper already exists (grep for `plex_rating_key.*suggestionsmirror`), reuse it. Add the import accordingly.

**Pick attribute name**: the executor must inspect the `DiscoveryPicksResponse` / `Pick` dataclass to use the correct attribute for `plex_rating_key`. The valid_picks objects already have `track_id` (used in `valid_ids`); confirm whether they expose `plex_rating_key` directly or whether the executor needs to JOIN against Track inside the helper. If the Pick dataclass does NOT carry `plex_rating_key`, derive it from the picks via a small sync helper `_read_plex_keys_for_track_ids_sync(track_ids: list[int]) -> list[str]` (also added to suggestions_service.py next to `_count_mirror_rows_sync`), preserving pick order. The test `test_discovery_pushes_new_picks_to_plex` is the contract — it asserts the rating keys passed to `update_playlist_items` match the 3 new picks' rating keys. Make whichever choice satisfies that test cleanly.

**RED-GREEN-REFACTOR**: write all 6 tests FIRST. Some will fail because `update_playlist_items` isn't called yet (Bug A test), others because eviction isn't wired (Bug B tests), the NotFound test because the self-heal path doesn't exist. Confirm reds, then implement the wiring above, then confirm greens.

**Gotchas the executor MUST honor (from task_context, repeated here for the file the executor will be looking at):**
- DO NOT change `_write_discovery_picks_sync` itself.
- DO NOT change `maybe_schedule_refill`'s deficit gate (still `deficit = max(0, target - current)` — it becomes correct again once the cap holds).
- DO NOT change `refill_mirror_sql`.
- DO NOT change `prune_suggestions_playlist_to_mirror`.
- NotFound detection MUST be the canonical 4-part match (`isinstance NotFound`, `"notfound"`, `"not found"`, `"404"`) — grep-equivalent to suggestions_service.py:1036-1043. The reviewer will diff these two blocks character-by-character.
- ADD before REMOVE (gotcha #10 — user experience: fresh tracks appear before old ones vanish).
- Removal failure must NOT roll back the add (weekly prune reconciles).
  </action>
  <verify>
<automated>cd /Users/Oreo/Projects/Composer && python -m pytest tests/test_suggestions_discovery.py -k "discovery_pushes_new_picks_to_plex or discovery_evicts_oldest_when_mirror_at_target or discovery_evicts_overflow_when_mirror_above_target or discovery_does_not_evict_when_mirror_below_target or discovery_self_heals_on_plex_notfound or discovery_skips_plex_push_when_no_managed_playlist_row" -x -v && python -m pytest tests/test_event_handlers.py::test_no_blocking_plexapi_in_async -x -v</automated>
  </verify>
  <done>
- 6 new `test_discovery_*` tests in tests/test_suggestions_discovery.py all pass.
- `discovery_call_weekly` now: (a) caps the mirror BEFORE write via `_evict_oldest_mirror_rows_sync`, (b) calls `update_playlist_items` with new picks AFTER write, (c) calls `remove_tracks_from_suggestions_playlist` with evicted keys AFTER add succeeded, (d) self-heals on Plex NotFound by calling `_materialize_suggestions_plex_playlist` with the full current mirror, (e) skips Plex push with a warning log when no `ManagedPlaylist(kind='suggestions')` row exists, (f) logs single info line `"discovery_call_weekly Plex sync: added=%d removed=%d (mirror_target=%d)"`.
- Phase 5 Convention #1 static AST test still passes.
- `git diff` shows ONLY: app/services/suggestions_service.py, app/services/suggestions_discovery.py, tests/test_suggestions_service.py, tests/test_suggestions_discovery.py.
- `grep -nE "def (maybe_schedule_refill|refill_mirror_sql|prune_suggestions_playlist_to_mirror|_write_discovery_picks_sync)" app/services/suggestions_service.py app/services/suggestions_discovery.py` shows these functions exist and `git diff -- <those file ranges>` shows they were NOT modified.
- `grep -n "SUGGESTIONS_TARGET_SIZE = 30" app/services/suggestions_service.py` shows the constant unchanged.
  </done>
</task>

</tasks>

<verification>
After all three tasks complete, the executor MUST run the gates below (these match the verification gates in task_context — re-stated here so they're enforced in `<verify>` form):

1. **Diff scope gate** — only the 4 files in `files_modified` are touched:
   ```bash
   git diff --stat HEAD~3..HEAD
   ```
   Expected: exactly `app/services/suggestions_service.py`, `app/services/suggestions_discovery.py`, `tests/test_suggestions_service.py`, `tests/test_suggestions_discovery.py`. If any other file appears (templates, routers, other services), FAIL and revert.

2. **No-touch gate** — confirm the protected functions are byte-identical to baseline:
   ```bash
   git diff HEAD~3..HEAD -- app/services/suggestions_service.py | grep -E "^[-+] +" | grep -E "(def maybe_schedule_refill|def refill_mirror_sql|SUGGESTIONS_TARGET_SIZE = 30)" | grep -v "^[-+] *#"
   ```
   Expected: no output (no edits inside `maybe_schedule_refill` or `refill_mirror_sql` signatures or body; constant unchanged).

3. **NotFound idiom parity gate** — confirm the new self-heal block uses the canonical 4-part match identical to refill_mirror_sql:
   ```bash
   grep -A 6 "is_not_found" app/services/suggestions_discovery.py | grep -E "(isinstance.*NotFound|notfound|not found|404)" | wc -l
   ```
   Expected: count `>= 4` (all four conditions present) in the new discovery block.

4. **New tests** — all 9 new tests pass:
   ```bash
   python -m pytest tests/test_suggestions_service.py -k "evict_oldest or remove_tracks" -v
   python -m pytest tests/test_suggestions_discovery.py -k "discovery_pushes_new_picks_to_plex or discovery_evicts_oldest_when_mirror_at_target or discovery_evicts_overflow_when_mirror_above_target or discovery_does_not_evict_when_mirror_below_target or discovery_self_heals_on_plex_notfound or discovery_skips_plex_push_when_no_managed_playlist_row" -v
   ```
   Expected: 9 passed (3 + 6).

5. **Regression gate** — adjacent test modules pass clean:
   ```bash
   python -m pytest tests/test_suggestions_service.py tests/test_suggestions_discovery.py tests/test_plex_playlist_service.py tests/test_discovery_service.py tests/test_event_handlers.py -x
   ```
   Expected: 0 failures. Specifically, the existing `_weekly_maintenance_tick` test in `tests/test_discovery_service.py` and the 260517-l84 self-heal tests in `tests/test_plex_playlist_service.py` MUST still pass.

6. **Phase 5 Convention #1 gate** — already covered by regression gate (`tests/test_event_handlers.py::test_no_blocking_plexapi_in_async`), but the executor should sanity-check by grepping the new helper: `grep -n "removeItems\|fetchItem" app/services/suggestions_service.py` should show those calls ONLY inside the `_remove_items_sync` nested function (inside `asyncio.to_thread`), not in any `async def` body directly.
</verification>

<success_criteria>
- All 6 verification gates pass.
- 9 new tests pass (3 unit + 6 integration), 0 regressions across the 5 adjacent test modules.
- Discovery now: caps mirror at 30 before write, pushes new picks to Plex after write, removes evicted rating keys from Plex after add, self-heals on NotFound using the canonical idiom.
- `refill_mirror_sql`, `maybe_schedule_refill`, `prune_suggestions_playlist_to_mirror`, `_write_discovery_picks_sync`, and `SUGGESTIONS_TARGET_SIZE` are byte-identical to baseline.
- `git diff --stat` shows EXACTLY the 4 files in `files_modified`.
- The executor commits the work with a message in the form `fix(suggestions): discovery pushes to Plex + cap SuggestionsMirror at target size (refill regression)`.
</success_criteria>

<output>
After completion, create `.planning/quick/260517-nkt-fix-suggestions-discovery-pushes-to-plex/260517-nkt-01-SUMMARY.md` describing:
- The two regressions fixed (Bug A + Bug B), one paragraph each.
- The two new helpers added, with one-line signatures.
- The wiring change in `discovery_call_weekly` (cap → write → add → remove).
- A note that `maybe_schedule_refill`, `refill_mirror_sql`, `prune_suggestions_playlist_to_mirror`, and `_write_discovery_picks_sync` are intentionally unchanged.
- NAS verification plan: on next deploy, watch `RefillTriggerLog` — within one day of activity, expect `sql_refill` events on play to resume (was 0 today, was many yesterday before the cap broke).
</output>
