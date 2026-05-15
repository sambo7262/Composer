"""Phase 7.1 Plan 01 — DiscoveryState model + LLMUsage.error_text +
REQUIREMENTS.md regression tests + conftest fixture smoke test.

Plan 02 will append the discovery-eligible / adaptive-pick / cron
registration test classes; Plan 03 will append the AST regression for
no `max_tokens=2000` literals.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import get_engine, init_db
from app.models.llm_usage import LLMUsage
from app.models.vibe import DiscoveryState


class TestDiscoveryStateModel:
    def test_discovery_state_created_with_defaults(self, fresh_db):
        """Fresh DB: table exists, no row seeded automatically."""
        rows = fresh_db.exec(select(DiscoveryState)).all()
        assert rows == []

        row = DiscoveryState()
        fresh_db.add(row)
        fresh_db.commit()
        fresh_db.refresh(row)
        assert row.id == 1
        assert row.plays_since_last_discovery == 0
        assert row.last_discovery_run_at is None

    def test_discovery_state_id_pk_default_one(self, fresh_db):
        """Single-row invariant — second insert at id=1 raises IntegrityError."""
        fresh_db.add(DiscoveryState())
        fresh_db.commit()

        fresh_db.add(DiscoveryState(id=1, plays_since_last_discovery=99))
        with pytest.raises(IntegrityError):
            fresh_db.commit()
        fresh_db.rollback()

    def test_discovery_state_upsert_via_read_modify(self, fresh_db):
        """Mirrors SetupState upsert: read id=1 -> mutate -> commit."""
        fresh_db.add(DiscoveryState())
        fresh_db.commit()

        row = fresh_db.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row is not None
        row.plays_since_last_discovery = 7
        row.last_discovery_run_at = "2026-05-15T03:00:00+00:00"
        fresh_db.add(row)
        fresh_db.commit()

        reread = fresh_db.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert reread.plays_since_last_discovery == 7
        assert reread.last_discovery_run_at == "2026-05-15T03:00:00+00:00"


class TestLLMUsageErrorText:
    """Phase 7.1 — additive nullable error_text column on LLMUsage."""

    def test_error_text_column_exists_after_init_db(self, fresh_db):
        from sqlalchemy import text

        with get_engine().connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(llmusage)")).all()
        col_names = {row[1] for row in cols}
        assert "error_text" in col_names, (
            "Phase 7.1 _migrate_add_columns did not add LLMUsage.error_text"
        )

    def test_error_text_persists_when_set(self, fresh_db):
        row = LLMUsage(
            called_at="2026-05-15T03:00:00+00:00",
            purpose="discovery_weekly_error",
            model="claude-sonnet-4-6",
            input_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=0,
            cost_estimate_usd=0.0,
            error_text="boom",
        )
        fresh_db.add(row)
        fresh_db.commit()
        fresh_db.refresh(row)
        assert row.error_text == "boom"

    def test_error_text_defaults_to_null(self, fresh_db):
        """Backwards-compat: existing call sites that don't pass
        error_text must still work."""
        row = LLMUsage(
            called_at="2026-05-15T03:00:00+00:00",
            purpose="taste_profile_summary",
            model="claude-sonnet-4-6",
            input_tokens=100,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=50,
            cost_estimate_usd=0.001,
        )
        fresh_db.add(row)
        fresh_db.commit()
        fresh_db.refresh(row)
        assert row.error_text is None

    def test_migration_idempotent(self, fresh_db):
        """Running init_db() twice must not re-add the column or raise."""
        from sqlalchemy import text

        # Already ran once via fresh_db fixture; run again.
        init_db()
        with get_engine().connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(llmusage)")).all()
        col_names = [row[1] for row in cols]
        assert col_names.count("error_text") == 1, (
            "_migrate_add_columns is not idempotent for LLMUsage.error_text"
        )


class TestConftestFixtureWiring:
    """W13 — verify db_with_phase7 was correctly promoted to conftest.py
    (importable from this NEW test file without local definition).
    """

    def test_db_with_phase7_yields_session_with_phase7_tables(
        self, db_with_phase7,
    ):
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track

        t = Track(plex_rating_key="conftest-smoke", title="t", artist="a")
        db_with_phase7.add(t)
        db_with_phase7.commit()
        db_with_phase7.refresh(t)
        assert t.id is not None

        mp = SuggestionsMirror(
            track_id=t.id,
            position=0,
            added_at="2026-05-15T03:00:00+00:00",
        )
        db_with_phase7.add(mp)
        db_with_phase7.commit()
        assert mp.id is not None


class TestRequirementsMdHasNewSuggIds:
    """Sanity check — SUGG-12, SUGG-13, SUGG-14, and SUGG-04 (REWORKED) were
    committed out-of-band in `dcfd855`. Plan 01 does NOT modify
    REQUIREMENTS.md any further; this test ensures they don't regress.
    """

    def test_sugg_12_13_14_present_and_sugg_04_marked_reworked(self):
        req_path = (
            Path(__file__).parent.parent
            / ".planning"
            / "REQUIREMENTS.md"
        )
        text = req_path.read_text()

        assert "**SUGG-12**" in text, "SUGG-12 missing from REQUIREMENTS.md"
        assert "**SUGG-13**" in text, "SUGG-13 missing from REQUIREMENTS.md"
        assert "**SUGG-14**" in text, "SUGG-14 missing from REQUIREMENTS.md"
        assert "REWORKED" in text and "7.1" in text, (
            "SUGG-04 not marked as REWORKED with 7.1 cross-link"
        )


# ---------------------------------------------------------------------------
# Task 3 — suggestions_discovery module: counter + state singleton + AST
# ---------------------------------------------------------------------------


def _run_async(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestIncrementPlaysSinceLastDiscovery:
    """Phase 7.1 D-A3 — counter persistence on DiscoveryState.id=1."""

    def test_increment_initializes_state_row(self, fresh_db):
        """On a fresh DB with no DiscoveryState row, calling
        ``await increment_plays_since_last_discovery()`` creates the id=1
        row with ``plays_since_last_discovery=1``.
        """
        from app.services.suggestions_discovery import (
            increment_plays_since_last_discovery,
        )

        new_count = _run_async(increment_plays_since_last_discovery())
        assert new_count == 1

        row = fresh_db.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row is not None
        assert row.plays_since_last_discovery == 1

    def test_increment_increments_existing_row(self, fresh_db):
        """With a seeded ``DiscoveryState(id=1, plays_since_last_discovery=5)``,
        the function increments to 6 and returns 6.
        """
        from app.services.suggestions_discovery import (
            increment_plays_since_last_discovery,
        )

        fresh_db.add(DiscoveryState(id=1, plays_since_last_discovery=5))
        fresh_db.commit()

        new_count = _run_async(increment_plays_since_last_discovery())
        assert new_count == 6

        # Refresh from a new query to confirm persistence.
        fresh_db.expire_all()
        row = fresh_db.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row.plays_since_last_discovery == 6


class TestDiscoveryServiceStatus:
    """Phase 5 D-08 module-singleton state pattern."""

    def test_get_state_returns_module_singleton(self, fresh_db):
        """Initial state='idle', last_increment_at=None. After a counter
        increment, last_increment_at is populated.
        """
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import (
            DiscoveryServiceStatus, get_state,
            increment_plays_since_last_discovery,
        )

        # Reset the module-level singleton so this test sees a fresh state
        # regardless of earlier-test ordering (Phase 5 D-08 pattern).
        suggestions_discovery._status = DiscoveryServiceStatus()

        st_before = get_state()
        assert isinstance(st_before, DiscoveryServiceStatus)
        assert st_before.state == "idle"
        assert st_before.last_increment_at is None

        _run_async(increment_plays_since_last_discovery())

        st_after = get_state()
        assert st_after.last_increment_at is not None


class TestComputeAdaptivePickCount:
    """Phase 7.1 D-A3 — pure function mapping plays to 3-7 picks."""

    def test_light_listening_returns_3(self):
        from app.services.suggestions_discovery import (
            compute_adaptive_pick_count,
        )
        assert compute_adaptive_pick_count(0) == 3
        assert compute_adaptive_pick_count(10) == 3

    def test_medium_listening_returns_5(self):
        from app.services.suggestions_discovery import (
            compute_adaptive_pick_count,
        )
        assert compute_adaptive_pick_count(11) == 5
        assert compute_adaptive_pick_count(25) == 5

    def test_heavy_listening_returns_7(self):
        from app.services.suggestions_discovery import (
            compute_adaptive_pick_count,
        )
        assert compute_adaptive_pick_count(26) == 7
        assert compute_adaptive_pick_count(100) == 7


# ---------------------------------------------------------------------------
# Phase 7.1 Plan 02 Task 1 — D-A1 discovery-eligible filter + constants +
# DiscoveryPicksResponse pydantic shape. W12 boundary test included.
# ---------------------------------------------------------------------------


class TestDiscoveryEligible:
    """D-A1 + W12 — discovery candidate pool filter (90-day unplayed window,
    minus tracks already in mirror / hard negatives / 14-day SuggestionHistory).
    """

    def test_compute_discovery_eligible_returns_only_unplayed_or_old_tracks(
        self, db_with_phase7,
    ):
        """Seed 4 tracks: A (NULL), B (now), C (now-91d), D (now-30d).
        Only A and C are eligible.
        """
        from datetime import datetime, timedelta, timezone

        from app.models.track import Track
        from app.services.suggestions_discovery import (
            compute_discovery_eligible,
        )

        now = datetime.now(timezone.utc)
        db_with_phase7.add_all([
            Track(plex_rating_key="A", title="A", artist="ArtA",
                  last_viewed_at=None),
            Track(plex_rating_key="B", title="B", artist="ArtB",
                  last_viewed_at=now.isoformat()),
            Track(plex_rating_key="C", title="C", artist="ArtC",
                  last_viewed_at=(now - timedelta(days=91)).isoformat()),
            Track(plex_rating_key="D", title="D", artist="ArtD",
                  last_viewed_at=(now - timedelta(days=30)).isoformat()),
        ])
        db_with_phase7.commit()

        eligible = _run_async(compute_discovery_eligible())
        keys = {e["plex_rating_key"] for e in eligible}
        assert keys == {"A", "C"}, (
            f"Expected only A (NULL) and C (>90d), got {keys}"
        )

    def test_track_at_exactly_90_day_threshold_is_excluded(
        self, db_with_phase7,
    ):
        """W12 boundary — t.last_viewed_at < :cutoff is STRICT less-than;
        a track at the cutoff exactly is NOT included.
        Documents the ISO 8601 lexicographic ordering reliance.
        """
        from datetime import datetime, timedelta, timezone

        from app.models.track import Track
        from app.services.suggestions_discovery import (
            compute_discovery_eligible,
        )

        now = datetime.now(timezone.utc)
        cutoff_iso = (now - timedelta(days=90)).isoformat()
        db_with_phase7.add(Track(
            plex_rating_key="boundary-track",
            title="At Boundary",
            artist="Test",
            last_viewed_at=cutoff_iso,
        ))
        db_with_phase7.commit()

        # Patch datetime.now inside the helper to a fixed instant so the
        # 90-day cutoff exactly equals our seeded last_viewed_at — without
        # this freeze, the function's own now() drifts past the seeded
        # timestamp by the microseconds of test execution, and the row
        # appears eligible (false positive for the boundary case).
        from unittest.mock import patch

        from app.services import suggestions_discovery as _sd
        from datetime import datetime as _dt
        class _FrozenNow(_dt):
            @classmethod
            def now(cls, tz=None):
                return now if tz is None else now.astimezone(tz)
        with patch.object(_sd, "datetime", _FrozenNow):
            eligible = _run_async(compute_discovery_eligible())
        keys = {e["plex_rating_key"] for e in eligible}
        assert "boundary-track" not in keys, (
            "Track at exactly 90-day cutoff should be excluded "
            "(strict less-than boundary; W12 invariant)"
        )

    def test_compute_discovery_eligible_excludes_in_mirror(
        self, db_with_phase7,
    ):
        """A track that satisfies the 90-day rule but is currently in
        SuggestionsMirror is excluded.
        """
        from app.models.suggestions import SuggestionsMirror
        from app.models.track import Track
        from app.services.suggestions_discovery import (
            compute_discovery_eligible,
        )

        t = Track(plex_rating_key="in-mirror", title="t", artist="a",
                  last_viewed_at=None)
        db_with_phase7.add(t)
        db_with_phase7.commit()
        db_with_phase7.refresh(t)
        db_with_phase7.add(SuggestionsMirror(
            track_id=t.id, position=0, added_at="2026-05-15T00:00:00+00:00",
        ))
        db_with_phase7.commit()

        eligible = _run_async(compute_discovery_eligible())
        keys = {e["plex_rating_key"] for e in eligible}
        assert "in-mirror" not in keys

    def test_compute_discovery_eligible_excludes_hard_negative_track(
        self, db_with_phase7,
    ):
        """A track with NegativeSignal(signal_type='hard_track') is excluded."""
        from app.models.suggestions import NegativeSignal
        from app.models.track import Track
        from app.services.suggestions_discovery import (
            compute_discovery_eligible,
        )

        t = Track(plex_rating_key="hard-track", title="t", artist="a",
                  last_viewed_at=None)
        db_with_phase7.add(t)
        db_with_phase7.commit()
        db_with_phase7.refresh(t)
        db_with_phase7.add(NegativeSignal(
            track_id=t.id, signal_type="hard_track",
            created_at="2026-05-15T00:00:00+00:00",
        ))
        db_with_phase7.commit()

        eligible = _run_async(compute_discovery_eligible())
        keys = {e["plex_rating_key"] for e in eligible}
        assert "hard-track" not in keys

    def test_compute_discovery_eligible_excludes_hard_negative_artist_with_recovery_pending(
        self, db_with_phase7,
    ):
        """A track whose artist has NegativeSignal(signal_type='hard_artist',
        recovery_pending=True) is excluded.
        """
        from app.models.suggestions import NegativeSignal
        from app.models.track import Track
        from app.services.suggestions_discovery import (
            compute_discovery_eligible,
        )

        db_with_phase7.add(Track(
            plex_rating_key="hard-artist-track",
            title="t", artist="HardArtistName",
            last_viewed_at=None,
        ))
        db_with_phase7.add(NegativeSignal(
            artist="HardArtistName", signal_type="hard_artist",
            recovery_pending=True,
            created_at="2026-05-15T00:00:00+00:00",
        ))
        db_with_phase7.commit()

        eligible = _run_async(compute_discovery_eligible())
        keys = {e["plex_rating_key"] for e in eligible}
        assert "hard-artist-track" not in keys

    def test_compute_discovery_eligible_excludes_recent_suggestion_history(
        self, db_with_phase7,
    ):
        """A track surfaced in SuggestionHistory within 14 days is excluded."""
        from datetime import datetime, timedelta, timezone

        from app.models.suggestions import SuggestionHistory
        from app.models.track import Track
        from app.services.suggestions_discovery import (
            compute_discovery_eligible,
        )

        t = Track(plex_rating_key="recent-history",
                  title="t", artist="a", last_viewed_at=None)
        db_with_phase7.add(t)
        db_with_phase7.commit()
        db_with_phase7.refresh(t)
        # Surfaced 3 days ago — inside the 14-day window.
        recent_iso = (
            datetime.now(timezone.utc) - timedelta(days=3)
        ).isoformat()
        db_with_phase7.add(SuggestionHistory(
            track_id=t.id, surfaced_at=recent_iso, refill_id=1,
        ))
        db_with_phase7.commit()

        eligible = _run_async(compute_discovery_eligible())
        keys = {e["plex_rating_key"] for e in eligible}
        assert "recent-history" not in keys


class TestDiscoveryConstants:
    """Phase 7.1 Plan 02 — module-level constants."""

    def test_weekly_discovery_picks_range_constant(self):
        from app.services.suggestions_discovery import (
            WEEKLY_DISCOVERY_PICKS_RANGE,
        )
        assert WEEKLY_DISCOVERY_PICKS_RANGE == (3, 7)

    def test_discovery_max_tokens_floor_constant(self):
        from app.services.suggestions_discovery import (
            DISCOVERY_MAX_TOKENS_FLOOR,
        )
        assert isinstance(DISCOVERY_MAX_TOKENS_FLOOR, int)
        assert DISCOVERY_MAX_TOKENS_FLOOR >= 8000

    def test_discovery_purpose_prefix(self):
        from app.services.suggestions_discovery import DISCOVERY_PURPOSE
        assert DISCOVERY_PURPOSE == "discovery_weekly"
        assert DISCOVERY_PURPOSE.startswith("discovery_")


class TestDiscoveryPicksResponseShape:
    """Pydantic response model for the weekly LLM discovery call."""

    def test_pydantic_parses_minimal_payload(self):
        from app.services.suggestions_discovery import (
            DiscoveryPicksResponse,
        )

        response = DiscoveryPicksResponse(
            picks=[{"track_id": 1, "rationale": "test"}]
        )
        assert len(response.picks) == 1
        assert response.picks[0].track_id == 1
        assert response.picks[0].rationale == "test"

    def test_pydantic_rejects_missing_rationale(self):
        from pydantic import ValidationError

        from app.services.suggestions_discovery import (
            DiscoveryPicksResponse,
        )

        with pytest.raises(ValidationError):
            DiscoveryPicksResponse(picks=[{"track_id": 1}])

    def test_pydantic_rejects_missing_track_id(self):
        from pydantic import ValidationError

        from app.services.suggestions_discovery import (
            DiscoveryPicksResponse,
        )

        with pytest.raises(ValidationError):
            DiscoveryPicksResponse(picks=[{"rationale": "missing id"}])


# ---------------------------------------------------------------------------
# Phase 7.1 Plan 02 Task 2 — discovery_call_weekly LLM handler tests.
# 13 tests covering happy-path, no-candidates / no-taste skip, breaker-trip,
# success-counter-reset, failure-no-reset, error_text capture, hallucinated-
# ID filter, and Blocker #5 retry semantics (MaxTokensTruncationError only).
# ---------------------------------------------------------------------------


def _seed_discovery_environment(
    db,
    *,
    num_candidates: int = 5,
    plays_since_last_discovery: int = 15,
    last_discovery_run_at=None,
    seed_taste_profile: bool = True,
):
    """Helper to seed the DB for discovery_call_weekly tests.

    Returns a dict with the seeded track ids in ``track_ids`` (in seed
    order). Used by every TestDiscoveryCallWeekly test that needs a
    candidate pool.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.taste_profile import TasteProfile
    from app.models.track import Track
    from app.models.vibe import DiscoveryState

    now = datetime.now(timezone.utc)
    seeded_track_ids: list = []
    for i in range(num_candidates):
        t = Track(
            plex_rating_key=f"cand-{i}",
            title=f"Candidate {i}",
            artist=f"ArtistC{i}",
            # Make it eligible: never played OR > 90d ago.
            last_viewed_at=(now - timedelta(days=120)).isoformat() if i % 2
                            else None,
        )
        db.add(t)
    db.commit()
    # Refresh to get ids.
    from app.models.track import Track as _T
    for i in range(num_candidates):
        row = db.exec(
            select(_T).where(_T.plex_rating_key == f"cand-{i}")
        ).first()
        if row is not None:
            seeded_track_ids.append(row.id)

    # Seed DiscoveryState.
    db.add(DiscoveryState(
        id=1,
        plays_since_last_discovery=plays_since_last_discovery,
        last_discovery_run_at=last_discovery_run_at,
    ))
    db.commit()

    # Seed TasteProfile with a non-empty summary_text.
    if seed_taste_profile:
        db.add(TasteProfile(
            id=1,
            rated_track_count=50,
            summary_text="The user enjoys energetic guitar-driven tracks.",
            computed_at=now.isoformat(),
        ))
        db.commit()

    return {"track_ids": seeded_track_ids}


def _make_fake_anthropic_client(call_mock):
    """Build a stub AnthropicClient class that returns an instance whose
    ``call_with_structured_output`` is the provided AsyncMock.
    """
    class _FakeClient:
        def __init__(self, *a, **kw):
            self.call_with_structured_output = call_mock

    return _FakeClient


class TestDiscoveryCallWeekly:
    """Phase 7.1 SUGG-13 — APScheduler-fired weekly LLM discovery call.

    Mocks ``AnthropicClient``, the taste-profile helper, and the cost
    breaker at the ``app.services.suggestions_discovery`` module attribute
    layer (mirrors the test_suggestions_service.py monkeypatch pattern).
    """

    def test_discovery_call_weekly_skips_when_no_eligible_candidates(
        self, db_with_phase7, monkeypatch,
    ):
        """No eligible candidates → log warning, write LLMUsage with
        purpose='discovery_weekly_skipped_no_candidates' and error_text,
        exit cleanly. Counter NOT reset, last_discovery_run_at NOT stamped.
        """
        from app.models.llm_usage import LLMUsage
        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import discovery_call_weekly

        # Pre-seed DiscoveryState so we can verify it's NOT reset.
        db_with_phase7.add(DiscoveryState(
            id=1, plays_since_last_discovery=15,
            last_discovery_run_at=None,
        ))
        db_with_phase7.commit()

        # No tracks seeded — eligible pool is empty.
        # Mock cost breaker to pass (won't reach it though).
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        # Counter unchanged.
        db_with_phase7.expire_all()
        row = db_with_phase7.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row.plays_since_last_discovery == 15
        assert row.last_discovery_run_at is None

        # LLMUsage row for skip with error_text populated.
        usage_rows = db_with_phase7.exec(
            select(LLMUsage).where(
                LLMUsage.purpose.like("discovery_weekly_%")
            )
        ).all()
        assert len(usage_rows) >= 1
        skip_rows = [
            r for r in usage_rows if "no_candidates" in (r.purpose or "")
        ]
        assert len(skip_rows) == 1
        assert skip_rows[0].error_text == "no eligible candidates"

    def test_discovery_call_weekly_skips_when_no_taste_profile(
        self, db_with_phase7, monkeypatch,
    ):
        """No cached taste profile → log warning, exit cleanly, no LLM
        invocation. ``LLMUsage.error_text="no cached taste profile"``.
        """
        from app.models.llm_usage import LLMUsage
        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import discovery_call_weekly

        # Seed env BUT with seed_taste_profile=False.
        _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
            seed_taste_profile=False,
        )

        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        db_with_phase7.expire_all()
        row = db_with_phase7.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row.plays_since_last_discovery == 15
        assert row.last_discovery_run_at is None

        usage_rows = db_with_phase7.exec(
            select(LLMUsage).where(
                LLMUsage.purpose.like("discovery_weekly_%")
            )
        ).all()
        skip_rows = [
            r for r in usage_rows
            if "no_taste_profile" in (r.purpose or "")
        ]
        assert len(skip_rows) == 1
        assert skip_rows[0].error_text == "no cached taste profile"

    def test_discovery_call_weekly_invokes_llm_with_correct_purpose_and_max_tokens(
        self, db_with_phase7, monkeypatch,
    ):
        """plays_since_last_discovery=15 → 5 picks requested per D-A3.
        LLM call kwargs: purpose='discovery_weekly',
        max_tokens=DISCOVERY_MAX_TOKENS_FLOOR,
        response_model=DiscoveryPicksResponse.
        """
        from unittest.mock import AsyncMock

        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import (
            DISCOVERY_MAX_TOKENS_FLOOR, DiscoveryPick,
            DiscoveryPicksResponse, discovery_call_weekly,
        )

        env = _seed_discovery_environment(
            db_with_phase7, num_candidates=50,
            plays_since_last_discovery=15,
        )

        response = DiscoveryPicksResponse(picks=[
            DiscoveryPick(track_id=env["track_ids"][0], rationale="ok"),
            DiscoveryPick(track_id=env["track_ids"][1], rationale="ok"),
            DiscoveryPick(track_id=env["track_ids"][2], rationale="ok"),
            DiscoveryPick(track_id=env["track_ids"][3], rationale="ok"),
            DiscoveryPick(track_id=env["track_ids"][4], rationale="ok"),
        ])
        call_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        assert call_mock.await_count == 1
        kwargs = call_mock.await_args.kwargs
        assert kwargs["purpose"] == "discovery_weekly"
        assert kwargs["max_tokens"] == DISCOVERY_MAX_TOKENS_FLOOR
        assert kwargs["response_model"] is DiscoveryPicksResponse
        # User prompt mentions "exactly 5" picks (15 plays → 5 picks).
        assert "exactly 5" in kwargs["user_prompt"]

    def test_discovery_call_weekly_writes_picks_to_mirror_with_rationale(
        self, db_with_phase7, monkeypatch,
    ):
        """5 valid picks → 5 SuggestionsMirror rows with LLM-authored rationale."""
        from unittest.mock import AsyncMock

        from app.models.suggestions import SuggestionsMirror
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import (
            DiscoveryPick, DiscoveryPicksResponse, discovery_call_weekly,
        )

        env = _seed_discovery_environment(
            db_with_phase7, num_candidates=10,
            plays_since_last_discovery=15,
        )
        picks_data = [
            DiscoveryPick(track_id=env["track_ids"][i],
                          rationale=f"LLM rationale {i}")
            for i in range(5)
        ]
        response = DiscoveryPicksResponse(picks=picks_data)
        call_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        mirror_rows = db_with_phase7.exec(select(SuggestionsMirror)).all()
        assert len(mirror_rows) == 5
        rationales = {r.rationale for r in mirror_rows}
        assert rationales == {f"LLM rationale {i}" for i in range(5)}

    def test_discovery_call_weekly_filters_hallucinated_track_ids(
        self, db_with_phase7, monkeypatch,
    ):
        """LLM returns picks where 2/5 track_ids are NOT in the pool.
        Only the 3 valid picks are written; the 2 hallucinated IDs are
        filtered; warning logged.
        """
        from unittest.mock import AsyncMock

        from app.models.suggestions import SuggestionsMirror
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import (
            DiscoveryPick, DiscoveryPicksResponse, discovery_call_weekly,
        )

        env = _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        # 3 valid + 2 hallucinated (impossibly high IDs).
        response = DiscoveryPicksResponse(picks=[
            DiscoveryPick(track_id=env["track_ids"][0], rationale="ok-0"),
            DiscoveryPick(track_id=env["track_ids"][1], rationale="ok-1"),
            DiscoveryPick(track_id=env["track_ids"][2], rationale="ok-2"),
            DiscoveryPick(track_id=999_999, rationale="hallucinated-1"),
            DiscoveryPick(track_id=999_998, rationale="hallucinated-2"),
        ])
        call_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        mirror_rows = db_with_phase7.exec(select(SuggestionsMirror)).all()
        assert len(mirror_rows) == 3
        track_ids = {r.track_id for r in mirror_rows}
        assert track_ids == set(env["track_ids"][:3])

    def test_discovery_call_weekly_resets_counter_on_success(
        self, db_with_phase7, monkeypatch,
    ):
        """Pre-seed counter=15. On LLM success: counter == 0,
        last_discovery_run_at is a recent ISO string.
        """
        from unittest.mock import AsyncMock

        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import (
            DiscoveryPick, DiscoveryPicksResponse, discovery_call_weekly,
        )

        env = _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        response = DiscoveryPicksResponse(picks=[
            DiscoveryPick(track_id=env["track_ids"][0], rationale="ok"),
        ])
        call_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        db_with_phase7.expire_all()
        row = db_with_phase7.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row.plays_since_last_discovery == 0
        assert row.last_discovery_run_at is not None
        assert row.last_discovery_run_at.startswith("20")

    def test_discovery_call_weekly_does_not_reset_counter_on_failure(
        self, db_with_phase7, monkeypatch,
    ):
        """LLM raises → counter unchanged, last_discovery_run_at still NULL."""
        from unittest.mock import AsyncMock

        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import discovery_call_weekly

        _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        call_mock = AsyncMock(side_effect=RuntimeError("network down"))
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        db_with_phase7.expire_all()
        row = db_with_phase7.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row.plays_since_last_discovery == 15
        assert row.last_discovery_run_at is None

    def test_discovery_call_weekly_writes_llmusage_error_row_on_failure(
        self, db_with_phase7, monkeypatch,
    ):
        """LLM raises RuntimeError → LLMUsage row with purpose
        ending in '_error' AND error_text contains 'RuntimeError'+'network'.
        DiscoveryServiceStatus.state=='error'.
        """
        from unittest.mock import AsyncMock

        from app.models.llm_usage import LLMUsage
        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import (
            DiscoveryServiceStatus, discovery_call_weekly, get_state,
        )

        # Reset module-singleton state.
        suggestions_discovery._status = DiscoveryServiceStatus()

        _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        call_mock = AsyncMock(side_effect=RuntimeError("network down"))
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        usage_rows = db_with_phase7.exec(
            select(LLMUsage).where(
                LLMUsage.purpose.like("discovery_weekly_%error%")
            )
        ).all()
        assert len(usage_rows) >= 1
        text = usage_rows[0].error_text or ""
        assert "RuntimeError" in text
        assert "network" in text

        st = get_state()
        assert st.state == "error"
        assert "RuntimeError" in (st.last_error or "")

    def test_discovery_call_weekly_handles_cost_breaker_trip(
        self, db_with_phase7, monkeypatch,
    ):
        """check_or_raise raises CostBreakerTrippedError → no LLM call,
        LLMUsage logged with error_text='breaker:...',
        DiscoveryServiceStatus.state=='cost_locked', counter NOT reset.
        """
        from unittest.mock import AsyncMock

        from app.models.llm_usage import LLMUsage
        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery
        from app.services.llm_cost_breaker import CostBreakerTrippedError
        from app.services.suggestions_discovery import (
            DiscoveryServiceStatus, discovery_call_weekly, get_state,
        )

        suggestions_discovery._status = DiscoveryServiceStatus()

        _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        call_mock = AsyncMock(side_effect=RuntimeError("should not run"))
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _trip(*a, **kw):
            raise CostBreakerTrippedError(
                "daily_quota_50", "2026-05-16T00:00:00+00:00",
            )
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _trip,
        )

        _run_async(discovery_call_weekly())

        assert call_mock.await_count == 0
        usage_rows = db_with_phase7.exec(
            select(LLMUsage).where(
                LLMUsage.purpose.like("%cost_locked%")
            )
        ).all()
        assert len(usage_rows) >= 1
        assert "breaker" in (usage_rows[0].error_text or "")

        st = get_state()
        assert st.state == "cost_locked"

        db_with_phase7.expire_all()
        row = db_with_phase7.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row.plays_since_last_discovery == 15

    def test_discovery_call_weekly_retries_once_on_max_tokens_truncation_error(
        self, db_with_phase7, monkeypatch,
    ):
        """Blocker #5 — MaxTokensTruncationError is the ONLY signal that
        triggers the doubled-budget retry. Second call sees max_tokens * 2.
        """
        from unittest.mock import AsyncMock

        from app.models.suggestions import SuggestionsMirror
        from app.services import suggestions_discovery
        from app.services.anthropic_client import MaxTokensTruncationError
        from app.services.suggestions_discovery import (
            DISCOVERY_MAX_TOKENS_FLOOR, DiscoveryPick,
            DiscoveryPicksResponse, discovery_call_weekly,
        )

        env = _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        success_response = DiscoveryPicksResponse(picks=[
            DiscoveryPick(track_id=env["track_ids"][0], rationale="ok"),
        ])
        call_mock = AsyncMock(side_effect=[
            MaxTokensTruncationError(
                purpose="discovery_weekly",
                requested_max_tokens=DISCOVERY_MAX_TOKENS_FLOOR,
                truncated_text_length=7900,
            ),
            success_response,
        ])
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        assert call_mock.await_count == 2
        first_kwargs = call_mock.await_args_list[0].kwargs
        second_kwargs = call_mock.await_args_list[1].kwargs
        assert first_kwargs["max_tokens"] == DISCOVERY_MAX_TOKENS_FLOOR
        assert second_kwargs["max_tokens"] == DISCOVERY_MAX_TOKENS_FLOOR * 2

        # Picks from the second (successful) call were written.
        mirror_rows = db_with_phase7.exec(select(SuggestionsMirror)).all()
        assert len(mirror_rows) == 1
        assert mirror_rows[0].track_id == env["track_ids"][0]

    def test_discovery_call_weekly_does_not_retry_on_generic_validation_error(
        self, db_with_phase7, monkeypatch,
    ):
        """Blocker #5 — generic pydantic.ValidationError must NOT trigger
        the retry. Failure path runs.
        """
        from unittest.mock import AsyncMock

        from pydantic import BaseModel, ValidationError

        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import discovery_call_weekly

        # Build a real ValidationError instance.
        class _TmpModel(BaseModel):
            x: int
        try:
            _TmpModel(x="not-an-int")  # type: ignore
        except ValidationError as e:
            ve = e

        _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        call_mock = AsyncMock(side_effect=ve)
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        assert call_mock.await_count == 1, (
            "Generic ValidationError must not trigger retry — only "
            "MaxTokensTruncationError does (Blocker #5)"
        )

    def test_discovery_call_weekly_does_not_retry_on_generic_runtime_error(
        self, db_with_phase7, monkeypatch,
    ):
        """Blocker #5 — RuntimeError (network down, etc.) must NOT trigger
        the retry. Exactly one LLM call.
        """
        from unittest.mock import AsyncMock

        from app.services import suggestions_discovery
        from app.services.suggestions_discovery import discovery_call_weekly

        _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        call_mock = AsyncMock(side_effect=RuntimeError("network down"))
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        assert call_mock.await_count == 1, (
            "RuntimeError must not trigger retry — only "
            "MaxTokensTruncationError does (Blocker #5)"
        )

    def test_discovery_call_weekly_does_not_retry_twice_on_repeated_truncation(
        self, db_with_phase7, monkeypatch,
    ):
        """Two MaxTokensTruncationErrors in a row → exit to failure path
        (no third attempt). Counter NOT reset; failure logged.
        """
        from unittest.mock import AsyncMock

        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery
        from app.services.anthropic_client import MaxTokensTruncationError
        from app.services.suggestions_discovery import (
            DISCOVERY_MAX_TOKENS_FLOOR, discovery_call_weekly,
        )

        _seed_discovery_environment(
            db_with_phase7, num_candidates=5,
            plays_since_last_discovery=15,
        )
        call_mock = AsyncMock(side_effect=[
            MaxTokensTruncationError(
                purpose="discovery_weekly",
                requested_max_tokens=DISCOVERY_MAX_TOKENS_FLOOR,
                truncated_text_length=7900,
            ),
            MaxTokensTruncationError(
                purpose="discovery_weekly",
                requested_max_tokens=DISCOVERY_MAX_TOKENS_FLOOR * 2,
                truncated_text_length=15800,
            ),
        ])
        monkeypatch.setattr(
            suggestions_discovery, "AnthropicClient",
            _make_fake_anthropic_client(call_mock),
        )
        async def _no_op(*a, **kw):
            return None
        monkeypatch.setattr(
            suggestions_discovery, "check_or_raise", _no_op,
        )

        _run_async(discovery_call_weekly())

        assert call_mock.await_count == 2, (
            "Loop must exit to failure path after 2 truncations — "
            "no third attempt (D-C3: no infinite retry)"
        )

        # Counter unchanged.
        db_with_phase7.expire_all()
        row = db_with_phase7.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        assert row.plays_since_last_discovery == 15


class TestSuggestionsDiscoveryAstShape:
    def test_no_session_outside_sync_helper_in_suggestions_discovery(self):
        """Phase 5 D-09 invariant — every ``Session(get_engine())`` call in
        ``app/services/suggestions_discovery.py`` lives inside a function
        whose name ends with ``_sync``. Mirrors
        tests/test_suggestions_service.py::TestSuggestionsServiceAstShape.
        """
        path = (
            Path(__file__).parent.parent
            / "app"
            / "services"
            / "suggestions_discovery.py"
        )
        source = path.read_text()
        tree = ast.parse(source)

        violations: list = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_name = node.name
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                if not isinstance(child.func, ast.Name):
                    continue
                if child.func.id != "Session":
                    continue
                if not func_name.endswith("_sync"):
                    violations.append(
                        f"Session() at line {child.lineno} inside "
                        f"non-_sync function {func_name!r} — Phase 5 "
                        f"D-09 violation"
                    )

        assert violations == [], (
            "Phase 5 D-09 invariant violated in "
            "app/services/suggestions_discovery.py:\n"
            + "\n".join(violations)
        )


class TestSuggestionsDiscoveryMaxTokensGuard:
    """Phase 7.1 SUGG-14 — defensive max_tokens sizing on the weekly
    discovery LLM call.

    The truncation pitfall from quick task 260514-e6w must not recur in
    the new module. The hotfix introduced this AST pattern for
    ``app/services/suggestions_service.py``; we re-apply it to the new
    ``app/services/suggestions_discovery.py`` module which is the only
    place an Anthropic call now lives outside legacy / debug code.
    """

    def _module_path(self) -> Path:
        return (
            Path(__file__).parent.parent
            / "app"
            / "services"
            / "suggestions_discovery.py"
        )

    def test_no_hardcoded_max_tokens_2000_in_suggestions_discovery(self):
        """No literal ``max_tokens=2000`` may appear as a keyword arg.

        AST (not grep) so docstrings / comments mentioning the value
        historically do not trip the assertion.
        """
        source = self._module_path().read_text()
        tree = ast.parse(source)

        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "max_tokens":
                    continue
                if (
                    isinstance(kw.value, ast.Constant)
                    and kw.value.value == 2000
                ):
                    violations.append(
                        f"max_tokens=2000 literal at line "
                        f"{kw.value.lineno}"
                    )

        assert violations == [], (
            "SUGG-14 violation: forbidden max_tokens=2000 literal "
            "found in app/services/suggestions_discovery.py "
            "(lesson from quick task 260514-e6w):\n"
            + "\n".join(violations)
        )

    def test_no_max_tokens_below_floor_in_suggestions_discovery(self):
        """Any int literal max_tokens MUST be >= DISCOVERY_MAX_TOKENS_FLOOR.

        Catches accidental downsizing to 4000 / 6000 etc. Symbolic
        references (max_tokens=DISCOVERY_MAX_TOKENS_FLOOR or
        max_tokens=current_max_tokens) are NOT flagged — AST cannot
        evaluate symbolic values without execution; symbolic references
        are the preferred pattern anyway.
        """
        from app.services.suggestions_discovery import (
            DISCOVERY_MAX_TOKENS_FLOOR,
        )

        source = self._module_path().read_text()
        tree = ast.parse(source)

        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "max_tokens":
                    continue
                if (
                    isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, int)
                    and kw.value.value < DISCOVERY_MAX_TOKENS_FLOOR
                ):
                    violations.append(
                        f"max_tokens={kw.value.value} literal at line "
                        f"{kw.value.lineno} is below "
                        f"DISCOVERY_MAX_TOKENS_FLOOR "
                        f"({DISCOVERY_MAX_TOKENS_FLOOR})"
                    )

        assert violations == [], (
            "SUGG-14 violation: max_tokens literal below floor in "
            "app/services/suggestions_discovery.py:\n"
            + "\n".join(violations)
        )

    def test_all_sql_helpers_called_via_to_thread(self):
        """Phase 5 D-09 — every direct call to a ``_*_sync`` helper inside
        an ``async def`` function must be the ``func`` argument of an
        ``asyncio.to_thread(...)`` (or bare ``to_thread(...)``) call.

        Catches the regression where someone writes
        ``await _read_X_sync()`` inside an async handler — which would
        block the event loop because ``_read_X_sync`` is a sync function.
        """
        source = self._module_path().read_text()
        tree = ast.parse(source)

        # Collect all async functions in the module.
        async_funcs = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)
        ]

        violations: list[str] = []
        for fn in async_funcs:
            for child in ast.walk(fn):
                if not isinstance(child, ast.Call):
                    continue
                # We only care about direct calls to a name ending
                # in ``_sync``.
                func = child.func
                called_name = None
                if isinstance(func, ast.Name):
                    called_name = func.id
                elif isinstance(func, ast.Attribute):
                    called_name = func.attr
                if called_name is None or not called_name.endswith("_sync"):
                    continue

                # The call is allowed if it is the FIRST positional
                # argument of an asyncio.to_thread(...) (or to_thread)
                # call — i.e. the parent Call whose .args[0] is this
                # node, with parent.func being to_thread.
                parent_is_to_thread = False
                for parent in ast.walk(tree):
                    if not isinstance(parent, ast.Call):
                        continue
                    if not parent.args:
                        continue
                    parent_func = parent.func
                    is_to_thread = (
                        (isinstance(parent_func, ast.Attribute)
                         and parent_func.attr == "to_thread")
                        or (isinstance(parent_func, ast.Name)
                            and parent_func.id == "to_thread")
                    )
                    if not is_to_thread:
                        continue
                    if parent.args[0] is func:
                        parent_is_to_thread = True
                        break
                if not parent_is_to_thread:
                    violations.append(
                        f"_*_sync helper {called_name!r} called "
                        f"directly inside async function "
                        f"{fn.name!r} at line {child.lineno} — "
                        f"must be wrapped in asyncio.to_thread(...)"
                    )

        assert violations == [], (
            "Phase 5 D-09 violation in "
            "app/services/suggestions_discovery.py:\n"
            + "\n".join(violations)
        )
