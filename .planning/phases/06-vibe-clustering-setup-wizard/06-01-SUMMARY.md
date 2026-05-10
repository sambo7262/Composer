---
phase: 06-vibe-clustering-setup-wizard
plan: 01
subsystem: foundation
tags: [sqlmodel, schema-migration, sqlite, ast-test, htmx, alpine-morph, tailwind, mobile-first]

# Dependency graph
requires:
  - phase: 05-plex-event-foundation-rating-sync
    provides: |
      Track.user_rating (raw 0-10) populated by backfill + webhook + poll;
      EventLog dedupe convention; AnthropicClient with prompt caching;
      _migrate_add_columns shim pattern; AST test pattern (test_no_blocking_plexapi_in_async,
      test_no_sklearn_import in taste_profile_service.py).
provides:
  - Vibe SQLModel table (centroid + spread + silhouette + is_active per D-28)
  - TrackVibe SQLModel table (composite PK; assigned_by enum cluster|auto-slot|manual; D-19, D-22)
  - ManagedPlaylist SQLModel table (UNIQUE plex_rating_key — DB-side dual-marker per OPS-06 / D-27)
  - SetupState SQLModel table (single-row id=1; draft_proposals_json + refinement_turn_count + last_llm_call_id per D-08)
  - Track.pending_slot_in column (D-29 — D-17 retroactive auto-slot flag)
  - 3 new indexes: ix_trackvibe_vibe_id, ix_managedplaylist_kind, ix_track_pending_slot_in (PARTIAL — WHERE pending_slot_in = 1)
  - app/services/vibe_helpers.feature_chip_text(centroid) pure helper (D-11)
  - tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module — sklearn allowlist tripwire (D-33)
  - app/templates/base.html ships hx-ext="alpine-morph" + min-h-dvh + viewport-fit=cover (D-09 / Pitfalls 15, 16)
  - app/static/css/input.css declares --touch-target-min: 44px (D-09 / Pitfall 17 / UI-04)
  - alpine-morph + alpine-morph-plugin JS bundles installed under app/static/js/
affects: [06-02, 06-03, 06-04, 07-01, 07-02, 08-01]

# Tech tracking
tech-stack:
  added:
    - "htmx-ext-alpine-morph 2.0.1 (bundled, no CDN)"
    - "@alpinejs/morph 3.14.9 (bundled, no CDN)"
  patterns:
    - "AST allowlist pattern (regex-matched filename whitelist) for module-level import boundaries"
    - "Composite-PK SQLModel pattern via primary_key=True on two FK columns (no __table_args__ needed)"
    - "SetupState single-row id=1 + session.merge upsert idiom (mirrors TasteProfile single-row cache)"
    - "Bundled htmx extensions as static JS (no CDN per OPS-06 NAS-deploy convention)"

key-files:
  created:
    - "app/models/vibe.py — Vibe + TrackVibe + ManagedPlaylist + SetupState (4 SQLModel tables)"
    - "app/services/vibe_helpers.py — feature_chip_text pure helper (D-11)"
    - "app/static/js/alpine-morph.min.js — htmx-ext-alpine-morph 2.0.1 adapter"
    - "app/static/js/alpine-morph-plugin.min.js — @alpinejs/morph 3.14.9 plugin"
    - "tests/test_database_phase6.py — 7 tests covering schema migration + UNIQUE + idempotency"
    - "tests/test_vibe_helpers.py — 7 tests covering chip-text rendering + sentinels"
    - "tests/test_vibe_clusterer.py — 1 test (sklearn allowlist AST tripwire)"
    - "tests/test_base_html_conventions.py — 5 tests pinning the project-wide UI conventions"
    - ".planning/phases/06-vibe-clustering-setup-wizard/deferred-items.md — pre-existing test failures (out of scope)"
  modified:
    - "app/database.py — Phase 6 model imports in init_db; pending_slot_in + 3 new indexes in _migrate_add_columns"
    - "app/templates/base.html — viewport-fit=cover + min-h-dvh + hx-ext=alpine-morph + 2 new script tags"
    - "app/static/css/input.css — --touch-target-min: 44px in @theme block"

key-decisions:
  - "DO NOT auto-import vibe models from app/models/__init__.py — it triggers FK resolution at create_all time before Track is in metadata, breaking test isolation. Each call site imports app.models.vibe directly (mirrors Phase 5 pattern)."
  - "ix_track_pending_slot_in is a PARTIAL index (WHERE pending_slot_in = 1) per D-30 — the analysis-service post-hook only ever queries the small 'waiting' subset; partial index keeps the index file tiny."
  - "SetupState.draft_proposals_json is plain str (not Pydantic JSON column) — Plan 03 will VibeProposalSet.model_validate_json() at the boundary per D-03 Phase 5 lock."
  - "TrackVibe composite PK declared via primary_key=True on both FK columns (no __table_args__). Verified round-trip works in test_trackvibe_round_trip_auto_slot."
  - "alpine-morph bundled as two files: htmx-ext adapter (synchronous load after htmx) + Alpine plugin (deferred load before Alpine core). NOT CDN — single-Docker-container deploy per OPS-06."
  - "Threshold bands for feature_chip_text picked from CONTEXT.md Claude's Discretion: 0.33/0.66 for energy/dance/valence; 90/130 BPM for tempo. Symmetric and easily defensible."

patterns-established:
  - "AST allowlist tripwire pattern: regex-match filename whitelist + ast.walk for both Import and ImportFrom forms. Reusable for any future 'this dependency belongs in only these files' boundary."
  - "Phase 6 conventions for HTMX swaps (alpine-morph), mobile vh (dvh), safe-area insets (viewport-fit=cover), and touch targets (--touch-target-min: 44px) — applies to every Phase 6/7/8 surface."
  - "Schema migration pattern: extend _migrate_add_columns dict + register new SQLModel imports inside init_db; create_all is idempotent, ALTER TABLE is column-existence-guarded, CREATE INDEX is IF NOT EXISTS guarded."

requirements-completed:
  - VIBE-01
  - VIBE-02
  - VIBE-03
  - OPS-06

# Metrics
duration: ~25min
completed: 2026-05-09
---

# Phase 6 Plan 01: Foundation Summary

**Four new SQLModel tables (Vibe, TrackVibe, ManagedPlaylist, SetupState) plus the Track.pending_slot_in column and three new indexes; project-wide HTMX/Alpine/dvh/safe-area/touch-target conventions wired into base.html + input.css; the AST sklearn-allowlist tripwire that gates Plan 02's clustering import.**

## Performance

- **Duration:** ~25 min (3 tasks, 3 commits)
- **Started:** 2026-05-09 (worktree branch worktree-agent-a5d0cffa077ecd885 created from main @ ffb9b02)
- **Completed:** 2026-05-09
- **Tasks:** 3 / 3 complete
- **Files created:** 9 (4 source + 4 test + 1 deferred-items + 2 JS bundles minus the SUMMARY)
- **Files modified:** 3 (app/database.py, app/templates/base.html, app/static/css/input.css)

## Accomplishments

- **Schema foundation for Plans 02-04 is in place.** Four Phase 6 tables registered in `init_db`; the `Track.pending_slot_in` column added via the existing `_migrate_add_columns` shim (additive only — Pitfall 19 honored). Three new indexes created including the partial `ix_track_pending_slot_in WHERE pending_slot_in = 1` for the post-analysis hook.
- **DB-side dual-marker enforcement.** `ManagedPlaylist.UNIQUE(plex_rating_key)` is the SQLite-layer guard for OPS-06 / D-27; the title-prefix `"Composer · "` check is the second marker, owned by service code in Plans 02-04.
- **Project-wide UI conventions land before any wizard surface ships.** `base.html` now declares `hx-ext="alpine-morph"` (Pitfall 15), uses `min-h-dvh` (Pitfall 16), and the viewport meta opts into `viewport-fit=cover` (Pitfall 16). `input.css` declares `--touch-target-min: 44px` (Pitfall 17 / UI-04). Two JS bundles (htmx-ext-alpine-morph + @alpinejs/morph) shipped under `app/static/js/` so the Docker container has no CDN dependency.
- **sklearn import boundary is gated.** `tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module` walks `app/services/*.py`, allowlists files matching `vibe_clusterer.py | clustering*.py | clusterer*.py`, and trips on both `import sklearn` and `from sklearn.X import Y` forms. Manually verified the test fails RED when sklearn is injected into a non-allowlisted file (and stays GREEN when added to a stub vibe_clusterer.py).
- **All Phase 5 invariants preserved.** `test_no_sklearn_import` (taste_profile_service-specific), `test_no_blocking_plexapi_in_async` (event_handlers async-PlexAPI), full `test_database` + `test_event_log` + `test_pages` suites still green.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add Vibe/TrackVibe/ManagedPlaylist/SetupState models + Phase 6 schema migration** — `27ac16e` (feat)
2. **Task 2: feature_chip_text helper + sklearn-allowlist AST tripwire** — `2d00992` (feat)
3. **Task 3: base.html alpine-morph + min-h-dvh + viewport-fit=cover; --touch-target-min token** — `b5a6318` (feat)

## Files Created/Modified

### Created
- `app/models/vibe.py` — four SQLModel tables (Vibe, TrackVibe, ManagedPlaylist, SetupState) per D-28; module docstring documents the dual-marker rule (D-27) and the `assigned_by` enum (D-19).
- `app/services/vibe_helpers.py` — pure helper module containing `feature_chip_text(centroid) -> str`; module-level threshold constants for energy/tempo/dance/valence; defensive None / empty / partial-dict handling.
- `app/static/js/alpine-morph.min.js` — htmx-ext-alpine-morph v2.0.1 (registers the htmx 'alpine-morph' extension; loaded synchronously after htmx).
- `app/static/js/alpine-morph-plugin.min.js` — @alpinejs/morph v3.14.9 (provides the Alpine.morph() API; loaded `defer` before alpine.min.js).
- `tests/test_database_phase6.py` — 7 tests covering table registration, fresh-DB column presence, idempotent re-run on Phase-5-shaped DB (Pitfall 19), TrackVibe round-trip with `assigned_by="auto-slot"`, ManagedPlaylist UNIQUE collision, partial-index WHERE clause verification, SetupState id=1 upsert via `session.merge`.
- `tests/test_vibe_helpers.py` — 7 tests covering full chip text, low/slow/high/low band, all-mid band, None / {} sentinels, partial dict (single dimension), and a separator-must-be-middle-dot guard.
- `tests/test_vibe_clusterer.py` — `test_sklearn_only_in_clusterer_module` AST allowlist tripwire.
- `tests/test_base_html_conventions.py` — 5 plain-string assertions guarding the project-wide conventions.
- `.planning/phases/06-vibe-clustering-setup-wizard/deferred-items.md` — log of 14 pre-existing unrelated test failures (verified on main; out of scope).

### Modified
- `app/database.py` — added Phase 6 model imports inside `init_db()` before `create_all`; extended `_migrate_add_columns` `new_columns` dict with `pending_slot_in INTEGER DEFAULT 0`; added three `CREATE INDEX IF NOT EXISTS` statements (including the partial index).
- `app/templates/base.html` — viewport meta gains `viewport-fit=cover`; body class `min-h-screen → min-h-dvh`; body gains `hx-ext="alpine-morph"`; two new `<script>` tags for the bundled morph adapter + plugin.
- `app/static/css/input.css` — `@theme` block gains `--touch-target-min: 44px`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Reverted `app/models/__init__.py` package-level re-exports.**
- **Found during:** Task 1 verification — `tests/test_database.py::test_init_db_creates_tables` started failing.
- **Issue:** I initially populated `app/models/__init__.py` with `from .vibe import ...` re-exports for SQLModel metadata registration. This caused `vibe.py` to load in any test that imports anything from `app.models`, which in turn registered Vibe/TrackVibe in `SQLModel.metadata` — but those tables have FKs to `track.id`, and the FK resolution at `create_all` time required Track to also be in metadata. Tests that imported only `ServiceConfig` (e.g., `test_init_db_creates_tables`) failed with `NoReferencedTableError`.
- **Fix:** Reverted `app/models/__init__.py` to empty. Phase 5 doesn't auto-export either; each call site imports the specific model module it needs. `init_db` does the explicit per-table imports inside the function.
- **Files modified:** `app/models/__init__.py` (reverted to empty).
- **Commit:** Bundled into `27ac16e`.

### Out of Scope (logged to deferred-items.md)

14 test failures exist on the unmodified main branch (verified by spot-checking three of them). They are unrelated to Phase 6 schema work and are tracked in `.planning/phases/06-vibe-clustering-setup-wizard/deferred-items.md`. NOT addressed in this plan per the scope-boundary rule.

## TDD Gate Compliance

This plan has type=`execute` (not `tdd`), so plan-level RED→GREEN→REFACTOR gate sequence is not required. Each task individually had `tdd="true"` and followed the per-task TDD cycle:

- **Task 1:** RED (test_database_phase6.py importing missing app.models.vibe → ModuleNotFoundError) → GREEN (created vibe.py + database.py edits → 7/7 pass).
- **Task 2:** RED (test_vibe_helpers.py importing missing app.services.vibe_helpers → ModuleNotFoundError; sklearn allowlist test passed trivially as expected) → GREEN (created vibe_helpers.py → 7/7 helper tests pass; allowlist test still green).
- **Task 3:** GREEN-direct (template + CSS file edits + new test file added together; no pre-existing test depended on the old `min-h-screen` class so RED would have been a no-op).

All commits use `feat(06-01)` prefix per project convention; per-task TDD cycle was collapsed into single `feat` commits because each task's RED/GREEN landed within the same execution turn (no intermediate state worth committing separately).

## Verification Run

Final verification suite (Phase 6 + Phase 5 invariants):

```
pytest tests/test_database_phase6.py tests/test_vibe_helpers.py \
       tests/test_vibe_clusterer.py tests/test_base_html_conventions.py \
       tests/test_database.py tests/test_event_log.py \
       tests/test_taste_profile_service.py tests/test_event_handlers.py \
       tests/test_pages.py tests/test_rating_helpers.py
=> 56 passed, 1 warning in 3.13s
```

Spot-check (per plan §5):

```
$ python -c "from app.database import init_db, get_engine, reset_engine; \
             reset_engine(); init_db(); init_db()"   # double-init must be idempotent
Double init_db OK

$ sqlite3 ... "PRAGMA index_list('trackvibe'); PRAGMA index_list('managedplaylist'); \
              PRAGMA index_list('track');" | grep -E ...
ix_trackvibe_vibe_id
ix_managedplaylist_kind
ix_track_pending_slot_in
```

AST tripwire boundary verification (per success criteria):

```
$ # Inject sklearn into rating_helpers.py
$ printf 'import sklearn  # TEMP\n' >> app/services/rating_helpers.py
$ pytest tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module -x
FAILED  rating_helpers.py: import sklearn  # AS EXPECTED — boundary enforced

$ # Try the "from sklearn.X import Y" form too
$ printf 'from sklearn.cluster import KMeans  # TEMP\n' >> app/services/rating_helpers.py
$ pytest tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module -x
FAILED  rating_helpers.py: from sklearn.cluster  # AS EXPECTED

$ # Verify allowlist permits vibe_clusterer.py
$ echo "from sklearn.cluster import KMeans" > app/services/vibe_clusterer.py
$ pytest tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module -x
PASSED  # AS EXPECTED — vibe_clusterer.py is on the allowlist

$ rm app/services/vibe_clusterer.py  # cleanup
$ # Restore rating_helpers.py from .bak
```

## Plan 02 Unblocked

- **Schema is in place** — `vibe_service.py` can write TrackVibe rows, `vibe_clusterer.py` can populate Vibe centroids, `plex_playlist_service.py` can persist `ManagedPlaylist` rows.
- **AST allowlist tripwire is live** — `vibe_clusterer.py` can land `from sklearn.cluster import KMeans` knowing other service files cannot accidentally do the same.
- **`feature_chip_text` is ready** — Plan 03's wizard templates and Plan 04's `/debug/vibes` page can render centroids without re-implementing the threshold logic.
- **base.html conventions are live** — Plan 03's refinement-loop card swaps can use `hx-swap="morph:innerHTML"` immediately; sticky-bottom CTAs can use `pb-[calc(16px+env(safe-area-inset-bottom))]`; touch-target buttons can use `min-h-11 min-w-11`.

## Self-Check: PASSED

Files referenced in this SUMMARY were verified to exist immediately after writing:

- `app/models/vibe.py` — FOUND (4 SQLModel classes: `grep -c "class .*(SQLModel, table=True):" app/models/vibe.py` = 4)
- `app/services/vibe_helpers.py` — FOUND (`grep -c "def feature_chip_text" app/services/vibe_helpers.py` = 1)
- `app/static/js/alpine-morph.min.js` — FOUND (505 bytes — htmx-ext adapter)
- `app/static/js/alpine-morph-plugin.min.js` — FOUND (4079 bytes — Alpine.morph plugin)
- `tests/test_database_phase6.py` — FOUND (7 tests)
- `tests/test_vibe_helpers.py` — FOUND (7 tests)
- `tests/test_vibe_clusterer.py` — FOUND (1 test)
- `tests/test_base_html_conventions.py` — FOUND (5 tests)

Commits referenced in this SUMMARY were verified via `git log --oneline`:

- `27ac16e` — FOUND (Task 1)
- `2d00992` — FOUND (Task 2)
- `b5a6318` — FOUND (Task 3)
