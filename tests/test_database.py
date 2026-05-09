from __future__ import annotations

from sqlmodel import SQLModel, text


def test_init_db_creates_tables(test_engine):
    """init_db creates the ServiceConfig table."""
    from app.models.settings import ServiceConfig  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    with test_engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = [row[0] for row in result]

    assert "serviceconfig" in tables


def test_wal_mode_active(test_engine):
    """SQLite WAL mode is active after engine connection."""
    with test_engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()

    assert mode == "wal"


def test_foreign_keys_enabled(test_engine):
    """SQLite foreign keys are enabled after engine connection."""
    with test_engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys"))
        fk = result.scalar()

    assert fk == 1


def test_phase5_migration(test_engine):
    """OPS-01: init_db() creates Phase 5 schema additions.

    Asserts:
    - 4 new Track columns: user_rating, last_viewed_at, view_count, rating_changed_at
    - 3 new tables: eventlog, llmusage, tasteprofile
    - 1 new index: ix_track_user_rating (RATE-04)
    """
    import sqlite3

    from app.database import init_db

    init_db()

    db_path = str(test_engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(track)")
        cols = {row[1]: row[2] for row in cur.fetchall()}
        assert cols.get("user_rating") == "REAL", f"user_rating column type wrong: {cols.get('user_rating')!r}"
        assert cols.get("last_viewed_at") == "TEXT"
        # SQLite reports the declared type; INTEGER is what _migrate_add_columns inserts.
        assert cols.get("view_count", "").upper().startswith("INTEGER")
        assert cols.get("rating_changed_at") == "TEXT"

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert {"eventlog", "llmusage", "tasteprofile"}.issubset(tables), (
            f"Missing Phase 5 tables. Found: {tables}"
        )

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_track_user_rating'"
        )
        assert cur.fetchone() is not None, "ix_track_user_rating index not created"
    finally:
        conn.close()
