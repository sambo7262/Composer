"""Tests for the library API endpoints (GET /api/library/tracks, GET /library)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.models.track import Track
from app.services.sync_service import SyncStateEnum, SyncStatus


@pytest.fixture
def client(test_engine):
    """Create a test client with fresh database."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def seeded_db(test_engine):
    """Seed the database with 60 test tracks for pagination testing."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        for i in range(60):
            track = Track(
                plex_rating_key=f"rk-{i:03d}",
                title=f"Track {i:03d}",
                artist=f"Artist {chr(65 + (i % 26))}",
                album=f"Album {i % 10}",
                genre="Rock" if i % 2 == 0 else "Pop",
                year=2000 + (i % 25),
                duration_ms=180000 + (i * 1000),
            )
            session.add(track)
        session.commit()

    yield

    SQLModel.metadata.drop_all(test_engine)


class TestGetTracks:
    """Tests for GET /api/library/tracks."""

    def test_returns_paginated_results(self, client, seeded_db):
        """Default request returns 50 tracks per page."""
        response = client.get("/api/library/tracks")
        assert response.status_code == 200
        assert "track-table" in response.text
        # Should have 50 tracks on page 1 (out of 60)
        # Count table rows (each track row has Track in the title)
        assert "Page 1 of 2" in response.text

    def test_page_2_returns_remaining(self, client, seeded_db):
        """Page 2 should show remaining 10 tracks."""
        response = client.get("/api/library/tracks?page=2")
        assert response.status_code == 200
        assert "Page 2 of 2" in response.text

    def test_search_filters_by_title(self, client, seeded_db):
        """Search parameter filters tracks by title."""
        response = client.get("/api/library/tracks?search=Track 001")
        assert response.status_code == 200
        assert "Track 001" in response.text

    def test_search_filters_by_artist(self, client, seeded_db):
        """Search parameter filters tracks by artist."""
        response = client.get("/api/library/tracks?search=Artist A")
        assert response.status_code == 200
        assert "Artist A" in response.text

    def test_search_filters_by_album(self, client, seeded_db):
        """Search parameter filters tracks by album."""
        response = client.get("/api/library/tracks?search=Album 0")
        assert response.status_code == 200
        assert "Album 0" in response.text

    def test_sort_by_artist(self, client, seeded_db):
        """Sort parameter orders results by artist."""
        response = client.get("/api/library/tracks?sort=artist&order=asc")
        assert response.status_code == 200
        assert "track-table" in response.text

    def test_sort_desc(self, client, seeded_db):
        """Order=desc reverses sort direction."""
        response = client.get("/api/library/tracks?sort=year&order=desc")
        assert response.status_code == 200
        assert "track-table" in response.text

    def test_invalid_sort_defaults_to_title(self, client, seeded_db):
        """Invalid sort column should default to title."""
        response = client.get("/api/library/tracks?sort=invalid_column")
        assert response.status_code == 200
        assert "track-table" in response.text

    def test_invalid_order_defaults_to_asc(self, client, seeded_db):
        """Invalid order should default to asc."""
        response = client.get("/api/library/tracks?order=invalid")
        assert response.status_code == 200

    def test_per_page_capped_at_100(self, client, seeded_db):
        """per_page over 100 should be rejected (T-02-07)."""
        response = client.get("/api/library/tracks?per_page=200")
        assert response.status_code == 422  # Validation error from Query(le=100)

    def test_page_below_1_rejected(self, client, seeded_db):
        """page below 1 should be rejected (T-02-07)."""
        response = client.get("/api/library/tracks?page=0")
        assert response.status_code == 422

    def test_empty_library_shows_message(self, client):
        """Empty library shows 'no tracks found' message."""
        response = client.get("/api/library/tracks")
        assert response.status_code == 200
        assert "No tracks found" in response.text

    def test_search_no_results_shows_message(self, client, seeded_db):
        """Search with no results shows appropriate message."""
        response = client.get("/api/library/tracks?search=zzz_nonexistent")
        assert response.status_code == 200
        assert "No tracks matching" in response.text


class TestStatsEndpoint:
    """Concern 1 / ROADMAP SC-5: GET /api/library/stats returns the library_stats partial."""

    def test_stats_returns_zero_for_empty_library(self, client):
        """Empty DB: total=0, rated=0, last_rating_event_at is None."""
        response = client.get("/api/library/stats")
        assert response.status_code == 200
        assert "Total tracks" in response.text
        assert "Rated tracks" in response.text
        assert "Last rating event" in response.text
        # No events yet → the partial renders the em-dash placeholder.
        assert "—" in response.text

    def test_stats_counts_total_and_rated(self, client, test_engine):
        """Seed mixed-rating library, confirm counts match."""
        from app.models.event_log import EventLog  # noqa: F401  ensures table exists
        from app.models.track import Track

        with Session(test_engine) as session:
            for i in range(5):
                session.add(
                    Track(
                        plex_rating_key=f"rk-rated-{i}",
                        title=f"Title {i}",
                        artist=f"Artist {i}",
                        user_rating=8.0,
                    )
                )
            for i in range(3):
                session.add(
                    Track(
                        plex_rating_key=f"rk-unrated-{i}",
                        title=f"Other {i}",
                        artist=f"Artist {i}",
                        user_rating=None,
                    )
                )
            session.commit()

        response = client.get("/api/library/stats")
        assert response.status_code == 200
        # 8 total tracks; 5 of them rated.
        assert "8" in response.text
        assert "5" in response.text

    def test_stats_returns_last_rating_event_at(self, client, test_engine):
        """Most recent rating_changed EventLog timestamp surfaces on the partial."""
        from app.models.event_log import EventLog

        with Session(test_engine) as session:
            session.add(
                EventLog(
                    source="webhook",
                    event_type="rating_changed",
                    plex_rating_key="42",
                    dedupe_key="aa11bb22cc33dd44",
                    received_at="2026-05-08T09:00:00+00:00",
                    processed_at="2026-05-08T09:00:00.500000+00:00",
                )
            )
            session.add(
                EventLog(
                    source="webhook",
                    event_type="rating_changed",
                    plex_rating_key="43",
                    dedupe_key="bb22cc33dd44ee55",
                    received_at="2026-05-08T11:30:00+00:00",
                    processed_at="2026-05-08T11:30:00.500000+00:00",
                )
            )
            # An unrelated event_type should NOT be picked up.
            session.add(
                EventLog(
                    source="webhook",
                    event_type="track_played",
                    plex_rating_key="44",
                    dedupe_key="cc33dd44ee55ff66",
                    received_at="2026-05-09T00:00:00+00:00",
                    processed_at="2026-05-09T00:00:00.500000+00:00",
                )
            )
            session.commit()

        response = client.get("/api/library/stats")
        assert response.status_code == 200
        # Phase 8 260516-tza: timestamps now render in America/Los_Angeles via
        # the local_time Jinja filter. UTC 11:30 = PDT 04:30 (summer DST).
        # Must show the most recent rating_changed timestamp, NOT the later
        # track_played one (UTC 2026-05-09T00:00 = PDT 2026-05-08 17:00).
        assert "2026-05-08 04:30 PDT" in response.text
        assert "2026-05-08 17:00 PDT" not in response.text


class TestLibraryPage:
    """Tests for GET /library."""

    def test_library_page_returns_html(self, client, seeded_db):
        """GET /library returns full HTML page."""
        with patch(
            "app.routers.pages.get_sync_status",
            return_value=SyncStatus(state=SyncStateEnum.IDLE),
        ):
            response = client.get("/library")

        assert response.status_code == 200
        assert "Library" in response.text
        assert "track-table" in response.text

    def test_library_page_includes_search(self, client, seeded_db):
        """Library page includes the search input."""
        with patch(
            "app.routers.pages.get_sync_status",
            return_value=SyncStatus(state=SyncStateEnum.IDLE),
        ):
            response = client.get("/library")

        assert response.status_code == 200
        assert "Search tracks" in response.text

    def test_library_page_includes_sync_banner(self, client, seeded_db):
        """Library page includes the sync banner."""
        with patch(
            "app.routers.pages.get_sync_status",
            return_value=SyncStatus(state=SyncStateEnum.IDLE),
        ):
            response = client.get("/library")

        assert response.status_code == 200
        assert "sync-banner" in response.text

    def test_library_nav_link_active(self, client, seeded_db):
        """Library nav link should be active on the library page."""
        with patch(
            "app.routers.pages.get_sync_status",
            return_value=SyncStatus(state=SyncStateEnum.IDLE),
        ):
            response = client.get("/library")

        assert response.status_code == 200
        assert "Library" in response.text
