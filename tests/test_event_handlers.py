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


class TestHandleTrackPlayed:
    """Tests for handle_track_played (Phase 5 Plan 02 — view_count + last_viewed_at)."""

    def test_handle_track_played(self, db_with_phase5):
        """TrackPlayed event increments Track.view_count and updates last_viewed_at."""
        from app.models.events import TrackPlayedEvent
        from app.models.track import Track
        from app.services.event_handlers import handle_track_played
        from app.database import get_engine

        # Pre-populate: track with view_count=3 and last_viewed_at=None
        track = Track(
            plex_rating_key="42",
            title="X",
            artist="Y",
            view_count=3,
            last_viewed_at=None,
        )
        db_with_phase5.add(track)
        db_with_phase5.commit()

        evt = TrackPlayedEvent(
            plex_rating_key="42",
            last_viewed_at="2026-05-08T12:00:00+00:00",
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )

        _run_async(handle_track_played(evt))

        with Session(get_engine()) as fresh:
            updated = fresh.exec(
                select(Track).where(Track.plex_rating_key == "42")
            ).first()
            assert updated is not None
            assert updated.view_count == 4
            assert updated.last_viewed_at == "2026-05-08T12:00:00+00:00"

    def test_handle_track_played_first_play(self, db_with_phase5):
        """TrackPlayed on a track with view_count=0 increments to 1."""
        from app.models.events import TrackPlayedEvent
        from app.models.track import Track
        from app.services.event_handlers import handle_track_played
        from app.database import get_engine

        track = Track(
            plex_rating_key="43",
            title="X",
            artist="Y",
            view_count=0,
        )
        db_with_phase5.add(track)
        db_with_phase5.commit()

        evt = TrackPlayedEvent(
            plex_rating_key="43",
            last_viewed_at="2026-05-08T13:00:00+00:00",
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        _run_async(handle_track_played(evt))

        with Session(get_engine()) as fresh:
            updated = fresh.exec(
                select(Track).where(Track.plex_rating_key == "43")
            ).first()
            assert updated.view_count == 1

    def test_handle_track_played_unknown_track_is_noop(self, db_with_phase5):
        """TrackPlayed for unknown ratingKey logs and exits cleanly (no crash)."""
        from app.models.events import TrackPlayedEvent
        from app.services.event_handlers import handle_track_played

        evt = TrackPlayedEvent(
            plex_rating_key="9999999",
            last_viewed_at="2026-05-08T13:00:00+00:00",
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        # Must not raise
        _run_async(handle_track_played(evt))


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

        Static AST scan walks the following service files and asserts no Call
        nodes reference PlexAPI symbols (PlexServer / fetchItem / searchTracks /
        library / fetchItems / createPlaylist / addItems / removeItems /
        editTitle) outside of an asyncio.to_thread wrapper.

        Files walked (extended in Phase 6 Plan 02 — see plan 06-02;
        Phase 7 Plan 01 added suggestions_service.py):
          - app/services/event_handlers.py    (Phase 5 origin)
          - app/services/plex_playlist_service.py (Phase 6 Plan 02 Task 2)
          - app/services/vibe_service.py      (Phase 6 Plan 02 Task 3)
          - app/services/suggestions_service.py   (Phase 7 Plan 01)

        Files that do not yet exist on disk are skipped — preserves backwards
        compatibility while Plan 02 commits land in order.
        """
        services_dir = Path(__file__).parent.parent / "app" / "services"
        paths = [
            services_dir / "event_handlers.py",
            services_dir / "plex_playlist_service.py",
            services_dir / "vibe_service.py",
            services_dir / "suggestions_service.py",  # Phase 7 Plan 01
        ]

        forbidden_names = {
            "PlexServer",
            "fetchItem",
            "searchTracks",
            "library",
            "fetchItems",
            "createPlaylist",
            "addItems",
            "removeItems",
            "editTitle",
        }

        def call_is_to_thread(call: ast.Call) -> bool:
            f = call.func
            if isinstance(f, ast.Attribute):
                return f.attr == "to_thread"
            if isinstance(f, ast.Name):
                return f.id == "to_thread"
            return False

        def collect_to_thread_targets(async_node: ast.AsyncFunctionDef) -> set:
            """Names of inner functions passed as the first arg of asyncio.to_thread.

            Such inner functions run in a worker thread — their PlexAPI calls
            are NOT blocking the event loop, so we approve them as wrapped.
            """
            approved: set[str] = set()
            for inner in ast.walk(async_node):
                if not isinstance(inner, ast.Call) or not call_is_to_thread(inner):
                    continue
                if not inner.args:
                    continue
                first = inner.args[0]
                if isinstance(first, ast.Name):
                    approved.add(first.id)
                elif isinstance(first, ast.Attribute):
                    approved.add(first.attr)
            return approved

        def check_calls_in_node(
            scope_label: str,
            node: ast.AST,
            violations: list,
            recurse_into_nested_funcs: bool = True,
            approved_function_names: set = None,
        ) -> None:
            """Walk Calls inside `node`. Skip approved inner function bodies."""
            approved_function_names = approved_function_names or set()

            # Build a set of inner FunctionDef nodes whose body should be
            # walked separately (not via the outer ast.walk).
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef):
                    # Inner sync function. If approved (passed to to_thread),
                    # skip — its PlexAPI calls are wrapped.
                    if child.name in approved_function_names:
                        continue
                    # Otherwise, recursively check its body too.
                    check_calls_in_node(
                        f"{scope_label}::{child.name}",
                        child,
                        violations,
                        recurse_into_nested_funcs=True,
                        approved_function_names=approved_function_names,
                    )
                elif isinstance(child, ast.AsyncFunctionDef):
                    # Nested AsyncFunctionDef gets its own top-level scan.
                    continue
                else:
                    # For non-function children, walk inner Calls directly.
                    for inner in ast.walk(child):
                        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            # Walked at the level above; don't double-count.
                            continue
                        if not isinstance(inner, ast.Call):
                            continue
                        if call_is_to_thread(inner):
                            continue
                        f = inner.func
                        if isinstance(f, ast.Name) and f.id in forbidden_names:
                            violations.append(
                                f"{scope_label}: direct call to {f.id}"
                            )
                        elif isinstance(f, ast.Attribute) and f.attr in forbidden_names:
                            violations.append(
                                f"{scope_label}: direct attr call .{f.attr}"
                            )

        violations: list[str] = []

        for path in paths:
            if not path.exists():
                # File not yet created (Plan 02 lands services in sequence).
                continue
            source = path.read_text()
            tree = ast.parse(source)

            for async_node in ast.walk(tree):
                if not isinstance(async_node, ast.AsyncFunctionDef):
                    continue
                approved = collect_to_thread_targets(async_node)
                check_calls_in_node(
                    f"{path.name}:{async_node.name}",
                    async_node,
                    violations,
                    approved_function_names=approved,
                )

        assert violations == [], (
            "Forbidden blocking PlexAPI calls outside asyncio.to_thread in:\n"
            + "\n".join(violations)
        )


# ===========================================================================
# Phase 7 Plan 01 Task 2 — handle_track_played drain branch tests
# ===========================================================================


@pytest.fixture
def db_with_phase7(test_engine):
    """Phase 5 + 6 + 7 tables for the drain-branch tests."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        MigrationLog,
        SetupState,
        SlotInLog,
        TrackVibe,
        Vibe,
    )
    from app.models.suggestions import SuggestionsMirror  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def _reset_suggestions_singleton():
    try:
        from app.services import suggestions_service
        suggestions_service._status = suggestions_service.SuggestionsServiceStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from app.services import suggestions_service
        suggestions_service._status = suggestions_service.SuggestionsServiceStatus()
    except (ImportError, AttributeError):
        pass


class TestHandleTrackPlayedSuggestionsDrain:
    """SUGG-03 drain half: handle_track_played removes the track from
    SuggestionsMirror if currently a member AND schedules a refill via the
    threshold gate, while preserving the existing Phase 5 RATE-04 view_count
    + last_viewed_at update.
    """

    def test_drains_mirror_when_track_is_member(self, db_with_phase7):
        from datetime import datetime, timezone

        from app.models.events import TrackPlayedEvent
        from app.models.event_log import EventLog
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track
        from app.services.event_handlers import handle_track_played
        from app.database import get_engine

        track = Track(
            plex_rating_key="42",
            title="X",
            artist="Y",
            view_count=3,
            last_viewed_at=None,
        )
        db_with_phase7.add(track)
        db_with_phase7.commit()
        db_with_phase7.refresh(track)
        db_with_phase7.add(
            SuggestionsMirror(
                track_id=track.id,
                position=0,
                added_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        db_with_phase7.commit()

        evt = TrackPlayedEvent(
            plex_rating_key="42",
            last_viewed_at="2026-05-13T12:00:00+00:00",
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )

        _run_async(handle_track_played(evt))

        # (1) Phase 5 RATE-04 view_count + last_viewed_at still updated.
        with Session(get_engine()) as fresh:
            updated = fresh.exec(
                select(Track).where(Track.plex_rating_key == "42")
            ).first()
            assert updated.view_count == 4
            assert updated.last_viewed_at == "2026-05-13T12:00:00+00:00"

            # (2) SuggestionsMirror row drained.
            mirror_rows = fresh.exec(
                select(SuggestionsMirror).where(
                    SuggestionsMirror.track_id == track.id
                )
            ).all()
            assert len(mirror_rows) == 0

            # (3) Threshold-gate marker scheduled (mirror is now empty <
            # target=30 → deficit=30).
            refill_markers = fresh.exec(
                select(EventLog).where(
                    EventLog.event_type == "suggestions_refill_pending"
                )
            ).all()
            assert len(refill_markers) == 1

    def test_no_refill_when_track_not_in_mirror_and_target_already_met(
        self, db_with_phase7
    ):
        """When the played track is NOT in the mirror AND the mirror is
        already at target, no refill marker is written (deficit=0).
        """
        from datetime import datetime, timezone

        from app.models.events import TrackPlayedEvent
        from app.models.event_log import EventLog
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track
        from app.services.event_handlers import handle_track_played
        from app.database import get_engine

        # Played track itself (not a mirror member).
        played = Track(
            plex_rating_key="99",
            title="Outsider",
            artist="External",
        )
        db_with_phase7.add(played)

        # Seed 30 unrelated tracks + 30 mirror rows so deficit=0.
        for i in range(30):
            t = Track(
                plex_rating_key=str(2000 + i),
                title=f"M{i}",
                artist="A",
            )
            db_with_phase7.add(t)
        db_with_phase7.commit()

        for idx, t in enumerate(
            db_with_phase7.exec(
                select(Track).where(Track.plex_rating_key.like("2%"))
            ).all()
        ):
            db_with_phase7.add(
                SuggestionsMirror(
                    track_id=t.id,
                    position=idx,
                    added_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        db_with_phase7.commit()

        evt = TrackPlayedEvent(
            plex_rating_key="99",
            last_viewed_at="2026-05-13T12:00:00+00:00",
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )

        _run_async(handle_track_played(evt))

        with Session(get_engine()) as fresh:
            # Drain returned False — no mirror row matched ratingKey "99".
            # The threshold gate is only triggered when `removed` is True
            # (see event_handlers.handle_track_played); since drain returned
            # False, maybe_schedule_refill is NOT called → no marker.
            refill_markers = fresh.exec(
                select(EventLog).where(
                    EventLog.event_type == "suggestions_refill_pending"
                )
            ).all()
            assert len(refill_markers) == 0

            # view_count still incremented.
            outsider = fresh.exec(
                select(Track).where(Track.plex_rating_key == "99")
            ).first()
            assert outsider.view_count == 1

    def test_drain_failure_does_not_break_rating_update(self, db_with_phase7):
        """If drain_track_from_mirror raises, the Phase 5 view_count update
        must still commit (mirrors the slot_track hook on handle_rating_changed
        — try/except, never break the primary path).
        """
        from datetime import datetime, timezone

        from app.models.events import TrackPlayedEvent
        from app.models.track import Track
        from app.services.event_handlers import handle_track_played
        from app.database import get_engine
        from app.services import suggestions_service

        track = Track(
            plex_rating_key="50",
            title="X",
            artist="Y",
            view_count=0,
        )
        db_with_phase7.add(track)
        db_with_phase7.commit()

        async def boom(rating_key):
            raise RuntimeError("simulated mirror outage")

        original = suggestions_service.drain_track_from_mirror
        suggestions_service.drain_track_from_mirror = boom
        try:
            evt = TrackPlayedEvent(
                plex_rating_key="50",
                last_viewed_at="2026-05-13T13:00:00+00:00",
                source="webhook",
                received_at=datetime.now(timezone.utc).isoformat(),
            )
            _run_async(handle_track_played(evt))
        finally:
            suggestions_service.drain_track_from_mirror = original

        with Session(get_engine()) as fresh:
            updated = fresh.exec(
                select(Track).where(Track.plex_rating_key == "50")
            ).first()
            # Phase 5 update still committed — the drain hook is best-effort.
            assert updated.view_count == 1
            assert updated.last_viewed_at == "2026-05-13T13:00:00+00:00"
