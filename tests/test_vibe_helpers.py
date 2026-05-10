"""Tests for app/services/vibe_helpers.py — centroid → human-readable chip text (D-11).

Pure helper, single function: ``feature_chip_text(centroid: Optional[dict]) -> str``.

Threshold rules (documented in vibe_helpers.py):
- energy:        <0.33 = "Low",  0.33-0.66 = "Mid",  >0.66 = "High"
- tempo:         <90  BPM = "Slow", 90-130 = "Mid",  >130  = "Fast"
- danceability:  <0.33 = "Low",  0.33-0.66 = "Mid",  >0.66 = "High"
- valence:       <0.33 = "Low",  0.33-0.66 = "Mid",  >0.66 = "High"

Separator is the U+00B7 middle dot (·) per UI-SPEC §"Audio-feature chip color".
None / empty dict / unknown values → "Unknown" sentinel string.
"""
from __future__ import annotations


def test_high_fast_low_mid():
    """High energy, fast tempo, low danceability, mid valence — full chip."""
    from app.services.vibe_helpers import feature_chip_text

    chip = feature_chip_text(
        {"energy": 0.85, "tempo": 145.0, "danceability": 0.32, "valence": 0.55}
    )
    assert chip == "High energy · Fast tempo · Low danceability · Mid valence"


def test_low_slow_high_low():
    """Low energy, slow tempo, high danceability, low valence."""
    from app.services.vibe_helpers import feature_chip_text

    chip = feature_chip_text(
        {"energy": 0.20, "tempo": 70.0, "danceability": 0.78, "valence": 0.20}
    )
    assert chip == "Low energy · Slow tempo · High danceability · Low valence"


def test_all_mid_band():
    """All four dimensions in the mid band."""
    from app.services.vibe_helpers import feature_chip_text

    chip = feature_chip_text(
        {"energy": 0.50, "tempo": 100.0, "danceability": 0.50, "valence": 0.50}
    )
    assert chip == "Mid energy · Mid tempo · Mid danceability · Mid valence"


def test_none_centroid_returns_unknown():
    """None centroid (no clustering yet) → 'Unknown' sentinel."""
    from app.services.vibe_helpers import feature_chip_text

    assert feature_chip_text(None) == "Unknown"


def test_empty_dict_returns_unknown():
    """Empty dict → 'Unknown' sentinel (no dimensions present)."""
    from app.services.vibe_helpers import feature_chip_text

    assert feature_chip_text({}) == "Unknown"


def test_partial_dict_skips_missing_dimensions():
    """Single dimension present → just that dimension; no orphan separator."""
    from app.services.vibe_helpers import feature_chip_text

    chip = feature_chip_text({"energy": 0.85})
    assert chip == "High energy"
    # No orphan trailing/leading separator
    assert not chip.startswith(" · ")
    assert not chip.endswith(" · ")


def test_separator_is_middle_dot_not_pipe_or_comma():
    """The separator MUST be U+00B7 middle dot, not '|' or ','.

    UI-SPEC §"Audio-feature chip color" pins the separator. Defensive guard
    against accidental ASCII fallback during refactors.
    """
    from app.services.vibe_helpers import feature_chip_text

    chip = feature_chip_text({"energy": 0.85, "tempo": 145.0})
    assert "·" in chip  # middle dot present
    assert "|" not in chip
    # Allow no commas in the chip text itself
    assert "," not in chip
