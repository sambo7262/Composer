# Requirements: Composer

**Defined:** 2026-04-09 (v1.0) · **v2.0 added:** 2026-05-08
**Core Value:** Your Plex stars are the truth. Composer turns them into living vibe playlists and a steady stream of personalized discoveries — without you having to describe a vibe each time.

---

## v1.0 Requirements (Validated)

Shipped in milestone v1.0 (phases 1–4). Core mood-to-playlist pipeline is being retired in v2.0; foundational capabilities (sync, audio analysis, settings, deployment) carry forward.

### Configuration

- [x] **CONF-01**: User can configure Plex server URL and token via in-app settings page (Phase 1)
- [x] **CONF-02**: User can configure LLM provider — was Ollama, replaced by Anthropic Claude mid-v1 (Phase 1)
- [x] **CONF-03**: User can configure Lidarr URL, API key, and default quality profile (Phase 1)
- [x] **CONF-04**: Settings stored securely; secrets never displayed or returned in API responses after entry (Phase 1)
- [x] **CONF-05**: App deploys as a single Docker container with compose YAML (Phase 1)
- [x] **CONF-06**: Plex media directory mounted as read-only volume for Essentia analysis (Phase 1)

### Library Sync

- [x] **SYNC-01**: Plex music library syncs to local SQLite with paginated fetching (Phase 2)
- [x] **SYNC-02**: Subsequent syncs are delta-only (Phase 2)
- [x] **SYNC-03**: Library sync runs as background job with UI progress indicator (Phase 2)
- [x] **SYNC-04**: Track metadata captured: title, artist, album, genre, year, duration, ratingKey (Phase 2)

### Audio Features

- [x] **AUDIO-01**: Essentia extracts energy, tempo, danceability, valence from local files (Phase 3)
- [x] **AUDIO-02**: Audio analysis runs as background job with progress + pause/resume (Phase 3)
- [x] **AUDIO-03**: When features unavailable, fall back to genre/year/artist as mood proxy (Phase 3)
- [x] **AUDIO-04**: Extracted features cached permanently in SQLite — each track analyzed once (Phase 3)

### Playlist Generation (Retiring in v2.0)

- [x] **PLAY-01**: User can describe mood in natural language and receive playlist (Phase 4) — *retiring; replaced by vibe + suggestions model*
- [x] **PLAY-02**: LLM interprets mood description into structured audio feature criteria (Phase 4) — *retiring*
- [x] **PLAY-03**: Library tracks scored against mood criteria via weighted distance (Phase 4) — *retained as scoring primitive; reused for vibe slotting*
- [x] **PLAY-04**: User can specify track count for generated playlist (Phase 4) — *retiring; Suggestions queue is fixed-size*
- [x] **PLAY-05**: User can review and edit generated playlists (Phase 4) — *retiring; v2.0 manages playlists automatically*
- [x] **PLAY-06**: User can push finalized playlist to Plex (Phase 4) — *retained as primitive; reused for vibe + Suggestions playlists*

### Deployment

- [x] **DEPL-01**: Docker image built via GitHub Actions CI/CD (Phase 1)
- [x] **DEPL-02**: Docker image published to Docker Hub (Phase 1)

### Superseded by v2.0

These v1 requirements were planned for Phase 5/6 but never built. They're superseded by v2.0 requirements rather than carried over verbatim:

- [~] **PLEX-01** (browse existing Plex playlists) → **Out of Scope** in v2.0 — hands-off principle, Composer manages only its own playlists
- [~] **PLEX-02** (analyze mood profile of existing playlist) → Absorbed: vibe clustering analyzes the rated set, not arbitrary user playlists
- [~] **PLEX-03** (suggest additions to existing playlists) → Absorbed: slotting suggests additions to *Composer-managed* vibe playlists only
- [~] **HIST-01, HIST-02** (history of generated playlists) → **Out of Scope** in v2.0 — continuous queue model makes a "generation history" obsolete
- [~] **DISC-01, DISC-02** (Lidarr artist recommendations + one-click add) → Reframed as taste-aware discovery in v2.0 — see **DISC-03..DISC-07** below

---

## v2.0 Requirements (Active — Music Companion)

Pivots Composer from one-shot mood-to-playlist into a continuous companion built around Plex star ratings as the taste signal. All requirements below are net-new for v2.0.

### Plex Event Ingestion

The foundation for everything else. Without reliable, deduplicated event streams from Plex, the rest of v2.0 cannot react to ratings, plays, or library additions.

- [x] **EVT-01**: Composer exposes a `POST /api/webhooks/plex` endpoint that accepts Plex Pass webhooks (multipart/form-data with JSON `payload` field), returns 200 in <50ms, and pushes a typed event onto the internal event bus *(Phase 5 Plan 01)*
- [x] **EVT-02**: Webhook handler dedupes events via an `EventLog` table with a `UNIQUE` constraint on `dedupe_key = sha256(event_type|ratingKey|user_rating|5s_timestamp_bucket)`; duplicates are dropped silently *(Phase 5 Plan 01)*
- [x] **EVT-03**: APScheduler-based polling job runs every 5 minutes (configurable) to detect rating, play, and library changes — emits the same typed events through the same event bus, dedupe handles webhook+poll overlap *(Phase 5 Plan 02 — `app/services/poll_service.py` + `schedule_polling` in `sync_scheduler.py`)*
- [x] **EVT-04**: Single asyncio dispatcher task consumes the event bus and serializes downstream handlers to avoid SQLite write contention *(Phase 5 Plan 01)*
- [x] **EVT-05**: User can manually trigger a "Resync now" full event scan from the UI (settings or vibes home) *(Phase 5 Plan 02 — POST /api/rating-sync/start + `partials/backfill_banner.html`; Plan 04 will surface the button on the settings page)*
- [x] **EVT-06**: PlexAPI calls inside event handlers run via `asyncio.to_thread()` so the FastAPI event loop is never blocked *(Phase 5 Plan 01 — enforced by AST static test)*
- [x] **EVT-07**: Setup wizard displays the user's webhook URL with a copy button, auto-detecting the accessible hostname/port; "test webhook" flow shows ✓ when Plex's test event is received *(Phase 5 Plan 04 — 3-candidate radio: Docker hostname / NAS LAN IP / Tailscale 100.x; user-validated against real Plex on 2026-05-09)*

### Rating Sync

Rated tracks are the taste signal. Composer reads `userRating` from Plex on every relevant event; never writes ratings.

- [x] **RATE-01**: `Track` model extended with `user_rating` field (Plex's 0–10 scale stored raw; conversion to 0–5 stars happens only at display boundaries) *(Phase 5 Plan 01 — `app/services/rating_helpers.stars_from_user_rating`)*
- [x] **RATE-02**: Initial library sync (and full Resync) populates `user_rating` for every track *(Phase 5 Plan 02 — `app/services/backfill_service.py` auto-fires from lifespan when DB has tracks but none rated; Resync now button shares the same singleton)*
- [x] **RATE-03**: `media.rate` webhook events (and rating diffs found by polling) update `user_rating` in real time and emit a `RatingChanged` event for downstream slotting *(Phase 5 Plan 01 — `handle_rating_changed`)*
- [x] **RATE-04**: A "rated set" is exposed as a derived view — `Track.user_rating > 0` — with an index on the field for fast queries *(Phase 5 Plan 01 — `ix_track_user_rating` index)*
- [x] **RATE-05**: A taste profile (centroid features + top artists/genres + rated-set summary text suitable for Anthropic prompt caching) is computed and cached; recomputes on user-triggered re-cluster or when the rated set changes by ≥10% *(Phase 5 Plan 03 — `app/services/taste_profile_service.py` with `recompute()` + `maybe_recompute_after_rating_change()`; 4-D centroid via numpy.mean, top 10 artists/genres via Counter, ~200-word LLM summary via AnthropicClient with explicit ttl=1h cache)*

### Vibe Curation

Vibes are persistent user-named buckets derived from the rated set. Newly-rated tracks auto-slot into matching vibes; each vibe has its own Composer-managed Plex playlist.

- [ ] **VIBE-01**: `Vibe` table stores name, optional description, centroid features (energy/tempo/danceability/valence), creation timestamp, last-clustered timestamp
- [ ] **VIBE-02**: `TrackVibe` link table caches track-to-vibe membership with computed distance — soft membership: a track may belong to up to 2 vibes if its distance to a second vibe centroid is within a configurable margin (default 1 std-dev)
- [ ] **VIBE-03**: `ManagedPlaylist` table tracks Composer-owned Plex playlists with a `kind` discriminator (`vibe` or `suggestions`), nullable `vibe_id` FK, and unique `plex_rating_key`
- [ ] **VIBE-04**: All Composer-managed Plex playlists use the `Composer · {name}` namespace prefix; existing user-created playlists are never read or modified
- [ ] **VIBE-05**: AI vibe clustering runs k-means on the rated set's audio features (Essentia 4-D normalized), automatically picking k between 3 and 7 via silhouette score; minimum silhouette threshold of 0.25 enforced, else clustering refuses with a degraded-mode message
- [ ] **VIBE-06**: Cold-start gating — clustering refuses if rated set <30 tracks; a "single-vibe degraded mode" runs at 30–49 tracks; full clustering at ≥50
- [ ] **VIBE-07**: After clustering, an LLM call proposes a name + one-line description for each cluster based on the cluster's seed-rated tracks (artist/genre/feature summary)
- [ ] **VIBE-08**: User can rename, reword the description, merge, or split vibes during the wizard; can also drag tracks between vibes (manual override flag preserved across re-clusters)
- [ ] **VIBE-09**: On `RatingChanged` event for a track newly entering rated state, the track is scored against current vibe centroids and slotted into matching vibes; corresponding Plex playlists are updated additively
- [ ] **VIBE-10**: On rating removed (track drops to 0), track is removed from all vibe playlists and the `TrackVibe` cache
- [ ] **VIBE-11**: User can manually trigger re-clustering via a "Re-cluster vibes" button (behind a confirm modal — re-clustering is rare, not nightly)
- [ ] **VIBE-12**: Plex playlist push verifies post-write: re-fetch the playlist and reconcile against expected track set; silently-dropped tracks (Plex rejected for any reason) are logged and retried once
- [ ] **VIBE-13**: LLM-direct vibe membership — each rated track is assigned to one user-typed vibe via a batched LLM call that receives `(title, artist, genre, energy, tempo, danceability, valence)` per track and returns the chosen vibe name + a confidence grade (`strong`/`weak`/`uncertain`). K-means is retained as a centroid generator only — no membership decisions from `k-means.labels_`. Cost per full library re-cluster ≤ $1.50 on Sonnet 4.6 with prompt caching (measured via the `LLMUsage` table)
- [ ] **VIBE-14**: Two-pass boundary review — tracks assigned with `weak` or `uncertain` confidence in VIBE-13's first pass are re-sent to the LLM in a second call along with the *peer-context* of their candidate vibes (the list of tracks already strongly assigned to each candidate). The LLM picks the final vibe with the peer list visible. Confidence chip surfaces on the proposal card for any track that went through the second pass

### Setup Wizard

First-run guided flow. The only place k-clustering and Plex webhook configuration are surfaced.

- [ ] **WIZ-01**: First-run detection — wizard auto-launches when no vibes exist and rated set ≥1 track; can be skipped, resumed, or replayed from settings
- [ ] **WIZ-02**: Wizard state persisted in a `SetupState` table (server-side, NOT cookies — vibe proposals are ~50KB)
- [ ] **WIZ-03**: Step 1: Confirm rating source — show count of rated tracks pulled from Plex; if <30, show "rate more tracks before clustering" message with a hint at the bulk rating session (Phase 9 if shipped)
- [ ] **WIZ-04**: Step 2: Webhook setup — show the `POST /api/webhooks/plex` URL with a copy button + step-by-step instructions for adding it in Plex Web → Settings → Account → Webhooks; "test webhook" listens for Plex's test event and shows ✓
- [ ] **WIZ-05**: Step 3: Propose vibes — runs clustering, displays each proposed cluster with seed tracks + auto-generated name and description; user edits inline
- [ ] **WIZ-06**: Step 4: Confirm vibes → Composer creates the corresponding `Composer · {name}` Plex playlists, populates them via initial slotting, and creates the `Composer · Suggestions` playlist
- [ ] **WIZ-07**: Step 5: Done — wizard exits to vibes home; user sees their vibes populated

### Suggestions Queue

Continuous "Composer · Suggestions" Plex playlist that drains as you listen and refills with taste-matched picks.

- [ ] **SUGG-01**: Composer maintains a single `Composer · Suggestions` Plex playlist sized to a configurable target (default 30 tracks)
- [ ] **SUGG-02**: SQLite is the source of truth for queue contents; Plex playlist is updated as eventual-consistent mirror to avoid LLM-latency races on consumption
- [ ] **SUGG-03**: On `media.scrobble` (or `lastViewedAt` change detected via polling) for a track in the Suggestions queue, the track is removed locally and a refill is scheduled
- [ ] **SUGG-04**: Refill pipeline: audio-feature distance to taste-profile centroid + top-K vibe centroids → shortlist of ~50 unrated candidates → Anthropic LLM ranks the shortlist → top N picks are added to fill the queue back to target
- [ ] **SUGG-05**: LLM ranking uses Anthropic prompt caching with explicit `ttl: "1h"` on the system message containing the taste profile (the silently-changed default is 5min, which would blow the budget — must be explicit)
- [ ] **SUGG-06**: Each suggestion stores a one-line "Why this track?" rationale generated at ranking time (e.g., "Matches Late Night vibe (energy 0.4) · same artist as 7 of your stars"); shown in the UI on tap
- [ ] **SUGG-07**: A `SuggestionHistory` table records every track ever surfaced as a suggestion with timestamp; tracks dedupe within a 14-day exclusion window so the same track doesn't reappear too soon
- [ ] **SUGG-08**: Skip-tracking: a track that was added to Suggestions, played by the user (lastViewedAt updated), but did NOT receive a rating within a configurable window (default 14 days) is marked as "soft negative" and ranking deboosts similar tracks
- [ ] **SUGG-09**: Skip-tracking: a track explicitly removed from Suggestions by the user (UI dismiss action) is marked as "hard negative" — track-level exclusion + ranking deboosts the artist
- [ ] **SUGG-10**: Vibe coverage indicator on vibes home — shows track count per vibe; vibes with <N tracks (default 25) display a "Find candidates" CTA that runs targeted clustering against unrated tracks for that vibe and offers them as a focused micro-Suggestions list
- [ ] **SUGG-11**: LLM cost circuit breaker — daily quota (50 ranking calls), burst limit (5 calls/60s), per-event debounce (no refill within 30s of last refill); circuit-broken state shown in UI as "Suggestions paused — cost limit hit"

### Lidarr Discovery

Closes the loop outward — recommend new artists that match your taste, add them to Lidarr, ingest their imports automatically.

- [ ] **DISC-03**: Composer surfaces a "Discover artists" page that proposes 10–20 artists not currently in your library, ranked by closeness to your taste profile; uses MusicBrainz/Last.fm "similar artists" seed sources combined with LLM taste re-ranking
- [ ] **DISC-04**: Discovery applies popularity-bias mitigation — penalizes artists with disproportionate library penetration ("you don't need more Coldplay"); demotes mainstream over differentiated picks
- [ ] **DISC-05**: One-click add sends an artist to Lidarr with the user's configured quality profile + metadata profile; pre-flight check confirms quality profile validity and surfaces clear error if Lidarr unreachable
- [ ] **DISC-06**: After Lidarr import completes (detected via Lidarr webhook OR aggressive polling on a short timer post-add), newly-imported tracks are auto-queued for Essentia analysis and then scored against vibes/taste profile
- [ ] **DISC-07**: Lidarr connection-test bug from v1 is fixed — settings page reliably validates URL/API key; clear error messages distinguish unreachable, auth failure, and version mismatch

### UI / Mobile-First

Every new v2 surface is portrait-first; v1 chat retires; legacy screens get a responsive pass.

- [ ] **UI-01**: New "Vibes" home page is the v2.0 landing route — shows each vibe as a card with track count, sample art, and tap-into-detail
- [ ] **UI-02**: Bottom tab bar navigation on mobile (Vibes / Suggestions / Discover / Settings); top nav on desktop
- [ ] **UI-03**: Every new screen uses Tailwind 4 dynamic viewport units (`h-dvh` not `h-screen`/`100vh`) and `env(safe-area-inset-bottom)` for fixed bottom bars
- [ ] **UI-04**: Touch targets ≥44px on every tappable element; no hover-only interactions
- [ ] **UI-05**: HTMX swaps use the `alpine-morph` extension to avoid lost Alpine state on partial replacement (v1 burned issue)
- [ ] **UI-06**: v1 mood-chat UI removed — `/chat` route deleted, nav references removed, related templates archived; data preserved in DB but no UI access
- [ ] **UI-07**: Responsive pass on settings page and library browse page — service cards and track lists work cleanly portrait at 375px wide
- [ ] **UI-08**: "Why this track?" rationale, vibe coverage indicator, and skip-track dismiss action are all designed mobile-first

### Debug / Observability

Cross-cutting debug surfaces. Every v2 phase ships at least one `/debug/{service}` HTML page that exposes recent activity, current state, and last errors in copy-pasteable form — so when something looks wrong, the user can grab a snapshot from the browser and share it without poking around in logs or SQLite. No auth needed (Tailscale-only access).

- [x] **DEBUG-01** (Phase 5): `/debug/events` page lists last 50 events received (webhook + poll), each with timestamp, source, dedupe_key, payload preview, processed_at, and any handler error. Also shows current poll interval, last poll result, last webhook test ✓/✗. *(Phase 5 Plan 04 — `/debug/events` route + library-at-a-glance header + queue depth + "Polling disabled" surfacing when opt-in flag off)*
- [ ] **DEBUG-02** (Phase 6): `/debug/vibes` page shows each vibe's centroid features, member count, silhouette score, last-clustered-at, and the last 20 slot-in decisions (track → vibe(s) with computed distance). Includes a "Re-show wizard cluster proposal" button that displays the last LLM cluster-naming response.
- [ ] **DEBUG-03** (Phase 7): `/debug/suggestions` page shows current queue contents (track + vibe + score + rationale), last 20 refill triggers (event source, candidates evaluated, picks made, latency), recent skip-tracking signals, and current circuit-breaker state. Also shows last 20 LLM calls with model, prompt-cache hit/miss, tokens, cost.
- [ ] **DEBUG-04** (Phase 8): `/debug/discovery` page shows last candidate set (with provenance per artist), recent MusicBrainz queries with rate-limit headers, recent Lidarr `add_artist` requests + responses, and the Lidarr connection-test result history.
- [ ] **DEBUG-05** (cross-cutting, all phases): Each debug page is plain HTML with copy-friendly layout (no JS-only rendering); reachable from a single `/debug` index page; flagged with "for diagnostics only" so the user knows it's not a feature surface. Linked from the settings page footer for discoverability.

### Operations / Migration

Schema migrations, observability, and the cost-control machinery the rest of v2.0 leans on.

- [x] **OPS-01**: Schema migrations extend the existing `_migrate_add_columns()` shim in `app/database.py` for additive changes; new tables use `create_all()` — no Alembic introduced *(Phase 5 Plan 01 — 4 Track cols + 3 new tables + 2 indexes)*
- [x] **OPS-02**: Anthropic SDK migration — `anthropic>=0.100,<1.0` replaces the v1 direct-httpx wrapper; existing chat service code is removed alongside chat UI retirement *(Phase 5 Plan 03 — `app/services/anthropic_client.py` shipped with explicit `cache_control={"type":"ephemeral","ttl":"1h"}` (Pitfall 4) and per-call LLMUsage logging; v1 `llm_client.py` + `chat_service.py` stay UNTOUCHED until Phase 7 retirement per D-01)*
- [x] **OPS-03**: `pyarr` pin bumped from `>=5.2,<6.0` to `>=6.6,<7.0` (required for current Lidarr endpoints in DISC-05/06) *(Phase 5 Plan 01 — requirements.txt)*
- [x] **OPS-04**: `scikit-learn>=1.8,<2.0` added for k-means + silhouette in vibe clustering *(Phase 5 Plan 01 — requirements.txt)*
- [ ] **OPS-05**: LLM usage logged per call (model, input tokens, cache_creation_input_tokens, cache_read_input_tokens, output tokens, cost estimate); daily aggregate exposed on settings page so cost surprises are visible
- [ ] **OPS-06**: Existing v1-generated Plex playlists in user's library are recognized as legacy (no `Composer ·` prefix in `ManagedPlaylist`) and explicitly NOT touched

---

## Phase 9 (Optional — "Feed the Engine")

Three small features bundled into a single optional final phase. The whole phase can be cut for a tighter v2.0 if budget runs short; nothing else depends on it.

- [ ] **ENG-01**: Bulk rating session — UI flow that surfaces 10 strategically diverse unrated tracks (mixed energy/tempo/genre, picked to teach the system more from each rating). Each track has a "Rate in Plexamp" deeplink — Composer never writes ratings, only nudges the user toward rating in their player of choice. After rating in Plexamp, the next webhook/poll cycle picks up the change and slots the tracks normally.
- [ ] **ENG-02**: Play-rated nudge — Composer surfaces unrated tracks with `viewCount ≥ 5` (configurable) on the vibes home as "you've played these but haven't rated them"; tap navigates to Plexamp with a deeplink so user can rate there
- [ ] **ENG-03**: Surprise Me — one-tap action on vibes home that picks a single unrated taste-matched track and deeplinks the user to Plexamp to play it; bypasses the Suggestions queue for instant discovery

---

## Future / Backlog (Beyond v2.0)

Tracked but not in current milestone scope. Carried over from v1 backlog.

### Spotify Integration

- **SPOT-01**: Optionally configure Spotify client credentials for enhanced audio features
- **SPOT-02**: Pull Spotify audio features as alternative/supplement to Essentia
- **SPOT-03**: Use Spotify recommendations API for enhanced artist discovery

### Cloud LLM Provider Switching

- **PROV-01**: Support cloud LLM providers (OpenAI, Anthropic, Gemini) with per-request override
- **PROV-02**: Configurable default LLM provider — currently Anthropic-only

### Enhanced Generation

- **GEN2-01**: Seed track mode — "20 tracks like this one"
- **GEN2-02**: Mood-based radio mode (continuous generation)
- **GEN2-03**: Playlist scheduling/automation

### Analytics

- **ANAL-01**: Library statistics dashboard (genre distribution, energy spread, etc.)
- **ANAL-02**: Listening-history-based recommendations (Last.fm/Listenbrainz integration)

---

## Out of Scope

Explicitly excluded. Documented to prevent scope creep across both milestones.

| Feature | Reason |
|---------|--------|
| Multi-user / authentication | Single-user personal tool — unnecessary complexity |
| Mobile app | Web-only; mobile-first responsive web is the answer |
| In-app music playback | Plex/Plexamp handles playback |
| Manual audio fingerprinting | Essentia handles feature extraction from files |
| Automatic artist downloading | Lidarr handles downloads after artist is added to wanted list |
| Real-time streaming integration | Out of scope for playlist generation tool |
| Social/sharing features | Single-user tool |
| Spotify integration in v1/v2 | External dependency; deferred to backlog |
| Cloud LLM providers in v1/v2 | Anthropic locked in for v2.0; switching deferred to backlog |
| Mood-chat / on-demand vibe-to-playlist generation | Retired in v2.0; companion model replaces it |
| Generation history UI | Continuous queue model makes "history of generated playlists" obsolete |
| Browsing or analyzing existing user-created Plex playlists | Hands-off principle; Composer manages only its own |
| Suggesting additions to user-created Plex playlists | Same hands-off principle |
| Plex now-playing context bar | Composer doesn't play music; faking a now-playing surface is misleading |
| Forgotten favorites / "rated but not played in a while" | User has an existing Plex smart playlist that covers this |
| Re-clustering vibes on a schedule | Only on user request; persistent vibes are the point |
| Hard partition of tracks into one vibe each | Soft membership (up to 2 vibes) — matches real listening patterns |
| Time-of-day vibe affinity | Cute but a maintenance trap; cut from v2.0 |
| Composer writing star ratings to Plex | Plex/Plexamp owns the rating; Composer only reads (revisit during ENG-01 phase planning if bulk rating becomes blocked) |
| Last.fm / Listenbrainz scrobbling-as-taste-signal | Different signal type pollutes the rated-stars model |
| Celery / RQ / Redis | Single-user single-container; APScheduler is enough |
| Alembic | Existing `_migrate_add_columns()` shim covers v2 schema needs |
| LangChain / LiteLLM | Two LLM call sites do not warrant a framework |
| Instructor | Removed in v1; fights prompt caching in v2 |

---

## Traceability

Maps requirements to phases. Filled during roadmap creation; updated as phases complete.

### v1.0 — Validated

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONF-01..06 | Phase 1 | Complete |
| DEPL-01, DEPL-02 | Phase 1 | Complete |
| SYNC-01..04 | Phase 2 | Complete |
| AUDIO-01..04 | Phase 3 | Complete |
| PLAY-01..06 | Phase 4 | Complete (retiring in v2.0) |

### v2.0 — Active

| Requirement | Phase | Status |
|-------------|-------|--------|
| EVT-01..07 (7 reqs) | Phase 5 | Complete (Plans 01–04) |
| RATE-01..05 (5 reqs) | Phase 5 | Complete — RATE-01/02/03/04 (Plans 01–02); RATE-05 (Plan 03) |
| OPS-01..04 (4 reqs) | Phase 5 | Complete — OPS-01/03/04 (Plan 01); OPS-02 (Plan 03) |
| DEBUG-01 (1 req) | Phase 5 | Complete (Plan 04) |
| VIBE-01..12 (12 reqs) | Phase 6 | Pending |
| WIZ-01..07 (7 reqs) | Phase 6 | Pending |
| DEBUG-02 (1 req) | Phase 6 | Pending (`/debug/vibes` page) |
| VIBE-13, VIBE-14 (2 reqs) | Phase 6.2 | Pending (LLM-direct vibe assignment + two-pass boundary review) |
| SUGG-01..11 (11 reqs) | Phase 7 | Pending |
| UI-01..06 (6 reqs) | Phase 7 | Pending (vibes home + chat retirement + mobile shell) |
| OPS-05 (1 req) | Phase 7 | Pending (LLM observability ships with first ranking call) |
| DEBUG-03, DEBUG-05 (2 reqs) | Phase 7 | Pending (`/debug/suggestions` + `/debug` index linked from settings) |
| DISC-03..07 (5 reqs) | Phase 8 | Pending |
| UI-07, UI-08 (2 reqs) | Phase 8 | Pending (legacy screen polish + multi-surface mobile-first detail) |
| OPS-06 (1 req) | Phase 8 | Pending (legacy playlist recognition) |
| DEBUG-04 (1 req) | Phase 8 | Pending (`/debug/discovery` page) |
| ENG-01..03 (3 reqs) | Phase 9 (optional) | Pending |

**Coverage (v2.0):**
- Required (Phases 5–8): **68 requirements** mapped — 7 EVT + 5 RATE + 4 OPS-01..04 + 1 DEBUG-01 + 12 VIBE-01..12 + 7 WIZ + 1 DEBUG-02 + 2 VIBE-13/14 + 11 SUGG + 6 UI-01..06 + 1 OPS-05 + 2 DEBUG-03/05 + 5 DISC + 2 UI-07/08 + 1 OPS-06 + 1 DEBUG-04 = 68
- Optional (Phase 9): **3 requirements** (ENG-01..03)
- Total v2.0 surface: **71 requirements**, all mapped, **0 unmapped**
- Phase 9 is explicitly cuttable; everything else is required for v2.0
- Roadmap validated 2026-05-11 — all v2.0 REQ-IDs map to exactly one phase, no orphans, no duplicates

---

*Requirements defined: 2026-04-09 (v1.0)*
*v2.0 requirements added: 2026-05-08 — Music Companion milestone*
*Traceability validated against ROADMAP.md: 2026-05-11*
