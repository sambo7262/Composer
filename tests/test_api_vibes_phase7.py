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
        from app.services import suggestions_service
        from app.services.suggestions_service import RefillResult

        with Session(test_engine) as s:
            vid = _seed_vibe(s, name="Coastal")

        mock_refill = AsyncMock(return_value=RefillResult(
            candidates_evaluated=15, picks_returned=15,
            picks_validated=15, picks_inserted=15,
        ))
        with patch.object(
            suggestions_service, "refill_suggestions_for_vibe", new=mock_refill,
        ):
            resp = client_phase7.post(f"/api/vibes/{vid}/find-candidates")

        assert resp.status_code == 200
        mock_refill.assert_awaited_once()
        # Awaited with vibe_id=vid AND target=15.
        call = mock_refill.await_args
        assert vid in call.args or call.kwargs.get("vibe_id") == vid
        assert call.kwargs.get("target", None) == 15 or 15 in call.args

    def test_invalid_vibe_404(self, client_phase7):
        resp = client_phase7.post("/api/vibes/9999/find-candidates")
        assert resp.status_code == 404

    def test_only_fires_on_post(self, client_phase7):
        # GET is not allowed (CTA-only on explicit POST).
        resp = client_phase7.get("/api/vibes/1/find-candidates")
        assert resp.status_code == 405


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


class TestRefillSuggestionsForVibeContract:
    def test_respects_cost_breaker(self, test_engine):
        """refill_suggestions_for_vibe returns breaker_tripped without LLM call."""
        from app.models.settings import ServiceConfig  # noqa: F401
        from app.models.track import SyncState, Track  # noqa: F401
        from app.models.event_log import EventLog  # noqa: F401
        from app.models.llm_usage import LLMUsage  # noqa: F401
        from app.models.taste_profile import TasteProfile  # noqa: F401
        from app.models.vibe import (  # noqa: F401
            ManagedPlaylist, MigrationLog, SetupState, SlotInLog,
            TrackVibe, Vibe,
        )
        from app.models.suggestions import (  # noqa: F401
            NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
        )

        SQLModel.metadata.create_all(test_engine)
        try:
            with Session(test_engine) as s:
                vid = _seed_vibe(s)
            from app.services import suggestions_service
            from app.services.llm_cost_breaker import CostBreakerTrippedError

            async def trip(*args, **kwargs):
                raise CostBreakerTrippedError("burst_5_per_60s", "until")

            import asyncio

            async def call_trip(*args, **kwargs):
                raise CostBreakerTrippedError("burst_5_per_60s", "until")

            with patch(
                "app.services.llm_cost_breaker.check_or_raise", new=call_trip,
            ):
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        suggestions_service.refill_suggestions_for_vibe(
                            vid, target=15,
                        )
                    )
                finally:
                    loop.close()
            assert result.breaker_tripped is True
            assert result.picks_inserted == 0
        finally:
            SQLModel.metadata.drop_all(test_engine)

    def test_partitions_to_single_vibe(self, test_engine):
        """W2 — every SuggestionsMirror row written has vibe_id == target_vibe_id;
        RefillTriggerLog event_source == 'vibe_coverage_cta'."""
        import asyncio
        from unittest.mock import MagicMock

        from app.models.settings import ServiceConfig  # noqa: F401
        from app.models.track import SyncState, Track  # noqa: F401
        from app.models.event_log import EventLog  # noqa: F401
        from app.models.llm_usage import LLMUsage  # noqa: F401
        from app.models.taste_profile import TasteProfile  # noqa: F401
        from app.models.vibe import (  # noqa: F401
            ManagedPlaylist, MigrationLog, SetupState, SlotInLog,
            TrackVibe, Vibe,
        )
        from app.models.suggestions import (  # noqa: F401
            NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
        )

        SQLModel.metadata.create_all(test_engine)
        try:
            from app.services import suggestions_service
            from app.services.suggestions_service import (
                SuggestionRankingPick, SuggestionRankingResponse,
            )

            with Session(test_engine) as s:
                from app.models.taste_profile import TasteProfile

                s.add(TasteProfile(
                    id=1, rated_track_count=20,
                    centroid_energy=0.5, centroid_tempo=120.0,
                    centroid_danceability=0.6, centroid_valence=0.7,
                    top_artists_json='[]', top_genres_json='[]',
                    summary_text="Test",
                    computed_at=datetime.now(timezone.utc).isoformat(),
                ))
                s.commit()

                # Seed 6 vibes.
                vibe_centers = [
                    (0.2, 80.0, 0.3, 0.4),
                    (0.4, 100.0, 0.5, 0.5),
                    (0.5, 120.0, 0.6, 0.6),
                    (0.7, 140.0, 0.7, 0.7),
                    (0.8, 160.0, 0.8, 0.8),
                    (0.6, 110.0, 0.4, 0.5),
                ]
                vibe_ids = []
                for i, (e, tmp, d, v) in enumerate(vibe_centers):
                    from app.models.vibe import Vibe
                    vv = Vibe(
                        name=f"V{i}", description="t",
                        centroid_energy=e, centroid_tempo=tmp,
                        centroid_danceability=d, centroid_valence=v,
                        spread_energy=0.15, spread_tempo=25.0,
                        spread_danceability=0.15, spread_valence=0.15,
                        created_at=datetime.now(timezone.utc).isoformat(),
                        is_active=True,
                    )
                    s.add(vv)
                    s.commit()
                    s.refresh(vv)
                    vibe_ids.append(vv.id)

                # 10 tracks per vibe.
                for vi, (e, tmp, d, v) in enumerate(vibe_centers):
                    for i in range(10):
                        s.add(Track(
                            plex_rating_key=f"v{vi}-tt{i}",
                            title=f"T{vi}{i}", artist=f"A{vi}",
                            energy=e + (i - 5) * 0.003,
                            tempo=tmp + (i - 5) * 0.3,
                            danceability=d + (i - 5) * 0.003,
                            valence=v + (i - 5) * 0.003,
                            analyzed_at=datetime.now(timezone.utc).isoformat(),
                        ))
                s.commit()

            target_vid = vibe_ids[2]

            # Echo back the first N indices.
            async def echo(*args, **kwargs):
                shortlist_size = (kwargs.get("user_prompt") or "").count("[")
                return SuggestionRankingResponse(picks=[
                    SuggestionRankingPick(
                        candidate_index=i, rationale="ok",
                    )
                    for i in range(min(shortlist_size, 15))
                ])

            with patch.object(
                suggestions_service, "_get_anthropic_client"
            ) as mock_get_client, patch.object(
                suggestions_service, "update_playlist_items",
                new=AsyncMock(return_value=None),
            ):
                client_instance = MagicMock()
                client_instance.call_with_structured_output = echo
                mock_get_client.return_value = client_instance
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        suggestions_service.refill_suggestions_for_vibe(
                            target_vid, target=15,
                        )
                    )
                finally:
                    loop.close()

            with Session(test_engine) as s:
                from app.models.suggestions import (
                    RefillTriggerLog, SuggestionsMirror,
                )

                rows = s.exec(select(SuggestionsMirror)).all()
                assert len(rows) >= 1
                assert all(r.vibe_id == target_vid for r in rows)

                logs = s.exec(select(RefillTriggerLog)).all()
                assert any(
                    l.event_source == "vibe_coverage_cta"
                    and l.target_vibe_id == target_vid
                    for l in logs
                )
        finally:
            SQLModel.metadata.drop_all(test_engine)
