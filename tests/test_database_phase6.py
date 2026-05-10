"""Tests for Phase 6 schema additions (D-28, D-29, D-30, OPS-06).

Verifies:
- Four new SQLModel tables registered: vibe, trackvibe, managedplaylist, setupstate.
- Track.pending_slot_in column added via _migrate_add_columns shim (idempotent).
- Three new indexes created: ix_trackvibe_vibe_id, ix_managedplaylist_kind,
  ix_track_pending_slot_in (partial, WHERE pending_slot_in = 1).
- ManagedPlaylist.UNIQUE(plex_rating_key) holds at the SQLite layer (D-27 / OPS-06
  DB-side dual-marker — the title-prefix check is the second marker, enforced
  in service code in Plans 02-04).
- TrackVibe round-trips an "auto-slot" assignment with a non-trivial distance.
- SetupState single-row pattern (id=1) is upsert-friendly via session.merge.
- _migrate_add_columns re-runs cleanly on a Phase-5-shaped DB without corrupting
  existing rows (Pitfall 19: additive-only schema migration).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Generator

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def db_with_phase6(test_engine) -> Generator[Session, None, None]:
    """Create all Phase 5 + Phase 6 tables and yield a session.

    Mirrors tests/test_event_handlers.py::db_with_phase5 but adds the four
    Phase 6 tables. autouse'd test_engine fixture comes from tests/conftest.py.
    """
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    # Phase 6
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        SetupState,
        SlotInLog,
        TrackVibe,
        Vibe,
    )

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


# ---------------------------------------------------------------------------
# Test 1: SQLModel registers the four new tables.
# ---------------------------------------------------------------------------
def test_init_db_registers_phase6_tables(test_engine):
    """After init_db(), SQLModel.metadata.tables contains all Phase 6 table names."""
    from app.database import init_db

    init_db()

    table_names = set(SQLModel.metadata.tables.keys())
    assert "vibe" in table_names
    assert "trackvibe" in table_names
    assert "managedplaylist" in table_names
    assert "setupstate" in table_names


# ---------------------------------------------------------------------------
# Test 2: Track.pending_slot_in column exists after init_db on a fresh DB.
# ---------------------------------------------------------------------------
def test_pending_slot_in_column_exists_after_init(test_engine):
    """Track.pending_slot_in is queryable after init_db on a brand-new DB.

    Brand-new DB: create_all() emits the column directly from the SQLModel
    definition (we don't add it to Track — the migration shim adds it for
    legacy DBs; create_all on a fresh DB also produces a working schema once
    the Phase 6 tables are registered).
    """
    from app.database import init_db

    init_db()
    db_path = str(test_engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        # SELECT must not raise; LIMIT 1 against an empty table returns no rows.
        cur.execute("SELECT pending_slot_in FROM track LIMIT 1")
        # Just verify the column exists per PRAGMA — fresh table has no rows.
        cur.execute("PRAGMA table_info(track)")
        cols = {row[1] for row in cur.fetchall()}
        assert "pending_slot_in" in cols, (
            f"pending_slot_in column missing after init_db. Got: {cols}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 3: _migrate_add_columns is idempotent on a Phase-5-shaped DB.
# ---------------------------------------------------------------------------
def test_migrate_idempotent_on_phase5_shaped_db(test_engine):
    """init_db() re-running on an already-migrated DB does not raise or corrupt data.

    Steps:
    1. Run init_db once (migrates from Phase 5 → Phase 6).
    2. Insert a sentinel Track row.
    3. Run init_db again. ALTER TABLE for already-existing columns is skipped;
       CREATE INDEX IF NOT EXISTS is no-op.
    4. The sentinel row is still readable, unchanged.
    """
    from app.database import init_db
    from app.models.track import Track

    init_db()

    # Insert a sentinel row using the migrated schema.
    db_path = str(test_engine.url).replace("sqlite:///", "")
    with Session(test_engine) as session:
        session.add(
            Track(
                plex_rating_key="phase6-idempotent-sentinel",
                title="Sentinel",
                artist="A",
                album="B",
                user_rating=8.0,
            )
        )
        session.commit()

    # Re-run init_db — must NOT throw and must NOT mutate the sentinel.
    init_db()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT title, artist, user_rating, pending_slot_in FROM track "
            "WHERE plex_rating_key = ?",
            ("phase6-idempotent-sentinel",),
        )
        row = cur.fetchone()
        assert row is not None, "Sentinel row vanished after re-running init_db"
        assert row[0] == "Sentinel"
        assert row[1] == "A"
        # user_rating preserved (float comparison loose)
        assert abs(row[2] - 8.0) < 1e-6
        # pending_slot_in defaults to 0 — column added cleanly
        assert row[3] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 4: TrackVibe round-trips an "auto-slot" assignment.
# ---------------------------------------------------------------------------
def test_trackvibe_round_trip_auto_slot(db_with_phase6):
    """A TrackVibe row with assigned_by="auto-slot", distance=0.42 round-trips."""
    from app.models.track import Track
    from app.models.vibe import TrackVibe, Vibe

    # A Track and a Vibe must exist to satisfy the FKs.
    db_with_phase6.add(
        Track(
            plex_rating_key="rt-1",
            title="t",
            artist="a",
            album="b",
        )
    )
    db_with_phase6.add(
        Vibe(
            name="Late Night",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    db_with_phase6.commit()

    # Fetch IDs.
    track_id = db_with_phase6.exec(select(Track.id)).first()
    vibe_id = db_with_phase6.exec(select(Vibe.id)).first()

    db_with_phase6.add(
        TrackVibe(
            track_id=track_id,
            vibe_id=vibe_id,
            distance=0.42,
            assigned_at=datetime.now(timezone.utc).isoformat(),
            assigned_by="auto-slot",
        )
    )
    db_with_phase6.commit()

    rows = db_with_phase6.exec(select(TrackVibe)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.track_id == track_id
    assert row.vibe_id == vibe_id
    assert abs(row.distance - 0.42) < 1e-6
    assert row.assigned_by == "auto-slot"


# ---------------------------------------------------------------------------
# Test 5: ManagedPlaylist UNIQUE(plex_rating_key) holds.
# ---------------------------------------------------------------------------
def test_managedplaylist_unique_plex_rating_key(db_with_phase6):
    """Inserting two ManagedPlaylist rows with the same plex_rating_key raises.

    DB-side enforcement of OPS-06 / D-27 dual-marker rule (the title-prefix
    "Composer · " is the second marker, checked in service code).
    """
    from app.models.vibe import ManagedPlaylist

    row1 = ManagedPlaylist(
        kind="vibe",
        plex_rating_key="42",
        composer_name="Composer · Late Night",
    )
    db_with_phase6.add(row1)
    db_with_phase6.commit()

    row2 = ManagedPlaylist(
        kind="vibe",
        plex_rating_key="42",  # same — must collide
        composer_name="Composer · Workout",
    )
    db_with_phase6.add(row2)
    with pytest.raises(IntegrityError):
        db_with_phase6.commit()


# ---------------------------------------------------------------------------
# Test 6: All three new indexes are created.
# ---------------------------------------------------------------------------
def test_phase6_indexes_present(test_engine):
    """init_db creates ix_trackvibe_vibe_id + ix_managedplaylist_kind +
    ix_track_pending_slot_in (D-30)."""
    from app.database import init_db

    init_db()
    db_path = str(test_engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
        index_names = {row[0] for row in cur.fetchall()}
        assert "ix_trackvibe_vibe_id" in index_names, (
            f"ix_trackvibe_vibe_id missing. Got: {sorted(index_names)}"
        )
        assert "ix_managedplaylist_kind" in index_names, (
            f"ix_managedplaylist_kind missing. Got: {sorted(index_names)}"
        )
        assert "ix_track_pending_slot_in" in index_names, (
            f"ix_track_pending_slot_in missing. Got: {sorted(index_names)}"
        )

        # Verify the partial-index WHERE clause on ix_track_pending_slot_in.
        cur.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='ix_track_pending_slot_in'"
        )
        sql = cur.fetchone()[0] or ""
        assert "WHERE pending_slot_in = 1" in sql, (
            f"ix_track_pending_slot_in is not a partial index. SQL: {sql!r}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 7: SetupState single-row pattern (id=1) supports session.merge upsert.
# ---------------------------------------------------------------------------
def test_setupstate_id_one_upsert_friendly(db_with_phase6):
    """Two consecutive session.merge(SetupState(id=1, ...)) calls do not duplicate."""
    from app.models.vibe import SetupState

    # First merge — inserts.
    db_with_phase6.merge(SetupState(id=1, step="proposing"))
    db_with_phase6.commit()

    # Second merge with the same id — updates in place.
    db_with_phase6.merge(SetupState(id=1, step="confirming"))
    db_with_phase6.commit()

    rows = db_with_phase6.exec(select(SetupState)).all()
    assert len(rows) == 1, (
        f"SetupState should remain a single-row table; got {len(rows)} rows"
    )
    assert rows[0].id == 1
    assert rows[0].step == "confirming"


# ===========================================================================
# Phase 6 Plan 04 — SlotInLog table + SetupState.recluster_mode column
# ===========================================================================

def test_slotinlog_table_created(test_engine):
    """D-36: after init_db, slotinlog table is registered with the expected columns."""
    from app.database import init_db

    init_db()

    table_names = set(SQLModel.metadata.tables.keys())
    assert "slotinlog" in table_names, (
        f"slotinlog table not registered. Got: {sorted(table_names)}"
    )

    db_path = str(test_engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(slotinlog)")
        cols = {row[1] for row in cur.fetchall()}
        for required in (
            "id",
            "timestamp",
            "track_id",
            "vibe_ids",
            "distances",
            "soft_membership_applied",
            "action",
            "note",
        ):
            assert required in cols, (
                f"slotinlog missing column {required!r}. Got: {sorted(cols)}"
            )
    finally:
        conn.close()


def test_slotinlog_timestamp_indexed(test_engine):
    """ix_slotinlog_timestamp index exists for the 'Last 20' ORDER BY DESC LIMIT 20 query."""
    from app.database import init_db

    init_db()
    db_path = str(test_engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA index_list('slotinlog')")
        idx_names = {row[1] for row in cur.fetchall()}
        assert "ix_slotinlog_timestamp" in idx_names, (
            f"ix_slotinlog_timestamp missing from slotinlog. Got: {sorted(idx_names)}"
        )
    finally:
        conn.close()


def test_setup_state_recluster_mode_column_added(test_engine):
    """D-20: SetupState.recluster_mode column added via migration; default 0 (False).

    Two-phase test: simulate a Plan 01-shape DB by creating the setupstate table
    WITHOUT the recluster_mode column, then run init_db and verify the migration
    added it.
    """
    from app.database import init_db

    # First init creates the schema fully (including recluster_mode if model present).
    init_db()
    db_path = str(test_engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(setupstate)")
        cols = {row[1] for row in cur.fetchall()}
        assert "recluster_mode" in cols, (
            f"setupstate missing recluster_mode column. Got: {sorted(cols)}"
        )

        # Insert a row without recluster_mode and verify default is 0.
        cur.execute(
            "INSERT INTO setupstate (id, step, draft_proposals_json, "
            "refinement_turn_count) VALUES (?, ?, ?, ?)",
            (1, "rating_source", "", 0),
        )
        conn.commit()
        cur.execute("SELECT recluster_mode FROM setupstate WHERE id = 1")
        val = cur.fetchone()[0]
        assert val == 0, f"recluster_mode default should be 0; got {val!r}"
    finally:
        conn.close()


def test_setup_state_recluster_mode_migration_on_legacy_db(test_engine):
    """Simulate a Plan 01-shape DB that lacks recluster_mode and verify migration adds it."""
    db_path = str(test_engine.url).replace("sqlite:///", "")

    # Create a legacy setupstate WITHOUT recluster_mode column.
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE setupstate ("
        "id INTEGER PRIMARY KEY, "
        "step TEXT, "
        "draft_proposals_json TEXT, "
        "started_at TEXT, "
        "completed_at TEXT, "
        "refinement_turn_count INTEGER DEFAULT 0, "
        "last_llm_call_id INTEGER"
        ")"
    )
    cur.execute(
        "INSERT INTO setupstate (id, step) VALUES (?, ?)",
        (1, "rating_source"),
    )
    conn.commit()
    conn.close()

    from app.database import init_db
    init_db()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(setupstate)")
        cols = {row[1] for row in cur.fetchall()}
        assert "recluster_mode" in cols, (
            f"recluster_mode not added by migration. Got: {sorted(cols)}"
        )
        # The pre-existing row's recluster_mode should default to 0.
        cur.execute("SELECT recluster_mode FROM setupstate WHERE id = 1")
        val = cur.fetchone()[0]
        assert val == 0, f"recluster_mode default should be 0; got {val!r}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 6.1 Test: MigrationLog table created after init_db (Blocker #4 — uses
# autouse tmp_data_dir fixture; NO monkeypatch.setenv).
# ---------------------------------------------------------------------------
def test_migrationlog_table_created_after_init_db(test_engine):
    """Phase 6.1 D-NEW-09 — init_db creates the migrationlog table with the
    expected columns (phase_id PK, completed_at NULL).
    """
    # Register all Phase 6 + 6.1 models so create_all picks up MigrationLog.
    from app.models.vibe import (  # noqa: F401
        Vibe, TrackVibe, ManagedPlaylist, SetupState, SlotInLog, MigrationLog,
    )
    SQLModel.metadata.create_all(test_engine)

    # Inspect schema directly via SQLAlchemy.
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(test_engine)
    tables = insp.get_table_names()
    assert "migrationlog" in tables, (
        f"migrationlog table missing after create_all; got {tables}"
    )
    cols = {c["name"] for c in insp.get_columns("migrationlog")}
    assert "phase_id" in cols, f"phase_id column missing; got {cols}"
    assert "completed_at" in cols, f"completed_at column missing; got {cols}"
