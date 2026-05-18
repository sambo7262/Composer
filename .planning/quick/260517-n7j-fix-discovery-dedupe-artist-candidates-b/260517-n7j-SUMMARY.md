---
status: complete
quick_id: 260517-n7j
completed_at: 2026-05-17
---

# Quick Task 260517-n7j: fix(discovery) — dedupe artist candidates by normalized artist_name

## One-liner

Added Phase 8.2 startup migration + stacked write-time dedup that collapses `DiscoveryCandidate` rows sharing the same normalized artist_name (different `mb_id`s for the same human artist). Stacks on top of the Phase 8.1 mb_id dedup from commit `8f8f739` — same `MigrationLog` gate pattern, same tiebreak rule.

## Why this was needed

The Phase 8.1 mb_id dedup shipped in `8f8f739` ran on the NAS at 2026-05-17 16:33:12 and reported `"deleted 0 duplicate DiscoveryCandidate rows"` — but the `/discover` page still showed visible duplicates because MusicBrainz often has multiple `mb_id` entries for one human artist (group/solo aliases, splits, disambiguation, regional variants). User examples from NAS UAT: Bonobo ×3, Flume ×2, Hot Chip ×3 in "Chill EDM Beats"; Bassnectar + Jamie xx spanning multiple vibes.

User explicitly confirmed: collapse everything by normalized name, accepting that two unrelated bands sharing a name (e.g., two acts called "Sun") will be collapsed to a single card.

## Changes shipped

### Code
- `app/services/discovery_service.py` (+149 lines)
  - `PHASE_08_2_MIGRATION_ID = "8.2-discovery-dedupe-artist-name"` constant
  - `_dedupe_discovery_candidates_by_name_sync()` — Python in-process normalization via `(row.artist_name or "").strip().casefold()` (`casefold()` not `lower()` for Unicode parity); deletes losers by `(COALESCE(llm_rank, 9999) ASC, id ASC)`; skips empty/whitespace artist_names
  - `run_phase_08_2_discovery_dedupe_artist_name()` async wrapper mirroring `run_phase_08_1_discovery_dedupe_mb_id` exactly (MigrationLog gate, in-flight stamp, try/except, success stamp, summary log line)
  - SECOND write-time dedup block in `artist_discovery_call_weekly` stacked AFTER the existing mb_id block, before `_write_discovery_candidates_sync`; passes through empty-name rows rather than dropping them

- `app/main.py` (+7 lines)
  - 2-line wire-up in lifespan: `from app.services.discovery_service import run_phase_08_2_discovery_dedupe_artist_name` + `await run_phase_08_2_discovery_dedupe_artist_name()` after the existing Phase 8.1 await, before `get_event_bus()`. Final lifespan order: 8.0 bootstrap → 8.1 mb_id dedupe → 8.2 name dedupe → event bus.

### Tests
- `tests/test_phase_08_2_discovery_dedupe_artist_name.py` (NEW, +250 lines) — mirrors `tests/test_phase_08_1_discovery_dedupe_mb_id.py` structure. Four tests:
  - `test_dedupe_collapses_same_artist_name_with_different_mb_ids_and_stamps`
  - `test_dedupe_is_case_insensitive_and_strip` (covers "Bonobo", "BONOBO", " bonobo ", "  Bonobo\t")
  - `test_dedupe_is_idempotent`
  - `test_dedupe_skips_empty_artist_names`
- `tests/test_discovery_service.py` (+106 lines) — added `test_artist_discovery_call_weekly_dedupes_artist_name_across_mb_ids`

## Commits
- `03cda38` feat(260517-n7j): add Phase 8.2 artist_name dedup helpers + write-time block
- `17b9c25` test(260517-n7j): add Phase 8.2 migration + write-time dedup regression tests
- `f5ed814` feat(260517-n7j): wire Phase 8.2 artist_name dedup migration into lifespan

## Verification

Sandbox blocked pytest in the executor agent; tests were re-run in the orchestrator's main repo context against the integrated state (both n7j + nkt merged):

**192/192 passed** across `tests/test_suggestions_service.py`, `tests/test_suggestions_discovery.py`, `tests/test_plex_playlist_service.py`, `tests/test_discovery_service.py`, `tests/test_phase_08_discovery_bootstrap.py`, `tests/test_phase_08_1_discovery_dedupe_mb_id.py`, `tests/test_phase_08_2_discovery_dedupe_artist_name.py`, `tests/test_event_handlers.py` (18.37s).

## Files modified
- `app/services/discovery_service.py`
- `app/main.py`
- `tests/test_phase_08_2_discovery_dedupe_artist_name.py` (new)
- `tests/test_discovery_service.py`
