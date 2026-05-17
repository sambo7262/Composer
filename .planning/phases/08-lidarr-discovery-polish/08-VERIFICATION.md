---
phase: 08-lidarr-discovery-polish
verified: 2026-05-17 PT
status: human_needed
must_haves_verified: 49/49
requirements_covered: 12/12
---

# Phase 8: Lidarr Discovery + Polish — Verification

## Summary

Goal-backward sweep against all 5 plans, 12 requirement IDs, and the 3
in-phase corrections (CR-01, WR-01, WR-05) confirms Phase 8 is materially
complete. Every must-have from PLAN frontmatter has a load-bearing code
anchor: pyarr 6.x-namespaced Lidarr client returns 3 lists in one
`to_thread`, the `_weekly_maintenance_tick` runs all 4 steps in order
(prune → suggestions discovery → artist discovery → `WeeklyCronState`
stamp), `/discover` filters `DiscoveryCandidate.created_at >= week_cutoff`
(CR-01), `_get_in_library_mbids_sync` reads only from `composer.tracks`
(OPS-06), every PlexAPI/pyarr call from async paths is wrapped in
`asyncio.to_thread`, and 183 Phase 8 tests pass. Three `tests/test_sync_scheduler.py`
failures are pre-existing test-environment issues (Python 3.9 venv / missing
`discoverystate` fixture) unrelated to Phase 8 code. The mobile-portrait UAT
on `/library` + `/discover` was approved during Plan 04 and is restated here
so it persists into `/gsd-progress`.

## Must-Have Verification Table

### Plan 01 — Foundation (Lidarr extension + sync cron + Phase 8 schema)

| # | Must-have (truth) | Anchor | Status |
|---|-------------------|--------|--------|
| 1 | Lidarr test returns quality + metadata + root in one call | `app/services/lidarr_client.py:46-104` (`_fetch_lidarr_test_payload` + `test_lidarr_connection`) | OK |
| 2 | Settings renders 3 dropdowns; root only when >1 root | `app/templates/partials/connection_status.html:54,70,87,95` | OK |
| 3 | `save_lidarr` persists 5 extras keys | `app/routers/api_settings.py:252-277` | OK |
| 4 | `schedule_sync(24)` uses `CronTrigger(hour=3)` not `IntervalTrigger` | `app/services/sync_scheduler.py:53-71` | OK |
| 5 | Job has `coalesce=True`, `misfire_grace_time>=3600`, `max_instances=1` | `app/services/sync_scheduler.py:62-71` | OK |
| 6 | Lifespan fires catch-up sync if stale > interval + 1h grace | `app/services/sync_scheduler.py:376-410` | OK |
| 7 | Silent sync exceptions write `EventLog(event_type='sync_failed')` | `app/services/sync_service.py:241-294` (incl. dedupe sha256 + 5-min bucket) | OK |
| 8 | `Vibe.color` column exists | `app/models/vibe.py` + `app/database.py::_migrate_add_columns` | OK |
| 9 | Bootstrap stamps `CostMeterBaseline`, `WeeklyCronState`, backfills `Vibe.color` | `discovery_service.py:128-301` (`run_phase_08_discovery_bootstrap`) | OK |
| 10 | Bootstrap gated by `MigrationLog`, idempotent | `discovery_service.py:107-127, 250-301` | OK |
| 11 | `Track.plex_artist_mbid` column exists | `app/models/track.py` + `_migrate_add_columns` | OK |
| 12 | Bootstrap best-effort backfills `Track.plex_artist_mbid` | `discovery_service.py:171-249` | OK |

### Plan 02 — Discovery pipeline backend (DISC-03/04/05/06)

| # | Must-have | Anchor | Status |
|---|-----------|--------|--------|
| 13 | ListenBrainz endpoint smoke-tested + fixture committed | `tests/fixtures/listenbrainz_similar_artists_sample.json` + `app/services/listenbrainz_client.py:1-23` (locked parser contract) | OK |
| 14 | `musicbrainz_client.lookup_artist` writes through `MusicBrainzCache`; 1 req/sec rate limit | `app/services/musicbrainz_client.py:36-40, 85-111` | OK |
| 15 | `compute_candidate_set_for_seed` runs full pipeline | `discovery_service.py:650-746` | OK |
| 16 | Popularity gate (D-A3) drops candidates lacking adjacency | `discovery_service.py:564-606` | OK |
| 17 | Cross-surface dedup against `composer.tracks` + Lidarr | `discovery_service.py:443-468, 496-562` | OK |
| 18 | `artist_discovery_call_weekly` wraps LLM in `llm_cost_breaker.check_or_raise(purpose='discovery_artist_weekly')` | `discovery_service.py:930-1195` | OK |
| 19 | Hallucinated mb_ids filtered against pre-validated pool | `discovery_service.py:1140-1170` | OK |
| 20 | Weekly tick runs 4 steps in order | `app/services/sync_scheduler.py:196-267` | OK |
| 21 | Artist-discovery failure writes `LLMUsage` row + never crashes tick | `discovery_service.py:747-766, 1186-1210` | OK |
| 22 | `composer_sync_seen_at` stamped post-sync | `app/services/sync_service.py:219-232` → `discovery_service.py:1247-1296` | OK |
| 23 | `essentia_complete_at` stamped post-analysis | `app/services/analysis_service.py:288-300` → `discovery_service.py:1299-1343` | OK |
| 24 | `vibe_slotted_at` stamped on slot | `app/services/vibe_service.py:582-606` → `discovery_service.py:1346-1406` | OK |
| 25 | `get_lidarr_status_for_add` returns "analyzed, slotted into vibes" | `discovery_service.py:1754-1851` | OK |

### Plan 03 — Vibe color propagation + cost chip (UI-09 / UI-10)

| # | Must-have | Anchor | Status |
|---|-----------|--------|--------|
| 26 | Every vibe-rendering template reads `{{ vibe.color }}` / `proposed_color` | `vibe_card.html`, `suggestions_row.html`, `recluster_modal.html`, `vibe_diagnostic_card.html` (used by `debug_vibes.html:70`), `vibe_proposal_card.html` (used by `setup_step3.html`) | OK |
| 27 | New vibes get color via palette | `discovery_service.py:60-68` + `vibe_service.py:722-746` (`ensure_vibe_color_on_creation`) | OK |
| 28 | Re-cluster preserves existing colors (D-22) | `vibe_service.py:742-743` (`if row.color: return`) | OK |
| 29 | Home page renders cost chip | `vibes_home.html` includes `partials/llm_cost_chip.html`; `pages.py::read_vibes_home` calls `_compute_home_chip_context` | OK |
| 30 | Chip sums LLMUsage where `called_at >= max(last_tick_at, deploy_at)` | `pages.py:86-146` | OK |
| 31 | Chip excludes pre-baseline rows permanently | `pages.py:108-138` | OK |
| 32 | Chip tap → `/debug/suggestions` | `llm_cost_chip.html:27` (`href="/debug/suggestions"`) | OK |
| 33 | Chip shows `paused` modifier when breaker open | `pages.py:162-180` + `llm_cost_chip.html:43-45` | OK |
| 34 | Pre-first-tick state shows next-tick LA string | `llm_cost_chip.html:39-42` + `jinja_filters.next_sunday_03_utc_in_la` | OK |

### Plan 04 — /discover + /library mobile-first (DISC-03/05/UI-07/08)

| # | Must-have | Anchor | Status |
|---|-----------|--------|--------|
| 35 | `/discover` renders vibe-grouped sections | `pages/discover.html:50-54` + `partials/discover_vibe_section.html` | OK |
| 36 | Artist card shows name + factual hook + vibe chip; tap expands Add/Dismiss | `partials/discover_artist_card.html:15,67,74` | OK |
| 37 | POST `/api/discovery/{mb_id}/add` adds via `add_artist_to_lidarr` + inserts `DiscoveryAdd` | `api_discovery.py:80-108` → `discovery_service.py:1528-1596` | OK |
| 38 | After Add, card swaps to status row | `api_discovery.py:103-108` returns `partials/discover_status_row.html` | OK |
| 39 | POST `.../dismiss` writes `DiscoveryDismissed`; swap to empty | `api_discovery.py:111-124` | OK |
| 40 | Slotted artists drop out of /discover (D-D4) | `discovery_service.py:1680-1686, 1725-1728` (`completed_mbids` filter) | OK |
| 41 | Empty states render appropriate CTA | `pages/discover.html:29-48` (lidarr / vibes / first-tick / no-candidates) | OK |
| 42 | Lazy-poll uses 5-min in-memory cache | `discovery_service.py:1446, 1766-1851` (`_lidarr_status_cache` + 300s TTL) | OK |
| 43 | Stale warning chip when add > 48h in searching/pending | `api_discovery.py:37-68` (`_compute_stale_context`) | OK |
| 44 | `/library` is mobile-first card list at sub-md | `pages/library.html` + `partials/library_results_wrapper.html` (`md:hidden` card list + `hidden md:block` table) | OK |
| 45 | `/library` HTMX target wraps cards AND table | `partials/library_results_wrapper.html:18-36` (single `#library-results` wrapper) | OK |
| 46 | Mobile honors `h-dvh`, safe-area, ≥44px tap targets, alpine-morph | `library.html`, `track_card.html`, `library_filter_chips.html`, `library_sort_sheet.html` (min-h-11 present) | OK |

### Plan 05 — /debug/discovery + OPS-06 (DEBUG-04 / OPS-06)

| # | Must-have | Anchor | Status |
|---|-----------|--------|--------|
| 47 | GET `/debug/discovery` returns 200 + plain HTML | `pages.py` route + `pages/debug_discovery.html` (extends `base.html`) | OK |
| 48 | Page renders 5 sections + cost panel + manual-tick button | `debug_discovery.html:102, 150, 191, 225, 260` + `:58, :76` (manual tick button + no-JS fallback form) | OK |
| 49 | OPS-06: `_get_in_library_mbids_sync` reads only from `composer.tracks` | `discovery_service.py:443-468` (no playlist join; regression-tested in `tests/test_discovery_service_ops06.py`) | OK |

## Requirement Traceability

| Req | Anchor | Status |
|-----|--------|--------|
| DISC-03 | `discover.html` + `discover_vibe_section.html` (vibe-grouped) + `compute_candidate_set_for_seed` (ListenBrainz + MB + LLM re-rank) | satisfied |
| DISC-04 | `_passes_popularity_gate` (`discovery_service.py:564-606`) + `_get_lidarr_known_artists` dedup | satisfied |
| DISC-05 | `api_discovery.py:80-108` + `lidarr_client.add_artist` (4 required params per Pitfall 14) | satisfied |
| DISC-06 | 3 lifecycle hooks: sync→analysis→slot (`sync_service.py:219`, `analysis_service.py:288`, `vibe_service.py:582`); `get_lidarr_status_for_add` surfaces "analyzed, slotted into vibes" | satisfied |
| DISC-07 | `test_lidarr_connection` returns 3 lists; 3 dropdowns + 5 saved extras keys | satisfied |
| DISC-08 | `schedule_sync` uses CronTrigger + coalesce + misfire_grace + lifespan catch-up gate + `sync_failed` EventLog | satisfied |
| UI-07 | `library.html` mobile-first card list + sticky search + filter chips + bottom sheet | satisfied (human UAT confirmed Plan 04) |
| UI-08 | Discover/library + cost chip min-h-11 throughout | satisfied (human UAT confirmed Plan 04) |
| UI-09 | `Vibe.color` + `assign_vibe_color` + 6 templates wire it | satisfied |
| UI-10 | `llm_cost_chip.html` + `_compute_home_chip_context` + `usd_cost` filter + `local_time` PT rendering | satisfied |
| OPS-06 | `_get_in_library_mbids_sync` reads only `composer.tracks` (regression test pinned) | satisfied |
| DEBUG-04 | `/debug/discovery` 5 sections + cost panel + manual-tick button | satisfied |

## Critical contracts verified

- **CR-01 (week cutoff on /discover):** `discovery_service.py:1659-1731` filters
  `DiscoveryCandidate.created_at >= WeeklyCronState.last_tick_at` on both the
  per-vibe candidate query and the `cand_vibe_lookup` in-flight grouping. Pinned
  by `tests/test_api_discovery.py::TestDiscoverWeeklyRotation`.
- **WR-01 (manual-tick state machine):** `run_manual_weekly_tick` only flips
  to `"idle"` when `_status.state == "running"` (`discovery_service.py:2060-2062`),
  and no longer clears `last_error` on entry.
- **WR-05 (missing baseline surfaces):** `_compute_home_chip_context` returns
  `has_first_tick=False` + `baseline_missing=True` instead of falling back to
  epoch (`pages.py:113-129`).
- **Phase 5 invariants:** `Track.user_rating` raw 0-10 (`track.py:41-43`);
  webhooks return 200 (`api_webhooks.py:60-145`); AST gate
  `test_no_blocking_plexapi_in_async` passes; DB timestamps UTC ISO 8601,
  display via `local_time`.
- **pyarr 6.x namespacing:** every Lidarr call uses
  `lidarr.{quality_profile,artist,root_folder,history}.{...}` or the
  documented `http_utils.request("metadataprofile")` workaround
  (`lidarr_client.py:40-43, 130, 144, 178`).

## Gaps Found

None — all 49 must-haves and all 12 requirements have load-bearing code
anchors. The 3 `tests/test_sync_scheduler.py` failures (
`TestStartScheduler.test_triggers_auto_sync_when_no_prior_sync`,
`TestStopScheduler.test_shuts_down_without_error`,
`TestUpdateSyncSchedule.test_updates_running_scheduler`) are pre-existing
test-environment issues (Python 3.9 in `.venv`, missing `discoverystate`
fixture, no running event loop on non-async tests) and do NOT block
Phase 8 — the Phase 8-specific catch-up + cron-trigger tests in the same
module pass. Five Warning findings and six Info findings from
`08-REVIEW.md` are explicitly deferred to Phase 8.1 per
`.review-deferrals.md`.

## Human Verification Items

These were approved during Plan 04 UAT (Plan 04 was `autonomous: false`);
restated here so they persist into `/gsd-progress`.

| Item | Test | Why human |
|------|------|-----------|
| 1 | Visit `/discover` on iPhone Safari portrait, confirm vibe-grouped sections + vibe-color borders + tap-to-expand artist card + Add-to-Lidarr round-trip + Dismiss instant removal | Visual rendering + tap interaction quality |
| 2 | Visit `/library` on iPhone Safari portrait, confirm card list at sub-md, sticky search, filter chips, bottom-sheet sort, ≥44px tap targets | Visual rendering + scroll/tap quality |
| 3 | Visit `/` and confirm cost chip renders with $X.XX, "next refresh in Nd" / "first refresh {LA datetime}" copy, tap navigates to `/debug/suggestions` | Visual chip + PT time correctness |
| 4 | After ≥1 successful weekly cron, confirm `/discover` shows current-week candidates only (CR-01 regression UAT) | DB inspection across multiple Sunday ticks |
| 5 | Click `[Run weekly tick now]` on `/debug/discovery`; confirm state polls `running → idle/error`, candidates land on `/discover`, cost chip transitions to post-first-tick state | Full backend round-trip on real Lidarr/MB |

---

_Verified: 2026-05-17_
_Verifier: Claude (gsd-verifier)_
