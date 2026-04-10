"""Playlist scoring engine: filters and ranks library tracks against mood criteria.

Implements weighted Euclidean distance scoring with:
- Energy normalization (Essentia spectral_rms range 0-0.3, not 0-1)
- Tempo normalization to [0,1]
- Metadata fallback for unanalyzed tracks (D-11)
- Genre filtering (include/exclude)
- Compact LLM-friendly output format
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from app.models.schemas import FeatureCriteria
from app.models.track import Track
from app.services.audio_analyzer import metadata_feature_vector

if TYPE_CHECKING:
    pass

# Scoring weights per feature (higher = more influence on distance)
FEATURE_WEIGHTS: dict[str, float] = {
    "energy": 1.0,
    "danceability": 0.8,
    "valence": 0.7,
    "tempo": 0.5,
}

# Essentia spectral_rms typically outputs in [0, ~0.3] range
ENERGY_NORM_DIVISOR = 0.3

# Tempo normalization constants
TEMPO_MIN = 40.0
TEMPO_RANGE = 180.0  # 220 - 40

# Penalty multiplier for tracks outside criteria range
OUT_OF_RANGE_PENALTY = 2.0

# Small penalty added to metadata-fallback tracks to prefer analyzed ones
METADATA_FALLBACK_PENALTY = 0.05


def score_track(track: object, criteria: FeatureCriteria) -> float:
    """Score a track against mood criteria using weighted Euclidean distance.

    Lower score = better match. Returns 1.0 for tracks with no scorable features.

    Args:
        track: A Track model instance (or mock with same attributes).
        criteria: The target mood criteria from LLM interpretation.

    Returns:
        Float score where 0.0 = perfect match, higher = worse match.
    """
    # Check if track has any audio features
    has_features = any(
        getattr(track, attr, None) is not None
        for attr in ("energy", "tempo", "danceability", "valence")
    )

    used_fallback = False
    if not has_features:
        # Use metadata fallback (D-11)
        fallback = metadata_feature_vector(
            getattr(track, "genre", None),
            getattr(track, "year", None),
        )
        energy_val = fallback.get("energy")
        tempo_val = fallback.get("tempo")
        dance_val = fallback.get("danceability")
        valence_val = fallback.get("valence")
        used_fallback = True
    else:
        energy_val = getattr(track, "energy", None)
        tempo_val = getattr(track, "tempo", None)
        dance_val = getattr(track, "danceability", None)
        valence_val = getattr(track, "valence", None)

    total_distance = 0.0
    feature_count = 0

    # Score each feature
    features = [
        ("energy", energy_val, criteria.energy_min, criteria.energy_max, True),
        ("tempo", tempo_val, criteria.tempo_min, criteria.tempo_max, False),
        ("danceability", dance_val, criteria.danceability_min, criteria.danceability_max, False),
        ("valence", valence_val, criteria.valence_min, criteria.valence_max, False),
    ]

    for name, value, crit_min, crit_max, needs_energy_norm in features:
        if value is None:
            continue

        # Normalize to [0, 1]
        if needs_energy_norm:
            normalized = min(value / ENERGY_NORM_DIVISOR, 1.0)
        elif name == "tempo":
            normalized = (value - TEMPO_MIN) / TEMPO_RANGE
            normalized = max(0.0, min(normalized, 1.0))
        else:
            # Danceability and valence already in [0, 1]
            normalized = max(0.0, min(value, 1.0))

        # Normalize criteria bounds for comparison
        if name == "tempo":
            norm_min = (crit_min - TEMPO_MIN) / TEMPO_RANGE
            norm_max = (crit_max - TEMPO_MIN) / TEMPO_RANGE
        else:
            norm_min = crit_min
            norm_max = crit_max

        # Distance to midpoint of criteria range
        midpoint = (norm_min + norm_max) / 2.0
        distance = abs(normalized - midpoint)

        # Apply penalty if outside range
        if normalized < norm_min or normalized > norm_max:
            distance *= OUT_OF_RANGE_PENALTY

        # Apply feature weight
        weight = FEATURE_WEIGHTS.get(name, 1.0)
        total_distance += (distance ** 2) * weight
        feature_count += 1

    if feature_count == 0:
        return 1.0

    score = math.sqrt(total_distance / feature_count)

    # Prefer analyzed tracks over metadata-fallback tracks
    if used_fallback:
        score += METADATA_FALLBACK_PENALTY

    return round(score, 6)


def filter_candidates(
    session: Session,
    criteria: FeatureCriteria,
    track_count: int = 20,
    candidate_limit: int = 300,
) -> list[tuple[Track, float]]:
    """Filter and score library tracks against mood criteria.

    Args:
        session: Database session.
        criteria: Target mood criteria.
        track_count: How many tracks the LLM should pick (passed to caller, not used here).
        candidate_limit: Maximum candidates to return for LLM context.

    Returns:
        List of (Track, score) tuples sorted by score ascending (best first).
    """
    # Query all tracks
    statement = select(Track)
    all_tracks = session.exec(statement).all()

    # Apply genre filters
    candidates = []
    for track in all_tracks:
        track_genre = (track.genre or "").lower()

        # Include filter: if genres specified, track must match at least one
        if criteria.genres:
            if not any(g.lower() in track_genre for g in criteria.genres):
                continue

        # Exclude filter: skip tracks matching excluded genres
        if criteria.exclude_genres:
            if any(g.lower() in track_genre for g in criteria.exclude_genres):
                continue

        candidates.append(track)

    # Score each candidate
    scored = [(track, score_track(track, criteria)) for track in candidates]

    # Sort by score ascending (lower = better match)
    scored.sort(key=lambda x: x[1])

    # Limit to candidate_limit
    return scored[:candidate_limit]


def format_candidates_for_llm(
    candidates: list[tuple[object, float]],
    limit: int = 300,
) -> str:
    """Format scored candidates as compact pipe-delimited text for LLM context.

    Format: ID|Title|Artist|Genre|Energy|Tempo|Dance|Valence
    Missing features shown as "?".

    Args:
        candidates: List of (Track, score) tuples.
        limit: Maximum number of tracks to include.

    Returns:
        Pipe-delimited string with header row.
    """
    lines = ["ID|Title|Artist|Genre|Energy|Tempo|Dance|Valence"]

    for track, _score in candidates[:limit]:
        energy = getattr(track, "energy", None)
        tempo = getattr(track, "tempo", None)
        dance = getattr(track, "danceability", None)
        valence = getattr(track, "valence", None)

        # Truncate long strings
        title = (getattr(track, "title", "") or "")[:40]
        artist = (getattr(track, "artist", "") or "")[:30]
        genre = (getattr(track, "genre", "") or "")[:20]

        lines.append(
            f"{track.id}|{title}|{artist}|{genre}"
            f"|{_fmt(energy)}|{_fmt(tempo)}|{_fmt(dance)}|{_fmt(valence)}"
        )

    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    """Format a float value or return '?' for None."""
    if value is None:
        return "?"
    return f"{value:.2f}"
