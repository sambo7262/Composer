"""Tests for app/services/poll_service.py — bounded polling that emits events (EVT-03)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from typing import Generator

import pytest
from sqlmodel import Session, SQLModel


def _run_async(coro):
    """Helper to run async coroutines in tests (mirrors test_sync_service.py:47)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def reset_poll_singleton():
    """Reset _poll_status between tests (mirrors test_sync_scheduler.py:11-23)."""
    try:
        import app.services.poll_service as ps

        ps._poll_status = ps.PollStatus()
    except (ImportError, AttributeError):
        pass  # Module not yet implemented in TDD red phase
    yield
    try:
        import app.services.poll_service as ps

        ps._poll_status = ps.PollStatus()
    except (ImportError, AttributeError):
        pass


@pytest.fixture(autouse=True)
def reset_event_bus():
    """Reset the event bus between tests so we can drain queues."""
    import app.services.event_bus as eb

    eb._queue = None
    eb._dispatcher_task = None
    yield
    eb._queue = None
    eb._dispatcher_task = None


@pytest.fixture
def db_with_phase5(test_engine) -> Generator[Session, None, None]:
    """Create all Phase 5 tables and yield a session."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


def _make_mock_setting(library_id="1"):
    setting = MagicMock()
    setting.is_configured = True
    setting.url = "http://plex:32400"
    setting.extra_config = {"library_id": library_id}
    return setting


def _make_mock_rated_track(rating_key, user_rating, last_viewed_at=None):
    """Build a MagicMock plex Track with userRating + optional lastViewedAt."""
    t = MagicMock()
    t.ratingKey = rating_key
    t.userRating = user_rating
    t.lastViewedAt = last_viewed_at
    return t


class TestRunPoll:
    """Tests for poll_service.run_poll — the EVT-03 polling pass."""

    def test_emits_rating_changed(self, db_with_phase5):
        """When poll sees a rating diff vs DB, emits RatingChangedEvent(source='poll')."""
        from app.models.track import Track
        from app.services import poll_service

        # Pre-populate one track with rating 5.0
        track = Track(plex_rating_key="201", title="X", artist="Y", user_rating=5.0)
        db_with_phase5.add(track)
        db_with_phase5.commit()

        # Plex returns the same track but with rating 8.0 (diff)
        plex_track = _make_mock_rated_track("201", 8.0)

        mock_section = MagicMock()
        mock_section.searchTracks.return_value = [plex_track]

        mock_plex = MagicMock()
        mock_plex.library.sectionByID.return_value = mock_section

        with patch("app.services.poll_service.PlexServer", return_value=mock_plex), \
             patch("app.services.poll_service.get_setting",
                   return_value=_make_mock_setting()), \
             patch("app.services.poll_service.get_decrypted_credential",
                   return_value="test-token"):
            _run_async(poll_service.run_poll())

        # Drain the bus and inspect events
        from app.services.event_bus import get_event_bus

        bus = get_event_bus()
        events = []
        while True:
            try:
                events.append(bus.get_nowait())
            except asyncio.QueueEmpty:
                break

        rating_events = [e for e in events if e.type == "rating_changed"]
        assert len(rating_events) >= 1
        assert rating_events[0].source == "poll"
        assert rating_events[0].plex_rating_key == "201"
        assert rating_events[0].new_rating == 8.0

    def test_uses_bounded_query(self, db_with_phase5):
        """Polling MUST use searchTracks(filters=..., limit<=200), NEVER library.all()."""
        from app.services import poll_service

        mock_section = MagicMock()
        mock_section.searchTracks.return_value = []

        mock_plex = MagicMock()
        mock_plex.library.sectionByID.return_value = mock_section
        # If poll_service tries to call library.all(), this magic mock would happily
        # return — so we additionally assert it was NOT called below.

        with patch("app.services.poll_service.PlexServer", return_value=mock_plex), \
             patch("app.services.poll_service.get_setting",
                   return_value=_make_mock_setting()), \
             patch("app.services.poll_service.get_decrypted_credential",
                   return_value="test-token"):
            _run_async(poll_service.run_poll())

        # searchTracks must have been called with bounded filters
        assert mock_section.searchTracks.called, "Expected searchTracks to be called"

        # At least one call should include either userRating filter or a limit cap
        call_kwargs_seen = []
        for call in mock_section.searchTracks.call_args_list:
            call_kwargs_seen.append(call.kwargs)

        has_bounded_call = False
        for kwargs in call_kwargs_seen:
            limit = kwargs.get("limit", None)
            filters = kwargs.get("filters", None)
            # Bounded if EITHER limit <= 200 OR filters present
            if (limit is not None and limit <= 200) or filters:
                has_bounded_call = True
                break
        assert has_bounded_call, (
            f"searchTracks must use bounded queries (filters or limit<=200); "
            f"saw call kwargs: {call_kwargs_seen}"
        )

        # Forbidden — must NEVER call library.all()
        # MagicMock's `library` attr would happily return; assert NOT called.
        assert not mock_plex.library.all.called, (
            "poll_service must NEVER call library.all() (Pitfall 21)"
        )

    def test_concurrency_guard(self, db_with_phase5):
        """A second run_poll() while one is RUNNING returns immediately."""
        from app.services import poll_service

        # Manually mark RUNNING
        poll_service._poll_status = poll_service.PollStatus(
            state=poll_service.PollStateEnum.RUNNING
        )

        # Patch PlexServer; if guard works it should never be called.
        with patch("app.services.poll_service.PlexServer") as mock_plex_cls, \
             patch("app.services.poll_service.get_setting",
                   return_value=_make_mock_setting()), \
             patch("app.services.poll_service.get_decrypted_credential",
                   return_value="test-token"):
            _run_async(poll_service.run_poll())

        assert mock_plex_cls.call_count == 0, (
            "Concurrency guard should prevent any Plex calls when state is RUNNING"
        )
