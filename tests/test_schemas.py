from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas import FeatureCriteria, TrackSelection


class TestFeatureCriteria:
    """Tests for FeatureCriteria Pydantic model."""

    def test_feature_criteria_valid(self):
        """FeatureCriteria with valid ranges creates successfully."""
        criteria = FeatureCriteria(
            energy_min=0.2,
            energy_max=0.8,
            tempo_min=80.0,
            tempo_max=140.0,
            danceability_min=0.3,
            danceability_max=0.9,
            valence_min=0.1,
            valence_max=0.7,
            genres=["rock", "pop"],
            artists=["Artist A"],
            exclude_genres=["metal"],
            explanation="Chill vibes",
        )
        assert criteria.energy_min == 0.2
        assert criteria.energy_max == 0.8
        assert criteria.tempo_min == 80.0
        assert criteria.tempo_max == 140.0
        assert criteria.danceability_min == 0.3
        assert criteria.danceability_max == 0.9
        assert criteria.valence_min == 0.1
        assert criteria.valence_max == 0.7
        assert criteria.genres == ["rock", "pop"]
        assert criteria.artists == ["Artist A"]
        assert criteria.exclude_genres == ["metal"]
        assert criteria.explanation == "Chill vibes"

    def test_feature_criteria_defaults(self):
        """FeatureCriteria uses sensible defaults for lists."""
        criteria = FeatureCriteria(
            energy_min=0.0,
            energy_max=1.0,
            tempo_min=40.0,
            tempo_max=220.0,
            danceability_min=0.0,
            danceability_max=1.0,
            valence_min=0.0,
            valence_max=1.0,
            explanation="Full range",
        )
        assert criteria.genres == []
        assert criteria.artists == []
        assert criteria.exclude_genres == []

    def test_feature_criteria_out_of_range_energy(self):
        """FeatureCriteria rejects energy_min < 0."""
        with pytest.raises(ValidationError):
            FeatureCriteria(
                energy_min=-1.0,
                energy_max=0.5,
                tempo_min=80.0,
                tempo_max=140.0,
                danceability_min=0.3,
                danceability_max=0.9,
                valence_min=0.1,
                valence_max=0.7,
                explanation="Bad range",
            )

    def test_feature_criteria_out_of_range_tempo(self):
        """FeatureCriteria rejects tempo_max > 220."""
        with pytest.raises(ValidationError):
            FeatureCriteria(
                energy_min=0.2,
                energy_max=0.8,
                tempo_min=80.0,
                tempo_max=300.0,
                danceability_min=0.3,
                danceability_max=0.9,
                valence_min=0.1,
                valence_max=0.7,
                explanation="Bad tempo",
            )

    def test_feature_criteria_min_gt_max(self):
        """FeatureCriteria rejects energy_min > energy_max."""
        with pytest.raises(ValidationError):
            FeatureCriteria(
                energy_min=0.9,
                energy_max=0.2,
                tempo_min=80.0,
                tempo_max=140.0,
                danceability_min=0.3,
                danceability_max=0.9,
                valence_min=0.1,
                valence_max=0.7,
                explanation="Min > max",
            )

    def test_feature_criteria_tempo_min_gt_max(self):
        """FeatureCriteria rejects tempo_min > tempo_max."""
        with pytest.raises(ValidationError):
            FeatureCriteria(
                energy_min=0.2,
                energy_max=0.8,
                tempo_min=180.0,
                tempo_max=80.0,
                danceability_min=0.3,
                danceability_max=0.9,
                valence_min=0.1,
                valence_max=0.7,
                explanation="Tempo min > max",
            )


class TestTrackSelection:
    """Tests for TrackSelection Pydantic model."""

    def test_track_selection_valid(self):
        """TrackSelection with track_ids and explanation creates successfully."""
        selection = TrackSelection(
            track_ids=[1, 2, 3, 42],
            explanation="These tracks match the energetic morning vibe",
        )
        assert selection.track_ids == [1, 2, 3, 42]
        assert "energetic" in selection.explanation
