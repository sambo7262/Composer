from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestComputeValenceProxy:
    """Tests for compute_valence_proxy function."""

    def test_returns_float_in_valid_range(self):
        """compute_valence_proxy returns float in [0.0, 1.0]."""
        from app.services.audio_analyzer import compute_valence_proxy

        result = compute_valence_proxy(
            scale="major",
            spectral_centroid=2500.0,
            danceability=1.5,
            pitch_salience=0.6,
        )
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_major_higher_than_minor(self):
        """Major scale produces higher valence than minor with same params."""
        from app.services.audio_analyzer import compute_valence_proxy

        major = compute_valence_proxy(
            scale="major", spectral_centroid=2500.0,
            danceability=1.5, pitch_salience=0.6,
        )
        minor = compute_valence_proxy(
            scale="minor", spectral_centroid=2500.0,
            danceability=1.5, pitch_salience=0.6,
        )
        assert major > minor

    def test_clamps_to_valid_range_extreme_inputs(self):
        """compute_valence_proxy clamps to [0.0, 1.0] even with extreme inputs."""
        from app.services.audio_analyzer import compute_valence_proxy

        # Very high values
        high = compute_valence_proxy(
            scale="major", spectral_centroid=99999.0,
            danceability=10.0, pitch_salience=5.0,
        )
        assert high <= 1.0

        # Very low / negative values
        low = compute_valence_proxy(
            scale="minor", spectral_centroid=-100.0,
            danceability=0.0, pitch_salience=-1.0,
        )
        assert low >= 0.0

    def test_returns_rounded_to_four_decimals(self):
        """compute_valence_proxy rounds result to 4 decimal places."""
        from app.services.audio_analyzer import compute_valence_proxy

        result = compute_valence_proxy(
            scale="major", spectral_centroid=1234.5678,
            danceability=1.2345, pitch_salience=0.5678,
        )
        # Check that multiplying by 10000 gives an integer (4 decimal places)
        assert result == round(result, 4)


class TestRemapPlexPath:
    """Tests for remap_plex_path function."""

    def test_remaps_matching_prefix(self):
        """remap_plex_path strips prefix and prepends container mount."""
        from app.services.audio_analyzer import remap_plex_path

        result = remap_plex_path(
            "/data/Music/Artist/Album/song.flac", "/data/Music"
        )
        assert result == "/music/Artist/Album/song.flac"

    def test_fallback_on_non_matching_prefix(self):
        """remap_plex_path falls back to filename when prefix doesn't match."""
        from app.services.audio_analyzer import remap_plex_path

        result = remap_plex_path(
            "/other/path/Artist/song.flac", "/data/Music"
        )
        assert result == "/music/song.flac"

    def test_rejects_path_traversal(self):
        """remap_plex_path raises ValueError on paths containing '..'."""
        from app.services.audio_analyzer import remap_plex_path

        with pytest.raises(ValueError, match="path traversal"):
            remap_plex_path("/data/Music/../../../etc/passwd", "/data/Music")

    def test_custom_container_mount(self):
        """remap_plex_path uses custom container_mount."""
        from app.services.audio_analyzer import remap_plex_path

        result = remap_plex_path(
            "/data/Music/Artist/song.flac", "/data/Music",
            container_mount="/mnt/music",
        )
        assert result == "/mnt/music/Artist/song.flac"


class TestMetadataFeatureVector:
    """Tests for metadata_feature_vector function."""

    def test_returns_dict_with_expected_keys(self):
        """metadata_feature_vector returns dict with all 8 feature keys."""
        from app.services.audio_analyzer import metadata_feature_vector

        result = metadata_feature_vector("rock", 2020)
        expected_keys = {
            "energy", "tempo", "danceability", "valence",
            "musical_key", "scale", "spectral_complexity", "loudness",
        }
        assert set(result.keys()) == expected_keys

    def test_metal_higher_energy_than_ambient(self):
        """Metal genre has higher energy than ambient."""
        from app.services.audio_analyzer import metadata_feature_vector

        metal = metadata_feature_vector("metal", 2020)
        ambient = metadata_feature_vector("ambient", 2020)
        assert metal["energy"] > ambient["energy"]

    def test_era_boost_for_1980s(self):
        """1980s tracks get a valence boost."""
        from app.services.audio_analyzer import metadata_feature_vector

        eighties = metadata_feature_vector("pop", 1985)
        modern = metadata_feature_vector("pop", 2020)
        assert eighties["valence"] > modern["valence"]

    def test_none_genre_uses_default(self):
        """None genre falls back to default energy (0.5)."""
        from app.services.audio_analyzer import metadata_feature_vector

        result = metadata_feature_vector(None, 2020)
        assert result["energy"] == 0.5

    def test_none_fields_for_unknown_metadata(self):
        """tempo, musical_key, scale, spectral_complexity, loudness are None."""
        from app.services.audio_analyzer import metadata_feature_vector

        result = metadata_feature_vector("rock", 2020)
        assert result["tempo"] is None
        assert result["musical_key"] is None
        assert result["scale"] is None
        assert result["spectral_complexity"] is None
        assert result["loudness"] is None


class TestExtractFeatures:
    """Tests for extract_features function (Essentia mocked)."""

    def _make_mock_pool(self):
        """Create a mock feature pool mimicking MusicExtractor output."""
        pool = {
            "rhythm.bpm": 120.0,
            "rhythm.danceability": 1.8,
            "tonal.key_edma.key": "C",
            "tonal.key_edma.scale": "major",
            "lowlevel.spectral_rms.mean": 0.15,
            "lowlevel.loudness_ebu128.integrated": -14.0,
            "lowlevel.spectral_complexity.mean": 8.5,
            "lowlevel.spectral_centroid.mean": 2500.0,
            "lowlevel.pitch_salience.mean": 0.65,
        }

        class FakePool:
            def __getitem__(self, key):
                return pool[key]

        return FakePool()

    @patch.dict("sys.modules", {"essentia": MagicMock(), "essentia.standard": MagicMock()})
    def test_returns_all_feature_keys(self):
        """extract_features returns dict with all 8 feature keys."""
        import sys

        mock_es = sys.modules["essentia.standard"]
        mock_extractor_instance = MagicMock()
        mock_extractor_instance.return_value = (self._make_mock_pool(), None)
        mock_es.MusicExtractor.return_value = mock_extractor_instance

        from importlib import reload

        import app.services.audio_analyzer as analyzer_mod
        reload(analyzer_mod)

        result = analyzer_mod.extract_features("/fake/path/song.flac")
        expected_keys = {
            "energy", "tempo", "danceability", "valence",
            "musical_key", "scale", "spectral_complexity", "loudness",
        }
        assert set(result.keys()) == expected_keys

    @patch.dict("sys.modules", {"essentia": MagicMock(), "essentia.standard": MagicMock()})
    def test_normalizes_danceability(self):
        """extract_features normalizes danceability from [0,3] to [0,1]."""
        import sys

        mock_es = sys.modules["essentia.standard"]
        mock_extractor_instance = MagicMock()
        mock_extractor_instance.return_value = (self._make_mock_pool(), None)
        mock_es.MusicExtractor.return_value = mock_extractor_instance

        from importlib import reload

        import app.services.audio_analyzer as analyzer_mod
        reload(analyzer_mod)

        result = analyzer_mod.extract_features("/fake/path/song.flac")
        # Original danceability is 1.8, normalized = 1.8/3.0 = 0.6
        assert 0.0 <= result["danceability"] <= 1.0
        assert abs(result["danceability"] - 0.6) < 0.01

    @patch.dict("sys.modules", {"essentia": MagicMock(), "essentia.standard": MagicMock()})
    def test_returns_correct_key_and_scale(self):
        """extract_features returns musical_key and scale from Essentia."""
        import sys

        mock_es = sys.modules["essentia.standard"]
        mock_extractor_instance = MagicMock()
        mock_extractor_instance.return_value = (self._make_mock_pool(), None)
        mock_es.MusicExtractor.return_value = mock_extractor_instance

        from importlib import reload

        import app.services.audio_analyzer as analyzer_mod
        reload(analyzer_mod)

        result = analyzer_mod.extract_features("/fake/path/song.flac")
        assert result["musical_key"] == "C"
        assert result["scale"] == "major"
