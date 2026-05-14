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
# Test 7 (Phase 6.2 VIBE-13): propose/init calls assign_tracks_to_user_vibes
# (Phase 6.1's map_user_vibes_to_clusters was replaced in Phase 6.2 Plan 01).
# ---------------------------------------------------------------------------
def test_post_setup_propose_init_calls_assign_tracks_to_user_vibes(
    client_with_phase6, test_engine, monkeypatch
):
    """Phase 6.2 supersedes Phase 6.1 D-NEW-01. propose/init dispatches to
    ``assign_tracks_to_user_vibes`` (LLM-direct two-pass pipeline) instead
    of the deleted ``map_user_vibes_to_clusters``.
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

    monkeypatch.setattr(api_setup, "assign_tracks_to_user_vibes", fake_map)

    # Seed an LLMUsage row so last_llm_call_id is non-None. Phase 6.2 uses
    # purpose=vibe_assign_pass1 / vibe_assign_pass2 / vibe_definitions_preamble.
    with Session(test_engine) as session:
        from app.models.llm_usage import LLMUsage
        session.add(
            LLMUsage(
                called_at=datetime.now(timezone.utc).isoformat(),
                purpose="vibe_assign_pass1",
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


def test_propose_init_happy_path_calls_assign_tracks_to_user_vibes(
    client_with_phase6, test_engine, monkeypatch,
):
    """Phase 6.2 VIBE-13: valid 3 names → assign_tracks_to_user_vibes called
    exactly once with the trimmed list; SetupState.draft_proposals_json
    populated; last_llm_call_id set; response body contains proposal cards.
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

    monkeypatch.setattr(api_setup, "assign_tracks_to_user_vibes", fake_map)

    # Seed an LLMUsage row so last_llm_call_id is non-None. Phase 6.2 purpose.
    with Session(test_engine) as session:
        from app.models.llm_usage import LLMUsage
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")
        session.add(LLMUsage(
            called_at=datetime.now(timezone.utc).isoformat(),
            purpose="vibe_assign_pass1",
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
# Phase 6.2 Plan 01 Task 3 — propose_init wiring tests
# ===========================================================================


def test_propose_init_handles_assign_tracks_value_error(
    client_with_phase6, test_engine, monkeypatch,
):
    """ValueError from assign_tracks_to_user_vibes (e.g. cold-start) renders
    refine_error.html with the exception message and HTTP 200.
    """
    from app.routers import api_setup

    async def fake_raise(names):
        raise ValueError("cold-start: <30 rated tracks (have 5)")

    monkeypatch.setattr(api_setup, "assign_tracks_to_user_vibes", fake_raise)

    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["a", "b", "c"]'},
    )
    assert r.status_code == 200
    assert "cold-start" in r.text


def test_propose_init_writes_draft_proposals_json_with_pass2_tracks(
    client_with_phase6, test_engine, monkeypatch,
):
    """Phase 6.2: SetupState.draft_proposals_json round-trips via
    VibeProposalSet.model_validate_json AND preserves pass2_tracks.
    """
    from app.routers import api_setup
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet

    async def fake_assign(names):
        return VibeProposalSet(
            proposals=[
                VibeProposal(
                    name="alpha", description="d", action="new",
                    seed_track_indices=[0, 1],
                    member_count=2,
                    pass2_tracks=[{
                        "rating_key": "rk_42",
                        "title": "Boundary Track",
                        "artist": "Some Artist",
                        "pass1_grade": "weak",
                        "pass1_reason": "thin signal",
                        "pass2_reason": "peer match confirmed",
                    }],
                ),
                VibeProposal(
                    name="beta", description="d", action="new",
                    seed_track_indices=[2, 3], member_count=2,
                ),
                VibeProposal(
                    name="gamma", description="d", action="new",
                    seed_track_indices=[4, 5], member_count=2,
                ),
            ],
            rated_track_count=6,
            rated_track_index_map=[
                {"index": i, "rating_key": str(1000 + i),
                 "title": f"T{i}", "artist": "A"}
                for i in range(6)
            ],
        )

    monkeypatch.setattr(api_setup, "assign_tracks_to_user_vibes", fake_assign)

    with Session(test_engine) as session:
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["alpha", "beta", "gamma"]'},
    )
    assert r.status_code == 200, r.text

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(
            select(SetupState).where(SetupState.id == 1)
        ).first()
        # Round-trip: JSON survives the persistence path.
        decoded = VibeProposalSet.model_validate_json(state.draft_proposals_json)
        alpha = next(p for p in decoded.proposals if p.name == "alpha")
        assert len(alpha.pass2_tracks) == 1
        entry = alpha.pass2_tracks[0]
        assert entry["pass1_grade"] == "weak"
        assert entry["pass1_reason"] == "thin signal"
        assert entry["pass2_reason"] == "peer match confirmed"
        assert entry["rating_key"] == "rk_42"


def test_propose_init_latest_llm_call_id_picks_up_pass2_purpose(
    client_with_phase6, test_engine, monkeypatch,
):
    """LLMUsage rows with purpose=vibe_assign_pass1 then vibe_assign_pass2:
    state.last_llm_call_id picks up the most recent (the Pass 2 row).
    """
    from app.routers import api_setup
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet

    async def fake_assign(names):
        return VibeProposalSet(
            proposals=[
                VibeProposal(name="a", description="d", action="new",
                             seed_track_indices=[0]),
                VibeProposal(name="b", description="d", action="new",
                             seed_track_indices=[1]),
                VibeProposal(name="c", description="d", action="new",
                             seed_track_indices=[2]),
            ],
            rated_track_count=3,
            rated_track_index_map=[
                {"index": i, "rating_key": str(i),
                 "title": f"T{i}", "artist": "A"} for i in range(3)
            ],
        )

    monkeypatch.setattr(api_setup, "assign_tracks_to_user_vibes", fake_assign)

    pass2_row_id: list = []
    with Session(test_engine) as session:
        from app.models.llm_usage import LLMUsage
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session, step="proposing")
        pass1 = LLMUsage(
            called_at="2026-05-12T10:00:00Z",
            purpose="vibe_assign_pass1",
            model="claude-sonnet",
        )
        session.add(pass1)
        session.commit()
        pass2 = LLMUsage(
            called_at="2026-05-12T10:05:00Z",
            purpose="vibe_assign_pass2",
            model="claude-sonnet",
        )
        session.add(pass2)
        session.commit()
        pass2_row_id.append(pass2.id)

    r = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["a", "b", "c"]'},
    )
    assert r.status_code == 200, r.text

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(
            select(SetupState).where(SetupState.id == 1)
        ).first()
        # The Pass 2 row should be most recent by id (inserted last) — the
        # "vibe_" prefix in _latest_llm_call_id must pick it up.
        assert state.last_llm_call_id == pass2_row_id[0], (
            f"Expected last_llm_call_id={pass2_row_id[0]} "
            f"(the Pass 2 row); got {state.last_llm_call_id}"
        )


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


# ===========================================================================
# Phase 6.2 Plan 02 — WIZ-08 "Start Over" reset (D-24..D-30).
#
# These tests cover the new POST /api/setup/start-over endpoint. The endpoint
# mirrors run_phase_61_migration's architecture (app/main.py) minus the
# MigrationLog gate (D-27 — WIZ-08 is repeatable, not one-shot).
#
# Mandatory tests (per the threat register):
# - test_start_over_preserves_serviceconfig_rows           (T-062-11 / T3)
# - test_start_over_idempotent_second_call_is_noop          (T-062-16 / D-29)
# - test_start_over_plex_archive_failure_does_not_block_db_wipe (T-062-12 / T4)
# - test_setup_state_draft_proposals_json_cleared_post_reset    (T-062-13)
# - test_start_over_tight_except_pattern                    (T-062-15)
# ===========================================================================


def _seed_serviceconfig_rows(session: Session) -> None:
    """Seed plex/anthropic/lidarr rows so the preserve-test has something
    to assert."""
    from app.services.settings_service import save_setting

    save_setting(session, "plex", "http://localhost:32400", "plex-token-xyz")
    save_setting(session, "anthropic", "https://api.anthropic.com", "anth-key-abc")
    save_setting(session, "lidarr", "http://localhost:8686", "lidarr-key-123")


def _seed_managed_playlists(session: Session, names: list[str]) -> list[int]:
    """Seed ManagedPlaylist rows; return their ids."""
    from app.models.vibe import ManagedPlaylist

    ids = []
    for i, name in enumerate(names):
        mp = ManagedPlaylist(
            kind="vibe",
            plex_rating_key=f"rk_{i}",
            composer_name=name,
            track_count=10,
        )
        session.add(mp)
    session.commit()
    rows = session.exec(select(ManagedPlaylist)).all()
    ids = [r.id for r in rows]
    return ids


def _seed_vibes_and_trackvibes(session: Session) -> None:
    """Seed Vibe + TrackVibe rows so the wipe-test can verify cleanup."""
    from app.models.track import Track
    from app.models.vibe import TrackVibe, Vibe

    now = datetime.now(timezone.utc).isoformat()
    v1 = Vibe(name="Vibe-A", description="d", is_active=True, created_at=now)
    v2 = Vibe(name="Vibe-B", description="d", is_active=True, created_at=now)
    session.add(v1)
    session.add(v2)
    session.commit()
    session.refresh(v1)
    session.refresh(v2)

    t1 = Track(plex_rating_key="rk-T1", title="T1", artist="A")
    t2 = Track(plex_rating_key="rk-T2", title="T2", artist="A")
    session.add(t1)
    session.add(t2)
    session.commit()
    session.refresh(t1)
    session.refresh(t2)

    session.add(TrackVibe(
        track_id=t1.id, vibe_id=v1.id, distance=0.1,
        assigned_at=now, assigned_by="cluster",
    ))
    session.add(TrackVibe(
        track_id=t2.id, vibe_id=v1.id, distance=0.1,
        assigned_at=now, assigned_by="cluster",
    ))
    session.commit()


# ---------------------------------------------------------------------------
# MANDATORY (T3): ServiceConfig preserved across reset.
# ---------------------------------------------------------------------------
def test_start_over_preserves_serviceconfig_rows(
    client_with_phase6, test_engine, monkeypatch
):
    """T-062-11: WIZ-08 reset MUST NOT touch ServiceConfig rows. If this
    test starts failing, the user would have to re-enter Plex token +
    Anthropic API key + Lidarr API key after every reset. HIGH severity.
    """
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        AsyncMock(),
    )

    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    # ServiceConfig rows MUST still exist post-reset.
    from app.models.settings import ServiceConfig
    with Session(test_engine) as session:
        rows = session.exec(select(ServiceConfig)).all()
        service_names = {r.service_name for r in rows}
        assert "plex" in service_names
        assert "anthropic" in service_names
        assert "lidarr" in service_names

        # url + is_configured preserved.
        for name in ("plex", "anthropic", "lidarr"):
            r = session.exec(
                select(ServiceConfig).where(ServiceConfig.service_name == name)
            ).first()
            assert r is not None, f"ServiceConfig.{name} missing"
            assert r.is_configured is True
            assert r.url, f"ServiceConfig.{name}.url was wiped"
            assert r.encrypted_credential, (
                f"ServiceConfig.{name}.encrypted_credential was wiped"
            )


# ---------------------------------------------------------------------------
# Plex playlists archived with date-stamped suffix (D-26).
# ---------------------------------------------------------------------------
def test_start_over_archives_plex_playlists_with_date_suffix(
    client_with_phase6, test_engine, monkeypatch
):
    """D-26: archive_playlist called with suffix='(archived YYYY-MM-DD)'."""
    from unittest.mock import AsyncMock
    from datetime import date

    mock_archive = AsyncMock()
    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        mock_archive,
    )

    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        _seed_managed_playlists(
            session,
            ["Composer · Workout", "Composer · Late Night"],
        )

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    assert mock_archive.call_count == 2
    today_iso = date.today().isoformat()
    expected_suffix = f"(archived {today_iso})"
    for call in mock_archive.call_args_list:
        assert call.kwargs.get("suffix") == expected_suffix, (
            f"archive_playlist called without correct suffix kwarg: "
            f"call={call}"
        )


# ---------------------------------------------------------------------------
# All five tables wiped + SetupState reset.
# ---------------------------------------------------------------------------
def test_start_over_wipes_all_five_tables(
    client_with_phase6, test_engine, monkeypatch
):
    """Vibe + TrackVibe + ManagedPlaylist + SlotInLog cleared; SetupState
    id=1 reset to step=rating_source, draft_proposals_json='', etc."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        AsyncMock(),
    )

    now = datetime.now(timezone.utc).isoformat()
    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        _seed_vibes_and_trackvibes(session)
        _seed_managed_playlists(session, ["Composer · X"])
        from app.models.vibe import SlotInLog
        session.add(SlotInLog(
            timestamp=now, track_id=1,
            vibe_ids="[1]", distances="[0.1]",
            soft_membership_applied=False, action="slot",
        ))
        _seed_setup_state(
            session, step="confirming",
            draft_proposals_json=_make_proposal_set_json(3),
            refinement_turn_count=5,
        )
        session.commit()

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    from app.models.vibe import (
        ManagedPlaylist, SetupState, SlotInLog, TrackVibe, Vibe,
    )
    with Session(test_engine) as session:
        assert session.exec(select(Vibe)).all() == []
        assert session.exec(select(TrackVibe)).all() == []
        assert session.exec(select(ManagedPlaylist)).all() == []
        assert session.exec(select(SlotInLog)).all() == []

        state = session.exec(
            select(SetupState).where(SetupState.id == 1)
        ).first()
        assert state is not None
        assert state.step == "rating_source"
        assert state.draft_proposals_json == ""
        assert state.refinement_turn_count == 0
        assert state.last_llm_call_id is None
        # recluster_mode can be False or 0 depending on SQLite storage.
        assert state.recluster_mode is False or state.recluster_mode == 0
        assert state.completed_at is None
        assert state.started_at is None


# ---------------------------------------------------------------------------
# Track.pending_slot_in cleared.
# ---------------------------------------------------------------------------
def test_start_over_clears_pending_slot_in(
    client_with_phase6, test_engine, monkeypatch
):
    from unittest.mock import AsyncMock
    from app.models.track import Track

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        AsyncMock(),
    )

    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        for i in range(5):
            session.add(
                Track(
                    plex_rating_key=f"slot-rk-{i}",
                    title=f"T{i}", artist="A",
                    pending_slot_in=1,
                )
            )
        session.commit()

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    with Session(test_engine) as session:
        for t in session.exec(select(Track)).all():
            assert (
                t.pending_slot_in == 0 or not t.pending_slot_in
            ), f"Track {t.plex_rating_key} still has pending_slot_in={t.pending_slot_in}"


# ---------------------------------------------------------------------------
# MANDATORY (D-29): second call is a no-op.
# ---------------------------------------------------------------------------
def test_start_over_idempotent_second_call_is_noop(
    client_with_phase6, test_engine, monkeypatch
):
    """ROADMAP success criterion 6: running /start-over twice in a row →
    zero Plex archive calls on the second call; zero DB row changes that
    affect observable state.
    """
    from unittest.mock import AsyncMock

    mock_archive = AsyncMock()
    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        mock_archive,
    )

    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        _seed_managed_playlists(session, ["Composer · One", "Composer · Two"])

    # First call: archives 2 playlists.
    r1 = client_with_phase6.post("/api/setup/start-over")
    assert r1.status_code == 204
    first_call_count = mock_archive.call_count
    assert first_call_count == 2

    # Second call: no managed playlists left → zero archive calls.
    r2 = client_with_phase6.post("/api/setup/start-over")
    assert r2.status_code == 204
    second_call_count = mock_archive.call_count
    assert second_call_count == first_call_count, (
        f"Second call MUST be a no-op (no Plex calls). "
        f"first={first_call_count}, second={second_call_count}"
    )

    # DB state still cleared (no rows recreated).
    from app.models.vibe import ManagedPlaylist
    with Session(test_engine) as session:
        assert session.exec(select(ManagedPlaylist)).all() == []


# ---------------------------------------------------------------------------
# T4: Plex archive failure on a single playlist does NOT block DB wipe.
# ---------------------------------------------------------------------------
def test_start_over_plex_archive_failure_does_not_block_db_wipe(
    client_with_phase6, test_engine, monkeypatch
):
    """T-062-12: first archive_playlist raises PlexApiException; second
    succeeds; DB wipe still commits.
    """
    from unittest.mock import AsyncMock
    import plexapi.exceptions

    call_count = {"n": 0}

    async def flaky_archive(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise plexapi.exceptions.PlexApiException(
                "transient plex failure"
            )

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        flaky_archive,
    )

    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        _seed_vibes_and_trackvibes(session)
        _seed_managed_playlists(
            session,
            ["Composer · First", "Composer · Second"],
        )
        from app.models.vibe import SlotInLog
        session.add(SlotInLog(
            timestamp=datetime.now(timezone.utc).isoformat(),
            track_id=1, vibe_ids="[1]", distances="[0.1]",
            soft_membership_applied=False, action="slot",
        ))
        session.commit()

    response = client_with_phase6.post("/api/setup/start-over")
    # (a) Response is 204.
    assert response.status_code == 204

    # (b) Both archive calls were attempted.
    assert call_count["n"] == 2, (
        f"Both playlists must be attempted; got {call_count['n']}"
    )

    # (c) All DB tables are wiped despite the partial failure.
    from app.models.vibe import (
        ManagedPlaylist, SlotInLog, TrackVibe, Vibe,
    )
    with Session(test_engine) as session:
        assert session.exec(select(Vibe)).all() == []
        assert session.exec(select(TrackVibe)).all() == []
        assert session.exec(select(ManagedPlaylist)).all() == []
        assert session.exec(select(SlotInLog)).all() == []


# ---------------------------------------------------------------------------
# 204 + HX-Redirect=/setup.
# ---------------------------------------------------------------------------
def test_start_over_endpoint_returns_204_with_hx_redirect_setup(
    client_with_phase6, test_engine, monkeypatch
):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        AsyncMock(),
    )

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204
    assert response.headers.get("hx-redirect") == "/setup"


# ---------------------------------------------------------------------------
# MANDATORY (T-062-15): tight except clause — never bare `except Exception`.
# Static AST scan of the start_over handler body.
# ---------------------------------------------------------------------------
def test_start_over_tight_except_pattern():
    """AST test: the start_over handler MUST catch EXACTLY the set
    {PlexApiException, httpx.HTTPError, PermissionError, TypeError}.
    Bare `except Exception` is FORBIDDEN inside the new handler body.

    Mirrors WARNING #9 enforcement on the Phase 6.1 migration.
    """
    import ast
    from pathlib import Path

    src = Path(
        "app/routers/api_setup.py"
    ).read_text()
    tree = ast.parse(src)

    start_over_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start_over":
            start_over_fn = node
            break
    assert start_over_fn is not None, "async def start_over not found"

    handlers_found = []
    for inner in ast.walk(start_over_fn):
        if not isinstance(inner, ast.Try):
            continue
        for handler in inner.handlers:
            handlers_found.append(handler)
            # Forbid bare `except:` and `except Exception` (with or without name).
            if handler.type is None:
                pytest.fail(
                    f"Bare `except:` clause found inside start_over at "
                    f"line {handler.lineno} — forbidden per WARNING #9."
                )
            # Build the set of exception names in this handler.
            if isinstance(handler.type, ast.Tuple):
                exc_nodes = handler.type.elts
            else:
                exc_nodes = [handler.type]

            for exc_node in exc_nodes:
                # Extract a printable name for diagnostics.
                if isinstance(exc_node, ast.Name):
                    exc_name = exc_node.id
                elif isinstance(exc_node, ast.Attribute):
                    # plexapi.exceptions.PlexApiException, httpx.HTTPError
                    parts = []
                    cur = exc_node
                    while isinstance(cur, ast.Attribute):
                        parts.append(cur.attr)
                        cur = cur.value
                    if isinstance(cur, ast.Name):
                        parts.append(cur.id)
                    exc_name = ".".join(reversed(parts))
                else:
                    exc_name = ast.dump(exc_node)
                assert exc_name != "Exception", (
                    f"`except Exception` found inside start_over at line "
                    f"{handler.lineno} — forbidden per WARNING #9. Use the "
                    f"tight set {{PlexApiException, httpx.HTTPError, "
                    f"PermissionError, TypeError}}."
                )

    assert len(handlers_found) >= 1, (
        "start_over must have at least one try/except guarding the per-"
        "playlist archive_playlist call (T-062-12)."
    )

    # Verify the per-playlist except clause catches the expected set.
    # Find the handler whose tuple contains the expected names.
    expected_exception_set = {
        "PlexApiException",
        "HTTPError",  # httpx.HTTPError
        "PermissionError",
        "TypeError",
    }

    def _exc_short_name(exc_node) -> str:
        if isinstance(exc_node, ast.Name):
            return exc_node.id
        if isinstance(exc_node, ast.Attribute):
            return exc_node.attr
        return ""

    matching_handler = None
    for handler in handlers_found:
        if isinstance(handler.type, ast.Tuple):
            names = {_exc_short_name(e) for e in handler.type.elts}
        elif handler.type is not None:
            names = {_exc_short_name(handler.type)}
        else:
            names = set()
        if names == expected_exception_set:
            matching_handler = handler
            break

    assert matching_handler is not None, (
        f"No except clause found in start_over with the exact tight set "
        f"{expected_exception_set}. Handlers seen: "
        f"{[ast.dump(h.type) for h in handlers_found if h.type is not None]}"
    )


# ---------------------------------------------------------------------------
# T-062-13: draft_proposals_json cleared post-reset (private-browser leak).
# ---------------------------------------------------------------------------
def test_setup_state_draft_proposals_json_cleared_post_reset(
    client_with_phase6, test_engine, monkeypatch
):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        AsyncMock(),
    )

    # Seed a non-empty draft_proposals_json (~1KB blob).
    blob = "x" * 1024
    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        _seed_setup_state(
            session, step="confirming",
            draft_proposals_json=blob,
            refinement_turn_count=4,
        )

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    from app.models.vibe import SetupState
    with Session(test_engine) as session:
        state = session.exec(
            select(SetupState).where(SetupState.id == 1)
        ).first()
        assert state is not None
        assert state.draft_proposals_json == ""


# ---------------------------------------------------------------------------
# Missing Plex credentials → DB wipe still runs; no archive call attempted.
# ---------------------------------------------------------------------------
def test_start_over_handles_missing_plex_credentials_gracefully(
    client_with_phase6, test_engine, monkeypatch
):
    """No Plex ServiceConfig → no archive call; DB wipe still runs."""
    from unittest.mock import AsyncMock

    mock_archive = AsyncMock()
    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        mock_archive,
    )

    with Session(test_engine) as session:
        # Seed only managed playlists — NO ServiceConfig for plex.
        _seed_managed_playlists(session, ["Composer · NoPlex"])
        _seed_vibes_and_trackvibes(session)

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    # No archive call attempted (no creds → skip Plex side).
    assert mock_archive.call_count == 0

    # DB wipe still ran.
    from app.models.vibe import ManagedPlaylist, TrackVibe, Vibe
    with Session(test_engine) as session:
        assert session.exec(select(Vibe)).all() == []
        assert session.exec(select(TrackVibe)).all() == []
        assert session.exec(select(ManagedPlaylist)).all() == []


# ---------------------------------------------------------------------------
# LLMUsage + EventLog NOT touched.
# ---------------------------------------------------------------------------
def test_start_over_does_not_touch_llm_usage_or_event_log(
    client_with_phase6, test_engine, monkeypatch
):
    """Cost history + event diagnostics survive reset."""
    from unittest.mock import AsyncMock
    from app.models.event_log import EventLog
    from app.models.llm_usage import LLMUsage

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        AsyncMock(),
    )

    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        session.add(LLMUsage(
            called_at=datetime.now(timezone.utc).isoformat(),
            purpose="vibe_assign_pass1",
            model="claude-sonnet",
            input_tokens=100,
            output_tokens=200,
            cost_estimate_usd=0.03,
        ))
        session.add(EventLog(
            source="webhook", event_type="rating_changed",
            plex_rating_key="rk1", dedupe_key="dedupe-1",
            received_at=datetime.now(timezone.utc).isoformat(),
        ))
        session.commit()

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    with Session(test_engine) as session:
        llm_rows = session.exec(select(LLMUsage)).all()
        evt_rows = session.exec(select(EventLog)).all()
        assert len(llm_rows) == 1, "LLMUsage rows lost (cost history wiped!)"
        assert len(evt_rows) == 1, "EventLog rows lost (diagnostics wiped!)"


# ---------------------------------------------------------------------------
# MigrationLog NOT touched (WIZ-08 doesn't undo first-deploy migration).
# ---------------------------------------------------------------------------
def test_start_over_does_not_run_phase_61_migration_gate(
    client_with_phase6, test_engine, monkeypatch
):
    """T-062-18: reset must NOT undo the Phase 6.1 first-deploy migration
    gate row in MigrationLog.
    """
    from unittest.mock import AsyncMock
    from app.models.vibe import MigrationLog

    monkeypatch.setattr(
        "app.services.plex_playlist_service.archive_playlist",
        AsyncMock(),
    )

    # The TestClient lifespan already ran run_phase_61_migration, which
    # inserted a MigrationLog(phase_id='6.1') row. Update its completed_at
    # to a known sentinel so we can assert it's preserved.
    sentinel = "2026-05-01T12:00:00+00:00"
    with Session(test_engine) as session:
        _seed_serviceconfig_rows(session)
        row = session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == "6.1")
        ).first()
        if row is None:
            session.add(MigrationLog(phase_id="6.1", completed_at=sentinel))
        else:
            row.completed_at = sentinel
            session.add(row)
        session.commit()

    response = client_with_phase6.post("/api/setup/start-over")
    assert response.status_code == 204

    with Session(test_engine) as session:
        row = session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == "6.1")
        ).first()
        assert row is not None, "MigrationLog row was wiped"
        assert row.completed_at == sentinel, (
            "MigrationLog.completed_at was modified"
        )


# ===========================================================================
# Phase 7 Plan 01 Task 2 — finalize() awaits bootstrap_suggestions_queue
# after reslot_all_rated_tracks success (D-01 wizard-finalize caller).
# ===========================================================================


@pytest.fixture
def client_with_phase7(test_engine) -> Generator[TestClient, None, None]:
    """Same as client_with_phase6 but also registers the Phase 7 tables.

    Phase 7 adds:
      - SuggestionsMirror (queue source-of-truth, FK to track + vibe).
      - MigrationLog (already registered by Phase 6.1; Phase 7 reuses it
        with phase_id='7.0-suggestions-bootstrap').
    """
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        MigrationLog,
        SetupState,
        SlotInLog,
        TrackVibe,
        Vibe,
    )
    from app.models.suggestions import SuggestionsMirror  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def _reset_suggestions_singleton_api_setup():
    try:
        from app.services import suggestions_service
        suggestions_service._status = suggestions_service.SuggestionsServiceStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from app.services import suggestions_service
        suggestions_service._status = suggestions_service.SuggestionsServiceStatus()
    except (ImportError, AttributeError):
        pass


def test_finalize_calls_bootstrap_suggestions_queue_after_reslot(
    client_with_phase7, test_engine, monkeypatch
):
    """D-01 — wizard finalize is one of two bootstrap callers. The bootstrap
    MUST be awaited AFTER reslot_all_rated_tracks (so the vibe playlists
    exist before Plan 02's first refill tries to read them) and BEFORE
    _finalize_status flips to 'completed'.
    """
    from unittest.mock import AsyncMock

    from app.routers import api_setup
    from app.services import suggestions_service
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

    call_order: list[str] = []

    async def fake_create_playlist(plex_url, plex_token, name, rating_keys, vibe_id=None):
        from app.models.vibe import ManagedPlaylist
        new_key = f"playlist-{vibe_id or len(call_order)}"
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

    async def fake_reslot():
        call_order.append("reslot")
        return 0

    bootstrap_mock = AsyncMock(side_effect=lambda: call_order.append("bootstrap"))

    monkeypatch.setattr(api_setup, "create_playlist", fake_create_playlist)
    monkeypatch.setattr(api_setup, "reslot_all_rated_tracks", fake_reslot)
    monkeypatch.setattr(
        suggestions_service, "bootstrap_suggestions_queue", bootstrap_mock
    )

    response = client_with_phase7.post("/api/setup/finalize")
    assert response.status_code == 200, response.text

    # bootstrap awaited exactly once.
    assert bootstrap_mock.await_count == 1
    # reslot ran BEFORE bootstrap.
    assert call_order == ["reslot", "bootstrap"]

    # _finalize_status flipped to completed (or at least reached the
    # bootstrap call site — final state should be 'completed').
    assert api_setup._finalize_status.state == "completed"


def test_finalize_bootstrap_failure_does_not_break_finalize_response(
    client_with_phase7, test_engine, monkeypatch
):
    """D-01 best-effort — if bootstrap_suggestions_queue raises (e.g.
    transient Plex outage), /api/setup/finalize still returns 200 with the
    completed banner. The lifespan migration retries on next restart.
    """
    from unittest.mock import AsyncMock

    from app.routers import api_setup
    from app.services import suggestions_service
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

    async def fake_create_playlist(plex_url, plex_token, name, rating_keys, vibe_id=None):
        from app.models.vibe import ManagedPlaylist
        new_key = f"playlist-{vibe_id or 0}"
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
    monkeypatch.setattr(
        api_setup, "reslot_all_rated_tracks", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        suggestions_service,
        "bootstrap_suggestions_queue",
        AsyncMock(side_effect=RuntimeError("simulated outage")),
    )

    response = client_with_phase7.post("/api/setup/finalize")
    assert response.status_code == 200

    # Finalize completed despite bootstrap failure — D-01 best-effort.
    assert api_setup._finalize_status.state == "completed"
    # Banner copy reaches completed state.
    assert "live in Plex" in response.text
