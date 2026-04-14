from __future__ import annotations

from typing import Generator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app import config

_engine = None


def get_engine():
    """Get or create the database engine. Lazy initialization allows test overrides."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.DATABASE_URL,
            echo=False,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            """Set SQLite pragmas on every connection: WAL mode and foreign keys."""
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def reset_engine():
    """Reset the engine singleton. Used by tests to pick up config changes."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_session() -> Generator[Session, None, None]:
    """Yield a database session."""
    with Session(get_engine()) as session:
        yield session


def _migrate_add_columns(engine) -> None:
    """Add any missing columns to existing tables (lightweight schema migration)."""
    import logging
    import sqlite3
    url = str(engine.url)
    db_path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get existing columns for the track table
    cursor.execute("PRAGMA table_info(track)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # Columns added in Phase 3 (audio feature extraction)
    new_columns = {
        "file_path": "TEXT",
        "energy": "REAL",
        "tempo": "REAL",
        "danceability": "REAL",
        "valence": "REAL",
        "musical_key": "TEXT",
        "scale": "TEXT",
        "spectral_complexity": "REAL",
        "loudness": "REAL",
        "analyzed_at": "TEXT",
        "analysis_error": "TEXT",
    }

    for col_name, col_type in new_columns.items():
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE track ADD COLUMN {col_name} {col_type}")

    # One-time migration: normalize energy values from raw [0, ~0.3] to [0, 1]
    # Detect if migration needed: if any analyzed track has energy < 0.5 and energy > 0,
    # it's likely raw (unnormalized). A value of 0.1 raw = 0.33 normalized.
    cursor.execute("SELECT COUNT(*) FROM track WHERE energy IS NOT NULL AND energy > 0 AND energy < 0.35 AND analyzed_at IS NOT NULL")
    raw_energy_count = cursor.fetchone()[0]
    if raw_energy_count > 0:
        cursor.execute("UPDATE track SET energy = MIN(energy / 0.3, 1.0) WHERE energy IS NOT NULL AND analyzed_at IS NOT NULL")
        logging.getLogger(__name__).info("Normalized energy values for %d tracks (raw -> [0,1])", raw_energy_count)

    conn.commit()
    conn.close()


def init_db() -> None:
    """Create all database tables and migrate schema if needed."""
    # Import models to register them with SQLModel metadata
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import Track, SyncState  # noqa: F401
    from app.models.playlist import Playlist, PlaylistTrack  # noqa: F401

    engine = get_engine()
    SQLModel.metadata.create_all(engine)

    # Add any missing columns to existing tables
    try:
        _migrate_add_columns(engine)
    except Exception:
        pass  # Table may not exist yet on first run — create_all handles it
