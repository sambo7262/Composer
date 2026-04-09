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


def init_db() -> None:
    """Create all database tables."""
    # Import models to register them with SQLModel metadata
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import Track, SyncState  # noqa: F401

    SQLModel.metadata.create_all(get_engine())
