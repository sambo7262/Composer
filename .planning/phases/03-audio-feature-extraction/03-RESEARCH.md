# Phase 3: Audio Feature Extraction - Research

**Researched:** 2026-04-09
**Domain:** Audio analysis with Essentia, background job processing, Docker C++ dependency management
**Confidence:** HIGH

## Summary

This phase adds Essentia-based audio feature extraction to Composer. The critical discovery is that Essentia provides pre-built Python wheels for Python 3.12 on Linux x86_64 (13.8MB for base `essentia`, 291MB for `essentia-tensorflow`). This eliminates the feared C++ build complexity -- `pip install essentia` works directly in `python:3.12-slim` with no additional system dependencies for the core algorithms.

The base `essentia` package (no TensorFlow) provides all needed algorithms: energy, danceability (DFA-based, range 0-3), BPM/tempo (RhythmExtractor2013), key/scale detection, spectral complexity, and loudness. For "valence" -- which is a Spotify-specific concept with no direct Essentia equivalent -- the recommended approach is a computed proxy using a weighted combination of mode (major/minor), spectral brightness, danceability, and other low-level features. This avoids the 291MB TensorFlow dependency while providing a functionally equivalent signal for the LLM playlist engine.

**Primary recommendation:** Use `essentia` (not `essentia-tensorflow`) with MusicExtractor for bulk feature extraction, plus individual algorithms for danceability and key. Compute valence as a weighted proxy from extracted features. Process tracks sequentially with `asyncio.to_thread()` to avoid blocking the event loop. Target ~2 seconds per track, ~5.5 hours for 10k tracks on first run.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Auto-analyze un-analyzed tracks after each library sync completes
- **D-02:** Manual "Analyze Library" button to force analysis of all un-analyzed tracks
- **D-03:** Stop and resume capability -- user can pause analysis, resume later, survives container restarts
- **D-04:** Already-analyzed tracks are never re-analyzed (cached permanently in SQLite)
- **D-05:** Extract expanded features: energy, tempo/BPM, danceability, valence, key, scale (major/minor), spectral complexity
- **D-06:** These features give the LLM more filtering power
- **D-07:** Features Essentia cannot extract (vocals, genre, instrument ID) handled by LLM + Plex metadata
- **D-08:** Inline banner on library page for quick status
- **D-09:** Expandable details section below banner for per-track status and error log
- **D-10:** HTMX polling for progress updates (same pattern as sync banner)
- **D-11:** Estimated time remaining based on average processing speed
- **D-12:** During library sync, grab each track's file path from Plex API and store it on Track record
- **D-13:** Remap Plex's file path to the container's /music mount point
- **D-14:** User's music directory is already mounted as /music:ro in docker-compose
- **D-15:** Tracks that fail analysis or haven't been analyzed use genre/year/artist as mood proxy
- **D-16:** Fallback is transparent to playlist generator -- feature vector either way

### Claude's Discretion
- Essentia model/algorithm selection for each feature type
- Batch size and concurrency for analysis
- Exact progress estimation algorithm
- Error handling for corrupted/unsupported audio files
- How to handle tracks where Plex file path doesn't exist on disk
- Docker image size impact of Essentia (multi-stage build considerations)

### Deferred Ideas (OUT OF SCOPE)
- Vocal detection (male/female/instrumental)
- Genre classification via Essentia
- Re-analysis capability (force re-analyze specific tracks)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AUDIO-01 | Extract audio features (energy, tempo, danceability, valence) from local audio files using Essentia | MusicExtractor + individual algorithms; valence via weighted proxy; see Standard Stack and Architecture Patterns |
| AUDIO-02 | Audio feature extraction runs as background job with progress tracking and resume capability | Reuse SyncStatus/SyncStateEnum pattern from sync_service.py; AnalysisState persisted to SQLite for resume; see Architecture Patterns |
| AUDIO-03 | When no audio features available, fall back to genre/year/artist as mood proxy | Metadata-derived feature vector with heuristic mappings; see Architecture Patterns Pattern 3 |
| AUDIO-04 | Extracted features cached permanently in SQLite -- each track only analyzed once | Track model gains `analyzed_at` timestamp; NULL = not analyzed; see Architecture Patterns |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- Deployment: Single Docker container with compose YAML
- Security: API keys never exposed in UI or API responses after initial configuration
- GSD workflow enforcement: Use GSD commands for planned work
- Stack: Python 3.12 + FastAPI + SQLModel + SQLite + Jinja2/HTMX/Alpine.js/Tailwind CSS
- Local dev uses Python 3.9 with `from __future__ import annotations`; Docker targets Python 3.12

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| essentia | 2.1b6.dev1389 | Audio feature extraction | Pre-built wheel for cp312 Linux x86_64 (13.8MB). No C++ compile needed. Provides energy, tempo, danceability, key, spectral features out of the box. |

**Version verification:** Version 2.1b6.dev1389 published 2025-07-24 on PyPI. Pre-built wheel `essentia-2.1b6.dev1389-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl` (13.8MB) confirmed available. [VERIFIED: pypi.org/project/essentia]

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| essentia (base) | essentia-tensorflow | Adds 291MB wheel + TensorFlow runtime. Enables deep learning valence/arousal models (DEAM dataset). NOT recommended -- proxy valence from base features is sufficient for LLM-driven playlist matching. |
| essentia MusicExtractor | Individual algorithms only | MusicExtractor extracts 150+ features in one pass. More efficient than running algorithms individually, but extracts far more than needed. Use MusicExtractor for the bulk, individual algorithms where MusicExtractor coverage is insufficient. |
| essentia | librosa | Librosa is pure Python, easier to install, but slower and missing danceability/key algorithms. Essentia's C++ core is faster for batch processing. |

### Why NOT essentia-tensorflow

The TensorFlow variant adds ~291MB to the Docker image and pulls in TensorFlow as a dependency. The only feature it unlocks that base essentia lacks is deep-learning-based arousal/valence regression (DEAM models). However:

1. "Valence" in Spotify terms is a measure of musical positivity. It has no universal definition.
2. Essentia's DEAM models output arousal+valence on a [1,9] scale, which is a different scale than Spotify's [0,1].
3. The LLM playlist engine doesn't need precise valence -- it needs a signal it can filter on.
4. A weighted proxy from mode (major=positive), spectral brightness, and danceability provides an adequate signal.
5. Keeping the image small matters for NAS deployment.

[VERIFIED: pypi.org essentia-tensorflow wheel size 291.5MB for cp312 manylinux]

**Installation:**
```bash
pip install essentia
```

No additional system packages needed in `python:3.12-slim` -- the manylinux wheel bundles all native dependencies. [VERIFIED: PyPI lists manylinux_2_17 wheel, which is compatible with python:3.12-slim (Debian bookworm, glibc 2.36)]

## Architecture Patterns

### Recommended Project Structure (additions to existing)

```
app/
├── models/
│   └── track.py            # Add file_path + 8 audio feature columns + analyzed_at
├── services/
│   ├── audio_analyzer.py   # NEW: Essentia wrapper for feature extraction
│   ├── analysis_service.py # NEW: Background job orchestrator (like sync_service.py)
│   ├── plex_client.py      # MODIFY: Extract file path during sync
│   └── sync_service.py     # MODIFY: Trigger analysis after sync completes
├── api/
│   └── api_analysis.py     # NEW: Start/stop/status endpoints
├── templates/partials/
│   └── analysis_banner.html # NEW: Clone of sync_banner.html pattern
```

### Pattern 1: Audio Feature Extraction with Essentia

**What:** Use `essentia.standard.MusicExtractor` for bulk feature extraction, supplemented by individual algorithms where needed.
**When to use:** For every track that has a valid file path and hasn't been analyzed yet.

```python
# Source: essentia.upf.edu/tutorial_extractors_musicextractor.html
import essentia.standard as es

def extract_features(file_path: str) -> dict:
    """Extract audio features from a single track file."""
    features, _ = es.MusicExtractor(
        lowlevelStats=['mean', 'stdev'],
        rhythmStats=['mean', 'stdev'],
        tonalStats=['mean', 'stdev'],
    )(file_path)

    # Direct features from MusicExtractor
    bpm = features['rhythm.bpm']
    key = features['tonal.key_edma.key']
    scale = features['tonal.key_edma.scale']
    danceability = features['rhythm.danceability']
    energy = features['lowlevel.spectral_rms.mean']
    loudness = features['lowlevel.loudness_ebu128.integrated']
    spectral_complexity = features['lowlevel.spectral_complexity.mean']

    # Compute valence proxy (see Pattern 2)
    valence = compute_valence_proxy(
        scale=scale,
        spectral_centroid=features['lowlevel.spectral_centroid.mean'],
        danceability=danceability,
        pitch_salience=features['lowlevel.pitch_salience.mean'],
    )

    return {
        'energy': energy,
        'tempo': bpm,
        'danceability': danceability,
        'valence': valence,
        'key': key,
        'scale': scale,
        'spectral_complexity': spectral_complexity,
        'loudness': loudness,
    }
```

[VERIFIED: MusicExtractor API from essentia.upf.edu/tutorial_extractors_musicextractor.html]

### Pattern 2: Valence Proxy Computation

**What:** Compute a valence-like score (0.0-1.0) from Essentia features since Essentia has no direct "valence" equivalent.
**Why:** Spotify's "valence" (musical positivity) is a proprietary metric. The closest open-source proxy combines mode (major keys are more "positive"), spectral brightness, danceability, and pitch salience.

```python
def compute_valence_proxy(
    scale: str,
    spectral_centroid: float,
    danceability: float,
    pitch_salience: float,
) -> float:
    """Approximate Spotify-style valence from Essentia features.

    Returns a value in [0.0, 1.0] where higher = more positive/happy.

    Weights inspired by Truedat project's mood mapping approach.
    """
    # Mode contribution: major = 1.0, minor = 0.0
    mode_score = 1.0 if scale == "major" else 0.0

    # Normalize danceability from Essentia's [0, ~3] to [0, 1]
    dance_norm = min(danceability / 3.0, 1.0)

    # Spectral centroid: higher = brighter = more positive
    # Typical range ~500-5000 Hz, normalize to [0, 1]
    brightness = min(max((spectral_centroid - 500) / 4500, 0.0), 1.0)

    # Pitch salience: clearer pitch = more melodic = more positive
    salience_norm = min(max(pitch_salience, 0.0), 1.0)

    # Weighted combination
    valence = (
        0.30 * mode_score +
        0.25 * dance_norm +
        0.25 * brightness +
        0.20 * salience_norm
    )
    return round(min(max(valence, 0.0), 1.0), 4)
```

[ASSUMED] The specific weights (0.30, 0.25, 0.25, 0.20) are heuristic. The Truedat project uses a similar approach with adjustable weights. These weights may need tuning after observing real playlist generation results.

### Pattern 3: Metadata Fallback Feature Vector (AUDIO-03)

**What:** When a track has no audio features (not yet analyzed, or analysis failed), generate an approximate feature vector from metadata.
**When to use:** For the playlist generator to have something to score against, so un-analyzed tracks can still appear in playlists.

```python
# Genre-to-feature heuristic mappings
GENRE_ENERGY = {
    "metal": 0.9, "punk": 0.85, "electronic": 0.75, "hip hop": 0.7,
    "rock": 0.65, "pop": 0.6, "r&b": 0.5, "folk": 0.35,
    "classical": 0.3, "ambient": 0.2, "jazz": 0.45,
}

def metadata_feature_vector(genre: str, year: int | None) -> dict:
    """Generate approximate features from metadata when Essentia data unavailable."""
    genre_lower = genre.lower() if genre else ""
    energy = next(
        (v for k, v in GENRE_ENERGY.items() if k in genre_lower),
        0.5  # default middle
    )
    # Era affects valence slightly (80s pop = high valence)
    era_valence_boost = 0.1 if year and 1980 <= year <= 1989 else 0.0

    return {
        'energy': energy,
        'tempo': None,  # Cannot estimate from metadata
        'danceability': energy * 0.8,  # Rough proxy
        'valence': 0.5 + era_valence_boost,
        'key': None, 'scale': None,
        'spectral_complexity': None,
        'loudness': None,
    }
```

[ASSUMED] Genre-to-energy mappings are heuristic and approximate. The LLM in Phase 4 will also use genre/artist knowledge directly, so these don't need to be precise.

### Pattern 4: File Path Remapping (D-12, D-13)

**What:** Extract file paths from PlexAPI track objects and remap them to the container's `/music` mount.

```python
# In plex_client.py _map_track function, add:
def _map_track(t) -> dict:
    # Existing fields...
    file_path = None
    if t.media and t.media[0].parts:
        file_path = t.media[0].parts[0].file
    return {
        # ... existing fields ...
        "file_path": file_path,
    }
```

The Plex server stores absolute paths like `/data/Music/Artist/Album/track.flac`. The container mounts the same directory as `/music:ro`. The remapping strips the Plex prefix and prepends `/music`:

```python
def remap_plex_path(plex_path: str, plex_music_root: str, container_mount: str = "/music") -> str:
    """Remap a Plex file path to the container's mount point.

    Example: /data/Music/Artist/Album/song.flac -> /music/Artist/Album/song.flac
    """
    if plex_path.startswith(plex_music_root):
        relative = plex_path[len(plex_music_root):]
        return container_mount + relative
    return container_mount + "/" + plex_path.split("/")[-1]  # fallback
```

[VERIFIED: PlexAPI Track has `media[0].parts[0].file` attribute -- python-plexapi.readthedocs.io/en/latest/modules/media.html]

**Important:** The `plex_music_root` (the Plex library's root path) must be configurable. It's the path on the Plex server, which differs from the container mount. This should be auto-detected from the first track's path or set via a settings field.

### Pattern 5: Analysis Service (mirrors sync_service.py)

**What:** Background analysis job with the same state machine pattern as SyncService.

```python
class AnalysisStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"      # D-03: user paused
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class AnalysisStatus:
    state: AnalysisStateEnum = AnalysisStateEnum.IDLE
    total_tracks: int = 0
    analyzed_tracks: int = 0
    failed_tracks: int = 0
    current_track: str = ""
    avg_seconds_per_track: float = 0.0
    errors: list[dict] = field(default_factory=list)

    @property
    def eta_seconds(self) -> float:
        remaining = self.total_tracks - self.analyzed_tracks - self.failed_tracks
        return remaining * self.avg_seconds_per_track if self.avg_seconds_per_track > 0 else 0
```

**Resume capability (D-03):** The analysis queries `Track` for records where `analyzed_at IS NULL AND file_path IS NOT NULL`. On pause/restart, it simply re-queries -- already-analyzed tracks are skipped automatically (D-04).

**Pause persistence:** Store `AnalysisStateEnum.PAUSED` in the AnalysisState table so it survives container restarts. On startup, check if state is PAUSED and don't auto-resume unless user clicks resume.

### Anti-Patterns to Avoid

- **Running Essentia in the async event loop:** Essentia is CPU-bound C++ code. MUST use `asyncio.to_thread()` to offload to a worker thread, exactly like the sync_service.py pattern for PlexAPI calls.
- **Loading entire audio file into memory:** Use MusicExtractor which handles file I/O internally via streaming mode. Do NOT load audio with MonoLoader and pass arrays around.
- **Analyzing all tracks in one transaction:** Commit each track's features individually (or in small batches) so progress is saved incrementally. If the container crashes mid-analysis, completed tracks retain their features.
- **Hard-coding the Plex music root path:** Different users have different Plex library paths. Must be configurable or auto-detected.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Audio feature extraction | Custom FFT/signal processing | essentia.standard.MusicExtractor | Battle-tested C++ library used by AcousticBrainz for millions of tracks |
| BPM detection | Peak detection on onset envelope | essentia RhythmExtractor2013 | Multi-method ensemble (degara, multifeature) handles edge cases |
| Key/scale detection | Chroma feature + template matching | essentia Key algorithm (edma profile) | 14 profile types optimized for different genres |
| Danceability scoring | Manual rhythm regularity metric | essentia Danceability (DFA-based) | Detrended Fluctuation Analysis handles tempo variation |
| Background job state machine | Custom state tracker | Copy SyncStatus/SyncStateEnum pattern | Already proven in Phase 2, same requirements |

## Common Pitfalls

### Pitfall 1: Essentia Crashes on Corrupted/Unsupported Files

**What goes wrong:** Essentia's MusicExtractor throws exceptions or segfaults on corrupted audio files, DRM-protected files, or unusual codecs.
**Why it happens:** Real music libraries have edge cases: partial downloads, zero-byte files, DTS-encoded tracks, .m4p (DRM) files.
**How to avoid:** Wrap each track analysis in try/except. Log the error, mark the track as `analysis_failed=True` (or set a specific error message), increment failed_tracks counter, continue to next track. Never let one bad file stop the batch.
**Warning signs:** Analysis stalls on a specific track for >30 seconds, or process memory grows unbounded.

### Pitfall 2: Path Mismatch Between Plex and Container Mount

**What goes wrong:** Plex reports file paths like `/data/Music/Artist/Album/song.flac` but the container has the music mounted at `/music/Artist/Album/song.flac`. Direct use of Plex paths fails with FileNotFoundError.
**Why it happens:** Plex runs on the host (or in its own container) with different mount points than Composer's container.
**How to avoid:** Store both the raw Plex path and implement a configurable path remapping. Auto-detect the common prefix from the first few tracks, or let the user configure the Plex music root in settings.
**Warning signs:** All tracks fail analysis with "file not found" errors.

### Pitfall 3: Blocking the Event Loop During Analysis

**What goes wrong:** Running Essentia analysis directly in an async handler freezes the web UI. FastAPI can't serve progress polling requests while analysis is running.
**Why it happens:** Essentia is CPU-bound C++ code. Python's GIL doesn't help here -- the GIL is released during C++ execution, but `asyncio.to_thread()` is still needed to keep the event loop responsive.
**How to avoid:** Run analysis in `asyncio.to_thread()` exactly like sync_service.py handles PlexAPI calls. The analysis loop should yield back to the event loop between tracks.
**Warning signs:** UI stops updating during analysis, HTMX polling requests time out.

### Pitfall 4: Docker Image Size Bloat

**What goes wrong:** Adding Essentia with TensorFlow bloats the Docker image by 300MB+, making pulls slow on home networks.
**Why it happens:** `essentia-tensorflow` bundles TensorFlow runtime (~280MB extra).
**How to avoid:** Use base `essentia` package only (13.8MB wheel). All needed algorithms (energy, danceability, BPM, key, spectral complexity) are in the base package. Valence is computed as a proxy from these features.
**Warning signs:** Docker image exceeds 500MB, pull times on gigabit NAS LAN exceed 30 seconds.

### Pitfall 5: MusicExtractor Feature Key Names

**What goes wrong:** Accessing wrong feature keys from MusicExtractor's output pool. Keys are undocumented in a single place and vary between versions.
**Why it happens:** MusicExtractor returns 150+ features with dot-notation keys. The exact keys aren't obvious (e.g., `lowlevel.spectral_rms.mean` vs `lowlevel.energy.mean`).
**How to avoid:** Print all pool keys during development to discover exact names. Key features confirmed: `rhythm.bpm`, `rhythm.danceability`, `tonal.key_edma.key`, `tonal.key_edma.scale`, `lowlevel.spectral_rms.mean`, `lowlevel.loudness_ebu128.integrated`, `lowlevel.spectral_complexity.mean`, `lowlevel.spectral_centroid.mean`, `lowlevel.pitch_salience.mean`.
**Warning signs:** KeyError exceptions during feature extraction.

### Pitfall 6: Danceability Scale Mismatch

**What goes wrong:** Essentia's danceability ranges from 0 to ~3, not 0 to 1 like Spotify. Downstream code assumes [0,1] range and produces bad results.
**Why it happens:** Essentia's DFA-based danceability uses a different mathematical basis than Spotify's proprietary model.
**How to avoid:** Normalize to [0,1] by dividing by 3.0 and clamping before storing. All stored features should be on a consistent [0,1] or well-documented scale.
**Warning signs:** Playlist generator treats danceability > 1.0 as invalid or clips it incorrectly.

## Code Examples

### Track Model with Audio Features

```python
# app/models/track.py - additions
class Track(SQLModel, table=True):
    # ... existing fields ...
    file_path: Optional[str] = Field(default=None)

    # Audio features (all nullable -- NULL means not yet analyzed)
    energy: Optional[float] = Field(default=None, index=True)
    tempo: Optional[float] = Field(default=None)
    danceability: Optional[float] = Field(default=None, index=True)
    valence: Optional[float] = Field(default=None, index=True)
    musical_key: Optional[str] = Field(default=None)    # "A" through "G#"
    scale: Optional[str] = Field(default=None)           # "major" or "minor"
    spectral_complexity: Optional[float] = Field(default=None)
    loudness: Optional[float] = Field(default=None)

    analyzed_at: Optional[str] = Field(default=None)     # ISO timestamp, NULL = not analyzed
    analysis_error: Optional[str] = Field(default=None)  # Error message if analysis failed
```

**Note:** Use `musical_key` not `key` to avoid shadowing Python's built-in or SQLModel's primary key convention. Index `energy`, `danceability`, `valence` for WHERE clause filtering in playlist generation (Phase 4).

### Dockerfile Addition

```dockerfile
# In the base stage, after pip install requirements.txt:
# No additional system packages needed -- essentia wheel is manylinux self-contained
# The existing Dockerfile structure works as-is, just add essentia to requirements.txt
```

The pre-built manylinux wheel bundles all native dependencies (FFTW, libyaml, ffmpeg codecs, etc.). No `apt-get install` of C++ libraries is needed. This is the key finding that eliminates the feared build complexity. [VERIFIED: manylinux_2_17 wheels are self-contained by design]

### requirements.txt Addition

```
essentia>=2.1b6.dev1389,<2.2
```

### PlexAPI File Path Extraction

```python
# Source: python-plexapi.readthedocs.io/en/latest/modules/media.html
# PlexAPI Track objects have: track.media[0].parts[0].file
# Or use the convenience property: track.locations -> list[str]

def _map_track(t) -> dict:
    """Map a PlexAPI Track object to a dict with standard field names."""
    # Get file path from media parts
    file_path = None
    try:
        if hasattr(t, 'media') and t.media:
            parts = t.media[0].parts
            if parts:
                file_path = parts[0].file
    except (IndexError, AttributeError):
        pass

    return {
        "plex_rating_key": str(t.ratingKey),
        "title": t.title or "",
        "artist": t.grandparentTitle or "",
        "album": t.parentTitle or "",
        "genre": ", ".join(g.tag for g in (t.genres or [])),
        "year": t.year,
        "duration_ms": t.duration or 0,
        "added_at": t.addedAt.isoformat() if t.addedAt else None,
        "updated_at": t.updatedAt.isoformat() if t.updatedAt else None,
        "file_path": file_path,
    }
```

[VERIFIED: PlexAPI MediaPart.file attribute confirmed at python-plexapi.readthedocs.io]

### Analysis Progress Estimation (D-11)

```python
import time

class AnalysisTracker:
    """Track analysis speed for ETA estimation."""

    def __init__(self):
        self._times: list[float] = []
        self._window = 50  # Rolling average over last 50 tracks

    def record(self, elapsed: float):
        self._times.append(elapsed)
        if len(self._times) > self._window:
            self._times = self._times[-self._window:]

    @property
    def avg_seconds(self) -> float:
        return sum(self._times) / len(self._times) if self._times else 2.0

    def eta_display(self, remaining: int) -> str:
        """Human-readable ETA string."""
        total_secs = int(remaining * self.avg_seconds)
        if total_secs < 60:
            return f"~{total_secs}s remaining"
        if total_secs < 3600:
            return f"~{total_secs // 60}m remaining"
        hours = total_secs // 3600
        mins = (total_secs % 3600) // 60
        return f"~{hours}h {mins}m remaining"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Build essentia from source in Docker | Pre-built manylinux wheels on PyPI | 2025 (2.1b6 releases) | Eliminates C++ build dependencies in Docker |
| Spotify audio-features API | Local analysis with Essentia | Nov 2024 (Spotify API restricted) | Self-hosted is now the only reliable approach for new projects |
| essentia.standard individual algorithms | MusicExtractor batch extraction | Stable since 2.1 | Single call extracts 150+ features, more efficient |
| TensorFlow required for ML features | Base essentia covers most needs | Ongoing | TF only needed for deep learning models (valence regression) |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Valence proxy weights (0.30 mode, 0.25 dance, 0.25 brightness, 0.20 salience) produce usable results | Pattern 2 | LOW -- weights are tunable, LLM compensates for imprecision |
| A2 | MusicExtractor processes one track in ~2 seconds on NAS hardware | Performance estimates | MEDIUM -- could be 1-5s depending on NAS CPU; affects ETA display accuracy |
| A3 | Genre-to-energy mappings in metadata fallback are reasonable | Pattern 3 | LOW -- fallback is explicitly approximate, LLM uses genre directly too |
| A4 | manylinux wheel works in python:3.12-slim without additional apt packages | Standard Stack | LOW -- manylinux wheels are designed to be self-contained; confirmed glibc 2.17 compat |
| A5 | Plex music root path can be auto-detected from first track's file path | Pattern 4 | MEDIUM -- works if all tracks share a common root; edge case with multiple Plex library roots |

## Open Questions

1. **Plex music root path detection**
   - What we know: Plex stores absolute server-side paths like `/data/Music/...`
   - What's unclear: Whether all tracks in a library share the same root path, or if Plex supports multiple root folders per library
   - Recommendation: Auto-detect common prefix from first 10 tracks. If inconsistent, prompt user to configure in settings.

2. **Essentia performance on NAS ARM hardware**
   - What we know: Pre-built wheels are x86_64 only. No aarch64 wheel available.
   - What's unclear: If user's NAS is ARM-based (e.g., some Synology models), essentia won't install from wheel
   - Recommendation: Assume x86_64 (most NAS running Docker are x86). Document as a known limitation.

3. **MusicExtractor memory usage per track**
   - What we know: Essentia documentation says "optimized for low memory usage" and streaming mode reduces memory
   - What's unclear: Exact RSS per MusicExtractor call for a 5-minute track
   - Recommendation: MusicExtractor uses streaming mode internally. Expect ~50-100MB RSS per call. No special memory management needed for sequential processing.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | None (uses pytest defaults, see existing tests/) |
| Quick run command | `python -m pytest tests/ -x -q` |
| Full suite command | `python -m pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AUDIO-01 | Feature extraction produces correct feature dict from audio file | unit | `python -m pytest tests/test_audio_analyzer.py -x` | Wave 0 |
| AUDIO-01 | Valence proxy computation returns [0,1] from feature inputs | unit | `python -m pytest tests/test_audio_analyzer.py::test_valence_proxy -x` | Wave 0 |
| AUDIO-02 | Analysis service starts, tracks progress, can be paused/resumed | unit | `python -m pytest tests/test_analysis_service.py -x` | Wave 0 |
| AUDIO-02 | Analysis API endpoints (start/stop/status) return correct responses | unit | `python -m pytest tests/test_analysis_api.py -x` | Wave 0 |
| AUDIO-03 | Metadata fallback generates feature vector from genre/year | unit | `python -m pytest tests/test_audio_analyzer.py::test_metadata_fallback -x` | Wave 0 |
| AUDIO-04 | Already-analyzed tracks (analyzed_at not null) are skipped | unit | `python -m pytest tests/test_analysis_service.py::test_skip_analyzed -x` | Wave 0 |
| D-12 | PlexAPI track mapping includes file_path | unit | `python -m pytest tests/test_plex_client_tracks.py -x` | Existing (needs update) |
| D-13 | Path remapping converts Plex path to container path | unit | `python -m pytest tests/test_audio_analyzer.py::test_path_remap -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/ -x -q`
- **Per wave merge:** `python -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_audio_analyzer.py` -- covers AUDIO-01, AUDIO-03, D-13
- [ ] `tests/test_analysis_service.py` -- covers AUDIO-02, AUDIO-04
- [ ] `tests/test_analysis_api.py` -- covers AUDIO-02 API layer
- [ ] Update `tests/conftest.py` -- add fixtures for mock Essentia and Track records with file paths

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | N/A (single-user, no auth) |
| V3 Session Management | no | N/A |
| V4 Access Control | no | N/A |
| V5 Input Validation | yes | Validate file paths before passing to Essentia -- no path traversal outside /music mount |
| V6 Cryptography | no | N/A |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal via crafted Plex file paths | Tampering | Validate remapped paths start with /music, reject paths with `..` |
| Resource exhaustion from analyzing huge files | Denial of Service | Set timeout per track (30s), skip files > 100MB |

## Sources

### Primary (HIGH confidence)
- [PyPI essentia 2.1b6.dev1389](https://pypi.org/project/essentia/) - Version, wheel availability, Python 3.12 support verified
- [Essentia MusicExtractor tutorial](https://essentia.upf.edu/tutorial_extractors_musicextractor.html) - Feature extraction API, pool key names
- [Essentia Algorithms overview](https://essentia.upf.edu/algorithms_overview.html) - Available algorithms: Energy, Danceability, Key, RhythmExtractor2013, SpectralComplexity
- [Essentia Danceability reference](https://essentia.upf.edu/reference/std_Danceability.html) - DFA-based, range 0 to ~3
- [Essentia Key reference](https://essentia.upf.edu/reference/std_Key.html) - Returns key + scale, 14 profile types
- [Essentia models page](https://essentia.upf.edu/models.html) - DEAM arousal/valence models (TF-dependent), MusiCNN embeddings
- [PlexAPI media module](https://python-plexapi.readthedocs.io/en/latest/modules/media.html) - MediaPart.file attribute for track file paths
- [Essentia Python examples](https://essentia.upf.edu/essentia_python_examples.html) - BPM, key, danceability code examples

### Secondary (MEDIUM confidence)
- [Essentia Docker images](https://github.com/MTG/essentia-docker) - Official Docker images exist but not needed (pip wheel sufficient)
- [Truedat project](https://github.com/halrad-com/Truedat) - Real-world example of Essentia for mood extraction, validates proxy valence approach, confirms 15-feature extraction pattern

### Tertiary (LOW confidence)
- [Essentia.js benchmarks](https://transactions.ismir.net/articles/10.5334/tismir.111) - JS benchmarks suggest 1.5-6.8% of audio duration for most algorithms; native Python is faster

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- PyPI wheel verified, API documented, examples confirmed
- Architecture: HIGH -- mirrors proven sync_service.py pattern, feature keys verified from official docs
- Pitfalls: HIGH -- Docker/path issues well-understood, file corruption handling is standard
- Valence proxy: MEDIUM -- heuristic approach, weights assumed, but functionally adequate

**Research date:** 2026-04-09
**Valid until:** 2026-05-09 (stable domain, Essentia release cycle is slow)
