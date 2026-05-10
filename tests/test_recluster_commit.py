"""Phase 6 Plan 04 — recluster commit reconciliation tests (D-21, D-22, D-23).

These tests exercise the full diff matrix (keep / renamed_from / dropped /
merged_from / split_from / new) of POST /api/vibes/recluster/commit. Each
action exercises a distinct combination of plex_playlist_service operations
+ Vibe row mutations:

  - keep: UPDATE Vibe in place, additive update_playlist_items.
  - renamed_from: UPDATE + rename_playlist + additive reconcile.
  - dropped: archive_playlist + Vibe.is_active=False.
  - new: INSERT Vibe + create_playlist + INSERT TrackVibe(cluster).
  - merged_from: archive(N) + INSERT Vibe + create_playlist.
  - split_from: archive(1) + INSERT Vibe + create_playlist.

Manual override preservation tests (D-22): TrackVibe(assigned_by='manual')
rows are snapshotted before commit and replayed by name match (or
source_vibe_ids fallback) afterward. If no successor exists, a SlotInLog
row with action='manual_override_lost' is written.

Idempotency tests (D-23): partial Plex API failure leaves the rest of the
proposals applied; re-running commit short-circuits already-created vibes
by name match.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def client_with_phase6(test_engine) -> Generator[TestClient, None, None]:
    """TestClient + fully migrated Phase 5 + Phase 6 schema."""
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


@pytest.fixture(autouse=True)
def patch_plex_credentials(monkeypatch, client_with_phase6, test_engine):
    """Configure a fake Plex setting so recluster_commit doesn't 500.

    Depends on client_with_phase6 to ensure init_db has run + tables exist.
    Monkeypatches get_decrypted_credential to bypass encryption inside the
    session boundary.
    """
    from app.models.settings import ServiceConfig

    with Session(test_engine) as s:
        existing = s.exec(
            select(ServiceConfig).where(
                ServiceConfig.service_name == "plex"
            )
        ).first()
        if existing is None:
            s.add(ServiceConfig(
                service_name="plex",
                url="http://plex.local",
                encrypted_credential="ignored-by-monkeypatch",
                is_configured=True,
            ))
        else:
            existing.url = "http://plex.local"
            existing.encrypted_credential = "ignored-by-monkeypatch"
            existing.is_configured = True
            s.add(existing)
        s.commit()

    # Bypass real decryption — return a fixed token string regardless of input.
    def fake_decrypt(session, name):
        return "fake-token"

    monkeypatch.setattr(
        "app.services.settings_service.get_decrypted_credential",
        fake_decrypt,
    )
    # Also patch the lazy-import name used inside api_vibes (it imports the
    # function inside the recluster_commit function body).
    yield


@pytest.fixture
def patched_plex_helpers(monkeypatch):
    """Patch the lazy-import shims on api_vibes router for create/update/archive/rename."""
    from unittest.mock import AsyncMock

    create_mock = AsyncMock(return_value="new-pl-key-100")
    update_mock = AsyncMock()
    archive_mock = AsyncMock()
    rename_mock = AsyncMock()

    monkeypatch.setattr(
        "app.routers.api_vibes.create_playlist", create_mock
    )
    monkeypatch.setattr(
        "app.routers.api_vibes.update_playlist_items", update_mock
    )
    monkeypatch.setattr(
        "app.routers.api_vibes.archive_playlist", archive_mock
    )
    monkeypatch.setattr(
        "app.routers.api_vibes.rename_playlist", rename_mock
    )
    return {
        "create": create_mock,
        "update": update_mock,
        "archive": archive_mock,
        "rename": rename_mock,
    }


# ---------------------------------------------------------------------------
# Test fixtures + helpers
# ---------------------------------------------------------------------------

def _seed_track(session, rk, title="Title", artist="Artist", rating=8.0):
    from app.models.track import Track
    t = Track(
        plex_rating_key=str(rk),
        title=title,
        artist=artist,
        album="Album",
        user_rating=rating,
        energy=0.5,
        tempo=120.0,
        danceability=0.5,
        valence=0.5,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_vibe(session, name, plex_pl_key=None):
    """Returns the vibe.id (an int) — safe across Session boundaries."""
    from app.models.vibe import Vibe, ManagedPlaylist
    v = Vibe(
        name=name,
        description=f"desc {name}",
        created_at=datetime.now(timezone.utc).isoformat(),
        centroid_energy=0.5, centroid_tempo=120.0,
        centroid_danceability=0.5, centroid_valence=0.5,
        spread_energy=0.1, spread_tempo=10.0,
        spread_danceability=0.1, spread_valence=0.1,
        is_active=True,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    vibe_id = v.id

    if plex_pl_key is not None:
        session.add(ManagedPlaylist(
            kind="vibe",
            vibe_id=vibe_id,
            plex_rating_key=str(plex_pl_key),
            composer_name=f"Composer · {name}",
            track_count=0,
        ))
        session.commit()
    return vibe_id


def _seed_track_id(session, rk, **kwargs):
    """Returns the track.id (an int) — safe across Session boundaries."""
    t = _seed_track(session, rk, **kwargs)
    return t.id


def _setup_state_with_proposals(session, proposals_set):
    from app.models.vibe import SetupState
    state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
    if state is None:
        state = SetupState(id=1)
        session.add(state)
        session.commit()
    state.recluster_mode = True
    state.step = "proposing"
    state.draft_proposals_json = proposals_set.model_dump_json()
    session.add(state)
    session.commit()


def _make_proposal(
    name, action, source_vibe_ids=None, seed_track_indices=None,
    description=None,
):
    from app.services.vibe_clusterer import VibeProposal
    return VibeProposal(
        name=name,
        description=description or f"desc {name}",
        action=action,
        source_vibe_ids=source_vibe_ids or [],
        seed_track_indices=seed_track_indices or [0],
        centroid={
            "energy": 0.5, "tempo": 120.0,
            "danceability": 0.5, "valence": 0.5,
        },
        spread={
            "energy": 0.1, "tempo": 10.0,
            "danceability": 0.1, "valence": 0.1,
        },
        silhouette=0.4,
    )


def _make_proposal_set(proposals, rated_keys):
    from app.services.vibe_clusterer import VibeProposalSet
    return VibeProposalSet(
        proposals=proposals,
        rated_track_count=len(rated_keys),
        rated_track_index_map=[
            {"index": i, "rating_key": rk} for i, rk in enumerate(rated_keys)
        ],
        forced_k=None,
        degraded_mode=False,
        silhouette_avg=0.4,
    )


# ===========================================================================
# Test 2: keep action — UPDATE in place, no rename, additive update.
# ===========================================================================
def test_recluster_commit_handles_keep_action(
    client_with_phase6, test_engine, patched_plex_helpers
):
    with Session(test_engine) as s:
        # Seed 3 existing vibes + 3 ManagedPlaylist + 3 tracks.
        v1_id = _seed_vibe(s, "Late Night", plex_pl_key="pl-1")
        v2_id = _seed_vibe(s, "Workout", plex_pl_key="pl-2")
        v3_id = _seed_vibe(s, "Focus", plex_pl_key="pl-3")
        _seed_track(s, rk=100)
        _seed_track(s, rk=101)
        _seed_track(s, rk=102)
        proposals = [
            _make_proposal("Late Night", "keep", source_vibe_ids=[v1_id], seed_track_indices=[0]),
            _make_proposal("Workout", "keep", source_vibe_ids=[v2_id], seed_track_indices=[1]),
            _make_proposal("Focus", "keep", source_vibe_ids=[v3_id], seed_track_indices=[2]),
        ]
        ps = _make_proposal_set(proposals, ["100", "101", "102"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # No rename calls (names unchanged).
    assert patched_plex_helpers["rename"].await_count == 0
    # No archive calls (no drops).
    assert patched_plex_helpers["archive"].await_count == 0
    # No new playlist (all keeps).
    assert patched_plex_helpers["create"].await_count == 0
    # update_playlist_items called once per kept vibe.
    assert patched_plex_helpers["update"].await_count == 3

    # All 3 vibes still exist + active.
    from app.models.vibe import Vibe
    with Session(test_engine) as s:
        vibes = s.exec(select(Vibe).where(Vibe.is_active == True)).all()  # noqa: E712
        assert len(vibes) == 3


# ===========================================================================
# Test 3: renamed_from — rename_playlist called, name updates, additive reconcile.
# ===========================================================================
def test_recluster_commit_handles_renamed_action(
    client_with_phase6, test_engine, patched_plex_helpers
):
    with Session(test_engine) as s:
        v1_id = _seed_vibe(s, "Late Night", plex_pl_key="pl-1")
        _seed_track(s, rk=100)
        proposals = [
            _make_proposal(
                "Late Night Drives", "renamed_from",
                source_vibe_ids=[v1_id], seed_track_indices=[0],
            ),
        ]
        ps = _make_proposal_set(proposals, ["100"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # rename_playlist called with the new title.
    assert patched_plex_helpers["rename"].await_count == 1
    args, kwargs = patched_plex_helpers["rename"].await_args
    # Signature: rename_playlist(plex_url, plex_token, playlist_key, new_name)
    new_name = args[3] if len(args) >= 4 else kwargs.get("new_name")
    assert new_name == "Composer · Late Night Drives"

    # Vibe row name updated.
    from app.models.vibe import Vibe
    with Session(test_engine) as s:
        v = s.exec(select(Vibe).where(Vibe.id == v1_id)).first()
        assert v.name == "Late Night Drives"


# ===========================================================================
# Test 4: dropped action — archive_playlist + Vibe.is_active=False.
# ===========================================================================
def test_recluster_commit_handles_dropped_action(
    client_with_phase6, test_engine, patched_plex_helpers
):
    with Session(test_engine) as s:
        v1_id = _seed_vibe(s, "Workout", plex_pl_key="pl-2")
        _seed_track(s, rk=200)
        proposals = [
            _make_proposal(
                "Workout", "dropped",
                source_vibe_ids=[v1_id], seed_track_indices=[],
            ),
        ]
        ps = _make_proposal_set(proposals, ["200"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # archive_playlist called once.
    assert patched_plex_helpers["archive"].await_count == 1
    # No create / rename.
    assert patched_plex_helpers["create"].await_count == 0
    assert patched_plex_helpers["rename"].await_count == 0

    # Vibe row preserved (audit trail) but is_active=False.
    from app.models.vibe import Vibe
    with Session(test_engine) as s:
        v = s.exec(select(Vibe).where(Vibe.id == v1_id)).first()
        assert v is not None, "Vibe row deleted — should be preserved per D-21"
        assert v.is_active is False or v.is_active == 0


# ===========================================================================
# Test 5: new action — INSERT Vibe + create_playlist + TrackVibe rows.
# ===========================================================================
def test_recluster_commit_handles_new_action(
    client_with_phase6, test_engine, patched_plex_helpers
):
    with Session(test_engine) as s:
        _seed_track(s, rk=300)
        _seed_track(s, rk=301)
        proposals = [
            _make_proposal(
                "Pre-Workout", "new",
                source_vibe_ids=[], seed_track_indices=[0, 1],
            ),
        ]
        ps = _make_proposal_set(proposals, ["300", "301"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # create_playlist called once.
    assert patched_plex_helpers["create"].await_count == 1
    args, kwargs = patched_plex_helpers["create"].await_args
    # Signature: create_playlist(plex_url, plex_token, name, rating_keys, vibe_id)
    name = args[2] if len(args) >= 3 else kwargs.get("name")
    keys = args[3] if len(args) >= 4 else kwargs.get("rating_keys")
    assert name == "Composer · Pre-Workout"
    assert set(keys) == {"300", "301"}

    # New Vibe row + TrackVibe rows created.
    from app.models.vibe import Vibe, TrackVibe
    with Session(test_engine) as s:
        v = s.exec(select(Vibe).where(Vibe.name == "Pre-Workout")).first()
        assert v is not None
        rows = s.exec(
            select(TrackVibe).where(TrackVibe.vibe_id == v.id)
        ).all()
        assert len(rows) == 2
        for r in rows:
            assert r.assigned_by == "cluster"


# ===========================================================================
# Test 6: split_from — archive source + 2 new vibes.
# ===========================================================================
def test_recluster_commit_handles_split_action(
    client_with_phase6, test_engine, patched_plex_helpers
):
    with Session(test_engine) as s:
        v1_id = _seed_vibe(s, "Workout", plex_pl_key="pl-w")
        _seed_track(s, rk=400)
        _seed_track(s, rk=401)
        # Two split proposals point at the same source vibe.
        proposals = [
            _make_proposal(
                "Cardio", "split_from",
                source_vibe_ids=[v1_id], seed_track_indices=[0],
            ),
            _make_proposal(
                "Lifting", "split_from",
                source_vibe_ids=[v1_id], seed_track_indices=[1],
            ),
        ]
        ps = _make_proposal_set(proposals, ["400", "401"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # Source archived ONCE (idempotency on the split source — not twice).
    assert patched_plex_helpers["archive"].await_count == 1
    # Two new playlists created.
    assert patched_plex_helpers["create"].await_count == 2

    from app.models.vibe import Vibe
    with Session(test_engine) as s:
        cardio = s.exec(select(Vibe).where(Vibe.name == "Cardio")).first()
        lifting = s.exec(select(Vibe).where(Vibe.name == "Lifting")).first()
        assert cardio is not None
        assert lifting is not None
        # Source dropped.
        src = s.exec(select(Vibe).where(Vibe.id == v1_id)).first()
        assert src.is_active is False or src.is_active == 0


# ===========================================================================
# Test 7: merged_from — archive both sources + 1 new vibe.
# ===========================================================================
def test_recluster_commit_handles_merged_action(
    client_with_phase6, test_engine, patched_plex_helpers
):
    with Session(test_engine) as s:
        v1_id = _seed_vibe(s, "Late Night", plex_pl_key="pl-l")
        v2_id = _seed_vibe(s, "Chill", plex_pl_key="pl-c")
        _seed_track(s, rk=500)
        _seed_track(s, rk=501)
        proposals = [
            _make_proposal(
                "Late Night Drives", "merged_from",
                source_vibe_ids=[v1_id, v2_id],
                seed_track_indices=[0, 1],
            ),
        ]
        ps = _make_proposal_set(proposals, ["500", "501"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # Both sources archived.
    assert patched_plex_helpers["archive"].await_count == 2
    # One new playlist.
    assert patched_plex_helpers["create"].await_count == 1

    from app.models.vibe import Vibe
    with Session(test_engine) as s:
        new = s.exec(select(Vibe).where(Vibe.name == "Late Night Drives")).first()
        assert new is not None
        assert new.is_active is True or new.is_active == 1
        for src_id in (v1_id, v2_id):
            src = s.exec(select(Vibe).where(Vibe.id == src_id)).first()
            assert src.is_active is False or src.is_active == 0


# ===========================================================================
# Test 8: manual override survives a renamed_from (D-22 source_vibe_ids fallback).
# ===========================================================================
def test_recluster_commit_preserves_manual_override_via_name_match(
    client_with_phase6, test_engine, patched_plex_helpers
):
    """D-22: when a vibe is renamed in place, the manual TrackVibe row
    survives via the source_vibe_id mapping."""
    with Session(test_engine) as s:
        v1_id = _seed_vibe(s, "Late Night", plex_pl_key="pl-1")
        t_id = _seed_track_id(s, rk=600)
        # Pre-existing manual TrackVibe.
        from app.models.vibe import TrackVibe
        s.add(TrackVibe(
            track_id=t_id, vibe_id=v1_id, distance=0.05,
            assigned_at=datetime.now(timezone.utc).isoformat(),
            assigned_by="manual",
        ))
        s.commit()
        proposals = [
            _make_proposal(
                "Night Drives", "renamed_from",
                source_vibe_ids=[v1_id], seed_track_indices=[0],
            ),
        ]
        ps = _make_proposal_set(proposals, ["600"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # The manual TrackVibe row should still exist (renamed in place).
    from app.models.vibe import TrackVibe
    with Session(test_engine) as s:
        rows = s.exec(
            select(TrackVibe).where(
                TrackVibe.track_id == t_id, TrackVibe.assigned_by == "manual"
            )
        ).all()
        assert len(rows) == 1, "manual override lost on renamed_from action"


# ===========================================================================
# Test 9: dropped vibe with manual override -> SlotInLog 'manual_override_lost'.
# ===========================================================================
def test_recluster_commit_logs_lost_manual_override(
    client_with_phase6, test_engine, patched_plex_helpers
):
    """D-22: when a vibe is dropped without a successor, manual overrides on
    that vibe are surfaced via a SlotInLog row with action='manual_override_lost'."""
    with Session(test_engine) as s:
        v1_id = _seed_vibe(s, "Lost Vibe", plex_pl_key="pl-x")
        t_id = _seed_track_id(s, rk=700)
        from app.models.vibe import TrackVibe
        s.add(TrackVibe(
            track_id=t_id, vibe_id=v1_id, distance=0.1,
            assigned_at=datetime.now(timezone.utc).isoformat(),
            assigned_by="manual",
        ))
        s.commit()
        proposals = [
            _make_proposal(
                "Lost Vibe", "dropped",
                source_vibe_ids=[v1_id], seed_track_indices=[],
            ),
        ]
        ps = _make_proposal_set(proposals, ["700"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    from app.models.vibe import SlotInLog
    with Session(test_engine) as s:
        rows = s.exec(
            select(SlotInLog).where(SlotInLog.action == "manual_override_lost")
        ).all()
        assert len(rows) == 1, (
            f"expected 1 manual_override_lost SlotInLog row; got {len(rows)}"
        )
        row = rows[0]
        assert row.track_id == t_id
        assert "Lost Vibe" in (row.note or "")


# ===========================================================================
# Test 10: idempotent on partial Plex API failure (D-23).
# ===========================================================================
def test_recluster_commit_idempotent_on_plex_failure(
    client_with_phase6, test_engine, monkeypatch
):
    """D-23: when create_playlist raises for one proposal, the rest still
    commit; _recluster_status records the failure."""
    from unittest.mock import AsyncMock

    call_count = {"create": 0}

    async def flaky_create(*args, **kwargs):
        call_count["create"] += 1
        if call_count["create"] == 2:
            raise RuntimeError("simulated Plex 502")
        return f"new-pl-key-{call_count['create']}"

    monkeypatch.setattr("app.routers.api_vibes.create_playlist", flaky_create)
    monkeypatch.setattr(
        "app.routers.api_vibes.update_playlist_items", AsyncMock()
    )
    monkeypatch.setattr(
        "app.routers.api_vibes.archive_playlist", AsyncMock()
    )
    monkeypatch.setattr(
        "app.routers.api_vibes.rename_playlist", AsyncMock()
    )

    with Session(test_engine) as s:
        for i in range(3):
            _seed_track(s, rk=800 + i)
        proposals = [
            _make_proposal(f"V{i}", "new", seed_track_indices=[i])
            for i in range(3)
        ]
        ps = _make_proposal_set(proposals, ["800", "801", "802"])
        _setup_state_with_proposals(s, ps)

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # 3 attempts at create_playlist; 2nd raises but 3rd still proceeds.
    assert call_count["create"] == 3

    from app.routers import api_vibes
    assert api_vibes._recluster_status.state == "failed"
    assert api_vibes._recluster_status.last_error is not None
    assert "Plex 502" in api_vibes._recluster_status.last_error


# ===========================================================================
# Test 11: re-runnable after partial failure (D-23 idempotency by name).
# ===========================================================================
def test_recluster_commit_re_runnable_after_partial_failure(
    client_with_phase6, test_engine, monkeypatch
):
    """D-23: after a partial failure, a second commit pass picks up where the
    first left off — successful proposals are skipped via name-match short-circuit."""
    from unittest.mock import AsyncMock

    create_mock = AsyncMock(return_value="new-pl-key-r")
    monkeypatch.setattr("app.routers.api_vibes.create_playlist", create_mock)
    monkeypatch.setattr(
        "app.routers.api_vibes.update_playlist_items", AsyncMock()
    )
    monkeypatch.setattr(
        "app.routers.api_vibes.archive_playlist", AsyncMock()
    )
    monkeypatch.setattr(
        "app.routers.api_vibes.rename_playlist", AsyncMock()
    )

    with Session(test_engine) as s:
        from app.models.vibe import Vibe
        # Pre-seed: V0 was created in a prior pass (simulates partial success).
        _seed_track(s, rk=900)
        _seed_track(s, rk=901)
        s.add(Vibe(
            name="V0", description="d",
            created_at=datetime.now(timezone.utc).isoformat(),
            is_active=True,
        ))
        s.commit()
        proposals = [
            _make_proposal("V0", "new", seed_track_indices=[0]),
            _make_proposal("V1", "new", seed_track_indices=[1]),
        ]
        ps = _make_proposal_set(proposals, ["900", "901"])

        # Set recluster_mode=True (the previous commit cleared it; tests must
        # re-prime it because retries explicitly re-enter recluster mode in
        # production flow via /api/vibes/recluster/start).
        from app.models.vibe import SetupState
        existing = s.exec(select(SetupState).where(SetupState.id == 1)).first()
        if existing is None:
            existing = SetupState(id=1)
        existing.recluster_mode = True
        existing.step = "proposing"
        existing.draft_proposals_json = ps.model_dump_json()
        s.add(existing)
        s.commit()

    resp = client_with_phase6.post("/api/vibes/recluster/commit")
    assert resp.status_code == 200

    # V0 already exists -> create_playlist NOT called for it. Only V1 created.
    assert create_mock.await_count == 1
    args, kwargs = create_mock.await_args
    name = args[2] if len(args) >= 3 else kwargs.get("name")
    assert name == "Composer · V1"


# ===========================================================================
# Test 12: api_setup propose/init branches purpose on recluster_mode.
# ===========================================================================
def test_post_setup_propose_init_records_recluster_purpose(
    client_with_phase6, test_engine, monkeypatch
):
    """When SetupState.recluster_mode=True, propose/init still records the
    LAST llm call id reflecting any vibe_clustering_* purpose. Phase 6.1
    rewires the endpoint to call map_user_vibes_to_clusters (Plan 01) with
    the user-typed names from the textbox-stack form — but the recluster_mode
    plumbing through last_llm_call_id is independent of the LLM dispatcher.
    """
    from app.models.vibe import SetupState
    from app.models.llm_usage import LLMUsage
    from app.services.vibe_clusterer import VibeProposalSet, VibeProposal

    async def fake_map(names):
        return VibeProposalSet(
            proposals=[
                VibeProposal(
                    name=names[0] if names else "X", description="d",
                    action="new", seed_track_indices=[0],
                    seed_tracks=[], members=[], member_count=1,
                    fit="strong",
                ),
                VibeProposal(
                    name=names[1] if len(names) > 1 else "Y", description="d",
                    action="new", seed_track_indices=[1],
                    seed_tracks=[], members=[], member_count=1,
                    fit="strong",
                ),
                VibeProposal(
                    name=names[2] if len(names) > 2 else "Z", description="d",
                    action="new", seed_track_indices=[2],
                    seed_tracks=[], members=[], member_count=1,
                    fit="strong",
                ),
            ],
            rated_track_count=10,
            rated_track_index_map=[{"index": i, "rating_key": str(100 + i)}
                                   for i in range(3)],
        )

    monkeypatch.setattr(
        "app.routers.api_setup.map_user_vibes_to_clusters", fake_map
    )

    # Prime SetupState recluster_mode + a fake LLMUsage row that we can
    # surface as last_llm_call_id.
    with Session(test_engine) as s:
        existing = s.exec(select(SetupState).where(SetupState.id == 1)).first()
        if existing is None:
            existing = SetupState(id=1)
        existing.recluster_mode = True
        existing.step = "proposing"
        existing.draft_proposals_json = ""
        s.add(existing)
        s.add(LLMUsage(
            called_at=datetime.now(timezone.utc).isoformat(),
            model="claude-sonnet-4-5",
            input_tokens=10,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=20,
            cost_estimate_usd=0.001,
            purpose="vibe_clustering_recluster",
        ))
        s.commit()

    resp = client_with_phase6.post(
        "/api/setup/propose/init",
        data={"vibe_names": '["A", "B", "C"]'},
    )
    assert resp.status_code == 200

    # SetupState.last_llm_call_id should pick up the most recent
    # vibe_clustering_* row (which is the recluster row we just inserted).
    with Session(test_engine) as s:
        state = s.exec(select(SetupState).where(SetupState.id == 1)).first()
        last = s.exec(
            select(LLMUsage)
            .where(
                LLMUsage.purpose.in_(  # type: ignore[union-attr]
                    [
                        "vibe_clustering_initial",
                        "vibe_clustering_refine",
                        "vibe_clustering_recluster",
                    ]
                )
            )
            .order_by(LLMUsage.id.desc())  # type: ignore[union-attr]
        ).first()
        assert state.last_llm_call_id == last.id
