from __future__ import annotations

import os
from typing import Generator

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.config as config
from app.services.encryption import CredentialEncryptor, get_or_create_key


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path):
    """Override app config paths to use a temporary directory for all tests."""
    original_data_dir = config.DATA_DIR
    original_db_url = config.DATABASE_URL
    original_key_path = config.ENCRYPTION_KEY_PATH

    config.DATA_DIR = str(tmp_path)
    config.DATABASE_URL = f"sqlite:///{tmp_path}/composer.db"
    config.ENCRYPTION_KEY_PATH = os.path.join(str(tmp_path), ".encryption.key")

    # Reset singletons so they pick up the new config paths
    import app.services.encryption as enc_module
    from app.database import reset_engine
    enc_module._encryptor = None
    reset_engine()

    yield tmp_path

    # Restore original config
    config.DATA_DIR = original_data_dir
    config.DATABASE_URL = original_db_url
    config.ENCRYPTION_KEY_PATH = original_key_path
    enc_module._encryptor = None
    reset_engine()


@pytest.fixture
def test_engine(tmp_data_dir):
    """Create a test database engine with WAL mode."""
    from sqlalchemy import event

    db_url = f"sqlite:///{tmp_data_dir}/composer.db"
    eng = create_engine(db_url, echo=False, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return eng


@pytest.fixture
def test_db(test_engine) -> Generator[Session, None, None]:
    """Create tables, yield a session, drop tables after."""
    # Import model to register it with SQLModel metadata
    from app.models.settings import ServiceConfig  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def test_encryptor(tmp_data_dir) -> CredentialEncryptor:
    """Create an encryptor with a temporary key file."""
    key_path = os.path.join(str(tmp_data_dir), ".encryption.key")
    key = get_or_create_key(key_path)
    return CredentialEncryptor(key)


@pytest.fixture
def db_with_phase7(test_engine):
    """Phase 5 + 6 + 7 + 7.1 tables — promoted from tests/test_event_handlers.py
    (W13). Used by test_event_handlers.py, test_suggestions_discovery.py,
    and test_pages_settings.py.

    Runs ``init_db()`` so the additive ``_migrate_add_columns`` ALTERs
    (LLMUsage.error_text from Phase 7.1 + the existing setupstate / track
    ones) execute against the fixture's engine.
    """
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        DiscoveryState,  # Phase 7.1
        ManagedPlaylist,
        MigrationLog,
        SetupState,
        SlotInLog,
        TrackVibe,
        Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal,
        RefillTriggerLog,
        SuggestionHistory,
        SuggestionsMirror,
    )

    # init_db() runs the additive _migrate_add_columns ALTERs (LLMUsage
    # error_text from Phase 7.1 + the existing setupstate / track ones)
    # so every fixture user gets the full current schema.
    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Phase 5 lazy-engine pattern — fresh SQLite per test (W8 / W13).

    Differs from db_with_phase7 by overriding COMPOSER_DB_PATH and
    resetting the engine singleton; useful for tests that need to
    verify the FULL init_db() create_all path on a virgin DB (e.g.
    DiscoveryState table creation, init_db idempotency, ALTER TABLE
    migrations).
    """
    from app import database as db_module
    from app.database import get_engine, init_db

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("COMPOSER_DB_PATH", str(db_path))
    # Reset the lazy engine singleton so init_db() picks up the new path.
    db_module._engine = None
    init_db()
    engine = get_engine()
    with Session(engine) as session:
        yield session
    db_module._engine = None
