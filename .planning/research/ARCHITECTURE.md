# Architecture Research

**Domain:** Self-hosted mood-based playlist generation with Plex/Lidarr/Spotify integration
**Researched:** 2026-04-09
**Confidence:** MEDIUM-HIGH

## Critical Discovery: Spotify Audio Features API Deprecated

As of November 27, 2024, Spotify deprecated the `/audio-features` endpoint for new applications. New developer apps receive 403 errors. Only apps that had extended quota mode before the cutoff retain access. In February 2026, Spotify further restricted the API by requiring Premium subscriptions for developer mode and reducing search result limits from 50 to 10.

**Architectural implication:** The system MUST be designed with a pluggable audio features provider. The primary strategy should be:
1. **Essentia** (self-hosted, open-source C++ library with Python bindings) for local audio analysis -- extracts tempo, energy, danceability, and other features directly from audio files.
2. **Spotify Search API** for metadata matching only (track identification via ISRC/title+artist) -- still available but with reduced limits.
3. If the user happens to have a grandfathered Spotify app, support it as an optional secondary source.

This changes the architecture significantly from the original assumption of "match tracks to Spotify, pull features." Instead: "analyze local files with Essentia, use Spotify only for metadata enrichment where useful."

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (SPA)                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │  Mood    │  │ Playlist │  │ Library  │  │  Settings    │    │
│  │  Input   │  │ Manager  │  │ Browser  │  │  / Config    │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
├───────┴──────────────┴──────────────┴──────────────┴────────────┤
│                        REST API Layer                            │
├─────────────────────────────────────────────────────────────────┤
│                       Application Core                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Mood         │  │ Playlist     │  │ Recommendation       │   │
│  │ Interpreter  │  │ Generator    │  │ Engine               │   │
│  │ (LLM)       │  │              │  │ (Artist suggestions) │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘   │
├─────────┴──────────────────┴────────────────────┴───────────────┤
│                     Integration Services                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │  Plex    │  │ Spotify  │  │ Lidarr   │  │  Audio       │    │
│  │  Client  │  │ Client   │  │ Client   │  │  Analyzer    │    │
│  │          │  │(metadata)│  │          │  │  (Essentia)  │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
├───────┴──────────────┴──────────────┴──────────────┴────────────┤
│                        Data Layer                                │
│  ┌──────────────────────┐  ┌─────────────────────────────┐      │
│  │  SQLite Database     │  │  Audio Feature Cache        │      │
│  │  (tracks, playlists, │  │  (computed features per     │      │
│  │   config, history)   │  │   track, avoids re-analysis)│      │
│  └──────────────────────┘  └─────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
         │              │              │              │
    ┌────┴────┐   ┌────┴────┐   ┌────┴────┐   ┌────┴────────┐
    │  Plex   │   │ Spotify │   │ Lidarr  │   │ Local Audio  │
    │ Server  │   │   API   │   │ Server  │   │ Files (Plex  │
    │         │   │         │   │         │   │ media dir)   │
    └─────────┘   └─────────┘   └─────────┘   └──────────────┘
```

## Component Responsibilities

| Component | Responsibility | Implementation |
|-----------|----------------|----------------|
| **Frontend SPA** | UI for mood input, playlist management, library browsing, settings | React or Svelte; served by backend |
| **REST API Layer** | HTTP endpoints, request validation, auth-free (single user) | Backend framework routes |
| **Mood Interpreter** | Converts natural language mood descriptions into numeric feature targets (energy, tempo, valence, danceability ranges) | LLM API call (OpenAI/Anthropic/local) with structured output |
| **Playlist Generator** | Filters library tracks by feature similarity to mood targets, ranks and selects tracks | Vector distance / weighted scoring against audio features DB |
| **Recommendation Engine** | Suggests artists similar to user's library for Lidarr addition | LLM-based or Last.fm similar artists API |
| **Plex Client** | Syncs track library, reads existing playlists, creates/updates playlists | REST calls to Plex server using token auth |
| **Spotify Client** | Metadata enrichment: matches local tracks to Spotify catalog for genre/popularity data | Spotify Search API (still available, reduced limits) |
| **Lidarr Client** | Looks up artists, adds to wanted list with quality profile | Lidarr REST API with API key |
| **Audio Analyzer** | Extracts audio features (tempo, energy, danceability, valence, loudness) from local files | Essentia Python library, runs as background job |
| **SQLite Database** | Persistent storage for tracks, features, playlists, history, config | Single file DB, no external service needed |

## Recommended Project Structure

```
composer/
├── backend/
│   ├── api/
│   │   ├── routes/
│   │   │   ├── mood.py          # POST /mood/interpret, POST /mood/generate
│   │   │   ├── playlists.py     # CRUD for playlists, push to Plex
│   │   │   ├── library.py       # GET library tracks, trigger sync
│   │   │   ├── recommendations.py # Artist suggestions, Lidarr add
│   │   │   └── settings.py      # API key config, sync triggers
│   │   └── middleware.py        # Error handling, logging
│   ├── services/
│   │   ├── mood_interpreter.py  # LLM interaction for mood → features
│   │   ├── playlist_generator.py # Feature matching + track selection
│   │   ├── recommendation.py    # Artist recommendation logic
│   │   └── library_sync.py     # Orchestrates Plex sync + analysis
│   ├── integrations/
│   │   ├── plex_client.py       # All Plex API interactions
│   │   ├── spotify_client.py    # Spotify search/metadata only
│   │   ├── lidarr_client.py     # Lidarr API interactions
│   │   └── audio_analyzer.py    # Essentia wrapper for feature extraction
│   ├── models/
│   │   ├── database.py          # SQLite schema, migrations
│   │   ├── track.py             # Track model with audio features
│   │   ├── playlist.py          # Playlist model with history
│   │   └── config.py            # Encrypted config storage
│   ├── jobs/
│   │   ├── sync_library.py      # Background: full Plex library sync
│   │   ├── analyze_tracks.py    # Background: Essentia feature extraction
│   │   └── scheduler.py        # Job scheduling (periodic sync)
│   └── main.py                 # App entry point
├── frontend/
│   ├── src/
│   │   ├── pages/              # Mood input, playlist view, library, settings
│   │   ├── components/         # Reusable UI components
│   │   └── api/                # API client functions
│   └── ...
├── Dockerfile
├── docker-compose.yml
└── README.md
```

### Structure Rationale

- **backend/integrations/:** Isolates all external API calls behind clean interfaces. If Spotify changes again or Plex updates their API, changes are contained to one file.
- **backend/services/:** Business logic separated from API routes and integrations. The playlist generator doesn't know about Plex -- it works with tracks and features.
- **backend/jobs/:** Background processing is critical. Library sync (10k+ tracks) and audio analysis (CPU-intensive) must not block the API.
- **backend/models/:** Single source of truth for data shapes and DB access.

## Architectural Patterns

### Pattern 1: Service Layer Abstraction for External APIs

**What:** Each external service (Plex, Spotify, Lidarr, Essentia) gets a client class that exposes domain-specific methods, hiding API details.
**When to use:** Always. These integrations are the most volatile part of the system.
**Trade-offs:** Slight over-engineering for a personal tool, but essential given Spotify's API instability.

```python
class PlexClient:
    def __init__(self, server_url: str, token: str):
        self._base_url = server_url
        self._token = token

    async def get_all_tracks(self, library_section: int) -> list[PlexTrack]:
        """Paginated fetch of all tracks from a Plex music library."""
        ...

    async def create_playlist(self, name: str, track_ids: list[str]) -> str:
        """Create playlist in Plex, return playlist ID."""
        ...

    async def update_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        """Replace playlist contents."""
        ...
```

### Pattern 2: Background Job Queue for Heavy Operations

**What:** Library sync and audio analysis run as background tasks with progress tracking, not blocking the HTTP request/response cycle.
**When to use:** For Plex library sync (10k+ tracks = many paginated API calls) and Essentia analysis (CPU-bound per track).
**Trade-offs:** Adds complexity (job state tracking, progress reporting) but necessary -- a full library analysis could take hours on first run.

```python
# Simple in-process job tracking (no need for Celery/Redis in single-container)
class JobManager:
    def __init__(self):
        self._jobs: dict[str, JobStatus] = {}

    async def start_job(self, job_id: str, coroutine) -> None:
        self._jobs[job_id] = JobStatus(state="running", progress=0)
        asyncio.create_task(self._run(job_id, coroutine))

    async def get_status(self, job_id: str) -> JobStatus:
        return self._jobs.get(job_id)
```

### Pattern 3: Feature Vector Matching for Playlist Generation

**What:** Each track has a feature vector (energy, tempo_normalized, valence, danceability, loudness_normalized, acousticness). Mood interpretation produces a target vector + tolerances. Playlist generation is nearest-neighbor search in feature space.
**When to use:** Core algorithm for mood-to-playlist matching.
**Trade-offs:** Simple and effective for 10k tracks (no need for ANN/FAISS). Linear scan with numpy is fast enough.

```python
import numpy as np

def generate_playlist(
    tracks: list[Track],
    target: FeatureVector,
    tolerances: FeatureVector,
    count: int
) -> list[Track]:
    """Score tracks by distance to target, return top N."""
    features = np.array([t.feature_vector for t in tracks])
    target_arr = np.array(target.as_list())
    tolerance_arr = np.array(tolerances.as_list())

    # Weighted euclidean distance (tighter tolerance = higher weight)
    weights = 1.0 / (tolerance_arr + 0.01)
    distances = np.sqrt(np.sum(weights * (features - target_arr) ** 2, axis=1))

    top_indices = np.argsort(distances)[:count]
    return [tracks[i] for i in top_indices]
```

## Data Flow

### Flow 1: Library Sync + Audio Analysis (Background)

```
User triggers "Sync Library"
    │
    ▼
Plex Client ──paginated fetch──▶ Plex Server
    │                              (GET /library/sections/{id}/all)
    │                              (X-Plex-Container-Size for paging)
    ▼
Track records upserted into SQLite
    │ (artist, album, title, file_path, plex_rating_key)
    ▼
For each track WITHOUT audio features:
    │
    ▼
Audio Analyzer (Essentia) reads file from Plex media directory
    │ (requires Docker volume mount to media files)
    ▼
Extracted features stored in SQLite
    (energy, tempo, valence, danceability, acousticness, loudness)
    │
    ▼
[Optional] Spotify Client matches track → enriches with genre, popularity
    (Search API: artist + title, limited to 10 results per query)
```

### Flow 2: Mood-Based Playlist Generation (Interactive)

```
User enters: "chill Sunday morning acoustic vibes"
    │
    ▼
Mood Interpreter (LLM API call)
    │  Prompt: "Convert this mood description to audio feature targets..."
    │  Output: { energy: 0.2-0.4, tempo: 70-100, valence: 0.4-0.7,
    │            danceability: 0.2-0.4, acousticness: 0.7-1.0 }
    ▼
Playlist Generator queries SQLite for all tracks with features
    │  Computes distance from each track to target vector
    │  Selects top N tracks (user-specified count)
    ▼
Returns ranked track list to frontend
    │  User reviews, reorders, adds/removes
    ▼
User clicks "Push to Plex"
    │
    ▼
Plex Client creates playlist
    (POST /playlists with track rating keys)
    │
    ▼
Playlist saved to history in SQLite
```

### Flow 3: Artist Recommendation + Lidarr Add

```
Recommendation Engine analyzes user's library
    │  (genres, artists, listening patterns from Plex)
    ▼
LLM or Last.fm API suggests similar artists NOT in library
    │
    ▼
User sees suggestions in UI
    │  Clicks "Add to Lidarr" on an artist
    ▼
Lidarr Client:
    1. POST /api/v1/artist/lookup?term={artist_name}
    2. POST /api/v1/artist with quality_profile_id, root_folder, monitored=true
    ▼
Lidarr handles search + download automatically
```

### Flow 4: New Track Playlist Suggestions

```
Periodic sync detects new tracks in Plex
    │
    ▼
Audio Analyzer extracts features for new tracks
    │
    ▼
For each existing playlist in history:
    │  Compute average feature vector of playlist
    │  Compare new track features to playlist centroid
    ▼
If distance < threshold: suggest "Track X fits Playlist Y"
    │
    ▼
User reviews suggestions, accepts/rejects
    │  Accept → Plex Client adds track to playlist
```

### State Management

```
SQLite (source of truth)
    ▼ (API queries)
Backend Services ──JSON──▶ Frontend
    ▼ (user actions)
Frontend ──API calls──▶ Backend ──writes──▶ SQLite
                                  ──calls──▶ External Services
```

No complex state management needed on the frontend. Server is the authority. Frontend fetches and displays.

## Scaling Considerations

This is a single-user, self-hosted app. Traditional scaling is irrelevant. What matters:

| Concern | At 10k tracks | At 50k tracks | At 200k tracks |
|---------|---------------|---------------|----------------|
| Library sync | ~2 min (paginated Plex API) | ~10 min | ~40 min, consider incremental sync |
| Audio analysis (Essentia) | ~3-8 hours first run (1-3s per track) | ~15-40 hours | Needs incremental + parallelized |
| Feature matching | <100ms (numpy in-memory) | <500ms | Consider pre-computed indices |
| SQLite storage | ~5MB | ~25MB | ~100MB, still fine for SQLite |
| Spotify metadata enrichment | ~17 min (10 results/query, rate limits) | Impractical without caching | Not viable; make optional |

### Scaling Priorities

1. **First bottleneck: Initial audio analysis.** Essentia processing 10k tracks takes hours. Must be a background job with progress tracking and resume-on-restart capability. Process incrementally (only new/changed tracks).
2. **Second bottleneck: Plex API pagination.** 10k tracks across paginated requests is slow. Cache aggressively, do incremental syncs (check Plex library update timestamp).

## Anti-Patterns

### Anti-Pattern 1: Blocking API on Analysis

**What people do:** Run Essentia analysis synchronously in an API request handler.
**Why it's wrong:** A single track analysis takes 1-3 seconds. 10k tracks = hours. The UI would be completely unresponsive.
**Do this instead:** Background job with progress API endpoint. Frontend polls for status.

### Anti-Pattern 2: Depending on Spotify Audio Features as Primary Source

**What people do:** Design the entire system around Spotify's audio features endpoint.
**Why it's wrong:** Endpoint is deprecated for new apps (Nov 2024). Even grandfathered apps face increasing restrictions (Feb 2026: Premium required, reduced limits).
**Do this instead:** Use Essentia for audio features (self-hosted, no API dependency). Use Spotify Search only for optional metadata enrichment (genre, popularity).

### Anti-Pattern 3: Multi-Container Architecture

**What people do:** Split into separate containers for API, worker, database, frontend.
**Why it's wrong:** Constraint is single Docker container alongside existing arr stack. Over-engineering for a single-user tool.
**Do this instead:** Monolith with in-process async job queue. SQLite instead of PostgreSQL. Frontend served by the backend process.

### Anti-Pattern 4: Raw LLM Output as Feature Filters

**What people do:** Send LLM output directly as database query parameters without validation.
**Why it's wrong:** LLM outputs are unpredictable. Could produce out-of-range values, wrong field names, or hallucinated parameters.
**Do this instead:** Define a strict schema (Pydantic model) for feature targets. Validate and clamp LLM output. Use structured output / function calling if the LLM supports it.

### Anti-Pattern 5: Full Library Re-sync Every Time

**What people do:** Fetch all 10k+ tracks from Plex on every sync.
**Why it's wrong:** Wastes time and hammers the Plex server.
**Do this instead:** Store Plex library `updatedAt` timestamp. On sync, check if library has changed. If so, fetch only updated items (Plex supports `updatedAt` filtering on library sections).

## Integration Points

### External Services

| Service | Integration Pattern | Key Endpoints | Gotchas |
|---------|---------------------|---------------|---------|
| **Plex Server** | REST API with `X-Plex-Token` header | `GET /library/sections/{id}/all` (tracks), `POST /playlists` (create), `PUT /playlists/{id}/items` (update) | Pagination mandatory for large libraries (`X-Plex-Container-Size/Start`). Token is long-lived. |
| **Spotify API** | OAuth2 client credentials flow | `GET /search?type=track` (match tracks) | Audio features DEPRECATED. Search limit reduced to 10 results. Rate limit: ~180 req/min. Premium required for dev mode (Mar 2026+). |
| **Lidarr** | REST API with `X-Api-Key` header | `GET /api/v1/artist/lookup` (search), `POST /api/v1/artist` (add), `GET /api/v1/qualityprofile` (list profiles) | Must include `rootFolderPath`, `qualityProfileId`, `metadataProfileId` when adding artist. |
| **LLM Provider** | REST API (OpenAI-compatible) | `POST /chat/completions` with structured output | Latency 1-5s per call. Use structured output / function calling for reliable feature vector extraction. Consider supporting local models (Ollama) for privacy. |
| **Essentia** | Python library (in-process) | `essentia.standard.MusicExtractor` or individual algorithms | CPU-intensive. Requires access to actual audio files (Docker volume mount to Plex media directory). ~1-3s per track. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Frontend <-> API | HTTP REST + JSON | No WebSocket needed except optionally for job progress |
| API Routes <-> Services | Direct function calls | Same process, no serialization overhead |
| Services <-> Integrations | Async function calls | Integration clients handle retries, rate limiting internally |
| Services <-> Database | SQLAlchemy or raw SQL | Single SQLite connection with WAL mode for concurrent reads |
| API <-> Jobs | In-process asyncio tasks | Job manager tracks state; API exposes status endpoint |

## Docker Volume Architecture

```
docker-compose.yml:
  composer:
    volumes:
      - ./data:/app/data          # SQLite DB, config (persistent)
      - /path/to/music:/music:ro  # Read-only access to audio files for Essentia
    ports:
      - "8080:8080"
```

The read-only mount to the music directory is critical for Essentia analysis. Without access to actual audio files, the self-hosted analysis approach doesn't work. This must be clearly documented in setup instructions.

## Build Order (Dependency Chain)

Components should be built in this order due to dependencies:

```
Phase 1: Foundation
  ├── SQLite schema + models
  ├── Backend framework + API skeleton
  ├── Plex Client (library sync)
  └── Frontend shell (settings page for API keys)

Phase 2: Core Features
  ├── Audio Analyzer (Essentia integration)  ← requires Phase 1 (tracks in DB)
  ├── Background job system                  ← required by Audio Analyzer
  └── Library browser UI                     ← requires Phase 1 (tracks in DB)

Phase 3: Playlist Generation
  ├── Mood Interpreter (LLM integration)     ← standalone
  ├── Playlist Generator (feature matching)  ← requires Phase 2 (features in DB)
  ├── Playlist management UI                 ← requires generator
  └── Plex playlist push                     ← requires Plex Client from Phase 1

Phase 4: Extended Features
  ├── Spotify metadata enrichment (optional) ← requires tracks in DB
  ├── New track suggestions for playlists    ← requires Phase 3 (playlists exist)
  ├── Artist recommendations                 ← requires library data
  └── Lidarr integration                     ← requires recommendation engine

Phase 5: Polish
  ├── Playlist history + analytics
  ├── Incremental sync optimization
  ├── Docker image + CI/CD
  └── Documentation
```

**Rationale:** You can't generate playlists without audio features. You can't extract features without tracks in the database. You can't have tracks without Plex sync. This chain dictates the build order. Lidarr integration and Spotify enrichment are leaf nodes with no downstream dependencies, so they come last.

## Sources

- [Spotify Web API Changes (Nov 2024)](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [Spotify February 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Spotify Audio Features API (deprecated)](https://developer.spotify.com/documentation/web-api/reference/get-audio-features)
- [Python PlexAPI Documentation](https://python-plexapi.readthedocs.io/en/latest/modules/audio.html)
- [Plex API Playlist Creation](https://www.plexopedia.com/plex-media-server/api/playlists/create/)
- [Plex API Documentation](https://plexapi.dev/api-reference/library/get-all-libraries)
- [Lidarr API (pyarr)](https://docs.totaldebug.uk/pyarr/modules/lidarr.html)
- [Lidarr API Docs](https://lidarr.audio/docs/api/)
- [Essentia Audio Analysis Library](https://essentia.upf.edu/)
- [Essentia GitHub](https://github.com/MTG/essentia)
- [Essentia Music Extractor](https://essentia.upf.edu/streaming_extractor_music.html)
- [Voclr.it: Spotify API Restrictions 2026](https://voclr.it/news/why-spotify-has-restricted-its-api-access-what-changed-and-why-it-matters-in-2026/)

---
*Architecture research for: Composer -- mood-based playlist generation*
*Researched: 2026-04-09*
