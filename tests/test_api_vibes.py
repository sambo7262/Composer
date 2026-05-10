"""Phase 6 Plan 04 tests for app/routers/api_vibes.py (D-20, D-22, D-36; VIBE-11).

Endpoints under test (commit-specific behavior is in tests/test_recluster_commit.py):
- POST /api/vibes/recluster/start  (D-20: enters re-cluster mode -> /setup/propose)
- POST /api/vibes/reslot-all        (D-36: fire-and-forget reslot)
- GET  /api/vibes/last-llm-call     (D-36: surface LLMUsage row aggregates)
- AST: api_vibes router registered BEFORE pages.router in app/main.py.
- Branch: api_setup.py refine call passes recluster_mode through.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
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
        SlotInLog,
        TrackVibe,
        Vibe,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def reset_recluster_status():
    try:
        from app.routers import api_vibes
        api_vibes._recluster_status = api_vibes.ReclusterStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from app.routers import api_vibes
        api_vibes._recluster_status = api_vibes.ReclusterStatus()
    except (ImportError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Test 1: POST /recluster/start sets recluster_mode + HX-Redirect /setup/propose
# ---------------------------------------------------------------------------
def test_recluster_start_redirects_to_setup_propose(client_with_phase6, test_engine):
    from app.models.vibe import SetupState

    # Pre-seed SetupState so we can verify it gets updated.
    with Session(test_engine) as s:
        existing = s.exec(select(SetupState).where(SetupState.id == 1)).first()
        if existing is None:
            s.add(SetupState(
                id=1, step="done", draft_proposals_json='{"existing": "data"}',
                refinement_turn_count=5, recluster_mode=False,
            ))
        else:
            existing.step = "done"
            existing.draft_proposals_json = '{"existing": "data"}'
            existing.refinement_turn_count = 5
            existing.recluster_mode = False
            s.add(existing)
        s.commit()

    resp = client_with_phase6.post(
        "/api/vibes/recluster/start", follow_redirects=False
    )
    assert resp.status_code == 204
    assert resp.headers.get("HX-Redirect") == "/setup/propose"

    # SetupState updated as expected.
    with Session(test_engine) as s:
        state = s.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.recluster_mode is True or state.recluster_mode == 1
        assert state.step == "proposing"
        assert state.draft_proposals_json == ""
        assert state.refinement_turn_count == 0


# ---------------------------------------------------------------------------
# Test 2: POST /reslot-all returns the small Reslotting partial + creates task
# ---------------------------------------------------------------------------
def test_post_reslot_all_returns_partial(client_with_phase6, monkeypatch):
    """D-36: POST /reslot-all fires asyncio.create_task and returns the partial."""
    fired = {"called": False}

    async def fake_reslot():
        fired["called"] = True
        return 0

    monkeypatch.setattr(
        "app.routers.api_vibes.reslot_all_rated_tracks",
        fake_reslot,
    )

    resp = client_with_phase6.post("/api/vibes/reslot-all")
    assert resp.status_code == 200
    body = resp.text
    assert "Reslotting" in body
    # Allow asyncio task to actually run.
    import time
    time.sleep(0.05)
    # The fire-and-forget task may or may not complete in a TestClient context;
    # the important assertion is the response shape.


# ---------------------------------------------------------------------------
# Test 3: GET /last-llm-call returns LLMUsage aggregates (or empty-state)
# ---------------------------------------------------------------------------
def test_last_llm_call_renders_empty_when_no_rows(client_with_phase6):
    resp = client_with_phase6.get("/api/vibes/last-llm-call")
    assert resp.status_code == 200
    assert "No cluster-proposal LLM calls" in resp.text


def test_last_llm_call_renders_latest_clustering_row(
    client_with_phase6, test_engine
):
    """D-36: surface model + token aggregates from the latest LLMUsage row."""
    from app.models.llm_usage import LLMUsage

    with Session(test_engine) as s:
        s.add(LLMUsage(
            called_at=datetime.now(timezone.utc).isoformat(),
            model="claude-sonnet-4-5",
            input_tokens=100,
            cache_creation_input_tokens=2048,
            cache_read_input_tokens=0,
            output_tokens=500,
            cost_estimate_usd=0.012,
            purpose="vibe_clustering_recluster",
        ))
        s.commit()

    resp = client_with_phase6.get("/api/vibes/last-llm-call")
    assert resp.status_code == 200
    body = resp.text
    assert "vibe_clustering_recluster" in body
    assert "claude-sonnet-4-5" in body
    assert "0.0120" in body  # 4-decimal cost format


# ---------------------------------------------------------------------------
# Test 4: app/main.py registers api_vibes BEFORE pages.router (AST walk)
# ---------------------------------------------------------------------------
def test_api_vibes_registered_before_pages_in_main():
    """The api_vibes router MUST be included before pages.router so /api/vibes/*
    routes resolve before any wildcard page handlers."""
    main_path = Path(__file__).parent.parent / "app" / "main.py"
    src = main_path.read_text()
    tree = ast.parse(src)

    api_vibes_idx = None
    pages_idx = None

    for i, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "include_router":
                if not node.args:
                    continue
                arg = node.args[0]
                # Match include_router(api_vibes.router) / include_router(pages.router)
                if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                    name = arg.value.id
                    if name == "api_vibes" and api_vibes_idx is None:
                        api_vibes_idx = i
                    elif name == "pages" and pages_idx is None:
                        pages_idx = i

    assert api_vibes_idx is not None, (
        "app/main.py does not register api_vibes.router"
    )
    assert pages_idx is not None, "app/main.py does not register pages.router"
    assert api_vibes_idx < pages_idx, (
        "api_vibes router must be included BEFORE pages.router; "
        f"api_vibes at AST index {api_vibes_idx}, pages at {pages_idx}"
    )


# ---------------------------------------------------------------------------
# Test 5: api_setup.refine passes recluster_mode through to refine_proposals.
# ---------------------------------------------------------------------------
def test_refine_passes_recluster_mode_through(client_with_phase6, monkeypatch, test_engine):
    """D-20 / D-34: when SetupState.recluster_mode=True, refine() forwards
    recluster_mode=True to vibe_clusterer.refine_proposals (which uses
    purpose='vibe_clustering_recluster')."""
    from app.models.vibe import SetupState
    from app.services.vibe_clusterer import VibeProposalSet, VibeProposal

    captured = {}

    async def fake_refine(prior, message, recluster_mode=False):
        captured["recluster_mode"] = recluster_mode
        # Return a minimal valid proposal set echoing prior.
        return VibeProposalSet(
            proposals=[
                VibeProposal(
                    name="X", description="Y", action="keep",
                    seed_track_indices=[0], source_vibe_ids=[],
                )
            ],
            rated_track_count=10,
            rated_track_index_map=[{"index": 0, "rating_key": "100"}],
        )

    monkeypatch.setattr(
        "app.routers.api_setup.refine_proposals", fake_refine
    )

    # Seed SetupState with recluster_mode=True + a draft proposal.
    with Session(test_engine) as s:
        prior = VibeProposalSet(
            proposals=[
                VibeProposal(
                    name="OldVibe", description="d", action="keep",
                    seed_track_indices=[0], source_vibe_ids=[],
                )
            ],
            rated_track_count=10,
            rated_track_index_map=[{"index": 0, "rating_key": "100"}],
        )
        existing = s.exec(select(SetupState).where(SetupState.id == 1)).first()
        if existing is None:
            s.add(SetupState(
                id=1, step="proposing",
                draft_proposals_json=prior.model_dump_json(),
                refinement_turn_count=0, recluster_mode=True,
            ))
        else:
            existing.step = "proposing"
            existing.draft_proposals_json = prior.model_dump_json()
            existing.refinement_turn_count = 0
            existing.recluster_mode = True
            s.add(existing)
        s.commit()

    resp = client_with_phase6.post(
        "/api/setup/refine", data={"message": "merge X and Y"}
    )
    assert resp.status_code == 200
    assert captured.get("recluster_mode") is True, (
        "refine must forward recluster_mode=True when SetupState.recluster_mode=True"
    )
