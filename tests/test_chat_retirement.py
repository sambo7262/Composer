"""Phase 7 Plan 03 Task 2 — UI-06 v1 chat retirement.

GET /chat returns 404. POST /api/chat/* endpoints return 404.
Templates are MOVED to app/templates/_archived/ (not deleted — data and
templates preserved per CONTEXT 'Deferred Ideas: v1 chat data archival
policy'). The Plex/SQLite chat-data tables are untouched.
"""
from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"


@pytest.fixture
def client_full(test_engine) -> Generator[TestClient, None, None]:
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist, MigrationLog, SetupState, SlotInLog, TrackVibe, Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)
    from app.main import app
    with TestClient(app) as c:
        yield c
    SQLModel.metadata.drop_all(test_engine)


def test_chat_get_returns_404(client_full):
    resp = client_full.get("/chat")
    assert resp.status_code == 404


def test_api_chat_message_returns_404(client_full):
    resp = client_full.post(
        "/api/chat/message",
        data={"message": "hi", "session_id": "s1"},
    )
    assert resp.status_code == 404


def test_api_chat_remove_track_returns_404(client_full):
    resp = client_full.post(
        "/api/chat/remove-track",
        data={"session_id": "s1", "track_id": 1},
    )
    assert resp.status_code == 404


def test_api_chat_reorder_returns_404(client_full):
    resp = client_full.post(
        "/api/chat/reorder",
        data={"session_id": "s1", "track_id": 1, "position": 0},
    )
    assert resp.status_code == 404


def test_api_chat_new_returns_404(client_full):
    resp = client_full.post("/api/chat/new", data={"session_id": "s1"})
    assert resp.status_code == 404


def test_api_chat_push_to_plex_returns_404(client_full):
    resp = client_full.post(
        "/api/chat/push-to-plex",
        data={"session_id": "s1", "playlist_name": "x"},
    )
    assert resp.status_code == 404


def test_chat_templates_archived_not_deleted():
    """T-07-03-08 / UI-06 — templates moved to _archived/, not removed."""
    archived_dir = TEMPLATES_DIR / "_archived"
    assert archived_dir.is_dir(), (
        "app/templates/_archived/ must exist (UI-06)"
    )
    assert (archived_dir / "chat.html").exists()
    assert (archived_dir / "chat_message.html").exists()
    assert (archived_dir / "playlist_card.html").exists()
    # The originals must NOT be in their old paths.
    assert not (TEMPLATES_DIR / "pages" / "chat.html").exists()
    assert not (TEMPLATES_DIR / "partials" / "chat_message.html").exists()
    assert not (TEMPLATES_DIR / "partials" / "playlist_card.html").exists()


def test_chat_data_models_still_import():
    """UI-06 deferred ideas: chat data preserved in DB; only UI access is
    removed. The Python imports for any chat-related model still resolve.
    """
    # The chat_service module is the canonical chat-data accessor; if it
    # imports cleanly, the data layer is intact.
    import importlib
    mod = importlib.import_module("app.services.chat_service")
    assert mod is not None


def test_nav_does_not_link_to_chat():
    body = (TEMPLATES_DIR / "partials" / "nav.html").read_text()
    assert "/chat" not in body
    # The label "Compose" was the v1 chat link label.
    import re
    assert not re.search(r"\bCompose\b", body)


def test_no_template_includes_archive():
    """T-07-03-09 — guard against future re-introduction of archived
    templates via {% include %}.
    """
    pages_dir = TEMPLATES_DIR / "pages"
    partials_dir = TEMPLATES_DIR / "partials"
    bad_refs = []
    for d in (pages_dir, partials_dir):
        for fp in d.rglob("*.html"):
            text = fp.read_text()
            if "_archived/" in text:
                bad_refs.append(str(fp))
    assert not bad_refs, (
        f"Found template includes referencing _archived/: {bad_refs}"
    )
