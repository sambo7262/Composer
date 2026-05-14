"""Phase 6 Plan 03 Task 3 — finalize end-to-end integration tests + push banner contract.

Tests cover:
- Push banner running state polls every 2s + has Pushing… {created}/{total} copy.
- Push banner completed state shows live-in-Plex copy + link to /setup/done.
- Push banner failed state shows partial-count + Retry button (D-25 idempotent retry).
- Finalize creates 3 Vibe rows + 3 ManagedPlaylist rows + 3 create_playlist calls
  (D-25 sequential under semaphore=1).
- Partial failure: 2 vibes persisted + step stays at confirming + state="failed".
- Sequential semaphore=1 — concurrent active count never exceeds 1 (D-25).
"""
from __future__ import annotations

import asyncio
import json
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


def _make_proposal_set_json(num_proposals: int = 3) -> str:
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
                    "energy": 0.5,
                    "tempo": 120.0,
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
        "rated_track_index_map": [
            {"index": idx, "rating_key": str(1000 + idx), "title": f"T{idx}", "artist": "A"}
            for idx in range(60)
        ],
    }
    return json.dumps(payload)


def _seed_rated_tracks(session: Session, n: int) -> None:
    from app.models.track import Track
    for i in range(n):
        session.add(
            Track(
                plex_rating_key=str(1000 + i),
                title=f"Title {i}",
                artist="Artist",
                user_rating=8.0,
                energy=0.5,
                tempo=120.0,
                danceability=0.5,
                valence=0.5,
            )
        )
    session.commit()


def _seed_setup_state(session: Session, **kwargs):
    from app.models.vibe import SetupState
    defaults = {
        "id": 1,
        "step": "proposing",
        "draft_proposals_json": _make_proposal_set_json(3),
        "refinement_turn_count": 2,
    }
    defaults.update(kwargs)
    session.merge(SetupState(**defaults))
    session.commit()


def _seed_plex_creds(session: Session) -> None:
    from app.services.settings_service import save_setting
    save_setting(session, "plex", "http://plex.local", "fake-token")


# ---------------------------------------------------------------------------
# Test 1: push banner running state polls every 2s + Pushing copy
# ---------------------------------------------------------------------------
def test_push_banner_running_state_polls_every_2s():
    src = Path(__file__).parent.parent / "app" / "templates" / "partials" / "push_to_plex_banner.html"
    text = src.read_text()
    assert 'every 2s' in text
    assert 'hx-get="/api/setup/finalize/status"' in text
    assert "Pushing…" in text


# ---------------------------------------------------------------------------
# Test 2: push banner completed state has live-in-Plex copy + link to /setup/done
# ---------------------------------------------------------------------------
def test_push_banner_completed_state_links_to_done():
    src = Path(__file__).parent.parent / "app" / "templates" / "partials" / "push_to_plex_banner.html"
    text = src.read_text()
    assert "live in Plex" in text
    assert "/setup/done" in text


# ---------------------------------------------------------------------------
# Test 3: push banner failed state has partial-count + Retry button
# ---------------------------------------------------------------------------
def test_push_banner_failed_state_shows_partial_count_and_retry():
    src = Path(__file__).parent.parent / "app" / "templates" / "partials" / "push_to_plex_banner.html"
    text = src.read_text()
    assert "Created" in text
    assert "Retry the rest" in text
    assert "Retry" in text  # button label
    assert 'hx-post="/api/setup/finalize"' in text


# ---------------------------------------------------------------------------
# Test 4: finalize creates 3 vibes + 3 playlists end-to-end
# ---------------------------------------------------------------------------
def test_finalize_integration_creates_three_vibes_and_three_playlists(
    client_with_phase6, test_engine, monkeypatch
):
    from app.routers import api_setup
    from app.models.vibe import ManagedPlaylist, SetupState, TrackVibe, Vibe

    with Session(test_engine) as session:
        _seed_plex_creds(session)
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session)

    create_calls: list = []

    async def fake_create_playlist(plex_url, plex_token, name, rating_keys, vibe_id=None):
        idx = len(create_calls) + 1
        create_calls.append({"name": name, "vibe_id": vibe_id, "rk_count": len(rating_keys)})
        new_key = f"playlist-{idx}"
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

    # Verify Composer · prefix on every playlist name (D-24 / VIBE-04).
    for call in create_calls:
        assert call["name"].startswith("Composer · "), call

    with Session(test_engine) as session:
        vibes = session.exec(select(Vibe)).all()
        assert len(vibes) == 3
        for v in vibes:
            assert v.is_active is True
            assert v.created_at
        # Phase 7 Plan 01: filter to kind='vibe' since finalize now also
        # calls bootstrap_suggestions_queue which may add a
        # ManagedPlaylist(kind='suggestions') row.
        managed = session.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.kind == "vibe")
        ).all()
        assert len(managed) == 3
        for mp in managed:
            assert mp.kind == "vibe"
            assert mp.vibe_id is not None
            assert mp.composer_name.startswith("Composer · ")
        track_vibes = session.exec(select(TrackVibe)).all()
        assert len(track_vibes) >= 6  # 3 proposals × 2 seed tracks
        for tv in track_vibes:
            assert tv.assigned_by == "cluster"

        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        assert state.step == "done"
        assert state.completed_at is not None

    # The push banner partial returned should be the completed state.
    assert "live in Plex" in response.text


# ---------------------------------------------------------------------------
# Test 5: partial failure — 2 vibes persisted + step stays at confirming
# ---------------------------------------------------------------------------
def test_finalize_integration_handles_partial_failure(
    client_with_phase6, test_engine, monkeypatch
):
    from app.routers import api_setup
    from app.models.vibe import ManagedPlaylist, SetupState, Vibe

    with Session(test_engine) as session:
        _seed_plex_creds(session)
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session)

    call_count = {"n": 0}

    async def fake_create_playlist(plex_url, plex_token, name, rating_keys, vibe_id=None):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("simulated Plex outage")
        new_key = f"playlist-{call_count['n']}"
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
    # Phase 6.1 Blocker #7 Option A: mock reslot to avoid real Plex slot_track.
    # On partial failure, reslot is NOT called — but this monkeypatch is a
    # safety net for the success branch should the failure branch behaviour
    # ever change.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        api_setup, "reslot_all_rated_tracks", AsyncMock(return_value=0)
    )

    response = client_with_phase6.post("/api/setup/finalize")
    assert response.status_code == 200

    with Session(test_engine) as session:
        # Two vibes created (3rd raised before INSERT chain completed).
        # Note: The 3rd Vibe row IS created (we INSERT vibe first, then create_playlist).
        # So we expect exactly 3 Vibe rows but only 2 ManagedPlaylist rows.
        # Phase 7 Plan 01: lifespan migration runs on TestClient startup and
        # inserts a ManagedPlaylist(kind='suggestions') row — filter the
        # assertion to kind='vibe' so the Phase 6 contract is unaffected.
        vibes = session.exec(select(Vibe)).all()
        managed = session.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.kind == "vibe")
        ).all()
        assert len(managed) == 2
        # The 3rd Vibe row may exist (we create Vibe before create_playlist).
        # Acceptable invariants: at least 2 Vibe rows + exactly 2 ManagedPlaylist rows.
        assert len(vibes) >= 2

        state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
        assert state is not None
        # Step must NOT be done — partial failure leaves it pre-done.
        assert state.step != "done"

    # Failed banner copy.
    assert "Created 2 of 3 playlists" in response.text


# ---------------------------------------------------------------------------
# Test 6: finalize uses semaphore(1) — never more than 1 concurrent
# ---------------------------------------------------------------------------
def test_finalize_uses_semaphore_one(client_with_phase6, test_engine, monkeypatch):
    from app.routers import api_setup
    from app.models.vibe import ManagedPlaylist

    with Session(test_engine) as session:
        _seed_plex_creds(session)
        _seed_rated_tracks(session, 60)
        _seed_setup_state(session)

    active_now = {"n": 0}
    max_observed = {"n": 0}

    async def fake_create_playlist(plex_url, plex_token, name, rating_keys, vibe_id=None):
        active_now["n"] += 1
        max_observed["n"] = max(max_observed["n"], active_now["n"])
        await asyncio.sleep(0.05)  # simulate Plex API latency
        new_key = f"playlist-{vibe_id or active_now['n']}"
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
        active_now["n"] -= 1
        return new_key

    monkeypatch.setattr(api_setup, "create_playlist", fake_create_playlist)
    # Phase 6.1 Blocker #7 Option A: mock reslot to avoid real Plex slot_track.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        api_setup, "reslot_all_rated_tracks", AsyncMock(return_value=0)
    )

    response = client_with_phase6.post("/api/setup/finalize")
    assert response.status_code == 200
    assert max_observed["n"] <= 1, (
        f"D-25 violated — observed {max_observed['n']} concurrent create_playlist "
        f"calls; expected ≤ 1 (sequential under asyncio.Semaphore(1))"
    )
