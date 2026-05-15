# Roadmap: Composer

## Overview

Composer's roadmap spans two milestones. **v1.0 (Phases 1–4)** shipped a self-hosted mood-to-playlist generator: Docker deployment + CI/CD, Plex library sync, Essentia audio-feature extraction, and an LLM-driven mood-chat playlist generator. **v2.0 (Phases 5–9)** pivots that foundation into a continuous music companion: Plex `userRating` becomes the taste signal, AI-clustered "vibes" replace one-shot mood chats, a Composer · Suggestions queue drains and refills as the user listens, and Lidarr discovery closes the loop outward. The v1 mood-chat UI is retired in v2.0; vibes home becomes the new landing page.

**Cross-cutting v2.0 concern — debug surfaces.** Every v2 phase ships at least one `/debug/{service}` HTML page exposing recent activity, current state, and last errors in copy-pasteable form. The user runs Composer self-hosted on a Synology NAS and is the primary maintainer; when something looks wrong, a quick browser screenshot from `/debug/...` should be enough to diagnose without poking SQLite or grep'ing logs. A `/debug` index page links to all of them and is reachable from the settings footer (DEBUG-05).

## Milestone Layout

- **Milestone v1.0 (Mood-to-Playlist):** Phases 1–4 ✓ shipped 2026-04-09 → 2026-04-10
- **Milestone v2.0 (Music Companion):** Phases 5–8 active, Phase 9 optional ("Feed the Engine")

> **Transition note (2026-05-08):** The original v1.0 plan included a *Phase 5: Playlist Management & History* and *Phase 6: Artist Discovery*. Both were planned but never built. They are **superseded by the v2.0 phase set below** — see `PROJECT.md` (Out of Scope: Generation history UI / Browsing existing Plex playlists) and `REQUIREMENTS.md` (Superseded by v2.0 section) for the absorption / deferral rationale. v2.0 phases continue the integer numbering from v1.0; nothing is renumbered.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3, …): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

#### Milestone v1.0 — Mood-to-Playlist (shipped)

- [x] **Phase 1: Foundation, Configuration & Deployment** — Docker container, CI/CD to Docker Hub, settings page, security patterns, Plex connection (completed 2026-04-09)
- [x] **Phase 2: Library Sync** — Full Plex music library synced to local SQLite with delta updates (completed 2026-04-09)
- [x] **Phase 3: Audio Feature Extraction** — Essentia analyzes local audio files for energy, tempo, danceability, valence (completed 2026-04-09)
- [x] **Phase 4: Playlist Generation** — Mood-to-playlist pipeline: natural language in, curated playlist out, pushed to Plex (completed 2026-04-10) *(retiring in v2.0; capabilities absorbed into vibe + suggestions model)*

#### Milestone v2.0 — Music Companion (active)

- [x] **Phase 5: Plex Event Foundation + Rating Sync** — Composer reliably ingests Plex webhook + polling events, dedupes them, and propagates RatingChanged end-to-end (completed 2026-05-10)
- [x] **Phase 6: Vibe Clustering + Setup Wizard** — User completes first-run wizard and ends with 3–7 named vibe playlists in Plex, populated from rated set, auto-slotting newly-rated tracks (completed 2026-05-13; capability delivered via inserted Phases 6.1 + 6.2)
- [x] **Phase 6.1: Vibe Wizard Foundations: server-led clustering + user-led vibe input** (INSERTED) — Server-led membership from k-means labels + user-typed vibe names replace LLM-led seed-picking; ensures all rated tracks land in a vibe playlist (completed 2026-05-13)
- [x] **Phase 6.2: LLM-Direct Vibe Assignment** (INSERTED) — Replace k-means membership decisions with LLM-direct zero-shot assignment per user-typed vibe; two-pass design (confidence-graded assign + boundary peer-review); audio features become a tiebreaker for obscure tracks (completed 2026-05-12)
- [ ] **Phase 7: Suggestions Queue + v1 Chat Retirement** — Continuous Composer · Suggestions playlist drains as the user listens and refills with taste-aware picks; v1 mood-chat retires; vibes home becomes the new landing page
- [ ] **Phase 7.1: Suggestions Cost Architecture: SQL refill + weekly discovery** (INSERTED) — Replace per-event LLM refill (~$0.05/play, trips $0.42 daily breaker after 8 plays) with SQL hot path against Phase 6 TrackVibe scores + one weekly LLM "discovery" call; drops steady-state cost from ~$150/mo to ~$0.20/mo
- [ ] **Phase 8: Lidarr Discovery + Polish** — Taste-aware artist discovery with one-click add to Lidarr; auto-ingest of new arrivals; legacy screens responsive on mobile
- [ ] **Phase 9 (OPTIONAL): Feed the Engine** — Bulk rating, play-rated nudge, Surprise Me; cuttable without affecting any other phase

## Phase Details

### Phase 1: Foundation, Configuration & Deployment
**Goal**: App runs in Docker with a working settings page, and the image is automatically built and published to Docker Hub so the user can pull and deploy on their NAS from the start
**Depends on**: Nothing (first phase)
**Requirements**: CONF-01, CONF-02, CONF-03, CONF-04, CONF-05, CONF-06, DEPL-01, DEPL-02
**Success Criteria** (what must be TRUE):
  1. User can `docker compose up` using an image pulled from Docker Hub and access the app in a browser
  2. User can enter Plex URL/token, Ollama endpoint/model, and Lidarr URL/API key/quality profile on a settings page
  3. After saving settings, sensitive values are never displayed in the UI or returned by any API endpoint
  4. Plex media directory is mounted read-only and accessible to the container for future Essentia analysis
  5. App persists configuration across container restarts via SQLite
  6. Docker image is automatically built via GitHub Actions on push/tag and published to Docker Hub
**Plans:** 3/3 plans complete
Plans:
- [x] 01-01-PLAN.md — Project foundation: database, encryption, models, app factory, Docker files, dark theme
- [x] 01-02-PLAN.md — Settings page: test-and-configure flows for Plex/Ollama/Lidarr, welcome page, progressive setup
- [x] 01-03-PLAN.md — CI/CD: GitHub Actions multi-platform Docker build and Docker Hub publish
**UI hint**: yes

### Phase 2: Library Sync
**Goal**: User's full Plex music library is synced locally with metadata, kept up to date automatically
**Depends on**: Phase 1
**Requirements**: SYNC-01, SYNC-02, SYNC-03, SYNC-04
**Success Criteria** (what must be TRUE):
  1. User can trigger a library sync and see their full Plex music library reflected in the app
  2. Sync progress is visible in the UI with a progress indicator while the background job runs
  3. Subsequent syncs only import new or changed tracks (delta sync), completing much faster than initial sync
  4. Each synced track has title, artist, album, genre, year, duration, and Plex ratingKey stored locally
**Plans:** 3/3 plans complete
Plans:
- [x] 02-01-PLAN.md — Track model, Plex client extension, sync service core, Plex library bug fix
- [x] 02-02-PLAN.md — Sync API endpoints, progress banner, library browse page with HTMX
- [x] 02-03-PLAN.md — APScheduler integration, auto-sync on startup, settings page sync interval
**UI hint**: yes

### Phase 3: Audio Feature Extraction
**Goal**: Every track in the library has audio features extracted from local files, enabling mood-based filtering
**Depends on**: Phase 2
**Requirements**: AUDIO-01, AUDIO-02, AUDIO-03, AUDIO-04
**Success Criteria** (what must be TRUE):
  1. User can trigger audio analysis and see progress tracking as Essentia processes their library
  2. Analysis can be stopped and resumed without re-analyzing previously completed tracks
  3. Tracks without audio features (analysis failed or pending) fall back to genre/year/artist for mood matching
  4. Extracted features (energy, tempo, danceability, valence) are cached permanently in SQLite — each track analyzed only once
**Plans:** 3/3 plans complete
Plans:
- [x] 03-01-PLAN.md — Track model expansion, Plex file_path extraction, Essentia audio analyzer module
- [x] 03-02-PLAN.md — Analysis service with pause/resume state machine, API endpoints, sync auto-trigger
- [x] 03-03-PLAN.md — Analysis progress banner UI on library page with HTMX polling

### Phase 4: Playlist Generation
**Goal**: User describes a mood in natural language and gets a playlist of matching tracks they can edit and push to Plex
**Depends on**: Phase 3
**Requirements**: PLAY-01, PLAY-02, PLAY-03, PLAY-04, PLAY-05, PLAY-06
**Success Criteria** (what must be TRUE):
  1. User can type a mood description (e.g., "chill Sunday morning coffee vibes") and receive a playlist of matching tracks
  2. User can specify the number of tracks to include before generating
  3. User can review the generated playlist and edit it — adding, removing, and reordering tracks before finalizing
  4. User can push the finalized playlist to a specific Plex library as a named playlist
  5. The LLM (Ollama in v1, replaced by Anthropic mid-milestone) interprets mood descriptions into structured audio feature criteria that drive track scoring
**Plans:** 3/3 plans complete
Plans:
- [x] 04-01-PLAN.md — Data models, Pydantic schemas, playlist scoring engine, nav update, Instructor dependency
- [x] 04-02-PLAN.md — Chat service with Instructor LLM pipeline, session state, API endpoints
- [x] 04-03-PLAN.md — Chat UI templates, playlist card with drag-drop, push-to-Plex, visual verification
**UI hint**: yes
**Status note**: Capabilities retiring in v2.0 — chat UI removed in Phase 7 (UI-06); scoring primitive (PLAY-03) and Plex push (PLAY-06) carry forward as building blocks for vibe slotting and Suggestions reconciliation.

---

## Milestone v2.0: Music Companion

> *Original v1.0 Phase 5 (Playlist Management & History) and Phase 6 (Artist Discovery) were superseded by the v2.0 phases below — see PROJECT.md and REQUIREMENTS.md for absorption/deferral rationale (PLEX-01/02/03 → out-of-scope or absorbed into vibe slotting; HIST-01/02 → out-of-scope under continuous-queue model; DISC-01/02 → reframed as DISC-03..07).*

### Phase 5: Plex Event Foundation + Rating Sync
**Goal**: Composer reliably ingests Plex events (webhook primary, polling fallback), dedupes them, and propagates `RatingChanged` end-to-end so every downstream v2 phase can react to ratings flowing in from Plexamp
**Depends on**: Phase 2 (extends existing Plex sync infrastructure); no v2 dependencies
**Requirements**: EVT-01, EVT-02, EVT-03, EVT-04, EVT-05, EVT-06, EVT-07, RATE-01, RATE-02, RATE-03, RATE-04, RATE-05, OPS-01, OPS-02, OPS-03, OPS-04, DEBUG-01
**Success Criteria** (what must be TRUE):
  1. User rates a track in Plexamp; within 5 seconds, Composer's UI reflects the new rating without a page refresh, and an audit row exists in `EventLog` with `processed_at` set
  2. Webhook + polling both populate `EventLog` for the same logical rating event but only one downstream `RatingChanged` is dispatched (dedupe via `UNIQUE` constraint on `dedupe_key`)
  3. User clicks "Resync now" on the home/settings page and Composer pulls `userRating` for every track in the rated view, emitting `RatingChanged` for every diff
  4. Setup-wizard webhook step shows the user's webhook URL with a copy button and turns ✓ when Plex's "test webhook" event is received
  5. Rated-track count is visible on the home page and updates in real time as ratings arrive (webhook or poll)
**Plans:** 4/4 plans complete
Plans:
- [x] 05-01-PLAN.md — Foundation: schema migration, models (EventLog/LLMUsage/TasteProfile/events.py), event bus + dispatcher, webhook receiver, Wave 0 test scaffolds, dependency adds (anthropic/scikit-learn/psutil + pyarr bump)
- [x] 05-02-PLAN.md — Polling job (APScheduler), rating sync service, plex_client extension for userRating/lastViewedAt/viewCount, first-run auto-backfill, Resync now button + backfill banner partial
- [x] 05-03-PLAN.md — Anthropic SDK client (explicit ttl=1h cache_control + LLMUsage logging) + taste profile service (4-D centroid + top artists/genres + LLM summary text + 10% delta recompute trigger)
- [x] 05-04-PLAN.md — Webhook URL auto-detection (Docker/LAN/Tailscale), wizard webhook radio form + test indicator, /debug/events page, settings additions (Resync button + diagnostics footer link), atomic ollama_client.py + tests deletion
**UI hint**: yes
**New dependencies**: `anthropic>=0.100,<1.0` (replaces v1 direct-httpx wrapper — OPS-02), `scikit-learn>=1.8,<2.0` (added here to keep image-build churn off the Phase 6 critical path — OPS-04), `pyarr>=6.6,<7.0` pin bump (OPS-03)
**Key Concerns** (pitfalls to bake in — see `.planning/research/PITFALLS.md`):
  - **Day-one webhook idempotency** (Pitfall 1): `EventLog.dedupe_key UNIQUE` constraint on the first commit, not retrofitted. Dedupe key = `sha256(event_type|ratingKey|user_rating|5s_timestamp_bucket)`. Insert-or-ignore pattern.
  - **PlexAPI is sync, blocks the event loop** (Pitfall 4): every PlexAPI call from an `async` handler runs through `asyncio.to_thread(...)` — or the handler is declared `def` so FastAPI dispatches it to the threadpool. Document the convention in CLAUDE.md before any handler is written.
  - **`userRating` is 0–10, not 0–5** (Pitfall 2): persist raw 0–10 in SQLite; convert to stars only at display boundaries via a single helper. Add a unit test asserting `userRating=7.0` → "3.5 stars" in UI, `7.0` in DB.
  - **Webhook reachability test** (EVT-07): wizard's webhook URL builder must use the Docker-network hostname (`http://composer:8085/api/webhooks/plex`) when Plex and Composer share `synobridge`, not the Tailscale IP. The "test webhook" listener confirms reachability before the user leaves the wizard step.
  - **Bounded polling queries** (Pitfall 21): polling fallback hits the recently-rated and recently-played views with `updatedAt>>` filters, never a full library scan. Default 5-min interval; back off when idle.
  - **Schema migration without Alembic** (Pitfall 19, OPS-01): extend the existing `_migrate_add_columns()` shim for `Track.user_rating`, `rating_changed_at`, `last_viewed_at`, `view_count`. New tables (`EventLog`) via `create_all()`. No Alembic.
  - **Debug surface from day one** (DEBUG-01): `/debug/events` HTML page lists last 50 received events (webhook + poll) with timestamp, source, dedupe_key, payload preview, processed_at, handler errors. Also surfaces current poll interval, last poll result, and last "test webhook" ping. This is the primary diagnostic surface when something looks wrong — copy-pasteable layout so the user can share output without poking SQLite.

### Phase 6: Vibe Clustering + Setup Wizard
**Goal**: User completes the first-run setup wizard and ends with 3–7 named, persistent "vibe" playlists in Plex, each populated from their rated set, with newly-rated tracks auto-slotting into matching vibes within seconds
**Depends on**: Phase 5 (rating sync must be working — vibes need a populated rated set and a `RatingChanged` event stream)
**Requirements**: VIBE-01, VIBE-02, VIBE-03, VIBE-04, VIBE-05, VIBE-06, VIBE-07, VIBE-08, VIBE-09, VIBE-10, VIBE-11, VIBE-12, WIZ-01, WIZ-02, WIZ-03, WIZ-04, WIZ-05, WIZ-06, WIZ-07, DEBUG-02
**Success Criteria** (what must be TRUE):
  1. User completes the setup wizard end-to-end and sees N (3–7) named vibe playlists in Plex Web titled `Composer · {name}`, each containing rated tracks that match the vibe's audio-feature centroid
  2. User rates a previously-unrated track 4 stars in Plexamp; within 10 seconds, the track appears in the matching `Composer · {name}` Plex playlist (slotting via audio-feature distance)
  3. User edits a vibe name in the wizard ("Late Night Drives" → "Night Drives"); the corresponding Plex playlist is renamed to `Composer · Night Drives` and persists across container restart
  4. User triggers "Re-cluster vibes" from settings; the LLM proposes new clusters, the user reviews and confirms, and existing manual track→vibe overrides are preserved across the re-cluster
  5. With <30 rated tracks, the wizard refuses to cluster and surfaces the "rate more tracks" gate; with 30–49 rated tracks, single-vibe degraded mode runs; with ≥50, full clustering at silhouette ≥ 0.25
**Plans:** 4 plans
Plans:
**Wave 1**
- [x] 06-01-PLAN.md — Foundation: Vibe/TrackVibe/ManagedPlaylist/SetupState models, Track.pending_slot_in column, base.html alpine-morph + min-h-dvh + viewport-fit=cover, --touch-target-min CSS token, vibe_helpers.feature_chip_text, AST sklearn-allowlist test

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 06-02-PLAN.md — Slot-in pipeline: vibe_clusterer (sklearn k-means + silhouette + LLM naming + refinement turn), plex_playlist_service (create/update/archive/rename/is_managed; Pitfall 5 additive + Pitfall 6 post-push verify), vibe_service (slot_track + unslot_track + per-track lock + soft-margin + pending_slot_in), event_handlers + analysis_service hooks, extended AST static test

**Wave 3** *(blocked on Wave 2 completion)*
- [x] 06-03-PLAN.md — Wizard + conversational refinement loop: api_setup router (7 endpoints), 5 wizard pages (/setup, /setup/webhook, /setup/propose, /setup/confirm, /setup/done) + 10 partials, GET / wizard auto-redirect, refinement loop with HTMX morph swap (Pitfall 15) + 10-turn cap (D-04), finalize creates Composer · {name} Plex playlists under semaphore=1 (D-25)

**Wave 4** *(blocked on Wave 3 completion)*
- [x] 06-04-PLAN.md — Re-cluster + settings + /debug/vibes: api_vibes router (recluster/start/commit/status + reslot-all), settings.html Vibes section + recluster_modal (Keep current vibes NOT Cancel — UI-SPEC BLOCK fix), /debug/vibes diagnostic page + 3 partials (vibe_diagnostic_card, slot_in_log_table, drift_indicator), SlotInLog table + SetupState.recluster_mode column, D-21 diff-based reconciliation, D-22 manual override preservation
**UI hint**: yes
**Key Concerns** (pitfalls to bake in):
  - **Cold-start gating** (Pitfall 3, VIBE-06): hard floor at <30 rated tracks (clustering disabled); 30–49 = single-vibe degraded mode; ≥50 = full clustering. `k_max = min(7, n_rated // 15)`. Silhouette ≥ 0.25 enforced; below threshold, surface a "your taste is tight, try k=2 or rate more" message — don't silently produce nonsense vibes.
  - **Soft membership margin** (Pitfall 24, VIBE-02): track joins a second vibe only if distance to second-closest is within 1 std-dev of distance to closest. Cap at 2 vibes per track. Prevents UX muddiness from blanket soft-assignment.
  - **Plex playlist hands-off + post-push verification** (Pitfalls 5, 6, 20, VIBE-04, VIBE-12, OPS-06): `Composer · ` namespace prefix is mandatory; `ManagedPlaylist` registry is the second ownership marker. After every playlist push, re-fetch and reconcile track set; silently-dropped tracks are logged and retried once. v1-generated playlists (no `Composer ·` prefix) are recognized as legacy and never touched.
  - **Anthropic prompt cache TTL — explicit `ttl: "1h"`** (Pitfall 9): vibe-clusterer Anthropic call sets `cache_control: {"type": "ephemeral", "ttl": "1h"}` explicitly. Log `cache_creation_input_tokens` / `cache_read_input_tokens` per response. The 5-minute default silently regressed in March 2026 — explicit TTL is mandatory.
  - **LLM hallucination validation on track IDs** (Pitfall 10, v1-burned issue): every LLM-returned track ID round-trips against the candidate set; prefer integer indices over string IDs in prompts. v1's Phase 4 burned this; the convention carries forward.
  - **Per-track lock on slot-in** (Pitfall 23): rapid rate-correct sequences (3★ → 4★ within 200ms) serialize through an `asyncio.Lock` keyed on ratingKey; latest rating read from Plex inside the lock, not from the webhook payload.
  - **Wizard state in SQLite, not cookies** (WIZ-02): step 2 produces ~50KB of cluster proposals — server-side `SetupState` row.
  - **Alpine morph for HTMX swaps** (Pitfall 15): wizard partials use `hx-ext="alpine-morph"` from the first template; document the convention project-wide.
  - **Debug surface for vibes** (DEBUG-02): `/debug/vibes` HTML page shows each vibe's centroid features, member count, silhouette score, last-clustered-at, and the last 20 slot-in decisions (track → vibe(s) with computed distance). "Re-show last cluster proposal" button surfaces the LLM naming response. Critical when slot-in produces a surprising assignment.

### Phase 06.1: Vibe Wizard Foundations: server-led clustering + user-led vibe input (INSERTED)

**Goal:** Replace the broken Phase 6 initial-proposal flow with server-led k-means membership (every rated track lands in its nearest vibe) and user-led Step 3 input (user types 3–7 vibe names; ONE LLM call maps cluster centroids to names with a strong/weak/no_match fit grade); ship a one-shot first-deploy migration that archives existing Composer · Plex playlists and wipes the broken state
**Requirements**: VIBE-02, VIBE-04, VIBE-05, VIBE-09, WIZ-05
**Depends on:** Phase 6
**Plans:** 2/2 plans complete

Plans:
- [x] 06.1-01-PLAN.md — Server-led clustering: LLMVibeFit + LLMVibeMappingResponse schemas, map_user_vibes_to_clusters (k-means labels drive seed_track_indices), permutation validator + retry-once, fit fields on VibeProposal
- [x] 06.1-02-PLAN.md — Wizard UI + finalize population + first-deploy migration: textbox-stack Step 3 input, fit-grade chip on proposal cards, propose/init endpoint rewire, finalize triggers reslot_all_rated_tracks, MigrationLog gate + run_phase_61_migration() in lifespan

### Phase 06.2: LLM-Direct Vibe Assignment (INSERTED)

**Goal:** Replace k-means cluster *membership* with LLM-direct zero-shot assignment of each rated track to its best-fit user-typed vibe. Two-pass design: (1) batched per-track assign with confidence grade (strong/weak/uncertain), (2) boundary-review pass that sends weak/uncertain tracks the peer context of their candidate vibes. K-means stays as a centroid generator only — no membership decisions from k-means. Audio features (energy/tempo/danceability/valence) still passed to the LLM as a tiebreaker for tracks with low artist-recognition. Also adds a user-triggered "Start Over" wizard reset that wipes vibe + wizard state (Vibe, TrackVibe, ManagedPlaylist, SlotInLog, SetupState) while preserving integration credentials (Plex/Anthropic/Lidarr in ServiceConfig).
**Requirements**: VIBE-02, VIBE-04, VIBE-13, VIBE-14, WIZ-08
**Depends on:** Phase 6.1 (user-led naming + fit-grade infra), Phase 6 (audio-feature plumbing)
**Plans:** 2/2 plans complete

Plans:
- [x] 06.2-01-PLAN.md — LLM-direct two-pass vibe assignment: AnthropicClient adaptive-thinking + robust text-block extraction (paired patch), assign_tracks_to_user_vibes (preamble + Pass 1 strong/weak/uncertain + Pass 2 peer-review), Vibe.centroid recomputed from LLM members, /propose/init rewire, Pass-2 confidence chip on proposal card, /debug/vibes purpose-segmented cost panel
- [x] 06.2-02-PLAN.md — WIZ-08 Start Over reset: archive_playlist suffix kwarg (date-stamped `(archived YYYY-MM-DD)`), POST /api/setup/start-over endpoint (atomic Plex archive best-effort + DB wipe; ServiceConfig preserved), settings.html Start Over button + start_over_modal.html (visible only when Vibe.count() > 0)

**Success Criteria** (what must be TRUE):
1. On a 600-track library with 5 user vibes, ≥90% of tracks with recognizable artist+title land in the vibe a human would pick (sampled UAT against 50 random tracks)
2. Total cost per full library re-cluster ≤ $1.50 (Sonnet 4.6 with prompt-cached system context); measured via LLMUsage table
3. K-means stays in the codebase as a centroid-generator only — membership decisions traceable to LLM output, not to `k-means.labels_`
4. Audio features still passed in the LLM input payload as a tiebreaker for low-recognition tracks
5. The wizard's existing "Re-cluster vibes" button runs the new pipeline; proposal cards show a per-track confidence chip (strong / weak / uncertain) on the second-pass boundary tracks
6. "Start Over" button in settings (or the wizard itself) wipes Vibe / TrackVibe / ManagedPlaylist / SlotInLog / SetupState — including the `draft_proposals_json` field — and re-archives any current `Composer · {name}` Plex playlists with `(archived)` suffix. ServiceConfig rows for Plex / Anthropic / Lidarr are NOT touched. After reset, a private-browser session shows a clean wizard at Step 1 with no leaked proposals from the prior session. Behavior is idempotent — running reset twice in a row is a no-op on the second call

### Phase 7: Suggestions Queue + v1 Chat Retirement
**Goal**: User has a continuous `Composer · Suggestions` Plex playlist that drains as they listen and refills with taste-aware picks within 30 seconds; v1 mood-chat is retired; vibes home is the new landing page
**Depends on**: Phase 6 (vibe centroids + cached taste profile must exist for shortlist + LLM ranking; Suggestions playlist is bootstrapped on wizard finalize)
**Requirements**: SUGG-01, SUGG-02, SUGG-03, SUGG-04, SUGG-05, SUGG-06, SUGG-07, SUGG-08, SUGG-09, SUGG-10, SUGG-11, UI-01, UI-02, UI-03, UI-04, UI-05, UI-06, OPS-05, DEBUG-03, DEBUG-05
**Success Criteria** (what must be TRUE):
  1. User plays a track from `Composer · Suggestions` to scrobble; within 30 seconds the played track is gone from the playlist and a new track has been appended at the bottom (taste-matched, with a "Why this track?" rationale visible on tap)
  2. v2 vibes home is the landing page at `/`; the legacy `/chat` route returns 404 (or redirects to `/`); nav references to mood-chat are gone; archived chat templates are not reachable from any link
  3. Mobile portrait layout at 375px wide: bottom tab bar (Vibes / Suggestions / Discover / Settings) is visible above the iOS toolbar (uses `h-dvh` + `safe-area-inset-bottom`); every tappable element measures ≥44px; no hover-only states block information access
  4. Settings page shows the current daily LLM cost meter ("Anthropic spend today: 7 calls, $0.02 / $0.42 budgeted") with `cache_read_input_tokens` visibly accumulating across calls (caching is hitting)
  5. A misconfigured refresh trigger (or any refill loop firing >5 times in 60s) trips the LLM cost circuit breaker; the UI surfaces "Suggestions paused — cost limit hit" and no further LLM ranking calls fire until the next event window
**Plans:** 3 plans
Plans:
- [x] 07-01-PLAN.md — Queue foundation: SuggestionsMirror model, bootstrap_suggestions_queue (dual-caller wizard finalize + lifespan migration), handle_track_played drain branch, extend AST PlexAPI test
- [x] 07-02-PLAN.md — LLM ranking pipeline + cost circuit breaker (same-commit per Pitfall 11) + skip-tracking (SuggestionHistory / NegativeSignal / RefillTriggerLog) + settings cost meter + SUGG-10 vibe coverage CTA
- [x] 07-03-PLAN.md — Mobile-first base shell (bottom tab bar, h-dvh, safe-area-inset, 44px targets) + /suggestions page + vibes home + v1 chat retirement + /debug index + /debug/suggestions
**UI hint**: yes
**Key Concerns** (pitfalls to bake in):
  - **LLM cost circuit breaker — FIRST commit, not last** (Pitfall 11, SUGG-11): daily quota (50 calls), burst limit (5 calls / 60s), per-event debounce (60s cooldown per `(track_id, event_type)`). All four (counter + dashboard + breaker + debounce) ship in the same commit as the first ranking call. Adding them after a runaway means refunding spend.
  - **Anthropic prompt cache `ttl: "1h"` explicit** (Pitfall 9, SUGG-05): suggestions ranking system message sets `cache_control: {"type": "ephemeral", "ttl": "1h"}`. Sonnet 4.6 minimum 2048 tokens — verify `cache_creation_input_tokens > 0` on first call. Without explicit TTL, every call is a 5-min cache miss and the budget blows.
  - **Pydantic validation on track IDs** (Pitfall 10, v1-burned): every LLM-ranked track ID validated against the shortlist before insertion into the queue. Use Instructor `max_retries` with a Pydantic validator, or send integer indices and map back server-side. v1's Phase 4 already taught this — non-negotiable.
  - **Trust the webhook payload, don't re-fetch** (Pitfall 7): consumption detection uses the webhook's own `lastViewedAt`/timestamp; no `plex.fetchItem(...).lastViewedAt` re-fetch race.
  - **Scrobble = consumption, not endorsement** (Pitfall 8, SUGG-08, SUGG-09): scrobble drains a track from Suggestions but does NOT strengthen the taste signal. Endorsement = `userRating ≥ 3 stars`. Skip-bomb detector: >10 tracks consumed in <10 min disables Suggestions drain.
  - **SQLite is read-truth, Plex is write-target** (eventually-consistent mirror, SUGG-02): on `TrackPlayed`, act locally first (remove from mirror, shortlist, rank, pick) then push to Plex via a single `update_playlist_items` call. Don't re-fetch the Plex playlist as the read path.
  - **Cross-surface dedup** (Pitfall 12, SUGG-07): `SuggestionHistory` table with 14-day exclusion window; `recently_recommended_track_ids` injected into the LLM prompt as "don't recommend these again."
  - **Alpine morph + iOS dvh + 44px targets + tap-not-hover** (Pitfalls 15, 16, 17, 18, UI-03/04/05): project-wide CSS conventions established in this phase — `--touch-target-min: 44px`, `h-dvh` not `h-screen`, `env(safe-area-inset-bottom)` on fixed bottom UI, `viewport-fit=cover` meta tag, `<body hx-ext="alpine-morph">`. Test every interaction on a real iPhone, not desktop emulation.
  - **LLM observability with first ranking call** (OPS-05): structured log per call (`{model, input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens, cost_estimate_usd, reason}`); daily aggregate exposed in settings.
  - **Debug surface for suggestions** (DEBUG-03): `/debug/suggestions` page shows current queue contents (track + vibe + score + rationale), last 20 refill triggers (event source, candidates evaluated, picks made, latency), recent skip-tracking signals, current circuit-breaker state, and last 20 LLM calls (model, cache hit/miss, tokens, cost, prompt summary). Highest-leverage debug page since suggestions is where most user "why did it pick that?" questions land.
  - **Debug index linked from settings** (DEBUG-05): `/debug` index page lists all debug surfaces (`/debug/events`, `/debug/vibes`, `/debug/suggestions`, `/debug/discovery` once it lands). Settings page footer links to `/debug` so the user can find diagnostics without remembering URLs. All debug pages render plain HTML (no JS-only content) so output is copy-pasteable.

### Phase 07.1: Suggestions Cost Architecture: SQL refill + weekly discovery (INSERTED)

**Goal:** Replace Phase 7's per-event LLM refill (which costs ~$0.05/play and trips the $0.42 daily breaker after ~8 plays — confirmed by NAS UAT 2026-05-14, captured in `.planning/notes/phase-07-followup-cost-architecture.md`) with a hybrid: (1) SQL-driven refill in the hot path against Phase 6's pre-computed `TrackVibe.distance` (free, instant on every drain), and (2) a once-weekly LLM "discovery" call that injects ~5 tracks the user owns but rarely plays (taste-aware variety). Drops steady-state cost from ~$150/mo to ~$0.20/mo while preserving Phase 7's AI-curated property.

**Requirements**: NEW: SUGG-12 (SQL refill from TrackVibe), SUGG-13 (weekly discovery LLM call), SUGG-14 (defensive max_tokens sizing); REWORKS: SUGG-04 (the "LLM ranks shortlist on every refill" semantic is replaced)

**Depends on:** Phase 7 (SuggestionsMirror + handle_track_played drain + Composer · Suggestions playlist materialization), Phase 6.2 (TrackVibe.distance computed for every track)

**Plans:** 2/3 plans executed

Plans:
- [x] 07.1-01-PLAN.md — Foundation: DiscoveryState model + SQL refill primitive (refill_mirror_sql) + DELETE legacy refill_suggestions_queue / refill_suggestions_for_vibe / SUGGESTIONS_RANK_MAX_TOKENS + handle_track_played counter increment + REQUIREMENTS.md updates (SUGG-12, SUGG-13, SUGG-14)
- [x] 07.1-02-PLAN.md — Weekly discovery cron: compute_discovery_eligible (D-A1) + DiscoveryPicksResponse pydantic + discovery_call_weekly LLM handler with cost-breaker gate + max_tokens retry guard + APScheduler Sunday 03:00 UTC cron + startup catch-up gate (D-C2) + lifespan wiring
- [ ] 07.1-03-PLAN.md — Cost breaker repurpose: rename DAILY_COST_BUDGET_USD → WEEKLY_DISCOVERY_BUDGET_USD (D-D3) + cost meter UI relabel DAILY → WEEKLY (rolling 7-day aggregation) + AST regression tests forbidding refill_suggestions_queue / SUGGESTIONS_RANK_MAX_TOKENS / max_tokens=2000 / async-context Session leakage

**Success Criteria** (what must be TRUE):
1. Refill on every play uses ZERO LLM tokens (SQL query against `TrackVibe.distance` ordered ascending, filtered by recency)
2. Weekly LLM discovery call fires on a hardcoded schedule (Sunday 03:00 UTC; configurable cron deferred per CONTEXT.md D-C1), injecting 3–7 tracks unplayed in 90+ days that fit the user's current taste profile (count adapts to listening intensity)
3. Daily LLM cost for steady-state listening (no rating changes, no library updates): $0.00
4. Mirror is repopulated to target (default 30) within seconds of any drain
5. Phase 7's per-event `refill_suggestions_queue` is removed or feature-flagged off by default; the LLM-led path lives only in the weekly discovery call
6. New constants pinned via regression tests; the truncation pitfall from quick task 260514-e6w cannot recur (defensive `max_tokens` sizing + `stop_reason=max_tokens` retry guard)

**Key Concerns** (pitfalls to bake in):
- **Don't reintroduce per-event LLM cost** — the entire point. Refill hot path stays SQL-only; LLM never fires from `handle_track_played` directly.
- **Defensive max_tokens** — quick task 260514-e6w bumped to 8000 as a temporary fix; the discovery call should size generously and add a `stop_reason=max_tokens` retry that doubles budget rather than returning truncated JSON to pydantic.
- **Caching becomes moot** — weekly calls always cold-start the cache. Don't waste effort on D-07's longer preamble.
- **Migration of in-flight state** — existing `MigrationLog` rows from Phase 7 + populated `SuggestionsMirror` data must remain valid; the SQL refill takes over draining the existing mirror seamlessly.

### Phase 8: Lidarr Discovery + Polish
**Goal**: User can discover new artists matching their taste and one-click add to Lidarr; new arrivals from Lidarr auto-ingest into Essentia analysis + vibe scoring; legacy v1 screens (settings, library) are responsive on mobile portrait
**Depends on**: Phase 7 (taste profile, prompt-cache infrastructure, LLM cost breaker, recommendation history table all reused; Lidarr is the leafy outward closer)
**Requirements**: DISC-03, DISC-04, DISC-05, DISC-06, DISC-07, UI-07, UI-08, OPS-06, DEBUG-04
**Success Criteria** (what must be TRUE):
  1. User opens the Discover tab and sees ≥10 artist suggestions ranked by closeness to taste profile, each with a one-line "why" rationale ("Same label as Four Tet · MusicBrainz adjacent to 3 starred artists"); no artist already in the library appears
  2. User clicks "Add" on a discovered artist; Lidarr accepts the request without error using the configured quality + metadata profiles, and Composer shows "Added to Lidarr" feedback
  3. After Lidarr imports a new album from a Composer-added artist, within one Plex sync cycle the new tracks are auto-queued for Essentia analysis, then auto-scored against vibes and become eligible for the Suggestions queue
  4. Settings page Lidarr connection-test reliably distinguishes unreachable / auth-failure / version-mismatch with clear error messages — the v1 carry-over connection bug is fixed
  5. Settings and library screens render cleanly portrait at 375px wide: service cards stack vertically, track tables become tap-friendly card lists, no horizontal scroll, no hover-only affordances
**Plans**: TBD
**UI hint**: yes
**Key Concerns** (pitfalls to bake in):
  - **Lidarr connection-test fix is foundational** (Pitfall 14, DISC-07): the v1 carry-over bug (likely Docker-network resolution against `synobridge` rather than `localhost`) is the FIRST task of the phase. Until connection-test is reliable, every other Lidarr feature is built on sand. Reference `.planning/notes/connection-test-bugs.md`.
  - **Popularity-bias guardrails** (Pitfall 13, DISC-04): anchor LLM prompt on a small specific seed set (3 underrepresented-genre tracks from the user's stars), filter MusicBrainz results by listener-count, require MusicBrainz adjacency to ≥1 starred artist OR shared label/release-group. Show provenance ("Suggested because you starred Four Tet — same label").
  - **pyarr 6.6 metadata profile + quality profile fetch** (Pitfall 14, OPS-03): pyarr 6.6's `add_artist()` requires both `qualityProfileId` AND `metadataProfileId` — fetch dynamically from `/api/v1/qualityprofile` and `/api/v1/metadataprofile`, never hardcode. Confirm signature during phase planning (light spike per SUMMARY.md).
  - **MusicBrainz validation gate** (Pitfall 10): every LLM-suggested artist name validated via MusicBrainz lookup before showing to user — prevents hallucinated "The Velvet Echoes from Brooklyn" with a real-sounding name. Lidarr's lookup endpoint suffices.
  - **Post-add monitoring** (Pitfall 14): 24h check after each Composer-initiated add — if Lidarr hasn't imported any releases, surface "Velvet Echoes added 2 days ago, no releases found yet — change quality profile?" Prevents silent failure mode.
  - **Auto-ingest plumbing already mostly works** (Pitfall 25, DISC-06): existing `trigger_post_sync_analysis()` from v1 Phase 3 picks up new tracks; only new code is the Lidarr webhook (or aggressive 10-min poll for 24h post-add) that triggers a Composer sync of the affected library section before the regular interval.
  - **Cross-surface dedup reused** (Pitfall 12): Discover filters out artists with any track already in `composer.tracks` or already in Lidarr's `/api/v1/artist` — cache the managed list for 1h.
  - **Legacy playlist recognition** (OPS-06): scan for v1-generated Plex playlists (no `Composer ·` prefix in `ManagedPlaylist`) and surface them as legacy — explicitly NOT touched, NOT analyzed as taste signal, NOT auto-imported into vibes.
  - **Mobile responsive pass** (UI-07, UI-08): apply the Phase 7 mobile conventions (h-dvh, safe-area-inset, 44px targets, tap-not-hover) to the v1 settings and library screens.
  - **Debug surface for discovery** (DEBUG-04): `/debug/discovery` HTML page shows the last candidate set (with provenance per artist — seed source, MusicBrainz adjacency, label/genre overlap, LLM rationale), recent MusicBrainz queries with rate-limit headers, recent Lidarr `add_artist` requests + responses, and the Lidarr connection-test result history. Specifically aimed at debugging "why did Composer recommend X?" and "why did Lidarr reject this add?"

### Phase 9 (OPTIONAL): Feed the Engine
**Goal**: Surface bulk rating, play-rated nudge, and Surprise Me to encourage faster rating accumulation, particularly for underweighted vibes
**Depends on**: Phase 7 (reuses UI conventions, taste profile, ranking infrastructure); nothing depends on Phase 9 downstream
**Requirements**: ENG-01, ENG-02, ENG-03
**Success Criteria** (what must be TRUE):
  1. User opens "Bulk Rating" and sees 10 strategically diverse unrated tracks (mixed energy/tempo/genre); each track has a "Rate in Plexamp" deeplink that opens the track in the Plexamp app for in-Plexamp rating
  2. Vibes home displays a "Played but not rated" nudge listing unrated tracks with `viewCount ≥ 5`; tap deeplinks to Plexamp
  3. User taps "Surprise Me"; Composer picks a single unrated taste-matched track and deeplinks to Plexamp to play it (bypasses the Suggestions queue)
**Plans**: TBD
**UI hint**: yes
**Status note**: **OPTIONAL AND CUTTABLE.** Nothing in v2.0 depends on Phase 9 downstream. If milestone budget runs short, drop Phase 9 entirely — the Music Companion is feature-complete after Phase 8. Phase 9 can also ship alongside or after Phase 8 without blocking anything.
**Key Concerns**:
  - **Composer never writes ratings** (PROJECT.md key decision): Phase 9 nudges the user toward rating in Plexamp via deeplinks — never writes `userRating` back to Plex. If bulk-rating UX becomes blocked because Plexamp deeplinks are unreliable, the locked decision can be revisited during Phase 9 planning, not before.
  - **Diversity selection for bulk rating** (ENG-01): the "10 strategically diverse" selection algorithm — pick across the audio-feature space and across genres so each rating teaches the system more — is a Phase 9 design problem; start with a simple stratified sample.

## Progress

**Execution Order:**
v1.0 phases executed 1 → 2 → 3 → 4 (complete).
v2.0 phases execute 5 → 6 → 7 → 8, then optionally 9.
Phase 9 has no downstream dependents; it can ship parallel to Phase 8 or be cut entirely.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation, Configuration & Deployment | 3/3 | Complete | 2026-04-09 |
| 2. Library Sync | 3/3 | Complete | 2026-04-09 |
| 3. Audio Feature Extraction | 3/3 | Complete | 2026-04-09 |
| 4. Playlist Generation | 3/3 | Complete (retiring in v2.0) | 2026-04-10 |
| 5. Plex Event Foundation + Rating Sync | 4/4 | Complete    | 2026-05-10 |
| 6. Vibe Clustering + Setup Wizard | 9/9 (3 Phase 6 + 2 Phase 6.1 + 2 Phase 6.2 + 2 hotfixes) | Complete | 2026-05-13 |
| 7. Suggestions Queue + v1 Chat Retirement | 0/TBD | Not started | - |
| 8. Lidarr Discovery + Polish | 0/TBD | Not started | - |
| 9. Feed the Engine (OPTIONAL) | 0/TBD | Not started (cuttable) | - |
