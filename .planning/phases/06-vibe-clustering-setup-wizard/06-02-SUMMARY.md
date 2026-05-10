---
phase: 06-vibe-clustering-setup-wizard
plan: 02
subsystem: vibe-runtime
tags: [sklearn, kmeans, anthropic-prompt-cache, asyncio-lock, plex-playlist, ast-test, sqlmodel, mobile-first]

# Dependency graph
requires:
  - phase: 06-vibe-clustering-setup-wizard
    plan: 01
    provides: |
      Vibe / TrackVibe / ManagedPlaylist / SetupState SQLModel tables;
      Track.pending_slot_in column + ix_track_pending_slot_in PARTIAL index;
      ix_trackvibe_vibe_id + ix_managedplaylist_kind indexes;
      sklearn AST allowlist tripwire (test_sklearn_only_in_clusterer_module);
      base.html alpine-morph + min-h-dvh + viewport-fit conventions;
      app/services/vibe_helpers.feature_chip_text helper.
  - phase: 05-plex-event-foundation-rating-sync
    provides: |
      AnthropicClient with explicit ttl=1h cache_control + Pydantic structured
      output + automatic LLMUsage logging;
      asyncio.to_thread invariant + AST static test (test_no_blocking_plexapi_in_async);
      EventLog dedupe (INSERT OR IGNORE on UNIQUE dedupe_key);
      raw 0-10 user_rating semantics (Pitfall 2);
      module-level singleton _state + get_state() pattern;
      RatingChangedEvent / dispatch_event pipeline;
      taste_profile_service.maybe_recompute_after_rating_change hook.

provides:
  - app/services/vibe_clusterer.py — VibeProposal + VibeProposalSet pydantic schemas (D-03 / D-32) — Plan 03 wizard /setup/propose imports verbatim
  - app/services/vibe_clusterer.initial_cluster_proposal / refine_proposals / materialize_clusters (D-32, D-33, D-34, D-35)
  - app/services/plex_playlist_service.py — create_playlist / update_playlist_items / remove_from_playlist / archive_playlist / rename_playlist / is_managed_playlist (D-24, D-27)
  - app/services/plex_playlist_service.ReconcileResult — added / unchanged / silently_dropped / retried (Pitfall 6 / VIBE-12)
  - app/services/vibe_service.py — slot_track / unslot_track / maybe_reslot_pending_track / reslot_all_rated_tracks / get_vibe_service_status (D-15 - D-19; VIBE-02, VIBE-09, VIBE-10)
  - Per-track asyncio.Lock dict (_slot_in_locks) bounded at 100 entries (D-16 / Pitfall 23)
  - Z-score-normalized 4-D distance + soft-margin cap=2 implementation (D-05 / Pitfall 24 / VIBE-02)
  - app/services/event_handlers.handle_rating_changed — appended best-effort vibe slot/unslot hook AFTER taste-profile recompute (D-15 / D-18)
  - app/services/analysis_service.run_analysis — appended D-17 retroactive reslot post-track hook
  - Track.pending_slot_in promoted from DB-only column to SQLModel-visible field
  - Extended AST test (tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async) — walks 3 files instead of 1; recognizes inner `def` + asyncio.to_thread(name) idiom; forbidden names extended with fetchItems / createPlaylist / addItems / removeItems / editTitle
affects: [06-03, 06-04, 07-01, 07-02]

# Tech tracking
tech-stack:
  added:
    - "scikit-learn (KMeans + silhouette_score) — sole import site is app/services/vibe_clusterer.py per D-33 allowlist"
  patterns:
    - "Inner `def` + asyncio.to_thread(name) idiom approved by AST walker — chat_service.push_playlist_to_plex pattern adopted in plex_playlist_service"
    - "OrderedDict-backed LRU lock dict bounded at N entries (vibe_service._slot_in_locks)"
    - "Pydantic VibeProposalSet integer-index validation + retry-once-then-raise (Pitfall 10 server-side guard against LLM hallucination)"
    - "Plan 5+ dual-marker mutation rule applied via is_managed_playlist + Composer · prefix check at every plex_playlist_service write site"
    - "Best-effort hook chaining in event_handlers.handle_rating_changed: taste_profile recompute → vibe slot/unslot, both lazy-imported, both swallow exceptions"

key-files:
  created:
    - "app/services/vibe_clusterer.py — k-means + silhouette + LLM naming + refinement turn (~590 lines)"
    - "app/services/plex_playlist_service.py — create / update_playlist_items / remove / archive / rename / is_managed_playlist (~360 lines)"
    - "app/services/vibe_service.py — slot_track / unslot_track / maybe_reslot_pending_track / reslot_all_rated_tracks (~470 lines)"
    - "tests/test_plex_playlist_service.py — 9 behavioral tests (FakePlexServer fixture)"
    - "tests/test_vibe_service.py — 12 behavioral tests covering slot semantics, lock serialization, retroactive path, reslot semaphore"
    - "tests/test_event_handlers_phase6.py — 4 wiring tests (slot/unslot branches, best-effort failure swallow, analysis post-hook)"
  modified:
    - "tests/test_vibe_clusterer.py — added 9 behavioral tests around the existing AST allowlist tripwire"
    - "tests/test_event_handlers.py — extended TestStaticAnalysis to walk 3 files + recognize inner-def idiom + 5 new forbidden PlexAPI names"
    - "app/services/event_handlers.py — appended Phase 6 vibe slot/unslot best-effort hook after the existing taste-profile recompute"
    - "app/services/analysis_service.py — appended D-17 maybe_reslot_pending_track call inside the result['success'] branch"
    - "app/models/track.py — promoted pending_slot_in to SQLModel field (was DB-column-only after Plan 01)"

key-decisions:
  - "AST walker upgraded to recognize inner `def` + asyncio.to_thread(name) idiom — needed because plex_playlist_service mirrors chat_service.push_playlist_to_plex's nested-def pattern. The walker now collects names passed as the first arg to asyncio.to_thread inside an AsyncFunctionDef and treats those inner FunctionDef bodies as approved (running in a worker thread). Other nested FunctionDef bodies remain walked recursively."
  - "Track.pending_slot_in PROMOTED from DB-only column to SQLModel field. Plan 01 added the column via _migrate_add_columns but did not include it in the Track model class — vibe_service needs ORM-level access to read/write the flag. Schema unchanged for existing DBs (column already exists; create_all is idempotent)."
  - "Soft-margin std-dev anchor = 1.0 in z-score-normalized space. The plan suggested averaging per-vibe spreads as the std-dev; in z-score space 1 std-dev IS 1.0 by construction. Simpler + correct."
  - "Inner `def` style chosen over module-level _xxx_sync(url, token, key) for plex_playlist_service public functions — matches chat_service.push_playlist_to_plex precedent and avoids per-call function arg plumbing. The AST walker upgrade made this safe."
  - "VibeProposalSet.rated_track_index_map and rated_track_count are server-controlled — overridden after every LLM response (initial + refine) regardless of what the LLM returns. Defends against LLM hallucinating these indices into a stale state."
  - "Retry-once corrective prompt for out-of-range seed_track_indices includes the exact valid range [0, n_rated). On second failure raises ValueError; the wizard surfaces this as a refinement-turn error and the user can re-prompt."
  - "Module-level scikit-learn import duplication avoided: vibe_service does NOT import sklearn (kept the allowlist pure). _z_score_normalize is duplicated as a 5-line pure-numpy helper in both vibe_clusterer and vibe_service for module independence."

patterns-established:
  - "Inner `def` + asyncio.to_thread(inner) is the canonical PlexAPI-from-async idiom. AST walker now approves it explicitly. Future Plex-touching async services should follow this pattern (matches chat_service / plex_playlist_service)."
  - "Composer · prefix + ManagedPlaylist row dual-marker check is enforced at the plex_playlist_service boundary via is_managed_playlist + an explicit ValueError on missing prefix. Callers (vibe_service.unslot_track, future Plan 04 re-cluster commit) inherit the check by routing through these public functions."
  - "Best-effort hook chaining in event_handlers: each new domain hook (taste_profile in Plan 5 + vibes in Plan 6) is appended AFTER the prior hook with its own try/except + lazy import. Order matters — taste-profile-first means later vibe hook sees a fresh centroid."
  - "Per-track asyncio.Lock for hot-path serialization (Pitfall 23): _slot_in_locks bounded at 100 via OrderedDict.popitem(last=False). Pattern reusable for any future per-key serialization (e.g., Phase 7 per-track Suggestions ranking)."

requirements-completed:
  - VIBE-02
  - VIBE-05
  - VIBE-06
  - VIBE-09
  - VIBE-10
  - VIBE-12
  - OPS-06

# Metrics
duration: ~22min
completed: 2026-05-10
---

# Phase 6 Plan 02: Slot-in Hot Path + Clusterer + Plex Playlist Service Summary

**Three new service modules — vibe_clusterer.py (k-means + silhouette + LLM naming + refinement turn), plex_playlist_service.py (create/update/remove/archive/rename + dual-marker enforcement), vibe_service.py (per-track lock-guarded slot/unslot/reslot with z-score normalization + soft-margin cap=2) — plus the event_handlers and analysis_service hook wiring that turns every RatingChanged event into a vibe membership update within seconds. Phase 5 and Plan 01 invariants preserved end-to-end.**

## Performance

- **Duration:** ~22 min (3 tasks, 3 commits)
- **Started:** 2026-05-10 03:34 UTC (worktree branch `worktree-agent-a8347d6bdc201c4bf` from `3d7b2b1`)
- **Completed:** 2026-05-10 03:57 UTC
- **Tasks:** 3 / 3 complete
- **Files created:** 6 (3 service + 3 test)
- **Files modified:** 5 (test_vibe_clusterer + test_event_handlers + event_handlers + analysis_service + track model)

## Accomplishments

- **vibe_clusterer.py** ships with a complete RED → GREEN cycle for the LLM-driven cluster proposer + refinement turn. Handles cold-start (n<30 → degraded "Your Taste" with NO LLM call), silhouette-optimal k pick over k∈[3, min(7, n//15)], forced_k override, z-score normalization (D-05 — tempo's [60,180] vs others' [0,1]), out-of-range seed_track_indices retry-once-then-raise (Pitfall 10), and three distinct purpose strings (D-34) for Phase 7 cost segmentation. The AnthropicClient cache_control + ttl=1h + LLMUsage logging is fully inherited from Phase 5; zero new LLM client code.

- **plex_playlist_service.py** ships the dual-marker-enforced playlist CRUD that Plans 03-04 will call into. Every public function wraps PlexAPI in asyncio.to_thread (D-09); the extended AST static test enforces this for all three new files. update_playlist_items is ADDITIVE ONLY (Pitfall 5) and runs the post-push verify with one-shot retry (Pitfall 6 / VIBE-12). The is_managed_playlist helper + Composer · prefix invariant on every mutation rejects unmanaged playlists with PermissionError (D-27 / OPS-06 / Pitfall 20). Token sanitization on every error path (T-06-02-03).

- **vibe_service.py** ships the always-on slot-in hot path. Per-track asyncio.Lock keyed on plex_rating_key serializes rapid rate-correct events (Pitfall 23). Inside the lock, the latest Track row is read from the DB (NOT the event payload — defensive against partial event snapshots). Z-score normalization uses the centroids' per-dimension mean/std as the basis; in that space 1 std-dev = 1.0 by construction, so the soft-membership margin (Pitfall 24 / VIBE-02) is a clean threshold. Cap at 2 vibes per track (D-19). Manual override stickiness: assigned_by='manual' rows are never overwritten and surface via SlotInResult.skipped_manual. D-17 retroactive path: missing audio features → set pending_slot_in=TRUE; the analysis_service post-track hook (added in this plan) calls maybe_reslot_pending_track once features land. reslot_all_rated_tracks runs sequentially under Semaphore(1) per D-25 (NAS-friendly).

- **event_handlers.handle_rating_changed extended** with the second best-effort hook AFTER the existing taste-profile recompute. Branches on event.new_rating: 0/None → unslot_track; > 0 → slot_track. Lazy import + try/except matches the existing convention.

- **analysis_service.run_analysis extended** inside the `if result["success"]:` branch with the maybe_reslot_pending_track call. Lazy import + best-effort try/except.

- **AST static test extended** to walk three files (event_handlers + plex_playlist_service + vibe_service) with five new forbidden PlexAPI names (fetchItems, createPlaylist, addItems, removeItems, editTitle). The walker now recognizes the inner `def` + asyncio.to_thread(name) idiom: inner functions whose name is passed as the first arg of asyncio.to_thread are approved (their PlexAPI calls run in a worker thread, NOT on the event loop). All other nested FunctionDef bodies are still walked recursively. Missing files are skipped to allow per-task incremental commits.

- **Track.pending_slot_in promoted** from DB-only column (Plan 01 _migrate_add_columns) to a SQLModel-visible field on the Track model. Schema unchanged — the column was already created; this just teaches the ORM about it. Without this promotion, vibe_service's ORM-based reads/writes would silently fail.

- **All Phase 5 + Plan 01 invariants preserved.** The sklearn allowlist test passes (sklearn lives only in vibe_clusterer.py); the Phase 5 test_no_sklearn_import on taste_profile_service still green; the original event_handlers AST test runs against the upgraded walker on three files instead of one.

## Task Commits

Each task was committed atomically:

1. **Task 1: vibe_clusterer.py — k-means + silhouette + LLM naming + refinement turn** — `4a7c4e4` (feat)
2. **Task 2: plex_playlist_service.py + extend AST static test to 3 files** — `cc03d49` (feat)
3. **Task 3: vibe_service.py + event_handlers + analysis_service wiring + Track.pending_slot_in promotion** — `92b3e78` (feat)

## Files Created/Modified

### Created

- **`app/services/vibe_clusterer.py`** — VibeProposal + VibeProposalSet pydantic schemas (Plan 03 wizard `/setup/propose` imports verbatim); `initial_cluster_proposal(forced_k)`, `refine_proposals(prior, user_message, recluster_mode)`, `materialize_clusters(proposals)` async/sync public API; `_aggregate_rated_set_sync`, `_z_score_normalize`, `_pick_best_k`, `_build_clustering_system_prompt` (>2048 tokens for Sonnet 4.6 cache), `_build_clustering_user_prompt`, `_validate_seed_indices`, `_call_llm_with_validation` (retry-once); module-local `get_anthropic_client_v2` shim. sklearn imports (KMeans + silhouette_score) live ONLY here per D-33.

- **`app/services/plex_playlist_service.py`** — ReconcileResult pydantic schema; `create_playlist`, `update_playlist_items`, `remove_from_playlist`, `archive_playlist`, `rename_playlist` async public functions; `is_managed_playlist` sync DB-only helper; `_sanitize` token-redactor; sync DB helpers (`_insert_managed_playlist_sync`, `_update_managed_playlist_sync`, `_delete_managed_playlist_sync`). Every PlexAPI call wrapped via inner `def` + `await asyncio.to_thread(_inner)`.

- **`app/services/vibe_service.py`** — SlotInResult + VibeServiceStatus dataclasses; `slot_track`, `unslot_track`, `maybe_reslot_pending_track`, `reslot_all_rated_tracks` async public functions; `get_vibe_service_status`, `_get_lock` (LRU bounded at 100); `_z_score_normalize` (duplicated from vibe_clusterer for module independence); sync DB helpers (`_read_track_and_vibes_sync`, `_read_track_by_id_sync`, `_read_existing_trackvibe_sync`, `_upsert_trackvibe_sync`, `_delete_trackvibes_sync`, `_set_pending_slot_in_sync`, `_read_managed_playlist_for_vibe_sync`, `_read_vibe_member_rating_keys_sync`, `_read_all_rated_track_keys_sync`, `_get_plex_credentials_sync`).

- **`tests/test_plex_playlist_service.py`** — 9 behavioral tests with FakePlexServer / FakePlaylist / FakeTrack fixtures; covers create persistence + prefix validation, additive update, post-push verify silent drops, dual-marker pre-flight on update + remove, is_managed_playlist DB lookup, archive rename + row delete, rename prefix validation.

- **`tests/test_vibe_service.py`** — 12 behavioral tests with `db_with_phase6` + autouse `reset_vibe_service_singletons` + `_patch_plex_helpers` fixtures; covers closest-vibe pick, soft-margin + cap=2, user_rating=0 no-op, missing-features pending path, manual stickiness, additive Plex push, unslot remove all + per-vibe Plex remove, per-track lock serialization (asyncio.gather acquires the lock manually then verifies the second call blocks), maybe_reslot_pending_track success/skip-when-unrated, reslot_all_rated_tracks Semaphore(1) max-concurrency assertion.

- **`tests/test_event_handlers_phase6.py`** — 4 wiring tests covering slot vs unslot branching, best-effort failure swallow (RuntimeError in slot_track does not propagate up), and analysis_service.run_analysis post-track maybe_reslot_pending_track call.

### Modified

- **`tests/test_vibe_clusterer.py`** — preserved Plan 01's `test_sklearn_only_in_clusterer_module` AST allowlist tripwire VERBATIM; added 9 behavioral tests + `db_with_phase6` fixture + `_seed_tracks(well_separated)` helper that synthesizes 3 Gaussian-separated 4-D clusters using `numpy.random.RandomState(42)`.

- **`tests/test_event_handlers.py`** — extended `TestStaticAnalysis::test_no_blocking_plexapi_in_async` to walk 3 file paths (event_handlers, plex_playlist_service, vibe_service); added 5 forbidden names (fetchItems, createPlaylist, addItems, removeItems, editTitle); upgraded the AST walker to collect inner `def` names passed as the first arg of `asyncio.to_thread(...)` and treat those inner FunctionDef bodies as approved. Missing files are skipped (preserves per-task incremental commit ordering).

- **`app/services/event_handlers.py`** — appended Phase 6 vibe slot/unslot hook AFTER the existing taste-profile recompute hook in `handle_rating_changed`. Lazy import + try/except (mirrors taste-profile pattern). The taste-profile hook MUST run first (rationale: Plan 04 re-cluster needs a fresh centroid; the LLM call sees the latest summary).

- **`app/services/analysis_service.py`** — inside the `if result["success"]:` branch (after `_analysis_status.analyzed_tracks += 1`), added the D-17 retroactive reslot hook with lazy import + try/except.

- **`app/models/track.py`** — promoted `pending_slot_in: Optional[int] = Field(default=0)` from DB-only (Plan 01 migration) to a SQLModel field on Track. Schema unchanged (column already exists from Plan 01); this just teaches the ORM about it.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking issue] Installed scikit-learn 1.6.1 in local Python 3.9 venv.**
- **Found during:** Task 1 GREEN (first attempt to import sklearn in vibe_clusterer.py).
- **Issue:** Spec'd version is `scikit-learn>=1.8,<2.0` in requirements.txt, but the local venv runs Python 3.9 (CLAUDE.md spec is Python 3.12+ for production). sklearn 1.8+ requires Python 3.11+; sklearn 1.6.1 is the highest compatible with Py3.9. KMeans + silhouette_score APIs are stable across these versions.
- **Fix:** Installed `scikit-learn==1.6.1` in the venv for local testing. Production Dockerfile (Python 3.12-slim per CLAUDE.md) will install the spec'd `>=1.8,<2.0`.
- **Files modified:** None — venv-only change. Production unchanged.
- **Commit:** None — environment-only.

**2. [Rule 3 — Blocking issue] Promoted Track.pending_slot_in from DB-only to SQLModel field.**
- **Found during:** Task 3 implementation (vibe_service needs ORM access to read/write the flag).
- **Issue:** Plan 01 added `pending_slot_in INTEGER DEFAULT 0` via the `_migrate_add_columns` shim but did NOT include it in the Track SQLModel class. SQLModel-based reads (`track.pending_slot_in`) would have AttributeError'd at runtime; SQLModel-based writes would have silently failed. Plan 01's SUMMARY explicitly notes "fresh table create_all also produces a working schema once the Phase 6 tables are registered" but that is misleading — it only works because `_migrate_add_columns` runs unconditionally after `create_all` in `init_db`.
- **Fix:** Added `pending_slot_in: Optional[int] = Field(default=0)` to `app/models/track.py`. Schema is unchanged (column already exists for both fresh and migrated DBs). Documented the rationale in the field's docstring.
- **Files modified:** `app/models/track.py`.
- **Commit:** Bundled into Task 3 (`92b3e78`).

**3. [Rule 1 — Bug] AST walker descended into nested `def` bodies, falsely flagging the chat_service inner-`def` + asyncio.to_thread idiom.**
- **Found during:** Task 2 GREEN (extended AST test reported every PlexAPI call in plex_playlist_service.py as a violation).
- **Issue:** The Phase 5 AST walker used `ast.walk(node)` to find Calls inside an AsyncFunctionDef. That walks ALL nested children including inner `def _create()` bodies — but those inner functions are explicitly designed to run in a worker thread via `asyncio.to_thread(_create)`. The original walker worked for event_handlers.py only because Phase 5 used module-level `_xxx_sync` functions everywhere. plex_playlist_service.py adopts the chat_service.push_playlist_to_plex inner-`def` style (already established in the codebase).
- **Fix:** Upgraded the walker to (a) collect all names passed as the first positional arg of `asyncio.to_thread(...)` calls inside the AsyncFunctionDef, (b) when descending into inner FunctionDef bodies, skip those whose name is in the approved set (their PlexAPI calls do NOT block the event loop), (c) recursively check non-approved nested FunctionDef bodies.
- **Files modified:** `tests/test_event_handlers.py` (TestStaticAnalysis::test_no_blocking_plexapi_in_async).
- **Commit:** Bundled into Task 2 (`cc03d49`).

**4. [Rule 1 — Bug] PlexAPI fetchItem accepts strings, not just ints — original implementation forced int() conversion.**
- **Found during:** Task 2 GREEN (test_update_playlist_items_is_additive_only failed with `ValueError: invalid literal for int() with base 10: 'e'`).
- **Issue:** My initial implementation called `plex.fetchItem(int(playlist_rating_key))` and `plex.fetchItem(int(k))` for track keys. Real PlexAPI ratingKeys are integers in production but stored as strings throughout Composer (`str(t.ratingKey)` everywhere). PlexAPI's fetchItem accepts both types, so the int() cast was unnecessary and broke string-keyed test fixtures.
- **Fix:** Pass-through whatever the caller provided. Production code paths still feed integer-shaped strings; tests can use any string identifier.
- **Files modified:** `app/services/plex_playlist_service.py` (4 inner-def callsites).
- **Commit:** Bundled into Task 2 (`cc03d49`).

### Out of Scope (none new)

The pre-existing 14 unrelated test failures from Plan 01 remain on the worktree's main-branch base; tracked in `.planning/phases/06-vibe-clustering-setup-wizard/deferred-items.md`. Plan 02 added zero new failures.

## TDD Gate Compliance

This plan has type=`execute` (not `tdd`), so plan-level RED→GREEN→REFACTOR gate sequence is not required. Each task individually had `tdd="true"` and followed the per-task TDD cycle:

- **Task 1:** RED (test_vibe_clusterer.py — 9 behavioral tests + the 1 preserved AST test failed on `ImportError: No module named 'app.services.vibe_clusterer'`) → GREEN (created vibe_clusterer.py → all 10 pass).
- **Task 2:** RED (test_plex_playlist_service.py — 9 tests failed on missing module; extended AST test passed trivially because plex_playlist_service.py + vibe_service.py didn't exist yet to trigger the walker's strict checks) → GREEN (created plex_playlist_service.py → 9 pass; AST walker upgrade fixed the false positive on inner-def idiom; ratingKey type-handling fixed → 19 pass).
- **Task 3:** RED (test_vibe_service.py + test_event_handlers_phase6.py — 16 tests failed on `ImportError: No module named 'app.services.vibe_service'`) → GREEN (created vibe_service.py + extended event_handlers + analysis_service + promoted Track.pending_slot_in to SQLModel field → 16 pass on first attempt).

All commits use `feat(06-02)` prefix per project convention; per-task TDD cycle was collapsed into single `feat` commits because each task's RED/GREEN landed within the same execution turn (no intermediate state worth committing separately).

## Verification Run

Final verification suite (Plan 02 + Phase 5 + Plan 01 invariants):

```
pytest tests/test_vibe_clusterer.py \
       tests/test_plex_playlist_service.py \
       tests/test_vibe_service.py \
       tests/test_event_handlers_phase6.py \
       tests/test_event_handlers.py \
       tests/test_taste_profile_service.py \
       tests/test_database.py \
       tests/test_database_phase6.py \
       tests/test_base_html_conventions.py \
       tests/test_vibe_helpers.py \
       tests/test_event_log.py \
       tests/test_rating_helpers.py
=> 84 passed, 1 warning in 8.08s
```

**AST static test enforcement** (extended in Task 2):

```
$ pytest tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async -x
PASSED  # walks event_handlers + plex_playlist_service + vibe_service; recognizes inner-def + to_thread idiom
```

**sklearn allowlist verification** (Plan 01 tripwire still green after Plan 02 added the import):

```
$ pytest tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module -x
PASSED

$ grep -l "import sklearn\|from sklearn" app/services/*.py | grep -v vibe_clusterer.py | wc -l
0  # no sklearn leakage
```

**Phase 5 invariant** (Phase 5 test that taste_profile_service has no sklearn — STILL GREEN):

```
$ pytest tests/test_taste_profile_service.py::test_no_sklearn_import -x
PASSED
```

**Event handler ordering check** (taste_profile recompute MUST appear BEFORE vibe slot/unslot in handle_rating_changed):

```
$ grep -nE 'taste_profile_service|vibe_service' app/services/event_handlers.py
174:        from app.services.taste_profile_service import (
190:        from app.services.vibe_service import slot_track, unslot_track
```
Line 174 < Line 190 — taste-profile recompute runs first. ✓

**analysis_service post-hook placement** (must be inside the success branch):

```
$ grep -B2 -A2 'maybe_reslot_pending_track' app/services/analysis_service.py | head -10
            if result["success"]:
                _analysis_status.analyzed_tracks += 1
                # Phase 6 D-17: retroactive slot-in. After audio features
                ...
                    from app.services.vibe_service import (
                        maybe_reslot_pending_track,
                    )
                    await maybe_reslot_pending_track(track_id)
```
Hook is inside `if result["success"]:` after `_analysis_status.analyzed_tracks += 1`. ✓

## Plan 03 Unblocked

- **VibeProposalSet contract is locked.** Plan 03's `/setup/propose` route can `await initial_cluster_proposal()`, render the proposal cards, then `await refine_proposals(prior, user_message)` per refinement turn — every field in the schema is the canonical contract.
- **plex_playlist_service.create_playlist + update_playlist_items are ready** for the wizard's Step 4 "Push to Plex" finalize. Plan 03 will iterate over confirmed VibeProposalSet, call create_playlist per proposal, then call update_playlist_items to seed initial members.
- **vibe_service.slot_track is dormant but provably correct.** No vibes exist yet (Plan 03 finalize creates the first ones), so all post-rating events return `primary_vibe_id=None` with the "No vibes yet" log line. Once Plan 03 ships, the slot path activates automatically — zero extra wiring.
- **/debug/vibes Reslot button** (Plan 04) can call `vibe_service.reslot_all_rated_tracks()` for manual recovery. Returns the count slotted; bounded concurrency under Semaphore(1).

## Self-Check: PASSED

Files referenced in this SUMMARY were verified to exist immediately after writing:

- `app/services/vibe_clusterer.py` — FOUND (`grep -c "from sklearn" app/services/vibe_clusterer.py` = 2)
- `app/services/plex_playlist_service.py` — FOUND (`grep -c "async def create_playlist\|async def update_playlist_items\|async def remove_from_playlist\|async def archive_playlist\|async def rename_playlist" app/services/plex_playlist_service.py` = 5)
- `app/services/vibe_service.py` — FOUND (`grep -c "async def slot_track\|async def unslot_track\|async def maybe_reslot_pending_track\|async def reslot_all_rated_tracks" app/services/vibe_service.py` = 4)
- `tests/test_plex_playlist_service.py` — FOUND (9 behavioral tests)
- `tests/test_vibe_service.py` — FOUND (12 behavioral tests)
- `tests/test_event_handlers_phase6.py` — FOUND (4 wiring tests)

Commits referenced in this SUMMARY were verified via `git log --oneline`:

- `4a7c4e4` — FOUND (Task 1)
- `cc03d49` — FOUND (Task 2)
- `92b3e78` — FOUND (Task 3)
