"""Phase 7 Plan 02 Task 2 — Tests for refill pipeline, shortlist, skip-tracking.

Builds on Plan 01's tests/test_suggestions_service.py — this file adds the
LLM-ranking refill, the Pitfall-10 validator, the cache-hit telemetry, and the
soft/hard negative signal lifecycle. Held in a separate test file purely for
diff-readability; pytest discovers both.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Generator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
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
    """Phase 5/6/7 tables + new Plan 02 tables (SuggestionHistory,
    NegativeSignal, RefillTriggerLog).
    """
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


def _seed_managed_suggestions(session: Session, plex_rk: str = "sugg-rk-1") -> None:
    """Seed ManagedPlaylist(kind='suggestions') with a non-empty rating key so
    the Plex push branch fires without hitting the deferred sentinel.
    """
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


def _seed_vibe(
    session: Session,
    name: str = "V1",
    energy=0.5, tempo=120.0, dance=0.6, valence=0.7,
    spread_e=0.2, spread_t=30.0, spread_d=0.2, spread_v=0.2,
) -> int:
    from app.models.vibe import Vibe

    v = Vibe(
        name=name,
        description=f"Test vibe {name}",
        centroid_energy=energy,
        centroid_tempo=tempo,
        centroid_danceability=dance,
        centroid_valence=valence,
        spread_energy=spread_e,
        spread_tempo=spread_t,
        spread_danceability=spread_d,
        spread_valence=spread_v,
        created_at=datetime.now(timezone.utc).isoformat(),
        is_active=True,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return v.id


def _seed_track(
    session: Session,
    rk: str,
    title: str = "T",
    artist: str = "A",
    energy=0.5, tempo=120.0, dance=0.6, valence=0.7,
    user_rating=None,
):
    from app.models.track import Track

    t = Track(
        plex_rating_key=rk,
        title=title,
        artist=artist,
        energy=energy,
        tempo=tempo,
        danceability=dance,
        valence=valence,
        user_rating=user_rating,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_taste_profile(session: Session, summary_text: str = "Test taste profile.") -> None:
    from app.models.taste_profile import TasteProfile

    tp = TasteProfile(
        id=1,
        rated_track_count=50,
        centroid_energy=0.5,
        centroid_tempo=120.0,
        centroid_danceability=0.6,
        centroid_valence=0.7,
        top_artists_json='[{"artist":"Foo","count":10}]',
        top_genres_json='[{"genre":"Rock","count":15}]',
        summary_text=summary_text,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(tp)
    session.commit()


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
# 2. Shortlist filtering (D-05 / D-06 / SUGG-07 / Pitfall 12)
# ---------------------------------------------------------------------------


class TestShortlist:
    def test_excludes_14_day_suggestion_history(self, db_phase7_v2):
        from app.models.suggestions import SuggestionHistory
        from app.services.suggestions_service import _build_shortlist_sync

        _seed_vibe(db_phase7_v2)

        # 10 unrated tracks within window (7 days ago) + 10 outside window (20 days ago).
        recent_track_ids = []
        old_track_ids = []
        for i in range(10):
            t = _seed_track(db_phase7_v2, rk=f"rk-recent-{i}",
                            title=f"Recent {i}", artist="A")
            recent_track_ids.append(t.id)
        for i in range(10):
            t = _seed_track(db_phase7_v2, rk=f"rk-old-{i}",
                            title=f"Old {i}", artist="A")
            old_track_ids.append(t.id)

        seven_days = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        twenty_days = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        for tid in recent_track_ids:
            db_phase7_v2.add(SuggestionHistory(
                track_id=tid, surfaced_at=seven_days, refill_id=1,
            ))
        for tid in old_track_ids:
            db_phase7_v2.add(SuggestionHistory(
                track_id=tid, surfaced_at=twenty_days, refill_id=1,
            ))
        db_phase7_v2.commit()

        shortlist = _build_shortlist_sync(target_size=50)
        track_ids = {row["track_id"] for row in shortlist}
        for tid in recent_track_ids:
            assert tid not in track_ids, f"recent track {tid} should be excluded"
        # At least some old-window tracks should appear (they are within 2σ
        # of centroid by default _seed_track features).
        assert any(tid in track_ids for tid in old_track_ids)

    def test_excludes_hard_negative_artists(self, db_phase7_v2):
        from app.models.suggestions import NegativeSignal
        from app.services.suggestions_service import _build_shortlist_sync

        _seed_vibe(db_phase7_v2)
        # 10 Coldplay tracks.
        for i in range(10):
            _seed_track(db_phase7_v2, rk=f"rk-cp-{i}",
                        title=f"Yellow {i}", artist="Coldplay")
        # 5 other-artist tracks for comparison.
        for i in range(5):
            _seed_track(db_phase7_v2, rk=f"rk-ok-{i}",
                        title=f"X {i}", artist="OtherBand")

        db_phase7_v2.add(NegativeSignal(
            track_id=None, artist="Coldplay", signal_type="hard_artist",
            created_at=datetime.now(timezone.utc).isoformat(),
            recovery_pending=True,
        ))
        db_phase7_v2.commit()

        shortlist = _build_shortlist_sync(target_size=50)
        artists = {row["artist"] for row in shortlist}
        assert "Coldplay" not in artists
        assert "OtherBand" in artists

    def test_2sigma_pre_filter_against_vibe_centroids(self, db_phase7_v2):
        from app.services.suggestions_service import _build_shortlist_sync

        # Vibe with tight spread.
        _seed_vibe(
            db_phase7_v2,
            energy=0.5, tempo=120.0, dance=0.6, valence=0.7,
            spread_e=0.1, spread_t=20.0, spread_d=0.1, spread_v=0.1,
        )
        # In-band track.
        _seed_track(
            db_phase7_v2, rk="in", title="In", artist="A",
            energy=0.55, tempo=130.0, dance=0.65, valence=0.75,
        )
        # Far out-of-band.
        _seed_track(
            db_phase7_v2, rk="out", title="Out", artist="B",
            energy=0.95, tempo=200.0, dance=0.10, valence=0.10,
        )
        shortlist = _build_shortlist_sync(target_size=50)
        rks = {row["plex_rating_key"] for row in shortlist}
        assert "in" in rks
        assert "out" not in rks

    def test_balanced_across_vibes_d_05(self, db_phase7_v2):
        from app.services.suggestions_service import _build_shortlist_sync

        # 6 vibes spread across feature space.
        vibe_centers = [
            (0.2, 80.0, 0.3, 0.4),
            (0.4, 100.0, 0.5, 0.5),
            (0.5, 120.0, 0.6, 0.6),
            (0.7, 140.0, 0.7, 0.7),
            (0.8, 160.0, 0.8, 0.8),
            (0.6, 110.0, 0.4, 0.5),
        ]
        for i, (e, tmp, d, v) in enumerate(vibe_centers):
            _seed_vibe(
                db_phase7_v2, name=f"V{i}",
                energy=e, tempo=tmp, dance=d, valence=v,
                spread_e=0.15, spread_t=25.0, spread_d=0.15, spread_v=0.15,
            )

        # Seed 25 tracks near each centroid (150 candidates).
        for vi, (e, tmp, d, v) in enumerate(vibe_centers):
            for i in range(25):
                _seed_track(
                    db_phase7_v2, rk=f"v{vi}-t{i}",
                    title=f"T{vi}-{i}", artist=f"A{vi}",
                    energy=e + (i - 12) * 0.005,
                    tempo=tmp + (i - 12) * 0.5,
                    dance=d + (i - 12) * 0.005,
                    valence=v + (i - 12) * 0.005,
                )

        shortlist = _build_shortlist_sync(target_size=50)
        # Bucket by assigned vibe.
        bucket = {}
        for row in shortlist:
            bucket.setdefault(row.get("assigned_vibe_id"), 0)
            bucket[row["assigned_vibe_id"]] += 1
        # ~50/6 ≈ 8.33 per vibe; allow ±3 per vibe.
        for v_id, count in bucket.items():
            assert count <= 12, f"vibe {v_id}: {count} candidates (should be ≤12)"
        # All 6 vibes represented (D-05 balanced shortlist).
        assert len(bucket) == 6

    def test_target_vibe_id_partitions_to_single_vibe(self, db_phase7_v2):
        """W2 — _build_shortlist_sync(target_vibe_id=N) returns ONLY candidates
        whose closest-centroid is vibe N.
        """
        from app.services.suggestions_service import _build_shortlist_sync

        vibe_centers = [
            (0.2, 80.0, 0.3, 0.4),
            (0.4, 100.0, 0.5, 0.5),
            (0.5, 120.0, 0.6, 0.6),
            (0.7, 140.0, 0.7, 0.7),
            (0.8, 160.0, 0.8, 0.8),
            (0.6, 110.0, 0.4, 0.5),
        ]
        vibe_ids = []
        for i, (e, tmp, d, v) in enumerate(vibe_centers):
            vid = _seed_vibe(
                db_phase7_v2, name=f"V{i}",
                energy=e, tempo=tmp, dance=d, valence=v,
                spread_e=0.15, spread_t=25.0, spread_d=0.15, spread_v=0.15,
            )
            vibe_ids.append(vid)

        # Seed 10 tracks per vibe (60 total).
        for vi, (e, tmp, d, v) in enumerate(vibe_centers):
            for i in range(10):
                _seed_track(
                    db_phase7_v2, rk=f"v{vi}-tt{i}",
                    title=f"T{vi}-{i}", artist=f"A{vi}",
                    energy=e + (i - 5) * 0.003,
                    tempo=tmp + (i - 5) * 0.3,
                    dance=d + (i - 5) * 0.003,
                    valence=v + (i - 5) * 0.003,
                )

        target_vid = vibe_ids[2]
        shortlist = _build_shortlist_sync(target_size=15, target_vibe_id=target_vid)
        # All candidates assigned_vibe_id == target_vid.
        for row in shortlist:
            assert row["assigned_vibe_id"] == target_vid
        # Total returned count <= 15 (and <= candidates-closest-to-vibe-target).
        assert len(shortlist) <= 15


# ---------------------------------------------------------------------------
# 3. Refill pipeline — cost breaker check ordering + LLM call shape
# ---------------------------------------------------------------------------


class TestRefillCostBreakerInvariant:
    def test_calls_cost_breaker_before_llm(self, db_phase7_v2):
        """Pitfall 11 invariant: check_or_raise runs BEFORE call_with_structured_output."""
        from app.services import suggestions_service

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}", title=f"T{i}", artist="A")

        order: List[str] = []

        async def fake_check_or_raise(purpose_prefix="suggestions_"):
            order.append("breaker")

        async def fake_call(*args, **kwargs):
            order.append("llm")
            from app.services.suggestions_service import (
                SuggestionRankingPick, SuggestionRankingResponse,
            )
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="ok"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch(
            "app.services.llm_cost_breaker.check_or_raise", new=fake_check_or_raise,
        ), patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = fake_call
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        assert order[0] == "breaker"
        assert "llm" in order
        assert order.index("breaker") < order.index("llm")

    def test_breaker_tripped_returns_without_llm_or_inserts(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.llm_cost_breaker import CostBreakerTrippedError
        from app.models.suggestions import SuggestionsMirror

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def trip(*args, **kwargs):
            raise CostBreakerTrippedError("daily_quota_50", "until")

        with patch(
            "app.services.llm_cost_breaker.check_or_raise", new=trip,
        ), patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client:
            client_instance = MagicMock()
            client_instance.call_with_structured_output = AsyncMock(
                side_effect=RuntimeError("must not be called"),
            )
            mock_get_client.return_value = client_instance
            result = _run_async(suggestions_service.refill_suggestions_queue())

        assert result.breaker_tripped is True
        # No SuggestionsMirror rows written.
        rows = db_phase7_v2.exec(select(SuggestionsMirror)).all()
        assert len(rows) == 0
        # State is cost_locked.
        assert suggestions_service.get_state().state == "cost_locked"


# ---------------------------------------------------------------------------
# 4. System prompt cache invariants
# ---------------------------------------------------------------------------


class TestSystemPromptCacheInvariants:
    def test_system_prompt_above_2048_tokens_proxy(self, db_phase7_v2):
        """Phase 6.2-style proxy: char length >= 8000 (~ >2048 tokens)."""
        from app.services import suggestions_service

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        captured = {}

        async def capture(*args, **kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt") or (args[0] if args else None)
            from app.services.suggestions_service import (
                SuggestionRankingPick, SuggestionRankingResponse,
            )
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="ok"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = capture
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        sp = captured["system_prompt"]
        assert sp is not None
        assert len(sp) >= 8000, f"system prompt was {len(sp)} chars (< 8000)"
        # Must embed required sections.
        assert "Suggestions" in sp or "suggestions" in sp.lower()
        # Embeds vibe info.
        assert "V1" in sp  # the seeded vibe name

    def test_purpose_is_suggestions_rank(self, db_phase7_v2):
        from app.services import suggestions_service

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        captured = {}

        async def cap(*args, **kwargs):
            captured["purpose"] = kwargs.get("purpose")
            captured["thinking"] = kwargs.get("thinking")
            from app.services.suggestions_service import (
                SuggestionRankingPick, SuggestionRankingResponse,
            )
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        assert captured["purpose"] == "suggestions_rank"
        assert captured["thinking"] == "off"


# ---------------------------------------------------------------------------
# 5. Pitfall 10 validation
# ---------------------------------------------------------------------------


class TestPitfall10Validation:
    def test_drops_out_of_range_index_with_retry(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.suggestions import SuggestionsMirror

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        call_count = {"n": 0}
        retry_prompt_seen = {"seen": False}

        async def cap(*args, **kwargs):
            call_count["n"] += 1
            user_prompt = kwargs.get("user_prompt") or args[1]
            if "VALIDATION FAILURE" in user_prompt:
                retry_prompt_seen["seen"] = True
                # second call: return only valid indices
                return SuggestionRankingResponse(picks=[
                    SuggestionRankingPick(candidate_index=1, rationale="r"),
                    SuggestionRankingPick(candidate_index=2, rationale="r"),
                ])
            # first call: 999 is out-of-range, 0 is valid
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=999, rationale="bad"),
                SuggestionRankingPick(candidate_index=0, rationale="ok"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            result = _run_async(suggestions_service.refill_suggestions_queue())

        assert call_count["n"] == 2, "expected one retry"
        assert retry_prompt_seen["seen"] is True
        # Validated/inserted picks are from the retry response.
        assert result.picks_inserted >= 1
        rows = db_phase7_v2.exec(select(SuggestionsMirror)).all()
        assert len(rows) >= 1

    def test_drops_duplicate_indices(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.suggestions import SuggestionsMirror

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=3, rationale="r"),
                SuggestionRankingPick(candidate_index=3, rationale="r-dup"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        # Only one SuggestionsMirror row inserted (dup dropped).
        rows = db_phase7_v2.exec(select(SuggestionsMirror)).all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# 6. Rationale, SuggestionHistory, RefillTriggerLog
# ---------------------------------------------------------------------------


class TestRefillSideEffects:
    def test_writes_rationale(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.suggestions import SuggestionsMirror

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="because vibes"),
                SuggestionRankingPick(candidate_index=1, rationale="energetic"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        rows = db_phase7_v2.exec(select(SuggestionsMirror)).all()
        rationales = {r.rationale for r in rows}
        assert "because vibes" in rationales
        assert "energetic" in rationales

    def test_writes_one_suggestionhistory_per_pick(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.suggestions import SuggestionHistory

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=i, rationale="r")
                for i in range(5)
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        rows = db_phase7_v2.exec(select(SuggestionHistory)).all()
        assert len(rows) == 5

    def test_writes_one_refilltriggerlog_row(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.suggestions import RefillTriggerLog

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        rows = db_phase7_v2.exec(select(RefillTriggerLog)).all()
        assert len(rows) == 1
        assert rows[0].event_source == "track_played"
        assert rows[0].breaker_tripped is False

    def test_calls_plex_update_playlist_items(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )

        _seed_managed_suggestions(db_phase7_v2, plex_rk="sentinel-plex-rk")
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        update_mock = AsyncMock(return_value=None)

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items", new=update_mock,
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        # update_playlist_items was called at least once with the seeded RK.
        assert update_mock.await_count >= 1
        # The plex_rating_key is one of the positional or kw args.
        call_args_str = str(update_mock.await_args)
        assert "sentinel-plex-rk" in call_args_str

    def test_first_refill_materializes_plex_playlist_when_sentinel(
        self, db_phase7_v2,
    ):
        """CR-01 regression — when the ManagedPlaylist(kind='suggestions') row
        carries the empty-string sentinel plex_rating_key, the first
        non-empty refill MUST call _materialize_suggestions_plex_playlist
        (lazily creates the Plex playlist) and MUST NOT call
        update_playlist_items (which would 404 with the sentinel key).
        """
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )

        # Seed the sentinel-row state (empty plex_rating_key).
        _seed_managed_suggestions(db_phase7_v2, plex_rk="")
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        materialize_mock = AsyncMock(return_value="new-plex-rk-123")
        update_mock = AsyncMock(return_value=None)

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "_materialize_suggestions_plex_playlist",
            new=materialize_mock,
        ), patch.object(
            suggestions_service, "update_playlist_items", new=update_mock,
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        # Materialize MUST be awaited; update_playlist_items MUST NOT be.
        assert materialize_mock.await_count >= 1, (
            "CR-01 — first refill with sentinel rk must call "
            "_materialize_suggestions_plex_playlist."
        )
        assert update_mock.await_count == 0, (
            "CR-01 — update_playlist_items must NOT be called against "
            "the empty-string sentinel rating key."
        )

    def test_subsequent_refill_uses_update_playlist_items(
        self, db_phase7_v2,
    ):
        """CR-01 regression — once the sentinel has been replaced with a real
        plex_rating_key, subsequent refills MUST use update_playlist_items
        (additive push, Pitfall 5) — not materialize again.
        """
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )

        _seed_managed_suggestions(db_phase7_v2, plex_rk="real-plex-rk-456")
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        materialize_mock = AsyncMock(return_value="should-not-be-used")
        update_mock = AsyncMock(return_value=None)

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "_materialize_suggestions_plex_playlist",
            new=materialize_mock,
        ), patch.object(
            suggestions_service, "update_playlist_items", new=update_mock,
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        assert materialize_mock.await_count == 0
        assert update_mock.await_count >= 1
        assert "real-plex-rk-456" in str(update_mock.await_args)

    def test_update_suggestions_managed_playlist_rk_sync_updates_in_place(
        self, db_phase7_v2,
    ):
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

        result = _update_suggestions_managed_playlist_rk_sync("new-rk-789", 7)
        assert result is True

        rows = db_phase7_v2.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.kind == "suggestions"
            )
        ).all()
        # Refresh detached objects.
        for r in rows:
            db_phase7_v2.refresh(r)
        assert len(rows) == 1, (
            "CR-01 — must update the row in place, not insert a new one."
        )
        assert rows[0].plex_rating_key == "new-rk-789"
        assert rows[0].track_count == 7

    def test_update_suggestions_managed_playlist_rk_sync_returns_false_when_missing(
        self, db_phase7_v2,
    ):
        """CR-01 helper — returns False (no insert) when no suggestions row
        exists. Caller treats this as bootstrap-skipped."""
        from app.services.suggestions_service import (
            _update_suggestions_managed_playlist_rk_sync,
        )

        result = _update_suggestions_managed_playlist_rk_sync("any-rk", 0)
        assert result is False

    def test_topup_to_target_after_drain(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.suggestions import SuggestionsMirror

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        # Seed 50 tracks for shortlist + 28 mirror rows so deficit=2.
        tracks = []
        for i in range(50):
            t = _seed_track(db_phase7_v2, rk=f"rk-{i}")
            tracks.append(t)
        for idx in range(28):
            db_phase7_v2.add(SuggestionsMirror(
                track_id=tracks[idx].id, position=idx,
                added_at=datetime.now(timezone.utc).isoformat(),
            ))
        db_phase7_v2.commit()

        async def cap(*args, **kwargs):
            # Return many picks; only 2 should be inserted.
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=i, rationale="r")
                for i in range(10)
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            result = _run_async(suggestions_service.refill_suggestions_queue())

        assert result.picks_inserted == 2
        all_rows = db_phase7_v2.exec(select(SuggestionsMirror)).all()
        assert len(all_rows) == 30


# ---------------------------------------------------------------------------
# 7. Skip-tracking: hard-track / hard-artist + recovery + soft sweep
# ---------------------------------------------------------------------------


class TestSkipTracking:
    def test_handle_dismiss_writes_hard_track_and_hard_artist(self, db_phase7_v2):
        from app.services.suggestions_service import handle_dismiss_track
        from app.models.suggestions import NegativeSignal

        track = _seed_track(db_phase7_v2, rk="dismiss-rk", artist="Coldplay")
        _run_async(handle_dismiss_track("dismiss-rk"))

        rows = db_phase7_v2.exec(select(NegativeSignal)).all()
        sigs = {(r.signal_type, r.artist, r.track_id) for r in rows}
        assert ("hard_track", None, track.id) in sigs or any(
            r.signal_type == "hard_track" and r.track_id == track.id
            for r in rows
        )
        assert any(
            r.signal_type == "hard_artist" and r.artist == "Coldplay"
            and r.recovery_pending is True
            for r in rows
        )

    def test_handle_artist_rating_recovery_clears_hard_artist(self, db_phase7_v2):
        from app.services.suggestions_service import handle_artist_rating_recovery
        from app.models.suggestions import NegativeSignal

        db_phase7_v2.add(NegativeSignal(
            artist="Coldplay", signal_type="hard_artist",
            created_at=datetime.now(timezone.utc).isoformat(),
            recovery_pending=True,
        ))
        db_phase7_v2.commit()

        # rating=6.0 raw = 3.0 stars; D-13 says 3+ stars clears the artist
        # deboost.
        _run_async(handle_artist_rating_recovery("Coldplay", rating=6.0))

        rows = db_phase7_v2.exec(select(NegativeSignal).where(
            NegativeSignal.artist == "Coldplay"
        )).all()
        # All hard_artist rows for Coldplay are now recovery_pending=False.
        hard = [r for r in rows if r.signal_type == "hard_artist"]
        assert all(r.recovery_pending is False for r in hard)

    def test_soft_negative_sweep_marks_unrated_tracks_14_days(self, db_phase7_v2):
        from app.services.suggestions_service import handle_soft_negative_sweep
        from app.models.suggestions import NegativeSignal, SuggestionHistory

        track = _seed_track(db_phase7_v2, rk="soft-rk", artist="A")
        # Update track to have last_viewed_at recent + user_rating None.
        track.last_viewed_at = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat()
        track.user_rating = None
        db_phase7_v2.add(track)

        # Seed SuggestionHistory row from 15 days ago (outside 14-day window).
        db_phase7_v2.add(SuggestionHistory(
            track_id=track.id,
            surfaced_at=(datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
            refill_id=1,
        ))
        db_phase7_v2.commit()

        n = _run_async(handle_soft_negative_sweep())
        assert n >= 1

        soft_rows = db_phase7_v2.exec(select(NegativeSignal).where(
            NegativeSignal.signal_type == "soft"
        )).all()
        assert any(r.track_id == track.id for r in soft_rows)

    def test_soft_negative_injected_into_user_prompt(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.suggestions import NegativeSignal

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")
        # 3 soft negatives.
        for title, artist in [
            ("Soft1", "ArtistOne"),
            ("Soft2", "ArtistTwo"),
            ("Soft3", "ArtistThree"),
        ]:
            t = _seed_track(db_phase7_v2, rk=f"soft-{title}", title=title, artist=artist)
            db_phase7_v2.add(NegativeSignal(
                track_id=t.id, signal_type="soft",
                created_at=datetime.now(timezone.utc).isoformat(),
                recovery_pending=False,
            ))
        db_phase7_v2.commit()

        captured = {}

        async def cap(*args, **kwargs):
            captured["user_prompt"] = kwargs.get("user_prompt") or args[1]
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            _run_async(suggestions_service.refill_suggestions_queue())

        up = captured["user_prompt"]
        assert "DO NOT PRIORITIZE" in up
        assert "Soft1" in up or "ArtistOne" in up


# ---------------------------------------------------------------------------
# 8. Cache-hit telemetry
# ---------------------------------------------------------------------------


class TestCacheTelemetry:
    def test_cache_hit_telemetry_second_consecutive_refill(self, db_phase7_v2):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.llm_usage import LLMUsage

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        # Pre-seed the LLMUsage table to simulate the first call having
        # written cache_creation_input_tokens=2500.
        call_count = {"n": 0}

        async def cap(*args, **kwargs):
            call_count["n"] += 1
            # Each call, inject one LLMUsage row reflecting cache behavior.
            with Session(suggestions_service.get_engine()) as s:
                if call_count["n"] == 1:
                    s.add(LLMUsage(
                        called_at=datetime.now(timezone.utc).isoformat(),
                        purpose="suggestions_rank",
                        model="claude-sonnet-4-6",
                        input_tokens=100,
                        cache_creation_input_tokens=2500,
                        cache_read_input_tokens=0,
                        output_tokens=50,
                        cost_estimate_usd=0.01,
                    ))
                else:
                    s.add(LLMUsage(
                        called_at=datetime.now(timezone.utc).isoformat(),
                        purpose="suggestions_rank",
                        model="claude-sonnet-4-6",
                        input_tokens=100,
                        cache_creation_input_tokens=0,
                        cache_read_input_tokens=2500,
                        output_tokens=50,
                        cost_estimate_usd=0.001,
                    ))
                s.commit()
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        with patch.object(
            suggestions_service, "_get_anthropic_client"
        ) as mock_get_client, patch.object(
            suggestions_service, "update_playlist_items",
            new=AsyncMock(return_value=None),
        ):
            client_instance = MagicMock()
            client_instance.call_with_structured_output = cap
            mock_get_client.return_value = client_instance
            r1 = _run_async(suggestions_service.refill_suggestions_queue())
            # Reset breaker state to avoid debounce on the second call.
            from app.services import llm_cost_breaker
            llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus()

            async def _bypass(*args, **kwargs):
                return None

            with patch(
                "app.services.llm_cost_breaker.check_or_raise",
                new=_bypass,
            ):
                r2 = _run_async(suggestions_service.refill_suggestions_queue())

        assert r1.cache_created is True
        assert r1.cache_hit is False
        assert r2.cache_hit is True
        assert r2.cache_created is False


# ---------------------------------------------------------------------------
# 9. W1 — warning on cache-creation == 0 for the FIRST observed call
# ---------------------------------------------------------------------------


class TestFirstCallCacheWarning:
    def test_warning_when_first_suggestions_rank_cache_creation_zero(
        self, db_phase7_v2, caplog,
    ):
        from app.services import suggestions_service
        from app.services.suggestions_service import (
            SuggestionRankingPick, SuggestionRankingResponse,
        )
        from app.models.llm_usage import LLMUsage

        _seed_managed_suggestions(db_phase7_v2)
        _seed_vibe(db_phase7_v2)
        _seed_taste_profile(db_phase7_v2)
        for i in range(50):
            _seed_track(db_phase7_v2, rk=f"rk-{i}")

        async def cap(*args, **kwargs):
            # Simulate the regression case: BOTH cache_creation == 0 AND
            # cache_read == 0 on the first observed call.
            with Session(suggestions_service.get_engine()) as s:
                s.add(LLMUsage(
                    called_at=datetime.now(timezone.utc).isoformat(),
                    purpose="suggestions_rank",
                    model="claude-sonnet-4-6",
                    input_tokens=100,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                    output_tokens=50,
                    cost_estimate_usd=0.01,
                ))
                s.commit()
            return SuggestionRankingResponse(picks=[
                SuggestionRankingPick(candidate_index=0, rationale="r"),
            ])

        import logging
        with caplog.at_level(logging.WARNING, logger="app.services.suggestions_service"):
            with patch.object(
                suggestions_service, "_get_anthropic_client"
            ) as mock_get_client, patch.object(
                suggestions_service, "update_playlist_items",
                new=AsyncMock(return_value=None),
            ):
                client_instance = MagicMock()
                client_instance.call_with_structured_output = cap
                mock_get_client.return_value = client_instance
                _run_async(suggestions_service.refill_suggestions_queue())

        assert any(
            "suggestions ranking cache creation not engaged" in rec.message
            for rec in caplog.records
        )
