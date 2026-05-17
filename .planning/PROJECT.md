# Composer

## What This Is

A self-hosted music companion that turns your Plex star ratings into a continuously curated listening experience. Composer uses your rated tracks as a taste signal: it auto-organizes them into a small set of "vibe" playlists, and it surfaces unrated tracks from your library that you'd probably like next. It runs as a Docker container alongside Plex/Plexamp and Lidarr.

## Core Value

Your Plex stars are the truth. Composer turns them into living vibe playlists and a steady stream of personalized discoveries — without you having to describe a vibe each time.

## Current Milestone: v2.0 Music Companion

**Goal:** Pivot Composer from a one-shot vibe-to-playlist generator into a continuous music companion built around your rated tracks as the taste signal — auto-curating vibe playlists and surfacing personalized discoveries from your unrated library.

**Target features:**
- Plex rating sync (userRating) as the foundational taste signal
- AI-clustered vibes (3–7) derived from rated tracks; user-named, persistent
- Auto-slot newly rated tracks into matching vibe playlist(s) on Plex
- Continuous "Composer Suggestions" queue (~30 unrated tracks) that drains as you listen
- Lidarr taste-aware artist discovery with one-click add
- Auto-ingest Lidarr arrivals into the analysis + scoring pipeline
- Mobile-first UI (portrait-first across all v2 surfaces)
- First-run setup wizard (rating source → propose vibes → confirm)
- v1 mood-chat UI retired

**Key context:**
- User has 460+ rated tracks (and growing) across a ~10k-track Plex library
- Rating happens in Plexamp during normal listening; Composer reads that signal, never overrides it
- Composer manages only its own playlists; existing Plex playlists stay untouched
- Anthropic Claude is the LLM (replaced Ollama mid-v1); used sparingly for vibe clustering and suggestion ranking only

## Requirements

### Validated

Shipped in v1.0 (phases 1–4):

- [x] App deploys as a single Docker container with compose YAML, built via GitHub Actions and published to Docker Hub (Phase 1)
- [x] In-app settings page configures Plex, Anthropic, and Lidarr credentials; secrets never displayed or returned in API responses after entry (Phase 1)
- [x] Plex media directory mounted read-only for Essentia analysis; configuration persisted across container restarts (Phase 1)
- [x] Plex music library syncs to local SQLite with paginated initial sync, delta updates, and background progress (Phase 2)
- [x] Track metadata captured: title, artist, album, genre, year, duration, ratingKey (Phase 2)
- [x] Background analysis pipeline runs APScheduler-driven sync on a configurable interval (Phase 2)
- [x] Essentia extracts audio features (energy, tempo, danceability, valence) from local files with pause/resume and per-track caching (Phase 3)
- [x] Mood-to-playlist chat pipeline: natural-language description → LLM-derived feature criteria → scored library tracks → editable playlist → push to Plex (Phase 4) *(retiring in v2.0; capabilities absorbed into vibe + suggestions model)*

### Active (v2.0)

High-level scope. Detailed REQ-IDs live in `.planning/REQUIREMENTS.md`.

- [ ] Sync Plex `userRating` per track and detect rating changes via webhook (primary) + polling (fallback) + manual refresh
- [ ] AI-cluster the rated set into 3–7 candidate vibes during a guided first-run setup wizard; user names/edits/finalizes
- [ ] Cache per-vibe feature profiles; auto-slot newly-rated tracks into matching vibes via audio-feature distance
- [ ] Manage one Plex playlist per vibe (Composer-owned, never touches existing user playlists)
- [ ] Maintain a continuous "Composer Suggestions" Plex playlist (default 30 tracks) that drains as tracks are played and refills with taste-matched candidates
- [ ] Detect track consumption via Plex `lastViewedAt` (webhook + polling); refill via audio-feature shortlist + LLM ranking
- [ ] Auto-ingest Lidarr-imported tracks into Essentia analysis + vibe/suggestion scoring
- [ ] Surface taste-aware artist recommendations not yet in the library; one-click add to Lidarr with configured quality profile
- [ ] Mobile-first portrait-first design for all new v2 surfaces; responsive pass on legacy settings/library screens
- [ ] Retire the v1 mood-chat UI; vibes home becomes the new landing page
- [ ] Fix Lidarr connection-test bug carried over from v1

### Out of Scope

- Multi-user / authentication — single-user personal tool
- Mobile app — web-only (mobile-first responsive web is the answer)
- In-app playback — Plex/Plexamp handles it
- Manual audio fingerprinting — Essentia handles feature extraction
- Automatic artist downloading — Lidarr handles downloads after add
- Mood-chat / on-demand vibe-to-playlist generation — retired in v2.0; companion model replaces it
- Generation history UI — continuous queue model makes a "history of generated playlists" obsolete
- Browsing or analyzing existing Plex playlists not created by Composer — hands-off principle
- Suggesting additions to user-created Plex playlists — Composer only manages its own
- Spotify integration — deferred; Essentia covers audio feature needs
- Cloud LLM provider switching — Anthropic locked in for v2.0
- Re-clustering vibes on a schedule — only on user request

## Context

- User has 10k+ tracks managed by Lidarr, served by Plex/Plexamp on a Synology DS423+ (Intel Celeron J4125, 32GB RAM, amd64)
- All NAS apps share the `synobridge` Docker network; remote access via Tailscale (no public auth/HTTPS in app)
- Plex library is the system of record for tracks AND for star ratings (Composer reads, never writes ratings)
- v1.0 shipped a working mood-chat playlist generator (phases 1–4); v2.0 reorients around the rated-tracks taste signal
- Anthropic Claude replaced Ollama late in v1.0 — current LLM stack is Claude API (Sonnet 4.6 default, configurable)
- Essentia analysis pipeline already extracts energy/tempo/danceability/valence and caches in SQLite — reused as-is in v2.0

## Constraints

- **Deployment**: Must run as a single Docker container with compose YAML, alongside the existing arr stack
- **Security**: API keys must never be exposed in UI or API responses after initial configuration
- **Dependencies**: Requires Plex server (with Plex Pass for webhooks; polling fallback otherwise), Lidarr instance, Anthropic API key
- **CPU**: No global library re-scoring under any code path — scores compute lazily per-event, cached in SQLite
- **LLM cost**: Stay under ~$5/year of Anthropic spend in normal use (event-driven generation, prompt-cached taste profile)
- **Mobile-first**: Every new v2 UI surface must be designed portrait-first; legacy screens get responsive pass

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Spotify API for audio features | Best data quality for energy/tempo/mood classification; free dev tier available | Replaced — Essentia (local, self-hosted) used instead from v1 onward |
| AI + Spotify combined approach | AI interprets natural language moods, Spotify features do quantitative filtering | Reframed in v2.0 — LLM clusters rated taste; audio features score candidates |
| Single Docker container | Matches existing deployment pattern, simple compose YAML alongside arr stack | Validated v1.0 |
| GitHub Actions → Docker Hub | CI/CD pipeline for image builds, standard for self-hosted app distribution | Validated v1.0 |
| One-click Lidarr add (not full auto) | User stays in control of what gets added, but friction is minimal | Carries to v2.0 |
| Anthropic Claude over Ollama | Quality/latency on a low-power NAS CPU made local LLM impractical for ranking | Validated v1.0 (mid-milestone) |
| v2.0: Rated tracks as taste signal | Solves v1 flaws — finite vibes, no preference signal — by anchoring everything to user-validated stars | Locked v2.0 |
| v2.0: Tiered similarity (LLM for clusters/ranking, distance for slotting) | Quality where it matters, cheap CPU/cost everywhere else; no global rescoring | Locked v2.0 |
| v2.0: Event-driven (no scheduled refreshes) | Composer Suggestions only generates the delta when you actually consume a track; near-zero idle cost | Locked v2.0 |
| v2.0: Hybrid vibe clustering | AI proposes 3–7 clusters from rated set; user names/edits/finalizes; persistent thereafter | Locked v2.0 |
| v2.0: Plex webhooks primary, polling fallback | Plex Pass holders get instant updates; everyone else still works via polling | Locked v2.0 |
| v2.0: Hands-off existing Plex playlists | Composer only manages its own (vibes + Suggestions); never touches user-created playlists | Locked v2.0 |
| v2.0: Mobile-first | Primary access pattern is phone in portrait; design constraint for every new screen | Locked v2.0 |
| v2.0: Retire v1 mood-chat | Companion model replaces it; cleaner mental model, no two-mode UX | Locked v2.0 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-17 — Phase 08 (Lidarr Discovery + Polish) complete. All 5 plans landed: Lidarr client + sync cron + schema bootstrap; LB/MB discovery pipeline via Sunday cron tick; vibe-color propagation + home cost chip; `/discover` + library mobile rewrite; `/debug/discovery` + OPS-06 legacy-playlist guard. Milestone v2.0 (Music Companion) is feature-complete — Phase 9 remains optional and cuttable.*

**Phase 07.1 highlights:** Replaced the LLM-on-every-refill suggestions hot path with SQL-driven refill from pre-computed TrackVibe.distance (zero LLM tokens per play). Added weekly LLM discovery cron (Sun 03:00 UTC) that backfills 3-7 fresh picks. Steady-state cost dropped from ~$150/month to ~$0.20/month. New requirements landed: SUGG-12 (SQL refill), SUGG-13 (weekly discovery), SUGG-14 (defensive max_tokens). SUGG-04 reworked. Plus quick task 260516-gym: weekly Plex Suggestions playlist prune aligned with the Sunday cron so playlists reset to ~30 fresh tracks weekly. 3 NAS-UAT gaps surfaced and closed in-phase (GAP-01 prompt 200K, GAP-02 htmx polling loop, GAP-03 PlexAPI int-cast). Remaining HUMAN-UAT items #1/#3/#4 will be verified Sunday 2026-05-17 post-cron-tick; any findings will be folded into bug-fix quick tasks.
