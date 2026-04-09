# Essentia Audio Analysis Reference

**Created:** 2026-04-09
**Purpose:** Comprehensive reference for executor agents using Essentia in Composer
**Essentia Version:** 2.1b6.dev1389 (pre-release, but the standard PyPI distribution)

---

## Table of Contents

1. [Installation](#installation)
2. [Audio Loading and Formats](#audio-loading-and-formats)
3. [MusicExtractor vs Individual Algorithms](#musicextractor-vs-individual-algorithms)
4. [Feature Reference: Exact Outputs and Ranges](#feature-reference-exact-outputs-and-ranges)
5. [Valence Proxy](#valence-proxy)
6. [Error Handling](#error-handling)
7. [Performance](#performance)
8. [Docker Considerations](#docker-considerations)
9. [Known Issues and Gotchas](#known-issues-and-gotchas)
10. [Code Patterns: Use This, Not That](#code-patterns-use-this-not-that)
11. [Complete MusicExtractor Descriptor Keys](#complete-musicextractor-descriptor-keys)
12. [Sources](#sources)

---

## Installation

### pip install

```bash
pip install essentia
```

That is it. No system packages, no C++ compiler, no apt-get. The manylinux wheel bundles all native dependencies (FFTW, libyaml, FFmpeg codecs). [VERIFIED: PyPI]

### Available Wheels (2.1b6.dev1389)

| Platform | Python Versions | Wheel Size |
|----------|----------------|------------|
| manylinux x86_64 | 3.9, 3.10, 3.11, 3.12, 3.13 | 13.8 MB |
| macOS ARM64 (Apple Silicon) | 3.9, 3.10, 3.11, 3.12, 3.13 | 20.4 MB |
| macOS x86_64 | 3.9, 3.10, 3.11, 3.12, 3.13 | 21.9 MB |

[VERIFIED: pypi.org/project/essentia/#files]

### Platform Notes

- **Linux x86_64 (Docker target):** Works out of the box with `python:3.12-slim`. The manylinux_2_17 wheel is compatible with Debian bookworm (glibc 2.36). [VERIFIED]
- **Linux ARM64/aarch64:** NO pre-built wheel available. Would require building from source with all C++ dependencies. This means ARM-based NAS devices (some Synology, Raspberry Pi) cannot run Essentia without significant effort. [VERIFIED: no aarch64 wheel on PyPI]
- **macOS (local dev):** Wheels available for both Intel and Apple Silicon. Works with Python 3.9 (the project's local dev target). [VERIFIED]
- **Windows:** No pre-built wheels. Not relevant for this project (Docker deployment).

### Do NOT install essentia-tensorflow

The `essentia-tensorflow` package is 291 MB and pulls in TensorFlow. It is only needed for deep learning models (DEAM arousal/valence, MusiCNN embeddings). All algorithms used in Composer are in the base `essentia` package. [VERIFIED: pypi.org]

### Version Pinning

```
essentia>=2.1b6.dev1389,<2.2
```

The version string looks unusual (dev release) but this IS the current stable distribution on PyPI. There has not been a non-dev release in years; the dev releases ARE the releases.

---

## Audio Loading and Formats

### Supported Formats

Essentia uses FFmpeg internally for audio I/O. Supported formats include all those supported by FFmpeg:

| Format | Extension | Status |
|--------|-----------|--------|
| WAV | .wav | Fully supported |
| FLAC | .flac | Fully supported |
| MP3 | .mp3 | Fully supported |
| OGG Vorbis | .ogg | Supported (note: decoded with reversed phase due to FFmpeg) |
| AAC/M4A | .m4a, .aac | Supported via FFmpeg |
| AIFF | .aiff, .aif | Fully supported |

[VERIFIED: essentia.upf.edu AudioLoader reference]

### How MusicExtractor Loads Audio

MusicExtractor handles file loading internally. You pass a file path string, NOT an audio array:

```python
# CORRECT: Pass file path to MusicExtractor
features, features_frames = es.MusicExtractor()(file_path)

# WRONG: Do not load audio first and pass an array
# audio = es.MonoLoader(filename=file_path)()
# features = es.MusicExtractor()(audio)  # MusicExtractor takes a filename, not audio data
```

MusicExtractor internally uses AudioLoader in streaming mode, which handles:
- Automatic resampling to 44100 Hz (the analysis sample rate)
- Stereo to mono downmixing
- Format detection and decoding via FFmpeg

### MonoLoader (for individual algorithms)

If you use individual algorithms instead of MusicExtractor, load audio with MonoLoader:

```python
audio = es.MonoLoader(filename=file_path, sampleRate=44100)()
```

Parameters:
- `filename` (string): Path to audio file
- `sampleRate` (float, default=44100): Desired output sample rate. Audio is automatically resampled.
- `downmix` (string, default="mix"): How to handle stereo. Options: "left", "right", "mix"

**Critical:** Many algorithms (RhythmExtractor2013, Danceability) require 44100 Hz sample rate. Always use 44100.

[VERIFIED: essentia.upf.edu MonoLoader reference]

---

## MusicExtractor vs Individual Algorithms

### Use MusicExtractor (recommended for Composer)

MusicExtractor computes 180+ descriptors in a single pass over the audio file. It is optimized for batch processing of music collections (used by AcousticBrainz for millions of tracks).

```python
import essentia.standard as es

extractor = es.MusicExtractor(
    lowlevelStats=['mean', 'stdev'],
    rhythmStats=['mean', 'stdev'],
    tonalStats=['mean', 'stdev'],
)
features, features_frames = extractor(file_path)
```

**Returns two Pool objects:**
- `features` (Pool): Aggregated statistics (mean, stdev, etc.) across all frames
- `features_frames` (Pool): Per-frame values (we do not need these)

Access features with dot-notation keys:
```python
bpm = features['rhythm.bpm']
key = features['tonal.key_edma.key']
energy = features['lowlevel.spectral_rms.mean']
```

List all available keys:
```python
sorted(features.descriptorNames())
```

### When to Use Individual Algorithms Instead

Only when you need an algorithm that MusicExtractor does not include, or when you need fine-grained control over parameters. For Composer, MusicExtractor covers everything we need.

### MusicExtractor Configuration

Key parameters (all have sensible defaults):

| Parameter | Default | Description |
|-----------|---------|-------------|
| analysisSampleRate | 44100 | Audio is resampled to this rate |
| lowlevelFrameSize | 2048 | Frame size for spectral features |
| lowlevelHopSize | 1024 | Hop size for spectral features |
| tonalFrameSize | 4096 | Frame size for tonal features |
| tonalHopSize | 2048 | Hop size for tonal features |
| lowlevelStats | ['mean', 'median', ...] | Statistics to compute for low-level features |
| rhythmStats | ['mean', 'median', ...] | Statistics for rhythm features |
| tonalStats | ['mean', 'median', ...] | Statistics for tonal features |
| startTime | 0 | Start analysis at this time (seconds) |
| endTime | 2000 | End analysis at this time (seconds) |
| minTempo / maxTempo | 40 / 208 | BPM detection range |

[VERIFIED: essentia.upf.edu MusicExtractor reference]

---

## Feature Reference: Exact Outputs and Ranges

This is the most important section. Every feature we extract has a specific range and meaning.

### 1. Energy (spectral_rms.mean)

| Property | Value |
|----------|-------|
| **MusicExtractor key** | `lowlevel.spectral_rms.mean` |
| **Algorithm** | RMS (root mean square of spectrum magnitude) |
| **Output range** | [0, 1] for normalized audio; typically 0.0001 to 0.3 for real music |
| **What it represents** | Overall energy/loudness of the signal as RMS of spectral magnitudes |
| **Normalization needed?** | Yes -- raw values are small. For meaningful 0-1 comparison, normalize across your library or use a fixed scale. |

**Gotcha:** RMS of the spectrum is NOT the same as perceived loudness. It is a physical measurement of signal energy. A heavily compressed pop track and a dynamic classical piece might have similar RMS but very different perceived loudness.

**Our implementation stores it raw.** This is acceptable because the LLM uses it as a relative comparison signal, not an absolute value. [ASSUMED]

[VERIFIED: essentia.upf.edu RMS reference -- computes "the root mean square of the input array"]

### 2. Tempo/BPM

| Property | Value |
|----------|-------|
| **MusicExtractor key** | `rhythm.bpm` |
| **Algorithm** | RhythmExtractor2013 (multifeature method) |
| **Output range** | [40, 208] BPM (configurable via minTempo/maxTempo) |
| **Additional outputs** | `rhythm.beats_position` (beat times in seconds), confidence |
| **Method** | multifeature (default) -- uses BeatTrackerMultiFeature with confidence estimation |

**Edge cases:**
- **Ambient/drone music with no clear beat:** Returns a BPM value anyway (usually incorrect). The confidence will be low but MusicExtractor does not expose confidence directly -- it only stores `rhythm.bpm`.
- **Very fast music (>208 BPM):** Will be detected at half tempo (e.g., 260 BPM blast beat detected as 130 BPM). Increase `maxTempo` if needed.
- **Very slow music (<40 BPM):** Will be detected at double tempo.
- **Tempo changes:** Returns the dominant tempo.

**Gotcha:** The `degara` method does NOT produce confidence values (always returns 0). Use `multifeature` (the default).

[VERIFIED: essentia.upf.edu RhythmExtractor2013 reference]

### 3. Danceability

| Property | Value |
|----------|-------|
| **MusicExtractor key** | `rhythm.danceability` |
| **Algorithm** | Danceability (Detrended Fluctuation Analysis) |
| **Output range** | **[0, ~3]** -- NOT [0, 1] |
| **Normalization** | Divide by 3.0 and clamp to [0, 1] |
| **What it represents** | Rhythmic regularity and predictability. Higher = more regular beat pattern = more danceable. |

**CRITICAL GOTCHA:** Essentia's danceability is 0 to ~3, not 0 to 1 like Spotify. This is the single most common mistake when using Essentia for Spotify-like features.

**Normalization formula:**
```python
danceability_normalized = min(raw_danceability / 3.0, 1.0)
```

**DFA explanation:** The algorithm uses Detrended Fluctuation Analysis on the audio signal. It measures how self-similar the rhythm is across different time scales (310ms to 8800ms by default). Highly regular dance music scores high; free jazz or ambient scores low.

**Parameters (defaults are good):**
- `minTau`: 310 ms (minimum segment length)
- `maxTau`: 8800 ms (maximum segment length)
- `tauMultiplier`: 1.1 (increment factor)
- `sampleRate`: 44100 Hz (must match audio)

[VERIFIED: essentia.upf.edu Danceability reference -- "Normal values range from 0 to ~3"]

### 4. Key and Scale

| Property | Value |
|----------|-------|
| **MusicExtractor keys** | `tonal.key_edma.key`, `tonal.key_edma.scale` |
| **Key output** | One of: A, A#, B, C, C#, D, D#, E, F, F#, G, G# |
| **Scale output** | "major" or "minor" |
| **Additional outputs** | `tonal.key_edma.strength` (confidence, 0-1) |
| **Profile used** | EDMA (Electronic Dance Music Analysis) |

**Available key profiles:**

| Profile | Best For | Notes |
|---------|----------|-------|
| `edma` | Electronic/pop/modern music | **Use this for Composer** |
| `edmm` | Electronic (minor keys) | Variant of EDMA |
| `temperley` | Classical music | Based on music theory |
| `krumhansl` | General/classical | Original Krumhansl-Schmuckler |
| `bgate` | Default in some configs | Bgate profile |
| `tonictriad` | Simple tonal music | Three-chord based |

MusicExtractor computes all profiles simultaneously. Keys available:
- `tonal.key_temperley.key` / `.scale` / `.strength`
- `tonal.key_krumhansl.key` / `.scale` / `.strength`
- `tonal.key_edma.key` / `.scale` / `.strength`

**Why EDMA:** It is optimized for modern/popular music which is the majority of a typical Plex library. Classical music users might prefer `temperley`. [ASSUMED]

**Gotcha:** The `key` output uses sharps only (A#, not Bb). Your code may need to handle enharmonic equivalents if comparing with metadata that uses flats.

[VERIFIED: essentia.upf.edu Key reference -- 13 profile types listed]

### 5. Loudness (EBU R128)

| Property | Value |
|----------|-------|
| **MusicExtractor key** | `lowlevel.loudness_ebu128.integrated` |
| **Output unit** | LUFS (Loudness Units Full Scale) |
| **Typical range** | -70 to 0 LUFS; most music falls between -20 and -5 LUFS |
| **What it represents** | Perceived loudness according to the EBU R128 broadcast standard |

**How it works:** Applies K-weighting filter (models human hearing), then computes loudness with gating:
- Absolute gate at -70 LUFS (silence ignored)
- Relative gate 10 LU below the absolute-gated level

**Typical values by genre:**
| Genre | Typical LUFS |
|-------|-------------|
| Classical, jazz | -20 to -14 |
| Pop, rock | -14 to -8 |
| EDM, metal (loudness war) | -8 to -5 |
| Ambient, spoken word | -25 to -18 |

[ASSUMED] These genre ranges are approximate generalizations.

**Note:** This is a NEGATIVE number. Louder tracks have values closer to 0. Unlike `spectral_rms`, this is a perceptual measurement calibrated to how humans hear loudness.

**Gotcha in MusicExtractor:** LoudnessEBUR128 expects stereo input. MusicExtractor handles this internally -- it feeds stereo audio to the loudness algorithm even though most other algorithms receive mono. If using the algorithm individually, you must provide stereo audio.

[VERIFIED: essentia.upf.edu LoudnessEBUR128 reference]

### 6. Spectral Complexity

| Property | Value |
|----------|-------|
| **MusicExtractor key** | `lowlevel.spectral_complexity.mean` |
| **Output range** | [0, unbounded] -- typically 0 to ~20 for music |
| **What it represents** | Number of significant peaks in the spectrum |
| **Musical meaning** | Higher = more harmonically complex (orchestral, jazz). Lower = simpler (sine waves, bass-heavy EDM). |

**Parameters:**
- `magnitudeThreshold` (default 0.005): Minimum peak magnitude to count
- `sampleRate` (default 44100)

**Normalization:** Not straightforward because the range depends on content. For Composer, store raw and let the LLM interpret relative values. [ASSUMED]

[VERIFIED: essentia.upf.edu SpectralComplexity reference]

### 7. Spectral Centroid

| Property | Value |
|----------|-------|
| **MusicExtractor key** | `lowlevel.spectral_centroid.mean` |
| **Output range** | [0, Nyquist/2] Hz -- practically 0 to ~22050 Hz at 44100 sample rate |
| **Typical values** | 500-5000 Hz for most music |
| **What it represents** | "Brightness" -- where the center of mass of the spectrum is |
| **Musical meaning** | Higher = brighter, more treble. Lower = darker, more bass-heavy. |

**Used in valence proxy.** Normalization for valence:
```python
brightness = min(max((spectral_centroid - 500) / 4500, 0.0), 1.0)
```
This maps the typical music range (500-5000 Hz) to [0, 1]. [ASSUMED -- range values are heuristic]

[VERIFIED: essentia.upf.edu Centroid reference]

### 8. Pitch Salience

| Property | Value |
|----------|-------|
| **MusicExtractor key** | `lowlevel.pitch_salience.mean` |
| **Output range** | **[0, 1]** -- already normalized |
| **What it represents** | How tonal/pitched the audio is |
| **Musical meaning** | Close to 0 = unpitched (percussion, noise). Close to 1 = clear harmonic content (voice, guitar, piano). |

**Algorithm:** Ratio of highest autocorrelation peak to the non-shifted autocorrelation value.

**Behavior:**
- Pure silence returns 0
- Pure tone returns close to 0 (few harmonics)
- Rich harmonic sounds (voice, strings) return higher values
- Noise/percussion returns close to 0

[VERIFIED: essentia.upf.edu PitchSalience reference -- "normalized between 0 and 1"]

### Summary Table: All Features We Extract

| Feature | MusicExtractor Key | Raw Range | Stored As | Normalization |
|---------|-------------------|-----------|-----------|---------------|
| Energy | `lowlevel.spectral_rms.mean` | [0, ~0.3] | float | None (raw) |
| Tempo | `rhythm.bpm` | [40, 208] | float | None (BPM) |
| Danceability | `rhythm.danceability` | [0, ~3] | float | / 3.0, clamp to [0, 1] |
| Key | `tonal.key_edma.key` | A-G# | string | None |
| Scale | `tonal.key_edma.scale` | major/minor | string | None |
| Loudness | `lowlevel.loudness_ebu128.integrated` | [-70, 0] LUFS | float | None (LUFS) |
| Spectral Complexity | `lowlevel.spectral_complexity.mean` | [0, ~20] | float | None (raw) |
| Spectral Centroid | `lowlevel.spectral_centroid.mean` | [0, ~22050] Hz | Used in valence only | (x - 500) / 4500 |
| Pitch Salience | `lowlevel.pitch_salience.mean` | [0, 1] | Used in valence only | None (already [0,1]) |
| Valence (computed) | N/A -- proxy | [0, 1] | float | Weighted combination |

---

## Valence Proxy

Essentia has NO valence algorithm. "Valence" is a Spotify-proprietary concept meaning "musical positivity." We compute a proxy.

### Formula

```python
def compute_valence_proxy(
    scale: str,
    spectral_centroid: float,
    danceability: float,       # RAW, not normalized
    pitch_salience: float,
) -> float:
    mode_score = 1.0 if scale == "major" else 0.0
    dance_norm = min(danceability / 3.0, 1.0)
    brightness = min(max((spectral_centroid - 500) / 4500, 0.0), 1.0)
    salience_norm = min(max(pitch_salience, 0.0), 1.0)

    valence = (
        0.30 * mode_score +
        0.25 * dance_norm +
        0.25 * brightness +
        0.20 * salience_norm
    )
    return round(min(max(valence, 0.0), 1.0), 4)
```

### Weight Rationale

| Component | Weight | Rationale |
|-----------|--------|-----------|
| Mode (major/minor) | 0.30 | Strongest indicator. Major keys perceived as happy, minor as sad. |
| Danceability | 0.25 | Danceable tracks tend to feel more positive/energetic. |
| Brightness (spectral centroid) | 0.25 | Brighter timbres correlate with positive perception. |
| Pitch salience | 0.20 | Melodic clarity contributes to perceived positivity. |

### Limitations

- Binary mode (major=1, minor=0) is crude. A minor key song can be energetic and positive (e.g., "Another One Bites the Dust").
- Danceability is about rhythm regularity, not emotional valence.
- These weights are heuristic and may need tuning. [ASSUMED]
- The LLM playlist engine compensates: it reads the numeric valence alongside genre, artist, and user prompt context. Precision is less important than having a signal to filter on.

### Alternative Approaches (NOT recommended for Composer)

1. **essentia-tensorflow DEAM model:** Outputs arousal + valence on [1, 9] scale. Adds 291 MB dependency. Overkill for our use case.
2. **Train a custom model:** Would require labeled dataset. Way out of scope.
3. **Use only mode (major/minor):** Too binary. The proxy is better than nothing.

---

## Error Handling

### Exception Types

Essentia throws Python `RuntimeError` for most errors. The underlying C++ code uses `EssentiaException` which is surfaced as `RuntimeError` in Python.

### Common Error Scenarios

| Scenario | Exception | Message Pattern | How to Handle |
|----------|-----------|----------------|---------------|
| File not found | RuntimeError | "AudioLoader: ... No such file" | Check path exists before calling |
| Unsupported codec | RuntimeError | "AudioLoader: Unsupported codec!" | Log and skip |
| Corrupted file | **SEGFAULT** | Process crash, no Python exception | See mitigation below |
| Empty/zero-byte file | RuntimeError | Various | Log and skip |
| >2 channels | RuntimeError | "more than 2 channels" | Rare; log and skip |
| Non-audio file | RuntimeError or **SEGFAULT** | Varies | Pre-filter by extension |

### CRITICAL: Segfault Risk with Corrupted Files

**This is the biggest gotcha in Essentia.** Some corrupted files or files with invalid codecs cause a C-level segfault that bypasses Python's try/except entirely. The Python process dies.

**Historical issue:** GitHub issue #546 and #847 document segfaults when loading corrupted files. PR #1494 (merged Feb 2026) fixed many of these by updating FFmpeg compatibility. [VERIFIED: GitHub issues]

**Mitigation strategy:**

```python
import os

# Pre-flight checks BEFORE passing to Essentia
def safe_extract_features(file_path: str) -> dict | None:
    """Extract features with safety checks to avoid segfaults."""

    # 1. Check file exists and is not empty
    if not os.path.isfile(file_path):
        logger.warning(f"File not found: {file_path}")
        return None

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        logger.warning(f"Empty file: {file_path}")
        return None

    # 2. Check file extension is a known audio format
    SUPPORTED_EXTENSIONS = {'.wav', '.flac', '.mp3', '.ogg', '.m4a', '.aac', '.aiff', '.aif'}
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        logger.warning(f"Unsupported extension {ext}: {file_path}")
        return None

    # 3. Optional: skip very large files (>500MB) to avoid memory issues
    if file_size > 500 * 1024 * 1024:
        logger.warning(f"File too large ({file_size} bytes): {file_path}")
        return None

    # 4. Now attempt extraction with try/except
    try:
        import essentia.standard as es
        extractor = es.MusicExtractor(
            lowlevelStats=['mean', 'stdev'],
            rhythmStats=['mean', 'stdev'],
            tonalStats=['mean', 'stdev'],
        )
        features, _ = extractor(file_path)
        # ... extract specific features ...
        return result
    except RuntimeError as exc:
        logger.error(f"Essentia error for {file_path}: {exc}")
        return None
    except Exception as exc:
        logger.error(f"Unexpected error for {file_path}: {exc}")
        return None
```

**Additional safety:** If paranoid about segfaults (e.g., untrusted music libraries), run extraction in a subprocess:

```python
import multiprocessing

def _extract_in_subprocess(file_path, result_queue):
    """Run extraction in separate process so segfaults don't kill the main process."""
    try:
        result = extract_features(file_path)
        result_queue.put(result)
    except Exception as exc:
        result_queue.put(None)

def safe_extract(file_path, timeout=60):
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=_extract_in_subprocess, args=(file_path, q))
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.terminate()
        return None
    return q.get_nowait() if not q.empty() else None
```

**For Composer:** The pre-flight check approach (option 1) is sufficient. Subprocess isolation is overkill for a personal music library where files are unlikely to be adversarial. The FFmpeg fixes in PR #1494 resolve most historical segfault issues.

---

## Performance

### Processing Time

No official benchmarks exist. Based on community reports and the AcousticBrainz project (which processed millions of tracks):

| Metric | Estimate | Confidence |
|--------|----------|------------|
| Time per track (MusicExtractor) | 1-5 seconds | [ASSUMED] |
| Time per 3-min track (typical) | ~2 seconds | [ASSUMED] |
| Time for 10K track library | ~5.5 hours | [ASSUMED] (at 2s/track) |
| Time for 1K track library | ~33 minutes | [ASSUMED] |

Factors affecting speed:
- **Track duration:** Linear relationship. A 10-minute track takes ~5x longer than 2-minute track.
- **CPU speed:** Essentia is CPU-bound C++ code. NAS CPUs (Celeron/Atom) are 3-5x slower than desktop CPUs.
- **Audio format:** FLAC and WAV are slightly faster than MP3 (no decode overhead), but the difference is minimal.
- **Sample rate:** Higher sample rate source files take longer (more samples to process). All are resampled to 44100 Hz internally.

### Memory Usage

| Metric | Estimate | Confidence |
|--------|----------|------------|
| RSS per MusicExtractor call | 50-100 MB | [ASSUMED] |
| Peak memory | ~150 MB | [ASSUMED] |
| Memory leak risk | Low -- each call is independent | [ASSUMED] |

MusicExtractor uses streaming mode internally, so it does not load the entire file into memory. Memory usage is proportional to frame buffer sizes, not file duration.

### CPU and Threading

**Essentia releases the GIL during C++ execution.** This means:
- `asyncio.to_thread()` works correctly -- the event loop remains responsive while Essentia runs.
- Multiple `asyncio.to_thread()` calls can overlap if the GIL is released, but each Essentia call is single-threaded internally.
- For true parallel processing, use `multiprocessing.ProcessPoolExecutor`. However, for Composer's sequential analysis pattern, this is unnecessary.

**Recommended approach for Composer:**
```python
result = await asyncio.to_thread(extract_features, file_path)
```
This keeps the FastAPI event loop responsive for HTMX progress polling. [VERIFIED: Essentia is C++ with Python bindings, GIL release during native execution is standard behavior]

### Batch Processing Tips

1. **Process sequentially, commit per-track.** Do not batch hundreds of tracks into one transaction. If the container crashes, you lose all progress.
2. **Do NOT create a new MusicExtractor instance per track.** Create one and reuse it:
   ```python
   extractor = es.MusicExtractor(
       lowlevelStats=['mean', 'stdev'],
       rhythmStats=['mean', 'stdev'],
       tonalStats=['mean', 'stdev'],
   )
   for file_path in tracks:
       features, _ = extractor(file_path)  # reuse extractor
   ```
3. **Yield between tracks** to allow progress updates:
   ```python
   for i, track in enumerate(tracks):
       result = await asyncio.to_thread(extractor, track.file_path)
       # Save result to DB
       # Update progress status
       await asyncio.sleep(0)  # Yield to event loop
   ```

---

## Docker Considerations

### Image Size Impact

| Component | Size |
|-----------|------|
| essentia wheel | 13.8 MB |
| Runtime dependencies | 0 (bundled in wheel) |
| Total added to image | ~14 MB |

This is negligible. The `python:3.12-slim` base image is ~150 MB. Adding essentia increases it by less than 10%.

### No Additional apt Packages Needed

The manylinux wheel bundles:
- FFmpeg codecs (for audio decoding)
- FFTW (for FFT computation)
- libyaml (for configuration)
- All other native dependencies

Do NOT add `apt-get install ffmpeg` or similar. It is unnecessary and may conflict with the bundled libraries.

### Dockerfile Changes

```dockerfile
# In requirements.txt, add:
# essentia>=2.1b6.dev1389,<2.2

# No Dockerfile changes needed beyond the pip install
# The existing pip install step handles everything
```

### Threading in Docker

Essentia uses a single thread per MusicExtractor call. No special threading configuration needed. The default Docker CPU limits are fine.

If running on a resource-constrained NAS:
```yaml
# docker-compose.yml
services:
  composer:
    # Optional: limit CPU to prevent starving other containers during analysis
    deploy:
      resources:
        limits:
          cpus: '1.0'  # Use at most 1 CPU core
```

---

## Known Issues and Gotchas

### 1. Danceability Range (0-3, NOT 0-1)

**Severity:** HIGH -- will produce incorrect downstream behavior if not normalized.

Essentia's danceability outputs values in [0, ~3]. Every consumer of this value must normalize:
```python
normalized = min(raw / 3.0, 1.0)
```

Our current `extract_features()` already does this correctly. `compute_valence_proxy()` receives the RAW value and normalizes internally. Do not double-normalize.

### 2. MusicExtractor Key Names Are Not Obvious

**Severity:** MEDIUM -- causes KeyError at runtime.

The feature keys use a specific naming convention that is not fully documented in one place. Confirmed working keys for our use case:

```python
features['rhythm.bpm']                          # float
features['rhythm.danceability']                  # float
features['tonal.key_edma.key']                   # string: "A" through "G#"
features['tonal.key_edma.scale']                 # string: "major" or "minor"
features['lowlevel.spectral_rms.mean']           # float
features['lowlevel.loudness_ebu128.integrated']  # float (LUFS)
features['lowlevel.spectral_complexity.mean']    # float
features['lowlevel.spectral_centroid.mean']      # float (Hz)
features['lowlevel.pitch_salience.mean']         # float [0, 1]
```

**Debugging tip:** If you get a KeyError, print all keys:
```python
for name in sorted(features.descriptorNames()):
    print(f"{name}: {features[name]}")
```

### 3. Segfaults on Corrupted Files (Mostly Fixed)

**Severity:** LOW (after PR #1494 fix) -- was HIGH before.

Historical issue with FFmpeg codec handling causing C-level segfaults. PR #1494 (Feb 2026) updated FFmpeg compatibility. Pre-flight file validation (check extension, check size > 0) is still recommended.

### 4. OGG Files Decoded with Reversed Phase

**Severity:** LOW -- does not affect feature extraction.

FFmpeg's OGG decoder reverses the phase of the audio signal. This affects waveform sign but NOT spectral features, energy, or any MusicExtractor output. [VERIFIED: essentia.upf.edu AudioLoader reference]

### 5. Chromaprint Warning When Library Missing

**Severity:** NEGLIGIBLE -- warning only, no functional impact.

MusicExtractor may log: "Chromaprint library not found, skipping computation." Chromaprint is optional and not needed for any of our features. Ignore the warning.

### 6. Loudness EBU R128 is a Negative Number

**Severity:** MEDIUM -- confusing for code that expects [0, 1] ranges.

Integrated loudness is in LUFS, which is always negative (or zero for a full-scale signal). Typical values: -20 to -5. Do not try to normalize to [0, 1] -- the LUFS scale is meaningful as-is. The LLM can interpret "loudness: -8.5 LUFS" directly.

### 7. No ARM64 Linux Wheels

**Severity:** LOW for Composer (Docker target is x86_64).

ARM-based NAS devices (some Synology, QNAP) cannot use the pre-built wheel. Building from source requires CMake, FFTW, FFmpeg dev headers, and significant compile time. Out of scope for Composer.

### 8. MusicExtractor is NOT Thread-Safe for the Same Instance

**Severity:** MEDIUM -- if you tried parallel processing.

Do not call the same MusicExtractor instance from multiple threads simultaneously. Either:
- Use sequential processing (recommended for Composer)
- Create separate instances per thread/process

---

## Code Patterns: Use This, Not That

### Loading Audio

```python
# USE THIS: Let MusicExtractor handle loading
features, _ = es.MusicExtractor()(file_path)

# NOT THIS: Loading audio separately
audio = es.MonoLoader(filename=file_path)()
# MusicExtractor doesn't accept audio arrays
```

### Accessing Features

```python
# USE THIS: Direct key access
bpm = features['rhythm.bpm']

# NOT THIS: Trying to iterate or use attribute access
bpm = features.rhythm.bpm  # Does not work
bpm = features['rhythm']['bpm']  # Does not work
```

### Error Handling

```python
# USE THIS: Pre-check + try/except
if not os.path.isfile(path):
    return None
ext = os.path.splitext(path)[1].lower()
if ext not in SUPPORTED_EXTENSIONS:
    return None
try:
    features, _ = extractor(path)
except RuntimeError as exc:
    logger.error(f"Extraction failed: {exc}")
    return None

# NOT THIS: Bare try/except hoping to catch segfaults
try:
    features, _ = extractor(path)  # Segfault on bad file = process death
except:
    pass
```

### Danceability

```python
# USE THIS: Normalize from [0, ~3] to [0, 1]
danceability = min(features['rhythm.danceability'] / 3.0, 1.0)

# NOT THIS: Using raw value as if it were [0, 1]
danceability = features['rhythm.danceability']  # Could be 2.5!
```

### Reusing the Extractor

```python
# USE THIS: Create once, call many times
extractor = es.MusicExtractor(
    lowlevelStats=['mean', 'stdev'],
    rhythmStats=['mean', 'stdev'],
    tonalStats=['mean', 'stdev'],
)
for track in tracks:
    features, _ = extractor(track.file_path)

# NOT THIS: Creating a new extractor per track
for track in tracks:
    extractor = es.MusicExtractor(...)  # Wasteful
    features, _ = extractor(track.file_path)
```

### Running in Async Context

```python
# USE THIS: asyncio.to_thread for non-blocking
import asyncio

async def analyze_track(file_path: str) -> dict:
    return await asyncio.to_thread(extract_features, file_path)

# NOT THIS: Blocking the event loop
async def analyze_track(file_path: str) -> dict:
    return extract_features(file_path)  # Blocks entire FastAPI server
```

---

## Complete MusicExtractor Descriptor Keys

For reference, here are all the descriptor keys we might ever need, organized by category. The ones we actually use are marked with `**`.

### Low-Level (prefix: `lowlevel.`)

**Energy/Loudness:**
- `average_loudness` -- Stevens' power law loudness, single value (not a stat)
- **`loudness_ebu128.integrated`** -- EBU R128 integrated loudness (LUFS)
- `loudness_ebu128.loudness_range` -- Dynamic range (LU)
- `dynamic_complexity` -- Dynamic range complexity
- **`spectral_rms.mean`** / `.stdev` -- RMS of spectrum
- `spectral_energy.mean` / `.stdev` -- Total spectral energy
- `silence_rate_20dB.mean` / `30dB` / `60dB` -- Fraction of silent frames

**Spectral Shape:**
- **`spectral_centroid.mean`** / `.stdev` -- Brightness (Hz)
- **`spectral_complexity.mean`** / `.stdev` -- Number of spectral peaks
- `spectral_flux.mean` / `.stdev` -- Frame-to-frame spectral change
- `spectral_rolloff.mean` / `.stdev` -- Frequency below which 85% of energy
- `spectral_spread.mean` / `.stdev` -- Bandwidth of spectrum
- `spectral_kurtosis.mean` / `.stdev` -- Peakedness of spectrum
- `spectral_skewness.mean` / `.stdev` -- Asymmetry of spectrum
- `spectral_decrease.mean` / `.stdev`
- `spectral_entropy.mean` / `.stdev`
- `spectral_strongpeak.mean` / `.stdev`
- `hfc.mean` / `.stdev` -- High frequency content

**Cepstral:**
- `mfcc.mean` / `.stdev` -- 13 MFCCs
- `gfcc.mean` / `.stdev` -- 13 GFCCs

**Band Energies:**
- `barkbands.mean` / `.stdev` -- 27 Bark bands
- `melbands.mean` / `.stdev` -- 40 Mel bands
- `melbands128.mean` / `.stdev` -- 128 Mel bands
- `erbbands.mean` / `.stdev` -- 40 ERB bands
- `spectral_energyband_low/middle_low/middle_high/high.mean`

**Tonal (in lowlevel):**
- `dissonance.mean` / `.stdev`
- **`pitch_salience.mean`** / `.stdev`
- `zerocrossingrate.mean` / `.stdev`

### Rhythm (prefix: `rhythm.`)

- **`bpm`** -- Detected tempo
- **`danceability`** -- DFA-based danceability score
- `beats_position` -- Array of beat times (seconds)
- `beats_count` -- Number of detected beats
- `onset_rate` -- Onsets per second
- `beats_loudness.mean` / `.stdev`
- `beats_loudness_band_ratio.mean` / `.stdev`
- `bpm_histogram_first_peak_bpm` / `_spread` / `_weight`
- `bpm_histogram_second_peak_bpm` / `_spread` / `_weight`

### Tonal (prefix: `tonal.`)

- **`key_edma.key`** -- Detected key (A-G#)
- **`key_edma.scale`** -- major/minor
- `key_edma.strength` -- Key detection confidence
- `key_temperley.key` / `.scale` / `.strength`
- `key_krumhansl.key` / `.scale` / `.strength`
- `tuning_frequency` -- Tuning reference (Hz, ideally ~440)
- `chords_key` / `chords_scale` -- Most common chord key/scale
- `chords_strength.mean` / `.stdev`
- `chords_changes_rate` -- Rate of chord changes
- `chords_number_rate` -- Number of distinct chords per second
- `hpcp.mean` / `.stdev` -- 36-dim harmonic pitch class profile
- `hpcp_entropy.mean` / `.stdev`
- `hpcp_crest.mean` / `.stdev`
- `tuning_diatonic_strength` / `tuning_equal_tempered_deviation` / `tuning_nontempered_energy_ratio`

---

## Review of Current Implementation

Our `audio_analyzer.py` is well-written. Specific observations:

### Correct

1. Danceability normalization: `min(raw_danceability / 3.0, 1.0)` -- correct.
2. Valence proxy passes RAW danceability to `compute_valence_proxy` which normalizes internally -- correct, no double-normalization.
3. MusicExtractor configuration with `['mean', 'stdev']` stats -- correct.
4. Key extraction uses EDMA profile -- good choice for popular music.
5. Path traversal protection with `..` check -- correct (ASVS V5).
6. Lazy import of essentia inside `extract_features()` -- correct for environments where essentia is not installed (local dev on macOS with Python 3.9 might not have it).

### Potential Issues to Watch

1. **No pre-flight file validation:** The current code does not check file existence or extension before passing to MusicExtractor. Add `os.path.isfile()` and extension check.
2. **Generic Exception catch:** `except Exception as exc: raise RuntimeError(...)` wraps everything including potential segfaults (though those will kill the process before reaching the except). This is fine but the caller must handle the RuntimeError gracefully.
3. **Extractor created per call:** `es.MusicExtractor(...)` is created inside `extract_features()`. For batch processing, the caller should ideally reuse the extractor. However, the overhead of creating a new one is minimal compared to the extraction itself. Low priority.
4. **No timeout:** If a corrupted file causes the extractor to hang (not segfault but spin), there is no timeout mechanism. The 30-second per-track timeout should be in the analysis service that calls this function.

---

## Sources

### Verified (HIGH confidence)
- [PyPI essentia package](https://pypi.org/project/essentia/) -- wheel availability, versions, platforms
- [Essentia MusicExtractor tutorial](https://essentia.upf.edu/tutorial_extractors_musicextractor.html) -- API, pool keys, configuration
- [Essentia Music Extractor descriptors](https://essentia.upf.edu/streaming_extractor_music.html) -- complete descriptor list
- [Essentia MusicExtractor reference](https://essentia.upf.edu/reference/std_MusicExtractor.html) -- parameters, 28 configuration options
- [Essentia Danceability reference](https://essentia.upf.edu/reference/std_Danceability.html) -- DFA algorithm, output range 0-3
- [Essentia Key reference](https://essentia.upf.edu/reference/std_Key.html) -- 13 profiles, output format
- [Essentia RhythmExtractor2013 reference](https://essentia.upf.edu/reference/std_RhythmExtractor2013.html) -- BPM, methods, confidence
- [Essentia LoudnessEBUR128 reference](https://essentia.upf.edu/reference/std_LoudnessEBUR128.html) -- LUFS output, K-weighting
- [Essentia PitchSalience reference](https://essentia.upf.edu/reference/std_PitchSalience.html) -- [0, 1] range
- [Essentia SpectralComplexity reference](https://essentia.upf.edu/reference/std_SpectralComplexity.html) -- peak counting
- [Essentia RMS reference](https://essentia.upf.edu/reference/std_RMS.html) -- quadratic mean
- [Essentia AudioLoader reference](https://essentia.upf.edu/reference/std_AudioLoader.html) -- FFmpeg-based, supported formats
- [Essentia MonoLoader reference](https://essentia.upf.edu/reference/std_MonoLoader.html) -- resampling, downmix
- [GitHub issue #847](https://github.com/MTG/essentia/issues/847) -- segfault on invalid codec
- [GitHub issue #546](https://github.com/MTG/essentia/issues/546) -- segfault loading non-audio files
- [GitHub PR #1494](https://github.com/MTG/essentia/pull/1494) -- FFmpeg 7.x compatibility fix (Feb 2026)

### Assumed (needs validation)
- Processing time estimates (~2 seconds per track) -- no official benchmarks found
- Memory usage estimates (~50-100 MB RSS) -- no official data
- Spectral centroid typical range for music (500-5000 Hz) -- heuristic
- Valence proxy weights (0.30/0.25/0.25/0.20) -- heuristic, may need tuning
- Genre-typical LUFS ranges -- approximate generalizations
