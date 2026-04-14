"""Audio feature extraction using Essentia with valence proxy and path remapping.

This module provides:
- extract_features: Essentia-based audio analysis returning normalized feature dict
- compute_valence_proxy: Weighted proxy for Spotify-style valence from Essentia features
- remap_plex_path: Remap Plex server paths to container mount paths
- metadata_feature_vector: Heuristic feature estimation from genre/year metadata
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Genre-to-energy heuristic mappings (D-15, D-16)
GENRE_ENERGY = {
    "metal": 0.9,
    "punk": 0.85,
    "electronic": 0.75,
    "hip hop": 0.7,
    "rock": 0.65,
    "pop": 0.6,
    "r&b": 0.5,
    "jazz": 0.45,
    "folk": 0.35,
    "classical": 0.3,
    "ambient": 0.2,
}


def compute_valence_proxy(
    scale: str,
    spectral_centroid: float,
    danceability: float,
    pitch_salience: float,
) -> float:
    """Approximate Spotify-style valence from Essentia features.

    Returns a value in [0.0, 1.0] where higher = more positive/happy.

    Weights: 0.30 mode, 0.25 danceability, 0.25 brightness, 0.20 pitch salience.
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
        0.30 * mode_score
        + 0.25 * dance_norm
        + 0.25 * brightness
        + 0.20 * salience_norm
    )
    return round(min(max(valence, 0.0), 1.0), 4)


def remap_plex_path(
    plex_path: str,
    plex_music_root: str,
    container_mount: str = "/music",
) -> str:
    """Remap a Plex file path to the container's mount point.

    Example: /data/Music/Artist/Album/song.flac -> /music/Artist/Album/song.flac

    Raises ValueError if path contains '..' (path traversal prevention, ASVS V5).
    """
    if ".." in plex_path:
        raise ValueError(
            f"Rejected path traversal attempt: path contains '..'"
        )

    if plex_path.startswith(plex_music_root):
        relative = plex_path[len(plex_music_root):]
        return container_mount + relative

    # Fallback: use filename only if prefix doesn't match
    filename = plex_path.split("/")[-1]
    return container_mount + "/" + filename


def metadata_feature_vector(genre: str | None, year: int | None) -> dict:
    """Generate approximate features from metadata when Essentia data unavailable.

    Used as fallback for tracks that haven't been analyzed or failed analysis (D-15, D-16).
    Returns a feature dict with the same keys as extract_features.
    """
    genre_lower = genre.lower() if genre else ""
    energy = next(
        (v for k, v in GENRE_ENERGY.items() if k in genre_lower),
        0.5,  # default middle
    )

    # Era affects valence slightly (80s pop = high valence)
    era_valence_boost = 0.1 if year and 1980 <= year <= 1989 else 0.0

    return {
        "energy": energy,
        "tempo": None,
        "danceability": energy * 0.8,
        "valence": 0.5 + era_valence_boost,
        "musical_key": None,
        "scale": None,
        "spectral_complexity": None,
        "loudness": None,
    }


def extract_features(file_path: str) -> dict:
    """Extract audio features from a single track file using Essentia MusicExtractor.

    Returns a normalized dict with keys: energy, tempo, danceability, valence,
    musical_key, scale, spectral_complexity, loudness.

    Raises RuntimeError with descriptive message on extraction failure.
    """
    try:
        import essentia.standard as es

        extractor = es.MusicExtractor(
            lowlevelStats=["mean", "stdev"],
            rhythmStats=["mean", "stdev"],
            tonalStats=["mean", "stdev"],
        )
        features, _ = extractor(file_path)

        # Extract raw features from pool
        bpm = features["rhythm.bpm"]
        raw_danceability = features["rhythm.danceability"]
        key = features["tonal.key_edma.key"]
        scale = features["tonal.key_edma.scale"]
        energy = features["lowlevel.spectral_rms.mean"]
        loudness = features["lowlevel.loudness_ebu128.integrated"]
        spectral_complexity = features["lowlevel.spectral_complexity.mean"]
        spectral_centroid = features["lowlevel.spectral_centroid.mean"]
        pitch_salience = features["lowlevel.pitch_salience.mean"]

        # Normalize energy from Essentia's spectral_rms [0, ~0.3] to [0, 1]
        energy = min(energy / 0.3, 1.0)

        # Normalize danceability from Essentia's [0, ~3] to [0, 1]
        danceability = min(raw_danceability / 3.0, 1.0)

        # Compute valence proxy from extracted features
        valence = compute_valence_proxy(
            scale=scale,
            spectral_centroid=spectral_centroid,
            danceability=raw_danceability,
            pitch_salience=pitch_salience,
        )

        return {
            "energy": float(energy),
            "tempo": float(bpm),
            "danceability": round(float(danceability), 4),
            "valence": valence,
            "musical_key": str(key),
            "scale": str(scale),
            "spectral_complexity": float(spectral_complexity),
            "loudness": float(loudness),
        }

    except Exception as exc:
        raise RuntimeError(
            f"Feature extraction failed for {file_path}: {exc}"
        ) from exc
