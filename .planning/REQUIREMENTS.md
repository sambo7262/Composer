# Requirements: Composer

**Defined:** 2026-04-09
**Core Value:** Turn a vibe description into a curated playlist from your own library — intelligently, without manual curation.

## v1 Requirements

Requirements for initial release. Fully self-hosted — uses only local services (Plex, Lidarr, Ollama) already on the NAS. No external API dependencies (Spotify, cloud LLMs).

### Configuration

- [ ] **CONF-01**: User can configure Plex server URL and token via in-app settings page
- [ ] **CONF-02**: User can configure Ollama endpoint URL and model selection via in-app settings page
- [ ] **CONF-03**: User can configure Lidarr URL, API key, and default quality profile via in-app settings page
- [ ] **CONF-04**: Settings are stored securely and never displayed in UI or exposed in API responses after initial entry
- [ ] **CONF-05**: App deploys as a single Docker container with a compose YAML file (includes Ollama service)
- [ ] **CONF-06**: Plex media directory is mounted as a read-only volume for Essentia audio analysis

### Library Sync

- [ ] **SYNC-01**: App syncs user's full Plex music library to a local SQLite database with paginated fetching
- [ ] **SYNC-02**: After initial sync, app performs delta sync — only importing new or changed tracks
- [ ] **SYNC-03**: Library sync runs as a background job with progress indicator visible in the UI
- [ ] **SYNC-04**: Sync captures track metadata: title, artist, album, genre, year, duration, Plex ratingKey

### Audio Features

- [ ] **AUDIO-01**: App extracts audio features (energy, tempo, danceability, valence) from local audio files using Essentia
- [ ] **AUDIO-02**: Audio feature extraction runs as a background job with progress tracking and resume capability
- [ ] **AUDIO-03**: When no audio features are available for a track, the app falls back to genre/year/artist as mood proxy
- [ ] **AUDIO-04**: Extracted features are cached permanently in SQLite — each track only analyzed once

### Playlist Generation

- [ ] **PLAY-01**: User can describe a mood or vibe in natural language and receive a playlist of matching tracks
- [ ] **PLAY-02**: Ollama LLM interprets the user's mood description into structured audio feature criteria (energy, tempo, valence, danceability ranges)
- [ ] **PLAY-03**: App scores library tracks against mood criteria using weighted distance and returns best matches
- [ ] **PLAY-04**: User can specify how many tracks to include in a generated playlist
- [ ] **PLAY-05**: User can review a generated playlist and edit it (add, remove, reorder tracks) before pushing to Plex
- [ ] **PLAY-06**: User can push a finalized playlist to a specific Plex library as a named playlist

### Plex Playlists

- [ ] **PLEX-01**: User can view all existing playlists stored in their Plex server
- [ ] **PLEX-02**: App can analyze an existing Plex playlist and display its mood/energy profile based on track features
- [ ] **PLEX-03**: App can suggest new tracks from the library that would fit an existing playlist's mood profile

### History

- [ ] **HIST-01**: App stores a history of all generated playlists with the mood description and parameters used
- [ ] **HIST-02**: User can browse and view previously generated playlists in the UI

### Artist Discovery

- [ ] **DISC-01**: App recommends artists similar to what's in the user's library (using library analysis and LLM knowledge)
- [ ] **DISC-02**: User can one-click add a recommended artist to Lidarr with the configured quality profile

### Deployment

- [ ] **DEPL-01**: Docker image is built via GitHub Actions CI/CD pipeline
- [ ] **DEPL-02**: Docker image is published to Docker Hub for easy pulling into compose stacks

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Spotify Integration

- **SPOT-01**: Optionally configure Spotify client credentials for enhanced audio features
- **SPOT-02**: Pull Spotify audio features as alternative/supplement to Essentia
- **SPOT-03**: Use Spotify recommendations API for enhanced artist discovery

### Cloud LLM Providers

- **PROV-01**: Support cloud LLM providers (OpenAI, Anthropic) in addition to Ollama
- **PROV-02**: Configurable default LLM provider with per-request override

### Enhanced Generation

- **GEN2-01**: Seed track mode — "20 tracks like this one"
- **GEN2-02**: Mood-based radio mode (continuous generation)
- **GEN2-03**: Playlist scheduling/automation

### Analytics

- **ANAL-01**: Library statistics dashboard (genre distribution, energy spread, etc.)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Multi-user / authentication | Single-user personal tool — unnecessary complexity |
| Mobile app | Web-only; Plexamp handles mobile playback |
| In-app music playback | Plex/Plexamp handles playback |
| Manual audio fingerprinting | Essentia handles feature extraction from files |
| Automatic artist downloading | Lidarr handles downloads after artist is added to wanted list |
| Real-time streaming integration | Out of scope for playlist generation tool |
| Social/sharing features | Single-user tool |
| Spotify integration in v1 | External dependency; deferred to v2 |
| Cloud LLM providers in v1 | External dependency; Ollama runs locally; cloud deferred to v2 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONF-01 | — | Pending |
| CONF-02 | — | Pending |
| CONF-03 | — | Pending |
| CONF-04 | — | Pending |
| CONF-05 | — | Pending |
| CONF-06 | — | Pending |
| SYNC-01 | — | Pending |
| SYNC-02 | — | Pending |
| SYNC-03 | — | Pending |
| SYNC-04 | — | Pending |
| AUDIO-01 | — | Pending |
| AUDIO-02 | — | Pending |
| AUDIO-03 | — | Pending |
| AUDIO-04 | — | Pending |
| PLAY-01 | — | Pending |
| PLAY-02 | — | Pending |
| PLAY-03 | — | Pending |
| PLAY-04 | — | Pending |
| PLAY-05 | — | Pending |
| PLAY-06 | — | Pending |
| PLEX-01 | — | Pending |
| PLEX-02 | — | Pending |
| PLEX-03 | — | Pending |
| HIST-01 | — | Pending |
| HIST-02 | — | Pending |
| DISC-01 | — | Pending |
| DISC-02 | — | Pending |
| DEPL-01 | — | Pending |
| DEPL-02 | — | Pending |

**Coverage:**
- v1 requirements: 29 total
- Mapped to phases: 0
- Unmapped: 29 ⚠️

---
*Requirements defined: 2026-04-09*
*Last updated: 2026-04-09 after restoring Lidarr to v1 (local service)*
