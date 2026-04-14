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

    # Recalculate energy as weighted combination of loudness, tempo, complexity.
    # Old energy was spectral_rms-only which is unreliable (mastering-dependent).
    # New formula uses loudness (35%), existing energy/rms (25%), tempo (25%), complexity (15%).
    # The existing energy column was already normalized to [0,1] by prior migration,
    # so we use it directly as the rms component.
    cursor.execute("""
        UPDATE track SET energy = ROUND(MIN(MAX(
            0.35 * MIN(MAX((COALESCE(loudness, -20) + 20) / 15.0, 0.0), 1.0)
            + 0.25 * COALESCE(energy, 0.0)
            + 0.25 * MIN(MAX((COALESCE(tempo, 100) - 60) / 140.0, 0.0), 1.0)
            + 0.15 * MIN(MAX(COALESCE(spectral_complexity, 0) / 20.0, 0.0), 1.0)
        , 0.0), 1.0), 4)
        WHERE analyzed_at IS NOT NULL AND loudness IS NOT NULL
    """)
    updated = cursor.rowcount
    if updated > 0:
        logging.getLogger(__name__).info("Recalculated weighted energy for %d tracks", updated)

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
