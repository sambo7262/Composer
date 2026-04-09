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
