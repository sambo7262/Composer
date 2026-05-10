"""Phase 6 Plan 03 wizard router tests (D-06, D-07, D-08, D-13, D-25;
WIZ-01..07).

Endpoints under test:
- POST /api/setup/step1/submit  (D-06 — conditional skip when webhook configured)
- GET  /api/setup/rated-count
- POST /api/setup/step2/submit
- POST /api/setup/propose/init  (D-13 force-k picker; idempotent)
- POST /api/setup/refine        (D-04 turn cap; D-05 Annotated[str, Form()])
- POST /api/setup/finalize      (D-25 sequential semaphore=1)
- POST /api/setup/reset
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def client_with_phase6(test_engine) -> Generator[TestClient, None, None]:
    """TestClient with Phase 5 + Phase 6 tables registered."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        SetupState,
        TrackVibe,
        Vibe,
    )

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def reset_finalize_status():
    """Reset api_setup._finalize_status between tests."""
    try:
        from app.routers import api_setup
        api_setup._finalize_status = api_setup.FinalizeStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from app.routers import api_setup
        api_setup._finalize_status = api_setup.FinalizeStatus()
    except (ImportError, AttributeError):
        pass


def _seed_rated_tracks(session: Session, n: int) -> None:
    from app.models.track import Track

    for i in range(n):
        session.add(
            Track(
                plex_rating_key=str(1000 + i),
                title=f"Title {i}",
                artist=f"Artist {i % 5}",
                album=f"Album {i % 7}",
                user_rating=8.0,
                energy=0.5,
                tempo=120.0,
                danceability=0.5,
                valence=0.5,
            )
        )
    session.commit()


def _make_proposal_set_json(num_proposals: int = 3) -> str:
    """Build a canned VibeProposalSet JSON for tests."""
    proposals = []
    for i in range(num_proposals):
        proposals.append(
            {
                "name": f"Vibe {i}",
                "description": f"Description {i}",
                "action": "new",
                "source_vibe_ids": [],
                "seed_track_indices": [i * 2, i * 2 + 1],
                "centroid": {
                    "energy": 0.5 + i * 0.1,
                    "tempo": 100.0 + i * 20.0,
                    "danceability": 0.5,
                    "valence": 0.5,
                },
                "spread": {
                    "energy": 0.1,
                    "tempo": 10.0,
                    "danceability": 0.1,
                    "valence": 0.1,
                },
                "silhouette": 0.5,
            }
        )
    payload = {
        "proposals": proposals,
        "rated_track_count": 60,
        "silhouette_avg": 0.5,
        "forced_k": None,
        "degraded_mode": False,
        "rated_track_index_map": [
            {
                "index": idx,
                "rating_key": str(1000 + idx),
                "title": f"Title {idx}",
                "artist": f"Artist {idx % 5}",
            }
            for idx in range(60)
        ],
    }
    return json.dumps(payload)


def _seed_setup_state(session: Session, **kwargs) -> None:
    from app.models.vibe import SetupState

    defaults = {
        "id": 1,
        "step": "rating_source",
        "draft_proposals_json": "",
        "refinement_turn_count": 0,
        "last_llm_call_id": None,
    }
    defaults.update(kwargs)
    state = SetupState(**defaults)
    session.merge(state)
    session.commit()


# ---------------------------------------------------------------------------
# Test 4: step1/submit advances to webhook when unconfigured
# ---------------------------------------------------------------------------
def test_post_setup_step1_submit_advances_to_webhook_when_unconfigured(
    client_with_phase6, test_engine
):
    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)

    response = client_with_phase6.post("/api/setup/step1/submit")
    assert response.status_code == 204
    assert response.headers.get("hx-redirect") == "/setup/webhook"

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.step == "webhook"


# ---------------------------------------------------------------------------
# Test 5: step1/submit skips webhook when already configured
# ---------------------------------------------------------------------------
def test_post_setup_step1_submit_skips_webhook_when_already_configured(
    client_with_phase6, test_engine
):
    from app.services.settings_service import save_setting

    with Session(test_engine) as session:
        save_setting(session, "webhook", "http://example.com/hook", "")
        _seed_rated_tracks(session, 60)

    response = client_with_phase6.post("/api/setup/step1/submit")
    assert response.status_code == 204
    assert response.headers.get("hx-redirect") == "/setup/propose"

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.step == "proposing"


# ---------------------------------------------------------------------------
# Test 6: step2/submit advances to proposing
# ---------------------------------------------------------------------------
def test_post_setup_step2_submit_advances_to_proposing(
    client_with_phase6, test_engine
):
    with Session(test_engine) as session:
        _seed_setup_state(session, step="webhook")

    response = client_with_phase6.post("/api/setup/step2/submit")
    assert response.status_code == 204
    assert response.headers.get("hx-redirect") == "/setup/propose"

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.step == "proposing"


# ---------------------------------------------------------------------------
# Test 7 (Phase 6.1 D-NEW-01): propose/init calls map_user_vibes_to_clusters
# with the user-typed names from the textbox-stack form.
# ---------------------------------------------------------------------------
def test_post_setup_propose_init_calls_map_user_vibes_to_clusters(
    client_with_phase6, test_engine, monkeypatch
):
    """Phase 6.1 supersedes Phase 6 D-13. propose/init now accepts a
    ``vibe_names`` Form field (JSON-array string per D-05) and dispatches to
    ``map_user_vibes_to_clusters`` from Plan 01 — NOT
    ``initial_cluster_proposal``.
    """
    from app.routers import api_setup
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet

    canned = VibeProposalSet(
        proposals=[
            VibeProposal(
                name="MyVibeAlpha",
                description="Test description alpha",
                action="new",
                seed_track_indices=[0, 1],
                centroid={"energy": 0.5, "tempo": 120.0,
                          "danceability": 0.5, "valence": 0.5},
                spread={"energy": 0.1, "tempo": 10.0,
                        "danceability": 0.1, "valence": 0.1},
                silhouette=0.5,
                seed_tracks=[], members=[], member_count=2,
                fit="strong",
            ),
            VibeProposal(
                name="MyVibeBeta", description="d", action="new",
                seed_track_indices=[2, 3], fit="weak",
                seed_tracks=[], members=[], member_count=2,
            ),
            VibeProposal(
                name="MyVibeGamma", description="d", action="new",
                seed_track_indices=[4, 5], fit="strong",
                seed_tracks=[], members=[], member_count=2,
            ),
        ],
        rated_track_count=60,
        silhouette_avg=0.5,
        rated_track_index_map=[
            {"index": i, "rating_key": str(1000 + i),
             "title": f"T{i}", "artist": "A"}
            for i in range(60)
        ],
    )

    called_with = []

    async def fake_map(names):
        called_with.append(list(names))
        return canned

    monkeypatch.setattr(api_setup, "map_user_vibes_to_clusters", fake_map)

    # Seed an LLMUsage row so last_llm_call_id is non-None.
    with Session(test_engine) as session:
        from app.models.llm_usage import LLMUsage
        session.add(
            LLMUsage(
                called_at=datetime.now(timezone.utc).isoformat(),
                purpose="vibe_clustering_user_led",
                model="claude-sonnet",
            )
        )
        session.commit()

    response = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["MyVibeAlpha","MyVibeBeta","MyVibeGamma"]'},
    )
    assert response.status_code == 200, response.text
    assert called_with == [["MyVibeAlpha", "MyVibeBeta", "MyVibeGamma"]]
    assert "MyVibeAlpha" in response.text

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.draft_proposals_json
        decoded = VibeProposalSet.model_validate_json(state.draft_proposals_json)
        assert decoded.proposals[0].name == "MyVibeAlpha"
        assert state.refinement_turn_count == 0
        assert state.last_llm_call_id is not None


# ---------------------------------------------------------------------------
# Test 8: refine calls refine_proposals + increments counter
# ---------------------------------------------------------------------------
def test_post_setup_refine_calls_refine_proposals_increments_counter(
    client_with_phase6, test_engine, monkeypatch
):
    from app.routers import api_setup
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet

    with Session(test_engine) as session:
        _seed_setup_state(
            session,
            step="proposing",
            draft_proposals_json=_make_proposal_set_json(3),
            refinement_turn_count=0,
        )

    captured: dict = {}

    async def fake_refine(prior, message, recluster_mode=False):
        captured["prior_count"] = len(prior.proposals)
        captured["message"] = message
        captured["recluster_mode"] = recluster_mode
        return VibeProposalSet(
            proposals=[
                VibeProposal(
                    name="MergedVibeBeta",
                    description="merged",
                    action="merged_from",
                    source_vibe_ids=[],
                    seed_track_indices=[0, 1, 2, 3],
                    centroid={"energy": 0.5, "tempo": 120.0, "danceability": 0.5, "valence": 0.5},
                    spread={"energy": 0.1, "tempo": 10.0, "danceability": 0.1, "valence": 0.1},
                    silhouette=0.6,
                ),
            ],
            rated_track_count=60,
            silhouette_avg=0.6,
            rated_track_index_map=prior.rated_track_index_map,
        )

    monkeypatch.setattr(api_setup, "refine_proposals", fake_refine)

    response = client_with_phase6.post(
        "/api/setup/refine",
        data={"message": "merge X and Y"},
    )
    assert response.status_code == 200, response.text
    assert captured["message"] == "merge X and Y"
    assert captured["recluster_mode"] is False
    assert captured["prior_count"] == 3
    assert "MergedVibeBeta" in response.text

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.refinement_turn_count == 1
        decoded = VibeProposalSet.model_validate_json(state.draft_proposals_json)
        assert decoded.proposals[0].name == "MergedVibeBeta"


# ---------------------------------------------------------------------------
# Test 9: refine caps at 10 turns
# ---------------------------------------------------------------------------
def test_post_setup_refine_caps_at_10_turns(
    client_with_phase6, test_engine, monkeypatch
):
    from app.routers import api_setup

    with Session(test_engine) as session:
        _seed_setup_state(
            session,
            step="proposing",
            draft_proposals_json=_make_proposal_set_json(3),
            refinement_turn_count=10,
        )

    call_count = {"n": 0}

    async def fake_refine(prior, message, recluster_mode=False):
        call_count["n"] += 1
        raise AssertionError("refine_proposals must NOT be called past cap")

    monkeypatch.setattr(api_setup, "refine_proposals", fake_refine)

    response = client_with_phase6.post(
        "/api/setup/refine",
        data={"message": "do something"},
    )
    assert response.status_code == 200
    assert call_count["n"] == 0
    assert "Refinement 10 of 10" in response.text
    assert "save what you have or start over" in response.text


# ---------------------------------------------------------------------------
# Test 10: finalize creates Vibe + ManagedPlaylist + TrackVibe
# ---------------------------------------------------------------------------
def test_post_setup_finalize_creates_vibes_and_playlists(
    client_with_phase6, test_engine, monkeypatch
):
    from app.routers import api_setup
    from app.services.settings_service import save_setting

    with Session(test_engine) as session:
        save_setting(session, "plex", "http://plex.local", "fake-token")
        _seed_rated_tracks(session, 60)
        _seed_setup_state(
            session,
            step="proposing",
            draft_proposals_json=_make_proposal_set_json(3),
            refinement_turn_count=2,
        )

    create_calls: list = []

    async def fake_create_playlist(plex_url, plex_token, name, rating_keys, vibe_id=None):
        create_calls.append({"name": name, "rk_count": len(rating_keys), "vibe_id": vibe_id})
        # Persist the ManagedPlaylist row as the real implementation would.
        from app.models.vibe import ManagedPlaylist
        new_key = f"playlist-{len(create_calls)}"
        with Session(test_engine) as session:
            session.add(
                ManagedPlaylist(
                    kind="vibe",
                    vibe_id=vibe_id,
                    plex_rating_key=new_key,
                    composer_name=name,
                    track_count=len(rating_keys),
                )
            )
            session.commit()
        return new_key

    monkeypatch.setattr(api_setup, "create_playlist", fake_create_playlist)

    # Phase 6.1 Blocker #7 Option A: finalize now AWAITS reslot_all_rated_tracks
    # after the success branch. Mock it here so tests don't hit real Plex via
    # slot_track. The synchronous-reslot semantics are tested separately in
    # test_finalize_synchronously_awaits_reslot_all_rated_tracks below.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        api_setup, "reslot_all_rated_tracks", AsyncMock(return_value=0)
    )

    response = client_with_phase6.post("/api/setup/finalize")
    assert response.status_code == 200, response.text
    assert len(create_calls) == 3
    for call in create_calls:
        assert call["name"].startswith("Composer · "), call

    from app.models.vibe import ManagedPlaylist, SetupState, TrackVibe, Vibe
    with Session(test_engine) as session:
        vibes = session.exec(select(Vibe)).all()
        assert len(vibes) == 3
        managed = session.exec(select(ManagedPlaylist)).all()
        assert len(managed) == 3
        # Each ManagedPlaylist should have a vibe_id linked.
        for mp in managed:
            assert mp.vibe_id is not None
        track_vibes = session.exec(select(TrackVibe)).all()
        # 3 proposals × 2 seed tracks = 6 (at minimum)
        assert len(track_vibes) >= 6
        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.step == "done"
        assert state.completed_at is not None


# ---------------------------------------------------------------------------
# Test 11: finalize skips dropped proposals
# ---------------------------------------------------------------------------
def test_post_setup_finalize_skips_dropped_proposals(
    client_with_phase6, test_engine, monkeypatch
):
    from app.routers import api_setup
    from app.services.settings_service import save_setting

    payload = json.loads(_make_proposal_set_json(4))
    payload["proposals"][2]["action"] = "dropped"
    proposals_json = json.dumps(payload)

    with Session(test_engine) as session:
        save_setting(session, "plex", "http://plex.local", "fake-token")
        _seed_rated_tracks(session, 60)
        _seed_setup_state(
            session,
            step="proposing",
            draft_proposals_json=proposals_json,
            refinement_turn_count=2,
        )

    create_calls: list = []

    async def fake_create_playlist(plex_url, plex_token, name, rating_keys, vibe_id=None):
        create_calls.append(name)
        from app.models.vibe import ManagedPlaylist
        new_key = f"playlist-{len(create_calls)}"
        with Session(test_engine) as session:
            session.add(
                ManagedPlaylist(
                    kind="vibe",
                    vibe_id=vibe_id,
                    plex_rating_key=new_key,
                    composer_name=name,
                    track_count=len(rating_keys),
                )
            )
            session.commit()
        return new_key

    monkeypatch.setattr(api_setup, "create_playlist", fake_create_playlist)

    # Phase 6.1 Blocker #7 Option A: finalize now AWAITS reslot_all_rated_tracks
    # after the success branch — mock to avoid real Plex slot_track calls.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        api_setup, "reslot_all_rated_tracks", AsyncMock(return_value=0)
    )

    response = client_with_phase6.post("/api/setup/finalize")
    assert response.status_code == 200, response.text
    assert len(create_calls) == 3
    from app.models.vibe import Vibe
    with Session(test_engine) as session:
        vibes = session.exec(select(Vibe)).all()
        assert len(vibes) == 3


# ---------------------------------------------------------------------------
# Test 12: reset clears state
# ---------------------------------------------------------------------------
def test_post_setup_reset_clears_state(client_with_phase6, test_engine):
    with Session(test_engine) as session:
        _seed_setup_state(
            session,
            step="confirming",
            draft_proposals_json=_make_proposal_set_json(3),
            refinement_turn_count=5,
        )

    response = client_with_phase6.post("/api/setup/reset")
    assert response.status_code == 204
    assert response.headers.get("hx-redirect") == "/setup"

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.step == "rating_source"
        assert state.draft_proposals_json == ""
        assert state.refinement_turn_count == 0


# ---------------------------------------------------------------------------
# Test 13: rated-count returns partial
# ---------------------------------------------------------------------------
def test_get_setup_rated_count_returns_partial(client_with_phase6, test_engine):
    with Session(test_engine) as session:
        _seed_rated_tracks(session, 42)

    response = client_with_phase6.get("/api/setup/rated-count")
    assert response.status_code == 200
    assert "42 rated tracks" in response.text


# ===========================================================================
# Phase 6.1 propose_init tests (Blocker #3 — use existing client_with_phase6
# + inline _seed_setup_state).
# ===========================================================================

def test_setup_step3_initial_render_shows_textbox_stack(
    client_with_phase6, test_engine,
):
    """Phase 6.1 D-NEW-06: GET /setup/propose renders the textbox-stack form
    on initial load (no draft proposals)."""
    with Session(test_engine) as session:
        _seed_setup_state(session, step="proposing")

    r = client_with_phase6.get("/setup/propose")
    assert r.status_code == 200
    body = r.text
    assert 'name="vibe_names"' in body
    assert "Cluster my library" in body
    assert "+ Add another" in body
    # No auto-fire to /api/setup/propose/init on initial render.
    assert 'hx-trigger="load"' not in body
    # Phase 6 UI-SPEC invariant.
    assert "text-[15px]" not in body
    # Touch target.
    assert "min-h-11" in body


def test_propose_init_rejects_count_below_3(
    client_with_phase6, test_engine,
):
    """D-NEW-07: 2 names → 200 + refine_error with "3 to 7" message."""
    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["workout", "focus"]'},
    )
    assert r.status_code == 200
    assert "3 to 7" in r.text


def test_propose_init_rejects_count_above_7(
    client_with_phase6, test_engine,
):
    """D-NEW-07: 8 names → 200 + refine_error with "3 to 7" message."""
    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["a","b","c","d","e","f","g","h"]'},
    )
    assert r.status_code == 200
    assert "3 to 7" in r.text


def test_propose_init_rejects_case_insensitive_duplicates(
    client_with_phase6, test_engine,
):
    """D-NEW-07: duplicates (case-insensitive) rejected → "Duplicate" message."""
    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["Workout", "workout", "focus"]'},
    )
    assert r.status_code == 200
    assert "Duplicate" in r.text or "duplicate" in r.text


def test_propose_init_trims_blanks_before_count_check(
    client_with_phase6, test_engine,
):
    """D-NEW-07: blank strings trimmed BEFORE count check.
    ["","workout","focus"] -> 2 effective names → "3 to 7" error.
    """
    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["", "workout", "focus"]'},
    )
    assert r.status_code == 200
    assert "3 to 7" in r.text


def test_propose_init_happy_path_calls_map_user_vibes(
    client_with_phase6, test_engine, monkeypatch,
):
    """D-NEW-01: valid 3 names → map_user_vibes_to_clusters called exactly once
    with the trimmed list; SetupState.draft_proposals_json populated;
    last_llm_call_id set; response body contains proposal cards.
    """
    from app.routers import api_setup
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet

    called_with = []

    async def fake_map(names):
        called_with.append(list(names))
        return VibeProposalSet(
            proposals=[
                VibeProposal(
                    name="workout", description="d", action="new",
                    seed_track_indices=[0, 1], fit="strong",
                    seed_tracks=[{"title": "T0", "artist": "A0",
                                  "rating_key": "rk_0"}],
                    members=[{"title": "T0", "artist": "A0",
                              "rating_key": "rk_0"}],
                    member_count=1,
                ),
                VibeProposal(
                    name="focus", description="d", action="new",
                    seed_track_indices=[2, 3], fit="weak",
                    seed_tracks=[], members=[], member_count=0,
                ),
                VibeProposal(
                    name="late night", description="d", action="new",
                    seed_track_indices=[4, 5], fit="strong",
                    seed_tracks=[], members=[], member_count=0,
                ),
            ],
            rated_track_count=6,
            forced_k=3,
            rated_track_index_map=[
                {"index": i, "rating_key": str(1000 + i),
                 "title": f"T{i}", "artist": "A"} for i in range(6)
            ],
        )

    monkeypatch.setattr(api_setup, "map_user_vibes_to_clusters", fake_map)

    # Seed an LLMUsage row so last_llm_call_id is non-None.
    with Session(test_engine) as session:
        from app.models.llm_usage import LLMUsage
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")
        session.add(LLMUsage(
            called_at=datetime.now(timezone.utc).isoformat(),
            purpose="vibe_clustering_user_led",
            model="claude-sonnet",
        ))
        session.commit()

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["workout", "focus", "late night"]'},
    )
    assert r.status_code == 200, r.text
    assert called_with == [["workout", "focus", "late night"]]

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(
            select(SetupState).where(SetupState.id == 1)
        ).first()
        assert state.draft_proposals_json
        assert state.last_llm_call_id is not None


# ===========================================================================
# Phase 6.1 finalize tests (Blocker #7 Option A — synchronous reslot).
# ===========================================================================

def test_finalize_synchronously_awaits_reslot_all_rated_tracks(
    client_with_phase6, test_engine, monkeypatch,
):
    """Blocker #7 Option A — finalize MUST await reslot_all_rated_tracks
    before returning. reslot is called exactly once during the HTTP request
    (synchronous; not fire-and-forget).
    """
    from app.routers import api_setup
    from unittest.mock import AsyncMock
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet
    from app.services.settings_service import save_setting

    # Mock the reslot shim — captures await order.
    reslot_mock = AsyncMock(return_value=42)
    monkeypatch.setattr(api_setup, "reslot_all_rated_tracks", reslot_mock)

    # Mock create_playlist so finalize doesn't hit Plex.
    create_calls: list = []

    async def fake_create(plex_url, plex_token, name, member_keys, vibe_id):
        idx = len(create_calls) + 1
        create_calls.append({"vibe_id": vibe_id, "name": name})
        from app.models.vibe import ManagedPlaylist
        new_key = f"fakerk_{idx}"
        with Session(test_engine) as session:
            session.add(
                ManagedPlaylist(
                    kind="vibe", vibe_id=vibe_id,
                    plex_rating_key=new_key, composer_name=name,
                    track_count=len(member_keys),
                )
            )
            session.commit()
        return new_key

    monkeypatch.setattr(api_setup, "create_playlist", fake_create)

    # Seed: rated tracks + Plex settings + SetupState.draft_proposals_json
    # with 2 proposals.
    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        save_setting(session, "plex", "http://localhost:32400", "fake-token")

        proposals = VibeProposalSet(
            proposals=[
                VibeProposal(
                    name="A", description="d", action="new",
                    seed_track_indices=[0, 1, 2],
                    centroid={"energy": 0.5, "tempo": 120,
                              "danceability": 0.5, "valence": 0.5},
                    spread={"energy": 0.1, "tempo": 10.0,
                            "danceability": 0.1, "valence": 0.1},
                    silhouette=0.4,
                    seed_tracks=[], members=[], member_count=3,
                    fit="strong",
                ),
                VibeProposal(
                    name="B", description="d", action="new",
                    seed_track_indices=[3, 4, 5],
                    centroid={"energy": 0.6, "tempo": 130,
                              "danceability": 0.6, "valence": 0.6},
                    spread={"energy": 0.1, "tempo": 10.0,
                            "danceability": 0.1, "valence": 0.1},
                    silhouette=0.4,
                    seed_tracks=[], members=[], member_count=3,
                    fit="weak",
                ),
            ],
            rated_track_count=60,
            forced_k=2,
            rated_track_index_map=[
                {"index": i, "rating_key": str(1000 + i),
                 "title": f"T{i}", "artist": "A"} for i in range(60)
            ],
        )
        _seed_setup_state(
            session, step="confirming",
            draft_proposals_json=proposals.model_dump_json(),
        )

    # POST finalize — synchronous, returns ONLY after reslot completes.
    r = client_with_phase6.post("/api/setup/finalize")

    # By the time the response returns, reslot has been awaited exactly once.
    assert reslot_mock.await_count == 1, (
        f"reslot_all_rated_tracks must be awaited synchronously by "
        f"finalize (Blocker #7 Option A); await_count={reslot_mock.await_count}"
    )
    assert r.status_code in (200, 201, 204), r.text


def test_finalize_does_not_call_reslot_on_partial_failure(
    client_with_phase6, test_engine, monkeypatch,
):
    """If create_playlist raises mid-loop, finalize bails out before the
    reslot await — assert reslot was NOT called.
    """
    from app.routers import api_setup
    from unittest.mock import AsyncMock
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet
    from app.services.settings_service import save_setting

    reslot_mock = AsyncMock(return_value=42)
    monkeypatch.setattr(api_setup, "reslot_all_rated_tracks", reslot_mock)

    call_n = {"n": 0}

    async def fake_create_one_then_fail(plex_url, plex_token, name,
                                        member_keys, vibe_id):
        call_n["n"] += 1
        if call_n["n"] == 1:
            from app.models.vibe import ManagedPlaylist
            new_key = f"fakerk_{vibe_id}"
            with Session(test_engine) as session:
                session.add(
                    ManagedPlaylist(
                        kind="vibe", vibe_id=vibe_id,
                        plex_rating_key=new_key, composer_name=name,
                        track_count=len(member_keys),
                    )
                )
                session.commit()
            return new_key
        raise RuntimeError("Plex unavailable")

    monkeypatch.setattr(
        api_setup, "create_playlist", fake_create_one_then_fail
    )

    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        save_setting(session, "plex", "http://localhost:32400", "fake-token")
        proposals = VibeProposalSet(
            proposals=[
                VibeProposal(name="A", description="d", action="new",
                             seed_track_indices=[0, 1],
                             seed_tracks=[], members=[], member_count=2),
                VibeProposal(name="B", description="d", action="new",
                             seed_track_indices=[2, 3],
                             seed_tracks=[], members=[], member_count=2),
            ],
            rated_track_count=60, forced_k=2,
            rated_track_index_map=[
                {"index": i, "rating_key": str(1000 + i),
                 "title": f"T{i}", "artist": "A"} for i in range(60)
            ],
        )
        _seed_setup_state(
            session, step="confirming",
            draft_proposals_json=proposals.model_dump_json(),
        )

    client_with_phase6.post("/api/setup/finalize")
    # Reslot NOT called — finalize bailed out before the success branch.
    assert reslot_mock.await_count == 0
