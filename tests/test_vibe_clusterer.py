"""Phase 6 Plan 02 vibe_clusterer tests + Plan 01 sklearn allowlist tripwire.

The Plan 01 AST allowlist test (``test_sklearn_only_in_clusterer_module``) is
preserved verbatim — Plan 02 adds ``from sklearn.cluster import KMeans`` and
``from sklearn.metrics import silhouette_score`` to ``vibe_clusterer.py``,
which the allowlist permits. Any sklearn leak into another service file fails
the test (D-33).

Plan 02 adds 8 behavioral tests covering:
- Cold-start gate (n_rated < 30 → degraded_mode, no LLM call) — Pitfall 3 / D-10.
- Silhouette-optimal k pick — Pitfall 3 / VIBE-05.
- forced_k override — D-13.
- Z-score normalization (tempo's [60,180] vs others' [0,1]) — D-05.
- Out-of-range seed_track_indices retry-once-then-raise — Pitfall 10 / D-03.
- purpose= flag on AnthropicClient call (initial / refine / recluster) — D-34.
- materialize_clusters fills centroid + spread + silhouette per proposal.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from sqlmodel import Session, SQLModel, select


SERVICES_DIR = Path(__file__).parent.parent / "app" / "services"
ALLOWLIST_RE = re.compile(r"^(vibe_clusterer\.py|clustering.*\.py|clusterer.*\.py)$")


# ---------------------------------------------------------------------------
# Plan 01 AST allowlist test — preserved verbatim.
# ---------------------------------------------------------------------------
def test_sklearn_only_in_clusterer_module():
    """Every app/services/*.py file is sklearn-free unless it's on the allowlist."""
    violations: list[str] = []
    for py_file in SERVICES_DIR.glob("*.py"):
        if ALLOWLIST_RE.match(py_file.name):
            continue
        source = py_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "sklearn" in alias.name:
                        violations.append(f"{py_file.name}: import {alias.name}")
            if isinstance(node, ast.ImportFrom):
                if node.module and "sklearn" in node.module:
                    violations.append(f"{py_file.name}: from {node.module}")
    assert violations == [], (
        "sklearn imports outside vibe_clusterer.py allowlist:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Plan 02 behavioral tests — fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db_with_phase6(test_engine) -> Generator[Session, None, None]:
    """Create all Phase 5 + Phase 6 tables and yield a session.

    Mirrors tests/test_database_phase6.py::db_with_phase6 (Plan 01 fixture).
    """
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    # Phase 6
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        SetupState,
        TrackVibe,
        Vibe,
    )

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


def _seed_tracks(session: Session, count: int, *, well_separated: bool = False) -> None:
    """Seed `count` rated tracks. If well_separated, build 3 Gaussian-separated clusters."""
    from app.models.track import Track

    rng = np.random.RandomState(42)
    if well_separated and count >= 30:
        # Build 3 well-separated 4-D clusters of count//3 points each.
        per = count // 3
        centers = np.array(
            [
                [0.20, 80.0, 0.20, 0.20],   # low-energy slow chill
                [0.80, 140.0, 0.70, 0.70],  # high-energy fast happy
                [0.50, 110.0, 0.50, 0.50],  # mid-energy mid-tempo neutral
            ]
        )
        scales = np.array([0.05, 5.0, 0.05, 0.05])
        i = 0
        for c_idx, center in enumerate(centers):
            for _ in range(per):
                pt = center + rng.randn(4) * scales
                session.add(
                    Track(
                        plex_rating_key=f"clu-{c_idx}-{i}",
                        title=f"Track {i}",
                        artist=f"Artist {c_idx}",
                        album="A",
                        user_rating=8.0,
                        energy=float(np.clip(pt[0], 0.0, 1.0)),
                        tempo=float(np.clip(pt[1], 60.0, 180.0)),
                        danceability=float(np.clip(pt[2], 0.0, 1.0)),
                        valence=float(np.clip(pt[3], 0.0, 1.0)),
                    )
                )
                i += 1
        # Fill remainder with random points (count - 3*per)
        for j in range(i, count):
            session.add(
                Track(
                    plex_rating_key=f"rem-{j}",
                    title=f"Track {j}",
                    artist="ArtistR",
                    album="A",
                    user_rating=8.0,
                    energy=float(rng.uniform(0.0, 1.0)),
                    tempo=float(rng.uniform(60.0, 180.0)),
                    danceability=float(rng.uniform(0.0, 1.0)),
                    valence=float(rng.uniform(0.0, 1.0)),
                )
            )
    else:
        for j in range(count):
            session.add(
                Track(
                    plex_rating_key=f"t-{j}",
                    title=f"Track {j}",
                    artist=f"Artist {j % 4}",
                    album="A",
                    user_rating=8.0,
                    energy=float(rng.uniform(0.0, 1.0)),
                    tempo=float(rng.uniform(60.0, 180.0)),
                    danceability=float(rng.uniform(0.0, 1.0)),
                    valence=float(rng.uniform(0.0, 1.0)),
                )
            )
    session.commit()


def _make_proposal_set(n_proposals: int, n_rated: int):
    """Build a canned VibeProposalSetLLMResponse with valid integer indices.

    Returns the slim LLM-side shape: each proposal is :class:`LLMVibeProposal`
    (permissive on ``source_vibe_ids``, accepting ``List[Union[int, str]]``)
    post quick-260510-i1q. The server canonicalizes to :class:`VibeProposal`
    inside ``_call_llm_with_validation`` from this slim response + aggregate /
    clustering parameters.

    Use :func:`_make_full_proposal_set` when you need a full
    :class:`VibeProposalSet` (e.g. as the ``prior`` argument to
    ``refine_proposals``).
    """
    from app.services.vibe_clusterer import (
        LLMVibeProposal,
        VibeProposalSetLLMResponse,
    )

    proposals = []
    chunk = max(1, n_rated // n_proposals)
    for i in range(n_proposals):
        start = i * chunk
        end = min(n_rated, start + chunk)
        proposals.append(
            LLMVibeProposal(
                name=f"Vibe {i+1}",
                description=f"Description {i+1}",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(start, end)),
            )
        )
    return VibeProposalSetLLMResponse(proposals=proposals)


def _make_full_proposal_set(n_proposals: int, n_rated: int):
    """Build a full VibeProposalSet — used as the ``prior`` arg to refine_proposals.

    The slim :class:`VibeProposalSetLLMResponse` from :func:`_make_proposal_set`
    is the *mock return value* shape; the *prior* shape passed into
    ``refine_proposals`` is still the full :class:`VibeProposalSet` (with
    server-controlled fields like ``forced_k`` and ``degraded_mode`` populated)
    and its proposals are the strict canonical :class:`VibeProposal`.
    """
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet

    slim = _make_proposal_set(n_proposals, n_rated)
    # Canonicalize slim → strict VibeProposal for the prior side. The slim
    # helper builds with empty source_vibe_ids and integer seed indices, so the
    # conversion is lossless here.
    canonical = [
        VibeProposal(
            name=p.name,
            description=p.description,
            action=p.action,
            source_vibe_ids=[],
            seed_track_indices=list(p.seed_track_indices),
        )
        for p in slim.proposals
    ]
    return VibeProposalSet(
        proposals=canonical,
        rated_track_count=n_rated,
    )


# ---------------------------------------------------------------------------
# Test 2: degraded mode below n_rated < 30
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initial_proposal_returns_degraded_mode_below_30(db_with_phase6, monkeypatch):
    """n_rated=25 → degraded_mode=True, single Your Taste proposal, no LLM call."""
    _seed_tracks(db_with_phase6, 25)

    fake_factory = MagicMock()
    fake_factory.call_with_structured_output = AsyncMock()
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_factory,
    )

    from app.services.vibe_clusterer import initial_cluster_proposal

    result = await initial_cluster_proposal()

    assert result.degraded_mode is True
    assert len(result.proposals) == 1
    assert result.proposals[0].name == "Your Taste"
    assert result.proposals[0].action == "new"
    # All 25 indices land in the single proposal.
    assert len(result.proposals[0].seed_track_indices) == 25
    assert result.proposals[0].seed_track_indices == list(range(25))
    assert result.silhouette_avg is None
    # LLM was NEVER called.
    assert fake_factory.call_with_structured_output.call_count == 0


# ---------------------------------------------------------------------------
# Test 3: silhouette-optimal k pick on 60 well-separated points
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initial_proposal_picks_silhouette_optimal_k(db_with_phase6, monkeypatch):
    """60 well-separated 3-cluster points → 3 proposals, sil ≥ 0.25, degraded=False."""
    _seed_tracks(db_with_phase6, 60, well_separated=True)

    canned = _make_proposal_set(3, 60)
    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=canned)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import initial_cluster_proposal

    result = await initial_cluster_proposal()

    assert len(result.proposals) == 3
    assert result.degraded_mode is False
    assert result.silhouette_avg is not None
    assert result.silhouette_avg >= 0.25
    assert result.forced_k is None
    fake_client.call_with_structured_output.assert_awaited_once()


# ---------------------------------------------------------------------------
# Regression: production 500 — slim LLM response succeeds end-to-end
# (quick-260510-das)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initial_proposal_succeeds_with_slim_llm_response(
    db_with_phase6, monkeypatch
):
    """Regression: prod 500 on POST /api/setup/propose/init.

    Before the fix, the LLM was asked to return rated_track_index_map; Claude
    returned `{}` (empty dict for empty collection — known JSON shape ambiguity),
    failing Pydantic validation against `List[dict]` at anthropic_client.py:128.

    After the fix, the LLM is constrained to VibeProposalSetLLMResponse
    (proposals only), and the server assembles VibeProposalSet from the LLM
    response + aggregate data. Validation cannot fail on a server-controlled
    field because the LLM never sees it.
    """
    _seed_tracks(db_with_phase6, count=60, well_separated=True)

    # Build a slim LLM response — the new contract.
    from app.services.vibe_clusterer import (
        LLMVibeProposal,
        VibeProposalSet,
        VibeProposalSetLLMResponse,
    )
    canned = VibeProposalSetLLMResponse(
        proposals=[
            LLMVibeProposal(
                name=f"Vibe {i+1}",
                description=f"Description {i+1}",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(i * 20, (i + 1) * 20)),
            )
            for i in range(3)
        ],
    )

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=canned)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda session: fake_client,
    )

    from app.services.vibe_clusterer import initial_cluster_proposal
    result = await initial_cluster_proposal()

    # Server populated the fields the LLM no longer sees.
    assert isinstance(result, VibeProposalSet)
    assert result.rated_track_count == 60
    assert isinstance(result.rated_track_index_map, list)
    assert len(result.rated_track_index_map) == 60
    assert result.degraded_mode is False  # n_rated >= 30, well-separated
    assert len(result.proposals) == 3

    # Confirm the LLM was asked for the slim model, NOT the full one.
    kwargs = fake_client.call_with_structured_output.await_args.kwargs
    assert kwargs["response_model"] is VibeProposalSetLLMResponse


# ---------------------------------------------------------------------------
# Defensive: slim LLM contract MUST NOT regress to include server fields
# (quick-260510-das)
# ---------------------------------------------------------------------------
def test_slim_llm_response_schema_has_no_rated_track_index_map_field():
    """Defensive: the slim LLM contract MUST NOT contain rated_track_index_map.

    If a future refactor accidentally re-adds it, this test fails immediately —
    preventing the production 500 from regressing.
    """
    from app.services.vibe_clusterer import VibeProposalSetLLMResponse

    fields = VibeProposalSetLLMResponse.model_fields
    assert set(fields.keys()) == {"proposals"}, (
        f"VibeProposalSetLLMResponse must contain ONLY `proposals`; "
        f"got {set(fields.keys())}. Adding server-controlled fields here "
        "re-opens the prod bug where Claude returns "
        "`rated_track_index_map: {}` and Pydantic validation fails."
    )


# ---------------------------------------------------------------------------
# Test 4: forced_k override
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initial_proposal_honors_forced_k(db_with_phase6, monkeypatch):
    """forced_k=5 → k-means runs at k=5; LLM returns 5 proposals; result.forced_k == 5."""
    _seed_tracks(db_with_phase6, 60, well_separated=True)

    canned = _make_proposal_set(5, 60)
    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=canned)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import initial_cluster_proposal

    result = await initial_cluster_proposal(forced_k=5)

    assert result.forced_k == 5
    assert len(result.proposals) == 5
    fake_client.call_with_structured_output.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 5: z-score normalization helper
# ---------------------------------------------------------------------------
def test_initial_proposal_zscore_normalizes_tempo():
    """Per-dimension mean ≈ 0, std ≈ 1 after _z_score_normalize."""
    from app.services.vibe_clusterer import _z_score_normalize

    rng = np.random.RandomState(7)
    n = 100
    matrix = np.column_stack(
        [
            rng.uniform(0.0, 1.0, n),       # energy
            rng.uniform(60.0, 180.0, n),    # tempo
            rng.uniform(0.0, 1.0, n),       # danceability
            rng.uniform(0.0, 1.0, n),       # valence
        ]
    )
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)

    normalized = _z_score_normalize(matrix, mean, std)

    # Per-dimension mean ≈ 0
    assert np.allclose(normalized.mean(axis=0), np.zeros(4), atol=1e-6)
    # Per-dimension std ≈ 1
    assert np.allclose(normalized.std(axis=0), np.ones(4), atol=1e-3)


# ---------------------------------------------------------------------------
# Test 6: out-of-range seed_track_indices → retry once → raise
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refine_proposals_validates_seed_indices_in_range(db_with_phase6, monkeypatch):
    """LLM returns out-of-range index → retry once → second failure raises ValueError."""
    _seed_tracks(db_with_phase6, 60, well_separated=True)

    from app.services.vibe_clusterer import (
        LLMVibeProposal,
        VibeProposalSetLLMResponse,
    )

    # Build a bad proposal: index 60 is out-of-range for n_rated=60 (valid: 0..59).
    # NOTE: mock returns the slim LLM-side model (LLMVibeProposal post
    # quick-260510-i1q).
    bad_set = VibeProposalSetLLMResponse(
        proposals=[
            LLMVibeProposal(
                name="Bad",
                description="d",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=[60],  # out-of-range
            )
        ],
    )
    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=[bad_set, bad_set])
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import refine_proposals

    prior = _make_full_proposal_set(3, 60)
    with pytest.raises(ValueError, match="out of range"):
        await refine_proposals(prior, "do something bad")
    # The mock was called TWICE (initial + 1 retry).
    assert fake_client.call_with_structured_output.await_count == 2


# ---------------------------------------------------------------------------
# Test 7: refine vs recluster purpose flag
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refine_proposals_uses_recluster_purpose_when_flag_set(db_with_phase6, monkeypatch):
    """recluster_mode=True → purpose='vibe_clustering_recluster'; False → '...refine'."""
    _seed_tracks(db_with_phase6, 60, well_separated=True)

    canned = _make_proposal_set(3, 60)
    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=canned)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import refine_proposals

    prior = _make_full_proposal_set(3, 60)

    # Refine mode: purpose=vibe_clustering_refine
    await refine_proposals(prior, "merge two", recluster_mode=False)
    kw1 = fake_client.call_with_structured_output.await_args.kwargs
    assert kw1["purpose"] == "vibe_clustering_refine"

    # Recluster mode: purpose=vibe_clustering_recluster
    fake_client.call_with_structured_output.reset_mock()
    fake_client.call_with_structured_output = AsyncMock(return_value=canned)
    await refine_proposals(prior, "redo clusters", recluster_mode=True)
    kw2 = fake_client.call_with_structured_output.await_args.kwargs
    assert kw2["purpose"] == "vibe_clustering_recluster"


# ---------------------------------------------------------------------------
# Test 8: defense in depth — n_rated < 30 never calls LLM (forced_k irrelevant)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initial_proposal_aborts_below_n_30_no_llm(db_with_phase6, monkeypatch):
    """n_rated=29 → degraded_mode=True; LLM mock NEVER called even on forced_k."""
    _seed_tracks(db_with_phase6, 29)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock()
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import initial_cluster_proposal

    result = await initial_cluster_proposal()
    assert result.degraded_mode is True
    assert fake_client.call_with_structured_output.call_count == 0


# ---------------------------------------------------------------------------
# Test 9: materialize_clusters fills centroid + spread + silhouette
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_materialize_clusters_assigns_centroids_and_spreads(db_with_phase6, monkeypatch):
    """initial_cluster_proposal returns proposals with centroid/spread/silhouette filled in."""
    _seed_tracks(db_with_phase6, 60, well_separated=True)

    canned = _make_proposal_set(3, 60)
    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=canned)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import initial_cluster_proposal

    result = await initial_cluster_proposal()

    assert len(result.proposals) == 3
    for prop in result.proposals:
        assert prop.centroid is not None
        assert isinstance(prop.centroid, dict)
        assert set(prop.centroid.keys()) == {"energy", "tempo", "danceability", "valence"}
        assert prop.spread is not None
        assert set(prop.spread.keys()) == {"energy", "tempo", "danceability", "valence"}
        assert prop.silhouette is not None
        assert isinstance(prop.silhouette, float)


# ---------------------------------------------------------------------------
# quick-260510-i1q regression tests: LLM returning vibe names (strings) in
# source_vibe_ids must be resolved to positional integer indices, not crash
# Pydantic validation.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refine_resolves_string_source_vibe_ids_to_indices(
    db_with_phase6, monkeypatch
):
    """Live prod crash: LLM returns source_vibe_ids=['Neon Nights', 'Hyperdrive'].

    Expectation: server name-matches against prior_proposals and produces
    canonical source_vibe_ids=[0, 1] (positional indices).
    """
    _seed_tracks(db_with_phase6, 60, well_separated=True)

    from app.services.vibe_clusterer import (
        LLMVibeProposal,
        VibeProposal,
        VibeProposalSet,
        VibeProposalSetLLMResponse,
    )

    # Prior proposals as Claude would have seen them — names in canonical order.
    prior = VibeProposalSet(
        proposals=[
            VibeProposal(
                name="Neon Nights",
                description="late-night synthwave",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(0, 20)),
            ),
            VibeProposal(
                name="Hyperdrive",
                description="high-energy fast happy",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(20, 40)),
            ),
        ],
        rated_track_count=60,
    )

    # Mock LLM returns a merged_from proposal whose source_vibe_ids are NAMES,
    # not integers — exactly the prod failure shape.
    bad_slim = VibeProposalSetLLMResponse(
        proposals=[
            LLMVibeProposal(
                name="Neon Hyperdrive",
                description="merged the two",
                action="merged_from",
                source_vibe_ids=["Neon Nights", "Hyperdrive"],
                seed_track_indices=list(range(0, 40)),
            )
        ],
    )
    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=bad_slim)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import refine_proposals

    result = await refine_proposals(prior, "merge them")

    assert isinstance(result, VibeProposalSet)
    assert len(result.proposals) == 1
    canonical = result.proposals[0]
    # Strict canonical typed as List[int] — values resolved positionally.
    assert canonical.source_vibe_ids == [0, 1]
    assert all(isinstance(x, int) for x in canonical.source_vibe_ids)


@pytest.mark.asyncio
async def test_refine_drops_unresolvable_string_source_vibe_id(
    db_with_phase6, monkeypatch, caplog
):
    """Unmatched name strings are dropped with a warning; matched ones survive."""
    import logging as _logging

    _seed_tracks(db_with_phase6, 60, well_separated=True)

    from app.services.vibe_clusterer import (
        LLMVibeProposal,
        VibeProposal,
        VibeProposalSet,
        VibeProposalSetLLMResponse,
    )

    prior = VibeProposalSet(
        proposals=[
            VibeProposal(
                name="Hyperdrive",
                description="fast",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(0, 30)),
            ),
        ],
        rated_track_count=60,
    )

    bad_slim = VibeProposalSetLLMResponse(
        proposals=[
            LLMVibeProposal(
                name="Mystery Merge",
                description="weird merge",
                action="merged_from",
                # 'Nonexistent Vibe' has NO match in prior; 'Hyperdrive' matches index 0.
                source_vibe_ids=["Nonexistent Vibe", "Hyperdrive"],
                seed_track_indices=list(range(0, 30)),
            )
        ],
    )
    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=bad_slim)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda s: fake_client,
    )

    from app.services.vibe_clusterer import refine_proposals

    with caplog.at_level(_logging.WARNING, logger="app.services.vibe_clusterer"):
        result = await refine_proposals(prior, "merge them")

    assert len(result.proposals) == 1
    canonical = result.proposals[0]
    # Unresolvable string dropped; matched string → positional index 0.
    assert canonical.source_vibe_ids == [0]
    # The warning fired for the unresolvable entry.
    assert any(
        "unresolvable name" in rec.getMessage()
        and "'Nonexistent Vibe'" in rec.getMessage()
        for rec in caplog.records
    )


def test_refine_user_prompt_includes_positional_ids():
    """Refinement user prompt must inject 'id': N per prior proposal + an
    explicit instruction telling Claude to use the integer 'id' field.

    Without this anchor Claude falls back to the vibe name string as the most
    stable identifier visible in the prompt and crashes Pydantic on the strict
    canonical source_vibe_ids: List[int] schema (quick-260510-i1q prod crash).
    """
    from app.services.vibe_clusterer import (
        VibeProposal,
        VibeProposalSet,
        _build_clustering_user_prompt,
    )

    prior = VibeProposalSet(
        proposals=[
            VibeProposal(
                name="Neon Nights",
                description="late-night synthwave",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(0, 20)),
            ),
            VibeProposal(
                name="Hyperdrive",
                description="high-energy fast happy",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(20, 40)),
            ),
        ],
        rated_track_count=60,
    )

    prompt = _build_clustering_user_prompt(prior, "merge them")

    # Positional integer 'id' fields are present in the JSON dump.
    assert '"id": 0' in prompt
    assert '"id": 1' in prompt
    # Explicit instruction telling Claude to use the integer id (not the name).
    assert "use the integer" in prompt
    assert "'id'" in prompt
    assert "NOT the name string" in prompt


# ---------------------------------------------------------------------------
# Phase 6.1 Plan 01 Task 1 — schemas: LLMVibeFit, LLMVibeMappingResponse,
# VibeProposal extensions (fit, fit_reason, seed_tracks, members, member_count)
# ---------------------------------------------------------------------------
def test_llm_vibe_fit_validates_required_fields():
    from app.services.vibe_clusterer import LLMVibeFit
    # Strong fit, no reason — OK.
    f = LLMVibeFit(user_name="workout", cluster_index=0,
                  description="High energy cardio", fit="strong")
    assert f.fit == "strong"
    assert f.reason is None
    # no_match fit with reason — OK.
    f2 = LLMVibeFit(user_name="ambient", cluster_index=2,
                   description="No cluster matches", fit="no_match",
                   reason="Library has no ambient artists")
    assert f2.fit == "no_match"
    assert f2.reason == "Library has no ambient artists"


def test_llm_vibe_mapping_response_holds_list_of_fits():
    from app.services.vibe_clusterer import LLMVibeFit, LLMVibeMappingResponse
    m = LLMVibeMappingResponse(mappings=[
        LLMVibeFit(user_name="a", cluster_index=0, description="d", fit="strong"),
        LLMVibeFit(user_name="b", cluster_index=1, description="d", fit="weak"),
    ])
    assert len(m.mappings) == 2
    assert m.mappings[0].cluster_index == 0
    assert m.mappings[1].cluster_index == 1


def test_vibe_proposal_accepts_fit_and_fit_reason_fields():
    from app.services.vibe_clusterer import VibeProposal
    p = VibeProposal(name="x", description="y", action="new",
                    fit="weak", fit_reason="Only 12 tracks match")
    assert p.fit == "weak"
    assert p.fit_reason == "Only 12 tracks match"
    # Defaults to None when omitted.
    p2 = VibeProposal(name="x", description="y", action="new")
    assert p2.fit is None
    assert p2.fit_reason is None


def test_vibe_proposal_accepts_seed_tracks_members_member_count_fields():
    """Blocker #1: seed_tracks + members + member_count survive on VibeProposal
    and round-trip through model_dump_json / model_validate_json. This is the
    persistence path used by SetupState.draft_proposals_json in Plan 02.
    """
    from app.services.vibe_clusterer import VibeProposal, VibeProposalSet
    track = {"title": "Night Drive", "artist": "Kavinsky",
             "rating_key": "rk_42"}
    p = VibeProposal(
        name="late night",
        description="Synth-driven cruising",
        action="new",
        seed_tracks=[track],
        members=[track, {"title": "T2", "artist": "A2", "rating_key": "rk_43"}],
        member_count=2,
        fit="strong",
    )
    # Round-trip via VibeProposalSet.model_dump_json → model_validate_json.
    pset = VibeProposalSet(proposals=[p], rated_track_count=2)
    roundtripped = VibeProposalSet.model_validate_json(pset.model_dump_json())
    rp = roundtripped.proposals[0]
    assert rp.seed_tracks == [track]
    assert len(rp.members) == 2
    assert rp.members[0]["title"] == "Night Drive"
    assert rp.member_count == 2
    assert rp.fit == "strong"


def test_vibe_proposal_default_seed_tracks_and_members_are_empty_lists():
    """Defensive default: not None, but empty list — template default-filter
    relies on this shape (proposal.seed_tracks | default([])).
    """
    from app.services.vibe_clusterer import VibeProposal
    p = VibeProposal(name="x", description="y", action="new")
    assert p.seed_tracks == []
    assert p.members == []
    assert p.member_count is None


def test_slim_user_led_response_schema_only_has_mappings_field():
    """Defense-in-depth: LLMVibeMappingResponse stays narrow.
    Mirrors test_slim_llm_response_schema_has_no_rated_track_index_map_field.
    """
    from app.services.vibe_clusterer import LLMVibeMappingResponse
    fields = set(LLMVibeMappingResponse.model_fields.keys())
    assert fields == {"mappings"}, (
        f"LLMVibeMappingResponse must only have 'mappings'; got {fields}"
    )


# ---------------------------------------------------------------------------
# Phase 6.1 Plan 01 Task 2 — map_user_vibes_to_clusters + helpers
# ---------------------------------------------------------------------------
from unittest.mock import patch  # noqa: E402  (test-section-local import)


def test_aggregate_rated_set_includes_genre_per_track(test_engine, monkeypatch):
    """WARNING #2: tracks_for_prompt entries must carry Track.genre so
    per-cluster top-genres aggregation in map_user_vibes_to_clusters works.
    """
    from sqlmodel import Session, SQLModel
    from app.models.track import Track
    from app.services import vibe_clusterer as vc

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as s:
        for i in range(40):
            s.add(Track(
                plex_rating_key=f"rk_{i}", title=f"T{i}",
                artist=f"A{i % 3}", album="X",
                user_rating=8.0, energy=0.5, tempo=120.0,
                danceability=0.5, valence=0.5,
                genre=("synth-pop" if i % 2 == 0 else "synthwave"),
            ))
        s.commit()

    monkeypatch.setattr(vc, "get_engine", lambda: test_engine)
    agg = vc._aggregate_rated_set_sync()
    assert agg["rated_track_count"] == 40
    # Every prompt entry has a "genre" key (string).
    for tp in agg["tracks_for_prompt"]:
        assert "genre" in tp, "tracks_for_prompt entry missing 'genre' key"
        assert isinstance(tp["genre"], str)


@pytest.mark.asyncio
async def test_map_user_vibes_rejects_count_below_3():
    from app.services.vibe_clusterer import map_user_vibes_to_clusters
    with pytest.raises(ValueError, match="vibe count must be 3-7"):
        await map_user_vibes_to_clusters(["workout", "focus"])


@pytest.mark.asyncio
async def test_map_user_vibes_rejects_count_above_7():
    from app.services.vibe_clusterer import map_user_vibes_to_clusters
    with pytest.raises(ValueError, match="vibe count must be 3-7"):
        await map_user_vibes_to_clusters(
            ["a", "b", "c", "d", "e", "f", "g", "h"]
        )


@pytest.mark.asyncio
async def test_map_user_vibes_rejects_blank_names():
    from app.services.vibe_clusterer import map_user_vibes_to_clusters
    with pytest.raises(ValueError, match="must not be blank"):
        await map_user_vibes_to_clusters(["workout", "  ", "focus"])


@pytest.mark.asyncio
async def test_map_user_vibes_rejects_case_insensitive_duplicates():
    from app.services.vibe_clusterer import map_user_vibes_to_clusters
    with pytest.raises(ValueError, match="duplicate vibe names"):
        await map_user_vibes_to_clusters(["Workout", "workout", "focus"])


@pytest.mark.asyncio
async def test_map_user_vibes_cold_start_under_30():
    from app.services import vibe_clusterer as vc

    # Mock the aggregate to return n_rated=20 → cold-start raises before LLM.
    with patch.object(vc, "_aggregate_rated_set_sync") as mock_agg:
        mock_agg.return_value = {
            "rated_track_count": 20,
            "feature_matrix": vc.np.zeros((20, 4)),
            "rated_track_index_map": [],
            "tracks_for_prompt": [],
            "top_artists": [],
            "top_genres": [],
        }
        with pytest.raises(ValueError, match="cold-start"):
            await vc.map_user_vibes_to_clusters(["a", "b", "c"])


def test_validate_mapping_permutation_detects_duplicate_cluster_index():
    from app.services.vibe_clusterer import (
        LLMVibeFit,
        _validate_mapping_permutation,
    )
    mappings = [
        LLMVibeFit(user_name="a", cluster_index=0, description="d", fit="strong"),
        LLMVibeFit(user_name="b", cluster_index=0, description="d", fit="weak"),
        LLMVibeFit(user_name="c", cluster_index=1, description="d", fit="strong"),
    ]
    err = _validate_mapping_permutation(mappings, n=3)
    assert err is not None
    assert "used more than once" in err


def test_validate_mapping_permutation_detects_missing_reason_on_no_match():
    from app.services.vibe_clusterer import (
        LLMVibeFit,
        _validate_mapping_permutation,
    )
    mappings = [
        LLMVibeFit(user_name="a", cluster_index=0, description="d", fit="strong"),
        LLMVibeFit(user_name="b", cluster_index=1, description="d", fit="no_match"),
        LLMVibeFit(user_name="c", cluster_index=2, description="d", fit="strong"),
    ]
    err = _validate_mapping_permutation(mappings, n=3)
    assert err is not None
    assert "non-empty 'reason'" in err


def test_validate_mapping_permutation_passes_valid_response():
    from app.services.vibe_clusterer import (
        LLMVibeFit,
        _validate_mapping_permutation,
    )
    mappings = [
        LLMVibeFit(user_name="a", cluster_index=0, description="d", fit="strong"),
        LLMVibeFit(user_name="b", cluster_index=2, description="d", fit="weak"),
        LLMVibeFit(user_name="c", cluster_index=1, description="d", fit="no_match",
                  reason="no match"),
    ]
    assert _validate_mapping_permutation(mappings, n=3) is None


def test_user_led_user_prompt_includes_positional_ids_top_genres_and_explicit_instruction():
    """Combines positional-ID test (existing requirement) with WARNING #2:
    top_genres MUST be present in the prompt payload.
    """
    from app.services.vibe_clusterer import (
        _build_user_led_clustering_user_prompt,
    )
    names = ["workout", "focus", "synth heavy"]
    summaries = [
        {"cluster_index": 0, "centroid": {"energy": 0.8, "tempo": 130,
         "danceability": 0.7, "valence": 0.5}, "top_artists": ["A1"],
         "top_genres": ["edm", "house"], "closest_tracks": [], "member_count": 50},
        {"cluster_index": 1, "centroid": {"energy": 0.3, "tempo": 90,
         "danceability": 0.4, "valence": 0.3}, "top_artists": ["A2"],
         "top_genres": ["ambient"], "closest_tracks": [], "member_count": 40},
        {"cluster_index": 2, "centroid": {"energy": 0.6, "tempo": 110,
         "danceability": 0.6, "valence": 0.5}, "top_artists": ["A3"],
         "top_genres": ["synth-pop", "synthwave"], "closest_tracks": [],
         "member_count": 35},
    ]
    prompt = _build_user_led_clustering_user_prompt(names, summaries)
    # Positional cluster_index integers present.
    assert "\"cluster_index\": 0" in prompt
    assert "\"cluster_index\": 1" in prompt
    assert "\"cluster_index\": 2" in prompt
    # Explicit positional-ID instruction.
    assert "integer `cluster_index` field" in prompt
    # User names verbatim.
    assert "workout" in prompt
    assert "focus" in prompt
    assert "synth heavy" in prompt
    # WARNING #2 — top_genres serialized into the prompt.
    assert "top_genres" in prompt
    assert "synth-pop" in prompt
    assert "synthwave" in prompt


def test_user_led_user_prompt_overrides_proposals_wrapper_with_mappings():
    """Regression for D-NEW-01 (quick-260510-tng): the cached system prompt
    instructs the LLM to return ``{"proposals": [...]}`` for the server-led
    clustering call. The user-led mapping call shares that cached system
    prompt (1h TTL — do NOT invalidate) but needs the LLM to return
    ``{"mappings": [...]}`` matching ``LLMVibeMappingResponse``.

    The user-prompt builder MUST contain an explicit RESPONSE FORMAT OVERRIDE
    block naming the ``mappings`` wrapper, the target schema, the per-entry
    fields, and explicit negation of the ``proposals`` wrapper.
    """
    from app.services.vibe_clusterer import (
        _build_user_led_clustering_user_prompt,
    )
    names = ["workout", "focus"]
    summaries = [
        {"cluster_index": 0, "centroid": {"energy": 0.8, "tempo": 130,
         "danceability": 0.7, "valence": 0.5}, "top_artists": ["A1"],
         "top_genres": ["edm"], "closest_tracks": [], "member_count": 20},
        {"cluster_index": 1, "centroid": {"energy": 0.3, "tempo": 90,
         "danceability": 0.4, "valence": 0.3}, "top_artists": ["A2"],
         "top_genres": ["ambient"], "closest_tracks": [], "member_count": 15},
    ]
    prompt = _build_user_led_clustering_user_prompt(names, summaries)
    # 1. Target wrapper field name present.
    assert "mappings" in prompt
    # 2. Wrong wrapper field named (so it can be explicitly avoided).
    assert "proposals" in prompt
    # 3. Negation phrasing — both lowercase "do not use" intent and uppercase NOT.
    assert "do not" in prompt.lower()
    assert "NOT" in prompt
    # 4. Every LLMVibeFit field name appears verbatim.
    assert "user_name" in prompt
    assert "cluster_index" in prompt
    assert "description" in prompt
    assert "fit" in prompt
    assert "reason" in prompt
    # 5. Target schema named so the LLM sees it.
    assert "LLMVibeMappingResponse" in prompt


@pytest.mark.asyncio
async def test_map_user_vibes_server_populates_seed_track_indices_seed_tracks_members():
    """Combined invariant: LLM never picks members; server populates
    seed_track_indices, seed_tracks (5 closest, dicts), members (all, dicts),
    member_count.
    """
    from app.services import vibe_clusterer as vc

    # Build a synthetic 90-track rated set with 3 clear clusters in 4-D space.
    np_local = vc.np
    cluster_a = np_local.random.RandomState(0).normal(
        loc=[0.2, 80, 0.3, 0.2], scale=0.05, size=(30, 4)
    )
    cluster_b = np_local.random.RandomState(1).normal(
        loc=[0.8, 140, 0.7, 0.7], scale=0.05, size=(30, 4)
    )
    cluster_c = np_local.random.RandomState(2).normal(
        loc=[0.5, 100, 0.5, 0.5], scale=0.05, size=(30, 4)
    )
    feat = np_local.vstack([cluster_a, cluster_b, cluster_c])
    index_map = [{"index": i, "rating_key": f"rk_{i}",
                  "title": f"T{i}", "artist": f"A{i % 5}"}
                 for i in range(90)]
    tracks_for_prompt = [{"title": f"T{i}", "artist": f"A{i % 5}",
                          "energy": float(feat[i, 0]),
                          "tempo": float(feat[i, 1]),
                          "danceability": float(feat[i, 2]),
                          "valence": float(feat[i, 3]),
                          "rating": 8.0,
                          "genre": "synthwave" if i >= 60 else "rock"}
                         for i in range(90)]

    async def _fake_call(*, system_prompt, user_prompt, response_model, purpose):
        assert purpose == "vibe_clustering_user_led"
        return vc.LLMVibeMappingResponse(mappings=[
            vc.LLMVibeFit(user_name="workout", cluster_index=1,
                         description="High-energy", fit="strong"),
            vc.LLMVibeFit(user_name="focus", cluster_index=0,
                         description="Low energy", fit="strong"),
            vc.LLMVibeFit(user_name="synth heavy", cluster_index=2,
                         description="Synth-driven mids", fit="weak"),
        ])

    fake_client = AsyncMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake_call)

    # materialize_clusters re-queries DB → no-op for this unit test.
    with patch.object(vc, "_aggregate_rated_set_sync") as mock_agg, \
         patch.object(vc, "get_anthropic_client_v2",
                     return_value=fake_client), \
         patch.object(vc, "materialize_clusters",
                     side_effect=lambda p: p):
        mock_agg.return_value = {
            "rated_track_count": 90,
            "feature_matrix": feat,
            "rated_track_index_map": index_map,
            "tracks_for_prompt": tracks_for_prompt,
            "top_artists": [], "top_genres": [],
        }
        result = await vc.map_user_vibes_to_clusters(
            ["workout", "focus", "synth heavy"]
        )

    # 3 proposals, one per user name.
    assert len(result.proposals) == 3

    # k-means labels partition the rated set.
    total = sum(len(p.seed_track_indices) for p in result.proposals)
    assert total == 90, (
        f"k-means labels do not partition rated set: total={total} != 90"
    )

    # Blocker #1 regression — seed_tracks + members + member_count populated.
    for p in result.proposals:
        assert len(p.seed_track_indices) > 0
        # seed_tracks is the top-5-closest list of dicts (or fewer if cluster
        # has <5 members; in this fixture every cluster has 30 members).
        assert len(p.seed_tracks) == 5, (
            f"Proposal {p.name} seed_tracks should have 5 entries, "
            f"got {len(p.seed_tracks)}"
        )
        for st in p.seed_tracks:
            assert st["title"], f"seed_track missing title: {st}"
            assert st["artist"], f"seed_track missing artist: {st}"
            assert st["rating_key"], f"seed_track missing rating_key: {st}"
        # members has ALL cluster members.
        assert len(p.members) == len(p.seed_track_indices), (
            f"members ({len(p.members)}) must equal seed_track_indices "
            f"({len(p.seed_track_indices)})"
        )
        for mem in p.members:
            assert mem["title"]
            assert mem["artist"]
            assert mem["rating_key"]
        # member_count == len(members).
        assert p.member_count == len(p.members)

    # Fit grades round-trip from LLM response.
    fits = {p.name: p.fit for p in result.proposals}
    assert fits["workout"] == "strong"
    assert fits["focus"] == "strong"
    assert fits["synth heavy"] == "weak"


@pytest.mark.asyncio
async def test_map_user_vibes_preserves_user_name_casing_on_llm_lowercase_echo():
    """WARNING #1 — server resolves LLM-echoed names back to user's casing
    via case-insensitive lookup table.
    """
    from app.services import vibe_clusterer as vc

    np_local = vc.np
    feat = np_local.random.RandomState(0).normal(size=(45, 4))
    feat[:, 1] = feat[:, 1] * 20 + 100

    async def _fake_call(*, system_prompt, user_prompt, response_model, purpose):
        # LLM lowercases the echoed names.
        return vc.LLMVibeMappingResponse(mappings=[
            vc.LLMVibeFit(user_name="workout", cluster_index=0,
                         description="d", fit="strong"),
            vc.LLMVibeFit(user_name="late night drive", cluster_index=1,
                         description="d", fit="strong"),
            vc.LLMVibeFit(user_name="synth heavy", cluster_index=2,
                         description="d", fit="weak"),
        ])

    fake_client = AsyncMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake_call)

    with patch.object(vc, "_aggregate_rated_set_sync") as mock_agg, \
         patch.object(vc, "get_anthropic_client_v2",
                     return_value=fake_client), \
         patch.object(vc, "materialize_clusters",
                     side_effect=lambda p: p):
        mock_agg.return_value = {
            "rated_track_count": 45,
            "feature_matrix": feat,
            "rated_track_index_map": [
                {"index": i, "rating_key": f"rk_{i}",
                 "title": f"T{i}", "artist": f"A{i}"} for i in range(45)
            ],
            "tracks_for_prompt": [
                {"title": f"T{i}", "artist": f"A{i}",
                 "energy": float(feat[i, 0]), "tempo": float(feat[i, 1]),
                 "danceability": float(feat[i, 2]),
                 "valence": float(feat[i, 3]), "rating": 8.0,
                 "genre": ""}
                for i in range(45)
            ],
            "top_artists": [], "top_genres": [],
        }
        # User input is MIXED CASE; LLM echoes lowercase.
        result = await vc.map_user_vibes_to_clusters(
            ["Workout", "Late Night Drive", "Synth Heavy"]
        )

    # Names preserve user's ORIGINAL casing despite LLM lowercase echo.
    names = {p.name for p in result.proposals}
    assert names == {"Workout", "Late Night Drive", "Synth Heavy"}, (
        f"Names should preserve user casing; got {names}"
    )


@pytest.mark.asyncio
async def test_map_user_vibes_retries_once_on_permutation_failure_then_raises():
    """LLM returns duplicate cluster_index twice → ValueError after retry."""
    from app.services import vibe_clusterer as vc

    call_count = {"n": 0}

    async def _bad_call(*, system_prompt, user_prompt, response_model, purpose):
        call_count["n"] += 1
        # Return invalid (duplicate cluster_index) every time.
        return vc.LLMVibeMappingResponse(mappings=[
            vc.LLMVibeFit(user_name="a", cluster_index=0, description="d", fit="strong"),
            vc.LLMVibeFit(user_name="b", cluster_index=0, description="d", fit="weak"),
            vc.LLMVibeFit(user_name="c", cluster_index=2, description="d", fit="strong"),
        ])

    fake_client = AsyncMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_bad_call)

    np_local = vc.np
    feat = np_local.random.RandomState(0).normal(size=(45, 4))
    feat[:, 1] = feat[:, 1] * 20 + 100

    with patch.object(vc, "_aggregate_rated_set_sync") as mock_agg, \
         patch.object(vc, "get_anthropic_client_v2",
                     return_value=fake_client), \
         patch.object(vc, "materialize_clusters",
                     side_effect=lambda p: p):
        mock_agg.return_value = {
            "rated_track_count": 45,
            "feature_matrix": feat,
            "rated_track_index_map": [
                {"index": i, "rating_key": f"rk_{i}",
                 "title": f"T{i}", "artist": f"A{i}"} for i in range(45)
            ],
            "tracks_for_prompt": [
                {"title": f"T{i}", "artist": f"A{i}",
                 "energy": float(feat[i, 0]), "tempo": float(feat[i, 1]),
                 "danceability": float(feat[i, 2]),
                 "valence": float(feat[i, 3]), "rating": 8.0,
                 "genre": ""}
                for i in range(45)
            ],
            "top_artists": [], "top_genres": [],
        }
        with pytest.raises(ValueError, match="failed permutation validation"):
            await vc.map_user_vibes_to_clusters(["a", "b", "c"])

    # Verified: exactly 2 LLM calls — initial + ONE retry.
    assert call_count["n"] == 2


def test_carryover_fit_from_prior_matches_case_insensitive_name():
    """Blocker #5 Option A — refine round-trip preserves fit/fit_reason."""
    from app.services.vibe_clusterer import (
        VibeProposal, _carryover_fit_from_prior,
    )
    priors = [
        VibeProposal(name="Workout", description="d", action="keep",
                    fit="strong", fit_reason=None),
        VibeProposal(name="Focus", description="d", action="keep",
                    fit="weak", fit_reason="Only 12 tracks match"),
        VibeProposal(name="Late Night", description="d", action="keep",
                    fit=None),
    ]
    # Refined proposal echoes name in different case — should match.
    new1 = VibeProposal(name="workout", description="new desc", action="keep")
    merged1 = _carryover_fit_from_prior(new1, priors)
    assert merged1.fit == "strong"

    new2 = VibeProposal(name="FOCUS", description="new desc", action="keep")
    merged2 = _carryover_fit_from_prior(new2, priors)
    assert merged2.fit == "weak"
    assert merged2.fit_reason == "Only 12 tracks match"

    # Brand-new proposal (action="new") — no prior match — fit stays None.
    new3 = VibeProposal(name="brand new", description="d", action="new")
    merged3 = _carryover_fit_from_prior(new3, priors)
    assert merged3.fit is None

    # Prior had fit=None — nothing to carry over.
    new4 = VibeProposal(name="late night", description="d", action="keep")
    merged4 = _carryover_fit_from_prior(new4, priors)
    assert merged4.fit is None

    # Idempotency: if new proposal already has fit, do not overwrite.
    new5 = VibeProposal(name="workout", description="d", action="keep",
                       fit="no_match", fit_reason="example")
    merged5 = _carryover_fit_from_prior(new5, priors)
    assert merged5.fit == "no_match"
    assert merged5.fit_reason == "example"


def test_carryover_fit_from_prior_with_no_priors_is_no_op():
    from app.services.vibe_clusterer import (
        VibeProposal, _carryover_fit_from_prior,
    )
    p = VibeProposal(name="x", description="d", action="new")
    assert _carryover_fit_from_prior(p, None).fit is None
    assert _carryover_fit_from_prior(p, []).fit is None


@pytest.mark.asyncio
async def test_map_user_vibes_passes_real_session_to_anthropic_factory(monkeypatch):
    """Quick-260510-sht regression — production-only AttributeError guard.

    The user-led clustering path crashed on the NAS because
    map_user_vibes_to_clusters called get_anthropic_client_v2(None) at the
    buggy site. The real factory at app/services/anthropic_client.py:164 then
    invoked session.exec(...) on None and raised AttributeError.

    Why none of the existing 35 tests caught it: every map_user_vibes_*
    test monkeypatches the module-local re-export shim
    ``app.services.vibe_clusterer.get_anthropic_client_v2`` with
    ``lambda s: fake_client``, which silently swallows whatever ``s`` is —
    including ``None``. The real upstream factory at
    ``anthropic_client.py:164`` is never exercised, so the
    ``session.exec(...)`` crash only surfaces in production.

    The fix-side regression test below monkeypatches the UPSTREAM name
    (``app.services.anthropic_client.get_anthropic_client_v2``), which the
    shim imports lazily inside its function body. That way the shim still
    runs, still receives whatever session the caller passed in, and the spy
    records what reached the real factory boundary.
    """
    from sqlmodel import Session as _SessionCls
    from app.services import anthropic_client as ac_module
    from app.services import vibe_clusterer as vc

    # Build a synthetic 90-track rated set with 3 clear clusters (mirrors
    # test_map_user_vibes_server_populates_seed_track_indices_seed_tracks_members).
    np_local = vc.np
    cluster_a = np_local.random.RandomState(0).normal(
        loc=[0.2, 80, 0.3, 0.2], scale=0.05, size=(30, 4)
    )
    cluster_b = np_local.random.RandomState(1).normal(
        loc=[0.8, 140, 0.7, 0.7], scale=0.05, size=(30, 4)
    )
    cluster_c = np_local.random.RandomState(2).normal(
        loc=[0.5, 100, 0.5, 0.5], scale=0.05, size=(30, 4)
    )
    feat = np_local.vstack([cluster_a, cluster_b, cluster_c])
    index_map = [
        {"index": i, "rating_key": f"rk_{i}",
         "title": f"T{i}", "artist": f"A{i % 5}"}
        for i in range(90)
    ]
    tracks_for_prompt = [
        {"title": f"T{i}", "artist": f"A{i % 5}",
         "energy": float(feat[i, 0]), "tempo": float(feat[i, 1]),
         "danceability": float(feat[i, 2]),
         "valence": float(feat[i, 3]), "rating": 8.0,
         "genre": "synthwave" if i >= 60 else "rock"}
        for i in range(90)
    ]

    # Build a valid LLMVibeMappingResponse to return from the fake client.
    async def _fake_call(*, system_prompt, user_prompt, response_model, purpose):
        return vc.LLMVibeMappingResponse(mappings=[
            vc.LLMVibeFit(user_name="alpha", cluster_index=0,
                          description="d", fit="strong"),
            vc.LLMVibeFit(user_name="beta", cluster_index=1,
                          description="d", fit="strong"),
            vc.LLMVibeFit(user_name="gamma", cluster_index=2,
                          description="d", fit="strong"),
        ])

    fake_client = AsyncMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake_call)

    # Spy on the UPSTREAM factory — captures whatever session the shim
    # passes through. This is the bug-detection surface. Patching
    # vc.get_anthropic_client_v2 (the shim) instead would reproduce the same
    # blind spot as the existing 11 tests, so we explicitly do NOT.
    seen_sessions: list = []

    def _spy(session):
        seen_sessions.append(session)
        return fake_client

    monkeypatch.setattr(
        "app.services.anthropic_client.get_anthropic_client_v2", _spy
    )

    # materialize_clusters re-queries the real DB → no-op for this unit test.
    with patch.object(vc, "_aggregate_rated_set_sync") as mock_agg, \
         patch.object(vc, "materialize_clusters", side_effect=lambda p: p):
        mock_agg.return_value = {
            "rated_track_count": 90,
            "feature_matrix": feat,
            "rated_track_index_map": index_map,
            "tracks_for_prompt": tracks_for_prompt,
            "top_artists": [], "top_genres": [],
        }
        await vc.map_user_vibes_to_clusters(["alpha", "beta", "gamma"])

    # The shim must have delegated to the upstream factory exactly once.
    assert len(seen_sessions) == 1, (
        f"Expected exactly 1 call to anthropic_client.get_anthropic_client_v2; "
        f"got {len(seen_sessions)}"
    )
    # THE BUG ASSERTION — current code passes None at line ~1145.
    assert seen_sessions[0] is not None, (
        "map_user_vibes_to_clusters passed None to the Anthropic factory; "
        "this triggers AttributeError on session.exec(...) in production "
        "(quick-260510-sht)."
    )
    # Stronger assertion: must be a real sqlmodel Session instance.
    assert isinstance(seen_sessions[0], _SessionCls), (
        f"Expected sqlmodel.Session; got {type(seen_sessions[0]).__name__}"
    )
