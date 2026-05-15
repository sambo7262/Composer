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
        # Phase 7.1 D-D3 — MIGRATED: DAILY → WEEKLY heading text. Behavior
        # otherwise unchanged (3 calls seeded within the new 7-day window).
        now = datetime.now(timezone.utc).isoformat()
        with Session(test_engine) as s:
            for _ in range(3):
                _seed_llmusage(s, now, cost=0.005)
        resp = client_settings.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Anthropic spend this week" in body
        assert "3 call" in body  # "3 calls"
        assert "$0.0" in body or "$0.01" in body
        assert "cache hit" in body.lower() or "Cache hit" in body

    def test_uses_rolling_seven_day_window(self, client_settings, test_engine):
        """Phase 7.1 D-D3 — MIGRATED + RENAMED from test_uses_today_utc_only.

        Seeds three rows at 1h ago, 6d ago, 8d ago. The 6d-ago row is now
        INCLUDED in the new rolling 7-day window (it was excluded under the
        old midnight-of-today gate). The 8d-ago row is still excluded.

        Note (W12): LLMUsage.called_at is stored as ISO 8601 UTC. The
        ``>= seven_days_ago_iso`` comparison in pages.py::read_settings
        depends on lexicographic ordering matching chronological ordering;
        this holds for fixed-width ISO 8601 timestamps with UTC offset.
        """
        now = datetime.now(timezone.utc)
        one_hour_ago = (now - timedelta(hours=1)).isoformat()
        six_days_ago = (now - timedelta(days=6)).isoformat()
        eight_days_ago = (now - timedelta(days=8)).isoformat()
        with Session(test_engine) as s:
            _seed_llmusage(s, one_hour_ago, cost=0.01)
            _seed_llmusage(s, six_days_ago, cost=0.02)
            _seed_llmusage(s, eight_days_ago, cost=0.99)
        resp = client_settings.get("/settings")
        body = resp.text
        # Two rows fall inside the 7-day window (1h ago + 6d ago).
        assert "2 call" in body
        # The 8-day-old row's $0.99 cost must NOT appear in the total.
        assert "$0.99" not in body

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

    def test_renders_weekly_budget_value_not_daily(self, client_settings):
        """Phase 7.1 D-D3 — the rendered budget moves from $0.42/day
        to $0.50/week. The old daily value must not appear."""
        resp = client_settings.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "$0.50" in body
        assert "$0.42" not in body

    def test_renders_weekly_label_not_daily(self, client_settings):
        """Phase 7.1 D-D3 — heading moves DAILY → WEEKLY."""
        resp = client_settings.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Anthropic spend this week" in body
        assert "Anthropic spend today" not in body

    def test_caching_warning_replaced_with_neutral_copy(self, client_settings):
        """Phase 7.1 D-D3 — the legacy 'system prompt may be below
        2048 tokens' warning is misleading at weekly cadence (cold
        start by design); it must be replaced with neutral copy."""
        resp = client_settings.get("/settings")
        assert resp.status_code == 200
        assert "system prompt may be below 2048 tokens" not in resp.text
