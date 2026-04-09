# Roadmap: Composer

## Overview

Composer delivers mood-based playlist generation from a fully self-hosted music library. The build starts with a deployable Docker image published via CI/CD -- from day one the user can pull from Docker Hub and `docker compose up` on their NAS, iterating by rebuilding as features land. With the container and settings page working, we sync the Plex library, extract audio features with Essentia, then build the core playlist generation engine. Once the pipeline works end-to-end, we layer on existing playlist analysis, history browsing, and artist discovery via Lidarr.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation, Configuration & Deployment** - Docker container, CI/CD to Docker Hub, settings page, security patterns, Plex connection (completed 2026-04-09)
- [x] **Phase 2: Library Sync** - Full Plex music library synced to local SQLite with delta updates (completed 2026-04-09)
- [x] **Phase 3: Audio Feature Extraction** - Essentia analyzes local audio files for energy, tempo, danceability, valence (completed 2026-04-09)
- [ ] **Phase 4: Playlist Generation** - Mood-to-playlist pipeline: natural language in, curated playlist out, pushed to Plex
- [ ] **Phase 5: Playlist Management & History** - Browse existing Plex playlists, analyze mood profiles, view generation history
- [ ] **Phase 6: Artist Discovery** - Lidarr artist recommendations with one-click add

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
  4. Extracted features (energy, tempo, danceability, valence) are cached permanently in SQLite -- each track analyzed only once
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
  3. User can review the generated playlist and edit it -- adding, removing, and reordering tracks before finalizing
  4. User can push the finalized playlist to a specific Plex library as a named playlist
  5. The Ollama LLM interprets mood descriptions into structured audio feature criteria that drive track scoring
**Plans**: TBD
**UI hint**: yes

### Phase 5: Playlist Management & History
**Goal**: User can browse and analyze existing Plex playlists and review all previously generated playlists
**Depends on**: Phase 4
**Requirements**: PLEX-01, PLEX-02, PLEX-03, HIST-01, HIST-02
**Success Criteria** (what must be TRUE):
  1. User can view all existing playlists from their Plex server in the app
  2. User can select a Plex playlist and see its mood/energy profile based on track audio features
  3. App suggests new tracks from the library that fit an existing playlist's mood profile
  4. User can browse a history of all previously generated playlists with the mood description and parameters used
**Plans**: TBD
**UI hint**: yes

### Phase 6: Artist Discovery
**Goal**: User gets artist recommendations based on their library and can add them to Lidarr with one click
**Depends on**: Phase 4
**Requirements**: DISC-01, DISC-02
**Success Criteria** (what must be TRUE):
  1. User sees artist recommendations derived from their library analysis and LLM knowledge
  2. User can one-click add a recommended artist to Lidarr with the configured quality profile
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6
Note: Phases 5 and 6 both depend on Phase 4 and could execute in parallel.

| Phase | Plans Complete | Status | Completed |
|-------|---------------|--------|-----------|
| 1. Foundation, Configuration & Deployment | 3/3 | Complete    | 2026-04-09 |
| 2. Library Sync | 3/3 | Complete    | 2026-04-09 |
| 3. Audio Feature Extraction | 3/3 | Complete    | 2026-04-09 |
| 4. Playlist Generation | 0/TBD | Not started | - |
| 5. Playlist Management & History | 0/TBD | Not started | - |
| 6. Artist Discovery | 0/TBD | Not started | - |
