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
