---
phase: 08-lidarr-discovery-polish
plan: 01
subsystem: lidarr-foundation
tags: [lidarr, sync-reliability, discovery-schema, mobile-first-prereq, ops]
requires:
  - phase 5 D-09 (asyncio.to_thread wrapping; AST enforcement)
  - phase 5 D-07 (EventLog dedupe via sha256 + 5min bucket)
  - phase 6.1 MigrationLog gate-row pattern
  - phase 7.0 run_phase_07_suggestions_bootstrap shape
  - phase 7.1 _weekly_maintenance_tick + DiscoveryState catch-up
provides:
  - lidarr_client.test_lidarr_connection returning {quality_profiles, metadata_profiles, root_folders}
  - lidarr_client.add_artist with BOTH profile IDs + root_dir + search_for_missing_albums=True
  - lidarr_client.get_recent_history for D-C3 lazy-poll
  - sync_scheduler.schedule_sync using CronTrigger anchored at 03:00 UTC for 24h/12h/6h cadences
  - sync_scheduler.start_scheduler library-sync missed-tick catch-up gate
  - sync_service._record_sync_failure_event writing sync_failed EventLog rows
  - app/models/discovery.py — six new SQLModel tables
  - Vibe.color column + locked Tailwind 4 -500 palette
  - Track.plex_artist_mbid column (Pitfall 12 cross-surface dedup foundation)
  - discovery_service.run_phase_08_discovery_bootstrap (MigrationLog-gated lifespan)
  - discovery_service.VIBE_COLOR_PALETTE + assign_vibe_color
  - plex_client.get_artist_mbid_by_name sync helper
affects:
  - app/routers/api_settings.py (save_lidarr extras shape — 5 keys + back-compat mirror)
  - app/templates/partials/connection_status.html (3 dropdowns for Lidarr)
  - app/main.py lifespan (Phase 8 bootstrap after Phase 7 bootstrap)
  - tests/conftest.py (db_with_phase7 fixture registers discovery models)
tech-stack:
  added:
    - musicbrainzngs>=0.7.1,<1.0 (Plan 02 dep pinned now so Docker rebuilds include it)
  patterns:
    - CronTrigger anchored to wall-clock UTC over IntervalTrigger
    - coalesce=True + misfire_grace_time=3600 + max_instances=1
    - MigrationLog gate-row + in-flight NULL marker for crash-recovery
    - sha256(event_type|err|5min_bucket) dedupe with INSERT OR IGNORE
key-files:
  created:
    - app/models/discovery.py
    - app/services/discovery_service.py
    - tests/test_phase_08_discovery_bootstrap.py
    - tests/test_lidarr_client.py
  modified:
    - app/services/lidarr_client.py
    - app/services/sync_scheduler.py
    - app/services/sync_service.py
    - app/services/plex_client.py
    - app/routers/api_settings.py
    - app/templates/partials/connection_status.html
    - app/database.py
    - app/models/vibe.py
    - app/models/track.py
    - app/main.py
    - tests/conftest.py
    - tests/test_event_handlers.py
    - tests/test_sync_scheduler.py
    - tests/test_sync_service.py
    - tests/test_service_clients.py
    - requirements.txt
decisions:
  - "test_lidarr_connection fetches all three lists in ONE asyncio.to_thread via _fetch_lidarr_test_payload helper (D-E1 'halve round-trips' intent)."
  - "save_lidarr persists 5 extras keys PLUS back-compat 'profile_id'/'profile_name' mirror of the quality_* values so legacy readers don't break."
  - "Root-folder dropdown is conditional (>1 folders only); single folder uses a hidden input so users with one music root don't see a meaningless single-option select."
  - "schedule_sync CronTrigger for 24/12/6 cadences (CronTrigger fires anchored at 03 UTC; 12h fires at 3,15; 6h fires at 3,9,15,21). Non-standard intervals fall back to IntervalTrigger."
  - "Library-sync catch-up gate uses 15s delay (vs the existing first-run 10s) so the first-run path takes precedence when SyncState is empty AND the catch-up gate fires after both blocks complete cleanly."
  - "Sync-failed EventLog row uses raw_payload=None (the EventLog model field is raw_payload, NOT payload_preview as the plan called it)."
  - "_backfill_track_artist_mbids_sync is wrapped in its own try/except inside the bootstrap so a Plex outage doesn't roll back the baseline + weekly_cron_state + vibe colors (D-E2 wants those even when Plex is down)."
  - "VIBE_COLOR_PALETTE includes orange-500 (#f97316) as the LAST entry — D-E2 collision-avoidance with the Plex accent #e5a00d."
  - "AST static test paths list uses path.exists() so Plan 02 files (musicbrainz_client.py / listenbrainz_client.py / api_discovery.py) are skipped gracefully until Plan 02 creates them."
  - "Track.plex_artist_mbid is index=True (Plan 02 _get_in_library_mbids_sync will filter by NULL/value; an index helps both lookups)."
metrics:
  duration_minutes: 11
  completed: 2026-05-17
---

# Phase 8 Plan 01: Lidarr Foundation + Sync Reliability + Discovery Schema Summary

Foundational Phase 8 plumbing. After this lands, every downstream Phase 8 plan can rest on a reliable daily sync, a Lidarr connection-test that returns the data `add_artist()` actually needs, and the schema/lifespan migration the rest of the phase will read from and write to.

## What Now Holds

1. **Lidarr connection test returns three lists in ONE async hop.** `test_lidarr_connection` now returns `{quality_profiles, metadata_profiles, root_folders}` via a single `_fetch_lidarr_test_payload` helper called through `asyncio.to_thread`. The existing 4-tier error ladder (auth / timeout / refused / generic) is preserved verbatim.
2. **`add_artist` requires BOTH profile IDs + `root_dir`** (Pitfall 14 / the v1 bug fix). `search_for_missing_albums=True` is intentional so post-add monitoring has real Lidarr activity to observe within the 24h post-add window.
3. **`get_recent_history` is the D-C3 lazy-poll source** for `/discover` status rows + `/debug/discovery` timeline (best-effort: returns `[]` on any error so the page render never blocks).
4. **Settings save flow persists 5 Lidarr extras keys** (`quality_profile_id`/`_name`, `metadata_profile_id`/`_name`, `root_folder_path`) plus a back-compat mirror of `profile_id`/`profile_name` so any legacy reader keeps working. Settings UI now renders three dropdowns; root-folder dropdown only appears when Lidarr reports >1 root (auto-selected silently otherwise via a hidden input).
5. **Library-sync schedule is wall-clock-anchored.** `schedule_sync(24)` registers `CronTrigger(hour=3, minute=0, timezone="UTC")` — not `IntervalTrigger`. 12h fires at 03:00 + 15:00 UTC, 6h fires at 03/09/15/21 UTC. All cron jobs use `coalesce=True` + `misfire_grace_time=3600` + `max_instances=1` so missed ticks (NAS reboot, container restart) collapse into ONE catch-up. Non-standard intervals (e.g. 7h) fall back to `IntervalTrigger`.
6. **Lifespan startup catch-up: library-sync edition.** A new catch-up gate (placed AFTER first-run auto-sync, BEFORE the Phase 7.1 discovery catch-up) fires a 15s-delayed `run_sync()` when `now - last_sync_completed > interval_hours + 1h grace`. Resilient to the NAS UAT 2026-05-16 scenario where the schedule went 48h stale on a 24h cadence.
7. **Silent sync failures now surface on `/debug/events`.** `_record_sync_failure_event` writes a `sync_failed` EventLog row using the Phase 5 D-07 dedupe pattern: `sha256(event_type|error|5min_bucket)` + `INSERT OR IGNORE` on the UNIQUE constraint. Burst failures from the same root cause produce ONE row; distinct failures across buckets are individually recorded.
8. **Phase 8 schema is in place.** `Vibe.color`, `Track.plex_artist_mbid`, and six new tables (`DiscoveryCandidate`, `DiscoveryAdd`, `DiscoveryDismissed`, `MusicBrainzCache`, `CostMeterBaseline`, `WeeklyCronState`) materialise on fresh DBs via `SQLModel.metadata.create_all`; existing DBs get the columns through additive `ALTER TABLE` blocks in `_migrate_add_columns`.
9. **Lifespan bootstrap is idempotent.** `run_phase_08_discovery_bootstrap` is gated by `MigrationLog(phase_id='8.0-discovery-bootstrap')`. First run stamps `CostMeterBaseline(deploy_at=now)`, seeds `WeeklyCronState(id=1)`, backfills `Vibe.color` from the locked Tailwind 4 -500 palette, and best-effort-backfills `Track.plex_artist_mbid` via `plex_client.get_artist_mbid_by_name`. Plex outages are isolated (the baseline / weekly_cron / colors still persist).
10. **`musicbrainzngs>=0.7.1,<1.0` pinned in `requirements.txt`** so the Docker rebuild for Plan 01 already includes the dep Plan 02 needs.
11. **AST static test extended.** `tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async` now scans `discovery_service.py` + `lidarr_client.py` (and is forward-compatible with Plan 02 files via `path.exists()`). New forbidden names: `add_artist`, `lookup_artist`, `get_quality_profile`, `get_metadata_profile`, `get_root_folder`, `get_history`, `get_artist_by_id`.

## File-by-File Changes

### Created

| File | Purpose |
|------|---------|
| `app/models/discovery.py` | 6 SQLModel tables (DiscoveryCandidate, DiscoveryAdd, DiscoveryDismissed, MusicBrainzCache, CostMeterBaseline, WeeklyCronState). Registered in `app/database.py::init_db`. |
| `app/services/discovery_service.py` | PHASE_08_MIGRATION_ID, VIBE_COLOR_PALETTE (9-tuple, orange LAST), assign_vibe_color, ArtistDiscoveryStatus singleton, run_phase_08_discovery_bootstrap, _bootstrap_baseline_sync, _bootstrap_weekly_cron_state_sync, _backfill_vibe_colors_sync, _backfill_track_artist_mbids_sync. |
| `tests/test_phase_08_discovery_bootstrap.py` | 18 tests covering schema, table creation, UNIQUE constraints, bootstrap behaviour, idempotency, palette lock, plex_artist_mbid backfill (success / failure / idempotency). |
| `tests/test_lidarr_client.py` | 13 tests for test_lidarr_connection (3-list shape + single to_thread + error tiers), add_artist (kwargs + empty-lookup), get_recent_history (records + tolerates errors), save_lidarr (5 keys + back-compat), connection_status.html (3 dropdowns + conditional root). |

### Modified

| File | Change |
|------|--------|
| `app/services/lidarr_client.py` | Replaced `test_lidarr_connection` body; added `_fetch_lidarr_test_payload`, `add_artist`, `get_recent_history`. |
| `app/services/sync_scheduler.py` | `schedule_sync` switched to CronTrigger (24/12/6); added library-sync catch-up gate in `start_scheduler` (placed between first-run path and discovery catch-up). |
| `app/services/sync_service.py` | Except block now calls `_record_sync_failure_event` (best-effort); added the helper with sha256 + 5min bucket dedupe via INSERT OR IGNORE. |
| `app/services/plex_client.py` | Added `get_artist_mbid_by_name` sync helper (reads ServiceConfig Plex creds, calls `section.searchArtists(title=name)`, parses `mbid://<uuid>` from `.guid` or `.guids`). |
| `app/routers/api_settings.py` | `test_lidarr` renders 3 dropdowns; `save_lidarr` uses `Annotated[str, Form()]` for 7 fields and persists 5 extras + back-compat mirror. |
| `app/templates/partials/connection_status.html` | Replaced single Lidarr quality-profile dropdown with three dropdowns (quality + metadata always; root only when >1, hidden input when 1). |
| `app/database.py` | Two new guarded ALTER blocks (vibe.color + track.plex_artist_mbid); registered 6 new Discovery models in `init_db`. |
| `app/models/vibe.py` | Added `color: Optional[str]` field to `Vibe`. |
| `app/models/track.py` | Added `plex_artist_mbid: Optional[str]` with `index=True`. |
| `app/main.py` lifespan | Added `await run_phase_08_discovery_bootstrap()` after `run_phase_07_suggestions_bootstrap`. |
| `tests/conftest.py` | `db_with_phase7` fixture registers the 6 new Discovery models. |
| `tests/test_event_handlers.py` | `TestStaticAnalysis::test_no_blocking_plexapi_in_async` paths + forbidden_names extended. |
| `tests/test_sync_scheduler.py` | Added `TestScheduleSyncCronTrigger` (5 tests) + `TestStartSchedulerLibrarySyncCatchUp` (3 tests). |
| `tests/test_sync_service.py` | Added `TestSyncFailureWritesEventLog` (3 tests). |
| `tests/test_service_clients.py` | Updated existing `test_success_returns_quality_profiles` to the new `quality_profiles` return shape. |
| `requirements.txt` | Pinned `musicbrainzngs>=0.7.1,<1.0`. |

## Deviations from Plan

**1. [Rule 1 — Bug] `EventLog.raw_payload` (not `payload_preview`)**
- **Found during:** Task 2 implementation.
- **Issue:** The plan's example code wrote `payload_preview=None` in the `_record_sync_failure_event` helper, but the actual `EventLog` model exposes `raw_payload`. Using the plan's literal name would have raised a `TypeError` at construction time.
- **Fix:** Used `raw_payload=None` to match the real model field.
- **Files modified:** `app/services/sync_service.py`.
- **Commit:** `a376745`.

**2. [Rule 2 — Missing critical] Updated `test_service_clients.py::test_success_returns_quality_profiles`**
- **Found during:** Task 1 verification.
- **Issue:** The pre-existing test asserted `result["profiles"]` against the v1 `test_lidarr_connection` return shape; my change to return `{quality_profiles, metadata_profiles, root_folders}` would have left this test broken.
- **Fix:** Updated the existing test to use `result["quality_profiles"]` and seeded the mock with `get_metadata_profile.return_value = []` + `get_root_folder.return_value = []` so the call path completes.
- **Files modified:** `tests/test_service_clients.py`.
- **Commit:** `6b6745f`.

**3. [Rule 2 — Missing critical] Added `Optional` import + `get_artist_mbid_by_name` helper to `plex_client.py`**
- **Found during:** Task 3 implementation.
- **Issue:** The plan's `_backfill_track_artist_mbids_sync` calls `plex_client.get_artist_mbid_by_name(name)` as a sync helper. The plan explicitly noted (Step 3a NOTE TO EXECUTOR) that if the helper didn't yet exist, the executor should add it. It did not exist on disk.
- **Fix:** Added a sync `get_artist_mbid_by_name(name) -> Optional[str]` helper that reads Plex credentials from `ServiceConfig`, calls `section.searchArtists(title=name)`, and parses MBIDs from `.guid` or `.guids` (regex on `mbid://<uuid>`). Returns `None` on any failure so the bootstrap caller can keep going.
- **Files modified:** `app/services/plex_client.py`.
- **Commit:** `16be169`.

## Authentication Gates

None. All work was code-level; no external credentials needed during execution.

## Pointers for Plan 02

Plan 02 builds the actual discovery pipeline on top of this foundation:

- `discovery_service.py` already exposes `VIBE_COLOR_PALETTE`, `assign_vibe_color`, `PHASE_08_MIGRATION_ID`, `get_state()`, and the `ArtistDiscoveryStatus` singleton. Plan 02 adds `compute_candidate_set_for_seed`, `artist_discovery_call_weekly`, and the `DISCOVERY_ARTIST_*` cap constants (mirror Phase 7.1 SUGG-14 shape).
- `Track.plex_artist_mbid` is the canonical source of truth for the Pitfall 12 cross-surface dedup gate. Plan 02's `_get_in_library_mbids_sync` should filter `WHERE plex_artist_mbid IS NOT NULL` (NULL rows are treated as "unknown" — at most one stale duplicate appears on `/discover` until the next bootstrap retry).
- `lidarr_client.add_artist` and `get_recent_history` are ready to wire into `discovery_service.add_artist_to_lidarr()` (Plan 04 UI work) and `/discover` lazy-poll (D-D4 lifecycle) respectively. Both read the 5 extras keys persisted by Plan 01's `save_lidarr`.
- `EventLog(event_type='sync_failed')` rows now surface on `/debug/events` (Phase 5 DEBUG-01); Plan 03 / Plan 05 don't need to add a separate failure surface.
- `WeeklyCronState(id=1, last_tick_at)` is the home-page cost chip's "next refresh in Nd" anchor (D-B4). Plan 03 / Plan 05 read this; Plan 02's `_weekly_maintenance_tick` extension (when it lands) must stamp it on successful completion.
- `CostMeterBaseline(id=1, deploy_at)` is the home-page chip's lower-bound filter for LLMUsage. Stable across restarts — Plan 03 / Plan 05 can read it directly.
- The AST static test already covers `discovery_service.py` and `lidarr_client.py`. Plan 02's `musicbrainz_client.py`, `listenbrainz_client.py`, and `api_discovery.py` will activate the existing path entries automatically (path.exists() guard).

## Plan 01 Verification

Run the suite:

```bash
cd /Users/Oreo/Projects/Composer && .venv/bin/pytest \
  tests/test_lidarr_client.py \
  tests/test_sync_scheduler.py \
  tests/test_sync_service.py \
  tests/test_phase_08_discovery_bootstrap.py \
  tests/test_event_handlers.py::TestStaticAnalysis \
  tests/test_service_clients.py \
  --tb=short
```

Result: **83 passed, 5 pre-existing failures** (all `discoverystate` table-not-created in standalone scheduler/service tests that bypass `init_db`; failures present on `cb8cd65` base before Plan 01 — verified via `git stash` round-trip).

## Deferred Issues

Pre-existing failures unrelated to Plan 01 (verified against base `cb8cd65`):

1. `tests/test_analysis_service.py::TestRunAnalysis::test_skips_oversized_files` — assertion expects `extract_features` to not be called; behaviour drift.
2. `tests/test_audio_analyzer.py::TestExtractFeatures::test_returns_all_feature_keys` + `test_normalizes_danceability` + `test_returns_correct_key_and_scale` — Essentia loadable on darwin but feature shape drift.
3. `tests/test_chat_service.py::TestProcessMessage::test_ollama_not_configured` + 3 siblings — Phase 7 chat-retirement test scaffolding.
4. `tests/test_sync_api.py::TestStartSync::test_start_sync_launches_background_task` — TestClient lifespan ordering on the suggestions bootstrap.
5. `tests/test_sync_scheduler.py::TestStartScheduler::test_triggers_auto_sync_when_no_prior_sync` + `TestStopScheduler::test_shuts_down_without_error` + `TestUpdateSyncSchedule::test_updates_running_scheduler` — these tests bypass `init_db` so the `discoverystate` table isn't created when `start_scheduler` reads it.
6. `tests/test_sync_service.py::TestRunSync::test_delta_sync_when_last_sync_exists` + `test_fallback_to_full_sync_when_delta_returns_zero` — same root cause as (5); standalone tests that don't go through the `fresh_db` / `db_with_phase7` fixture path.

None of these block Plan 02 — they're test-infrastructure issues, not production-code issues. Production lifespan goes through `init_db()` so `discoverystate` is always present.

## Self-Check: PASSED

Created files verified to exist:
- `app/models/discovery.py` — FOUND
- `app/services/discovery_service.py` — FOUND
- `tests/test_phase_08_discovery_bootstrap.py` — FOUND
- `tests/test_lidarr_client.py` — FOUND

Commits verified:
- `6b6745f` (Task 1: Lidarr connection-test extension) — FOUND
- `a376745` (Task 2: CronTrigger + catch-up + sync_failed EventLog) — FOUND
- `16be169` (Task 3: Phase 8 schema + bootstrap) — FOUND

Done-criteria grep gates all pass for Task 1 / Task 2 / Task 3 (verified inline during execution).
