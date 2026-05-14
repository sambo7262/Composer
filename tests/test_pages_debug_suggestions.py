"""Phase 7 Plan 03 Task 3 — /debug index + /debug/suggestions + settings link.

DEBUG-05 — /debug index linking to /debug/events, /debug/vibes,
            /debug/suggestions (plain HTML, no JS-only content).
DEBUG-03 — /debug/suggestions surfaces:
            - current SuggestionsMirror contents (track + vibe + score + rationale)
            - last 20 RefillTriggerLog rows
            - last 20 LLMUsage rows with purpose LIKE 'suggestions_%'
            - current cost breaker state (in-process singleton)
            - recent NegativeSignal rows
DEBUG-05 — settings footer links to /debug.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_track(session, *, plex_rating_key="rk-1", title="T1", artist="A1"):
    from app.models.track import Track
    t = Track(plex_rating_key=plex_rating_key, title=title, artist=artist)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_mirror(session, *, track_id, position, rationale=None, score=None):
    from app.models.suggestions import SuggestionsMirror
    row = SuggestionsMirror(
        track_id=track_id,
        position=position,
        added_at=datetime.now(timezone.utc).isoformat(),
        rationale=rationale,
        score=score,
    )
    session.add(row)
    session.commit()
    return row


def _seed_refill_log(session, *, triggered_at, source="track_played",
                     candidates_evaluated=10, picks_made=2, latency_ms=500,
                     cost_estimate_usd=0.001, breaker_tripped=False):
    from app.models.suggestions import RefillTriggerLog
    row = RefillTriggerLog(
        triggered_at=triggered_at,
        event_source=source,
        candidates_evaluated=candidates_evaluated,
        picks_made=picks_made,
        latency_ms=latency_ms,
        cost_estimate_usd=cost_estimate_usd,
        breaker_tripped=breaker_tripped,
    )
    session.add(row)
    session.commit()
    return row


def _seed_llmusage(session, *, called_at, purpose="suggestions_rank",
                   cost_estimate_usd=0.002):
    from app.models.llm_usage import LLMUsage
    row = LLMUsage(
        called_at=called_at,
        purpose=purpose,
        model="claude-sonnet-4-6",
        input_tokens=100,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=50,
        cost_estimate_usd=cost_estimate_usd,
    )
    session.add(row)
    session.commit()
    return row


def _seed_negative(session, *, signal_type="hard_track", track_id=None,
                   artist=None):
    from app.models.suggestions import NegativeSignal
    row = NegativeSignal(
        track_id=track_id,
        artist=artist,
        signal_type=signal_type,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------------------
# /debug index (DEBUG-05)
# ---------------------------------------------------------------------------


class TestDebugIndex:
    def test_renders_links_to_three_surfaces(self, client_full):
        resp = client_full.get("/debug")
        assert resp.status_code == 200
        body = resp.text
        assert '<a href="/debug/events"' in body
        assert '<a href="/debug/vibes"' in body
        assert '<a href="/debug/suggestions"' in body

    def test_is_plain_html_no_inline_script_in_content(self, client_full):
        """DEBUG-05: 'Plain HTML, copy-friendly'. The rendered page should
        not embed a <script> tag in the content area. base.html ships
        Alpine.js etc in the <head>; we only assert the index page itself
        adds no inline <script>.

        We bound the assertion to the slice between the <main> tags so the
        head-script assets in base.html don't trip the assertion.
        """
        resp = client_full.get("/debug")
        body = resp.text
        # Slice between <main ...> ... </main>
        if "<main" in body and "</main>" in body:
            start = body.index("<main")
            end = body.index("</main>")
            content = body[start:end]
            assert "<script" not in content


# ---------------------------------------------------------------------------
# /debug/suggestions (DEBUG-03)
# ---------------------------------------------------------------------------


class TestDebugSuggestions:
    def test_renders_current_queue(self, client_full, test_engine):
        with Session(test_engine) as s:
            t1 = _seed_track(s, plex_rating_key="rk-A", title="Alpha", artist="X")
            t2 = _seed_track(s, plex_rating_key="rk-B", title="Beta", artist="Y")
            t3 = _seed_track(s, plex_rating_key="rk-C", title="Gamma", artist="Z")
            _seed_mirror(s, track_id=t1.id, position=0)
            _seed_mirror(s, track_id=t2.id, position=1)
            _seed_mirror(s, track_id=t3.id, position=2)
        resp = client_full.get("/debug/suggestions")
        assert resp.status_code == 200
        body = resp.text
        assert "Alpha" in body
        assert "Beta" in body
        assert "Gamma" in body
        assert body.index("Alpha") < body.index("Beta") < body.index("Gamma")

    def test_renders_last_20_refill_triggers(self, client_full, test_engine):
        with Session(test_engine) as s:
            # Seed 25 in DESC triggered_at order; the page must show the
            # most-recent 20 and must NOT show the 25th-oldest.
            for i in range(25):
                _seed_refill_log(
                    s,
                    triggered_at=f"2026-05-14T{i:02d}:00:00+00:00",
                    candidates_evaluated=i,
                )
        resp = client_full.get("/debug/suggestions")
        body = resp.text
        # The most-recent (i=24) IS shown.
        assert "2026-05-14T24:00:00+00:00" in body
        # The oldest (i=0) is NOT shown — it's the 25th most-recent.
        assert "2026-05-14T00:00:00+00:00" not in body

    def test_renders_last_20_llm_calls_filtered_by_suggestions_purpose(
        self, client_full, test_engine,
    ):
        with Session(test_engine) as s:
            for i in range(5):
                _seed_llmusage(
                    s, called_at=f"2026-05-14T{10+i:02d}:00:00+00:00",
                    purpose="suggestions_rank",
                )
            for i in range(5):
                _seed_llmusage(
                    s, called_at=f"2026-05-14T{15+i:02d}:00:00+00:00",
                    purpose="vibe_assign_pass1",
                )
        resp = client_full.get("/debug/suggestions")
        body = resp.text
        # Find the LLM-calls table section.
        assert "suggestions_rank" in body
        # vibe_assign_pass1 must NOT appear in the LLM-calls section.
        # We approximate by asserting it does not appear at all on the
        # /debug/suggestions page.
        assert "vibe_assign_pass1" not in body

    def test_renders_cost_breaker_state(self, client_full, test_engine):
        from app.services import llm_cost_breaker
        # Trip the breaker manually (mirror test_pages_settings).
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus(
            today_calls=50,
            today_cost_usd=0.42,
            last_call_at=datetime.now(timezone.utc).isoformat(),
            last_tripped_at=datetime.now(timezone.utc).isoformat(),
            last_tripped_reason="daily_quota_50",
        )
        resp = client_full.get("/debug/suggestions")
        body = resp.text
        assert "daily_quota_50" in body
        # today_calls renders as the number 50 somewhere in the cost-breaker
        # block.
        assert "50" in body

    def test_renders_recent_negativesignal_rows(self, client_full, test_engine):
        with Session(test_engine) as s:
            for i in range(5):
                _seed_negative(
                    s,
                    signal_type=f"hard_track" if i % 2 == 0 else "soft",
                    track_id=i + 1,
                    artist=None,
                )
        resp = client_full.get("/debug/suggestions")
        body = resp.text
        # All 5 signals appear.
        assert "hard_track" in body
        assert "soft" in body

    def test_copy_friendly_layout_uses_pre_or_code(self, client_full):
        """DEBUG-05 — copy-friendly layout. The diagnostic page must render
        at least one <pre> or <code> block (cost-breaker block uses <pre>).
        """
        resp = client_full.get("/debug/suggestions")
        body = resp.text
        assert "<pre" in body or "<code" in body


# ---------------------------------------------------------------------------
# Settings footer link (DEBUG-05)
# ---------------------------------------------------------------------------


class TestSettingsFooterLink:
    def test_settings_footer_links_to_debug(self, client_full):
        resp = client_full.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        # Either a direct anchor to /debug OR a labeled "View diagnostics" link.
        assert 'href="/debug"' in body
        assert "diagnostics" in body.lower()
