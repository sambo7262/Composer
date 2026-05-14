"""Phase 7 Plan 03 Task 2 — /api/suggestions endpoints.

POST /api/suggestions/{rating_key}/dismiss is the user-dismiss action
(D-10 / SUGG-09). Calls handle_dismiss_track (Plan 02). Returns empty
HTML body so the HTMX outerHTML swap removes the row.
"""
from __future__ import annotations

from typing import Generator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel


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


class TestDismissEndpoint:
    def test_calls_handle_dismiss_track(self, client_full, monkeypatch):
        mock = AsyncMock()
        from app.services import suggestions_service
        monkeypatch.setattr(
            suggestions_service, "handle_dismiss_track", mock,
        )
        resp = client_full.post("/api/suggestions/rk-42/dismiss")
        assert resp.status_code == 200
        assert resp.text == ""  # empty body — HTMX outerHTML removes the row
        mock.assert_awaited_once_with("rk-42")

    def test_unknown_rating_key_idempotent(self, client_full, monkeypatch):
        """handle_dismiss_track is a no-op on unknown rating_keys (Plan 02
        contract). The endpoint still returns 200 and an empty body."""
        async def _noop(rk):
            return None
        from app.services import suggestions_service
        monkeypatch.setattr(
            suggestions_service, "handle_dismiss_track", _noop,
        )
        resp = client_full.post("/api/suggestions/rk-9999/dismiss")
        assert resp.status_code == 200

    def test_handle_dismiss_track_exception_is_swallowed(
        self, client_full, monkeypatch,
    ):
        """Best-effort: if Plan 02 raises, dismiss endpoint logs + returns
        200 (avoid leaving the row stuck on the page)."""
        async def _raise(rk):
            raise RuntimeError("plan 02 boom")
        from app.services import suggestions_service
        monkeypatch.setattr(
            suggestions_service, "handle_dismiss_track", _raise,
        )
        resp = client_full.post("/api/suggestions/rk-1/dismiss")
        # Endpoint returns 200 even on inner failure.
        assert resp.status_code == 200
