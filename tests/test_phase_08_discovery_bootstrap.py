"""Phase 8 Plan 01 Task 3 — schema migration + lifespan bootstrap tests.

Covers:
- Vibe.color field + _migrate_add_columns ALTER (D-E2)
- Track.plex_artist_mbid field + ALTER (Pitfall 12 foundation)
- New SQLModel tables in app/models/discovery.py
- run_phase_08_discovery_bootstrap: stamps CostMeterBaseline, seeds
  WeeklyCronState, backfills Vibe.color, best-effort backfills
  Track.plex_artist_mbid
- MigrationLog gate idempotency
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select


# ============================================================================
# Schema — model field existence + column ALTER
# ============================================================================


def test_vibe_color_column_exists():
    """Vibe model exposes Optional[str] color field defaulting to None."""
    from app.models.vibe import Vibe

    assert "color" in Vibe.model_fields, (
        "Vibe.color field missing — D-E2 requires Optional[str] hex like '#3b82f6'"
    )
    info = Vibe.model_fields["color"]
    # Default is None
    assert info.default is None or info.default == ... or info.default is None


def test_migration_add_columns_adds_color(fresh_db):
    """Running _migrate_add_columns on a fresh DB ensures Vibe.color is present."""
    # fresh_db fixture already ran init_db() which calls _migrate_add_columns.
    from app.database import get_engine

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("PRAGMA table_info(vibe)")
        cols = {row[1] for row in cur.fetchall()}
        assert "color" in cols, "vibe.color column missing after _migrate_add_columns"
        # Sanity: SELECT runs without error.
        cur.execute("SELECT color FROM vibe LIMIT 1")
        cur.fetchall()
    finally:
        raw_conn.close()


def test_track_plex_artist_mbid_column_exists():
    """Track model exposes Optional[str] plex_artist_mbid field for Pitfall 12 dedup."""
    from app.models.track import Track

    assert "plex_artist_mbid" in Track.model_fields, (
        "Track.plex_artist_mbid field missing — Pitfall 12 cross-surface dedup foundation"
    )


def test_migration_adds_track_plex_artist_mbid(fresh_db):
    """_migrate_add_columns adds track.plex_artist_mbid additively."""
    from app.database import get_engine

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("PRAGMA table_info(track)")
        cols = {row[1] for row in cur.fetchall()}
        assert "plex_artist_mbid" in cols, (
            "track.plex_artist_mbid column missing"
        )
        cur.execute("SELECT plex_artist_mbid FROM track LIMIT 1")
        cur.fetchall()
    finally:
        raw_conn.close()


# ============================================================================
# Discovery models — six new SQLModel classes registered
# ============================================================================


def test_discovery_candidate_table_exists(fresh_db):
    """DiscoveryCandidate field shape + table created."""
    from app.models.discovery import DiscoveryCandidate

    # Field-shape contract.
    fields = DiscoveryCandidate.model_fields
    for name in (
        "id", "mb_id", "artist_name", "seed_track_id", "seed_vibe_id",
        "mb_listener_count", "popularity_gate_pass", "llm_rank",
        "llm_rationale", "factual_hook", "created_at",
    ):
        assert name in fields, f"DiscoveryCandidate.{name} missing"

    # Table actually created.
    from app.database import get_engine

    raw = get_engine().raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='discoverycandidate'"
        )
        assert cur.fetchone() is not None, "discoverycandidate table not created"
    finally:
        raw.close()


def test_discovery_add_table_exists(fresh_db):
    from app.models.discovery import DiscoveryAdd

    fields = DiscoveryAdd.model_fields
    for name in (
        "id", "mb_id", "artist_name", "added_at",
        "lidarr_artist_id", "lidarr_status", "lidarr_status_polled_at",
        "composer_sync_seen_at", "essentia_complete_at",
        "vibe_slotted_at", "removed_from_discover_at",
    ):
        assert name in fields, f"DiscoveryAdd.{name} missing"


def test_discovery_dismissed_unique(fresh_db):
    """DiscoveryDismissed.mb_id is UNIQUE — second insert raises IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    from app.models.discovery import DiscoveryDismissed

    fresh_db.add(DiscoveryDismissed(
        mb_id="abc", artist_name="Foo", dismissed_at="2026-05-16T00:00:00+00:00",
    ))
    fresh_db.commit()

    fresh_db.add(DiscoveryDismissed(
        mb_id="abc", artist_name="Foo", dismissed_at="2026-05-17T00:00:00+00:00",
    ))
    with pytest.raises(IntegrityError):
        fresh_db.commit()
    fresh_db.rollback()


def test_musicbrainz_cache_unique(fresh_db):
    """MusicBrainzCache.mb_id is UNIQUE."""
    from sqlalchemy.exc import IntegrityError

    from app.models.discovery import MusicBrainzCache

    fresh_db.add(MusicBrainzCache(
        mb_id="abc", payload_json="{}", cached_at="2026-05-16T00:00:00+00:00",
    ))
    fresh_db.commit()

    fresh_db.add(MusicBrainzCache(
        mb_id="abc", payload_json="{}", cached_at="2026-05-17T00:00:00+00:00",
    ))
    with pytest.raises(IntegrityError):
        fresh_db.commit()
    fresh_db.rollback()


def test_cost_meter_baseline_is_single_row(fresh_db):
    from app.models.discovery import CostMeterBaseline

    info = CostMeterBaseline.model_fields["id"]
    # SQLModel exposes default via .default; for id with default=1 the actual default is 1.
    assert info.default == 1, (
        f"CostMeterBaseline.id default expected 1, got {info.default!r}"
    )
    assert "deploy_at" in CostMeterBaseline.model_fields


def test_weekly_cron_state_is_single_row(fresh_db):
    from app.models.discovery import WeeklyCronState

    info = WeeklyCronState.model_fields["id"]
    assert info.default == 1
    assert "last_tick_at" in WeeklyCronState.model_fields


# ============================================================================
# Bootstrap behaviour
# ============================================================================


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_bootstrap_stamps_baseline_once(fresh_db):
    """Bootstrap stamps CostMeterBaseline(id=1) + writes MigrationLog row."""
    from app.models.discovery import CostMeterBaseline
    from app.models.vibe import MigrationLog
    from app.services.discovery_service import (
        PHASE_08_MIGRATION_ID,
        run_phase_08_discovery_bootstrap,
    )

    _run_async(run_phase_08_discovery_bootstrap())

    baseline = fresh_db.get(CostMeterBaseline, 1)
    assert baseline is not None
    assert baseline.deploy_at  # ISO 8601 string populated

    log = fresh_db.exec(
        select(MigrationLog).where(MigrationLog.phase_id == PHASE_08_MIGRATION_ID)
    ).first()
    assert log is not None
    assert log.completed_at is not None


def test_bootstrap_creates_weekly_cron_state(fresh_db):
    from app.models.discovery import WeeklyCronState
    from app.services.discovery_service import run_phase_08_discovery_bootstrap

    _run_async(run_phase_08_discovery_bootstrap())

    cron = fresh_db.get(WeeklyCronState, 1)
    assert cron is not None
    assert cron.last_tick_at is None


def test_bootstrap_backfills_vibe_colors(fresh_db):
    """Bootstrap backfills color on every Vibe row that lacks one."""
    from app.models.vibe import Vibe
    from app.services.discovery_service import (
        VIBE_COLOR_PALETTE,
        run_phase_08_discovery_bootstrap,
    )

    # Pre-seed 3 vibes with no color
    now = datetime.now(timezone.utc).isoformat()
    fresh_db.add(Vibe(id=1, name="Late Night", created_at=now, color=None))
    fresh_db.add(Vibe(id=2, name="Focus", created_at=now, color=None))
    fresh_db.add(Vibe(id=3, name="Run", created_at=now, color=None))
    fresh_db.commit()

    _run_async(run_phase_08_discovery_bootstrap())

    fresh_db.expire_all()
    palette = set(VIBE_COLOR_PALETTE)
    for vibe_id in (1, 2, 3):
        v = fresh_db.get(Vibe, vibe_id)
        assert v.color is not None
        assert v.color in palette


def test_bootstrap_is_idempotent(fresh_db):
    """Second bootstrap is a no-op — baseline + vibe colors unchanged."""
    from app.models.discovery import CostMeterBaseline
    from app.models.vibe import Vibe
    from app.services.discovery_service import run_phase_08_discovery_bootstrap

    now = datetime.now(timezone.utc).isoformat()
    fresh_db.add(Vibe(id=42, name="Test", created_at=now, color=None))
    fresh_db.commit()

    _run_async(run_phase_08_discovery_bootstrap())

    fresh_db.expire_all()
    baseline_first = fresh_db.get(CostMeterBaseline, 1)
    first_deploy = baseline_first.deploy_at
    first_color = fresh_db.get(Vibe, 42).color
    assert first_color is not None

    _run_async(run_phase_08_discovery_bootstrap())

    fresh_db.expire_all()
    baseline_second = fresh_db.get(CostMeterBaseline, 1)
    second_color = fresh_db.get(Vibe, 42).color
    # deploy_at must be unchanged
    assert baseline_second.deploy_at == first_deploy
    # vibe color must be unchanged
    assert second_color == first_color


def test_bootstrap_palette_uses_locked_hexes(fresh_db):
    """The Tailwind 4 -500 palette tuple is exactly the D-E2 locked set; orange-500 LAST."""
    from app.services.discovery_service import VIBE_COLOR_PALETTE

    expected_set = {
        "#3b82f6", "#8b5cf6", "#10b981", "#f43f5e", "#f59e0b",
        "#06b6d4", "#ec4899", "#84cc16", "#f97316",
    }
    assert set(VIBE_COLOR_PALETTE) == expected_set
    # orange-500 collides with Plex accent (#e5a00d) so it MUST be last
    # (only used at 7+ vibes per D-E2).
    assert VIBE_COLOR_PALETTE[-1] == "#f97316", (
        "orange-500 must be the LAST palette entry (D-E2 Plex accent collision)"
    )


def test_bootstrap_backfills_track_artist_mbids(fresh_db, monkeypatch):
    """plex_client.get_artist_mbid_by_name resolves names → MBIDs; bootstrap
    persists them on every Track row for that artist.
    """
    from app.models.track import Track
    from app.services import discovery_service, plex_client

    fresh_db.add(Track(
        plex_rating_key="1", title="A", artist="Four Tet",
        plex_artist_mbid=None,
    ))
    fresh_db.add(Track(
        plex_rating_key="2", title="B", artist="Four Tet",
        plex_artist_mbid=None,
    ))
    fresh_db.add(Track(
        plex_rating_key="3", title="C", artist="Four Tet",
        plex_artist_mbid=None,
    ))
    fresh_db.commit()

    def _fake_lookup(name: str):
        if name == "Four Tet":
            return "f6f2326f-6b25-4170-b89d-e235b25508e8"
        return None

    monkeypatch.setattr(
        plex_client, "get_artist_mbid_by_name", _fake_lookup, raising=False,
    )

    _run_async(discovery_service.run_phase_08_discovery_bootstrap())

    fresh_db.expire_all()
    tracks = fresh_db.exec(select(Track)).all()
    assert all(
        t.plex_artist_mbid == "f6f2326f-6b25-4170-b89d-e235b25508e8"
        for t in tracks
    ), [t.plex_artist_mbid for t in tracks]


def test_bootstrap_backfill_tolerates_plex_unavailable(fresh_db, monkeypatch):
    """Plex unreachable → bootstrap still completes; baseline + vibe colors
    are still populated; track rows remain NULL.
    """
    from app.models.discovery import CostMeterBaseline, WeeklyCronState
    from app.models.track import Track
    from app.models.vibe import Vibe
    from app.services import discovery_service, plex_client

    now = datetime.now(timezone.utc).isoformat()
    fresh_db.add(Vibe(id=7, name="Chill", created_at=now, color=None))
    fresh_db.add(Track(
        plex_rating_key="1", title="A", artist="Foo",
        plex_artist_mbid=None,
    ))
    fresh_db.commit()

    def _raise(name: str):
        raise ConnectionError("plex unreachable")

    monkeypatch.setattr(
        plex_client, "get_artist_mbid_by_name", _raise, raising=False,
    )

    _run_async(discovery_service.run_phase_08_discovery_bootstrap())

    fresh_db.expire_all()
    # Baseline, weekly_cron_state, vibe color all populated despite Plex failure.
    assert fresh_db.get(CostMeterBaseline, 1) is not None
    assert fresh_db.get(WeeklyCronState, 1) is not None
    assert fresh_db.get(Vibe, 7).color is not None
    # Tracks remain NULL.
    track = fresh_db.exec(
        select(Track).where(Track.plex_rating_key == "1")
    ).first()
    assert track.plex_artist_mbid is None


def test_bootstrap_backfill_idempotent(fresh_db, monkeypatch):
    """Second bootstrap is a no-op — plex_client.get_artist_mbid_by_name
    is NOT called again because no rows are NULL anymore (and the MigrationLog
    gate short-circuits earlier).
    """
    from app.models.track import Track
    from app.services import discovery_service, plex_client

    fresh_db.add(Track(
        plex_rating_key="1", title="A", artist="Four Tet",
        plex_artist_mbid=None,
    ))
    fresh_db.commit()

    call_count = {"n": 0}

    def _counting_lookup(name: str):
        call_count["n"] += 1
        return "f6f2326f-6b25-4170-b89d-e235b25508e8"

    monkeypatch.setattr(
        plex_client, "get_artist_mbid_by_name", _counting_lookup, raising=False,
    )

    _run_async(discovery_service.run_phase_08_discovery_bootstrap())
    first_run_calls = call_count["n"]
    assert first_run_calls >= 1

    # Reset counter; run again
    call_count["n"] = 0
    _run_async(discovery_service.run_phase_08_discovery_bootstrap())
    # Second run short-circuits at MigrationLog gate; no lookups happen.
    assert call_count["n"] == 0
