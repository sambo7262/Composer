"""Phase 7 Plan 01 Task 1 — Tests for the run_phase_07_suggestions_bootstrap
lifespan migration step.

D-02: lifespan migration is one of two bootstrap callers (the other is the
wizard finalize hook from D-01). Gated by
``MigrationLog(phase_id='7.0-suggestions-bootstrap')`` so the bootstrap fires
exactly once per Composer install across container restarts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, SQLModel, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def db_phase7(test_engine) -> Generator[Session, None, None]:
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


PHASE_07_MIGRATION_ID = "7.0-suggestions-bootstrap"


class TestRunPhase07SuggestionsBootstrap:
    def test_sets_migration_log_on_first_run(self, db_phase7):
        """Fresh DB (no MigrationLog row) → run_phase_07_suggestions_bootstrap
        registers a ManagedPlaylist + writes MigrationLog row with
        completed_at non-null.
        """
        from app.services.suggestions_service import (
            run_phase_07_suggestions_bootstrap,
        )
        from app.models.vibe import ManagedPlaylist, MigrationLog

        _run_async(run_phase_07_suggestions_bootstrap())

        gate = db_phase7.exec(
            select(MigrationLog).where(
                MigrationLog.phase_id == PHASE_07_MIGRATION_ID
            )
        ).first()
        assert gate is not None
        assert gate.completed_at is not None
        # Should be a parseable ISO timestamp.
        datetime.fromisoformat(gate.completed_at)

        mp = db_phase7.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.kind == "suggestions"
            )
        ).first()
        assert mp is not None

    def test_is_idempotent(self, db_phase7):
        """Pre-seeded MigrationLog row with completed_at set → the gate
        short-circuits before bootstrap_suggestions_queue is reached.
        """
        from app.services.suggestions_service import (
            run_phase_07_suggestions_bootstrap,
        )
        from app.models.vibe import ManagedPlaylist, MigrationLog

        db_phase7.add(
            MigrationLog(
                phase_id=PHASE_07_MIGRATION_ID,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        db_phase7.commit()

        with patch(
            "app.services.suggestions_service._create_plex_suggestions_playlist",
            new=AsyncMock(side_effect=RuntimeError("must not be called")),
        ):
            _run_async(run_phase_07_suggestions_bootstrap())

        # No ManagedPlaylist row created — gate short-circuited.
        rows = db_phase7.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.kind == "suggestions"
            )
        ).all()
        assert len(rows) == 0

    def test_failure_leaves_gate_unset(self, db_phase7):
        """On bootstrap failure, MigrationLog row exists with completed_at=NULL
        (defensive in-flight marker) so the next restart will retry.
        """
        from app.services.suggestions_service import (
            run_phase_07_suggestions_bootstrap,
        )
        from app.models.vibe import ManagedPlaylist, MigrationLog

        # Force the inner bootstrap step to raise.
        with patch(
            "app.services.suggestions_service.bootstrap_suggestions_queue",
            new=AsyncMock(side_effect=RuntimeError("simulated outage")),
        ):
            _run_async(run_phase_07_suggestions_bootstrap())

        gate = db_phase7.exec(
            select(MigrationLog).where(
                MigrationLog.phase_id == PHASE_07_MIGRATION_ID
            )
        ).first()
        # In-flight marker exists, but completed_at is still NULL.
        assert gate is not None
        assert gate.completed_at is None

        mp = db_phase7.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.kind == "suggestions"
            )
        ).first()
        assert mp is None
