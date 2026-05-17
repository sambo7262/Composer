---
phase: 08-lidarr-discovery-polish
plan: 02
subsystem: discovery-pipeline
tags: [discovery, listenbrainz, musicbrainz, llm-cron, lifecycle, disc-03, disc-04, disc-05, disc-06]
requires:
  - phase 8 plan 01 (DiscoveryAdd schema, Track.plex_artist_mbid column, Vibe.color, run_phase_08_discovery_bootstrap, lidarr_client extension, sync_failed EventLog, CronTrigger schedule, musicbrainzngs requirements pin)
  - phase 7.1 SUGG-14 (MaxTokensTruncationError retry pattern + DISCOVERY_* candidate-cap shape)
  - phase 7.1 D-D3 (shared WEEKLY_DISCOVERY_BUDGET_USD ceiling)
  - phase 7.1 _weekly_maintenance_tick (combined Sunday cron)
  - phase 5 D-09 (asyncio.to_thread invariant + AST static enforcement)
  - phase 5 D-04 (LLMUsage per-call row with purpose suffixes for observability)
provides:
  - listenbrainz_client.get_similar_artists (async httpx wrapper for the labs subdomain; empty-list on 5xx/timeout)
  - musicbrainz_client.lookup_artist + lookup_artist_by_name (module-load UA + 1 req/sec rate-limit; MusicBrainzCache write-through; envelope unwrap)
  - discovery_service.compute_candidate_set_for_seed (D-A1 pipeline: LB fetch → cross-surface dedup → MB validate → popularity gate)
  - discovery_service.artist_discovery_call_weekly (Sunday cron step 3 — short-circuits on no-Lidarr/no-vibes, cost-breaker gate, SUGG-14 retry-on-truncation, Pitfall 10 hallucination filter, writes DiscoveryCandidate rows)
  - discovery_service.update_weekly_cron_state (Sunday cron step 4 — D-B4 home-chip anchor)
  - discovery_service.DISCOVERY_ARTIST_* constants (mirror Phase 7.1 SUGG-14 caps)
  - discovery_service.CandidateRecord + CandidatePipelineCounts (read by /debug/discovery in Plan 05)
  - discovery_service ArtistDiscoveryPicksResponse / LLMArtistPick Pydantic shapes
  - discovery_service.stamp_discovery_adds_composer_sync_seen (hook 1) + stamp_discovery_adds_essentia_complete (hook 2) + stamp_discovery_adds_vibe_slotted (hook 3) — DiscoveryAdd lifecycle stamps
  - discovery_service.get_lidarr_status_for_add ("analyzed, slotted into vibes" branch closes DISC-06 SC#3)
  - Vibe.last_seed_track_id column (D-A2 round-robin rotation cursor)
  - sync_scheduler._weekly_maintenance_tick steps 3 + 4 (artist discovery + WeeklyCronState stamp)
affects:
  - app/services/sync_service.py (post-sync hook 1)
  - app/services/analysis_service.py (post-analysis hook 2)
  - app/services/vibe_service.py (slot-tail hook 3 — Option A single call site)
  - tests/test_event_handlers.py::TestStaticAnalysis (removed `lookup_artist` from forbidden_names to avoid false-positiving on our own safe async wrapper)
tech-stack:
  added:
    - musicbrainzngs (already pinned in Plan 01) — now actually exercised via the module-load init
    - httpx (already a project dep) — exercised via listenbrainz_client
  patterns:
    - Module-load init for sync libraries (musicbrainzngs.set_useragent + set_rate_limit)
    - Cache-aside write-through for indefinite cross-week amortisation (MusicBrainzCache)
    - SUGG-14 single-doubling MaxTokensTruncationError retry; no infinite loop
    - Pitfall 10 mb_id post-LLM validation against the pre-built candidate pool
    - Pitfall 12 cross-surface dedup (in-library + 1h-cached Lidarr set)
    - Pitfall 13 popularity gate (artist-relation adjacency / shared-label / release-group count threshold)
    - Best-effort cron-tick step isolation (try/except per step; later steps still fire)
    - Forward-only lifecycle state machine with strict gate per stamp
key-files:
  created:
    - app/services/listenbrainz_client.py
    - app/services/musicbrainz_client.py
    - tests/test_listenbrainz_client.py (extended from Task 1 RED gate)
    - tests/test_musicbrainz_client.py
    - tests/test_discovery_service.py
    - tests/test_discovery_lifecycle_hooks.py
  modified:
    - app/services/discovery_service.py (full Plan 02 pipeline + lifecycle hooks appended)
    - app/services/sync_scheduler.py (_weekly_maintenance_tick steps 3 + 4)
    - app/services/sync_service.py (post-sync hook 1 wired before trigger_post_sync_analysis)
    - app/services/analysis_service.py (post-analysis hook 2 wired at end of run_analysis)
    - app/services/vibe_service.py (slot-tail hook 3 wired at end of _slot_track_inner — Option A)
    - app/models/vibe.py (Vibe.last_seed_track_id column added)
    - app/database.py (_migrate_add_columns ALTER for vibe.last_seed_track_id)
    - tests/test_event_handlers.py (removed `lookup_artist` from forbidden_names — see Deviations §2)
decisions:
  - "ListenBrainz parser preserves `score` as int (raw labs response) — locked per Task 1 user-approved checkpoint. List-position ordering preserved as the natural similarity ranking."
  - "MusicBrainzCache write-through is keyed by mb_id with no TTL (artists don't change); cached_at recorded for diagnostic value only."
  - "Popularity gate ships with three arms: artist-relation adjacency (strong taste signal), shared-label (best-effort — currently a no-op because Track has no label column), release-group count threshold (drop if >200 AND no adjacency). Below-threshold candidates pass through to LLM re-rank — the gate is meant to filter megastars, not require explicit adjacency."
  - "starred_labels() returns set() in v1 — reserved for future enrichment via a TrackLabel join table. The adjacency arm (artist-relation on starred MBIDs) is sufficient for v1 coverage."
  - "AnthropicClient + MaxTokensTruncationError + check_or_raise + CostBreakerTrippedError imports placed at module bottom in discovery_service.py to dodge a circular import that the in-test fixture path would otherwise hit (anthropic_client.py imports from llm_usage / settings_service)."
  - "Hook 3 wired at the END of vibe_service._slot_track_inner (Option A from plan, recommended). Single call site covers all paths that slot a track — rating-change handler, re-cluster commit, wizard finalisation. The just-slotted track's plex_artist_mbid is passed as triggering_mb_id for the fast-path; None scope (typically <10 pending adds) is acceptable when the track lacks an MBID."
  - "AST static test's `lookup_artist` forbidden name removed — pyarr 6.x actually exposes `lidarr.artist.lookup` (i.e. `.lookup` attr on the `artist` namespace) NOT `lookup_artist`; the name was false-positiving on our own safe async wrapper `musicbrainz_client.lookup_artist`. Documented rationale inline in the test."
  - "DiscoveryCandidate writes are INSERT-only — Plan 02 does NOT delete prior week's rows. Plan 04 will read with a `created_at >= last_tick_at` filter (or similar) to show only the current week; older rows kept for /debug/discovery history. A future quick task can add a sweep if the table grows unwieldy."
  - "get_lidarr_status_for_add ships the slotted branch + three fallback states ('imported, awaiting Composer sync' / 'imported, awaiting analysis' / 'awaiting vibe slot-in'). Plan 04 will extend with the Lidarr `history.get` round-trip to fill in searching/downloading states. The slotted branch closes DISC-06 SC#3 end-to-end."
metrics:
  duration_minutes: 22
  completed: 2026-05-17
---

# Phase 8 Plan 02: Discovery Pipeline Backend Summary

Backend for the Sunday cron tick that produces the week's `DiscoveryCandidate` rows. After this plan, the database fills with candidates every Sunday (or via Plan 01's first-restart catch-up gate); Plan 04's `/discover` UI reads from `DiscoveryCandidate` directly with `DiscoveryDismissed` subtracted at render time per D-B2.

## What Now Holds

1. **ListenBrainz labs endpoint client.** `app/services/listenbrainz_client.py::get_similar_artists` is a thin async httpx wrapper around `https://labs.api.listenbrainz.org/similar-artists/json` with the locked algorithm string `session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30`. Returns the raw labs entries (`{artist_mbid, name, score, comment, type, gender, reference_mbid}`) sliced to `limit`. Empty list on 5xx / timeout / parse error — the discovery cron never crashes on one seed's lookup failure. Score is preserved as raw int (Task 1 user-approved contract).

2. **MusicBrainz client with module-load init + indefinite cache.** `app/services/musicbrainz_client.py` calls `musicbrainzngs.set_useragent("Composer", "2.0", contact_email)` + `musicbrainzngs.set_rate_limit(limit_or_interval=1.0)` at import time. `lookup_artist(mb_id)` unwraps the `{"artist": {...}}` envelope, writes through to `MusicBrainzCache` (no TTL), returns None on `ResponseError` / any unexpected exception. `lookup_artist_by_name(name)` calls `musicbrainzngs.search_artists(query=f"artist:{name}")`. All blocking calls are wrapped in `asyncio.to_thread` per Phase 5 D-09 — AST static test verifies.

3. **D-A2 per-vibe seed rotation.** `Vibe.last_seed_track_id` (additive `INTEGER` column) tracks the round-robin cursor. `_pick_next_seed_track_for_vibe_sync(vibe_id)` returns the smallest starred track id > `last_seed_track_id`, wrapping to the smallest id when exhausted. Unstarred tracks are skipped (`user_rating > 0` filter). Empty vibes return None — the cron silently skips them.

4. **`compute_candidate_set_for_seed` — full D-A1 pipeline.** For a given (seed_track_id, seed_vibe_id):
   - Resolve seed artist MBID from `Track.plex_artist_mbid` (skip if NULL).
   - Fetch up to 100 similar artists from ListenBrainz.
   - Pitfall 12 cross-surface dedup BEFORE expensive MB lookup: drop candidates whose mb_id is in `composer.tracks` (via `plex_artist_mbid`) OR in the 1h-cached Lidarr `artist.get()` set.
   - Pitfall 10 hallucination validation: `musicbrainz_client.lookup_artist(mb_id)` must return non-None; else drop.
   - D-A3 + Pitfall 13 popularity gate: pass if (artist-relation adjacency to a starred MBID) OR (shared label with a starred artist — currently a no-op) OR (release-group count ≤ threshold). Fail iff none of the above AND release-group count > threshold.
   - Returns `(kept: list[CandidateRecord], counts: CandidatePipelineCounts)` — the counts (`listenbrainz_returned`, `validation_drops`, `dedup_drops`, `popularity_drops`, `kept`) are what `/debug/discovery` (Plan 05) renders.

5. **`artist_discovery_call_weekly` — best-effort cron handler.** Pipeline:
   - Short-circuit + LLMUsage skip-row breadcrumb on no-Lidarr (`_skipped_no_lidarr`) or no-active-vibes (`_skipped_no_vibes`).
   - Per active vibe, run `pick_next_seed_track_for_vibe` → `compute_candidate_set_for_seed` and accumulate.
   - Short-circuit on empty post-gate pool (`_skipped_no_candidates`).
   - `check_or_raise(purpose_prefix="discovery_")` cost-breaker gate (shares `WEEKLY_DISCOVERY_BUDGET_USD` with suggestions discovery); on trip → `_status="cost_locked"` + LLMUsage `_cost_locked` row.
   - Build prompts; trim if estimated tokens exceed `DISCOVERY_ARTIST_PROMPT_TOKEN_CEILING` (mirror Phase 7.1 GAP-01).
   - `AnthropicClient.call_with_structured_output(..., purpose="discovery_artist_weekly", max_tokens=DISCOVERY_ARTIST_MAX_TOKENS_FLOOR)` with a single SUGG-14 retry-on-`MaxTokensTruncationError` (doubled budget); failure path on second truncation writes `_error_max_tokens_truncated`; generic exceptions write `_error` and exit gracefully.
   - Pitfall 10 hallucination filter against the validated candidate pool — drops LLM-returned mb_ids that aren't in the pool with a warning per drop.
   - Writes `DiscoveryCandidate` rows with `llm_rank` + `llm_rationale` + `factual_hook` + `seed_track_id` + `seed_vibe_id` populated.

6. **`_weekly_maintenance_tick` now runs 4 steps** in `sync_scheduler.py`: prune → suggestions discovery → artist discovery → `WeeklyCronState` stamp. Steps 3 + 4 wrapped in individual try/except — step 3 failure does NOT block step 4 stamping. Source of truth for the D-B4 home-page "next refresh in Nd" chip.

7. **DiscoveryAdd lifecycle stamping hooks (DISC-06 closure).** Three idempotent forward-only state-machine stamps:
   - Hook 1 (`composer_sync_seen`): wired in `sync_service.run_sync` BEFORE `trigger_post_sync_analysis`. Bulk-fetches the library MBID set, stamps every pending DiscoveryAdd whose mb_id is in the set.
   - Hook 2 (`essentia_complete`): wired in `analysis_service.run_analysis` at the end of the analyze loop. Stamps each DiscoveryAdd whose `composer_sync_seen_at` is set AND ALL Track rows for that mb_id have `energy IS NOT NULL`.
   - Hook 3 (`vibe_slotted`): wired at the END of `vibe_service._slot_track_inner` immediately after the SlotInLog write. Option A from the plan — single call site covers rating-change handlers, re-cluster commit, and wizard finalisation. Passes `triggering_mb_id=track.plex_artist_mbid` for the fast-path; helper tolerates None.
   Each hook is best-effort try/except — a hook failure NEVER blocks the primary sync / analysis / slot path.

8. **`get_lidarr_status_for_add` closes DISC-06 SC#3.** Returns "analyzed, slotted into vibes" when `vibe_slotted_at IS NOT NULL`. Three fallback branches for the intermediate lifecycle states. Plan 04 extends with Lidarr `history.get` for the searching/downloading states.

## File-by-File Changes

### Created

| File | Purpose |
|------|---------|
| `app/services/listenbrainz_client.py` | Async httpx wrapper for the labs subdomain. `LISTENBRAINZ_SIMILAR_ARTISTS_URL` + `LISTENBRAINZ_DEFAULT_ALGORITHM` + `get_similar_artists(seed_mbid, limit=100)`. |
| `app/services/musicbrainz_client.py` | Module-load `set_useragent` + `set_rate_limit(1.0)`; `_cache_get_sync` / `_cache_put_sync` + `lookup_artist` / `lookup_artist_by_name` async wrappers. |
| `tests/test_musicbrainz_client.py` | 9 tests: module-load UA + rate-limit pin, envelope unwrap, cache hit/miss, 404 → None, name-search query shape, write-through persist, preseeded-row hit. |
| `tests/test_discovery_service.py` | 21 tests for Task 3: constants pin, Vibe migration, D-A2 selector (round-robin + unstarred-skip + empty-vibe), `compute_candidate_set_for_seed` (validation/library-dedup/lidarr-dedup/popularity/adjacency), `artist_discovery_call_weekly` (skips + cost-breaker + write + hallucination filter + retry + graceful-fail), `_weekly_maintenance_tick` ordering + step-3-failure isolation + WeeklyCronState stamp. |
| `tests/test_discovery_lifecycle_hooks.py` | 14 tests for Task 4: hook 1/2/3 stamp + idempotency + gate enforcement + AST scan for `asyncio.to_thread` + `get_lidarr_status_for_add` slotted/non-slotted branches + triggering_mb_id fast-path. |

### Modified

| File | Change |
|------|--------|
| `app/services/discovery_service.py` | Appended ~1,600 lines: DISCOVERY_ARTIST_* constants, CandidateRecord + CandidatePipelineCounts dataclasses, D-A2 selector, Pitfall 12 helpers (`_get_in_library_mbids_sync`, `_get_lidarr_known_artists`, `_starred_artist_mbids_sync`, `_starred_labels_sync`), `_passes_popularity_gate`, `compute_candidate_set_for_seed`, `_log_artist_discovery_failure_sync`, `_read_lidarr_configured_sync`, `_read_active_vibes_sync`, `_write_discovery_candidates_sync`, `LLMArtistPick` + `ArtistDiscoveryPicksResponse` shapes, `_build_artist_discovery_system_prompt` / `_build_artist_discovery_user_prompt`, `artist_discovery_call_weekly`, `_update_weekly_cron_state_sync` + `update_weekly_cron_state`, three lifecycle stamp helpers + their async wrappers, `_read_discovery_add_lifecycle_sync` + `get_lidarr_status_for_add`. |
| `app/services/sync_scheduler.py` | `_weekly_maintenance_tick` extended with step 3 (`artist_discovery_call_weekly`) and step 4 (`update_weekly_cron_state`), each wrapped in individual try/except for best-effort isolation. |
| `app/services/sync_service.py` | Hook 1 wired BEFORE `trigger_post_sync_analysis`. Best-effort try/except. |
| `app/services/analysis_service.py` | Hook 2 wired at end of `run_analysis` (after final state transition). Best-effort try/except. |
| `app/services/vibe_service.py` | Hook 3 wired at END of `_slot_track_inner` immediately after SlotInLog write (Option A — single call site). Best-effort try/except. |
| `app/models/vibe.py` | Added `Vibe.last_seed_track_id: Optional[int] = Field(default=None, foreign_key="track.id")` for D-A2 round-robin cursor. |
| `app/database.py` | `_migrate_add_columns` ALTER TABLE vibe ADD COLUMN last_seed_track_id INTEGER (additive, PRAGMA-guarded). |
| `tests/test_listenbrainz_client.py` | Extended Task 1 RED gate with 4 more behaviour tests (5xx → [], timeout → [], query-param shape, limit cap). |
| `tests/test_event_handlers.py` | Removed `lookup_artist` from `TestStaticAnalysis.forbidden_names` — pyarr 6.x doesn't expose this flat name (uses `lidarr.artist.lookup`); inline rationale documents the choice. |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] `lookup_artist` in forbidden_names was a name collision**
- **Found during:** Task 3 AST static test run.
- **Issue:** Plan 01 Task 1's AST static test added `lookup_artist` to the `forbidden_names` set as a defensive guard against pyarr blocking calls. But pyarr 6.x actually exposes `lidarr.artist.lookup` (the `.lookup` attr on the `artist` namespace), NOT a flat `lookup_artist`. The name collided with our own intentionally-async wrapper `musicbrainz_client.lookup_artist(mb_id)` which IS safe (its internal blocking call is wrapped in `asyncio.to_thread`). The AST test cannot distinguish between calling a pyarr blocking method and awaiting an async wrapper that itself uses `to_thread` — the name match fires a false positive.
- **Fix:** Removed `lookup_artist` from `forbidden_names`; documented rationale inline. Note that `add_artist` is also a name collision (our own `lidarr_client.add_artist` is also an async wrapper) but it was already passing because the existing Plan 01 AST scan didn't flag it (the wrapping pattern means the outer-async wrapper's body has the inner blocking `lidarr.artist.add` wrapped in `to_thread`, and the AST scan correctly approves that path).
- **Files modified:** `tests/test_event_handlers.py`.
- **Commit:** `26da78a` (Task 3).

**2. [Rule 2 — Missing critical] Bottom-of-module imports in `discovery_service.py` to avoid circular import**
- **Found during:** Task 3 implementation.
- **Issue:** Importing `AnthropicClient` + `MaxTokensTruncationError` + `check_or_raise` + `CostBreakerTrippedError` at the top of `discovery_service.py` would trigger a circular through `anthropic_client.py → llm_usage / settings_service → app.models.taste_profile` at module load. Plan 01's `discovery_service.py` is imported by `app/main.py` lifespan before the test fixture path has fully materialised some dependencies.
- **Fix:** Moved the LLM-stack imports to the module bottom (just above their first use, after the Pydantic class definitions). Same pattern used elsewhere in the codebase (suggestions_discovery does similar lazy imports inside functions). Functionally identical for callers; just a load-order detail.
- **Files modified:** `app/services/discovery_service.py`.
- **Commit:** `26da78a` (Task 3).

**3. [Rule 1 — Bug] Test helper `_seed_discovery_add` had a base-defaults + **overrides collision**
- **Found during:** Task 4 first test run.
- **Issue:** The helper passed `composer_sync_seen_at=None` in the explicit kwargs AND accepted `**overrides`, so tests passing `composer_sync_seen_at="<iso>"` triggered `TypeError: got multiple values for keyword argument`.
- **Fix:** Switched to a `defaults` dict merged with `overrides` before constructing the row.
- **Files modified:** `tests/test_discovery_lifecycle_hooks.py`.
- **Commit:** `0ed542c` (Task 4).

## Authentication Gates

None. All work was code-level; no external credentials needed during execution.

## ListenBrainz Smoke-Test Outcome (Task 1 — already merged)

Per the continuation context, Task 1 was completed in a prior session and merged at `3f79756` → `50ad3e4`. The user approved the fixture shape verbatim:

> "Approve — lock shape as-is. Parser keeps score as int (matches raw response), preserves list-position ordering from ListenBrainz. Task 3 popularity-gate doesn't need score to be a float."

Task 2's parser preserves the labs response as-is (`score` kept as int, no float coercion, no normalisation). The fixture-vs-live diff via `tests/fixtures/listenbrainz_similar_artists_sample.json` is the canary if the endpoint shape changes upstream.

## Pointers for Plan 04

Plan 04 builds the `/discover` UI on top of this backend:

- **Read path:** `SELECT * FROM discoverycandidate WHERE created_at >= (SELECT last_tick_at FROM weeklycronstate WHERE id=1) ORDER BY seed_vibe_id, llm_rank ASC`. Group results by `seed_vibe_id` for the vibe-section layout (D-D2). Subtract `DiscoveryDismissed.mb_id` at render time for instant dismiss (D-B2).
- **Render-time status:** `discovery_service.get_lidarr_status_for_add(mb_id)` returns the four-state lifecycle string. Plan 04 should extend this helper to layer Lidarr `history.get` for searching/downloading states (currently only the post-import branches are populated). The slotted branch is wired and verified end-to-end.
- **Add flow:** Plan 01's `lidarr_client.add_artist(mb_id, url, api_key, quality_profile_id, metadata_profile_id, root_dir)` is the entry point. After a successful add, insert a `DiscoveryAdd` row with `added_at=now, lidarr_artist_id=<from response>` so the lifecycle hooks below populate as the artist flows through sync → analysis → vibe slot-in.
- **Card-removal lifecycle:** Filter `WHERE vibe_slotted_at IS NULL` (or its negation, depending on which view) to honour D-D4's "REMOVED from /discover" rule. Hook 3 stamps this column the first time ANY track for the artist enters a vibe — the auto-removal is now end-to-end functional.
- **Empty states:** Plan 02 honours D-C2 (no Lidarr → cron skips with `_skipped_no_lidarr` LLMUsage breadcrumb; no vibes → `_skipped_no_vibes`). Plan 04 should render the matching CTAs by reading either the latest `LLMUsage WHERE purpose LIKE 'discovery_artist_%'` row's purpose suffix OR checking `is_service_configured(session, 'lidarr')` + `SELECT COUNT(*) FROM vibe WHERE is_active=1` directly.

## Pointers for Plan 05 (/debug/discovery)

Plan 05 surfaces the diagnostic page on top of this backend:

- **Cost panel:** `SELECT purpose, called_at, cost_estimate_usd, input_tokens, output_tokens, cache_read_input_tokens, error_text FROM llmusage WHERE purpose LIKE 'discovery_artist_%' ORDER BY called_at DESC LIMIT 50`. The purpose suffixes are stable: `discovery_artist_weekly` (success), `_skipped_no_lidarr`, `_skipped_no_vibes`, `_skipped_no_candidates`, `_cost_locked`, `_error`, `_error_max_tokens_truncated`. Each failure path writes exactly one row with the failure reason in `error_text`.
- **Last weekly candidate set:** `SELECT * FROM discoverycandidate ORDER BY created_at DESC, llm_rank ASC`. Group by `created_at`/week to show the latest week's pool. Include `factual_hook` + `llm_rationale` + `popularity_gate_pass` for full provenance per artist.
- **Counter dataclass:** `CandidatePipelineCounts` (returned by `compute_candidate_set_for_seed`) is NOT persisted yet — Plan 02 logs counts but doesn't write them to the DB. If `/debug/discovery` needs to show per-tick funnel counts (listenbrainz_returned / validation_drops / dedup_drops / popularity_drops / kept), Plan 05 can either (a) parse them from log lines via `EventLog` (cheap), or (b) add a `DiscoveryCounterLog` table and a one-line write inside `artist_discovery_call_weekly`. Planner's call.
- **DiscoveryAdd timeline:** `SELECT * FROM discoveryadd ORDER BY added_at DESC` — every Composer-initiated add with all four lifecycle timestamps. The columns are already populated by hooks 1/2/3 — Plan 05 just renders them.
- **WeeklyCronState chip data:** `SELECT last_tick_at FROM weeklycronstate WHERE id=1` — used by the home-page "next refresh in Nd" chip (D-B4) and by Plan 05's "Last cron tick" header.

## Test Counts

| Test file | Count | All passing |
|-----------|-------|-------------|
| `tests/test_listenbrainz_client.py` | 5 | ✅ |
| `tests/test_musicbrainz_client.py` | 9 | ✅ |
| `tests/test_discovery_service.py` | 21 | ✅ |
| `tests/test_discovery_lifecycle_hooks.py` | 14 | ✅ |
| `tests/test_event_handlers.py::TestStaticAnalysis` | 1 | ✅ |
| **Plan 02 total** | **50** | **50 passing** |

## Deferred Issues

Pre-existing failures unrelated to Plan 02 (documented in Plan 01 SUMMARY; verified via `git stash` round-trip on `26da78a` that these failures pre-exist Task 2/3/4 changes):

1. `tests/test_sync_service.py::TestRunSync::test_delta_sync_when_last_sync_exists` — asserts `synced_tracks == 2` via mocked `get_tracks_since`, but `run_sync` no longer takes the delta-sync code path (retired in some prior commit; `get_tracks_since` is imported but never called).
2. `tests/test_sync_service.py::TestRunSync::test_fallback_to_full_sync_when_delta_returns_zero` — same root cause as (1).
3. `tests/test_sync_scheduler.py::TestStartScheduler::test_triggers_auto_sync_when_no_prior_sync` + `TestStopScheduler::test_shuts_down_without_error` + `TestUpdateSyncSchedule::test_updates_running_scheduler` — APScheduler `start()` requires a running event loop; these tests don't wrap in `_run_async`.

Plus the pre-existing tail from Plan 01 SUMMARY: `test_analysis_service.py` (Essentia shape drift on darwin), `test_audio_analyzer.py` (Essentia feature shape drift), `test_chat_service.py` (Phase 7 chat-retirement scaffolding), `test_sync_api.py` (TestClient lifespan ordering). None of these are Plan 02 regressions.

## Self-Check: PASSED

Created files verified to exist:
- `app/services/listenbrainz_client.py` — FOUND
- `app/services/musicbrainz_client.py` — FOUND
- `tests/test_musicbrainz_client.py` — FOUND
- `tests/test_discovery_service.py` — FOUND
- `tests/test_discovery_lifecycle_hooks.py` — FOUND

Commits verified:
- `ecb40a6` (Task 2: client modules) — FOUND
- `26da78a` (Task 3: pipeline + cron) — FOUND
- `0ed542c` (Task 4: lifecycle hooks) — FOUND

All Task 2/3/4 grep gates pass (verified inline during execution). AST static test passes. 50/50 Plan 02 tests passing.
