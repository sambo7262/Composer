"""Tests for app/models/event_log.py — UNIQUE(dedupe_key) constraint (EVT-02)."""
from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel


@pytest.fixture
def db_with_eventlog(test_engine) -> Generator[Session, None, None]:
    """Create tables including EventLog (and the rest of the Phase 5 model trio)."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


class TestEventLogModel:
    def test_dedupe_unique_constraint(self, db_with_eventlog):
        """Inserting two EventLog rows with the same dedupe_key raises IntegrityError.

        This is the source of truth for dedupe (D-07 / EVT-02).
        """
        from app.models.event_log import EventLog

        row1 = EventLog(
            source="webhook",
            event_type="rating_changed",
            plex_rating_key="42",
            dedupe_key="abc123",
            received_at="2026-05-08T12:00:00+00:00",
        )
        db_with_eventlog.add(row1)
        db_with_eventlog.commit()

        row2 = EventLog(
            source="poll",
            event_type="rating_changed",
            plex_rating_key="42",
            dedupe_key="abc123",  # same key
            received_at="2026-05-08T12:00:01+00:00",
        )
        db_with_eventlog.add(row2)
        with pytest.raises(IntegrityError):
            db_with_eventlog.commit()

    def test_event_log_required_fields(self, db_with_eventlog):
        """source, event_type, dedupe_key, received_at are all required (NOT NULL).

        Omitting source raises IntegrityError on commit.
        """
        from app.models.event_log import EventLog

        # source is non-Optional; SQLModel will reject None at validation
        # We verify by trying to commit a row with source=None via raw construction
        with pytest.raises((IntegrityError, ValueError, TypeError)):
            row = EventLog(
                source=None,  # type: ignore[arg-type]
                event_type="rating_changed",
                dedupe_key="missing-source",
                received_at="2026-05-08T12:00:00+00:00",
            )
            db_with_eventlog.add(row)
            db_with_eventlog.commit()

    def test_received_at_index_exists(self, db_with_eventlog):
        """EventLog.received_at must be indexed (DEBUG-01: ORDER BY received_at DESC)."""
        inspector = inspect(db_with_eventlog.get_bind())
        indexes = inspector.get_indexes("eventlog")
        indexed_columns = set()
        for idx in indexes:
            for col in idx["column_names"]:
                indexed_columns.add(col)
        assert "received_at" in indexed_columns

    def test_dedupe_key_indexed(self, db_with_eventlog):
        """dedupe_key must be indexed for fast INSERT OR IGNORE lookup."""
        inspector = inspect(db_with_eventlog.get_bind())
        indexes = inspector.get_indexes("eventlog")
        indexed_columns = set()
        for idx in indexes:
            for col in idx["column_names"]:
                indexed_columns.add(col)
        assert "dedupe_key" in indexed_columns

    def test_optional_fields_default_none(self, db_with_eventlog):
        """processed_at, handler_error, raw_payload, plex_rating_key default None."""
        from app.models.event_log import EventLog

        row = EventLog(
            source="webhook",
            event_type="webhook_test",
            dedupe_key="opt-defaults",
            received_at="2026-05-08T12:00:00+00:00",
        )
        db_with_eventlog.add(row)
        db_with_eventlog.commit()
        db_with_eventlog.refresh(row)

        assert row.processed_at is None
        assert row.handler_error is None
        assert row.raw_payload is None
        assert row.plex_rating_key is None
