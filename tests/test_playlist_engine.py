"""Tests for the playlist scoring engine."""

from __future__ import annotations

from app.models.schemas import FeatureCriteria
from app.services.playlist_engine import (
    format_candidates_for_llm,
    score_track,
)


def _make_criteria(**overrides) -> FeatureCriteria:
    """Helper to create FeatureCriteria with defaults."""
    defaults = dict(
        energy_min=0.4,
        energy_max=0.6,
        tempo_min=100.0,
        tempo_max=140.0,
        danceability_min=0.4,
        danceability_max=0.6,
        valence_min=0.4,
        valence_max=0.6,
        explanation="test criteria",
    )
    defaults.update(overrides)
    return FeatureCriteria(**defaults)


class _MockTrack:
    """Lightweight mock matching Track model attributes needed by score_track."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 1)
        self.title = kwargs.get("title", "Test Song")
        self.artist = kwargs.get("artist", "Test Artist")
        self.album = kwargs.get("album", "Test Album")
        self.genre = kwargs.get("genre", "rock")
        self.year = kwargs.get("year", 2020)
        self.duration_ms = kwargs.get("duration_ms", 240000)
        self.energy = kwargs.get("energy", None)
        self.tempo = kwargs.get("tempo", None)
        self.danceability = kwargs.get("danceability", None)
        self.valence = kwargs.get("valence", None)


class TestScoreTrack:
    """Tests for score_track function."""

    def test_score_track_perfect_match(self):
        """Track at criteria midpoint scores near 0."""
        # Criteria midpoint: energy=0.5, tempo=120, dance=0.5, valence=0.5
        # Energy stored raw from Essentia: 0.5 * 0.3 = 0.15 (normalized back to 0.5)
        track = _MockTrack(
            energy=0.15,  # 0.15 / 0.3 = 0.5 normalized
            tempo=120.0,  # (120-40)/180 = 0.444 ~ midpoint of (100-40)/180=0.333 to (140-40)/180=0.556
            danceability=0.5,
            valence=0.5,
        )
        criteria = _make_criteria()
        score = score_track(track, criteria)
        # Should be close to 0 (perfect match)
        assert score < 0.3, f"Perfect match score should be low, got {score}"

    def test_score_track_outside_range(self):
        """Track outside criteria range scores higher due to 2x penalty."""
        # Track with very high energy (outside 0.4-0.6 range)
        track_outside = _MockTrack(
            energy=0.3,  # 0.3/0.3=1.0 normalized, way above 0.6 max
            tempo=200.0,  # (200-40)/180=0.889, well above range
            danceability=0.9,
            valence=0.9,
        )
        # Track within range
        track_inside = _MockTrack(
            energy=0.15,  # 0.5 normalized
            tempo=120.0,
            danceability=0.5,
            valence=0.5,
        )
        criteria = _make_criteria()
        score_out = score_track(track_outside, criteria)
        score_in = score_track(track_inside, criteria)
        assert score_out > score_in, (
            f"Outside-range track ({score_out}) should score higher than inside ({score_in})"
        )

    def test_score_track_no_features(self):
        """Track with all None features uses metadata fallback or returns high score."""
        track = _MockTrack(
            energy=None,
            tempo=None,
            danceability=None,
            valence=None,
            genre="rock",
            year=2020,
        )
        criteria = _make_criteria()
        score = score_track(track, criteria)
        # Should get a score (metadata fallback), not crash
        assert 0.0 <= score <= 2.0, f"Score should be valid, got {score}"

    def test_score_track_energy_normalization(self):
        """Track with energy=0.15 (raw Essentia) scored correctly against criteria energy_mid=0.5."""
        track = _MockTrack(
            energy=0.15,  # 0.15 / 0.3 = 0.5 normalized -- matches midpoint
            tempo=None,
            danceability=None,
            valence=None,
        )
        criteria = _make_criteria(energy_min=0.4, energy_max=0.6)
        score = score_track(track, criteria)
        # With only energy feature and it matching the midpoint, score should be low
        assert score < 0.2, f"Energy-matched track should score low, got {score}"

    def test_score_track_partial_features(self):
        """Track with only some features scores based on available ones."""
        track = _MockTrack(
            energy=0.15,
            tempo=120.0,
            danceability=None,
            valence=None,
        )
        criteria = _make_criteria()
        score = score_track(track, criteria)
        assert 0.0 <= score <= 2.0


class TestFormatCandidates:
    """Tests for format_candidates_for_llm function."""

    def test_format_candidates_compact(self):
        """Output is pipe-delimited with header row."""
        track1 = _MockTrack(id=1, title="Song A", artist="Artist X", genre="rock",
                            energy=0.15, tempo=120.0, danceability=0.5, valence=0.6)
        track2 = _MockTrack(id=2, title="Song B", artist="Artist Y", genre="pop",
                            energy=None, tempo=None, danceability=None, valence=None)
        candidates = [(track1, 0.1), (track2, 0.5)]
        output = format_candidates_for_llm(candidates)
        lines = output.strip().split("\n")
        # First line is header
        assert "ID" in lines[0]
        assert "|" in lines[0]
        # Should have header + 2 data rows
        assert len(lines) == 3
        # Track with missing features uses "?"
        assert "?" in lines[2]

    def test_format_candidates_limit(self):
        """Output respects the limit parameter."""
        tracks = [
            (_MockTrack(id=i, title=f"Song {i}", artist="A", genre="rock",
                        energy=0.1, tempo=100.0, danceability=0.5, valence=0.5), 0.1)
            for i in range(10)
        ]
        output = format_candidates_for_llm(tracks, limit=5)
        lines = output.strip().split("\n")
        # header + 5 data rows
        assert len(lines) == 6
