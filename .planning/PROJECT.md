# Composer

## What This Is

A self-hosted web app that generates mood-based playlists from your personal music library using Spotify's audio features and AI interpretation. It integrates with Plex/Plexamp for playlist playback and Lidarr for discovering and adding new artists. Runs as a Docker container alongside your existing media stack.

## Core Value

Turn a vibe description into a curated playlist from your own library — intelligently, without manual curation.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] User can describe a mood/vibe in natural language and receive a playlist of matching tracks from their library
- [ ] AI interprets the user's mood description into search criteria, Spotify audio features (energy, tempo, valence, danceability) do the actual filtering
- [ ] User can specify how many tracks to include in a generated playlist
- [ ] User can review, edit (add/remove/reorder tracks), and then push a playlist to a specific Plex library
- [ ] User can browse existing Plex playlists and analyze their contents
- [ ] When new tracks are added to the library, the app suggests slotting them into existing playlists that match their vibe
- [ ] User can view history of all previously generated playlists
- [ ] User gets artist recommendations based on their library — one-click adds artist to Lidarr with configured quality profile
- [ ] App syncs with Plex to know the user's full track library and metadata
- [ ] App matches local library tracks against Spotify's catalog to pull audio features
- [ ] In-app configuration page for API keys (Plex token, Spotify credentials, Lidarr API key/URL, quality profile)
- [ ] API keys are stored securely — never displayed in UI after entry, never exposed in API responses
- [ ] Dockerized with a compose YAML — easy deploy alongside existing arr stack
- [ ] Docker image built via GitHub Actions CI/CD and pushed to Docker Hub
- [ ] Clean, simple UI — functional, no frills

### Out of Scope

- Multi-user / authentication — single user, personal tool
- Mobile app — web-only
- Streaming or playback within the app — Plex/Plexamp handles playback
- Manual audio analysis / fingerprinting — relies on Spotify's existing audio feature data
- Automatic downloading — Lidarr integration adds artists to wanted list, Lidarr handles the rest

## Context

- User has 10k+ tracks managed by Lidarr, served by Plex/Plexamp
- Existing infrastructure is Docker-based (Lidarr and other arr apps in containers)
- Plex is accessible via internal server IP + token
- Lidarr is accessible via API with configurable quality profiles
- Spotify API provides audio features (energy, tempo, danceability, valence, etc.) per track — free developer account required
- The matching pipeline: user describes vibe → AI interprets into feature criteria → Spotify audio features filter the library → playlist presented for editing → pushed to Plex
- Code maintained in GitHub, Docker image built and published via GitHub Actions

## Constraints

- **Deployment**: Must run as a single Docker container with compose YAML — no complex multi-service setup beyond what's needed
- **Security**: API keys must never be exposed in UI or API responses after initial configuration
- **Dependencies**: Requires Plex server, Lidarr instance, and Spotify developer credentials to function
- **Tech stack**: No preference stated — research should determine best fit

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Spotify API for audio features | Best data quality for energy/tempo/mood classification; free dev tier available | — Pending |
| AI + Spotify combined approach | AI interprets natural language moods, Spotify features do quantitative filtering — flexible and accurate | — Pending |
| Single Docker container | Matches existing deployment pattern, simple compose YAML alongside arr stack | — Pending |
| GitHub Actions → Docker Hub | CI/CD pipeline for image builds, standard for self-hosted app distribution | — Pending |
| One-click Lidarr add (not full auto) | User stays in control of what gets added, but friction is minimal | — Pending |

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
*Last updated: 2026-04-09 after initialization*
