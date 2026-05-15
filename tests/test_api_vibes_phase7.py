"""Phase 7 Plan 02 Task 3 — SUGG-10 vibe coverage CTA endpoint + progress
endpoint extension.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def client_phase7(test_engine) -> Generator[TestClient, None, None]:
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
def reset_breaker():
    try:
        from app.services import llm_cost_breaker
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus()
    except (ImportError, AttributeError):
        pass
    yield


def _seed_vibe(session, name="V1") -> int:
    from app.models.vibe import Vibe

    v = Vibe(
        name=name, description="Test",
        centroid_energy=0.5, centroid_tempo=120.0,
        centroid_danceability=0.6, centroid_valence=0.7,
        spread_energy=0.2, spread_tempo=30.0,
        spread_danceability=0.2, spread_valence=0.2,
        created_at=datetime.now(timezone.utc).isoformat(),
        is_active=True,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return v.id


class TestFindVibeCandidatesEndpoint:
    def test_runs_targeted_refill(self, client_phase7, test_engine):
        """Phase 7.1: the endpoint now kicks ``refill_mirror_sql`` (the
        per-vibe LLM partition was deleted per D-D1 — D-B1 proportional
        allocation across all active vibes replaces it).
        """
        from app.services import suggestions_service
        from app.services.suggestions_service import RefillResult

        with Session(test_engine) as s:
            vid = _seed_vibe(s, name="Coastal")

        mock_refill = AsyncMock(return_value=RefillResult(picks_made=15))
        with patch.object(
            suggestions_service, "refill_mirror_sql", new=mock_refill,
        ):
            resp = client_phase7.post(f"/api/vibes/{vid}/find-candidates")

        assert resp.status_code == 200
        mock_refill.assert_awaited_once()
        # Awaited with target=15 (Phase 7.1 — vibe_id no longer passed).
        call = mock_refill.await_args
        assert call.kwargs.get("target", None) == 15 or 15 in call.args

    def test_invalid_vibe_404(self, client_phase7):
        resp = client_phase7.post("/api/vibes/9999/find-candidates")
        assert resp.status_code == 404

    def test_only_fires_on_post(self, client_phase7):
        # GET is not allowed (CTA-only on explicit POST).
        resp = client_phase7.get("/api/vibes/1/find-candidates")
        assert resp.status_code == 405

    def test_response_renders_autostart_progress_card(
        self, client_phase7, test_engine,
    ):
        """CR-03 fix: the response is the progress card with autostart=true so
        Alpine begins polling on swap-in (the htmx:beforeRequest listener has
        already fired by the time the partial reaches the DOM).
        """
        from app.services import suggestions_service
        from app.services.suggestions_service import RefillResult

        with Session(test_engine) as s:
            vid = _seed_vibe(s, name="V-cr03")

        mock_refill = AsyncMock(return_value=RefillResult(picks_made=15))
        with patch.object(
            suggestions_service, "refill_mirror_sql", new=mock_refill,
        ):
            resp = client_phase7.post(f"/api/vibes/{vid}/find-candidates")

        assert resp.status_code == 200
        body = resp.text
        # The Alpine factory MUST be invoked with `true` so the card's
        # init() autostarts polling. (`llmProgressCard(false)` is the
        # server-rendered empty-state path on /suggestions — different.)
        assert "llmProgressCard(true)" in body, (
            "CR-03 — find-candidates response must render "
            "llmProgressCard(true) so the progress card auto-starts polling."
        )

    def test_fires_refill_as_background_task(
        self, client_phase7, test_engine,
    ):
        """CR-03 fix: the endpoint MUST NOT block on the LLM round-trip. The
        response should return quickly (< 1s) even when the refill coroutine
        is slow. We simulate a 2s refill and assert the response returns
        before the refill completes — the response-time gap is the contract.
        """
        import time as _time
        from app.services import suggestions_service
        from app.services.suggestions_service import RefillResult

        with Session(test_engine) as s:
            vid = _seed_vibe(s, name="V-slow")

        async def slow_refill(*args, **kwargs):
            import asyncio
            await asyncio.sleep(0.5)
            return RefillResult(picks_made=15)

        with patch.object(
            suggestions_service, "refill_mirror_sql",
            new=slow_refill,
        ):
            t0 = _time.monotonic()
            resp = client_phase7.post(f"/api/vibes/{vid}/find-candidates")
            elapsed = _time.monotonic() - t0

        assert resp.status_code == 200
        # The handler must return BEFORE the 0.5s refill completes — fire-and
        # -forget contract. If we ever go back to awaiting the refill the
        # elapsed time would be ≥ 0.5s; the threshold below is generous.
        assert elapsed < 0.4, (
            f"CR-03 — find-candidates must fire-and-forget the refill; "
            f"response took {elapsed:.3f}s (expected < 0.4s)."
        )


class TestProgressEndpointMatchesSuggestionsPurposes:
    def test_progress_matches_suggestions_rank(self, client_phase7, test_engine):
        from app.models.llm_usage import LLMUsage

        # Seed one vibe_* row + one suggestions_rank row; suggestions_rank
        # is newer → must be returned by the progress endpoint.
        with Session(test_engine) as s:
            s.add(LLMUsage(
                called_at="2026-05-13T00:00:00+00:00",
                purpose="vibe_clustering_initial",
                model="claude-sonnet-4-6",
                input_tokens=100,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                output_tokens=50,
                cost_estimate_usd=0.001,
            ))
            s.add(LLMUsage(
                called_at="2026-05-14T00:00:00+00:00",
                purpose="suggestions_rank",
                model="claude-sonnet-4-6",
                input_tokens=100,
                cache_creation_input_tokens=2500,
                cache_read_input_tokens=0,
                output_tokens=50,
                cost_estimate_usd=0.01,
            ))
            s.commit()
        resp = client_phase7.get("/api/vibes/last-llm-call/progress")
        assert resp.status_code == 200
        data = resp.json()
        # The newest call is suggestions_rank — endpoint MUST return it.
        assert data["purpose"] == "suggestions_rank"


# Phase 7.1 D-D1: TestRefillSuggestionsForVibeContract was deleted. The
# per-vibe LLM entry point it pinned was removed; the
# /api/vibes/{vibe_id}/find-candidates CTA now kicks ``refill_mirror_sql``
# directly (see TestFindVibeCandidatesEndpoint above). The SQL-driven
# refill contract lives in tests/test_suggestions_service.py
# ::TestRefillMirrorSql.
