"""Phase 8 Plan 02 Task 4 — DiscoveryAdd lifecycle write hook tests.

Three idempotent stamping hooks with strict forward-only state machine:
  added_at → composer_sync_seen_at → essentia_complete_at → vibe_slotted_at

Hook gating:
- Hook 1 (composer_sync_seen): stamps when ANY Track exists with the same
  plex_artist_mbid as the DiscoveryAdd row. Idempotent NULL guard.
- Hook 2 (essentia_complete): requires composer_sync_seen_at NOT NULL AND
  ALL Track rows for the artist have energy NOT NULL.
- Hook 3 (vibe_slotted): requires essentia_complete_at NOT NULL AND a
  TrackVibe row exists for ANY Track with that plex_artist_mbid.

Hook 3 flipping `vibe_slotted_at` is the D-D4 "REMOVED from /discover"
lifecycle bit; Plan 04's read filter subtracts these rows from the page.

All hooks: best-effort, never raise, idempotent, async wrapper routes DB
work through asyncio.to_thread per Phase 5 D-09.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================================
# Hook 1: composer_sync_seen_at
# ============================================================================


def _seed_discovery_add(session, mb_id: str, **overrides):
    """Helper: create a DiscoveryAdd row with sensible defaults.

    Defaults can be overridden via kwargs; the helper merges so callers
    can pass e.g. ``composer_sync_seen_at="..."`` without colliding with
    the base dict.
    """
    from app.models.discovery import DiscoveryAdd

    defaults = {
        "artist_name": f"Artist {mb_id}",
        "added_at": "2026-05-15T00:00:00+00:00",
        "lidarr_artist_id": None,
        "lidarr_status": None,
        "lidarr_status_polled_at": None,
        "composer_sync_seen_at": None,
        "essentia_complete_at": None,
        "vibe_slotted_at": None,
    }
    defaults.update(overrides)
    row = DiscoveryAdd(mb_id=mb_id, **defaults)
    session.add(row)
    session.commit()
    return row


def _seed_track(
    session, *, plex_rating_key: str, plex_artist_mbid: str,
    energy=None, user_rating=None,
):
    from app.models.track import Track

    t = Track(
        plex_rating_key=plex_rating_key,
        title=f"T-{plex_rating_key}",
        artist=f"A-{plex_artist_mbid}",
        plex_artist_mbid=plex_artist_mbid,
        energy=energy,
        user_rating=user_rating,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_discovery_lifecycle_composer_sync_seen(db_with_phase7):
    """Pre-seed DiscoveryAdd(mb_id='abc', composer_sync_seen_at=None) +
    Track(plex_artist_mbid='abc'). Run hook → composer_sync_seen_at
    populated to a valid ISO 8601 UTC string.
    """
    from app.models.discovery import DiscoveryAdd
    from app.services.discovery_service import (
        _stamp_discovery_adds_composer_sync_seen_sync,
    )

    _seed_discovery_add(db_with_phase7, mb_id="abc")
    _seed_track(
        db_with_phase7, plex_rating_key="rk-1", plex_artist_mbid="abc",
    )

    _stamp_discovery_adds_composer_sync_seen_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row is not None
    assert row.composer_sync_seen_at is not None
    datetime.fromisoformat(row.composer_sync_seen_at)


def test_discovery_lifecycle_composer_sync_seen_idempotent(db_with_phase7):
    """composer_sync_seen_at already set → re-running hook does NOT change it."""
    from app.models.discovery import DiscoveryAdd
    from app.services.discovery_service import (
        _stamp_discovery_adds_composer_sync_seen_sync,
    )

    preset = "2026-05-15T03:00:00+00:00"
    _seed_discovery_add(
        db_with_phase7, mb_id="abc", composer_sync_seen_at=preset,
    )
    _seed_track(
        db_with_phase7, plex_rating_key="rk-1", plex_artist_mbid="abc",
    )

    _stamp_discovery_adds_composer_sync_seen_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.composer_sync_seen_at == preset


def test_discovery_lifecycle_composer_sync_seen_skips_when_no_matching_track(
    db_with_phase7,
):
    """DiscoveryAdd(mb_id='zzz'); NO Track has plex_artist_mbid='zzz' →
    composer_sync_seen_at remains NULL.
    """
    from app.models.discovery import DiscoveryAdd
    from app.services.discovery_service import (
        _stamp_discovery_adds_composer_sync_seen_sync,
    )

    _seed_discovery_add(db_with_phase7, mb_id="zzz")
    # Some unrelated track in the library
    _seed_track(
        db_with_phase7, plex_rating_key="rk-1", plex_artist_mbid="something-else",
    )

    _stamp_discovery_adds_composer_sync_seen_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "zzz")
    ).first()
    assert row.composer_sync_seen_at is None


# ============================================================================
# Hook 2: essentia_complete_at
# ============================================================================


def test_discovery_lifecycle_essentia_complete(db_with_phase7):
    """composer_sync_seen_at set + all tracks analyzed (energy NOT NULL) →
    essentia_complete_at populated.
    """
    from app.models.discovery import DiscoveryAdd
    from app.services.discovery_service import (
        _stamp_discovery_adds_essentia_complete_sync,
    )

    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
    )
    # 3 tracks for the artist, all analyzed (energy=0.5)
    for i in range(3):
        _seed_track(
            db_with_phase7, plex_rating_key=f"rk-{i}",
            plex_artist_mbid="abc", energy=0.5,
        )

    _stamp_discovery_adds_essentia_complete_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.essentia_complete_at is not None
    datetime.fromisoformat(row.essentia_complete_at)


def test_discovery_lifecycle_essentia_complete_requires_composer_sync_seen(
    db_with_phase7,
):
    """composer_sync_seen_at is NULL → essentia_complete_at remains NULL
    even when ALL tracks for the artist are analyzed (gate enforced).
    """
    from app.models.discovery import DiscoveryAdd
    from app.services.discovery_service import (
        _stamp_discovery_adds_essentia_complete_sync,
    )

    _seed_discovery_add(db_with_phase7, mb_id="abc")  # composer_sync_seen=NULL
    for i in range(2):
        _seed_track(
            db_with_phase7, plex_rating_key=f"rk-{i}",
            plex_artist_mbid="abc", energy=0.5,
        )

    _stamp_discovery_adds_essentia_complete_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.essentia_complete_at is None


def test_discovery_lifecycle_essentia_complete_waits_for_last_unanalyzed(
    db_with_phase7,
):
    """5 tracks, 4 analyzed + 1 NULL energy → hook does NOT stamp.
    Mark the 5th analyzed → next hook call stamps it.
    """
    from app.models.discovery import DiscoveryAdd
    from app.models.track import Track
    from app.services.discovery_service import (
        _stamp_discovery_adds_essentia_complete_sync,
    )

    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
    )
    for i in range(4):
        _seed_track(
            db_with_phase7, plex_rating_key=f"rk-{i}",
            plex_artist_mbid="abc", energy=0.5,
        )
    # 5th track unanalyzed
    last_track = _seed_track(
        db_with_phase7, plex_rating_key="rk-5",
        plex_artist_mbid="abc", energy=None,
    )

    _stamp_discovery_adds_essentia_complete_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.essentia_complete_at is None, (
        "should NOT stamp while any track is unanalyzed"
    )

    # Mark the 5th analyzed and re-run.
    target = db_with_phase7.get(Track, last_track.id)
    target.energy = 0.7
    db_with_phase7.add(target)
    db_with_phase7.commit()

    _stamp_discovery_adds_essentia_complete_sync()
    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.essentia_complete_at is not None


def test_discovery_lifecycle_essentia_complete_idempotent(db_with_phase7):
    """essentia_complete_at already set → re-run is a no-op (value preserved)."""
    from app.models.discovery import DiscoveryAdd
    from app.services.discovery_service import (
        _stamp_discovery_adds_essentia_complete_sync,
    )

    preset = "2026-05-15T05:00:00+00:00"
    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        essentia_complete_at=preset,
    )
    for i in range(2):
        _seed_track(
            db_with_phase7, plex_rating_key=f"rk-{i}",
            plex_artist_mbid="abc", energy=0.5,
        )

    _stamp_discovery_adds_essentia_complete_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.essentia_complete_at == preset


# ============================================================================
# Hook 3: vibe_slotted_at
# ============================================================================


def test_discovery_lifecycle_vibe_slotted(db_with_phase7):
    """essentia_complete_at set + TrackVibe row exists for the artist →
    vibe_slotted_at populated.
    """
    from app.models.discovery import DiscoveryAdd
    from app.models.vibe import TrackVibe, Vibe
    from app.services.discovery_service import (
        _stamp_discovery_adds_vibe_slotted_sync,
    )

    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        essentia_complete_at="2026-05-15T05:00:00+00:00",
    )
    track = _seed_track(
        db_with_phase7, plex_rating_key="rk-1",
        plex_artist_mbid="abc", energy=0.5,
    )
    db_with_phase7.add(Vibe(
        id=1, name="V1", created_at="2026-05-15T00:00:00+00:00",
    ))
    db_with_phase7.commit()
    db_with_phase7.add(TrackVibe(
        track_id=track.id, vibe_id=1, distance=0.3,
        assigned_at="2026-05-15T05:30:00+00:00",
        assigned_by="auto-slot",
    ))
    db_with_phase7.commit()

    _stamp_discovery_adds_vibe_slotted_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.vibe_slotted_at is not None
    datetime.fromisoformat(row.vibe_slotted_at)


def test_discovery_lifecycle_vibe_slotted_requires_essentia_complete(
    db_with_phase7,
):
    """essentia_complete_at IS NULL → vibe_slotted_at NOT stamped, even
    when a TrackVibe row exists.
    """
    from app.models.discovery import DiscoveryAdd
    from app.models.vibe import TrackVibe, Vibe
    from app.services.discovery_service import (
        _stamp_discovery_adds_vibe_slotted_sync,
    )

    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        # essentia_complete_at intentionally NULL
    )
    track = _seed_track(
        db_with_phase7, plex_rating_key="rk-1",
        plex_artist_mbid="abc", energy=0.5,
    )
    db_with_phase7.add(Vibe(
        id=1, name="V1", created_at="2026-05-15T00:00:00+00:00",
    ))
    db_with_phase7.commit()
    db_with_phase7.add(TrackVibe(
        track_id=track.id, vibe_id=1, distance=0.3,
        assigned_at="2026-05-15T05:30:00+00:00",
        assigned_by="auto-slot",
    ))
    db_with_phase7.commit()

    _stamp_discovery_adds_vibe_slotted_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.vibe_slotted_at is None


def test_discovery_lifecycle_vibe_slotted_idempotent(db_with_phase7):
    """vibe_slotted_at already set → re-run does NOT re-stamp."""
    from app.models.discovery import DiscoveryAdd
    from app.models.vibe import TrackVibe, Vibe
    from app.services.discovery_service import (
        _stamp_discovery_adds_vibe_slotted_sync,
    )

    preset = "2026-05-15T06:00:00+00:00"
    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        essentia_complete_at="2026-05-15T05:00:00+00:00",
        vibe_slotted_at=preset,
    )
    track = _seed_track(
        db_with_phase7, plex_rating_key="rk-1",
        plex_artist_mbid="abc", energy=0.5,
    )
    db_with_phase7.add(Vibe(
        id=1, name="V1", created_at="2026-05-15T00:00:00+00:00",
    ))
    db_with_phase7.commit()
    db_with_phase7.add(TrackVibe(
        track_id=track.id, vibe_id=1, distance=0.3,
        assigned_at="2026-05-15T05:30:00+00:00",
        assigned_by="auto-slot",
    ))
    db_with_phase7.commit()

    _stamp_discovery_adds_vibe_slotted_sync()

    db_with_phase7.expire_all()
    row = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    assert row.vibe_slotted_at == preset


# ============================================================================
# AST + observability tests
# ============================================================================


def test_all_three_hooks_wrap_db_in_asyncio_to_thread():
    """The three async wrappers
    (stamp_discovery_adds_composer_sync_seen / _essentia_complete /
    _vibe_slotted) MUST route DB work through asyncio.to_thread per
    Phase 5 D-09.
    """
    path = (
        Path(__file__).parent.parent
        / "app" / "services" / "discovery_service.py"
    )
    source = path.read_text()
    tree = ast.parse(source)

    wrapper_names = {
        "stamp_discovery_adds_composer_sync_seen",
        "stamp_discovery_adds_essentia_complete",
        "stamp_discovery_adds_vibe_slotted",
    }
    found = {n: False for n in wrapper_names}

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name not in wrapper_names:
            continue
        # Walk body for an asyncio.to_thread Call.
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                f = inner.func
                if isinstance(f, ast.Attribute) and f.attr == "to_thread":
                    found[node.name] = True
                    break
                if isinstance(f, ast.Name) and f.id == "to_thread":
                    found[node.name] = True
                    break

    missing = [n for n, ok in found.items() if not ok]
    assert not missing, (
        f"Async wrappers missing asyncio.to_thread: {missing!r} — "
        f"Phase 5 D-09 invariant"
    )


def test_status_returns_analyzed_slotted(db_with_phase7):
    """When ``DiscoveryAdd.vibe_slotted_at IS NOT NULL``, the lifecycle
    status helper returns "analyzed, slotted into vibes" — the D-D4
    branch that flips the card-removal bit.

    Plan 04 owns the eventual ``get_lidarr_status_for_add``. Task 4 ships
    a minimal helper that Plan 04 will call (or replace with a wider
    surface). The helper reads DiscoveryAdd.vibe_slotted_at by mb_id.
    """
    from app.services.discovery_service import (
        get_lidarr_status_for_add,
    )

    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        essentia_complete_at="2026-05-15T05:00:00+00:00",
        vibe_slotted_at="2026-05-15T06:00:00+00:00",
    )

    result = _run_async(get_lidarr_status_for_add("abc"))
    assert result == "analyzed, slotted into vibes"


def test_status_returns_other_state_when_not_slotted(db_with_phase7):
    """Vibe_slotted_at NULL → status helper returns a different string
    (Plan 04 wires the full set of in-progress / completed states;
    Task 4 just needs the slotted branch + an obviously-not-slotted
    fallback for safety).
    """
    from app.services.discovery_service import (
        get_lidarr_status_for_add,
    )

    _seed_discovery_add(
        db_with_phase7, mb_id="not-slotted",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        essentia_complete_at="2026-05-15T05:00:00+00:00",
        # vibe_slotted_at intentionally NULL
    )

    result = _run_async(get_lidarr_status_for_add("not-slotted"))
    assert result != "analyzed, slotted into vibes"


# ============================================================================
# Integration: triggering_mb_id fast-path for hook 3
# ============================================================================


def test_discovery_lifecycle_vibe_slotted_fast_path(db_with_phase7):
    """The vibe_slotted hook's ``triggering_mb_id`` scopes the check to
    one artist — used from event_handlers / vibe_service when the
    just-slotted track's artist MBID is known.
    """
    from app.models.discovery import DiscoveryAdd
    from app.models.vibe import TrackVibe, Vibe
    from app.services.discovery_service import (
        _stamp_discovery_adds_vibe_slotted_sync,
    )

    # Two pending DiscoveryAdd rows, both eligible (composer_sync + essentia ok).
    _seed_discovery_add(
        db_with_phase7, mb_id="abc",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        essentia_complete_at="2026-05-15T05:00:00+00:00",
    )
    _seed_discovery_add(
        db_with_phase7, mb_id="xyz",
        composer_sync_seen_at="2026-05-15T03:00:00+00:00",
        essentia_complete_at="2026-05-15T05:00:00+00:00",
    )
    # Only "abc" has a slotted track.
    track = _seed_track(
        db_with_phase7, plex_rating_key="rk-1",
        plex_artist_mbid="abc", energy=0.5,
    )
    db_with_phase7.add(Vibe(
        id=1, name="V1", created_at="2026-05-15T00:00:00+00:00",
    ))
    db_with_phase7.commit()
    db_with_phase7.add(TrackVibe(
        track_id=track.id, vibe_id=1, distance=0.3,
        assigned_at="2026-05-15T05:30:00+00:00",
        assigned_by="auto-slot",
    ))
    db_with_phase7.commit()

    # Fast-path: scope to mb_id="abc" only.
    _stamp_discovery_adds_vibe_slotted_sync(triggering_mb_id="abc")

    db_with_phase7.expire_all()
    abc = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "abc")
    ).first()
    xyz = db_with_phase7.exec(
        select(DiscoveryAdd).where(DiscoveryAdd.mb_id == "xyz")
    ).first()
    assert abc.vibe_slotted_at is not None
    assert xyz.vibe_slotted_at is None  # not in scope of the fast-path call
