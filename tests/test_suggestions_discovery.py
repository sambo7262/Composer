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
