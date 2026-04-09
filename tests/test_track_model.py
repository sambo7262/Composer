from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def db_with_tracks(test_engine):
    """Create tables including Track and SyncState, yield session, drop after."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


class TestTrackModel:
    """Tests for the Track SQLModel."""

    def test_create_track_with_all_fields(self, db_with_tracks):
        """Track model stores all required metadata fields."""
        from app.models.track import Track

        track = Track(
            plex_rating_key="12345",
            title="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            genre="Rock, Progressive Rock",
            year=1975,
            duration_ms=354000,
            added_at="2024-01-15T12:00:00",
            updated_at="2024-01-15T12:00:00",
            synced_at="2024-01-16T08:00:00",
        )
        db_with_tracks.add(track)
        db_with_tracks.commit()
        db_with_tracks.refresh(track)

        assert track.id is not None
        assert track.plex_rating_key == "12345"
        assert track.title == "Bohemian Rhapsody"
        assert track.artist == "Queen"
        assert track.album == "A Night at the Opera"
        assert track.genre == "Rock, Progressive Rock"
        assert track.year == 1975
        assert track.duration_ms == 354000
        assert track.added_at == "2024-01-15T12:00:00"
        assert track.updated_at == "2024-01-15T12:00:00"
        assert track.synced_at == "2024-01-16T08:00:00"

    def test_plex_rating_key_is_unique(self, db_with_tracks):
        """Inserting duplicate plex_rating_key raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        from app.models.track import Track

        track1 = Track(plex_rating_key="99999", title="Song A", artist="Artist A")
        db_with_tracks.add(track1)
        db_with_tracks.commit()

        track2 = Track(plex_rating_key="99999", title="Song B", artist="Artist B")
        db_with_tracks.add(track2)
        with pytest.raises(IntegrityError):
            db_with_tracks.commit()

    def test_title_and_artist_are_indexed(self, db_with_tracks):
        """Title and artist fields have database indexes."""
        from app.models.track import Track  # noqa: F401

        inspector = inspect(db_with_tracks.get_bind())
        indexes = inspector.get_indexes("track")
        indexed_columns = set()
        for idx in indexes:
            for col in idx["column_names"]:
                indexed_columns.add(col)

        assert "title" in indexed_columns
        assert "artist" in indexed_columns

    def test_track_default_values(self, db_with_tracks):
        """Track fields have correct defaults for optional fields."""
        from app.models.track import Track

        track = Track(plex_rating_key="11111", title="Minimal", artist="Test")
        db_with_tracks.add(track)
        db_with_tracks.commit()
        db_with_tracks.refresh(track)

        assert track.album == ""
        assert track.genre == ""
        assert track.year is None
        assert track.duration_ms == 0
        assert track.added_at is None
        assert track.synced_at is None

    def test_audio_feature_columns_exist(self, db_with_tracks):
        """Track model has all 11 new audio feature columns with correct defaults."""
        from app.models.track import Track

        track = Track(plex_rating_key="22222", title="Feature Test", artist="Test")
        db_with_tracks.add(track)
        db_with_tracks.commit()
        db_with_tracks.refresh(track)

        # All new fields should default to None
        assert track.file_path is None
        assert track.energy is None
        assert track.tempo is None
        assert track.danceability is None
        assert track.valence is None
        assert track.musical_key is None
        assert track.scale is None
        assert track.spectral_complexity is None
        assert track.loudness is None
        assert track.analyzed_at is None
        assert track.analysis_error is None

    def test_audio_feature_columns_indexed(self, db_with_tracks):
        """energy, danceability, valence columns have database indexes."""
        from app.models.track import Track  # noqa: F401

        inspector = inspect(db_with_tracks.get_bind())
        indexes = inspector.get_indexes("track")
        indexed_columns = set()
        for idx in indexes:
            for col in idx["column_names"]:
                indexed_columns.add(col)

        assert "energy" in indexed_columns
        assert "danceability" in indexed_columns
        assert "valence" in indexed_columns

    def test_track_with_audio_features(self, db_with_tracks):
        """Track model stores audio feature values correctly."""
        from app.models.track import Track

        track = Track(
            plex_rating_key="33333",
            title="Analyzed Song",
            artist="Test",
            file_path="/music/Artist/Album/song.flac",
            energy=0.75,
            tempo=120.5,
            danceability=0.68,
            valence=0.55,
            musical_key="C",
            scale="major",
            spectral_complexity=12.3,
            loudness=-8.5,
            analyzed_at="2026-04-09T12:00:00",
        )
        db_with_tracks.add(track)
        db_with_tracks.commit()
        db_with_tracks.refresh(track)

        assert track.file_path == "/music/Artist/Album/song.flac"
        assert track.energy == 0.75
        assert track.tempo == 120.5
        assert track.danceability == 0.68
        assert track.valence == 0.55
        assert track.musical_key == "C"
        assert track.scale == "major"
        assert track.spectral_complexity == 12.3
        assert track.loudness == -8.5
        assert track.analyzed_at == "2026-04-09T12:00:00"
        assert track.analysis_error is None


class TestSyncStateModel:
    """Tests for the SyncState SQLModel."""

    def test_create_sync_state(self, db_with_tracks):
        """SyncState model can be created and persisted."""
        from app.models.track import SyncState

        state = SyncState(
            last_sync_started="2024-01-16T08:00:00",
            last_sync_completed="2024-01-16T08:05:00",
            total_tracks=500,
        )
        db_with_tracks.add(state)
        db_with_tracks.commit()
        db_with_tracks.refresh(state)

        assert state.id is not None
        assert state.last_sync_started == "2024-01-16T08:00:00"
        assert state.last_sync_completed == "2024-01-16T08:05:00"
        assert state.total_tracks == 500

    def test_sync_state_defaults(self, db_with_tracks):
        """SyncState has correct defaults."""
        from app.models.track import SyncState

        state = SyncState()
        db_with_tracks.add(state)
        db_with_tracks.commit()
        db_with_tracks.refresh(state)

        assert state.last_sync_started is None
        assert state.last_sync_completed is None
        assert state.total_tracks == 0
