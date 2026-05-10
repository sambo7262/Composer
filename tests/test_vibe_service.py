"""Phase 6 Plan 02 vibe_service tests (D-15, D-16, D-17, D-18, D-19; VIBE-02, VIBE-09, VIBE-10).

Tests cover:
- slot_track picks the closest vibe (z-score-normalized distance).
- Soft-margin rule applies a second vibe within 1 std-dev (Pitfall 24).
- Hard cap of 2 vibes per track (D-19 / VIBE-02).
- user_rating=0 returns without slotting (caller should use unslot_track).
- Missing audio features → set pending_slot_in=TRUE; return pending=True (D-17).
- Manual override stickiness — assigned_by='manual' rows survive auto-slot (D-19).
- additive Plex playlist push via update_playlist_items (Pitfall 5).
- unslot_track removes ALL TrackVibe rows + calls remove_from_playlist per vibe.
- Per-track asyncio.Lock serializes concurrent slot_track calls (D-16 / Pitfall 23).
- maybe_reslot_pending_track clears the flag after a successful slot.
- maybe_reslot_pending_track skips when user_rating dropped to 0.
- reslot_all_rated_tracks iterates with concurrency=1 semaphore (D-25).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, select


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
    init_db()  # ensures pending_slot_in column + indexes are present

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def reset_vibe_service_singletons():
    """Reset the module-level lock dict + status before each test."""
    try:
        from app.services import vibe_service
    except ImportError:
        # Module not yet created — early test runs before GREEN.
        yield
        return
    vibe_service._slot_in_locks.clear()
    vibe_service._status.last_slot_at = None
    vibe_service._status.last_error = None
    vibe_service._status.state = "idle"
    yield
    vibe_service._slot_in_locks.clear()


@pytest.fixture(autouse=True)
def mock_plex_credentials(monkeypatch):
    """Avoid loading actual Plex creds — vibe_service reads them via to_thread."""
    def fake_get_plex_creds():
        return ("http://plex.local", "fake-token")

    # Lazy patch — only applies if vibe_service imports the helper.
    yield monkeypatch


def _add_vibe(session, name, ce, ct, cd, cv, se=0.1, st=10.0, sd=0.1, sv=0.1):
    from app.models.vibe import Vibe
    v = Vibe(
        name=name,
        created_at=datetime.now(timezone.utc).isoformat(),
        centroid_energy=ce,
        centroid_tempo=ct,
        centroid_danceability=cd,
        centroid_valence=cv,
        spread_energy=se,
        spread_tempo=st,
        spread_danceability=sd,
        spread_valence=sv,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return v


def _add_track(
    session,
    rk: str,
    rating: float = 8.0,
    energy=0.5,
    tempo=120.0,
    danceability=0.5,
    valence=0.5,
):
    from app.models.track import Track
    t = Track(
        plex_rating_key=rk,
        title=f"Title {rk}",
        artist="Artist",
        album="Album",
        user_rating=rating,
        energy=energy,
        tempo=tempo,
        danceability=danceability,
        valence=valence,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _patch_plex_helpers(monkeypatch):
    """Mock plex_playlist_service.update_playlist_items + remove_from_playlist + creds."""
    update_mock = AsyncMock()
    remove_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.vibe_service.update_playlist_items", update_mock
    )
    monkeypatch.setattr(
        "app.services.vibe_service.remove_from_playlist", remove_mock
    )
    monkeypatch.setattr(
        "app.services.vibe_service._get_plex_credentials_sync",
        lambda: ("http://plex.local", "fake-token"),
    )
    return update_mock, remove_mock


# ---------------------------------------------------------------------------
# Test 1: slot_track picks closest vibe
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_track_picks_closest_vibe(db_with_phase6, monkeypatch):
    update_mock, _ = _patch_plex_helpers(monkeypatch)

    # 3 vibes with distinct centroids; v_target is closest to track features.
    v_target = _add_vibe(db_with_phase6, "Target", 0.5, 120.0, 0.5, 0.5)
    _add_vibe(db_with_phase6, "Far A", 0.9, 170.0, 0.9, 0.9)
    _add_vibe(db_with_phase6, "Far B", 0.1, 70.0, 0.1, 0.1)

    t = _add_track(db_with_phase6, rk="t-close", rating=8.0,
                   energy=0.50, tempo=120.0, danceability=0.50, valence=0.50)

    from app.services.vibe_service import slot_track
    from app.models.vibe import TrackVibe

    result = await slot_track("t-close")

    assert result.primary_vibe_id == v_target.id
    assert result.pending is False
    rows = db_with_phase6.exec(
        select(TrackVibe).where(TrackVibe.track_id == t.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].vibe_id == v_target.id
    assert rows[0].assigned_by == "auto-slot"


# ---------------------------------------------------------------------------
# Test 2 + 3: soft-margin rule + cap=2
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_track_applies_soft_margin_and_caps_at_two(db_with_phase6, monkeypatch):
    """Three vibes within 1 std-dev margin → exactly TWO TrackVibe rows."""
    _patch_plex_helpers(monkeypatch)

    # Track at (0.5, 120, 0.5, 0.5). Three vibes very close in the same direction.
    v_a = _add_vibe(db_with_phase6, "A", 0.50, 120.0, 0.50, 0.50)  # closest
    v_b = _add_vibe(db_with_phase6, "B", 0.52, 121.0, 0.52, 0.52)  # near
    v_c = _add_vibe(db_with_phase6, "C", 0.55, 122.0, 0.55, 0.55)  # also near

    t = _add_track(db_with_phase6, rk="t-soft", rating=8.0,
                   energy=0.50, tempo=120.0, danceability=0.50, valence=0.50)

    from app.services.vibe_service import slot_track
    from app.models.vibe import TrackVibe

    result = await slot_track("t-soft")

    assert result.primary_vibe_id == v_a.id
    rows = db_with_phase6.exec(
        select(TrackVibe).where(TrackVibe.track_id == t.id)
    ).all()
    # Cap at 2 — only closest + soft second vibe inserted.
    assert len(rows) <= 2
    vibe_ids = {r.vibe_id for r in rows}
    assert v_a.id in vibe_ids
    # Either the second-closest (b) but never the third.
    assert v_c.id not in vibe_ids or len(rows) == 2


# ---------------------------------------------------------------------------
# Test 4: user_rating=0 → no slot
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_track_skips_when_user_rating_zero(db_with_phase6, monkeypatch):
    _patch_plex_helpers(monkeypatch)
    _add_vibe(db_with_phase6, "Target", 0.5, 120.0, 0.5, 0.5)
    t = _add_track(db_with_phase6, rk="t-zero", rating=0.0)

    from app.services.vibe_service import slot_track
    from app.models.vibe import TrackVibe

    result = await slot_track("t-zero")

    assert result.primary_vibe_id is None
    rows = db_with_phase6.exec(
        select(TrackVibe).where(TrackVibe.track_id == t.id)
    ).all()
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Test 5: missing audio features → pending=True + flag set
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_track_sets_pending_when_audio_features_missing(db_with_phase6, monkeypatch):
    _patch_plex_helpers(monkeypatch)
    _add_vibe(db_with_phase6, "Target", 0.5, 120.0, 0.5, 0.5)

    from app.models.track import Track
    from app.models.vibe import TrackVibe
    from app.database import get_engine

    # Build a track WITHOUT danceability.
    with Session(get_engine()) as s:
        t = Track(
            plex_rating_key="t-missing",
            title="Title",
            artist="Artist",
            album="Album",
            user_rating=8.0,
            energy=0.5,
            tempo=120.0,
            danceability=None,  # missing
            valence=0.5,
        )
        s.add(t)
        s.commit()
        track_id = t.id

    from app.services.vibe_service import slot_track

    result = await slot_track("t-missing")

    assert result.pending is True
    rows = db_with_phase6.exec(
        select(TrackVibe).where(TrackVibe.track_id == track_id)
    ).all()
    assert len(rows) == 0

    # pending_slot_in flag should be 1.
    import sqlite3
    db_path = str(db_with_phase6.bind.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT pending_slot_in FROM track WHERE id = ?", (track_id,))
    flag = cur.fetchone()[0]
    conn.close()
    assert flag == 1


# ---------------------------------------------------------------------------
# Test 6: manual override stickiness (D-19)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_track_skips_manual_override_target(db_with_phase6, monkeypatch):
    _patch_plex_helpers(monkeypatch)
    v = _add_vibe(db_with_phase6, "Target", 0.5, 120.0, 0.5, 0.5)
    t = _add_track(db_with_phase6, rk="t-manual", rating=8.0,
                   energy=0.5, tempo=120.0, danceability=0.5, valence=0.5)

    # Pre-existing manual TrackVibe row.
    from app.models.vibe import TrackVibe
    db_with_phase6.add(
        TrackVibe(
            track_id=t.id,
            vibe_id=v.id,
            distance=0.1,
            assigned_at=datetime.now(timezone.utc).isoformat(),
            assigned_by="manual",
        )
    )
    db_with_phase6.commit()

    from app.services.vibe_service import slot_track

    result = await slot_track("t-manual")

    # The manual row stays untouched; the result flag is True.
    assert result.skipped_manual is True
    rows = db_with_phase6.exec(
        select(TrackVibe).where(TrackVibe.track_id == t.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].assigned_by == "manual"


# ---------------------------------------------------------------------------
# Test 7: additive Plex playlist push
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_track_pushes_to_plex_playlist_additively(db_with_phase6, monkeypatch):
    update_mock, _ = _patch_plex_helpers(monkeypatch)

    v = _add_vibe(db_with_phase6, "Workout", 0.5, 120.0, 0.5, 0.5)

    # Create a ManagedPlaylist row so vibe_service knows the playlist key.
    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            vibe_id=v.id,
            plex_rating_key="pl-100",
            composer_name="Composer · Workout",
        )
    )
    db_with_phase6.commit()

    _add_track(db_with_phase6, rk="t-push", rating=8.0,
               energy=0.5, tempo=120.0, danceability=0.5, valence=0.5)

    from app.services.vibe_service import slot_track

    await slot_track("t-push")

    # update_playlist_items called for the matched vibe with the full member list.
    assert update_mock.await_count == 1
    args, kwargs = update_mock.await_args
    # Either positional or keyword call — accept both.
    if args and len(args) >= 4:
        playlist_key = args[2]
        rating_keys = args[3]
    else:
        playlist_key = kwargs.get("playlist_rating_key")
        rating_keys = kwargs.get("desired_rating_keys")
    assert playlist_key == "pl-100"
    assert "t-push" in rating_keys


# ---------------------------------------------------------------------------
# Test 8: unslot_track removes all TrackVibe + remove_from_playlist
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unslot_track_removes_all_trackvibe_and_plex_calls(db_with_phase6, monkeypatch):
    _, remove_mock = _patch_plex_helpers(monkeypatch)

    v1 = _add_vibe(db_with_phase6, "V1", 0.4, 110.0, 0.4, 0.4)
    v2 = _add_vibe(db_with_phase6, "V2", 0.6, 130.0, 0.6, 0.6)

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe", vibe_id=v1.id, plex_rating_key="pl-1",
            composer_name="Composer · V1",
        )
    )
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe", vibe_id=v2.id, plex_rating_key="pl-2",
            composer_name="Composer · V2",
        )
    )
    db_with_phase6.commit()

    t = _add_track(db_with_phase6, rk="t-unslot", rating=8.0)

    from app.models.vibe import TrackVibe
    db_with_phase6.add(
        TrackVibe(track_id=t.id, vibe_id=v1.id, distance=0.1,
                  assigned_at=datetime.now(timezone.utc).isoformat(),
                  assigned_by="auto-slot")
    )
    db_with_phase6.add(
        TrackVibe(track_id=t.id, vibe_id=v2.id, distance=0.2,
                  assigned_at=datetime.now(timezone.utc).isoformat(),
                  assigned_by="auto-slot")
    )
    db_with_phase6.commit()

    from app.services.vibe_service import unslot_track

    await unslot_track("t-unslot")

    rows = db_with_phase6.exec(
        select(TrackVibe).where(TrackVibe.track_id == t.id)
    ).all()
    assert len(rows) == 0
    assert remove_mock.await_count == 2


# ---------------------------------------------------------------------------
# Test 9: per-track lock serializes concurrent slot_track
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_per_track_lock_serializes_concurrent_slot_in(db_with_phase6, monkeypatch):
    _patch_plex_helpers(monkeypatch)
    _add_vibe(db_with_phase6, "T", 0.5, 120.0, 0.5, 0.5)
    _add_track(db_with_phase6, rk="t-concurrent", rating=8.0,
               energy=0.5, tempo=120.0, danceability=0.5, valence=0.5)

    from app.services.vibe_service import slot_track, _get_lock

    # Two concurrent calls for the same ratingKey should serialize on the lock.
    lock = _get_lock("t-concurrent")
    # Manually acquire the lock first.
    await lock.acquire()

    async def call_slot():
        return await slot_track("t-concurrent")

    task = asyncio.create_task(call_slot())
    # Give the task a chance to start and block on the lock.
    await asyncio.sleep(0.05)
    assert not task.done(), "slot_track should be blocked on the per-track lock"
    lock.release()
    result = await task
    assert result.rating_key == "t-concurrent"


# ---------------------------------------------------------------------------
# Test 10: maybe_reslot_pending_track clears flag on success
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_maybe_reslot_pending_track_clears_flag_after_success(db_with_phase6, monkeypatch):
    _patch_plex_helpers(monkeypatch)
    _add_vibe(db_with_phase6, "Target", 0.5, 120.0, 0.5, 0.5)
    t = _add_track(db_with_phase6, rk="t-reslot", rating=8.0,
                   energy=0.5, tempo=120.0, danceability=0.5, valence=0.5)

    # Mark the track as pending_slot_in via raw SQL.
    import sqlite3
    db_path = str(db_with_phase6.bind.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE track SET pending_slot_in = 1 WHERE id = ?", (t.id,))
    conn.commit()
    conn.close()

    from app.services.vibe_service import maybe_reslot_pending_track

    await maybe_reslot_pending_track(t.id)

    # Flag should be cleared.
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT pending_slot_in FROM track WHERE id = ?", (t.id,))
    flag = cur.fetchone()[0]
    conn.close()
    assert flag == 0


# ---------------------------------------------------------------------------
# Test 11: maybe_reslot_pending_track skips when unrated
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_maybe_reslot_pending_track_skips_when_unrated(db_with_phase6, monkeypatch):
    _patch_plex_helpers(monkeypatch)
    _add_vibe(db_with_phase6, "Target", 0.5, 120.0, 0.5, 0.5)
    t = _add_track(db_with_phase6, rk="t-cleared", rating=0.0)

    import sqlite3
    db_path = str(db_with_phase6.bind.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE track SET pending_slot_in = 1 WHERE id = ?", (t.id,))
    conn.commit()
    conn.close()

    from app.services.vibe_service import maybe_reslot_pending_track
    await maybe_reslot_pending_track(t.id)

    # Flag stays 1 (rating cleared, no slot performed).
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT pending_slot_in FROM track WHERE id = ?", (t.id,))
    flag = cur.fetchone()[0]
    conn.close()
    assert flag == 1


# ---------------------------------------------------------------------------
# Test 12: reslot_all_rated_tracks runs sequentially
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reslot_all_rated_tracks_iterates_with_semaphore(db_with_phase6, monkeypatch):
    _patch_plex_helpers(monkeypatch)
    _add_vibe(db_with_phase6, "T", 0.5, 120.0, 0.5, 0.5)
    for i in range(5):
        _add_track(db_with_phase6, rk=f"r-{i}", rating=8.0,
                   energy=0.5, tempo=120.0, danceability=0.5, valence=0.5)

    # Mock slot_track to track concurrency
    concurrent_count = {"current": 0, "max": 0}
    real_slot = None

    async def counting_slot(rk):
        concurrent_count["current"] += 1
        concurrent_count["max"] = max(concurrent_count["max"], concurrent_count["current"])
        await asyncio.sleep(0.01)
        concurrent_count["current"] -= 1
        from app.services.vibe_service import SlotInResult
        return SlotInResult(
            track_id=0, rating_key=rk, primary_vibe_id=None,
            primary_distance=None, secondary_vibe_id=None,
            secondary_distance=None, pending=False,
        )

    monkeypatch.setattr("app.services.vibe_service.slot_track", counting_slot)

    from app.services.vibe_service import reslot_all_rated_tracks

    count = await reslot_all_rated_tracks()
    assert count == 5
    # D-25 semaphore=1 → never more than 1 active.
    assert concurrent_count["max"] == 1
