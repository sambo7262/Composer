"""Phase 7 Plan 02 Task 2 — Tests for refill pipeline, shortlist, skip-tracking.

Phase 7.1 D-D1: this file was pruned. The LLM-ranking test classes
(TestShortlist, TestRefillCostBreakerInvariant, TestSystemPromptCacheInvariants,
TestPitfall10Validation, TestRefillSideEffects (LLM-ranking part),
TestCacheTelemetry, TestFirstCallCacheWarning,
TestSkipTracking::test_soft_negative_injected_into_user_prompt) were
deleted alongside the legacy refill_suggestions_queue /
build_suggestions_ranking_*_prompt / _validate_picks / _build_shortlist_sync /
SuggestionRankingPick / SuggestionRankingResponse internals they exercised.

What remains:

- TestNewTables — schema sanity for SuggestionHistory / NegativeSignal /
  RefillTriggerLog (still present in 7.1).
- ``_update_suggestions_managed_playlist_rk_sync`` helper tests — CR-01
  helper preserved in suggestions_service.py.
- TestSkipTracking — handle_dismiss_track / handle_artist_rating_recovery /
  handle_soft_negative_sweep are preserved by Plan 01 (see
  07.1-01-PLAN.md "DO NOT touch" list).

The TestRefillMirrorSql class (the SQL-driven replacement) lives in
tests/test_suggestions_service.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlmodel import Session, SQLModel, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_phase7_v2(test_engine) -> Generator[Session, None, None]:
    """Phase 5/6/7 + 7.1 tables — kept for backwards compat with the
    surviving tests in this file.
    """
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        DiscoveryState,  # Phase 7.1
        ManagedPlaylist,
        MigrationLog,
        SetupState,
        SlotInLog,
        TrackVibe,
        Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal,
        RefillTriggerLog,
        SuggestionHistory,
        SuggestionsMirror,
    )

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
    try:
        from app.services import llm_cost_breaker
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus()
    except (ImportError, AttributeError):
        pass
    yield


def _seed_managed_suggestions(
    session: Session, plex_rk: str = "sugg-rk-1",
) -> None:
    """Seed ManagedPlaylist(kind='suggestions') row."""
    from app.models.vibe import ManagedPlaylist

    session.add(
        ManagedPlaylist(
            kind="suggestions",
            vibe_id=None,
            plex_rating_key=plex_rk,
            composer_name="Composer · Suggestions",
            last_pushed_at=datetime.now(timezone.utc).isoformat(),
            track_count=0,
        )
    )
    session.commit()


def _seed_track(
    session: Session,
    rk: str,
    title: str = "T",
    artist: str = "A",
    user_rating=None,
):
    from app.models.track import Track

    t = Track(
        plex_rating_key=rk,
        title=title,
        artist=artist,
        user_rating=user_rating,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


# ---------------------------------------------------------------------------
# 1. New SQLModel tables exist
# ---------------------------------------------------------------------------


class TestNewTables:
    def test_suggestionhistory_table_created(self, db_phase7_v2, test_engine):
        conn = test_engine.raw_connection()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(suggestionhistory)")
            rows = cur.fetchall()
        finally:
            conn.close()
        by_name = {r[1]: r[2].upper() for r in rows}
        assert "track_id" in by_name
        assert "surfaced_at" in by_name
        assert "refill_id" in by_name

    def test_negativesignal_table_created(self, db_phase7_v2, test_engine):
        conn = test_engine.raw_connection()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(negativesignal)")
            rows = cur.fetchall()
        finally:
            conn.close()
        by_name = {r[1]: r[2].upper() for r in rows}
        assert "track_id" in by_name
        assert "artist" in by_name
        assert "signal_type" in by_name
        assert "created_at" in by_name
        assert "recovery_pending" in by_name

    def test_refilltriggerlog_table_created(self, db_phase7_v2, test_engine):
        conn = test_engine.raw_connection()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(refilltriggerlog)")
            rows = cur.fetchall()
        finally:
            conn.close()
        by_name = {r[1]: r[2].upper() for r in rows}
        for col in (
            "triggered_at",
            "event_source",
            "target_vibe_id",
            "candidates_evaluated",
            "picks_made",
            "latency_ms",
            "cost_estimate_usd",
            "breaker_tripped",
            "error",
        ):
            assert col in by_name, f"missing column {col}"


# ---------------------------------------------------------------------------
# 2. _update_suggestions_managed_playlist_rk_sync helper (CR-01)
# ---------------------------------------------------------------------------


class TestUpdateSuggestionsManagedPlaylistRkSync:
    def test_updates_in_place(self, db_phase7_v2):
        """CR-01 helper — _update_suggestions_managed_playlist_rk_sync MUST
        update the existing kind='suggestions' row in place (not insert a
        new row). The row's plex_rating_key changes from sentinel to the
        new rating key; track_count and last_pushed_at also refresh.
        """
        from app.models.vibe import ManagedPlaylist
        from app.services.suggestions_service import (
            _update_suggestions_managed_playlist_rk_sync,
        )

        _seed_managed_suggestions(db_phase7_v2, plex_rk="")
        before_count = len(
            db_phase7_v2.exec(select(ManagedPlaylist)).all()
        )
        assert before_count == 1

        result = _update_suggestions_managed_playlist_rk_sync(
            "new-rk-789", 7,
        )
        assert result is True

        rows = db_phase7_v2.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.kind == "suggestions"
            )
        ).all()
        for r in rows:
            db_phase7_v2.refresh(r)
        assert len(rows) == 1, (
            "CR-01 — must update the row in place, not insert a new one."
        )
        assert rows[0].plex_rating_key == "new-rk-789"
        assert rows[0].track_count == 7

    def test_returns_false_when_missing(self, db_phase7_v2):
        """CR-01 helper — returns False (no insert) when no suggestions row
        exists. Caller treats this as bootstrap-skipped."""
        from app.services.suggestions_service import (
            _update_suggestions_managed_playlist_rk_sync,
        )

        result = _update_suggestions_managed_playlist_rk_sync("any-rk", 0)
        assert result is False


# ---------------------------------------------------------------------------
# 3. Skip-tracking — Phase 7 dismiss + artist recovery + soft-negative sweep
#    (still in scope for 7.1; only the LLM-prompt-coupling test was deleted)
# ---------------------------------------------------------------------------


class TestSkipTracking:
    def test_handle_dismiss_writes_hard_track_and_hard_artist(
        self, db_phase7_v2,
    ):
        from app.models.suggestions import NegativeSignal
        from app.services.suggestions_service import handle_dismiss_track

        track = _seed_track(db_phase7_v2, rk="dismiss-rk", artist="Coldplay")
        _run_async(handle_dismiss_track("dismiss-rk"))

        rows = db_phase7_v2.exec(select(NegativeSignal)).all()
        assert any(
            r.signal_type == "hard_track" and r.track_id == track.id
            for r in rows
        )
        assert any(
            r.signal_type == "hard_artist" and r.artist == "Coldplay"
            and r.recovery_pending is True
            for r in rows
        )

    def test_handle_artist_rating_recovery_clears_hard_artist(
        self, db_phase7_v2,
    ):
        from app.models.suggestions import NegativeSignal
        from app.services.suggestions_service import (
            handle_artist_rating_recovery,
        )

        db_phase7_v2.add(NegativeSignal(
            artist="Coldplay", signal_type="hard_artist",
            created_at=datetime.now(timezone.utc).isoformat(),
            recovery_pending=True,
        ))
        db_phase7_v2.commit()

        # rating=6.0 raw = 3.0 stars; D-13 clears the artist deboost.
        _run_async(handle_artist_rating_recovery("Coldplay", rating=6.0))

        rows = db_phase7_v2.exec(
            select(NegativeSignal).where(
                NegativeSignal.artist == "Coldplay"
            )
        ).all()
        hard = [r for r in rows if r.signal_type == "hard_artist"]
        assert all(r.recovery_pending is False for r in hard)

    def test_soft_negative_sweep_marks_unrated_tracks_14_days(
        self, db_phase7_v2,
    ):
        from app.models.suggestions import NegativeSignal, SuggestionHistory
        from app.services.suggestions_service import handle_soft_negative_sweep

        track = _seed_track(db_phase7_v2, rk="soft-rk", artist="A")
        # Update track to have last_viewed_at recent + user_rating None.
        track.last_viewed_at = (
            datetime.now(timezone.utc) - timedelta(days=12)
        ).isoformat()
        track.user_rating = None
        db_phase7_v2.add(track)

        # Seed SuggestionHistory row from 15 days ago (outside 14-day window).
        db_phase7_v2.add(SuggestionHistory(
            track_id=track.id,
            surfaced_at=(
                datetime.now(timezone.utc) - timedelta(days=15)
            ).isoformat(),
            refill_id=1,
        ))
        db_phase7_v2.commit()

        n = _run_async(handle_soft_negative_sweep())
        assert n >= 1

        soft_rows = db_phase7_v2.exec(
            select(NegativeSignal).where(
                NegativeSignal.signal_type == "soft"
            )
        ).all()
        assert any(r.track_id == track.id for r in soft_rows)
