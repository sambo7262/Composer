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

    # Reset the encryption singleton so it picks up the new key path
    import app.services.encryption as enc_module
    enc_module._encryptor = None

    yield tmp_path

    # Restore original config
    config.DATA_DIR = original_data_dir
    config.DATABASE_URL = original_db_url
    config.ENCRYPTION_KEY_PATH = original_key_path
    enc_module._encryptor = None


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
