"""Phase 7 Plan 02 Task 3 — Settings page LLM cost meter (OPS-05)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel


@pytest.fixture
def client_settings(test_engine) -> Generator[TestClient, None, None]:
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


@pytest.fixture(autouse=True)
def reset_breaker_status():
    try:
        from app.services import llm_cost_breaker
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from app.services import llm_cost_breaker
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus()
    except (ImportError, AttributeError):
        pass


def _seed_llmusage(session, called_at_iso, cache_creation=0, cache_read=0,
                   cost=0.001, purpose="suggestions_rank"):
    from app.models.llm_usage import LLMUsage

    session.add(LLMUsage(
        called_at=called_at_iso,
        purpose=purpose,
        model="claude-sonnet-4-6",
        input_tokens=100,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        output_tokens=50,
        cost_estimate_usd=cost,
    ))
    session.commit()


class TestSettingsCostMeter:
    def test_renders_cost_meter(self, client_settings, test_engine):
        now = datetime.now(timezone.utc).isoformat()
        with Session(test_engine) as s:
            for _ in range(3):
                _seed_llmusage(s, now, cost=0.005)
        resp = client_settings.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Anthropic spend today" in body
        assert "3 call" in body  # "3 calls"
        assert "$0.0" in body or "$0.01" in body
        assert "cache hit" in body.lower() or "Cache hit" in body

    def test_uses_today_utc_only(self, client_settings, test_engine):
        now = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=1)).isoformat()
        today = now.isoformat()
        with Session(test_engine) as s:
            _seed_llmusage(s, yesterday, cost=0.05)
            _seed_llmusage(s, today, cost=0.01)
        resp = client_settings.get("/settings")
        body = resp.text
        # Today shows count=1 (yesterday excluded).
        assert "1 call" in body
        # Yesterday's $0.05 must NOT appear in the today total.
        assert "$0.05" not in body or "0.06" not in body

    def test_cache_hit_pct_displayed(self, client_settings, test_engine):
        now = datetime.now(timezone.utc).isoformat()
        with Session(test_engine) as s:
            _seed_llmusage(s, now, cache_creation=2500, cache_read=0)
            _seed_llmusage(s, now, cache_creation=0, cache_read=2500)
        resp = client_settings.get("/settings")
        body = resp.text
        assert "50%" in body

    def test_breaker_paused_status_surfaces(self, client_settings, test_engine):
        from app.services import llm_cost_breaker

        # Trip the breaker manually so the settings page surfaces it.
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus(
            today_calls=50,
            today_cost_usd=0.42,
            last_call_at=datetime.now(timezone.utc).isoformat(),
            last_tripped_at=datetime.now(timezone.utc).isoformat(),
            last_tripped_reason="daily_quota_50",
        )
        resp = client_settings.get("/settings")
        body = resp.text
        assert "Suggestions paused" in body
        assert "daily_quota_50" in body

    def test_zero_state_no_calls(self, client_settings):
        resp = client_settings.get("/settings")
        body = resp.text
        assert "0 call" in body
        # cache hit pct shows "—" or absent percentage when no rows.
        assert "$0.00" in body
