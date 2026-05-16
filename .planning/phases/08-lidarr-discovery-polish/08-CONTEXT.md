# Phase 8: Lidarr Discovery + Polish — Context

**Gathered:** 2026-05-16
**Status:** Ready for research / planning
**Predecessors:** Phase 7 (taste profile, Anthropic prompt-cache infra, LLM cost breaker, `/debug/suggestions` pattern) + Phase 7.1 (weekly Sunday 03:00 UTC SUGG-13 cron, SQL hot-path refill, `MaxTokensTruncationError` retry guard, `DiscoveryState` counter pattern)

<domain>
## Phase Boundary

Phase 8 closes the v2.0 loop outward and polishes legacy v1 surfaces. Three concrete deliverables:

1. **Taste-aware artist discovery** — `/discover` surfaces 10–20 artists not in your library, ranked against your taste profile, anchored on a small seed set rotated per-vibe. One-click add sends them to Lidarr with both `qualityProfileId` AND `metadataProfileId` (the v1 bug). Hallucinated names are blocked via MusicBrainz lookup before any artist ever appears on the page. Popularity-bias gate (listener-count filter + adjacency requirement) is mandatory from day one.

2. **Auto-ingest of Lidarr arrivals** — Composer-added artists eventually flow through the existing daily `library_sync` → `trigger_post_sync_analysis` → vibe slotting chain. No new trigger infrastructure; the existing daily cron is enough. `/discover` shows a per-add status timeline so the user knows where each added artist is in the pipeline.

3. **Polish on legacy v1 screens + cross-surface niceties** — Full mobile-first rewrite of the library page (UI-07/UI-08). Two-step Lidarr settings flow that fetches BOTH quality and metadata profiles dynamically. Vibe color coding propagated everywhere a vibe label renders. Home-page weekly LLM cost chip as a runaway-cost trip wire. `/debug/discovery` plain-HTML diagnostic page per the DEBUG-04 / DEBUG-05 convention.

**Phase 8 does NOT touch:**
- Vibe membership / clustering logic — Phase 6.2 owns it; this phase consumes vibes as the seed source
- Suggestions queue refill — Phase 7.1's SQL hot path + weekly LLM discovery is already cost-optimized; Phase 8 adds a SECOND weekly LLM call (artist discovery) that piggybacks the same Sunday cron tick
- v1-generated Plex playlists (no `Composer ·` prefix) — recognized as legacy, never touched (OPS-06)
- Composer writing `userRating` to Plex — Plex is the rating source of truth (PROJECT.md locked)
- The Phase 7.1 SUGG-13 weekly suggestions discovery call — keeps running as-is
- New Lidarr-OnImport webhook — explicitly deferred (see D-C2); the daily sync covers the use case
</domain>

<prior_decisions>
## Locked from Predecessor Work

**From PROJECT.md key decisions (v2.0):**
- One-click Lidarr add, never auto-add (PROJECT.md `One-click Lidarr add (not full auto)` + Pitfall 14)
- Anthropic over Ollama; explicit `cache_control={"type":"ephemeral","ttl":"1h"}` on every system message (Phase 5 OPS-02; the March 2026 default regressed to 5min and that was the original burned issue)
- LLM cost circuit breaker (daily quota + burst limit + per-event debounce) is mandatory infra (Phase 7 / Phase 7.1 `llm_cost_breaker.py` reused, NOT re-implemented)
- Mobile-first conventions baked project-wide: `h-dvh`, `env(safe-area-inset-bottom)`, ≥44px tap targets, no hover-only, `alpine-morph` on HTMX swaps (Phase 7 UI-03..05)
- Composer never touches v1-generated Plex playlists; `Composer · ` prefix + `ManagedPlaylist` registry as dual ownership markers (Phase 6 D-25 + OPS-06)
- Settings page contract: secrets never displayed or returned after entry (Phase 1 CONF-04)

**From Phase 5 / 6 / 6.2 / 7 / 7.1 carry-forward:**
- `pyarr>=6.6,<7.0` already pinned (Phase 5 OPS-03). `add_artist()` requires BOTH `qualityProfileId` AND `metadataProfileId` — fetch dynamically, never hardcode (Pitfall 14).
- All PlexAPI / pyarr sync calls in `async def` paths route through `asyncio.to_thread` (Phase 5 D-09 — enforced by AST static test; extend to `lidarr_client.py` + new `discovery_service.py` / `api_discovery.py`).
- `app/services/anthropic_client.py::call_with_structured_output` is the single entry point for Anthropic — already handles `thinking="off"|"adaptive"`, 400-fallback retry on extended-thinking unsupported, JSON-from-prose tolerance (260510-t5n), and raises `MaxTokensTruncationError` on `stop_reason="max_tokens"` (Phase 7.1 SUGG-14).
- `LLMUsage` row per call with `purpose=` field — `/debug/discovery` aggregates by purpose for the cost panel (Phase 5 D-04 / Phase 7 D-07 pattern). New purpose strings: `discovery_artist_weekly`.
- Module-level singleton + `get_state()` pattern with autouse-reset fixture in tests (Phase 5 D-08). `discovery_service` follows the same shape as `suggestions_discovery`.
- `MigrationLog` gate-row pattern for one-shot bootstrap (Phase 6.1 / 7.0 / 7.1 — `run_phase_61_migration` is the canonical shape).
- APScheduler shared singleton in `sync_scheduler.py`; new jobs added alongside `library_sync` + `plex_polling` + `weekly_maintenance_tick`. Phase 8 piggybacks on the existing `_weekly_maintenance_tick` rather than registering a second weekly job.
- Anti-pattern: `BackgroundTasks` per request → use the existing event bus singleton.
- Anti-pattern: `pydantic.Json[Model]` inside `Form()` — FastAPI bug #10997.
- Phase 7 dismiss-in-expanded-panel pattern (D-09 / D-10) is the project's confirm-before-destructive-action vocabulary.
- Phase 7.1 D-A2 candidate cap pattern (`DISCOVERY_CANDIDATE_LIMIT`, `DISCOVERY_PROMPT_TOKEN_CEILING`) is the established defense against Claude's 200K input context limit on cold-start catch-up — reuse the same shape for artist discovery prompts.

**From Pitfalls (all five Phase 8-relevant entries are locked, not re-decided):**
- Pitfall 10 — MusicBrainz validation gate on every LLM-returned artist (drops hallucinations before the user sees them).
- Pitfall 12 — Cross-surface dedup: filter out artists with any track already in `composer.tracks` OR already in Lidarr's `/api/v1/artist`; cache the managed list 1h.
- Pitfall 13 — Popularity-bias guardrails: anchor LLM on small specific seed set, listener-count filter via MusicBrainz, hard gate of adjacency-to-≥1-starred OR shared-label/release-group.
- Pitfall 14 — Lidarr connection-test fix is the FIRST task of the phase. `metadataProfileId` required alongside `qualityProfileId`. Post-add monitoring (24h check) must ship with first version of the feature.
- Pitfall 25 — Auto-ingest plumbing already mostly works via existing `trigger_post_sync_analysis()` from v1 Phase 3 (idempotent on already-analyzed tracks).
</prior_decisions>

<decisions>
## Implementation Decisions

### Area A — Discovery Pipeline

**D-A1 — Seed-first pipeline: ListenBrainz similar-artists → MusicBrainz validation gate → LLM re-rank.** *(Amended 2026-05-16 per research push-back — see 08-RESEARCH.md §1.)*
**Original D-A1** (before research): "MusicBrainz similar artists → LLM re-rank." Research surfaced that MusicBrainz does NOT expose a broad "similar artists" relation — its artist-to-artist graph is limited to `member of band`, `collaboration`, `supporting musician`, `subgroup`. Insufficient adjacency for the 50–100 candidates per seed we need.

**Locked pipeline:**
1. **ListenBrainz `similar-artists` labs endpoint** (`https://labs.api.listenbrainz.org/similar-artists`) is the candidate-source. Built from real listening-pattern co-occurrence; MetaBrainz family (sister project to MusicBrainz); free; no API key. ~50–100 similar artists per seed.
2. **MusicBrainz lookup** (`/ws/2/artist?query=name:...`) validates each candidate exists with a real MBID before any candidate enters `/discover`. Hallucination gate per Pitfall 10.
3. **Popularity-bias gate** per D-A3 (still locked from Pitfall 13).
4. **LLM re-rank** for taste fit + factual-hook-anchored rationale per D-A4.

Cheaper than LLM-first, real similarity (not just shared-band membership), MusicBrainz still the canonical hallucination gate. **Smoke test required at Plan 02 start:** ListenBrainz is on a `labs.` subdomain — researcher flagged the contract is less stable than core MB. Plan 02 first task does a manual `curl` check + writes a fixture so test suite catches future labs-endpoint breakage. Rejected: Path A (synthesize from MB rels + shared label/release-group — sparser adjacency, more rate-limit pressure), Path C (Last.fm `artist.getSimilar` — needs API key, latent tension with PROJECT.md Last.fm out-of-scope item even though that's about scrobbles-as-taste-signal, not discovery candidate source).

**D-A2 — Per-vibe rotation seeds: one starred track per vibe.**
For each of the user's 3–7 vibes, pick ONE starred track per vibe → MusicBrainz expands each into its "similar artists" list. Guarantees every vibe gets discovery candidates. Natural diversity from vibe heterogeneity. Mirrors Phase 7 D-05's "balanced across all vibes" shortlist intuition. Which track gets picked per vibe rotates week-over-week so each Sunday cron tick gets fresh seeds (planner picks the rotation mechanism — random-from-vibe / round-robin index / least-recently-used; all acceptable).

**D-A3 — Popularity-bias hard gate locked from Pitfall 13.**
Not re-decided. Enforced after MusicBrainz candidate fetch, BEFORE the LLM re-rank: (1) MusicBrainz listener-count filter — drop top-N% globally popular unless the user's library explicitly contains comparable-popularity artists; (2) hard adjacency requirement — every candidate must have MusicBrainz adjacency to ≥1 starred artist OR share a label / release-group with a starred artist. Candidates that fail are dropped silently; counts surfaced on `/debug/discovery` so the user can audit why something didn't appear.

**D-A4 — "Why this artist?" = LLM-written one-liner anchored on a factual MusicBrainz hook.**
Rationale shape: `{factual_hook} · {LLM_one_liner}`. Example: `"Same label as Four Tet · Atmospheric IDM with a strong drum-machine spine."` The factual hook (MusicBrainz adjacency / shared label / release-group) is templated from MusicBrainz data and is non-negotiable provenance. The LLM only writes the second clause as part of the re-rank call. One LLM call covers both ranking and rationale for the whole batch (cached preamble + per-call payload of N candidates → top-K with rationales).

### Area B — Refresh Cadence + Cost

**D-B1 — Artist discovery piggybacks the existing Sunday 03:00 UTC weekly cron.**
The Phase 7.1 `_weekly_maintenance_tick` in `sync_scheduler.py` (currently runs `prune_suggestions_playlist_to_mirror` + `discovery_call_weekly` for suggestions) gains a third step: `artist_discovery_call_weekly`. ONE cron tick, three operations. Same APScheduler job, no second weekly registration. Steady-state cost: estimated +$0.10–$0.30/mo on top of Phase 7.1's $0.20/mo. Net stays well under the v2.0 ~$5/year envelope.

**D-B2 — `/discover` serves cached results all week; cache invalidates only at the next cron tick.**
The Sunday cron writes the week's candidate set to a new `DiscoveryCandidate` table (or per-week JSON blob — planner picks). `/discover` reads from this cache. No LLM call on page open. Seeds rotate Sunday → Sunday. Dismissed artists (see D-D5) are subtracted at read time from the cached set, so dismiss is instant.

**D-B3 — Catch-up + failure handling mirror SUGG-13.**
On Composer startup, if `now - last_successful_artist_discovery > 7 days`, fire one immediately (mirrors Phase 7.1 D-C2). On LLM failure (timeout, parse error, breaker tripped, network), log a structured `LLMUsage` row with the error and exit; `/discover` shows the previous week's cache or an empty state. Next Sunday tries again (mirrors Phase 7.1 D-C3 — NO SQL fallback "pseudo-discovery"; failures must be observable).

**D-B4 — Home-page "LLM this week" compact chip, anchored to last cron tick, fresh-start baseline.**
On the vibes home (`/` / `/vibes`), a compact chip at the top: `"This week: $0.07 · next refresh in 4d"`. Computation: sum `LLMUsage.cost_estimate_usd` WHERE `called_at >= last_weekly_cron_tick_at`. Source of `last_weekly_cron_tick_at`: the `_weekly_maintenance_tick` updates a `WeeklyCronState` row on successful completion. Tap opens `/debug/suggestions` (existing) for per-purpose breakdown. **Initialization: a `CostMeterBaseline` row records the Phase 8 deploy timestamp; all rows in `LLMUsage` with `called_at < baseline.deploy_at` are excluded from the home-page chip permanently (historical / testing rows are irrelevant for the runaway-cost check).** The /debug/suggestions cost meter card continues to report unfiltered history.

### Area C — Auto-ingest

**D-C1 — Auto-ingest piggybacks on the existing daily `library_sync` cron. No new trigger source.**
The chain `library_sync → trigger_post_sync_analysis → Essentia analyze → handle_rating_changed slot_track (if rated)` already exists end-to-end from Phase 2/3/6. For unrated new arrivals, Essentia analysis is the last step; tracks become eligible for the SQL hot-path Suggestions refill (Phase 7.1 SUGG-12) as soon as analysis completes. Latency: up to 24h from Lidarr import to "appears in Suggestions". Acceptable because Essentia analysis itself can take hours-to-days for a full new discography — saving 10 minutes on the trigger doesn't materially help the end-to-end timeline.

**⚠ Load-bearing dependency on sync reliability — see D-E3.** This decision is only valid if the daily `library_sync` cron is in fact reliably daily. NAS UAT 2026-05-16 surfaced that `Last synced: 2026-05-14T03:01:24` was the most recent successful sync on a 24h schedule (~48h stale). D-E3 fixes the underlying cron reliability problem in Phase 8 Plan 01 BEFORE the discovery + auto-ingest features rest on it.

**D-C2 — Lidarr OnImport webhook NOT built in Phase 8. Documented as "easy to add later".**
The Pitfall 25 option of a dedicated `/api/webhooks/lidarr` endpoint + OnImport handler is documented as a future low-cost addition for debugging / faster ingest observability. NOT built now. If the daily sync ever proves insufficient (or post-add observability needs sub-day resolution), the webhook can be added without touching the rest of the discovery pipeline.

**D-C3 — Per-add status timeline lives on `/discover` AND `/debug/discovery`.**
Each Composer-initiated add gets a row in a new `DiscoveryAdd` table tracking: `mb_id`, `artist_name`, `added_at`, `lidarr_status_last_polled_at`, `lidarr_status`, `composer_sync_seen_at`, `essentia_complete_at`, `vibe_slotted_at`. `/discover` renders a status row for each add until the artist is fully ingested + vibe-slotted (see D-D4 lifecycle). `/debug/discovery` shows the full timeline of all adds ever, including completed/removed ones. Lidarr status is polled lazily on page render (cached 5 min) via `lidarr.get_history()` filtered by artist — NO aggressive 24h polling cron (rejected — daily sync is enough per D-C1). Post-add monitoring surfacing (Pitfall 14 "added 2d ago, no releases") is the same row showing `lidarr_status: searching` for >2 days with a soft warning chip.

### Area D — Discover UX + Mobile Pass

**D-D1 — Full mobile-first rewrite of the library page (UI-07/UI-08). Probably its own plan.**
Treat the library page as a v2 surface, not a polish layer: card-per-track vertical list at sub-md (replaces the wide table), filter chips at top (analyzed / unanalyzed / rated), full-width sticky search bar, sort dropdown moved to a bottom sheet. `h-dvh`, `env(safe-area-inset-bottom)`, ≥44px targets, `alpine-morph` on the HTMX search swap. Settings page service cards already stack — verify-only, no further changes there.

**D-D2 — `/discover` layout: vibe-grouped sections.**
One horizontal-scroll row per vibe: `"For your {vibe_name} vibe: 3 artists →"`. Each row shows artist cards with album-art-if-available, name, the MusicBrainz factual hook (D-A4), and a tap-to-expand affordance. Vibe-grouped layout makes provenance visually obvious (matches the per-vibe seed rotation of D-A2). Vibe section headers use the vibe color (see D-E2).

**D-D3 — Artist cards: tap-to-expand mirrors Phase 7.**
Default card state is compact (name + factual hook + vibe color chip). Tap → Alpine `x-show` toggle expands inline panel below the card with the "Why this artist?" LLM one-liner + `[Add to Lidarr]` + `[Dismiss]` buttons. Reuses Phase 7 D-09 / D-10 gesture vocabulary — no new patterns. `alpine-morph` on HTMX swaps so card state survives the Lidarr-status partial refresh.

**D-D4 — Lifecycle: after Add, card transforms into a status row; once fully ingested, REMOVED from `/discover`.**
Sequence:
1. Compact card on `/discover`
2. Tap to expand → `[Add to Lidarr]` / `[Dismiss]`
3. After Add: card swaps to a status row showing Lidarr progress + Composer ingest progress (states: `Lidarr: searching` → `downloading X of Y` → `imported, awaiting Composer sync` → `analyzed, slotted into vibes` → DONE)
4. **Once `vibe_slotted_at IS NOT NULL` AND no remaining unanalyzed tracks exist for this artist → remove the card from `/discover` entirely.** The artist is now a regular library artist; they'll surface in Suggestions / vibe playlists naturally. `/debug/discovery` retains the full timeline for audit. (User's explicit instruction: "once tracks are all added and downloads complete, remove from here as it's no longer needed (it's a typical artist)".)

**D-D5 — Dismiss = artist-only exclude. No neighborhood deboost.**
Tap `[Dismiss]` in the expanded panel → write a row to a new `DiscoveryDismissed` table keyed on `mb_id`. Future weekly cron filters dismissed artists out of MusicBrainz candidates BEFORE the LLM re-rank, so they never reappear. Does NOT propagate to MusicBrainz neighbors or labelmates — clean, predictable, no surprises (rejected: neighborhood deboost (collateral damage), label-level deboost (narrower but still surprising)). Dismiss is instant on `/discover` (subtract at read time from cached candidate set per D-B2). A future settings UI to unblock dismissed artists is out of scope for Phase 8.

### Area E — Settings + Cross-Surface Polish

**D-E1 — Two-step Lidarr settings flow: test connection → select quality + metadata profiles (+ root folder if multiple) → save.** *(Amended 2026-05-16 per research finding §2 + user decision on root folder UX.)*
Current `test_lidarr_connection` returns only quality profiles. Extend to fetch THREE lists in the same `to_thread` call: `get_quality_profile`, `get_metadata_profile`, `get_root_folder`. The connection_status partial renders dropdowns:
- **Quality profile** — always-shown dropdown
- **Metadata profile** — always-shown dropdown
- **Root folder** — auto-selected silently if Lidarr returns exactly one; rendered as a dropdown only when >1 (most users have a single music root)

`save_lidarr` persists `qualityProfileId`, `qualityProfileName`, `metadataProfileId`, `metadataProfileName`, `rootFolderPath` to the `ServiceConfig` `lidarr` row's `extras` JSON. `discovery_service.add_artist()` reads all three values from settings and passes them to `pyarr.add_artist(artist=lookup_result, root_dir=..., quality_profile_id=..., metadata_profile_id=..., monitored=True, artist_monitor="all", search_for_missing_albums=True)` per Pitfall 14 and the pyarr 6.6 signature confirmed in RESEARCH.md §2. `search_for_missing_albums=True` is intentional so post-add monitoring (Pitfall 14's 24h check) has real Lidarr activity to observe.

This is the **Lidarr connection-test fix that lands FIRST in the phase** (DISC-07 + Pitfall 14 — every other Lidarr feature is built on this).

**D-E3 — Library-sync cron reliability fix lands in Plan 01 alongside D-E1.**
NAS UAT 2026-05-16: `Last synced: 2026-05-14T03:01:24` on a 24h schedule (~48h stale). Likely causes: (a) container restart resets `IntervalTrigger` next_run_time, compounding across redeploys; (b) silent sync failure (`sync_service.py:226–229` catches the exception, marks `state=FAILED`, never writes `last_synced`). D-C1's auto-ingest design assumes the daily sync IS daily — this fix is load-bearing for the entire phase.

Scope:
- Investigate the actual cause via `docker logs composer | grep -E "Sync|sync"`, `SyncState` DB row inspection, and `/api/sync/status` state at deploy time.
- Add a lifespan-level missed-tick catch-up that mirrors Phase 7.1 D-C2: on startup, if `now - last_sync_completed > interval_hours + grace`, fire one sync immediately. Persistent state via the existing `SyncState` table.
- Add a structured failure log path so silent failures surface on `/debug/events` (route the exception into the event bus or directly into `EventLog` with `event_type="sync_failed"`). Visible failures > silent ones.
- (Optional, planner discretion) Switch from `IntervalTrigger` to `CronTrigger(hour=3, minute=0)` so the schedule is anchored to wall-clock time, not container start time. Wall-clock is more predictable across restarts.

This is the SECOND foundational task in Plan 01 (after D-E1's Lidarr connection-test fix); both must land before any discovery work.

**D-E2 — Vibe color coding: each vibe gets a persistent distinct color, propagated everywhere a vibe label renders.** *(Palette locked 2026-05-16 to Tailwind 4 `-500` stops per user decision after research §5.)*
Add `Vibe.color` column (TEXT, hex like `"#f97316"`). Colors auto-assigned at vibe creation time from the curated Tailwind 4 `-500` palette: `orange-500 #f97316`, `blue-500 #3b82f6`, `emerald-500 #10b981`, `violet-500 #8b5cf6`, `rose-500 #f43f5e`, `amber-500 #f59e0b`, `cyan-500 #06b6d4`, `pink-500 #ec4899`, `lime-500 #84cc16` (planner: order/extend as needed; ≥7 hues are required for 7-vibe max, the 9 listed give headroom). Chosen for cleanest fit to existing Tailwind 4 styling tokens, native dark-theme tuning, and minimal CSS surgery. Note: `orange-500 #f97316` is very close to Composer's existing Plex-orange accent (`#e5a00d`) — researcher flagged the collision; orange goes LAST in assignment order so it's only used at 7+ vibes when alternatives are exhausted, and `/discover` Lidarr "Add" CTA continues to use the existing `bg-accent` Plex-orange to keep system-action color reserved.

Surfaces that pick up the color:
- Home page (`/` / `/vibes`) — each vibe card uses the color as an accent (border, badge, or background tint — designer picks within Tailwind 4 tokens)
- `/suggestions` — vibe chip per row uses the matching color
- `/discover` — vibe-grouped section headers use the matching color (D-D2)
- Wizard proposal cards — vibe chip uses the matching color
- `/debug/vibes` — vibe diagnostic cards use the matching color
- `recluster_modal` — vibe pills use the matching color

Source of truth: the `Vibe.color` column. New vibes from re-cluster get a fresh palette assignment; existing vibes keep their color across re-clusters (preservation rule mirrors D-22 manual override preservation). Migration: `_migrate_add_columns()` adds the column; existing rows get auto-assigned colors on first read after deploy (or a one-shot lifespan migration — planner picks).

### Claude's Discretion

- **Rotation mechanism for D-A2 seed selection** — random-from-vibe / round-robin index on a `Vibe.last_seed_track_id` column / least-recently-used; all acceptable. Planner picks the simplest that avoids same-track-back-to-back-weeks.
- **MusicBrainz API client choice** — `musicbrainzngs` is the canonical Python library. Confirm rate-limit headers (1 req/sec free tier) handled gracefully; cache lookups by `mb_id` indefinitely in a new `MusicBrainzCache` table to amortize repeat queries across weeks.
- **Last.fm vs MusicBrainz as candidate source** — SUMMARY.md flagged this as a Phase 8 light spike. Default to MusicBrainz only (free, no API key, deterministic adjacency graph). Spike Last.fm only if MusicBrainz adjacency proves too sparse for the user's niche-leaning taste at the actual library scale.
- **Discovery LLM call shape** — single call with cached preamble (taste profile + vibe definitions, mirroring Phase 7 D-07's >2048-token cache engagement) + per-call payload of N candidates. `purpose="discovery_artist_weekly"`. `max_tokens` floor sized generously per SUGG-14 pattern with `MaxTokensTruncationError` retry doubling the budget.
- **Discovery cost ceiling** — repurpose Phase 7.1 D-D3's `WEEKLY_DISCOVERY_BUDGET_USD` ($0.50/week) as a SHARED ceiling for the entire Sunday cron tick (suggestions discovery + artist discovery combined). Tighter than two separate budgets, defends against future regressions.
- **Empty states for `/discover`**:
  - Lidarr unconfigured → "Configure Lidarr in Settings to enable Discovery" CTA. Weekly cron does NOT fire `artist_discovery_call_weekly` until Lidarr is configured.
  - No vibes yet (pre-wizard) → "Finish the setup wizard to enable Discovery" CTA. (Implied by seed-first pipeline needing vibes.)
  - Wizard done but no cron tick has run yet → "Your first discoveries land Sunday at 03:00 UTC."
- **Discovery candidate cap** — borrow Phase 7.1's `DISCOVERY_CANDIDATE_LIMIT` (200) + `DISCOVERY_PROMPT_TOKEN_CEILING` (150_000) + chars-per-token ratio (3.5) shape for the artist re-rank prompt. Defends against MusicBrainz returning a very large adjacency expansion.
- **`/debug/discovery` page layout** — plain HTML per DEBUG-05 convention; sections: (1) last weekly candidate set with provenance per artist (seed source, MusicBrainz adjacency hook, listener count, popularity-gate result, LLM rank, LLM rationale), (2) `DiscoveryAdd` timeline rows (full history), (3) recent MusicBrainz queries with rate-limit headers, (4) recent Lidarr `add_artist` requests + responses, (5) Lidarr connection-test result history. Linked from `/debug` index (already has the placeholder slot).
- **DiscoveryAdd → Lidarr status sync mechanism** — lazy poll on `/discover` render with 5-min cache. NOT an aggressive cron. Acceptable because the user opens the page intermittently and the underlying state moves slowly.
- **Color palette assignment order** — Planner picks the palette; the per-vibe assignment can be deterministic (palette[vibe_id % len(palette)]) or sequential by creation order. Either works; the user just wants distinct colors that match across surfaces.
</decisions>

<canonical_refs>
## Canonical References (MUST READ during research / planning)

**Phase scope + requirements:**
- `.planning/ROADMAP.md` §"Phase 8: Lidarr Discovery + Polish" (lines 267–289) — phase goal, dependencies, 5 success criteria, full Key Concerns list
- `.planning/REQUIREMENTS.md` DISC-03..07 (5 reqs), UI-07/UI-08 (2 reqs), OPS-06 (1 req), DEBUG-04 (1 req) = 9 requirements; planner will need to ADD a small set of new requirements for the home-page weekly cost chip (D-B4) + vibe color coding (D-E2) — these emerged in this discussion and are not yet in REQUIREMENTS.md.
- `.planning/PROJECT.md` §"Key Decisions" — locked v2.0 decisions, especially: one-click Lidarr add (not auto), Anthropic over Ollama, hands-off existing Plex playlists, mobile-first, event-driven (no scheduled refreshes EXCEPT the existing weekly cron tick)

**Research backing the locked decisions:**
- `.planning/research/SUMMARY.md` lines 266–296 (Phase 8 ship gate, "leafy" ordering, light spike call-outs for MusicBrainz/Last.fm + metadataProfileId) — and lines 119, 121, 308 (auto-ingest closes the loop; Plex Pass branching)
- `.planning/research/PITFALLS.md` Pitfalls 10, 12, 13, 14, 25 (Phase 8's full pitfall surface)
- `.planning/research/ARCHITECTURE.md` §"Phase 8: Lidarr discovery + polish" (lines 578–589) — service breakdown + ship gate; §"Integration Gotchas" Lidarr row (line 654) — connection-test bug context

**Phase 5 / 6 / 7 / 7.1 carry-forward (NOT re-decided here):**
- `.planning/notes/connection-test-bugs.md` — original v1 Lidarr connection-test bug report. D-E1 is the fix.
- `.planning/notes/mobile-first.md` — mobile-first design conventions; D-D1 / D-D3 inherit.
- `.planning/notes/phase-07-followup-cost-architecture.md` — SOURCE OF TRUTH for the Phase 7.1 weekly-cron + SQL-hot-path architecture that Phase 8 piggybacks on (D-B1).
- `.planning/phases/07-suggestions-queue-v1-chat-retirement/07-CONTEXT.md` D-07 (system-prompt cache engagement / >2048-token preamble), D-09 / D-10 (tap-to-expand dismiss pattern) — Phase 8 reuses both verbatim.
- `.planning/phases/07.1-suggestions-cost-architecture-sql-refill-weekly-discovery/07.1-CONTEXT.md` D-A1..D-A3 (discovery-eligible / adaptive picks pattern that ARTIST discovery mirrors), D-C1..D-C3 (weekly cron + catch-up + failure handling), D-D3 (`WEEKLY_DISCOVERY_BUDGET_USD` shared ceiling) — Phase 8 mirrors most of these.

**Codebase canonical paths (downstream agents MUST read before touching):**
- `app/services/lidarr_client.py` — current `test_lidarr_connection` (49 lines, returns only quality profiles). D-E1 extends this.
- `app/services/anthropic_client.py` — `call_with_structured_output` entry point; `MaxTokensTruncationError`; constants & cache pattern. New `purpose="discovery_artist_weekly"` flows through here.
- `app/services/llm_cost_breaker.py` — `check_or_raise` cost-breaker; Phase 8 wraps the discovery call.
- `app/services/sync_scheduler.py` — `_weekly_maintenance_tick` registration; Phase 8 ADDS a third call inside the existing tick (D-B1), NOT a second weekly job.
- `app/services/suggestions_discovery.py` — Phase 7.1 weekly discovery shape; `compute_discovery_eligible`, `discovery_call_weekly`, `DISCOVERY_*` constants. Artist discovery mirrors this structure.
- `app/services/analysis_service.py::trigger_post_sync_analysis` (line 299) — the auto-ingest tail. D-C1 piggybacks; no changes needed here.
- `app/services/sync_service.py` (line 221) — already calls `trigger_post_sync_analysis()` after sync. D-C1 piggybacks; no changes needed here.
- `app/services/taste_profile_service.py` — cached preamble pattern; the discovery LLM call extends this shape with vibe definitions + candidate list per call.
- `app/routers/api_settings.py` (lines 209–266) — Lidarr endpoints. D-E1 modifies `test_lidarr` + `save_lidarr` to add the metadata profile dropdown.
- `app/routers/pages.py::read_settings` — cost-meter UI query (LLMUsage GROUP BY DATE). D-B4 home-page chip reuses similar query shape with the baseline filter.
- `app/routers/pages.py::read_debug_*` — pattern for `/debug/*` plain-HTML diagnostic pages with last-N tables. D-? `/debug/discovery` follows this shape.
- `app/templates/pages/debug_index.html` — the `/debug` index ALREADY has a placeholder slot for `/debug/discovery (Phase 8 — not yet shipped)`. Just unmask + link.
- `app/templates/pages/discover_placeholder.html` — Phase 7 placeholder page; replaced by Phase 8 with the real vibe-grouped layout.
- `app/templates/pages/library.html` — current wide-table layout; D-D1 full mobile rewrite.
- `app/templates/partials/llm_cost_meter.html` — Phase 7 OPS-05 daily cost card on settings; reference pattern for D-B4 (different query shape, different placement, but same Jinja structure).
- `app/templates/base.html` — `<body hx-ext="alpine-morph">` + `<main>` padding for bottom tab bar. NO changes needed for Phase 8.
- `app/templates/partials/bottom_tab_bar.html` — already includes the `/discover` tab (Phase 7); the Discover entry simply starts resolving to real content.
- `app/templates/partials/recluster_modal.html` — vibe pills will pick up the new color from D-E2.
- `app/templates/partials/connection_status.html` — Lidarr quality-profile dropdown today; D-E1 adds a sibling metadata-profile dropdown.
- `app/models/vibe.py` — `Vibe`, `TrackVibe`, `DiscoveryState`; D-E2 adds `Vibe.color`.
- `app/models/suggestions.py` — `SuggestionsMirror`, `RefillTriggerLog`, `SuggestionHistory`. Phase 8 ADDS new models alongside (`DiscoveryCandidate`, `DiscoveryAdd`, `DiscoveryDismissed`, `MusicBrainzCache`, `CostMeterBaseline`, `WeeklyCronState`) — exact split between modules is planner's call; could fit in a new `app/models/discovery.py`.
- `app/database.py::_migrate_add_columns` — additive migration shim. Phase 8 uses it for `Vibe.color` and any other column additions.
- `app/main.py` lifespan — `run_phase_61_migration` / `run_phase_07_suggestions_bootstrap` pattern; Phase 8 adds `run_phase_08_discovery_bootstrap` (gates `CostMeterBaseline` + `Vibe.color` backfill + `DiscoveryDismissed` table creation).
- `.planning/quick/260514-e6w-*` and `260512-kvs-*` — `max_tokens` defensive sizing pattern. Phase 8 artist discovery call inherits via the SUGG-14 retry guard already in `anthropic_client.py`.

**MANDATORY — research already produced:**
- `.planning/phases/08-lidarr-discovery-polish/08-RESEARCH.md` (committed `3f6ca06`, 1,139 lines) — light-spike answers + pyarr 6.6 API surface map + ListenBrainz/MusicBrainz strategy + vibe palette comparison + APScheduler reliability pattern + Pitfall integration. Critical findings folded into amended D-A1, D-E1, D-E2 above. Plan 02 task 1 MUST run the ListenBrainz endpoint smoke-test per RESEARCH.md §3.
- **Phase 8 validation architecture** is collocated inside RESEARCH.md §"Validation Architecture" (lines ~1002–1056) rather than a standalone VALIDATION.md file — covers test framework, per-requirement test map, sampling rate, and Wave 0 test-file gaps. Plan-checker may flag the missing standalone file; the demotion to warning was accepted because the equivalent content exists in RESEARCH.md.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`app/services/anthropic_client.py::call_with_structured_output`** — single entry point for all Anthropic calls; handles `thinking="off"|"adaptive"`, 400-fallback retry, JSON-from-prose tolerance, and `MaxTokensTruncationError`. Discovery adds `purpose="discovery_artist_weekly"` here. NO new SDK client.
- **`app/services/llm_cost_breaker.py::check_or_raise`** — circuit-breaker entry point. Discovery call wraps in `check_or_raise(purpose="discovery_artist_weekly")` before invoking Anthropic.
- **`app/services/sync_scheduler.py::_weekly_maintenance_tick`** — Phase 7.1 weekly cron. Phase 8 ADDS a third step inside this tick: `await artist_discovery_call_weekly()`. No new APScheduler job registration.
- **`app/services/suggestions_discovery.py`** — closest analog for the artist discovery service shape. `compute_adaptive_pick_count`, `compute_discovery_eligible`, `discovery_call_weekly`, `DISCOVERY_*` constants. Mirror the file layout in a new `app/services/discovery_service.py`.
- **`app/services/analysis_service.py::trigger_post_sync_analysis`** — auto-ingest tail. Already wired into `sync_service.py` line 221. D-C1 piggybacks. NO code change here.
- **`app/services/taste_profile_service.py`** — cached preamble (>2048 tokens, padded for Sonnet 4.6 cache minimum). The discovery LLM call EXTENDS this preamble with vibe definitions + popularity-gate context.
- **`app/services/plex_playlist_service.py::is_managed_playlist`** — `Composer · ` prefix check; OPS-06 legacy recognition. Phase 8 doesn't create playlists but should respect this when surfacing any playlist references.
- **`app/services/event_handlers.py::handle_rating_changed`** — once a Lidarr-imported new track is rated, this path slots it via `vibe_service.slot_track`. Phase 8 doesn't modify; just relies on the chain.
- **`app/routers/api_settings.py::test_lidarr` / `save_lidarr`** (lines 212–266) — D-E1 modifies these two endpoints + `partials/connection_status.html` to add the metadata-profile dropdown.
- **`app/routers/pages.py::read_debug_vibes`** + `app/templates/pages/debug_vibes.html` — pattern for `/debug/*` plain-HTML pages with last-N tables. `/debug/discovery` follows this shape.
- **`app/templates/partials/llm_cost_meter.html`** — Phase 7 daily cost card. Reference shape for the home-page D-B4 weekly chip (smaller, different query, baseline-filtered).
- **`app/templates/partials/bottom_tab_bar.html`** — already has the `/discover` entry (Phase 7). No template change here.
- **`app/templates/pages/debug_index.html`** — Phase 7 already has the `/debug/discovery (Phase 8 — not yet shipped)` placeholder slot; just unmask + link.

### Established Patterns

- **PlexAPI / pyarr sync calls wrapped in `asyncio.to_thread`** (Phase 5 D-09). Any pyarr call from an `async def` MUST be wrapped. Extend the AST static test to cover `discovery_service.py` and any `api_discovery.py` routes (planner: this is a known surface-extension of the existing enforcement).
- **Module-level singleton + `get_state()`** for new services (Phase 5 D-08). `discovery_service` follows.
- **`MigrationLog`-gated lifespan bootstrap** (Phase 6.1 / 7.0 / 7.1). Phase 8 adds `run_phase_08_discovery_bootstrap`: creates `CostMeterBaseline` row + backfills `Vibe.color` for existing vibes + creates empty `DiscoveryCandidate` / `DiscoveryAdd` / `DiscoveryDismissed` / `MusicBrainzCache` tables (via `create_all()` for new tables, `_migrate_add_columns()` for `Vibe.color`).
- **`LLMUsage` logging on every Anthropic call** (Phase 5 D-04). `purpose="discovery_artist_weekly"`. `/debug/discovery` cost panel aggregates by purpose.
- **HTMX swap + `alpine-morph`** (Phase 6 UI-05). `/discover` artist cards use morph swaps to preserve expand/collapse Alpine state.
- **Anthropic prompt cache engagement** (Phase 7 D-07). Shared longer preamble = vibe definitions + taste profile + Composer context. Target >2048 tokens. ONE cache namespace serves vibe assignment, suggestions ranking, AND artist discovery.
- **Tight per-handler `except` clauses** — no `except Exception` in route handlers (Phase 5 / 6 / 6.2 / 7 / 7.1 convention).
- **Dismiss-in-expanded-panel** (Phase 7 D-09 / D-10). `/discover` artist cards inherit verbatim.
- **Defensive `max_tokens` floor + `MaxTokensTruncationError` retry** (Phase 7.1 SUGG-14). Artist discovery call inherits via the existing `anthropic_client.py` plumbing.

### Integration Points

- **APScheduler hot point** — `sync_scheduler.py::_weekly_maintenance_tick` adds `artist_discovery_call_weekly()` as the third step (after `prune_suggestions_playlist_to_mirror`, after `discovery_call_weekly` for suggestions).
- **Lifespan migration** — `app/main.py` lifespan adds `await run_phase_08_discovery_bootstrap()` after `run_phase_07_suggestions_bootstrap`. Same MigrationLog gate pattern.
- **Settings page additions** — `app/routers/api_settings.py` extends `test_lidarr` to fetch `get_metadata_profile`; `save_lidarr` persists `metadata_profile_id` + `metadata_profile_name` to ServiceConfig.extras.
- **Auto-ingest chain** — NO changes. The existing `library_sync → trigger_post_sync_analysis → Essentia → handle_rating_changed slot_track (if rated)` chain handles Lidarr-imported tracks.
- **Home-page weekly cost chip** — `app/routers/pages.py` for `/` / `/vibes` adds a small partial render at the top of `vibes_home.html`. Query: `SELECT SUM(cost_estimate_usd) FROM llm_usage WHERE called_at >= (SELECT last_tick_at FROM weekly_cron_state LIMIT 1) AND called_at >= (SELECT deploy_at FROM cost_meter_baseline LIMIT 1)`.
- **Vibe color propagation** — `Vibe.color` read in any template that renders a vibe label. Add a `{{ vibe.color }}` Jinja variable or pass via context dict. Surfaces: home (vibes_home.html), suggestions (suggestions.html row partials), discover (discover.html new vibe-section partials), wizard proposal cards (setup_step3.html), `/debug/vibes`, recluster_modal.

### New Models Needed (planner finalizes module split)

- `DiscoveryCandidate` — weekly cron output; `mb_id`, `artist_name`, `seed_track_id`, `seed_vibe_id`, `mb_listener_count`, `popularity_gate_pass`, `llm_rank`, `llm_rationale`, `factual_hook`, `created_at` (= cron tick timestamp). Replaced wholesale each Sunday.
- `DiscoveryAdd` — per Composer-initiated add lifecycle. `mb_id`, `artist_name`, `added_at`, `lidarr_status`, `lidarr_status_polled_at`, `composer_sync_seen_at`, `essentia_complete_at`, `vibe_slotted_at`, `removed_from_discover_at`. Drives the `/discover` status row (D-D4) and `/debug/discovery` timeline.
- `DiscoveryDismissed` — `mb_id`, `artist_name`, `dismissed_at` (single-column UNIQUE on mb_id). Read at `/discover` render time and subtracted from `DiscoveryCandidate` (D-D5).
- `MusicBrainzCache` — `mb_id`, `payload_json`, `cached_at`. Indefinite cache, amortizes repeat lookups.
- `CostMeterBaseline` — single-row id=1: `deploy_at` set at lifespan migration. D-B4 home chip filter.
- `WeeklyCronState` — single-row id=1: `last_tick_at` updated on every successful `_weekly_maintenance_tick`. D-B4 home chip + D-B3 catch-up gate.
- `Vibe.color` — additive column on existing `Vibe` table (D-E2). Backfilled in lifespan migration.
</code_context>

<specifics>
## Specific Ideas / References

- **Plan 01 is foundational: Lidarr connection-test fix (D-E1) + library-sync cron reliability fix (D-E3).** Per Pitfall 14 and the user's explicit instructions in this discussion: every Lidarr feature is built on a reliable connection test; every auto-ingest behavior is built on a reliable daily sync. NEITHER can be deferred to a later plan. Both land in Plan 01 before any discovery work proceeds. Plan 01 ship gate: connection-test returns quality + metadata profiles, both dropdowns persist; daily sync is observably running on schedule with missed-tick catch-up on lifespan; silent sync failures surface on `/debug/events`.
- **Mirror Phase 7.1 SUGG-13's discovery shape, including the candidate cap.** The `DISCOVERY_CANDIDATE_LIMIT` (200) + `DISCOVERY_PROMPT_TOKEN_CEILING` (150_000) defense in `app/services/suggestions_discovery.py` exists because the 200K Claude input context limit bit at cold-start catch-up. Artist discovery's MusicBrainz adjacency expansion can also produce a large candidate set per vibe × 7 vibes — apply the same pattern.
- **Vibe color palette must look good on the dark theme already in `app/static/css/output.css`.** Hard constraint — Composer is dark-only. The palette must be color-blind-safe AND saturation-balanced for dark backgrounds. Researcher should propose 2–3 candidate palettes; the user picks one during planning.
- **Reuse `_weekly_maintenance_tick`, NOT a second APScheduler job.** Phase 7.1 explicitly designed this tick to be the cron entry-point for cost-bounded LLM work. Adding a second weekly job duplicates the catch-up logic and complicates testing.
- **DiscoveryAdd Lidarr-status polling is lazy on render with 5-min cache.** NOT a cron. Phase 7.1's cost-discipline principle: don't add scheduled work without a strict need. The user opens `/discover` intermittently; lazy poll is enough.
- **The home-page chip is a runaway-cost trip wire, not a billing dashboard.** D-B4's small footprint is intentional. Detailed breakdown stays on `/debug/suggestions` (existing cost meter card). The chip just shows "$X.XX · next refresh in Nd" so the user notices if something is wrong WITHOUT having to deep-link to debug.
</specifics>

<deferred>
## Deferred Ideas (Out of Scope for Phase 8)

- **Lidarr OnImport webhook + `/api/webhooks/lidarr` endpoint** — D-C2 explicitly defers. Easy to add later for sub-day ingest observability if the daily sync proves insufficient. Documented as a known future addition.
- **Last.fm as a parallel candidate source** — D-A1's seed-first pipeline goes through MusicBrainz only. If MusicBrainz adjacency proves too sparse for the user's niche-leaning taste, Last.fm becomes a fallback / supplement (likely Phase 8.1 or a quick task).
- **Dismiss neighborhood deboost** — D-D5 explicitly chose artist-only exclude. Soft-deboost of MusicBrainz neighbors or shared-label artists could be added in a future Phase 8 polish or Phase 9, IF the user finds dismiss-only doesn't capture enough negative signal.
- **Settings UI to unblock dismissed artists** — D-D5 says dismiss is permanent in v1. An "Unblock dismissed artists" settings panel is a small future addition; out of scope here.
- **Configurable discovery cadence** — D-B1 hardcoded Sunday 03:00 UTC piggyback. Exposing a configurable cron via env var or settings is a trivial future addition if needed.
- **Per-vibe discover-this-vibe button on `/vibes`** — instead of cron-driven cross-vibe rotation, let the user tap a vibe and trigger discovery scoped to that vibe. Adds an on-demand cost path; out of scope here (cost ceiling concerns).
- **Discover page polish: album art for MusicBrainz-only artists** — MusicBrainz doesn't reliably ship cover art for artists you don't yet have. Could integrate Cover Art Archive or fall back to a placeholder/initials avatar. Out of scope; planner picks a placeholder for v1.
- **"Recommend Composer's added artists back to me later" loop** — Once Lidarr fully ingests a Composer-added artist's discography, those tracks become eligible for Suggestions via the existing pipeline; no special handling needed. The user gets the loop closure naturally.
- **Bulk add ("add all 10 artists in this section")** — D-D3 is one-at-a-time confirmation. Bulk add is convenient but amplifies the failure modes of Pitfall 14 (some artists may fail for quality reasons); out of scope for v1.
- **Vibe color customization UI** — D-E2 has the system auto-assign colors from a palette. User-picked color per vibe is nice-to-have polish; out of scope for v1.
- **Mobile-first rewrite of additional v1 surfaces beyond library** — D-D1 scopes to library page (the worst offender). Settings page service cards already stack. If other v1 surfaces (the welcome page, the analysis progress banner) need a polish pass, it's a future quick task.
</deferred>

<scope_guard>
## Out of Scope (Will Not Be Done in This Phase)

- Changes to vibe clustering or membership decisions (Phase 6.2 territory)
- Changes to the Suggestions queue refill mechanism (Phase 7.1 owns SQL hot path + weekly LLM discovery)
- Composer writing `userRating` to Plex (PROJECT.md key decision; reads only)
- Touching v1-generated Plex playlists without `Composer · ` prefix (OPS-06)
- Adding Alembic — `_migrate_add_columns()` shim + `create_all()` continues to suffice
- Multi-user / authentication (single-user app)
- Lidarr "auto-add" (always one-click confirm — PROJECT.md + Pitfall 14)
- New LLM provider beyond Anthropic (locked v2.0 — PROJECT.md)
- A separate microservice for discovery (single Docker container — PROJECT.md constraint)
- Real-time WebSocket updates on `/discover` (lazy poll on render is sufficient)
- Spike Last.fm beyond a light feasibility check (researcher confirms during research phase only)
</scope_guard>

---

*Phase: 08-lidarr-discovery-polish*
*Context gathered: 2026-05-16*
