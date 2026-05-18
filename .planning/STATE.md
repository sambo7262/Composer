---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: — Music Companion
status: executing
stopped_at: Phase 7 context gathered via /gsd-discuss-phase. Four gray areas resolved (refill cadence + bootstrap, shortlist composition, queue UI + dismiss interaction, skip-tracking calibration); 13 implementation decisions captured in `07-CONTEXT.md`. Researcher and planner can proceed without re-asking the user. The system-prompt-caching fix (Anthropic >2048-token minimum that bit Phase 6.2) is folded into Phase 7 D-07 as the shared longer preamble — load-bearing for SC4 ("`cache_read_input_tokens` accumulating").
last_updated: "2026-05-17T14:59:40.361Z"
last_activity: 2026-05-17
progress:
  total_phases: 11
  completed_phases: 11
  total_plans: 37
  completed_plans: 37
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-08)

**Core value:** Your Plex stars are the truth. Composer turns them into living vibe playlists and a steady stream of personalized discoveries — without you having to describe a vibe each time.
**Current focus:** Phase 08 — lidarr-discovery-polish

## Current Position

Phase: 08
Plan: Not started
Status: Executing Phase 08
Last activity: 2026-05-17 - Completed quick task 260517-p2b (Phase 8.3 startup mirror trim — completes the refill regression fix)

### v2.0 Phase Snapshot

| Phase | Goal (one-line) | Status |
|-------|-----------------|--------|
| 5 — Plex Event Foundation + Rating Sync | Composer reliably ingests Plex webhook + polling events, dedupes them, propagates `RatingChanged` end-to-end | Complete |
| 6 — Vibe Clustering + Setup Wizard | First-run wizard ends with 3–7 named vibe playlists in Plex, auto-slotting newly-rated tracks | Complete (2026-05-13) |
| 7 — Suggestions Queue + v1 Chat Retirement | Continuous Composer · Suggestions playlist drains and refills; vibes home is the new landing page | Next up |
| 8 — Lidarr Discovery + Polish | Taste-aware artist discovery, one-click add, auto-ingest of arrivals, mobile responsive pass | Pending |
| 9 — Feed the Engine (OPTIONAL) | Bulk rating, play-rated nudge, Surprise Me — cuttable | Pending |

## Performance Metrics

**Velocity (v1.0 — shipped):**

- Total plans completed: 26
- Phases completed: 4 of 4 (v1.0)
- Total v1.0 execution time: ~57 min across plans (logged below)

**By Phase (v1.0):**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 3 | ~14min | ~4.7min |
| 2 | 3 | ~20min | ~6.7min |
| 3 | 3 | ~11min | ~3.7min |
| 4 | 3 | ~12min | ~4.0min |
| 5 | 4 | - | - |
| 07.1 | 5 | - | - |
| 08 | 5 | - | - |

**Recent Trend:**

- Last 5 plans: ~4–5min avg per plan
- Trend: stable, plans landing in the 2–8 minute range

*Updated after each plan completion*
| Phase 01 P01 | 7min | 2 tasks | 32 files |
| Phase 01 P02 | 5min | 2 tasks | 17 files |
| Phase 01 P03 | 2min | 1 tasks | 1 files |
| Phase 02-library-sync P01 | 6min | 2 tasks | 10 files |
| Phase 02-library-sync P02 | 6min | 2 tasks | 10 files |
| Phase 02-library-sync P03 | 8min | 2 tasks | 8 files |
| Phase 03 P01 | 4min | 2 tasks | 9 files |
| Phase 03 P02 | 5min | 2 tasks | 7 files |
| Phase 03 P03 | 2min | 1 tasks | 4 files |
| Phase 04 P01 | 4min | 2 tasks | 9 files |
| Phase 04-playlist-generation P02 | 3min | 2 tasks | 5 files |
| Phase 04 P03 | 5min | 2 tasks | 8 files |
| Phase 05 P01 | ~2h | 3 tasks | 21 files |
| Phase 05 P02 | ~2h | 3 tasks | 14 files |
| Phase 05 P03 | ~1h | 3 tasks | 5 files |

## Accumulated Context

### Roadmap Evolution

- Phase 6.1 inserted after Phase 6 (URGENT — 2026-05-10): Vibe Wizard Foundations: server-led clustering + user-led vibe input. Phase 6 UAT exposed three foundational gaps — k-means labels unused for membership (~10 tracks per playlist out of 600 rated), empty song lists in cards (sparse seed_track_indices), LLM-first UX wastes compute. 6.1 server-leads track membership and replaces Step 3 with user-typed vibe names.

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

**v2.0 (Music Companion) — locked at milestone start:**

- [v2.0]: Rated tracks (Plex `userRating`) as taste signal — Composer reads, never writes
- [v2.0]: Tiered similarity — LLM for clusters/ranking, audio-feature distance for slotting; no global rescoring
- [v2.0]: Event-driven — Suggestions only refills on consumption, never on a schedule
- [v2.0]: Hybrid vibe clustering — AI proposes 3–7 clusters, user names/edits/finalizes; persistent thereafter
- [v2.0]: Plex webhooks primary, polling fallback; dedupe via `EventLog UNIQUE(dedupe_key)` from day one
- [v2.0]: Hands-off existing Plex playlists — `Composer · ` namespace + `ManagedPlaylist` registry as dual ownership markers
- [v2.0]: Mobile-first portrait on every new screen — `h-dvh`, `safe-area-inset-bottom`, 44px touch targets
- [v2.0]: v1 mood-chat retired — vibes home becomes the landing page in Phase 7
- [v2.0]: Anthropic prompt cache `ttl: "1h"` explicit — March 2026 default regressed to 5min
- [v2.0]: LLM cost circuit breaker (daily 50, burst 5/60s, debounce 60s) ships with FIRST ranking call, not later
- [Roadmap v2.0]: Phase 9 (Feed the Engine) is optional and cuttable; nothing depends on it
- [Roadmap v2.0]: `anthropic>=0.100`, `scikit-learn>=1.8`, `pyarr>=6.6` adopted in Phase 5 (consolidates dep churn before clustering work)

**v1.0 (shipped) — recent decisions:**

- [Roadmap]: Fully self-hosted v1 — Essentia for audio features, Ollama → Anthropic mid-milestone, no external APIs
- [Roadmap]: Stack: Python 3.12 + FastAPI + SQLModel + SQLite + Jinja2/HTMX/Alpine.js/Tailwind CSS
- [Phase 01]: Lazy engine singleton pattern for database — allows test isolation by resetting engine between tests
- [Phase 01]: TemplateResponse uses new request-first parameter order (Starlette deprecation fix)
- [Phase 01]: HTMX test-and-configure pattern: form hx-post to test endpoint, connection_status partial swapped in, save replaces entire service card
- [Phase 01]: Service card id={service}-card pattern enables HTMX swap targeting across test/save/reconfigure flows
- [Phase 01]: Docker Hub credentials via GitHub encrypted secrets, metadata-action for auto tag generation, GHA cache for arm64 build performance
- [Phase 02-library-sync]: Sync service uses module-level SyncStatus singleton for in-memory progress tracking
- [Phase 02-library-sync]: Batch upsert commits per 200-track batch to minimize WAL contention
- [Phase 02-library-sync]: Library name resolved server-side via test_plex_connection to fix D-12 bug
- [Phase 02-library-sync]: Sync banner uses HTMX polling every 2s for real-time progress updates
- [Phase 02-library-sync]: Sort column allowlist and per_page cap for API security (T-02-05, T-02-07)
- [Phase 02-library-sync]: APScheduler 3.x for stable AsyncIOScheduler; interval allowlist [6,12,24] for T-02-09; singleton + replace_existing for T-02-10
- [Phase 03]: Valence proxy: weighted combo of mode(0.30), danceability(0.25), brightness(0.25), pitch_salience(0.20)
- [Phase 03]: Essentia manylinux wheel is self-contained — no apt-get changes needed in Dockerfile
- [Phase 03]: Use musical_key (not key) for Track column to avoid Python builtin conflict
- [Phase 03]: Analysis service mirrors sync_service singleton pattern with 5-state machine
- [Phase 03]: ETA rolling window of 50 tracks, error list capped at 50, files >100MB skipped
- [Phase 03]: Renamed state -> analysis_state template variable to avoid sync/analysis banner conflict on library page
- [Phase 04]: Energy normalization uses 0.3 divisor (Essentia spectral_rms range) not 1.0
- [Phase 04]: Metadata fallback adds 0.05 penalty to prefer analyzed tracks over heuristic estimates
- [Phase 04]: Brand text Composer made non-navigational; Compose link added as primary nav item
- [Phase 04-02]: Instructor uses JSON mode (not TOOLS) for universal Ollama compatibility
- [Phase 04-02]: Two-phase LLM pipeline: mood interpretation -> candidate filtering -> LLM curation
- [Phase 04-02]: Track ID validation filters LLM-hallucinated IDs before playlist assembly (T-04-03)
- [Phase 04-03]: Alpine.js Sort plugin loaded via CDN before Alpine core per plugin convention
- [Phase 04-03]: Push-to-Plex uses batch fetchItems with comma-separated ratingKeys (avoids N+1)
- [Phase 05-01]: EventLog UNIQUE(dedupe_key) + INSERT OR IGNORE — race-free dedupe; never SELECT-then-INSERT
- [Phase 05-01]: dedupe_key = sha256(event_type|ratingKey|user_rating|5s_bucket) — webhook+poll overlap resolves naturally
- [Phase 05-01]: Track.user_rating stored RAW 0-10 from Plex; conversion to "X.X stars" only at display via stars_from_user_rating()
- [Phase 05-01]: Single asyncio.Queue + one dispatcher task started in lifespan (queue→dispatcher→scheduler order); NOT FastAPI Depends()
- [Phase 05-01]: stop_dispatcher resets _queue to None — prevents cross-loop "Future attached to a different loop" between TestClient sessions
- [Phase 05-01]: All PlexAPI calls in async paths route through asyncio.to_thread; enforced by AST static test (test_no_blocking_plexapi_in_async)
- [Phase 05-01]: Webhook handler uses Annotated[str, Form()] + json.loads (NEVER pydantic.Json[Model] — FastAPI bug #10997); ALWAYS returns 200 (Plex retries non-2xx)
- [Phase 05-01]: if/elif on event.type used in dispatch_event instead of match (Python 3.9 venv parse compat; functionally identical for discriminator-only routing)
- [Phase 05-02]: APScheduler polling job 'plex_polling' added alongside library_sync (5-min default); both share the existing AsyncIOScheduler singleton — never spawn a second instance
- [Phase 05-02]: Polling uses bounded queries (searchTracks(filters={'track.userRating>>': 0}, limit=200, sort='lastRatedAt:desc')) — Pitfall 21 forbids any unbounded full-library scan
- [Phase 05-02]: Polling emits typed events with source='poll' through the SAME asyncio.Queue + dispatcher as webhooks; UNIQUE(dedupe_key) handles webhook+poll overlap naturally — no app-level coordination
- [Phase 05-02]: Auto-backfill gate is Pitfall 7: (any Track exists) AND (no Track has user_rating populated) — empty DB is a no-op, already-rated DB is a no-op
- [Phase 05-02]: handle_track_played reads lastViewedAt from event payload itself (Pitfall 7 — no Plex re-fetch); increments view_count = (view_count or 0) + 1
- [Phase 05-02]: rating_sync_service is a thin re-export shim over backfill_service (D-11 — manual Resync now and auto first-run backfill share the same singleton state machine; future divergence stays cheap)
- [Phase 05-02]: Lifespan ordering: queue → dispatcher → scheduler → maybe_trigger_first_run_backfill (after scheduler so polling job is registered first)
- [Phase 05-03]: NEW app/services/anthropic_client.py — separate from v1 llm_client.py (D-01 untouched until Phase 7); AsyncAnthropic SDK with explicit cache_control={'type':'ephemeral','ttl':'1h'} on every system message (Pitfall 4 / OPS-02); structured output via Pydantic.model_validate_json — NO Instructor (D-03)
- [Phase 05-03]: Per-call LLMUsage row inserted with all 4 token counts + cost_estimate_usd via asyncio.to_thread; circuit-breaker counters computed on demand via aggregate SELECT — Phase 5 schema sufficient for Phase 7 correctness (DESIGN NOTE in anthropic_client.py)
- [Phase 05-03]: Logger.warning fires when cache_creation==0 AND cache_read==0 — catches prompts below the 2048-token Sonnet 4.6 minimum
- [Phase 05-03]: TasteProfile centroid is single numpy.mean over rated tracks' (energy, tempo, danceability, valence) — NOT k-means; sklearn import forbidden in taste_profile_service.py (clustering = Phase 6; static AST test enforces)
- [Phase 05-03]: System prompt for taste profile padded above 2048 tokens with real Composer context — identical across calls within 1h TTL = cache engages
- [Phase 05-03]: Recompute trigger uses true accumulation: prior.rated_track_count snapshot vs current count >=10% threshold; fires from handle_rating_changed via lazy import + try/except (best-effort — never breaks rating-update path)
- [Phase 05-03]: TasteProfileSummary(BaseModel) is the AnthropicClient response shape — minimal {summary_text: str}; LLM failure persists structured aggregates with empty summary rather than crashing (T-05-16 mitigation)

### Pending Todos

- ✅ Phase 5 Plan 01 (event foundation + schema migration) — COMPLETE
- ✅ Phase 5 Plan 02 (poll service + RATE-04 view_count handler + auto-backfill + Resync now) — COMPLETE
- ✅ Phase 5 Plan 03 (anthropic_client.py with prompt caching + taste profile builder + RATE-05 LLM summary) — COMPLETE
- Phase 5 Plan 04 — wizard UI (3-candidate detection), `/debug/events`, ollama_client deletion (FINAL Phase 5 plan; visual checkpoint, autonomous: false)
- Confirm Docker-network webhook URL during Phase 5 Plan 04 wizard build (`http://composer:8085/api/webhooks/plex` on `synobridge`)
- Light spike at Phase 8 planning to confirm pyarr 6.6 `add_artist()` signature, MusicBrainz adjacency query rate limits, Last.fm vs MusicBrainz as candidate source

### Blockers/Concerns

- None blocking Phase 5. The `[Research flag]` Essentia Docker note from v1 was resolved in Phase 3 (manylinux wheel).
- Phase 5 risk-watch: dedupe + `userRating` 0–10 scale + PlexAPI sync→async wrapping must all land in the FIRST commit of the phase (per PITFALLS 1, 2, 4).
- Phase 7 risk-watch: LLM cost circuit breaker must ship with the first ranking call commit, not as a follow-up (PITFALL 11).

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260510-sht | Fix map_user_vibes_to_clusters None-session AttributeError (regression test + 2-line Session-wrapping fix) | 2026-05-11 | 03dfb87 | [260510-sht-fix-map-user-vibes-to-clusters-attribute](./quick/260510-sht-fix-map-user-vibes-to-clusters-attribute/) |
| 260510-t5n | Fix AnthropicClient.call_with_structured_output to tolerate trailing/leading prose around JSON (raw_decode + 2 regression tests) | 2026-05-11 | ca691bd | [260510-t5n-fix-anthropicclient-call-with-structured](./quick/260510-t5n-fix-anthropicclient-call-with-structured/) |
| 260510-tng | Fix LLMVibeMappingResponse mappings-field-required by prepending RESPONSE FORMAT OVERRIDE to user-led user prompt (preserves cached system prompt) | 2026-05-11 | f673c8b | [260510-tng-fix-llmvibemappingresponse-mappings-fiel](./quick/260510-tng-fix-llmvibemappingresponse-mappings-fiel/) |
| 260511-bpf | Dedupe Plex playlist members by (artist, title) canonical key — fixes cross-album dupes (quality-aware dedupe deferred to Phase 6.2) | 2026-05-11 | 1371b53 | [260511-bpf-dedupe-plex-playlist-members-by-artist-t](./quick/260511-bpf-dedupe-plex-playlist-members-by-artist-t/) |
| 260512-k3n | Phase 6.2 hotfix: translate `thinking="adaptive"` to `{"type":"enabled","budget_tokens":2000}` with 400-fallback retry, plus live "Calling Anthropic…" progress card on propose page | 2026-05-12 | d57befe | [260512-k3n-hotfix-phase-6-2-replace-adaptive-thinki](./quick/260512-k3n-hotfix-phase-6-2-replace-adaptive-thinki/) |
| 260512-kvs | Phase 6.2 hotfix #2: bump `PASS1_MAX_TOKENS` 3000→6000 + `PASS2_MAX_TOKENS` 4000→8000 (Pass 2 with extended thinking was returning thinking-only blocks on `stop_reason=max_tokens`); remove $3 cost-cap warning from /debug/vibes per user request | 2026-05-12 | 9ff83e3 | [260512-kvs-hotfix-2-remove-3-cost-cap-warning-bump-](./quick/260512-kvs-hotfix-2-remove-3-cost-cap-warning-bump-/) |
| 260514-cdl | Phase 7 bootstrap deadlock hotfix: drop `if removed:` gate in `handle_track_played` so `maybe_schedule_refill` always runs after drain (deficit check inside preserves no-churn intent); empty-mirror regression test added | 2026-05-14 | 8594589 | [260514-cdl-fix-phase-7-suggestions-bootstrap-deadlo](./quick/260514-cdl-fix-phase-7-suggestions-bootstrap-deadlo/) |
| 260514-e6w | Phase 7 suggestions_rank max_tokens hotfix: bump 2000→8000 via `SUGGESTIONS_RANK_MAX_TOKENS` constant at 4 call sites (NAS UAT confirmed truncation aborting refill); 2 regression tests (constant pin + AST forbid-literal). Temporary patch — proper architectural fix in `.planning/notes/phase-07-followup-cost-architecture.md` (Option C). | 2026-05-14 | 482d52e | [260514-e6w-fix-phase-7-suggestions-rank-max-tokens-](./quick/260514-e6w-fix-phase-7-suggestions-rank-max-tokens-/) |
| 260516-gym | Phase 7.1 follow-up: weekly Plex Suggestions playlist prune via Sun 03:00 UTC `_weekly_maintenance_tick` (prune → discovery in one cron tick). `prune_suggestions_playlist_to_mirror` in `plex_playlist_service.py` removes Plex tracks no longer in `SuggestionsMirror`; hard-asserts `kind='suggestions'` before mutation; preserves GAP-03 int-cast invariant; D-C2 catch-up gate runs prune before catch-up discovery. 5 new tests (4 prune + 1 scheduler) using `StrictFakePlexServer` from GAP-03. | 2026-05-16 | (see merge) | [260516-gym-weekly-suggestions-playlist-prune](./quick/260516-gym-weekly-suggestions-playlist-prune/) |
| 260517-l84 | fix(suggestions): self-heal in weekly prune when Composer · Suggestions Plex playlist vanishes. NAS UAT 2026-05-17 15:04:26 showed prune crashing with `plexapi.NotFound(404)` on `mp.plex_rating_key=77830` (user-deleted in Plexamp), scheduler swallowing it, and `discovery_call_weekly` writing picks to mirror but never recreating the Plex playlist. Fix splits the single try in `prune_suggestions_playlist_to_mirror` into two: first wraps the playlist `fetchItem` and on NotFound lazy-imports `_materialize_suggestions_plex_playlist` (avoids circular), recreates the playlist from `sorted(mirror_keys)`, returns healed `PruneResult`; second wraps the removal branch unchanged. 2 regression tests added (non-empty mirror + empty-mirror edge case). 81/81 tests pass, zero changes to `sync_scheduler.py` or `suggestions_service.py`. | 2026-05-17 | 4fbc060 | [260517-l84-fix-suggestions-self-heal-in-weekly-prun](./quick/260517-l84-fix-suggestions-self-heal-in-weekly-prun/) |
| 260517-lyw | fix(discovery): dedupe artist candidates by `mb_id` — both within-vibe and cross-vibe duplicates. NAS UAT 2026-05-17 15:06:55-15:07:03 showed same MBIDs fetched 4× each on one /discover load (duplicate DOM ids → multiple `hx-trigger="revealed once"` → ListenBrainz 429 cascade). Root cause: `artist_discovery_call_weekly` loops per active vibe; LLM picks same artist once per vibe context; writer `_write_discovery_candidates_sync` is insert-only with no dedup (per its own docstring). Two-part fix: (1) write-time dedup at `discovery_service.py:~1185` collapses `valid_picks` by `mb_id` keeping lowest `llm_rank`; (2) one-shot Phase 8.1 startup migration `run_phase_08_1_discovery_dedupe_mb_id` (phase_id=`8.1-discovery-dedupe-mb-id`) mirrors `run_phase_08_discovery_bootstrap` pattern with `MigrationLog` gate, cleans existing dupes from NAS DB on next deploy. Tiebreak: `COALESCE(llm_rank, 9999) ASC, id ASC` — identical in both paths. 4 new tests (1 write-time + 3 migration), 48/48 pass. No template/schema changes (UNIQUE on mb_id punted to follow-up). | 2026-05-17 | 8f8f739 | [260517-lyw-fix-discovery-dedupe-artist-candidates-b](./quick/260517-lyw-fix-discovery-dedupe-artist-candidates-b/) |
| 260517-n7j | fix(discovery): dedupe artist candidates by normalized artist_name (stacked on top of 260517-lyw's mb_id dedup). NAS UAT 2026-05-17 16:33:12 confirmed Phase 8.1 migration ran cleanly ("deleted 0") but /discover still showed visible duplicates because MusicBrainz has multiple mb_id entries per human artist (group/solo aliases, splits, disambiguation). Examples: Bonobo ×3, Flume ×2, Hot Chip ×3 in "Chill EDM Beats"; Bassnectar + Jamie xx cross-vibe. Fix: Phase 8.2 startup migration `run_phase_08_2_discovery_dedupe_artist_name` + write-time block stacked AFTER existing mb_id dedup, both keyed on `(artist_name or "").strip().casefold()` (`casefold()` not `lower()` for Unicode parity). Tiebreak `(COALESCE(llm_rank, 9999) ASC, id ASC)` matches Phase 8.1. Empty/whitespace artist_names passed through, not deleted. User accepted the trade-off that two unrelated bands sharing a name collapse to one card. Lifespan ordering: 8.0 → 8.1 → 8.2 → event_bus. 4 new tests; 192/192 pass on integrated state. | 2026-05-17 | f5ed814 | [260517-n7j-fix-discovery-dedupe-artist-candidates-b](./quick/260517-n7j-fix-discovery-dedupe-artist-candidates-b/) |
| 260517-nkt | fix(suggestions): refill regression — discovery_call_weekly now pushes new picks to Plex AND SuggestionsMirror is capped at SUGGESTIONS_TARGET_SIZE (30) with oldest-first eviction. NAS UAT 2026-05-17: yesterday's logs had many `sql_refill` events on play (mirror at target); today's 3 `track_played` webhooks (11:45, 14:24, 16:46 PDT) produced 0 `sql_refill` events because `maybe_schedule_refill` deficit gate returns 0 when mirror is above target — and `discovery_call_weekly` had been pumping uncapped picks into the mirror without ever pushing to Plex (Bug A) since it never called `update_playlist_items`. Restores the rate-feedback loop that makes Suggestions work as designed. New `_evict_oldest_mirror_rows_sync` (by `added_at ASC, id ASC`) + async `remove_tracks_from_suggestions_playlist` helper; discovery wired as: cap → write → ADD-to-Plex → REMOVE-from-Plex (add-first for user-visible freshness; remove in own try/except so failure doesn't undo add). NotFound self-heal idiom byte-equivalent to canonical form at suggestions_service.py:1036-1043. 9 new tests (3 evict + 3 remove + 6 integration); 192/192 pass on integrated state. No-touch verified: `refill_mirror_sql`, `maybe_schedule_refill`, `SUGGESTIONS_TARGET_SIZE`, `prune_suggestions_playlist_to_mirror`, `_write_discovery_picks_sync` byte-identical. | 2026-05-17 | 4d838cf | [260517-nkt-fix-suggestions-discovery-pushes-to-plex](./quick/260517-nkt-fix-suggestions-discovery-pushes-to-plex/) |
| 260517-p2b | fix(suggestions): Phase 8.3 one-shot startup trim — completes the refill regression fix from 260517-nkt. NAS UAT 2026-05-17: after deploying 260517-nkt the discovery WRITE cap worked, but existing 39-row mirror was never cleaned — deficit gate stayed at 0, no `sql_refill` fired on play. Fix: new `run_phase_08_3_trim_suggestions_mirror_to_target` mirrors Phase 8.1/8.2 MigrationLog gate pattern; on startup it reads current count, computes `evict_count = max(0, current - SUGGESTIONS_TARGET_SIZE)`, calls existing `_evict_oldest_mirror_rows_sync` (by `added_at ASC, id ASC`), pushes removal to Plex via existing `remove_tracks_from_suggestions_playlist`. Plex push failure (NotFound or otherwise) does NOT block the success stamp — DB trim is the loop-unstall win. Lifespan ordering: 8.0 → 8.1 → 8.2 → **8.3** → event_bus. NO new helpers — reuses 7 existing helpers from prior commits. 4 new tests (evict-when-above-target, no-op-at-or-below, idempotent, plex-notfound-handling); 196/196 pass on integrated state. | 2026-05-17 | d587d9c | [260517-p2b-fix-suggestions-one-shot-startup-trim-of](./quick/260517-p2b-fix-suggestions-one-shot-startup-trim-of/) |

## Session Continuity

Last session: 2026-05-13T01:00:00Z
Stopped at: Phase 7 context gathered via /gsd-discuss-phase. Four gray areas resolved (refill cadence + bootstrap, shortlist composition, queue UI + dismiss interaction, skip-tracking calibration); 13 implementation decisions captured in `07-CONTEXT.md`. Researcher and planner can proceed without re-asking the user. The system-prompt-caching fix (Anthropic >2048-token minimum that bit Phase 6.2) is folded into Phase 7 D-07 as the shared longer preamble — load-bearing for SC4 ("`cache_read_input_tokens` accumulating").
Resume file: .planning/phases/07-suggestions-queue-v1-chat-retirement/07-CONTEXT.md
Next actions:

  - `/gsd-plan-phase 7` to break Phase 7 into plans. Researcher reads `07-CONTEXT.md` for what to investigate; planner reads it for what's locked.
  - Phase 7 requirements: SUGG-01..11 (11 reqs), UI-01..06 (6 reqs), OPS-05 (1 req), DEBUG-03 + DEBUG-05 (2 reqs) = 20 requirements total.
  - Phase 7 ships the LLM cost circuit breaker (Pitfall 11) in the FIRST commit, not the last — daily 50 calls + 5/60s burst + 60s per-event debounce. Plan accordingly.
  - Optional cleanup queue (still deferred, doesn't block Phase 7): WR-01 finalize dead code, WR-03 zero-member vibe leak; pre-existing test failures in `deferred-items.md`.
