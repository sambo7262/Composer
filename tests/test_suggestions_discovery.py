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
