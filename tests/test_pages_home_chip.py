"""Phase 8 Plan 03 Task 2 — Home-page weekly LLM cost chip (UI-10 / D-B4).

Pins:
- Chip query shape (sum LLMUsage.cost_estimate_usd WHERE called_at >=
  max(WeeklyCronState.last_tick_at, CostMeterBaseline.deploy_at)).
- Pre-baseline / pre-first-tick / breaker-open variants.
- Days-until-refresh computation.
- Tap target ≥44px (min-h-11).
- Partial include from vibes_home.html.
- Defensive against missing CostMeterBaseline / WeeklyCronState rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def client_full(test_engine) -> Generator[TestClient, None, None]:
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        DiscoveryState, ManagedPlaylist, MigrationLog, SetupState, SlotInLog,
        TrackVibe, Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
    )
    from app.models.discovery import (  # noqa: F401
        CostMeterBaseline, DiscoveryAdd, DiscoveryCandidate, DiscoveryDismissed,
        MusicBrainzCache, WeeklyCronState,
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
    """Reset the in-memory breaker singleton between tests."""
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


def _seed_plex_configured_and_vibe(session):
    """Plex configured + 1 vibe so / renders vibes_home (not redirect/welcome)."""
    from app.services.settings_service import save_setting
    from app.models.vibe import Vibe

    save_setting(
        session, service_name="plex",
        url="http://plex.local:32400",
        credential="x" * 20,
    )
    now = datetime.now(timezone.utc).isoformat()
    v = Vibe(name="V1", created_at=now, color="#3b82f6")
    session.add(v)
    session.commit()


def _seed_baseline(session, deploy_at_iso=None):
    from app.models.discovery import CostMeterBaseline
    if deploy_at_iso is None:
        deploy_at_iso = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
    row = session.get(CostMeterBaseline, 1)
    if row is None:
        row = CostMeterBaseline(id=1, deploy_at=deploy_at_iso)
        session.add(row)
    else:
        row.deploy_at = deploy_at_iso
        session.add(row)
    session.commit()
    return deploy_at_iso


def _seed_weekly_cron(session, last_tick_at_iso=None):
    from app.models.discovery import WeeklyCronState
    row = session.get(WeeklyCronState, 1)
    if row is None:
        row = WeeklyCronState(id=1, last_tick_at=last_tick_at_iso)
        session.add(row)
    else:
        row.last_tick_at = last_tick_at_iso
        session.add(row)
    session.commit()
    return last_tick_at_iso


def _seed_llmusage(session, called_at_iso, cost=0.05, purpose="discovery_artist_weekly"):
    from app.models.llm_usage import LLMUsage
    session.add(LLMUsage(
        called_at=called_at_iso,
        purpose=purpose,
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost_estimate_usd=cost,
    ))
    session.commit()


# ============================================================================
# Chip context computation
# ============================================================================


class TestHomeChipQuery:
    def test_chip_renders_zero_when_no_llm_usage(self, client_full, test_engine):
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s)
            _seed_weekly_cron(s, last_tick_at_iso=None)
        resp = client_full.get("/vibes")
        body = resp.text
        assert resp.status_code == 200
        # Pre-first-tick message renders.
        # Pre-first-tick message now renders the next Sun 03:00 UTC tick as
        # LA-localized "Saturday May 17 at 8:00 PM PDT" (or PST in winter).
        # DST-aware via zoneinfo. Pin the prefix + the PT zone marker.
        # Pre-first-tick chip now renders cost (even $0.00) + "first refresh"
        # context (LA-localized via next_sunday_03_utc_in_la, DST-aware).
        assert "first refresh" in body
        assert ("PDT" in body or "PST" in body)

    def test_chip_baseline_filter_excludes_historical_rows(self, client_full, test_engine):
        """Rows with called_at < CostMeterBaseline.deploy_at are excluded."""
        now = datetime.now(timezone.utc)
        deploy_iso = now.isoformat()
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s, deploy_at_iso=deploy_iso)
            _seed_weekly_cron(s, last_tick_at_iso=None)
            # Historical row BEFORE baseline — must be excluded.
            _seed_llmusage(s, (now - timedelta(hours=1)).isoformat(), cost=0.20)
            # Recent row AFTER baseline — must be counted.
            _seed_llmusage(s, (now + timedelta(hours=1)).isoformat(), cost=0.07)
        resp = client_full.get("/vibes")
        body = resp.text
        # Only the post-baseline $0.07 row should count.
        # Pre-first-tick state still shows the first-tick message, so we can't
        # assert via display. Instead, render /vibes and pluck the cost from
        # the chip when has_first_tick is True. Switch to last_tick_at set.
        with Session(test_engine) as s:
            _seed_weekly_cron(s, last_tick_at_iso=deploy_iso)
        resp = client_full.get("/vibes")
        body = resp.text
        assert "$0.07" in body
        # The pre-baseline $0.20 row must not appear in the chip.
        assert "$0.27" not in body

    def test_chip_filters_to_last_tick(self, client_full, test_engine):
        """When last_tick_at is later than baseline, that becomes the floor."""
        now = datetime.now(timezone.utc)
        baseline_iso = (now - timedelta(days=30)).isoformat()
        tick_iso = (now - timedelta(days=2)).isoformat()
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s, deploy_at_iso=baseline_iso)
            _seed_weekly_cron(s, last_tick_at_iso=tick_iso)
            # Row BEFORE last_tick — must be excluded.
            _seed_llmusage(s, (now - timedelta(days=5)).isoformat(), cost=0.50)
            # Row AFTER last_tick — must be counted.
            _seed_llmusage(s, (now - timedelta(days=1)).isoformat(), cost=0.11)
        resp = client_full.get("/vibes")
        body = resp.text
        assert "$0.11" in body
        assert "$0.61" not in body  # combined sum must not leak

    def test_chip_renders_breaker_paused_modifier(self, client_full, test_engine, monkeypatch):
        from app.services import llm_cost_breaker
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s)
            _seed_weekly_cron(
                s, last_tick_at_iso=datetime.now(timezone.utc).isoformat(),
            )
        # Trip the breaker — the chip should render the "paused" modifier.
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus(
            today_calls=10, today_cost_usd=0.0,
            last_call_at=None,
            last_tripped_at=datetime.now(timezone.utc).isoformat(),
            last_tripped_reason="burst_5_per_60s",
        )
        resp = client_full.get("/vibes")
        body = resp.text
        assert "paused" in body

    def test_chip_days_until_refresh_computed_correctly(self, client_full, test_engine):
        """last_tick = 3 days ago → days_until_refresh ≈ 4 (7 - 3)."""
        now = datetime.now(timezone.utc)
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s)
            _seed_weekly_cron(
                s, last_tick_at_iso=(now - timedelta(days=3)).isoformat(),
            )
        resp = client_full.get("/vibes")
        body = resp.text
        # 7 - 3 = 4 days remaining. timedelta(days=7) - timedelta(days=3) = 4d.
        assert "in 4d" in body or "in 3d" in body, body[:2000]

    def test_chip_days_until_refresh_zero_when_overdue(self, client_full, test_engine):
        """last_tick = 10 days ago → days_until_refresh == 0 (overdue)."""
        now = datetime.now(timezone.utc)
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s)
            _seed_weekly_cron(
                s, last_tick_at_iso=(now - timedelta(days=10)).isoformat(),
            )
        resp = client_full.get("/vibes")
        body = resp.text
        assert "in 0d" in body

    def test_chip_tap_navigates_to_debug_suggestions(self, client_full, test_engine):
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s)
            _seed_weekly_cron(s, last_tick_at_iso=None)
        resp = client_full.get("/vibes")
        body = resp.text
        assert 'href="/debug/suggestions"' in body

    def test_chip_min_tap_target(self, client_full, test_engine):
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s)
            _seed_weekly_cron(s, last_tick_at_iso=None)
        resp = client_full.get("/vibes")
        body = resp.text
        # The chip partial uses min-h-11 (44px) somewhere — check the chip
        # markup is present.
        assert "min-h-11" in body

    def test_chip_handles_missing_weekly_cron_state_row(self, client_full, test_engine):
        """Pre-bootstrap edge case — WeeklyCronState row absent."""
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            _seed_baseline(s)
            # Do NOT seed WeeklyCronState — simulate the pre-bootstrap state.
        resp = client_full.get("/vibes")
        assert resp.status_code == 200
        body = resp.text
        # Falls back to the "first weekly tick" message without crashing.
        # Pre-first-tick message now renders the next Sun 03:00 UTC tick as
        # LA-localized "Saturday May 17 at 8:00 PM PDT" (or PST in winter).
        # DST-aware via zoneinfo. Pin the prefix + the PT zone marker.
        # Pre-first-tick chip now renders cost (even $0.00) + "first refresh"
        # context (LA-localized via next_sunday_03_utc_in_la, DST-aware).
        assert "first refresh" in body
        assert ("PDT" in body or "PST" in body)

    def test_chip_handles_missing_baseline_row(self, client_full, test_engine):
        """Pre-bootstrap edge case — CostMeterBaseline row absent."""
        with Session(test_engine) as s:
            _seed_plex_configured_and_vibe(s)
            # Do NOT seed CostMeterBaseline.
            _seed_weekly_cron(s, last_tick_at_iso=None)
        resp = client_full.get("/vibes")
        assert resp.status_code == 200


# ============================================================================
# Template structure
# ============================================================================


class TestHomeChipIncludeInVibesHome:
    def test_chip_includes_partial_from_vibes_home(self):
        path = TEMPLATES_DIR / "pages" / "vibes_home.html"
        text = path.read_text()
        assert 'include "partials/llm_cost_chip.html"' in text or (
            "llm_cost_chip.html" in text and "include" in text
        ), "vibes_home.html must include partials/llm_cost_chip.html"

    def test_chip_partial_exists_with_required_strings(self):
        path = TEMPLATES_DIR / "partials" / "llm_cost_chip.html"
        assert path.exists(), "llm_cost_chip.html partial must exist"
        text = path.read_text()
        assert "This week:" in text
        # The "next tick" string is now computed in PT by the router context;
        # the partial just renders the pre-formatted variable.
        # Partial now renders next-tick info on the "first refresh" branch
        # (post-UAT chip restructure that always shows cost regardless of tick state).
        assert "first refresh" in text
        assert "{{ next_tick_local_str }}" in text
        assert 'href="/debug/suggestions"' in text
        assert "min-h-11" in text
