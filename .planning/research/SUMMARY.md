# Research Summary — Composer v2.0 Music Companion

**Project:** Composer v2.0 — Music Companion
**Domain:** Event-driven continuous music recommendation on self-hosted Plex/Lidarr stack
**Researched:** 2026-05-08
**Confidence:** HIGH (stack additions verified against PyPI/official docs; architecture grounded in observable v1 codebase; pitfalls sourced from Plex official docs, Anthropic SDK, and v1 production history)

---

## Locked User Decisions (Scoping Constraints)

These were finalized during milestone discovery and are **not revisitable** during requirements or roadmap stages. Surface them as constraints everywhere.

| Decision | What It Means for Roadmap |
|----------|--------------------------|
| Vibe definition: hybrid (AI proposes 3–7 clusters, user names/finalizes) | Setup wizard is P1; can't defer it |
| Rating source: Plex `userRating` only; Composer never writes ratings | Rating sync is the Phase 5 foundation; no UI rating widget needed |
| v1 mood-chat: retired in v2.0 | Vibes home replaces the current landing page in Phase 7 |
| Suggestions queue: ~30 tracks, drains as you listen (event-driven) | No scheduled refresh anywhere on the Suggestions path |
| Similarity: LLM clusters + audio-feature distance slotting; no global rescoring | Cheap hot path; LLM cost only on consumption refill and discovery |
| Plex events: webhooks primary, polling fallback | Phase 5 must implement both; dedupe from day one |
| Plex Pass confirmed for this user | Webhooks are the expected happy path; polling is safety net |
| Lidarr: in-scope for taste-aware discovery + auto-ingest | Phase 8 work, depends on Phases 5–7 |
| Mobile: portrait-first on every new screen | Design constraint on every phase, not a separate phase |
| Existing user Plex playlists: hands-off | `Composer · ` namespace is mandatory; never touch others |
| Skip-tracking / negative signal | Played-but-not-rated and dismissed-from-queue feed into ranking |
| "Why this track?" rationale | One-line templated explanation per suggestion (not LLM) |
| Vibe coverage indicator | Surface lopsided vibes; offer to backfill underweighted vibes |
| "Feed the engine" mini-phase | Bulk rating session + play-rated nudge + Surprise Me — Phase 9, optional |

---

## Executive Summary

Composer v2.0 is a continuous music companion that pivots from v1's one-shot mood-to-playlist model to a persistent, rating-anchored curation loop. The core mechanic: Plex `userRating` stars (read-only from Plexamp, 0–10 internal scale) are the taste signal. From that signal, the system derives 3–7 persistent user-named vibe playlists via LLM-assisted k-means clustering, auto-slots newly-rated tracks into matching vibes via audio-feature Euclidean distance, and maintains a continuous "Composer Suggestions" Plex playlist (~30 tracks) that drains as the user listens and refills via shortlist-then-LLM-rank. Lidarr taste-aware discovery closes the loop by surfacing new artists and auto-ingesting their imports into the pipeline.

The recommended approach builds directly on the v1 stack (Python 3.12, FastAPI, SQLModel/SQLite, HTMX/Alpine.js/Tailwind, PlexAPI, Anthropic via SDK, Essentia, APScheduler) with two net-new dependencies: `anthropic>=0.100` (replaces the direct-httpx Anthropic wrapper; enables native prompt caching) and `scikit-learn>=1.8` (k-means + silhouette for vibe clustering). All other v2 surface area — webhook reception, polling fallback, event bus, clustering, Suggestions queue, discovery — is new service/router code following existing v1 patterns: singleton services with module-level status dataclasses, asyncio.Queue event dispatch, HTMX-driven partials, SQLite via SQLModel. No new infrastructure; no second container.

The primary risks are operational, not architectural. Plex webhooks deliver duplicates with no idempotency key (build the dedupe key from `event_type|ratingKey|timestamp_bucket` and insert into `EventLog` with a `UNIQUE` constraint on day one). Anthropic's prompt-cache TTL silently changed from 1h to 5min in March 2026 — must specify `ttl: "1h"` explicitly or every LLM call pays cache-write overhead with no hit amortization, blowing the $5/year budget. PlexAPI is fully synchronous and will stall the asyncio event loop inside `async def` handlers — all PlexAPI calls must run in `asyncio.to_thread()` or via a sync endpoint dispatched to FastAPI's threadpool. These three issues, if missed, cost real debugging time or real money. The LLM cost circuit breaker (daily quota + burst limit) must exist from the first phase that introduces ranking, not as a post-launch addition.

---

## Key Findings

### Recommended Stack (v2.0 Additions Only)

The v1.0 stack is **unchanged**. See `.planning/research/v1.0/STACK.md` for the full foundation. v2.0 adds:

| Dependency | Version | Purpose | Notes |
|------------|---------|---------|-------|
| `anthropic` | `>=0.100,<1.0` | Replace direct-httpx Anthropic calls | Native `cache_control` + typed usage responses. No Instructor; manual `model_validate_json()` is cleaner with caching. |
| `scikit-learn` | `>=1.8,<2.0` | k-means + silhouette for vibe clustering | amd64 manylinux wheel; ~50MB image delta; k-means++ init avoids local minima; silhouette `sample_size` handles 10k+ rated tracks later. |
| `numpy` | `>=1.26` | Per-event distance scoring | Already present transitively; use directly for `np.linalg.norm(features - centroids)` on the hot path. |
| `python-multipart` | `>=0.0.24` | Parse Plex webhook multipart/form-data | Already in v1 requirements.txt; load-bearing for the webhook receiver. |

**One pin to fix in requirements.txt:**
```diff
- pyarr>=5.2,<6.0
+ pyarr>=6.6,<7.0
```
(v1 dev left this at 5.x despite STACK.md recommending 6.6; the Lidarr code path in Phase 8 needs the bump.)

**What NOT to add:** Celery/RQ/Redis (single-user single-container; APScheduler is enough), LangChain/LiteLLM (two LLM call sites do not warrant a framework), Alembic (use the existing `_migrate_add_columns()` shim for additive column changes and `create_all()` for new tables — ARCHITECTURE.md and PITFALLS.md disagree here; the shim wins for v2 scope), `pydantic.Json[Model]` inside `Form()` (open FastAPI bug #10997), Instructor (removed in v1; fights prompt caching).

**Frontend:** Tailwind CSS 4 already ships native container queries (`@container`, `@sm:`), dynamic viewport units (`h-dvh`, `min-h-dvh`), and `portrait:`/`landscape:` modifiers. Use `h-dvh` for full-height mobile layouts (not `h-screen`/`100vh`) and `env(safe-area-inset-bottom)` for any fixed-bottom bars.

**Sonnet 4.6 cache minimum:** Prompt caching requires **2,048 token minimum** on Sonnet 4.6 (vs 1,024 on earlier models). Verify `usage.cache_creation_input_tokens > 0` on first call; if zero, the system message is too short and caching is silently disabled.

---

### Expected Features

**Must have — table stakes (launch with all of these):**
- Plex `userRating` sync: webhook (`media.rate`) primary, 5-min polling fallback, manual "Resync now" button
- First-run setup wizard: rating-source confirm → sync ratings → propose K vibes → user names/edits → generate first Suggestions queue; cold-start gate for <50 rated tracks
- 3–7 persistent vibe playlists: LLM-proposed, user-finalized, pushed to Plex as `Composer · {name}`
- Auto-slot newly-rated tracks via audio-feature Euclidean distance to vibe centroids (soft membership: up to 2 vibes per track)
- Continuous "Composer Suggestions" Plex playlist (~30 tracks): drains on `media.scrobble`, refills via shortlist + LLM re-rank
- "Why this track?" templated explanation per suggestion (not LLM — generated from feature proximity + seed artist)
- Mobile portrait-first UI: bottom tab bar (Vibes / Suggestions / Discover / Settings), 44px tap targets, `h-dvh`, `safe-area-inset-bottom`
- Lidarr taste-aware artist discovery with one-click add and popularity-bias demotion
- Auto-ingest Lidarr arrivals into Essentia + scoring pipeline
- Vibe coverage indicator: surface lopsided vibes, offer backfill suggestions
- Manual re-cluster button (rare escape hatch, behind confirm modal)
- Skip-tracking / negative signal: played-but-not-rated and dismissed-from-queue feed into ranking
- Retire v1 mood-chat UI; vibes home becomes landing page
- Fix Lidarr connection-test bug carried from v1

**Should have — competitive differentiators (shipping in v2.0 per locked decisions):**
- Event-driven Suggestions refill (never scheduled)
- Prompt-cached taste profile (Anthropic `cache_control` with explicit `ttl: "1h"`)
- LLM cost circuit breaker: daily quota (50 calls), burst limit (5/60s), per-event debounce
- Suggestion history table (14-day exclusion window for previously shown tracks)
- Vibe hands-off principle: `Composer · ` namespace + `ManagedPlaylist` registry as dual ownership markers; exclusion list for user-removed tracks
- Plex playlist post-push verification (re-fetch and compare to detect silent drops)

**"Feed the engine" mini-phase (Phase 9, optional):**
- Bulk rating session surface ("rate tracks from your library to train your vibes")
- Play-rated nudge ("you have N highly-rated tracks you haven't heard in a while")
- Surprise Me (queue a random high-rated but recently-unplayed track)

**Defer to v2.1+:**
- Per-vibe seed mode ("give me 10 more like this rated track")
- Vibe taste-drift indicator ("your vibe has drifted X% from your original centroid")
- Configurable Suggestions queue size (default 30; range 10–100)
- Skip / "not for me" in-Composer button
- Vibe playback statistics

**Anti-features — do not build:**

| Temptation | Hard No |
|-----------|---------|
| Celery / RQ / Redis | Single-container constraint; APScheduler handles everything |
| Nightly auto-refresh of vibes | Violates event-driven constraint; vibes are user-identity, not weekly output |
| In-app rating UI | Plex is the rating source of truth; write-back creates conflict |
| In-app playback / now-playing bar | Plexamp handles this; deep-link via `plex://` URI |
| Writing to user-created Plex playlists | One accidental overwrite destroys trust permanently |
| Mood-chat preservation (two-mode UX) | Companion model replaces it; split mental model is worse |
| Multi-user / per-user vibes | Out of scope per PROJECT.md |
| Multi-vibe membership >2 per track | Noise; cap at 2 |
| LangChain / LiteLLM | Two LLM call sites don't need a framework; hides cache_control semantics |
| Instructor | Removed in v1; reformats system messages and fights prompt caching |
| Recommending 1–2 star tracks | Hard-exclude from all suggestion paths |

---

### Architecture Approach

v2.0 extends the v1 process-architecture rather than adding infrastructure. The overarching rule: **single container, single process, single SQLite file, in-process asyncio.** All v2 services follow the v1 singleton-status pattern (`module-level _status dataclass + get_*_status() + run_*() + stop_()`). New work routes through a single `asyncio.Queue` event bus with one dispatcher task (started in FastAPI `lifespan`), not per-request `BackgroundTasks`.

**New services (all following v1 singleton pattern):**
1. `event_bus` — `asyncio.Queue` + dispatcher; persists raw events to `EventLog` for dedupe + audit trail
2. `api_webhooks` router — receives `POST /api/webhooks/plex`; acks in <50ms; pushes typed events to bus
3. `rating_sync_service` — reads `userRating` / `viewCount` / `lastViewedAt` from Plex; emits `RatingChanged` / `TrackPlayed` events
4. `poll_service` — APScheduler job; emits same event types as webhooks; dedupe handles both arriving for same logical event
5. `vibe_service` — CRUD for `Vibe` + `TrackVibe` + `ManagedPlaylist`; slots tracks on `RatingChanged` events
6. `vibe_clusterer` — one-shot function (not singleton); Anthropic call → 3–7 clusters; used by wizard and manual re-cluster only
7. `taste_profile_service` — caches LLM-friendly taste summary + structured aggregates; recomputes on re-cluster or 10% rating-set delta
8. `suggestions_service` — owns Suggestions queue state machine; on `TrackPlayed`: remove → shortlist → LLM rank → refill → push to Plex
9. `discovery_service` — taste-aware Lidarr artist discovery; periodic + on-demand; validates against MusicBrainz before showing
10. `setup_wizard` — multi-step HTMX wizard; state in `SetupState` table (not cookies; step 2 produces ~50KB cluster JSON)

**New data model (additive; no destructive changes to v1 tables):**
- `Track` — add `user_rating`, `rating_changed_at`, `last_viewed_at`, `view_count` columns (nullable; via existing `_migrate_add_columns()` shim)
- `Vibe` — user-named vibe with cached centroid (energy/tempo/danceability/valence) + std-dev spread
- `TrackVibe` — many-to-many membership with cached distance; `assigned_by` tracks source (cluster / auto-slot / manual)
- `ManagedPlaylist` — links vibe_id or "suggestions" to Plex playlist ratingKey
- `EventLog` — dedupe key (`UNIQUE`) + audit trail for webhook + poll events; prune >30 days
- `TasteProfile` — single-row cache of taste summary text + structured aggregates; prompt-cacheable prefix
- `SetupState` — single-row wizard state; persists across container restarts

**Key data flow insight:** `Plex playlist = eventually consistent user-visible truth; SQLite = fast-react mirror.` On `TrackPlayed`: act locally first (remove from mirror, shortlist, LLM rank, pick candidates), then push to Plex as a single `update_playlist_items` call. Never re-fetch the Plex playlist as the read path — SQLite read is sub-millisecond.

---

### Critical Pitfalls — Watch Out For

These are the pitfalls that, if missed, will cost real debugging time or real money. Full detail in `.planning/research/PITFALLS.md`.

**1. Plex webhook idempotency — no built-in dedupe key (Pitfall 1)**
Plex uses at-least-once delivery with no `event_id`. Build dedupe key: `sha256(f"{event_type}|{ratingKey}|{timestamp_bucket_5s}")`. Insert into `EventLog` with `UNIQUE` constraint; on `IntegrityError` skip dispatch. Must exist on day one of Phase 5.

**2. Plex `userRating` is 0–10, not 0–5 (Pitfall 2)**
1 star = 2.0, 5 stars = 10.0. Divide by 2 only at display boundaries. Persist raw 0–10 in SQLite. Filter with `WHERE user_rating IS NOT NULL AND user_rating > 0`. Unit test: `userRating=7.0` → display "3.5 stars", DB stores 7.0.

**3. Anthropic cache TTL silent regression — 1h dropped to 5min (Pitfall 9)**
As of March 2026, default ephemeral TTL is 5 minutes. Must specify `cache_control: {"type": "ephemeral", "ttl": "1h"}` explicitly. Log `cache_creation_input_tokens` / `cache_read_input_tokens` per call. Circuit breaker: daily quota (50 calls), burst limit (5/60s), $1.50/month alert.

**4. PlexAPI is sync — blocks asyncio event loop (Pitfall 4)**
PlexAPI makes blocking HTTP calls via `requests`. Convention: either declare webhook handler `def` (FastAPI dispatches to threadpool) or wrap every PlexAPI call: `await asyncio.to_thread(plex.fetchItem, rating_key)`. Webhook handler must return 200 in <50ms.

**5. Plex playlist conflict — user edits Composer-managed playlists in Plexamp (Pitfall 5)**
On every slot-in, fetch current Plex playlist contents, add only the delta, detect user-removed tracks and add to per-vibe exclusion list. Use `Composer · ` name prefix + `ManagedPlaylist` registry as dual ownership markers.

**6. K-means cold-start below 30 ratings (Pitfall 3)**
Enforce: disable clustering below 30 rated tracks; `k_max = min(7, n_rated // 15)`; only accept result when silhouette score > 0.25. Setup wizard must gate at <50 ratings. This user has 460+ so it is wizard UX, not a blocker.

**7. LLM cost runaway from accidental refresh loop (Pitfall 11)**
One misplaced `hx-trigger="every 5s"` can trigger 17,280 LLM calls per day. Circuit breaker must exist from the first phase that introduces ranking: daily quota in SQLite, burst limit as sliding-window counter, per-event debounce (60s cooldown per `(track_id, event_type)`). Not optional.

---

## Implications for Roadmap

Architecture proposed 4 phases (5–8) and an optional 5th (Phase 9). PITFALLS research describes 22 pitfalls as sub-concerns to bake into those phases — they are not separate phases. The right granularity is 4 primary phases plus 1 optional "Feed the Engine" phase. This continues v1 numbering (v1 shipped Phases 1–4).

### Phase 5: Rating-Source Foundation
**Goal:** Ratings flow in. Nothing else can be built without this.
**Delivers:**
- `Track.user_rating`, `last_viewed_at`, `view_count`, `rating_changed_at` columns (via `_migrate_add_columns()`)
- `rating_sync_service`: full pull on demand, delta on event
- `EventLog` table + `event_bus` skeleton (dispatcher with no downstream handlers yet; proves dedupe end-to-end)
- `api_webhooks` router: receives `media.rate`, `media.scrobble` → writes `EventLog` only
- `poll_service`: APScheduler job, same event types, same dedupe key
- Settings UI: webhook URL display, polling interval, Plex Pass detection, webhook health indicator
- Manual "Resync ratings" button on home page

**Key pitfalls to bake in:**
- Dedupe key + `EventLog` UNIQUE constraint (Pitfall 1) — day one
- `userRating` 0–10 scale + display helper (Pitfall 2) — add unit test before merge
- PlexAPI async wrapping convention: `asyncio.to_thread` (Pitfall 4) — document in CLAUDE.md
- Polling bounded queries only: recently-rated + recently-played, not full library (Pitfall 21)

**Ship gate:** Rated-track count visible on home page; manual refresh works; webhook + poll both populate `EventLog` with no duplicates under test.
**New dependencies:** none

---

### Phase 6: Vibe Clustering + Setup Wizard
**Goal:** User has 3–7 named, persistent vibe playlists in Plex.
**Depends on:** Phase 5 (rating set must exist)
**Delivers:**
- `Vibe`, `TrackVibe`, `ManagedPlaylist`, `TasteProfile`, `SetupState` tables
- `vibe_clusterer`: one-shot Anthropic call → proposes 3–7 clusters with names + centroids
- `taste_profile_service`: computes and caches taste summary; recomputes on 10% rating-set delta
- `vibe_service`: CRUD + `slot_track()` via audio-feature distance; soft membership (up to 2 vibes per track)
- Plex playlist push: `create_playlist`, `update_playlist_items`, `delete_playlist`
- `api_setup` router + HTMX wizard templates (5-step flow; state in `SetupState`, not cookies)
- `api_vibes` router: list, rename, delete, trigger re-cluster
- Wire `RatingChanged` handler in event bus: slot newly-rated track into matching vibes

**Key pitfalls to bake in:**
- K-means cold-start: 30-rating floor, `k_max = min(7, n_rated // 15)`, silhouette > 0.25 gate (Pitfall 3)
- Plex playlist conflict: delta-only slot-in, exclusion list, dual ownership markers (Pitfall 5)
- Stale ratingKey handling: post-push verification, re-resolution by `(artist, title, album)` (Pitfall 6)
- Prompt cache TTL: `ttl: "1h"` in vibe_clusterer call; log `cache_creation_input_tokens` (Pitfall 9)
- LLM hallucination validation: all returned track IDs validated against candidate set (Pitfall 10)
- v1 playlist migration: scan for v1-generated playlists, offer keep/adopt/delete choice (Pitfall 20)
- Alpine morph for HTMX swap correctness on wizard steps (Pitfall 15)

**Ship gate:** User completes wizard; has 3–7 named vibes pushed to Plex; rating a new track in Plexamp slots it into matching vibe within 10 seconds.
**New dependencies:** `anthropic>=0.100`, `scikit-learn>=1.8`

---

### Phase 7: Suggestions Queue + Mobile UI
**Goal:** Continuous Suggestions playlist exists in Plexamp, drains as user listens, refills with taste-matched tracks; mobile-first UI shell replaces v1 landing page.
**Depends on:** Phase 6 (vibe centroids + taste profile must exist)
**Delivers:**
- `suggestions_service`: state machine; shortlist via audio-feature distance to taste centroid; LLM re-rank top 50; target ~30 tracks
- LLM cost circuit breaker: daily quota, burst limit, per-event debounce (ships with ranking code)
- Wire `TrackPlayed` handler: consume from Suggestions → refill cycle
- `suggestion_history` table: 14-day exclusion window, skip/dismissed signal storage
- "Why this track?" templated explanation per suggestion
- Bootstrap flow: create Suggestions playlist in Plex on wizard finalize
- Mobile-first UI shell: bottom tab bar, `h-dvh`, `safe-area-inset-bottom`, 44px touch targets
- Retire v1 mood-chat UI; vibes home becomes landing page
- Vibe coverage indicator: surface lopsided vibes, offer backfill
- Skip-tracking / negative signal feeding into ranking

**Key pitfalls to bake in:**
- LLM cost circuit breaker (Pitfall 11) — ships with ranking code, not later
- Scrobble is consumption only, not endorsement; skip-bomb detection for >10 tracks in <10 min (Pitfall 8)
- Trust webhook payload timestamp; don't re-fetch `lastViewedAt` for consumption detection (Pitfall 7)
- Cross-surface deduplication: `track_recommendation_history` table; 14-day exclusion (Pitfall 12)
- iOS `100vh` / bottom toolbar: `h-dvh` + `viewport-fit=cover` + `safe-area-inset-bottom` (Pitfall 16)
- 44px touch targets: CSS token `--touch-target-min: 44px`; confirm modals for destructive actions (Pitfall 17)
- Hover-only states replaced with tap-to-expand disclosure (Pitfall 18)
- Alpine morph project-wide for all HTMX swaps (Pitfall 15)

**Ship gate:** Suggestions playlist exists in Plexamp; drains on play; refills within 30s of scrobble; LLM cost counter visible in Settings; mobile portrait UI looks correct on real iPhone.
**New dependencies:** none (anthropic already added in Phase 6)

---

### Phase 8: Lidarr Discovery + Polish
**Goal:** Taste-aware artist discovery; Lidarr arrivals auto-ingested; responsive pass on legacy screens; Lidarr connection-test bug fixed.
**Depends on:** Phases 5–7 (rated set + taste profile + library penetration data)
**Delivers:**
- `discovery_service`: LLM artist suggestions anchored on vibe seeds → MusicBrainz validation → popularity-bias demotion → one-click add to Lidarr
- `lidarr_client` extensions: `add_artist()`, `get_recent_imports()`
- FIX: Lidarr connection-test bug (Docker network resolution to `synobridge` hostname)
- `api_discovery` router + Discover tab UI
- `LidarrImport` event handler: detect new imports → trigger Essentia analysis → add to Suggestions eligibility
- Post-add monitoring: 24h check if Lidarr has imported any releases; surface warning if not
- `pyarr` pin bump: `>=6.6,<7.0`
- Responsive pass on legacy library/settings pages
- Settings: cost dashboard ("Anthropic spend this month: $X / $0.42 budgeted")

**Key pitfalls to bake in:**
- Mainstream popularity bias: anchor on specific seeds, filter by listener counts, require MusicBrainz adjacency (Pitfall 13)
- MusicBrainz validation: all LLM-returned artist names validated before showing to user (Pitfall 10)
- Lidarr quality profile mismatch: surface quality profile in discovery UI; post-add monitoring (Pitfall 14)
- Cross-surface deduplication: Lidarr discovery filters out artists already in library (Pitfall 12)
- `metadataProfileId` required alongside `qualityProfileId` for artist add (Pitfall 14)

**Ship gate:** Discover page shows ≥10 artist suggestions; one-click adds to Lidarr; imported tracks auto-analyze and become eligible for Suggestions; no regression on legacy screens on mobile.
**New dependencies:** `pyarr` pin bump only

---

### Phase 9: Feed the Engine (Optional)
**Goal:** Encourage rating behavior that improves the taste signal, particularly for underweighted vibes.
**Depends on:** Phases 5–8 complete
**Delivers:**
- Bulk rating session surface ("Rate tracks from your library to strengthen your vibes")
- Play-rated nudge ("You have N highly-rated tracks you haven't played in a while")
- Surprise Me: queue a random high-rated but recently-unplayed track

**Note:** All core functionality is complete after Phase 8. Phase 9 can ship alongside or after Phase 8 without blocking anything. The "now-playing bar" and "forgotten-favorites" items from earlier discovery were dropped — Composer doesn't play music and the user already has a smart playlist for this.

---

### Phase Ordering Rationale

1. **Phase 5 first because ratings gate everything.** Vibe clustering, auto-slot, Suggestions ranking, and Lidarr discovery all require `Track.user_rating` to be populated. Building Phase 6+ without Phase 5 tested and stable means building on an unstable foundation.
2. **Phase 6 before Phase 7 because vibes produce the centroids that Suggestions needs.** Without vibes, Suggestions would need its own separate clustering pass.
3. **Phase 7 before Phase 8 because Suggestions and Lidarr share infrastructure.** Discovery uses the taste profile, recommendation history table, and Lidarr client — all easier to build against an already-working Suggestions loop.
4. **Phase 8 is "leafy"** — depends on Phases 5–7 but not the reverse. Lidarr could theoretically swap with Phase 7 if discovery were higher priority, but Suggestions is the headline v2.0 feature.
5. **Mobile-first is a constraint across all phases**, not a dedicated phase. Every new template is designed portrait-first from Phase 5 onward.

---

### Research Flags

**Phase 5 — standard patterns, skip research-phase:**
PlexAPI webhook reception thoroughly documented; dedupe pattern is well-established; polling fallback reuses v1 APScheduler patterns. All library versions confirmed. No research phase needed.

**Phase 6 — no research phase needed:**
Anthropic SDK prompt caching documented and verified (anthropic 0.100 GA). scikit-learn k-means + silhouette is canonical. Plex playlist CRUD via PlexAPI is confirmed. Vibe clustering architecture fully designed in ARCHITECTURE.md.

**Phase 7 — no research phase needed:**
Suggestions queue architecture fully specified in ARCHITECTURE.md. LLM ranking pattern extends from Phase 6 Anthropic integration. Mobile Tailwind patterns confirmed in STACK.md.

**Phase 8 — consider light research spike:**
MusicBrainz adjacency gate and Lidarr artist add API parameters (especially `metadataProfileId`) have open questions specific to pyarr 6.6. Recommend a short spike at Phase 8 planning time to confirm: (a) pyarr 6.6 `add_artist` signature and required params, (b) MusicBrainz API rate limits at our query volume, (c) Last.fm vs MusicBrainz as the candidate source for artist discovery.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All library versions verified against PyPI as of 2026-05-08. Two new deps have confirmed amd64 wheels and Python 3.12 compatibility. |
| Features | MEDIUM-HIGH | Table-stakes features well-researched against Spotify/Apple patterns and competitor analysis (Lidify, Cmdarr, MediaSage, Sonic Sage). Cold-start thresholds (30/50 ratings) are principled estimates, not measured data. |
| Architecture | HIGH | Grounded directly in observable v1 codebase (`sync_service.py`, `analysis_service.py`, `database.py`). Event bus, polling, wizard patterns are established idioms being extended. Data model fully specified with trade-off analysis. |
| Pitfalls | HIGH | Plex webhook behavior verified against official Plex docs and Plex Pro Week '25 post. Anthropic cache TTL change confirmed against anthropic-sdk-python source and April 2026 post. PlexAPI sync behavior confirmed from source. v1 production history (STATE.md) cross-referenced throughout. |

**Overall confidence:** HIGH

### Gaps to Address During Planning/Execution

- **Plex webhook URL through Docker network:** Plex server and Composer container are on the same `synobridge` network, so register the internal hostname (`http://composer:8085/api/webhooks/plex`) not the Tailscale IP. Confirm this in Phase 5 setup wizard copy.

- **Lidarr `metadataProfileId` for artist add:** pyarr 6.6's `add_artist()` requires this alongside `qualityProfileId`. Fetch dynamically from Lidarr's `/api/v1/metadataprofile` endpoint, don't hardcode. Resolve during Phase 8 planning.

- **Essentia analysis completion detection in wizard:** New deployments need Essentia to finish before clustering produces meaningful results. The detection logic (`WHERE energy IS NULL`) is trivial; surface as a wizard step requirement in Phase 6.

- **Negative signal ranking weight:** Skip-tracking and dismissed-from-queue feed into ranking (locked decision), but the exact weight for "played-but-not-rated" vs "explicitly dismissed" is undefined. Treat as a Phase 7 implementation decision — start with a simple downweight multiplier and iterate.

---

## Sources

### Primary (HIGH confidence)
- [Plex Webhooks documentation](https://support.plex.tv/articles/115002267687-webhooks/) — webhook format, event types, Plex Pass requirement
- [anthropic on PyPI](https://pypi.org/project/anthropic/) — v0.100.0 released 2026-05-06
- [Prompt caching — Claude API docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — `ttl: "1h"` GA, 2048-token minimum for Sonnet 4.6
- [scikit-learn 1.8.0 on PyPI](https://pypi.org/project/scikit-learn/) — k-means, silhouette score, Python 3.11+ requirement
- [PlexAPI 4.18.1 documentation](https://python-plexapi.readthedocs.io/) — `userRating` (0.0–10.0), `lastViewedAt`, `viewCount`
- [pyarr 6.6.0 on PyPI](https://pypi.org/project/pyarr/) — March 2026, Python 3.12+
- [Tailwind CSS v4 documentation](https://tailwindcss.com/docs/) — native container queries, dvh units, portrait/landscape modifiers

### Secondary (MEDIUM confidence)
- [Spotify Daily Mix explained](https://playlistpilotapp.com/blog/spotify-daily-mix-explained) — K range guidance, 4–6 mix standard
- [Discover Weekly mechanics](https://medium.com/the-sound-of-ai/spotifys-discover-weekly-explained-breaking-from-your-music-bubble-or-maybe-not-b506da144123) — 30-track target, exclusion window
- [Implicit vs explicit feedback — ACM RecSys](https://dl.acm.org/doi/10.1145/1869446.1869453) — explicit ratings beat implicit at single-user scale
- [Popularity bias in music recommendation — arXiv](https://arxiv.org/pdf/2208.09517) — LLM popularity bias, mitigation strategies
- [Mobile navigation patterns — UXPin 2026](https://www.uxpin.com/studio/blog/mobile-navigation-examples/) — bottom tab bar standard
- [FastAPI multipart / pydantic.Json bug #10997](https://github.com/fastapi/fastapi/issues/10997) — avoid `pydantic.Json[Model]` in `Form()`

### Tertiary (LOW confidence — validate during Phase 8)
- Last.fm `artist.getSimilar` as candidate source for discovery — behavior at niche genre scale unverified
- MusicBrainz API rate limits at ~10 lookups/session — documented as 1 req/sec; should be fine but confirm during Phase 8 planning

---
*Research completed: 2026-05-08*
*Ready for roadmap: yes*
