from __future__ import annotations

from typing import Generator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    """Set SQLite pragmas on every connection: WAL mode and foreign keys."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_session() -> Generator[Session, None, None]:
    """Yield a database session."""
    with Session(engine) as session:
        yield session


def init_db() -> None:
    """Create all database tables."""
    SQLModel.metadata.create_all(engine)
