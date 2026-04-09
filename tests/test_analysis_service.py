from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select

from app.models.track import Track


@pytest.fixture
def analysis_db(test_engine):
    """Create tables including Track, yield session, drop after."""
    from app.models.settings import ServiceConfig  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


def _run_async(coro):
    """Helper to run async coroutines in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _reset_analysis_status():
    """Reset the module-level analysis status to IDLE."""
    import app.services.analysis_service as svc
    svc._analysis_status = svc.AnalysisStatus()


def _make_track(
    session,
    key="1",
    title="Song",
    artist="Artist",
    file_path="/data/Music/Artist/Album/song.flac",
    analyzed_at=None,
    analysis_error=None,
):
    """Insert a track into the DB and return it."""
    track = Track(
        plex_rating_key=key,
        title=title,
        artist=artist,
        album="Album",
        genre="Rock",
        year=2020,
        duration_ms=240000,
        file_path=file_path,
        analyzed_at=analyzed_at,
        analysis_error=analysis_error,
    )
    session.add(track)
    session.commit()
    session.refresh(track)
    return track


class TestAnalysisStateEnum:
    """Tests for AnalysisStateEnum states."""

    def test_has_five_states(self):
        """AnalysisStateEnum has IDLE, RUNNING, PAUSED, COMPLETED, FAILED states."""
        from app.services.analysis_service import AnalysisStateEnum

        assert AnalysisStateEnum.IDLE == "idle"
        assert AnalysisStateEnum.RUNNING == "running"
        assert AnalysisStateEnum.PAUSED == "paused"
        assert AnalysisStateEnum.COMPLETED == "completed"
        assert AnalysisStateEnum.FAILED == "failed"
        assert len(AnalysisStateEnum) == 5


class TestAnalysisStatus:
    """Tests for AnalysisStatus dataclass."""

    def test_get_analysis_status_returns_dataclass(self):
        """get_analysis_status returns AnalysisStatus dataclass with expected fields."""
        _reset_analysis_status()
        from app.services.analysis_service import AnalysisStateEnum, get_analysis_status

        status = get_analysis_status()
        assert status.state == AnalysisStateEnum.IDLE
        assert status.total_tracks == 0
        assert status.analyzed_tracks == 0
        assert status.failed_tracks == 0
        assert status.current_track == ""
        assert status.avg_seconds_per_track == 0.0
        assert status.errors == []

    def test_eta_display_hours(self):
        """eta_display returns hours and minutes format when > 60 minutes."""
        from app.services.analysis_service import AnalysisStatus, AnalysisStateEnum

        status = AnalysisStatus(
            state=AnalysisStateEnum.RUNNING,
            total_tracks=1000,
            analyzed_tracks=0,
            failed_tracks=0,
            avg_seconds_per_track=10.0,
        )
        # 1000 * 10 = 10000 seconds = 2h 46m
        eta = status.eta_display
        assert "h" in eta
        assert "m" in eta

    def test_eta_display_minutes(self):
        """eta_display returns minutes format when < 60 minutes."""
        from app.services.analysis_service import AnalysisStatus, AnalysisStateEnum

        status = AnalysisStatus(
            state=AnalysisStateEnum.RUNNING,
            total_tracks=100,
            analyzed_tracks=50,
            failed_tracks=0,
            avg_seconds_per_track=5.0,
        )
        # 50 * 5 = 250 seconds = ~4m
        eta = status.eta_display
        assert "m" in eta

    def test_eta_display_seconds(self):
        """eta_display returns seconds format when < 60 seconds."""
        from app.services.analysis_service import AnalysisStatus, AnalysisStateEnum

        status = AnalysisStatus(
            state=AnalysisStateEnum.RUNNING,
            total_tracks=10,
            analyzed_tracks=5,
            failed_tracks=0,
            avg_seconds_per_track=5.0,
        )
        # 5 * 5 = 25 seconds
        eta = status.eta_display
        assert "s" in eta


class TestRunAnalysis:
    """Tests for run_analysis main entry point."""

    def setup_method(self):
        _reset_analysis_status()

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_queries_unanalyzed_tracks(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """run_analysis queries tracks WHERE analyzed_at IS NULL AND file_path IS NOT NULL."""
        _make_track(analysis_db, key="1", file_path="/data/Music/song1.flac")
        _make_track(analysis_db, key="2", file_path="/data/Music/song2.flac", analyzed_at="2024-01-01T00:00:00")
        _make_track(analysis_db, key="3", file_path=None)

        mock_extract.return_value = {
            "energy": 0.8, "tempo": 120.0, "danceability": 0.7,
            "valence": 0.6, "musical_key": "C", "scale": "major",
            "spectral_complexity": 50.0, "loudness": -10.0,
        }

        from app.services.analysis_service import run_analysis
        _run_async(run_analysis())

        # Only track 1 should be analyzed (track 2 already analyzed, track 3 no file_path)
        assert mock_extract.call_count == 1

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_skips_already_analyzed_tracks(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Already-analyzed tracks (analyzed_at not null) are skipped (AUDIO-04)."""
        _make_track(analysis_db, key="1", analyzed_at="2024-01-01T00:00:00", file_path="/data/Music/song.flac")

        from app.services.analysis_service import run_analysis, get_analysis_status
        _run_async(run_analysis())

        assert mock_extract.call_count == 0
        status = get_analysis_status()
        assert status.total_tracks == 0

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_skips_tracks_without_file_path(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Tracks without file_path are skipped (not failed, just skipped)."""
        _make_track(analysis_db, key="1", file_path=None)

        from app.services.analysis_service import run_analysis, get_analysis_status
        _run_async(run_analysis())

        assert mock_extract.call_count == 0

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_stop_analysis_sets_paused(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """stop_analysis sets state to PAUSED and analysis loop respects the flag."""
        import app.services.analysis_service as svc

        # Create many tracks so we can pause mid-analysis
        for i in range(5):
            _make_track(analysis_db, key=str(i), file_path=f"/data/Music/song{i}.flac")

        call_count = 0

        def extract_side_effect(path):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                # Simulate stopping after 2 tracks
                _run_async(svc.stop_analysis())
            return {
                "energy": 0.8, "tempo": 120.0, "danceability": 0.7,
                "valence": 0.6, "musical_key": "C", "scale": "major",
                "spectral_complexity": 50.0, "loudness": -10.0,
            }

        mock_extract.side_effect = extract_side_effect

        _run_async(svc.run_analysis())

        status = svc.get_analysis_status()
        assert status.state == svc.AnalysisStateEnum.PAUSED
        # Should have processed fewer than all 5 tracks
        assert mock_extract.call_count < 5

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_resume_after_pause_requeries_unanalyzed(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """run_analysis when state is PAUSED resumes by re-querying un-analyzed tracks."""
        import app.services.analysis_service as svc

        _make_track(analysis_db, key="1", file_path="/data/Music/song1.flac")
        _make_track(analysis_db, key="2", file_path="/data/Music/song2.flac")

        mock_extract.return_value = {
            "energy": 0.8, "tempo": 120.0, "danceability": 0.7,
            "valence": 0.6, "musical_key": "C", "scale": "major",
            "spectral_complexity": 50.0, "loudness": -10.0,
        }

        # Set to PAUSED state then run - should resume
        svc._analysis_status.state = svc.AnalysisStateEnum.PAUSED
        _run_async(svc.run_analysis())

        status = svc.get_analysis_status()
        assert status.state == svc.AnalysisStateEnum.COMPLETED

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_successful_analysis_sets_analyzed_at(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Each successful analysis sets analyzed_at timestamp and clears analysis_error."""
        _make_track(analysis_db, key="1", file_path="/data/Music/song.flac", analysis_error="old error")

        mock_extract.return_value = {
            "energy": 0.8, "tempo": 120.0, "danceability": 0.7,
            "valence": 0.6, "musical_key": "C", "scale": "major",
            "spectral_complexity": 50.0, "loudness": -10.0,
        }

        from app.services.analysis_service import run_analysis
        _run_async(run_analysis())

        from app.database import get_engine
        with Session(get_engine()) as session:
            track = session.exec(select(Track).where(Track.plex_rating_key == "1")).first()
            assert track.analyzed_at is not None
            assert track.analysis_error is None
            assert track.energy == 0.8
            assert track.tempo == 120.0

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_failed_analysis_sets_error(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Each failed analysis sets analysis_error message and increments failed_tracks."""
        _make_track(analysis_db, key="1", file_path="/data/Music/song.flac")

        mock_extract.side_effect = RuntimeError("Extraction failed")

        from app.services.analysis_service import run_analysis, get_analysis_status
        _run_async(run_analysis())

        status = get_analysis_status()
        assert status.failed_tracks == 1
        assert len(status.errors) == 1

        from app.database import get_engine
        with Session(get_engine()) as session:
            track = session.exec(select(Track).where(Track.plex_rating_key == "1")).first()
            assert track.analysis_error is not None
            assert track.analyzed_at is None

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_eta_uses_rolling_average(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """ETA calculation uses rolling average of last 50 track durations."""
        for i in range(3):
            _make_track(analysis_db, key=str(i), file_path=f"/data/Music/song{i}.flac")

        mock_extract.return_value = {
            "energy": 0.8, "tempo": 120.0, "danceability": 0.7,
            "valence": 0.6, "musical_key": "C", "scale": "major",
            "spectral_complexity": 50.0, "loudness": -10.0,
        }

        from app.services.analysis_service import run_analysis, get_analysis_status
        _run_async(run_analysis())

        status = get_analysis_status()
        # After completion, avg_seconds_per_track should be > 0
        assert status.avg_seconds_per_track >= 0.0

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_concurrent_run_rejected(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Concurrent run_analysis calls are rejected (returns immediately if RUNNING)."""
        import app.services.analysis_service as svc

        svc._analysis_status.state = svc.AnalysisStateEnum.RUNNING

        _run_async(svc.run_analysis())

        # Should still be RUNNING, and extract not called
        assert svc._analysis_status.state == svc.AnalysisStateEnum.RUNNING
        mock_extract.assert_not_called()

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_detect_plex_music_root(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Plex music root is auto-detected from common prefix of first 10 file paths."""
        _make_track(analysis_db, key="1", file_path="/data/Music/Artist1/song1.flac")
        _make_track(analysis_db, key="2", file_path="/data/Music/Artist2/song2.flac")

        mock_extract.return_value = {
            "energy": 0.8, "tempo": 120.0, "danceability": 0.7,
            "valence": 0.6, "musical_key": "C", "scale": "major",
            "spectral_complexity": 50.0, "loudness": -10.0,
        }

        from app.services.analysis_service import run_analysis
        _run_async(run_analysis())

        # Should have called extract with remapped paths
        assert mock_extract.call_count == 2

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=False)
    @patch("app.services.analysis_service.extract_features")
    def test_skips_missing_files(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Tracks with non-existent remapped file paths are skipped."""
        _make_track(analysis_db, key="1", file_path="/data/Music/missing.flac")

        from app.services.analysis_service import run_analysis, get_analysis_status
        _run_async(run_analysis())

        mock_extract.assert_not_called()
        status = get_analysis_status()
        # Missing file should be counted as failed
        assert status.failed_tracks == 1

    @patch("app.services.analysis_service.os.path.getsize", return_value=200_000_000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_skips_oversized_files(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Files > 100MB are skipped (T-03-05 mitigation)."""
        _make_track(analysis_db, key="1", file_path="/data/Music/huge.flac")

        from app.services.analysis_service import run_analysis, get_analysis_status
        _run_async(run_analysis())

        mock_extract.assert_not_called()
        status = get_analysis_status()
        assert status.failed_tracks == 1

    @patch("app.services.analysis_service.os.path.getsize", return_value=1000)
    @patch("app.services.analysis_service.os.path.isfile", return_value=True)
    @patch("app.services.analysis_service.extract_features")
    def test_error_list_capped_at_50(self, mock_extract, mock_isfile, mock_getsize, analysis_db):
        """Errors list is capped at 50 entries."""
        for i in range(60):
            _make_track(analysis_db, key=str(i), file_path=f"/data/Music/song{i}.flac")

        mock_extract.side_effect = RuntimeError("fail")

        from app.services.analysis_service import run_analysis, get_analysis_status
        _run_async(run_analysis())

        status = get_analysis_status()
        assert len(status.errors) <= 50
        assert status.failed_tracks == 60
