"""Phase 6.1 migration tests (D-NEW-09).

Covers idempotency, state cleanup, Plex archive invocation with 4-arg
signature (Blocker #2), pending_slot_in reset, and tight except clause
behavior (WARNING #9).

Test isolation: the autouse ``tmp_data_dir`` fixture in tests/conftest.py
rewrites config.DATA_DIR + DATABASE_URL per test AND calls
app.database.reset_engine(). Tests do NOT need monkeypatch.setenv
(and there is no COMPOSER_DB_PATH env var — Blocker #4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from app.database import get_engine, init_db
from app.models.track import Track
from app.models.vibe import (
    ManagedPlaylist, MigrationLog, SetupState, SlotInLog, TrackVibe, Vibe,
)


@pytest.fixture
def init_phase61_db():
    """Per-test DB init. Relies on autouse tmp_data_dir from conftest for
    path isolation + engine reset.
    """
    # Register all models so create_all picks them up.
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    init_db()
    yield


@pytest.mark.asyncio
async def test_run_phase_61_migration_records_completion_on_empty_db(
    init_phase61_db,
):
    """Test 2: empty DB → one MigrationLog row with completed_at set."""
    from app.main import run_phase_61_migration

    with patch(
        "app.services.plex_playlist_service.archive_playlist",
        new_callable=AsyncMock,
    ) as mock_archive:
        await run_phase_61_migration()
        assert mock_archive.call_count == 0

    with Session(get_engine()) as s:
        row = s.exec(
            select(MigrationLog).where(MigrationLog.phase_id == "6.1")
        ).first()
        assert row is not None
        assert row.completed_at is not None


@pytest.mark.asyncio
async def test_run_phase_61_migration_idempotent_no_double_archive(
    init_phase61_db,
):
    """Test 5: Second call is a no-op. Plex API NOT called the second time."""
    from app.main import run_phase_61_migration
    from app.services.settings_service import save_setting

    with Session(get_engine()) as s:
        mp = ManagedPlaylist(
            plex_rating_key="rk_999", kind="vibe",
            composer_name="Composer · Old Workout",
        )
        s.add(mp)
        s.commit()
        save_setting(s, "plex", "http://localhost:32400", "fake-token")

    with patch(
        "app.services.plex_playlist_service.archive_playlist",
        new_callable=AsyncMock,
    ) as mock_archive:
        await run_phase_61_migration()
        first_calls = mock_archive.call_count
        # Run again — gate must short-circuit.
        await run_phase_61_migration()
        second_calls = mock_archive.call_count
        assert first_calls == 1, (
            f"First call should archive 1 playlist; got {first_calls}"
        )
        assert second_calls == 1, (
            f"Second call must be no-op; got {second_calls}"
        )


@pytest.mark.asyncio
async def test_run_phase_61_migration_clears_existing_vibe_state(
    init_phase61_db,
):
    """Test 3: All Vibe / TrackVibe / ManagedPlaylist / SlotInLog cleared;
    SetupState reset to step='rating_source'.
    """
    from app.main import run_phase_61_migration

    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as s:
        v1 = Vibe(name="A", description="d", is_active=True, created_at=now)
        v2 = Vibe(name="B", description="d", is_active=True, created_at=now)
        s.add(v1)
        s.add(v2)
        s.commit()
        s.refresh(v1)
        s.refresh(v2)
        # Seed Tracks so TrackVibe.track_id FK is satisfied.
        t1 = Track(plex_rating_key="tv-1", title="T1", artist="A", album="Alb")
        t2 = Track(plex_rating_key="tv-2", title="T2", artist="A", album="Alb")
        s.add(t1)
        s.add(t2)
        s.commit()
        s.refresh(t1)
        s.refresh(t2)
        s.add(TrackVibe(
            track_id=t1.id, vibe_id=v1.id, distance=0.1,
            assigned_at=now, assigned_by="cluster",
        ))
        s.add(TrackVibe(
            track_id=t2.id, vibe_id=v1.id, distance=0.1,
            assigned_at=now, assigned_by="cluster",
        ))
        s.add(ManagedPlaylist(
            plex_rating_key="rk_1", kind="vibe",
            composer_name="Composer · A", vibe_id=v1.id,
        ))
        s.add(SlotInLog(
            timestamp=now, track_id=t1.id,
            vibe_ids="[1]", distances="[0.1]",
            soft_membership_applied=False, action="slot",
        ))
        state = SetupState(
            id=1, step="confirming",
            draft_proposals_json="{...}",
            refinement_turn_count=3,
            recluster_mode=True,
            completed_at=None,
        )
        s.merge(state)
        s.commit()

    # No Plex creds → archive call should NOT fire (best-effort skip).
    with patch(
        "app.services.plex_playlist_service.archive_playlist",
        new_callable=AsyncMock,
    ):
        await run_phase_61_migration()

    with Session(get_engine()) as s:
        assert s.exec(select(Vibe)).all() == []
        assert s.exec(select(TrackVibe)).all() == []
        assert s.exec(select(ManagedPlaylist)).all() == []
        assert s.exec(select(SlotInLog)).all() == []
        state2 = s.exec(
            select(SetupState).where(SetupState.id == 1)
        ).first()
        assert state2 is not None
        assert state2.step == "rating_source"
        assert state2.draft_proposals_json == ""
        assert state2.refinement_turn_count == 0
        assert state2.recluster_mode is False or state2.recluster_mode == 0
        assert state2.completed_at is None


@pytest.mark.asyncio
async def test_run_phase_61_migration_clears_pending_slot_in(init_phase61_db):
    """Test 4: Track.pending_slot_in cleared (set to 0) for all rows."""
    from app.main import run_phase_61_migration

    with Session(get_engine()) as s:
        for i in range(3):
            t = Track(
                plex_rating_key=f"rk_{i}", title=f"T{i}", artist="A",
                album="Alb", pending_slot_in=1,
            )
            s.add(t)
        s.commit()

    with patch(
        "app.services.plex_playlist_service.archive_playlist",
        new_callable=AsyncMock,
    ):
        await run_phase_61_migration()

    with Session(get_engine()) as s:
        for t in s.exec(select(Track)).all():
            assert (not t.pending_slot_in) or t.pending_slot_in == 0


@pytest.mark.asyncio
async def test_run_phase_61_migration_archives_legacy_playlists(
    init_phase61_db,
):
    """Test 6 (Blocker #2 regression): archive_playlist MUST be called with 4 args
    (plex_url, plex_token, plex_rating_key, current_name).
    """
    from app.main import run_phase_61_migration
    from app.services.settings_service import save_setting

    with Session(get_engine()) as s:
        s.add(ManagedPlaylist(
            plex_rating_key="rk-1", kind="vibe",
            composer_name="Composer · OldVibe",
        ))
        s.add(ManagedPlaylist(
            plex_rating_key="rk-2", kind="vibe",
            composer_name="Composer · Other",
        ))
        s.commit()
        save_setting(s, "plex", "http://localhost:32400", "fake-token")

    with patch(
        "app.services.plex_playlist_service.archive_playlist",
        new_callable=AsyncMock,
    ) as mock_archive:
        await run_phase_61_migration()
        assert mock_archive.call_count == 2, (
            f"Expected 2 archive calls; got {mock_archive.call_count}"
        )
        # Inspect each call's positional args — must be 4.
        for call in mock_archive.call_args_list:
            assert len(call.args) == 4, (
                f"archive_playlist called with {len(call.args)} args "
                f"(must be 4 per Blocker #2): {call.args}"
            )
        # First call assertion: exact 4-arg shape (positional).
        first_args = mock_archive.call_args_list[0].args
        assert first_args == (
            "http://localhost:32400", "fake-token", "rk-1",
            "Composer · OldVibe",
        ), f"4-arg signature mismatch: {first_args}"


@pytest.mark.asyncio
async def test_run_phase_61_migration_surfaces_arity_typeerror(
    init_phase61_db, caplog,
):
    """Test 7 (Blocker #2 + WARNING #9): tight except clause catches TypeError
    and LOGS it (not bare Exception which would silently swallow).
    """
    import logging
    from app.main import run_phase_61_migration
    from app.services.settings_service import save_setting

    with Session(get_engine()) as s:
        s.add(ManagedPlaylist(
            plex_rating_key="rk-1", kind="vibe",
            composer_name="Composer · OldVibe",
        ))
        s.commit()
        save_setting(s, "plex", "http://localhost:32400", "fake-token")

    # Simulate future arity drift: archive_playlist raises TypeError.
    async def broken_archive(*args, **kwargs):
        raise TypeError(
            "archive_playlist() missing 1 required positional argument: "
            "'current_name'"
        )

    with caplog.at_level(logging.ERROR), patch(
        "app.services.plex_playlist_service.archive_playlist",
        side_effect=broken_archive,
    ):
        await run_phase_61_migration()

    # Tight except clause caught it AND logged with archive_playlist name.
    assert any(
        "archive_playlist failed" in (rec.message or "")
        for rec in caplog.records
    ), (
        "TypeError should surface in logs: "
        f"{[r.message for r in caplog.records]}"
    )
