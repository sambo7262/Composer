# Phase 3: Audio Feature Extraction - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Extract audio features from local music files using Essentia. Background job with progress tracking, stop/resume, permanent caching in SQLite. Tracks without features fall back to genre/year/artist metadata for mood matching. This phase provides the audio feature data that Phase 4's playlist generation engine scores against.

</domain>

<decisions>
## Implementation Decisions

### Analysis trigger & flow
- **D-01:** Auto-analyze un-analyzed tracks after each library sync completes
- **D-02:** Manual "Analyze Library" button to force analysis of all un-analyzed tracks
- **D-03:** Stop and resume capability — user can pause analysis, resume later, survives container restarts
- **D-04:** Already-analyzed tracks are never re-analyzed (cached permanently in SQLite)

### Expanded feature set
- **D-05:** Extract expanded features beyond the basic 4: energy, tempo/BPM, danceability, valence, key, scale (major/minor), spectral complexity
- **D-06:** These features give the LLM more filtering power — e.g., "harmonic" can map to major key + moderate complexity
- **D-07:** Features that Essentia cannot extract (vocals, genre, instrument ID) are handled by the LLM using its knowledge of artists + Plex genre metadata

### Progress & visibility
- **D-08:** Inline banner on library page for quick status: "Analyzing... 2,450 / 10,234 tracks (~3h remaining)"
- **D-09:** Expandable details section below banner for per-track status and error log
- **D-10:** HTMX polling for progress updates (same pattern as sync banner from Phase 2)
- **D-11:** Estimated time remaining based on average processing speed

### File path mapping
- **D-12:** During library sync (Phase 2), grab each track's file path from Plex API (`track.media[0].parts[0].file`) and store it on the Track record
- **D-13:** Remap Plex's file path to the container's `/music` mount point for Essentia to read
- **D-14:** User's music directory is already mounted as `/music:ro` in docker-compose (from Phase 1 D-11)

### Metadata fallback
- **D-15:** Tracks that fail analysis or haven't been analyzed yet use genre/year/artist as mood proxy (AUDIO-03)
- **D-16:** The fallback is transparent to the playlist generator — it gets a feature vector either way (Essentia features or metadata-derived approximation)

### Claude's Discretion
- Essentia model/algorithm selection for each feature type
- Batch size and concurrency for analysis (balance CPU load vs speed)
- Exact progress estimation algorithm
- Error handling for corrupted or unsupported audio files
- How to handle tracks where Plex file path doesn't exist on disk (skip + log)
- Docker image size impact of Essentia (multi-stage build considerations)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above and in:
- `.planning/REQUIREMENTS.md` — AUDIO-01 through AUDIO-04
- `.planning/research/STACK.md` — Essentia library recommendation
- `.planning/research/ARCHITECTURE.md` — Audio Analyzer component design
- `.planning/research/PITFALLS.md` — Essentia Docker build complexity, analysis blocking if synchronous

### Existing code (from Phase 1 & 2)
- `app/models/track.py` — Track model (needs file_path field + audio feature fields added)
- `app/services/sync_service.py` — Sync patterns (background job, progress tracking, stop/resume model)
- `app/services/plex_client.py` — Plex client (needs to extract file path during sync)
- `app/templates/partials/sync_banner.html` — HTMX progress banner pattern to reuse
- `docker-compose.yml` — `/music:ro` volume mount already configured
- `Dockerfile` — Multi-stage build (Essentia adds C++ dependencies)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `sync_service.py`: SyncStatus/SyncStateEnum pattern — reuse for AnalysisStatus with same state machine
- `sync_banner.html`: HTMX polling progress banner — clone and adapt for analysis progress
- `api_sync.py`: Start/status endpoint pattern — reuse for analysis trigger and progress endpoints
- Background task pattern via `asyncio.create_task()` — established in Phase 2

### Established Patterns
- `asyncio.to_thread()` for CPU-bound work (Essentia analysis is CPU-bound like PlexAPI calls)
- SQLite WAL mode for concurrent reads during long-running writes
- Progress tracking via in-memory dataclass + HTMX polling

### Integration Points
- `Track` model — add `file_path` field (populated during sync) + audio feature columns
- `plex_client.py` — extend `get_library_tracks` to also extract file path from PlexAPI track objects
- `sync_service.py` — trigger analysis after sync completes (D-01)
- Library page — add analysis banner and details section
- `Dockerfile` — add Essentia installation (C++ library, significant image size increase)
- `requirements.txt` — add essentia package

</code_context>

<specifics>
## Specific Ideas

- First run is a "set it and forget it" job — kick it off before bed, ~5-6 hours for 10k tracks
- After initial analysis, incremental analysis is fast (just new tracks from sync)
- Stop/resume is important for NAS users who don't want continuous CPU load
- The expanded feature set (key, scale, spectral complexity) was chosen to give the LLM more to work with for nuanced prompts like "harmonic rock" or "dark moody electronic"

</specifics>

<deferred>
## Deferred Ideas

- Vocal detection (male/female/instrumental) — would require specialized ML model, defer to v2+
- Genre classification via Essentia — unreliable, Plex genre tags are better
- Re-analysis capability (force re-analyze specific tracks) — defer unless needed

</deferred>

---

*Phase: 03-audio-feature-extraction*
*Context gathered: 2026-04-09*
