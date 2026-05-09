"""Tests for app/services/backfill_service.py — first-run auto-backfill (RATE-02)."""
from __future__ import annotations

import asyncio
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def reset_backfill_singleton():
    """Reset _backfill_status between tests."""
    try:
        import app.services.backfill_service as bs

        bs._backfill_status = bs.BackfillStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        import app.services.backfill_service as bs

        bs._backfill_status = bs.BackfillStatus()
    except (ImportError, AttributeError):
        pass


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


def _make_mock_setting(library_id="1"):
    setting = MagicMock()
    setting.is_configured = True
    setting.url = "http://plex:32400"
    setting.extra_config = {"library_id": library_id}
    return setting


def _make_track_dict(rating_key, user_rating=None, last_viewed_at=None, view_count=0):
    return {
        "plex_rating_key": rating_key,
        "user_rating": user_rating,
        "last_viewed_at": last_viewed_at,
        "view_count": view_count,
    }


class TestRunBackfill:
    """Tests for run_backfill — pages Plex and populates Track rating fields."""

    def test_full_run(self, db_with_phase5):
        """Pre-populate 3 Track rows; mock single-batch Plex; assert all 3 backfilled."""
        from app.models.track import Track
        from app.services import backfill_service
        from app.database import get_engine

        # Pre-populate 3 tracks with NULL user_rating
        for i in range(3):
            db_with_phase5.add(
                Track(plex_rating_key=str(i + 1000), title=f"T{i}", artist="A")
            )
        db_with_phase5.commit()

        plex_batch = [
            _make_track_dict("1000", user_rating=8.0, view_count=5),
            _make_track_dict("1001", user_rating=6.0, view_count=2),
            _make_track_dict(
                "1002", user_rating=10.0, last_viewed_at="2026-05-08T10:00:00+00:00"
            ),
        ]

        async def mock_get_library_tracks(*args, **kwargs):
            return (plex_batch, 3)

        with patch(
            "app.services.backfill_service.get_library_tracks",
            side_effect=mock_get_library_tracks,
        ), patch(
            "app.services.backfill_service.get_setting",
            return_value=_make_mock_setting(),
        ), patch(
            "app.services.backfill_service.get_decrypted_credential",
            return_value="test-token",
        ):
            _run_async(backfill_service.run_backfill())

        status = backfill_service.get_backfill_status()
        assert status.state == backfill_service.BackfillStateEnum.COMPLETED

        # Verify all 3 Track rows now have user_rating populated
        with Session(get_engine()) as fresh:
            tracks = fresh.exec(select(Track).order_by(Track.plex_rating_key)).all()
            assert len(tracks) == 3
            assert tracks[0].user_rating == 8.0
            assert tracks[0].view_count == 5
            assert tracks[1].user_rating == 6.0
            assert tracks[2].user_rating == 10.0
            assert tracks[2].last_viewed_at == "2026-05-08T10:00:00+00:00"

    def test_paginated_smoke(self, db_with_phase5):
        """250 tracks across 2 batches (200 + 50) — exercises the pagination cursor."""
        from app.models.track import Track
        from app.services import backfill_service
        from app.database import get_engine

        # Pre-populate 250 tracks
        for i in range(250):
            db_with_phase5.add(
                Track(plex_rating_key=str(20000 + i), title=f"T{i}", artist="A")
            )
        db_with_phase5.commit()

        # Build 2 batches that mimic Plex's 200/page default
        batch1 = [
            _make_track_dict(str(20000 + i), user_rating=5.0) for i in range(200)
        ]
        batch2 = [
            _make_track_dict(str(20000 + i), user_rating=7.0)
            for i in range(200, 250)
        ]

        call_log = []

        async def mock_get_library_tracks(url, token, library_id, **kwargs):
            call_log.append(kwargs)
            cs = kwargs.get("container_start", 0)
            if cs == 0:
                return (batch1, 250)
            else:
                return (batch2, 250)

        with patch(
            "app.services.backfill_service.get_library_tracks",
            side_effect=mock_get_library_tracks,
        ), patch(
            "app.services.backfill_service.get_setting",
            return_value=_make_mock_setting(),
        ), patch(
            "app.services.backfill_service.get_decrypted_credential",
            return_value="test-token",
        ):
            _run_async(backfill_service.run_backfill())

        status = backfill_service.get_backfill_status()
        assert status.state == backfill_service.BackfillStateEnum.COMPLETED

        # Pagination cursor must advance: at least two calls with offsets 0 and 200
        assert len(call_log) >= 2, (
            f"Expected pagination across at least 2 batches, got {len(call_log)} calls"
        )
        offsets = [c.get("container_start", 0) for c in call_log]
        assert 0 in offsets
        assert 200 in offsets

        # ALL 250 tracks should have user_rating populated
        with Session(get_engine()) as fresh:
            tracks = fresh.exec(select(Track)).all()
            assert len(tracks) == 250
            for t in tracks:
                assert t.user_rating is not None, (
                    f"Track {t.plex_rating_key} not backfilled"
                )

    def test_concurrency_guard(self, db_with_phase5):
        """Second run_backfill while first RUNNING returns immediately."""
        from app.services import backfill_service

        backfill_service._backfill_status = backfill_service.BackfillStatus(
            state=backfill_service.BackfillStateEnum.RUNNING
        )

        with patch(
            "app.services.backfill_service.get_library_tracks"
        ) as mock_glt, patch(
            "app.services.backfill_service.get_setting",
            return_value=_make_mock_setting(),
        ), patch(
            "app.services.backfill_service.get_decrypted_credential",
            return_value="test-token",
        ):
            _run_async(backfill_service.run_backfill())

        assert mock_glt.call_count == 0


class TestMaybeTriggerFirstRunBackfill:
    """Tests for the auto-trigger gate (Pitfall 7: tracks exist AND no rating populated)."""

    def test_maybe_trigger_no_op_when_already_rated(self, db_with_phase5):
        """If any track already has user_rating, don't auto-trigger."""
        from app.models.track import Track
        from app.services import backfill_service

        db_with_phase5.add(
            Track(plex_rating_key="500", title="X", artist="Y", user_rating=8.0)
        )
        db_with_phase5.commit()

        with patch(
            "app.services.backfill_service.asyncio.create_task"
        ) as mock_create_task:
            _run_async(backfill_service.maybe_trigger_first_run_backfill())

        assert mock_create_task.call_count == 0, (
            "Should NOT trigger when at least one track has user_rating"
        )
        assert (
            backfill_service.get_backfill_status().state
            == backfill_service.BackfillStateEnum.IDLE
        )

    def test_maybe_trigger_no_op_when_empty_db(self, db_with_phase5):
        """Pitfall 7: empty DB means sync hasn't run yet — don't backfill."""
        from app.services import backfill_service

        # No tracks in DB
        with patch(
            "app.services.backfill_service.asyncio.create_task"
        ) as mock_create_task:
            _run_async(backfill_service.maybe_trigger_first_run_backfill())

        assert mock_create_task.call_count == 0, (
            "Should NOT trigger backfill on empty DB (Pitfall 7)"
        )
        assert (
            backfill_service.get_backfill_status().state
            == backfill_service.BackfillStateEnum.IDLE
        )

    def test_maybe_trigger_runs_when_unrated_tracks_exist(self, db_with_phase5):
        """Tracks exist with all NULL user_rating → trigger backfill."""
        from app.models.track import Track
        from app.services import backfill_service

        for i in range(3):
            db_with_phase5.add(
                Track(plex_rating_key=str(800 + i), title="X", artist="Y")
            )
        db_with_phase5.commit()

        with patch(
            "app.services.backfill_service.asyncio.create_task"
        ) as mock_create_task:
            _run_async(backfill_service.maybe_trigger_first_run_backfill())

        assert mock_create_task.call_count == 1, (
            "Should trigger backfill exactly once when unrated tracks exist"
        )
