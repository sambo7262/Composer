"""Phase 6 Plan 02 event_handlers + analysis_service wiring tests.

Verifies:
- handle_rating_changed branches to slot_track when new_rating > 0 (D-15).
- handle_rating_changed branches to unslot_track when new_rating is 0 / None (D-18).
- The vibe hook is best-effort: a failure does NOT propagate up (matches the
  existing taste_profile recompute hook convention).
- analysis_service.run_analysis post-track hook calls
  vibe_service.maybe_reslot_pending_track on every successful analysis (D-17).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def db_with_phase6(test_engine) -> Generator[Session, None, None]:
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        SetupState,
        TrackVibe,
        Vibe,
    )

    from app.database import init_db
    init_db()

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def reset_vibe_singletons():
    try:
        from app.services import vibe_service
    except ImportError:
        yield
        return
    vibe_service._slot_in_locks.clear()
    yield
    vibe_service._slot_in_locks.clear()


# ---------------------------------------------------------------------------
# Test 13: handle_rating_changed → slot_track on rating > 0
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_rating_changed_calls_slot_track_when_rating_above_zero(
    db_with_phase6, monkeypatch
):
    from app.models.events import RatingChangedEvent
    from app.models.track import Track

    db_with_phase6.add(Track(plex_rating_key="rk-1", title="T", artist="A"))
    db_with_phase6.commit()

    slot_mock = AsyncMock()
    unslot_mock = AsyncMock()
    monkeypatch.setattr("app.services.vibe_service.slot_track", slot_mock)
    monkeypatch.setattr("app.services.vibe_service.unslot_track", unslot_mock)
    # Avoid taste-profile-recompute side effects on this test.
    recompute_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.taste_profile_service.maybe_recompute_after_rating_change",
        recompute_mock,
    )

    from app.services.event_handlers import handle_rating_changed

    evt = RatingChangedEvent(
        plex_rating_key="rk-1",
        new_rating=8.0,
        source="webhook",
        received_at=datetime.now(timezone.utc).isoformat(),
    )
    await handle_rating_changed(evt)

    assert slot_mock.await_count == 1
    assert unslot_mock.await_count == 0


# ---------------------------------------------------------------------------
# Test 14: handle_rating_changed → unslot_track on rating cleared
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_rating_changed_calls_unslot_track_when_rating_zero(
    db_with_phase6, monkeypatch
):
    from app.models.events import RatingChangedEvent
    from app.models.track import Track

    db_with_phase6.add(Track(plex_rating_key="rk-2", title="T", artist="A"))
    db_with_phase6.commit()

    slot_mock = AsyncMock()
    unslot_mock = AsyncMock()
    monkeypatch.setattr("app.services.vibe_service.slot_track", slot_mock)
    monkeypatch.setattr("app.services.vibe_service.unslot_track", unslot_mock)
    monkeypatch.setattr(
        "app.services.taste_profile_service.maybe_recompute_after_rating_change",
        AsyncMock(),
    )

    from app.services.event_handlers import handle_rating_changed

    # new_rating = None
    evt = RatingChangedEvent(
        plex_rating_key="rk-2",
        new_rating=None,
        source="webhook",
        received_at=datetime.now(timezone.utc).isoformat(),
    )
    await handle_rating_changed(evt)

    assert unslot_mock.await_count == 1
    assert slot_mock.await_count == 0

    # new_rating = 0 also triggers unslot
    unslot_mock.reset_mock()
    slot_mock.reset_mock()

    evt2 = RatingChangedEvent(
        plex_rating_key="rk-2",
        new_rating=0,
        source="webhook",
        received_at=datetime.now(timezone.utc).isoformat(),
    )
    await handle_rating_changed(evt2)

    assert unslot_mock.await_count == 1
    assert slot_mock.await_count == 0


# ---------------------------------------------------------------------------
# Test 15: vibe hook is best-effort (slot_track raises → outer succeeds)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_rating_changed_swallows_slot_in_failure(
    db_with_phase6, monkeypatch
):
    from app.models.events import RatingChangedEvent
    from app.models.track import Track

    db_with_phase6.add(Track(plex_rating_key="rk-3", title="T", artist="A"))
    db_with_phase6.commit()

    slot_mock = AsyncMock(side_effect=RuntimeError("kaboom in vibes"))
    monkeypatch.setattr("app.services.vibe_service.slot_track", slot_mock)
    monkeypatch.setattr(
        "app.services.taste_profile_service.maybe_recompute_after_rating_change",
        AsyncMock(),
    )

    from app.services.event_handlers import handle_rating_changed

    evt = RatingChangedEvent(
        plex_rating_key="rk-3",
        new_rating=8.0,
        source="webhook",
        received_at=datetime.now(timezone.utc).isoformat(),
    )
    # Must not propagate.
    await handle_rating_changed(evt)
    assert slot_mock.await_count == 1


# ---------------------------------------------------------------------------
# Test 16: analysis_service post-track hook calls maybe_reslot_pending_track
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_analysis_service_post_track_hook_calls_maybe_reslot(
    db_with_phase6, monkeypatch
):
    """run_analysis processes 1 successful track → maybe_reslot_pending_track called."""
    from app.models.track import Track

    db_with_phase6.add(
        Track(
            plex_rating_key="aud-1",
            title="A",
            artist="X",
            file_path="/data/x.mp3",
        )
    )
    db_with_phase6.commit()

    # Mock the heavy parts: detect_root + analyze_single returns success.
    monkeypatch.setattr(
        "app.services.analysis_service._detect_plex_music_root_sync",
        lambda: "/data",
    )
    fake_result = {
        "success": True,
        "features": {"energy": 0.5, "tempo": 120.0,
                     "danceability": 0.5, "valence": 0.5,
                     "musical_key": "C", "scale": "major",
                     "spectral_complexity": 5.0, "loudness": -10.0},
        "error": None,
        "elapsed": 0.01,
    }
    monkeypatch.setattr(
        "app.services.analysis_service._analyze_single_track_sync",
        lambda track_id, root: fake_result,
    )

    reslot_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.vibe_service.maybe_reslot_pending_track",
        reslot_mock,
    )

    from app.services import analysis_service
    # Reset analyzer status so run_analysis proceeds.
    analysis_service._analysis_status = analysis_service.AnalysisStatus()

    await analysis_service.run_analysis()

    assert reslot_mock.await_count == 1
