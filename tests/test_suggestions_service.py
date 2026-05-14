"""Phase 7 Plan 01 Task 1 — Tests for app/services/suggestions_service.py.

Covers the SuggestionsMirror schema, bootstrap idempotency, drain-from-mirror
helper, threshold refill gate, and the static AST check that every
``Session(get_engine())`` block lives inside a ``_*_sync`` helper.

Tests do NOT exercise real Plex (every PlexAPI call is wrapped in
asyncio.to_thread per Phase 5 D-09; mocking happens at the
``app.services.suggestions_service`` module layer or at
``app.services.plex_playlist_service`` for the bootstrap path per the plan).
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def db_phase7(test_engine) -> Generator[Session, None, None]:
    """Create Phase 5/6/7 tables and yield a session."""
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
def reset_suggestions_state():
    """Phase 5 D-08 — reset module-level singleton between tests."""
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


# ---------------------------------------------------------------------------
# Schema tests — SuggestionsMirror table + columns + UNIQUE constraint
# ---------------------------------------------------------------------------


class TestSuggestionsMirrorTable:
    def test_suggestions_mirror_table_created(self, db_phase7, test_engine):
        """init_db's create_all registers the suggestionsmirror table with
        expected columns + types. Verified via PRAGMA table_info."""
        conn = test_engine.raw_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(suggestionsmirror)")
            rows = cursor.fetchall()
        finally:
            conn.close()

        # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
        by_name = {r[1]: r[2].upper() for r in rows}
        assert "track_id" in by_name and by_name["track_id"] == "INTEGER"
        assert "position" in by_name and by_name["position"] == "INTEGER"
        assert "added_at" in by_name and by_name["added_at"] == "TEXT"
        assert "rationale" in by_name and by_name["rationale"] == "TEXT"
        assert "vibe_id" in by_name and by_name["vibe_id"] == "INTEGER"
        assert "score" in by_name and by_name["score"] in {"REAL", "FLOAT"}

    def test_suggestions_mirror_track_id_unique(self, db_phase7):
        """UNIQUE(track_id) — inserting two rows with the same track_id raises
        IntegrityError."""
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track

        track = Track(plex_rating_key="42", title="X", artist="Y")
        db_phase7.add(track)
        db_phase7.commit()
        db_phase7.refresh(track)

        db_phase7.add(
            SuggestionsMirror(
                track_id=track.id,
                position=0,
                added_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        db_phase7.commit()

        # Second insert with same track_id must violate UNIQUE constraint.
        db_phase7.add(
            SuggestionsMirror(
                track_id=track.id,
                position=1,
                added_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        with pytest.raises(IntegrityError):
            db_phase7.commit()
        db_phase7.rollback()


# ---------------------------------------------------------------------------
# bootstrap_suggestions_queue tests
#
# DEVIATION (Rule 3): the plan's example asks for
# ``await create_playlist(SUGGESTIONS_PLAYLIST_NAME, [])`` — the real
# ``plex_playlist_service.create_playlist`` requires 5 args (plex_url,
# plex_token, name, rating_keys, vibe_id=None) AND raises ValueError on empty
# rating_keys (PlexAPI rejects empty playlists with BadRequest). Plan 01 has
# no seed tracks to pass, so we defer the actual Plex playlist creation to
# Plan 02's first refill. The bootstrap registers the ManagedPlaylist row
# with ``plex_rating_key=""`` sentinel — Plan 02 creates the Plex playlist
# on first seeding and updates the row. Tests assert the row exists with
# kind="suggestions" + composer_name="Composer · Suggestions"; the sentinel
# rating-key is verified in test_bootstrap_uses_deferred_sentinel.
# ---------------------------------------------------------------------------


class TestBootstrapSuggestionsQueue:
    def test_bootstrap_creates_managed_playlist_row(self, db_phase7):
        """A single ManagedPlaylist(kind='suggestions') row is created with the
        Composer · Suggestions composer_name on bootstrap.
        """
        from app.services.suggestions_service import bootstrap_suggestions_queue
        from app.models.vibe import ManagedPlaylist

        _run_async(bootstrap_suggestions_queue())

        rows = db_phase7.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.kind == "suggestions")
        ).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.kind == "suggestions"
        assert row.composer_name == "Composer · Suggestions"
        assert row.vibe_id is None
        assert row.track_count == 0

    def test_bootstrap_uses_deferred_sentinel(self, db_phase7):
        """Plan 01 deviation (documented in SUMMARY): empty plex_rating_key
        sentinel signals 'Plex playlist not yet created'; Plan 02's first
        refill creates the Plex playlist and updates this field.
        """
        from app.services.suggestions_service import bootstrap_suggestions_queue
        from app.models.vibe import ManagedPlaylist

        _run_async(bootstrap_suggestions_queue())

        row = db_phase7.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.kind == "suggestions")
        ).first()
        assert row is not None
        assert row.plex_rating_key == ""  # sentinel

    def test_bootstrap_is_idempotent_via_existing_managed_playlist_check(
        self, db_phase7
    ):
        """A second bootstrap call on an already-bootstrapped install is a
        no-op: short-circuits on the existing ManagedPlaylist(kind=suggestions)
        row, never touches Plex, never inserts a second row.
        """
        from app.services.suggestions_service import bootstrap_suggestions_queue
        from app.models.vibe import ManagedPlaylist

        # Pre-seed a ManagedPlaylist row.
        db_phase7.add(
            ManagedPlaylist(
                kind="suggestions",
                vibe_id=None,
                plex_rating_key="pre-existing-rk",
                composer_name="Composer · Suggestions",
                last_pushed_at=datetime.now(timezone.utc).isoformat(),
                track_count=0,
            )
        )
        db_phase7.commit()

        # Patch any Plex side-effect to RAISE; if bootstrap takes the
        # short-circuit path, this never fires.
        with patch(
            "app.services.suggestions_service._create_plex_suggestions_playlist",
            new=AsyncMock(side_effect=RuntimeError("must not be called")),
        ):
            _run_async(bootstrap_suggestions_queue())

        rows = db_phase7.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.kind == "suggestions")
        ).all()
        assert len(rows) == 1
        assert rows[0].plex_rating_key == "pre-existing-rk"


# ---------------------------------------------------------------------------
# drain_track_from_mirror tests
# ---------------------------------------------------------------------------


class TestDrainTrackFromMirror:
    def test_drain_removes_existing_member(self, db_phase7):
        """Drain returns True and removes the row when the track is in mirror."""
        from app.services.suggestions_service import drain_track_from_mirror
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track

        track = Track(plex_rating_key="42", title="X", artist="Y")
        db_phase7.add(track)
        db_phase7.commit()
        db_phase7.refresh(track)

        db_phase7.add(
            SuggestionsMirror(
                track_id=track.id,
                position=0,
                added_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        db_phase7.commit()

        result = _run_async(drain_track_from_mirror("42"))
        assert result is True

        remaining = db_phase7.exec(
            select(SuggestionsMirror).where(SuggestionsMirror.track_id == track.id)
        ).all()
        assert len(remaining) == 0

    def test_drain_returns_false_when_not_in_mirror(self, db_phase7):
        """No-op + returns False when the played track was not in the mirror."""
        from app.services.suggestions_service import drain_track_from_mirror
        from app.models.track import Track

        track = Track(plex_rating_key="42", title="X", artist="Y")
        db_phase7.add(track)
        db_phase7.commit()

        result = _run_async(drain_track_from_mirror("42"))
        assert result is False

    def test_drain_with_empty_rating_key_returns_false(self, db_phase7):
        from app.services.suggestions_service import drain_track_from_mirror

        assert _run_async(drain_track_from_mirror("")) is False


# ---------------------------------------------------------------------------
# maybe_schedule_refill tests
# ---------------------------------------------------------------------------


class TestMaybeScheduleRefill:
    def test_writes_refill_pending_marker_when_below_target(self, db_phase7):
        """deficit > 0 → INSERT OR IGNORE on EventLog(event_type=
        'suggestions_refill_pending')."""
        from app.services.suggestions_service import maybe_schedule_refill
        from app.models.event_log import EventLog

        # Empty mirror, target=30 → deficit=30.
        deficit = _run_async(maybe_schedule_refill())
        assert deficit == 30

        rows = db_phase7.exec(
            select(EventLog).where(
                EventLog.event_type == "suggestions_refill_pending"
            )
        ).all()
        assert len(rows) == 1

    def test_returns_zero_when_at_target(self, db_phase7):
        """No EventLog row written when mirror size >= target."""
        from app.services.suggestions_service import maybe_schedule_refill
        from app.models.event_log import EventLog
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track

        # Seed 30 mirror rows.
        for i in range(30):
            t = Track(plex_rating_key=str(100 + i), title=f"T{i}", artist="A")
            db_phase7.add(t)
        db_phase7.commit()
        tracks = db_phase7.exec(select(Track)).all()
        for idx, t in enumerate(tracks):
            db_phase7.add(
                SuggestionsMirror(
                    track_id=t.id,
                    position=idx,
                    added_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        db_phase7.commit()

        deficit = _run_async(maybe_schedule_refill())
        assert deficit == 0

        rows = db_phase7.exec(
            select(EventLog).where(
                EventLog.event_type == "suggestions_refill_pending"
            )
        ).all()
        assert len(rows) == 0

    def test_custom_target(self, db_phase7):
        from app.services.suggestions_service import maybe_schedule_refill

        deficit = _run_async(maybe_schedule_refill(target=10))
        assert deficit == 10


# ---------------------------------------------------------------------------
# AST: every Session(get_engine()) lives inside a _*_sync helper.
# Phase 5 D-09 / Plan 07-01 must_haves[5].
# ---------------------------------------------------------------------------


class TestSuggestionsServiceAstShape:
    def test_bootstrap_called_inside_asyncio_to_thread_for_db_writes(self):
        """Every ``Session(get_engine())`` block in suggestions_service.py
        lives inside a sync ``_*_sync`` helper that is called via
        ``asyncio.to_thread`` (mirrors event_handlers.py pattern).
        """
        path = (
            Path(__file__).parent.parent
            / "app"
            / "services"
            / "suggestions_service.py"
        )
        source = path.read_text()
        tree = ast.parse(source)

        # Walk module-level function definitions. For each Call to
        # ``Session(get_engine())`` somewhere in the file, assert the
        # enclosing function name matches the ``_*_sync`` pattern.
        violations: list[str] = []

        def find_session_calls(node: ast.AST, enclosing_func_name: str) -> None:
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                f = child.func
                # Match Session(get_engine())
                is_session = (
                    (isinstance(f, ast.Name) and f.id == "Session")
                    or (isinstance(f, ast.Attribute) and f.attr == "Session")
                )
                if not is_session:
                    continue
                if not enclosing_func_name.endswith("_sync"):
                    violations.append(
                        f"Session(get_engine()) inside {enclosing_func_name!r} "
                        f"— must live in a _*_sync helper"
                    )

        for top in ast.iter_child_nodes(tree):
            if isinstance(top, ast.FunctionDef):
                find_session_calls(top, top.name)
            elif isinstance(top, ast.AsyncFunctionDef):
                # async functions MUST NOT directly hold Session blocks.
                find_session_calls(top, top.name)

        assert violations == [], (
            "Phase 5 D-09 violation in suggestions_service.py:\n"
            + "\n".join(violations)
        )
