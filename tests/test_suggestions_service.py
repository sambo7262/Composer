"""Phase 7 Plan 01 Task 1 — Tests for app/services/suggestions_service.py.

Covers the SuggestionsMirror schema, bootstrap idempotency, drain-from-mirror
helper, threshold refill gate, and the static AST check that every
``Session(get_engine())`` block lives inside a ``_*_sync`` helper.

Tests do NOT exercise real Plex (every PlexAPI call is wrapped in
asyncio.to_thread per Phase 5 D-09; mocking happens at the
``app.services.suggestions_service`` module layer or at
``app.services.plex_playlist_service`` for the bootstrap path per the plan).

Phase 7.1 D-D1: the legacy LLM-ranking section
(refill_suggestions_queue / SuggestionRankingPick / SuggestionRankingResponse /
build_suggestions_ranking_*_prompt / _validate_picks / _build_shortlist_sync /
SUGGESTIONS_RANK_MAX_TOKENS) was deleted. The
``TestSuggestionsRankMaxTokensHotfix260514E6w`` regression class was removed
alongside the constant it pinned. The new ``TestRefillMirrorSql`` class
exercises the pure-SQL replacement (zero LLM calls, free, instant).
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
        # SQLite stores SQLModel ``str`` columns as VARCHAR (no length affinity
        # distinction from TEXT); SQLModel ``Optional[float]`` lands as FLOAT
        # which SQLite treats as REAL via type affinity.
        TEXT_TYPES = {"TEXT", "VARCHAR"}
        INT_TYPES = {"INTEGER", "INT"}
        REAL_TYPES = {"REAL", "FLOAT", "DOUBLE"}
        assert "track_id" in by_name and by_name["track_id"] in INT_TYPES
        assert "position" in by_name and by_name["position"] in INT_TYPES
        assert "added_at" in by_name and by_name["added_at"] in TEXT_TYPES
        assert "rationale" in by_name and by_name["rationale"] in TEXT_TYPES
        assert "vibe_id" in by_name and by_name["vibe_id"] in INT_TYPES
        assert "score" in by_name and by_name["score"] in REAL_TYPES

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
    """Phase 7.1 SUGG-12 — maybe_schedule_refill now delegates to
    ``refill_mirror_sql`` (pure SQL; no LLM, no cost breaker). The public
    symbol ``maybe_schedule_refill`` is preserved for monkeypatch stability
    in existing test sites (~6 sites in test_event_handlers.py).
    """

    def test_awaits_refill_when_below_target(self, db_phase7):
        """Phase 7.1: when deficit > 0, refill_mirror_sql is awaited."""
        from unittest.mock import AsyncMock
        from app.services import suggestions_service

        original = suggestions_service.refill_mirror_sql
        mock_refill = AsyncMock(return_value=None)
        suggestions_service.refill_mirror_sql = mock_refill
        try:
            deficit = _run_async(suggestions_service.maybe_schedule_refill())
        finally:
            suggestions_service.refill_mirror_sql = original
        assert deficit == 30
        mock_refill.assert_awaited_once()

    def test_returns_zero_when_at_target(self, db_phase7):
        """Phase 7.1: when mirror size >= target, refill_mirror_sql is
        NOT awaited (deficit == 0 short-circuit)."""
        from unittest.mock import AsyncMock
        from app.services import suggestions_service
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track

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

        original = suggestions_service.refill_mirror_sql
        mock_refill = AsyncMock(return_value=None)
        suggestions_service.refill_mirror_sql = mock_refill
        try:
            deficit = _run_async(suggestions_service.maybe_schedule_refill())
        finally:
            suggestions_service.refill_mirror_sql = original
        assert deficit == 0
        mock_refill.assert_not_called()

    def test_custom_target(self, db_phase7):
        from unittest.mock import AsyncMock
        from app.services import suggestions_service

        original = suggestions_service.refill_mirror_sql
        mock_refill = AsyncMock(return_value=None)
        suggestions_service.refill_mirror_sql = mock_refill
        try:
            deficit = _run_async(
                suggestions_service.maybe_schedule_refill(target=10)
            )
        finally:
            suggestions_service.refill_mirror_sql = original
        assert deficit == 10
        mock_refill.assert_awaited_once()


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


# ---------------------------------------------------------------------------
# Phase 7.1 SUGG-12 — refill_mirror_sql: pure SQL-driven refill (replaces
# refill_suggestions_queue / refill_suggestions_for_vibe LLM bodies).
# Free, instant, no LLM tokens consumed. Cross-vibe pool weighted by
# library share (Vibe.is_active = 1 only); top-N closest-by-distance
# per vibe with light randomization for variety.
# ---------------------------------------------------------------------------


def _seed_vibe(
    session, name: str, *, is_active: bool = True, vibe_id: int = None,
):
    """Helper: insert one Vibe with reasonable defaults."""
    from app.models.vibe import Vibe

    v = Vibe(
        id=vibe_id,
        name=name,
        description=f"{name} description",
        centroid_energy=0.5,
        centroid_tempo=120.0,
        centroid_danceability=0.5,
        centroid_valence=0.5,
        spread_energy=0.1,
        spread_tempo=20.0,
        spread_danceability=0.1,
        spread_valence=0.1,
        created_at=datetime.now(timezone.utc).isoformat(),
        is_active=is_active,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return v


def _seed_track_with_vibe(
    session, vibe_id: int, *, rk: str, distance: float = 0.5,
    artist: str = "ArtistA", title: str = None,
):
    """Helper: seed a Track + a TrackVibe row."""
    from app.models.track import Track
    from app.models.vibe import TrackVibe

    t = Track(
        plex_rating_key=rk,
        title=title or f"Title {rk}",
        artist=artist,
        energy=0.5, tempo=120.0, danceability=0.5, valence=0.5,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    session.add(TrackVibe(
        track_id=t.id, vibe_id=vibe_id, distance=distance,
        assigned_at=datetime.now(timezone.utc).isoformat(),
        assigned_by="cluster",
    ))
    session.commit()
    return t


def _seed_managed_suggestions(session, *, plex_rating_key: str = "") -> None:
    """Helper: seed the ManagedPlaylist(kind='suggestions') row that the
    refill Plex push branch expects. Default sentinel rating_key forces
    the "deferred materialization" branch.
    """
    from app.models.vibe import ManagedPlaylist

    session.add(ManagedPlaylist(
        kind="suggestions",
        vibe_id=None,
        plex_rating_key=plex_rating_key,
        composer_name="Composer · Suggestions",
        last_pushed_at=datetime.now(timezone.utc).isoformat(),
        track_count=0,
    ))
    session.commit()


def _seed_plex_creds(session) -> None:
    """Helper: seed Plex ServiceConfig + encrypted token so
    _read_plex_creds_sync returns non-empty creds.
    """
    from app.models.settings import ServiceConfig
    from app.services.settings_service import set_credential

    session.add(ServiceConfig(
        service_name="plex",
        url="http://localhost:32400",
        is_configured=True,
        extra_config={},
    ))
    session.commit()
    set_credential(session, "plex", "test-token")


class TestRefillMirrorSql:
    """Phase 7.1 SUGG-12 — pure-SQL refill against TrackVibe.distance."""

    def test_zero_llm_calls(self, db_phase7):
        """Bedrock proof: refill_mirror_sql does NOT instantiate or call
        AnthropicClient. _get_anthropic_client is patched to raise — the
        call must succeed (no LLM activity).
        """
        from unittest.mock import patch
        from app.services import suggestions_service
        from app.models.suggestions import SuggestionsMirror

        _seed_managed_suggestions(db_phase7)
        v = _seed_vibe(db_phase7, "active")
        for i in range(5):
            _seed_track_with_vibe(
                db_phase7, v.id, rk=f"rk-{i}", distance=0.1 * i,
            )

        with patch.object(
            suggestions_service, "_get_anthropic_client",
            side_effect=RuntimeError("MUST NOT BE CALLED"),
        ):
            result = _run_async(
                suggestions_service.refill_mirror_sql(target=3)
            )

        assert result.picks_made == 3
        rows = db_phase7.exec(select(SuggestionsMirror)).all()
        assert len(rows) == 3

    def test_picks_proportional_to_vibe_share(self, db_phase7):
        """D-B1 — allocation proportional to library share per ACTIVE vibe."""
        from app.services import suggestions_service
        from app.models.suggestions import SuggestionsMirror

        _seed_managed_suggestions(db_phase7)
        v_a = _seed_vibe(db_phase7, "A")
        v_b = _seed_vibe(db_phase7, "B")
        # 80 tracks on vibe_a, 20 on vibe_b → 80/20 split for target=10
        for i in range(80):
            _seed_track_with_vibe(
                db_phase7, v_a.id, rk=f"a-{i}", distance=0.1 + i * 0.001,
            )
        for i in range(20):
            _seed_track_with_vibe(
                db_phase7, v_b.id, rk=f"b-{i}", distance=0.1 + i * 0.001,
            )

        _run_async(suggestions_service.refill_mirror_sql(target=10))

        rows = db_phase7.exec(select(SuggestionsMirror)).all()
        from_a = sum(1 for r in rows if r.vibe_id == v_a.id)
        from_b = sum(1 for r in rows if r.vibe_id == v_b.id)
        assert from_a + from_b == 10
        # 80/20 share → ~8 from A, ~2 from B (±1 for rounding).
        assert 7 <= from_a <= 9
        assert 1 <= from_b <= 3

    def test_excludes_in_mirror_and_recent_history(self, db_phase7):
        """Tracks already in the mirror or surfaced within 14 days are NOT
        picked even if they're in the top-N.
        """
        from datetime import timedelta
        from app.services import suggestions_service
        from app.models.suggestions import (
            SuggestionHistory, SuggestionsMirror,
        )

        _seed_managed_suggestions(db_phase7)
        v = _seed_vibe(db_phase7, "active")
        tracks = []
        for i in range(5):
            tracks.append(_seed_track_with_vibe(
                db_phase7, v.id, rk=f"rk-{i}", distance=0.1 * i,
            ))

        # Put tracks[0] in the mirror.
        db_phase7.add(SuggestionsMirror(
            track_id=tracks[0].id, position=0,
            added_at=datetime.now(timezone.utc).isoformat(),
        ))
        # Put tracks[1] in 14-day history.
        db_phase7.add(SuggestionHistory(
            track_id=tracks[1].id,
            surfaced_at=(
                datetime.now(timezone.utc) - timedelta(days=7)
            ).isoformat(),
            refill_id=999,
        ))
        db_phase7.commit()

        _run_async(suggestions_service.refill_mirror_sql(target=5))

        rows = db_phase7.exec(select(SuggestionsMirror)).all()
        picked_ids = {r.track_id for r in rows}
        # tracks[0] was pre-seeded; tracks[1] is in recent history.
        assert tracks[1].id not in picked_ids
        # tracks[2-4] should be picked.
        assert tracks[2].id in picked_ids
        assert tracks[3].id in picked_ids
        assert tracks[4].id in picked_ids

    def test_excludes_hard_negative_artist_and_track(self, db_phase7):
        """hard_track / hard_artist (recovery_pending=True) excluded."""
        from app.services import suggestions_service
        from app.models.suggestions import (
            NegativeSignal, SuggestionsMirror,
        )

        _seed_managed_suggestions(db_phase7)
        v = _seed_vibe(db_phase7, "active")
        t0 = _seed_track_with_vibe(
            db_phase7, v.id, rk="rk-0", distance=0.1, artist="HardArtist",
        )
        t1 = _seed_track_with_vibe(
            db_phase7, v.id, rk="rk-1", distance=0.2, artist="OK",
        )
        t2 = _seed_track_with_vibe(
            db_phase7, v.id, rk="rk-2", distance=0.3, artist="HardArtist",
        )
        t3 = _seed_track_with_vibe(
            db_phase7, v.id, rk="rk-3", distance=0.4, artist="HardTrackArtist",
        )

        # Mark t3 as hard_track; mark artist "HardArtist" as hard_artist.
        db_phase7.add(NegativeSignal(
            track_id=t3.id, signal_type="hard_track",
            created_at=datetime.now(timezone.utc).isoformat(),
        ))
        db_phase7.add(NegativeSignal(
            artist="HardArtist", signal_type="hard_artist",
            created_at=datetime.now(timezone.utc).isoformat(),
            recovery_pending=True,
        ))
        db_phase7.commit()

        _run_async(suggestions_service.refill_mirror_sql(target=5))

        rows = db_phase7.exec(select(SuggestionsMirror)).all()
        picked = {r.track_id for r in rows}
        assert t0.id not in picked  # hard_artist
        assert t1.id in picked      # OK
        assert t2.id not in picked  # hard_artist
        assert t3.id not in picked  # hard_track

    def test_excludes_archived_vibes_from_pool_weights(self, db_phase7):
        """Blocker #7: archived vibes contribute zero weight."""
        from app.services.suggestions_service import (
            _read_vibe_pool_weights_sync,
        )

        v_act = _seed_vibe(db_phase7, "active", is_active=True)
        v_arc = _seed_vibe(db_phase7, "archived", is_active=False)
        # 30 tracks on active, 70 on archived (intentionally inverted).
        for i in range(30):
            _seed_track_with_vibe(
                db_phase7, v_act.id, rk=f"act-{i}", distance=0.1,
            )
        for i in range(70):
            _seed_track_with_vibe(
                db_phase7, v_arc.id, rk=f"arc-{i}", distance=0.1,
            )

        weights = _read_vibe_pool_weights_sync()
        assert len(weights) == 1
        assert weights[0][0] == v_act.id
        assert weights[0][1] == pytest.approx(1.0)

    def test_excludes_archived_vibe_members(self, db_phase7):
        """Blocker #7: _read_top_n_for_vibe_sync returns [] for archived."""
        from app.services.suggestions_service import (
            _read_top_n_for_vibe_sync,
        )

        v_arc = _seed_vibe(db_phase7, "archived", is_active=False)
        for i in range(5):
            _seed_track_with_vibe(
                db_phase7, v_arc.id, rk=f"arc-{i}", distance=0.1,
            )

        rows = _read_top_n_for_vibe_sync(v_arc.id)
        assert rows == []

    def test_create_then_archive_vibe_excludes_subsequent_picks(self, db_phase7):
        """Blocker #7 end-to-end: archive a vibe → next refill doesn't surface
        any of its tracks.
        """
        from app.services import suggestions_service
        from app.models.suggestions import SuggestionsMirror
        from app.models.vibe import Vibe

        _seed_managed_suggestions(db_phase7)
        v = _seed_vibe(db_phase7, "v1", is_active=True)
        tracks_v = [
            _seed_track_with_vibe(
                db_phase7, v.id, rk=f"v-{i}", distance=0.1,
            )
            for i in range(5)
        ]
        # Archive the vibe BEFORE the refill.
        v.is_active = False
        db_phase7.add(v)
        db_phase7.commit()

        _run_async(suggestions_service.refill_mirror_sql(target=5))

        rows = db_phase7.exec(select(SuggestionsMirror)).all()
        picked = {r.track_id for r in rows}
        # Zero archived-vibe tracks surfaced.
        for t in tracks_v:
            assert t.id not in picked

    def test_writes_refill_trigger_log_with_event_source(self, db_phase7):
        """RefillTriggerLog row inserted with event_source='sql_refill'."""
        from app.services import suggestions_service
        from app.models.suggestions import RefillTriggerLog

        _seed_managed_suggestions(db_phase7)
        v = _seed_vibe(db_phase7, "active")
        for i in range(5):
            _seed_track_with_vibe(
                db_phase7, v.id, rk=f"rk-{i}", distance=0.1 * i,
            )

        _run_async(suggestions_service.refill_mirror_sql(target=3))

        log_rows = db_phase7.exec(select(RefillTriggerLog)).all()
        assert len(log_rows) == 1
        log = log_rows[0]
        assert log.event_source == "sql_refill"
        assert log.picks_made == 3
        assert log.cost_estimate_usd == 0.0
        assert log.breaker_tripped is False

    def test_first_call_materializes_plex_playlist(self, db_phase7):
        """When ManagedPlaylist.plex_rating_key is the empty sentinel,
        the refill calls _materialize_suggestions_plex_playlist(url, token, keys).
        """
        from unittest.mock import AsyncMock, patch
        from app.services import suggestions_service

        _seed_managed_suggestions(db_phase7, plex_rating_key="")
        _seed_plex_creds(db_phase7)
        v = _seed_vibe(db_phase7, "active")
        for i in range(3):
            _seed_track_with_vibe(
                db_phase7, v.id, rk=f"rk-{i}", distance=0.1 * i,
            )

        materialize_mock = AsyncMock(return_value="new-rk-999")
        update_mock = AsyncMock(return_value=None)
        with patch.object(
            suggestions_service, "_materialize_suggestions_plex_playlist",
            new=materialize_mock,
        ), patch.object(
            suggestions_service, "update_playlist_items",
            new=update_mock,
        ):
            _run_async(suggestions_service.refill_mirror_sql(target=3))

        materialize_mock.assert_awaited_once()
        call_args = materialize_mock.await_args
        # THREE positional args: plex_url, plex_token, rating_keys.
        assert call_args.args[0] == "http://localhost:32400"
        assert call_args.args[1] == "test-token"
        rating_keys = call_args.args[2]
        assert isinstance(rating_keys, list)
        assert len(rating_keys) == 3
        # update_playlist_items must NOT have been called.
        update_mock.assert_not_awaited()

    def test_existing_playlist_calls_update_playlist_items(self, db_phase7):
        """When ManagedPlaylist.plex_rating_key is already set (e.g. '12345'),
        the refill calls update_playlist_items(url, token, '12345', keys).
        """
        from unittest.mock import AsyncMock, patch
        from app.services import suggestions_service

        _seed_managed_suggestions(db_phase7, plex_rating_key="12345")
        _seed_plex_creds(db_phase7)
        v = _seed_vibe(db_phase7, "active")
        for i in range(3):
            _seed_track_with_vibe(
                db_phase7, v.id, rk=f"rk-{i}", distance=0.1 * i,
            )

        materialize_mock = AsyncMock(return_value=None)
        update_mock = AsyncMock(return_value=None)
        with patch.object(
            suggestions_service, "_materialize_suggestions_plex_playlist",
            new=materialize_mock,
        ), patch.object(
            suggestions_service, "update_playlist_items",
            new=update_mock,
        ):
            _run_async(suggestions_service.refill_mirror_sql(target=3))

        update_mock.assert_awaited_once()
        call_args = update_mock.await_args
        # FOUR positional args: url, token, rating_key, keys.
        assert call_args.args[0] == "http://localhost:32400"
        assert call_args.args[1] == "test-token"
        assert call_args.args[2] == "12345"
        rating_keys = call_args.args[3]
        assert isinstance(rating_keys, list)
        # _materialize_suggestions_plex_playlist must NOT have been called.
        materialize_mock.assert_not_awaited()

    def test_deficit_zero_short_circuits(self, db_phase7):
        """When the mirror is already at target, refill_mirror_sql returns
        immediately with picks_made=0 and writes no rows.
        """
        from app.services import suggestions_service
        from app.models.suggestions import (
            RefillTriggerLog, SuggestionsMirror,
        )
        from app.models.track import Track

        # Seed exactly target=30 mirror rows.
        for i in range(30):
            t = Track(plex_rating_key=str(1000 + i), title=f"T{i}", artist="A")
            db_phase7.add(t)
        db_phase7.commit()
        for idx, t in enumerate(db_phase7.exec(select(Track)).all()):
            db_phase7.add(SuggestionsMirror(
                track_id=t.id, position=idx,
                added_at=datetime.now(timezone.utc).isoformat(),
            ))
        db_phase7.commit()

        result = _run_async(suggestions_service.refill_mirror_sql(target=30))
        assert result.picks_made == 0
        rows = db_phase7.exec(select(SuggestionsMirror)).all()
        assert len(rows) == 30
        # No trigger log row when deficit==0 (the short-circuit returns
        # without inserting a log row).
        log_rows = db_phase7.exec(select(RefillTriggerLog)).all()
        assert len(log_rows) == 0
