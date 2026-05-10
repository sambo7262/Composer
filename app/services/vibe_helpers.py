"""Vibe display helpers (D-11).

A vibe's centroid is the raw 4-D mean over its assigned tracks (energy, tempo,
danceability, valence). The chip is the human-readable text rendering of that
centroid — what the user sees on the proposal card and on /debug/vibes.

Per UI-SPEC §"Audio-feature chip color":
- The chip is text-only (no background fill, no pill, no icon).
- Color: ``text-text-secondary`` (Tailwind token); applied at the template
  layer, not in this module.
- Separator: U+00B7 middle dot (``·``), surrounded by single spaces.

Threshold rules (chosen for legibility; documented for verifier scrutiny):

==========  =========  ============  ==========
dimension   low band   mid band      high band
==========  =========  ============  ==========
energy        <0.33    0.33–0.66       >0.66
tempo (BPM)   <90      90–130          >130
danceability  <0.33    0.33–0.66       >0.66
valence       <0.33    0.33–0.66       >0.66
==========  =========  ============  ==========

Boundary convention: ``< low_threshold`` is "Low"; ``< high_threshold`` is
"Mid"; everything else is "High" (or "Fast" for tempo, "Slow" for low).

Sentinel: ``None`` centroid (no clustering yet) and an empty dict both return
the literal string ``"Unknown"``. A partial dict (one or two dimensions
present) renders only the present dimensions, joined by ``" · "`` — no
orphan separators.

Examples:
    >>> feature_chip_text(
    ...     {"energy": 0.85, "tempo": 145.0, "danceability": 0.32, "valence": 0.55}
    ... )
    'High energy · Fast tempo · Low danceability · Mid valence'
    >>> feature_chip_text(
    ...     {"energy": 0.20, "tempo": 70.0, "danceability": 0.78, "valence": 0.20}
    ... )
    'Low energy · Slow tempo · High danceability · Low valence'
    >>> feature_chip_text(
    ...     {"energy": 0.50, "tempo": 100.0, "danceability": 0.50, "valence": 0.50}
    ... )
    'Mid energy · Mid tempo · Mid danceability · Mid valence'
    >>> feature_chip_text(None)
    'Unknown'
    >>> feature_chip_text({})
    'Unknown'
    >>> feature_chip_text({"energy": 0.85})
    'High energy'

This module is pure: no I/O, no DB, no PlexAPI, no LLM. The Phase 6 D-33
sklearn allowlist test (``tests/test_vibe_clusterer.py``) explicitly forbids
sklearn imports here — clustering math lives in ``vibe_clusterer.py`` only.
"""
from __future__ import annotations

from typing import Optional

# Threshold constants (module-level; any future tuning happens here so the
# values stay one place).
_ENERGY_LOW = 0.33
_ENERGY_HIGH = 0.66
_TEMPO_LOW = 90.0   # BPM
_TEMPO_HIGH = 130.0  # BPM
_DANCE_LOW = 0.33
_DANCE_HIGH = 0.66
_VALENCE_LOW = 0.33
_VALENCE_HIGH = 0.66

_SEPARATOR = " · "  # U+00B7 middle dot, single-spaced both sides
_UNKNOWN = "Unknown"


def _band_zero_one(value: float, low: float, high: float) -> str:
    """Bucket a 0-1 audio feature into Low / Mid / High."""
    if value < low:
        return "Low"
    if value < high:
        return "Mid"
    return "High"


def _band_tempo(bpm: float) -> str:
    """Bucket a BPM value into Slow / Mid / Fast."""
    if bpm < _TEMPO_LOW:
        return "Slow"
    if bpm < _TEMPO_HIGH:
        return "Mid"
    return "Fast"


def feature_chip_text(centroid: Optional[dict]) -> str:
    """Render a vibe centroid as a human-readable chip text string.

    Per D-11 / UI-SPEC §"Audio-feature chip color". See module docstring for
    threshold rules and examples.
    """
    if not centroid:
        return _UNKNOWN

    parts: list[str] = []

    if "energy" in centroid and centroid["energy"] is not None:
        parts.append(f"{_band_zero_one(centroid['energy'], _ENERGY_LOW, _ENERGY_HIGH)} energy")
    if "tempo" in centroid and centroid["tempo"] is not None:
        parts.append(f"{_band_tempo(centroid['tempo'])} tempo")
    if "danceability" in centroid and centroid["danceability"] is not None:
        parts.append(
            f"{_band_zero_one(centroid['danceability'], _DANCE_LOW, _DANCE_HIGH)} danceability"
        )
    if "valence" in centroid and centroid["valence"] is not None:
        parts.append(
            f"{_band_zero_one(centroid['valence'], _VALENCE_LOW, _VALENCE_HIGH)} valence"
        )

    if not parts:
        return _UNKNOWN

    return _SEPARATOR.join(parts)
