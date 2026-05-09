"""Tests for app/services/event_handlers.py — dispatch + INSERT OR IGNORE + handlers (RATE-03, EVT-06)."""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from sqlmodel import Session, SQLModel, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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


class TestHandleRatingChanged:
    def test_handle_rating_changed(self, db_with_phase5):
        """RATE-03: media.rate event updates Track.user_rating + rating_changed_at."""
        from app.models.events import RatingChangedEvent
        from app.models.track import Track
        from app.services.event_handlers import handle_rating_changed

        track = Track(plex_rating_key="42", title="X", artist="Y")
        db_with_phase5.add(track)
        db_with_phase5.commit()

        evt = RatingChangedEvent(
            plex_rating_key="42",
            new_rating=8.0,
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )

        _run_async(handle_rating_changed(evt))

        # Re-fetch from a fresh session — handler uses its own engine session
        from app.database import get_engine

        with Session(get_engine()) as fresh:
            statement = select(Track).where(Track.plex_rating_key == "42")
            updated = fresh.exec(statement).first()
            assert updated is not None
            assert updated.user_rating == 8.0
            assert updated.rating_changed_at is not None
            # Should be a parseable ISO timestamp
            datetime.fromisoformat(updated.rating_changed_at)

    def test_handle_rating_changed_clears_rating(self, db_with_phase5):
        """new_rating=None clears the rating; rating_changed_at still updates."""
        from app.models.events import RatingChangedEvent
        from app.models.track import Track
        from app.services.event_handlers import handle_rating_changed
        from app.database import get_engine

        track = Track(
            plex_rating_key="43", title="X", artist="Y", user_rating=6.0
        )
        db_with_phase5.add(track)
        db_with_phase5.commit()

        evt = RatingChangedEvent(
            plex_rating_key="43",
            new_rating=None,
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        _run_async(handle_rating_changed(evt))

        with Session(get_engine()) as fresh:
            updated = fresh.exec(
                select(Track).where(Track.plex_rating_key == "43")
            ).first()
            assert updated.user_rating is None
            assert updated.rating_changed_at is not None

    def test_handle_rating_changed_unknown_track_is_noop(self, db_with_phase5):
        """RatingChanged for an unknown ratingKey logs and exits cleanly (no crash)."""
        from app.models.events import RatingChangedEvent
        from app.services.event_handlers import handle_rating_changed

        evt = RatingChangedEvent(
            plex_rating_key="99999999",  # not in DB
            new_rating=5.0,
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        # Must not raise
        _run_async(handle_rating_changed(evt))


class TestDispatchEvent:
    def test_dispatch_event_inserts_event_log(self, db_with_phase5):
        """dispatch_event inserts an EventLog row with processed_at set after handler."""
        from app.models.events import RatingChangedEvent
        from app.models.track import Track
        from app.models.event_log import EventLog
        from app.services.event_handlers import dispatch_event
        from app.database import get_engine

        track = Track(plex_rating_key="44", title="X", artist="Y")
        db_with_phase5.add(track)
        db_with_phase5.commit()

        evt = RatingChangedEvent(
            plex_rating_key="44",
            new_rating=4.0,
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        _run_async(dispatch_event(evt))

        with Session(get_engine()) as fresh:
            rows = fresh.exec(
                select(EventLog).where(EventLog.plex_rating_key == "44")
            ).all()
            assert len(rows) == 1
            assert rows[0].processed_at is not None
            assert rows[0].handler_error is None or rows[0].handler_error == ""

    def test_dispatch_event_dedupes_silently(self, db_with_phase5):
        """Two dispatches of the same logical event within 5s window → 1 EventLog row."""
        from app.models.events import RatingChangedEvent
        from app.models.track import Track
        from app.models.event_log import EventLog
        from app.services.event_handlers import dispatch_event
        from app.database import get_engine

        track = Track(plex_rating_key="45", title="X", artist="Y")
        db_with_phase5.add(track)
        db_with_phase5.commit()

        # Two events with the same fields in same 5s bucket
        ts = datetime.now(timezone.utc).isoformat()
        evt1 = RatingChangedEvent(
            plex_rating_key="45",
            new_rating=4.0,
            source="webhook",
            received_at=ts,
        )
        evt2 = RatingChangedEvent(
            plex_rating_key="45",
            new_rating=4.0,
            source="poll",  # different source — irrelevant to dedupe key
            received_at=ts,
        )
        _run_async(dispatch_event(evt1))
        _run_async(dispatch_event(evt2))

        with Session(get_engine()) as fresh:
            rows = fresh.exec(
                select(EventLog).where(EventLog.plex_rating_key == "45")
            ).all()
            assert len(rows) == 1

    def test_dispatch_event_records_handler_error(self, db_with_phase5):
        """If a handler raises, dispatch_event records handler_error on EventLog row."""
        from app.models.events import RatingChangedEvent
        from app.models.event_log import EventLog
        from app.services import event_handlers
        from app.database import get_engine

        # Force handler to raise
        async def boom(evt):
            raise RuntimeError("kaboom")

        original = event_handlers.handle_rating_changed
        event_handlers.handle_rating_changed = boom
        try:
            evt = RatingChangedEvent(
                plex_rating_key="46",
                new_rating=2.0,
                source="webhook",
                received_at=datetime.now(timezone.utc).isoformat(),
            )
            _run_async(event_handlers.dispatch_event(evt))
        finally:
            event_handlers.handle_rating_changed = original

        with Session(get_engine()) as fresh:
            rows = fresh.exec(
                select(EventLog).where(EventLog.plex_rating_key == "46")
            ).all()
            assert len(rows) == 1
            assert rows[0].handler_error is not None
            assert "kaboom" in rows[0].handler_error


class TestStaticAnalysis:
    def test_no_blocking_plexapi_in_async(self):
        """EVT-06 / D-09: No PlexAPI calls outside asyncio.to_thread in async functions.

        Static AST scan of app/services/event_handlers.py — walk every async def body
        and assert no Call nodes reference PlexAPI symbols (PlexServer, fetchItem,
        searchTracks) outside of an asyncio.to_thread wrapper.
        """
        path = Path(__file__).parent.parent / "app" / "services" / "event_handlers.py"
        source = path.read_text()
        tree = ast.parse(source)

        forbidden_names = {"PlexServer", "fetchItem", "searchTracks", "library"}

        def call_is_to_thread(call: ast.Call) -> bool:
            f = call.func
            if isinstance(f, ast.Attribute):
                return f.attr == "to_thread"
            if isinstance(f, ast.Name):
                return f.id == "to_thread"
            return False

        violations = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue

            # Walk inner Calls
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                if call_is_to_thread(inner):
                    # to_thread wraps the underlying call — skip its args
                    continue

                # Check the call itself
                f = inner.func
                if isinstance(f, ast.Name) and f.id in forbidden_names:
                    violations.append(f"{node.name}: direct call to {f.id}")
                elif isinstance(f, ast.Attribute) and f.attr in forbidden_names:
                    violations.append(f"{node.name}: direct attr call .{f.attr}")

        assert violations == [], (
            "Forbidden blocking PlexAPI calls outside asyncio.to_thread in event_handlers.py:\n"
            + "\n".join(violations)
        )
