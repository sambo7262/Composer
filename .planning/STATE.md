---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: — Music Companion
status: executing
stopped_at: "Phase 5 Plan 02 complete"
last_updated: "2026-05-09T20:00:00.000Z"
last_activity: 2026-05-09 -- Phase 05 Plan 02 (polling + auto-backfill + Resync now) shipped
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 16
  completed_plans: 14
  percent: 88
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-08)

**Core value:** Your Plex stars are the truth. Composer turns them into living vibe playlists and a steady stream of personalized discoveries — without you having to describe a vibe each time.
**Current focus:** Phase 05 — Plex Event Foundation + Rating Sync

## Current Position

Phase: 05 (Plex Event Foundation + Rating Sync) — EXECUTING
Plan: 3 of 4 (Plans 01–02 ✅ complete)
Status: Plan 02 shipped — polling fallback + Track view_count + auto-backfill + Resync now all wired; ready for Plan 03
Last activity: 2026-05-09 -- Phase 05 Plan 02 complete (3 commits, 16 new tests green, 0 regressions)

### v2.0 Phase Snapshot

| Phase | Goal (one-line) | Status |
|-------|-----------------|--------|
| 5 — Plex Event Foundation + Rating Sync | Composer reliably ingests Plex webhook + polling events, dedupes them, propagates `RatingChanged` end-to-end | Next up |
| 6 — Vibe Clustering + Setup Wizard | First-run wizard ends with 3–7 named vibe playlists in Plex, auto-slotting newly-rated tracks | Pending |
| 7 — Suggestions Queue + v1 Chat Retirement | Continuous Composer · Suggestions playlist drains and refills; vibes home is the new landing page | Pending |
| 8 — Lidarr Discovery + Polish | Taste-aware artist discovery, one-click add, auto-ingest of arrivals, mobile responsive pass | Pending |
| 9 — Feed the Engine (OPTIONAL) | Bulk rating, play-rated nudge, Surprise Me — cuttable | Pending |

## Performance Metrics

**Velocity (v1.0 — shipped):**

- Total plans completed: 12
- Phases completed: 4 of 4 (v1.0)
- Total v1.0 execution time: ~57 min across plans (logged below)

**By Phase (v1.0):**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 3 | ~14min | ~4.7min |
| 2 | 3 | ~20min | ~6.7min |
| 3 | 3 | ~11min | ~3.7min |
| 4 | 3 | ~12min | ~4.0min |

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

## Accumulated Context

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

### Pending Todos

- ✅ Phase 5 Plan 01 (event foundation + schema migration) — COMPLETE
- ✅ Phase 5 Plan 02 (poll service + RATE-04 view_count handler + auto-backfill + Resync now) — COMPLETE
- Phase 5 Plan 03 — anthropic_client.py with prompt caching + taste profile builder + RATE-05 LLM summary
- Phase 5 Plan 04 — wizard UI (3-candidate detection), `/debug/events`, ollama_client deletion
- Confirm Docker-network webhook URL during Phase 5 Plan 04 wizard build (`http://composer:8085/api/webhooks/plex` on `synobridge`)
- Light spike at Phase 8 planning to confirm pyarr 6.6 `add_artist()` signature, MusicBrainz adjacency query rate limits, Last.fm vs MusicBrainz as candidate source

### Blockers/Concerns

- None blocking Phase 5. The `[Research flag]` Essentia Docker note from v1 was resolved in Phase 3 (manylinux wheel).
- Phase 5 risk-watch: dedupe + `userRating` 0–10 scale + PlexAPI sync→async wrapping must all land in the FIRST commit of the phase (per PITFALLS 1, 2, 4).
- Phase 7 risk-watch: LLM cost circuit breaker must ship with the first ranking call commit, not as a follow-up (PITFALL 11).

## Session Continuity

Last session: 2026-05-09T20:00:00.000Z
Stopped at: Phase 5 Plan 02 complete — orchestrator should spawn Plan 03
Resume file: .planning/phases/05-plex-event-foundation-rating-sync/05-03-PLAN.md
