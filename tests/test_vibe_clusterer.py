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
# Phase 6.1 VibeProposal schema extensions kept; the LLMVibeFit /
# LLMVibeMappingResponse schema tests were deleted in Phase 6.2 Plan 01 with
# the function rewrite (D-18). The replacement Pass 1 / Pass 2 slim schemas
# get their own defensive tests further down.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Phase 6.2 Plan 01 — assign_tracks_to_user_vibes + helpers
# ---------------------------------------------------------------------------
from unittest.mock import patch  # noqa: E402  (test-section-local import)


def test_aggregate_rated_set_includes_genre_per_track(test_engine, monkeypatch):
    """WARNING #2: tracks_for_prompt entries must carry Track.genre so
    per-cluster top-genres aggregation in assign_tracks_to_user_vibes works.
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


# ===========================================================================
# Phase 6.2 Plan 01 Task 2 — assign_tracks_to_user_vibes tests
# ===========================================================================


# ---------------------------------------------------------------------------
# Slim Pass 1 / Pass 2 schema defensive tests (D-33)
# ---------------------------------------------------------------------------
def test_slim_pass1_response_has_no_server_controlled_fields():
    """Pass 1 slim contract MUST stay narrow.

    If any future refactor adds a server-controlled field (silhouette,
    centroid, cluster_index, etc.) to ``LLMVibeAssignmentResponse``, this
    test fails — preventing the same class of shape-mismatch bug that
    quick-260510-das fixed for the initial-pass LLM contract.
    """
    from app.services.vibe_clusterer import LLMVibeAssignmentResponse

    fields = set(LLMVibeAssignmentResponse.model_fields.keys())
    assert fields == {"assignments"}, (
        f"LLMVibeAssignmentResponse must only have 'assignments'; got {fields}"
    )


def test_slim_pass2_response_has_no_server_controlled_fields():
    """Pass 2 slim contract MUST stay narrow."""
    from app.services.vibe_clusterer import LLMVibeBoundaryResponse

    fields = set(LLMVibeBoundaryResponse.model_fields.keys())
    assert fields == {"decisions"}, (
        f"LLMVibeBoundaryResponse must only have 'decisions'; got {fields}"
    )


def test_slim_definitions_response_has_no_server_controlled_fields():
    """Preamble slim contract MUST stay narrow."""
    from app.services.vibe_clusterer import LLMVibeDefinitionsResponse

    fields = set(LLMVibeDefinitionsResponse.model_fields.keys())
    assert fields == {"definitions"}, (
        f"LLMVibeDefinitionsResponse must only have 'definitions'; got {fields}"
    )


def test_pass1_required_reason_field():
    """D-03: reason field is REQUIRED on every Pass 1 assignment (not Optional).

    Pydantic must raise ValidationError when an assignment is constructed
    without a reason — under output pressure LLMs drop optional fields, so
    making reason required forces the LLM to emit it.
    """
    from pydantic import ValidationError
    from app.services.vibe_clusterer import LLMPass1Assignment

    # Valid case.
    a = LLMPass1Assignment(
        track_index=0,
        vibe_name="Workout",
        grade="strong",
        reason="synth-heavy ambient texture, low energy",
    )
    assert a.reason == "synth-heavy ambient texture, low energy"

    # Missing reason → ValidationError.
    with pytest.raises(ValidationError):
        LLMPass1Assignment(
            track_index=0,
            vibe_name="Workout",
            grade="strong",
        )


# ---------------------------------------------------------------------------
# AST test: k-means.labels_ MUST NOT be used for membership (D-19)
# ---------------------------------------------------------------------------
def test_no_kmeans_labels_used_for_membership():
    """VIBE-13 success criterion 3 / D-19 / T-062-08.

    K-means runs once for centroid summaries that scaffold the Pass 1 system
    prompt — but the resulting ``labels_`` array must NEVER be read again in
    any code path that produces final membership in ``assign_tracks_to_user_vibes``.

    The current implementation uses ``del kmeans_labels`` after the Pass 1
    system-prompt builder finishes consuming it, so any accidental read
    AFTER that point would raise NameError at runtime. This AST test makes
    the same guarantee at the static level: walk the function body, find the
    cluster-summaries call, and assert there are no ``kmeans_labels`` /
    ``labels_`` references in any sibling statement that runs after it.
    """
    import ast
    from pathlib import Path

    source = (
        Path(__file__).parent.parent
        / "app" / "services" / "vibe_clusterer.py"
    ).read_text()
    tree = ast.parse(source)

    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "assign_tracks_to_user_vibes":
            target = node
            break
    assert target is not None, (
        "assign_tracks_to_user_vibes not found in vibe_clusterer.py"
    )

    # Find the line index of the call to _build_cluster_summaries_for_prompt
    # OR the call to _build_pass1_system_prompt (whichever comes first in
    # the function body). All statements AFTER that index must not reference
    # `kmeans_labels` or any attribute `.labels_`.
    body = target.body
    boundary_idx = None
    for i, stmt in enumerate(body):
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Name) and func.id in (
                    "_build_cluster_summaries_for_prompt",
                    "_build_pass1_system_prompt",
                ):
                    boundary_idx = i
                    break
        if boundary_idx is not None:
            break

    assert boundary_idx is not None, (
        "Could not find _build_cluster_summaries_for_prompt or "
        "_build_pass1_system_prompt call in assign_tracks_to_user_vibes."
    )

    # Walk every statement AFTER the boundary; collect any forbidden
    # references. ``del kmeans_labels`` is explicitly allowed (the defensive
    # cleanup AT the boundary).
    forbidden_refs: list[str] = []
    for stmt in body[boundary_idx + 1:]:
        # Allow `del kmeans_labels` statements — they are the safety cleanup
        # itself, not a read for membership.
        if isinstance(stmt, ast.Delete):
            continue
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and sub.id == "kmeans_labels":
                forbidden_refs.append(
                    f"line {sub.lineno}: read of kmeans_labels"
                )
            if isinstance(sub, ast.Attribute) and sub.attr == "labels_":
                forbidden_refs.append(
                    f"line {sub.lineno}: read of .labels_"
                )

    assert forbidden_refs == [], (
        "T-062-08 / D-19 regression: kmeans labels MUST NOT be read for "
        "membership after the Pass 1 prompt is built. Found:\n"
        + "\n".join(forbidden_refs)
    )


# ---------------------------------------------------------------------------
# Behavioral tests for assign_tracks_to_user_vibes
# ---------------------------------------------------------------------------


def _make_pass1_aggregate(n_rated: int = 30):
    """Build a synthetic aggregate dict for assign_tracks_to_user_vibes mocks.

    Deterministic well-separated 3-cluster 4-D feature space mirroring
    ``_seed_tracks(well_separated=True)``. Returns the dict shape that
    ``_aggregate_rated_set_sync`` produces.
    """
    rng = np.random.RandomState(42)
    centers = np.array([
        [0.20, 80.0, 0.20, 0.20],
        [0.80, 140.0, 0.70, 0.70],
        [0.50, 110.0, 0.50, 0.50],
    ])
    scales = np.array([0.05, 5.0, 0.05, 0.05])
    per = n_rated // 3
    rows = []
    index_map = []
    tracks_for_prompt = []
    for c_idx, c in enumerate(centers):
        for j in range(per):
            i = c_idx * per + j
            pt = c + rng.randn(4) * scales
            pt[0] = float(np.clip(pt[0], 0.0, 1.0))
            pt[1] = float(np.clip(pt[1], 60.0, 180.0))
            pt[2] = float(np.clip(pt[2], 0.0, 1.0))
            pt[3] = float(np.clip(pt[3], 0.0, 1.0))
            rows.append(list(pt))
            index_map.append({
                "index": i, "rating_key": f"rk_{i}",
                "title": f"T{i}", "artist": f"A{c_idx}",
            })
            tracks_for_prompt.append({
                "title": f"T{i}", "artist": f"A{c_idx}",
                "energy": pt[0], "tempo": pt[1],
                "danceability": pt[2], "valence": pt[3],
                "rating": 8.0, "genre": "rock" if c_idx == 0 else "synthwave",
            })
    # Fill remainder
    for i in range(per * 3, n_rated):
        pt = rng.uniform(0, 1, 4)
        pt[1] = 60.0 + pt[1] * 120.0
        rows.append(list(pt))
        index_map.append({
            "index": i, "rating_key": f"rk_{i}",
            "title": f"T{i}", "artist": f"A_R",
        })
        tracks_for_prompt.append({
            "title": f"T{i}", "artist": f"A_R",
            "energy": pt[0], "tempo": pt[1],
            "danceability": pt[2], "valence": pt[3],
            "rating": 8.0, "genre": "",
        })
    return {
        "rated_track_count": n_rated,
        "feature_matrix": np.array(rows, dtype=float),
        "rated_track_index_map": index_map,
        "tracks_for_prompt": tracks_for_prompt,
        "top_artists": [],
        "top_genres": [],
    }


def _build_pass1_response_all_strong(
    batch_tracks_len: int,
    vibe_name: str,
):
    """Build a Pass 1 response with all-strong grades for one fixed vibe."""
    from app.services.vibe_clusterer import (
        LLMPass1Assignment, LLMVibeAssignmentResponse,
    )
    return LLMVibeAssignmentResponse(assignments=[
        LLMPass1Assignment(
            track_index=i, vibe_name=vibe_name, grade="strong",
            reason="strong fit",
        )
        for i in range(batch_tracks_len)
    ])


def _build_definitions_response(user_names):
    from app.services.vibe_clusterer import (
        LLMVibeDefinition, LLMVibeDefinitionsResponse,
    )
    return LLMVibeDefinitionsResponse(definitions=[
        LLMVibeDefinition(vibe_name=n, definition=f"def for {n}")
        for n in user_names
    ])


@pytest.mark.asyncio
async def test_pass1_batch_size_25(db_with_phase6, monkeypatch):
    """Aggregate of 30 rated tracks → Pass 1 issues 2 batches (25 + 5).

    Plus 1 preamble call up front. No Pass 2 (all-strong → no boundary tracks).
    """
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)

    # Mock client: preamble + 2 Pass 1 batches (25 + 5), all strong.
    fake_client = MagicMock()
    call_log: list = []

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        purpose = kw.get("purpose")
        call_log.append({"purpose": purpose, "prompt": user_prompt, "kw": kw})
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["alpha", "beta", "gamma"])
        if response_model is vc.LLMVibeAssignmentResponse:
            # Determine batch size by counting lines in the prompt.
            # Lines like "0: T0 — A0 (..." indicate tracks. Count exactly.
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            return _build_pass1_response_all_strong(len(lines), "alpha")
        raise AssertionError(f"Unexpected response_model: {response_model}")

    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)

    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    result = await vc.assign_tracks_to_user_vibes(["alpha", "beta", "gamma"])

    # Expected calls: 1 preamble + 2 Pass 1 batches = 3 total.
    pass1_calls = [c for c in call_log if c["purpose"] == "vibe_assign_pass1"]
    preamble_calls = [c for c in call_log if c["purpose"] == "vibe_definitions_preamble"]
    assert len(preamble_calls) == 1, f"Expected 1 preamble; got {len(preamble_calls)}"
    assert len(pass1_calls) == 2, f"Expected 2 Pass 1 batches; got {len(pass1_calls)}"

    # First batch: 25 tracks; second batch: 5 tracks.
    def _track_line_count(prompt):
        return len([
            ln for ln in prompt.splitlines()
            if ln and ln.split(":", 1)[0].strip().isdigit()
        ])
    batch_sizes = [_track_line_count(c["prompt"]) for c in pass1_calls]
    assert sorted(batch_sizes) == [5, 25], f"Got batch sizes {batch_sizes}"
    assert isinstance(result, vc.VibeProposalSet)


@pytest.mark.asyncio
async def test_pass1_serial_execution(db_with_phase6, monkeypatch):
    """Batches execute strictly serially: batch N+1 starts only after batch N completes."""
    import asyncio as _asyncio
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)

    in_flight = 0
    max_in_flight = [0]
    lock = _asyncio.Lock()

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        nonlocal in_flight
        async with lock:
            in_flight += 1
            max_in_flight[0] = max(max_in_flight[0], in_flight)
        try:
            # Yield so other coroutines could interleave if not serial.
            await _asyncio.sleep(0.001)
            if response_model is vc.LLMVibeDefinitionsResponse:
                return _build_definitions_response(["alpha", "beta", "gamma"])
            if response_model is vc.LLMVibeAssignmentResponse:
                lines = [
                    ln for ln in user_prompt.splitlines()
                    if ln and ln.split(":", 1)[0].strip().isdigit()
                ]
                return _build_pass1_response_all_strong(len(lines), "alpha")
        finally:
            async with lock:
                in_flight -= 1
        raise AssertionError(f"Unexpected response_model: {response_model}")

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["alpha", "beta", "gamma"])

    assert max_in_flight[0] == 1, (
        f"Expected serial execution (max 1 in-flight); got {max_in_flight[0]}"
    )


@pytest.mark.asyncio
async def test_pass1_retry_once_on_validation_failure(db_with_phase6, monkeypatch):
    """First Pass 1 call returns a bad batch (wrong batch size) → retry-once.

    Retry user prompt MUST contain the substring "PREVIOUS RESPONSE WAS INVALID".
    """
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)

    pass1_call_count = [0]
    seen_pass1_prompts: list = []

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["alpha", "beta", "gamma"])
        if response_model is vc.LLMVibeAssignmentResponse:
            seen_pass1_prompts.append(user_prompt)
            pass1_call_count[0] += 1
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            if pass1_call_count[0] == 1:
                # Return WRONG count: only 1 assignment for a 25-track batch.
                return vc.LLMVibeAssignmentResponse(assignments=[
                    vc.LLMPass1Assignment(
                        track_index=0, vibe_name="alpha", grade="strong",
                        reason="ok",
                    )
                ])
            return _build_pass1_response_all_strong(len(lines), "alpha")
        raise AssertionError

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["alpha", "beta", "gamma"])

    # Pass 1 was called for batch 1: initial + retry = 2 calls, plus batch 2 = 3.
    assert pass1_call_count[0] == 3, (
        f"Expected 3 Pass 1 calls (batch 1 + retry + batch 2); got {pass1_call_count[0]}"
    )
    # Second prompt (the retry) must carry the corrective addendum.
    assert "PREVIOUS RESPONSE WAS INVALID" in seen_pass1_prompts[1], (
        "Retry user prompt must include the corrective addendum."
    )


@pytest.mark.asyncio
async def test_pass1_validates_track_indices_against_batch(db_with_phase6, monkeypatch):
    """T-062-02: LLM returns track_index=99 when batch has 0..24 → retry-once."""
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)
    pass1_call_count = [0]

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["alpha", "beta", "gamma"])
        if response_model is vc.LLMVibeAssignmentResponse:
            pass1_call_count[0] += 1
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            if pass1_call_count[0] == 1:
                # First batch: return one OUT-OF-RANGE index.
                bad = [
                    vc.LLMPass1Assignment(
                        track_index=i if i < n - 1 else 99,
                        vibe_name="alpha", grade="strong", reason="ok",
                    )
                    for i in range(n)
                ]
                return vc.LLMVibeAssignmentResponse(assignments=bad)
            return _build_pass1_response_all_strong(n, "alpha")
        raise AssertionError

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["alpha", "beta", "gamma"])
    # Pass 1 batches: 1 (bad) + 1 (retry-success) + 1 (next batch) = 3
    assert pass1_call_count[0] == 3


@pytest.mark.asyncio
async def test_pass1_validates_vibe_name_against_user_list(db_with_phase6, monkeypatch):
    """T-062-03: casefold resolution works; unknown name triggers retry-once."""
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)
    pass1_call_count = [0]

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["Workout", "Late Night", "Smooth Jazz"])
        if response_model is vc.LLMVibeAssignmentResponse:
            pass1_call_count[0] += 1
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            if pass1_call_count[0] == 1:
                # Use "smoothjazz" (collapsed) — should casefold to "Smooth Jazz"
                # which IS in the list. Server should NOT trigger retry on
                # this case. Use "Bossa" (NOT in list) to force retry.
                return vc.LLMVibeAssignmentResponse(assignments=[
                    vc.LLMPass1Assignment(
                        track_index=0, vibe_name="Bossa",
                        grade="strong", reason="bad name",
                    ),
                    *[
                        vc.LLMPass1Assignment(
                            track_index=i, vibe_name="Workout",
                            grade="strong", reason="ok",
                        )
                        for i in range(1, n)
                    ],
                ])
            # Retry: emit valid casefold variant of an in-list name.
            # "SMOOTH JAZZ" → casefold → "smooth jazz" which IS in the
            # name_lookup. The server preserves the user's ORIGINAL casing
            # ("Smooth Jazz") on the resolved name.
            return vc.LLMVibeAssignmentResponse(assignments=[
                vc.LLMPass1Assignment(
                    track_index=i,
                    vibe_name="SMOOTH JAZZ" if i == 0 else "Workout",
                    grade="strong", reason="ok",
                )
                for i in range(n)
            ])
        raise AssertionError

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    result = await vc.assign_tracks_to_user_vibes(
        ["Workout", "Late Night", "Smooth Jazz"]
    )
    # batch 1 (bad) + retry (good) + batch 2 = 3
    assert pass1_call_count[0] == 3
    # Resolved casefold "smoothjazz" → "Smooth Jazz" — must appear as a proposal name.
    names = {p.name for p in result.proposals}
    assert "Smooth Jazz" in names


@pytest.mark.asyncio
async def test_pass2_only_runs_on_weak_or_uncertain(db_with_phase6, monkeypatch):
    """All-strong Pass 1 → ZERO Pass 2 LLM calls."""
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)
    pass2_call_count = [0]

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if kw.get("purpose") == "vibe_assign_pass2":
            pass2_call_count[0] += 1
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            return _build_pass1_response_all_strong(len(lines), "a")
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["a", "b", "c"])
    assert pass2_call_count[0] == 0, (
        f"Pass 2 must NOT run when no track is weak/uncertain; "
        f"got {pass2_call_count[0]} calls."
    )


@pytest.mark.asyncio
async def test_pass2_candidate_set_includes_pass1_pick(db_with_phase6, monkeypatch):
    """Even when Pass 1's pick is not the centroid-closest, candidate set includes it.

    Setup: Pass 1 marks one track as weak with vibe_name='c' (the third-
    closest by centroid). Pass 2 user prompt for that track must list
    EXACTLY 2 candidates, one of which is 'c'.
    """
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)
    seen_pass2_prompts: list = []

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if kw.get("purpose") == "vibe_assign_pass2":
            seen_pass2_prompts.append(user_prompt)
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            # Mark track index 0 in first batch as weak with vibe 'c';
            # everyone else strong with 'a'.
            assignments = []
            for i in range(n):
                if i == 0:
                    assignments.append(vc.LLMPass1Assignment(
                        track_index=i, vibe_name="c",
                        grade="weak", reason="mixed signal",
                    ))
                else:
                    assignments.append(vc.LLMPass1Assignment(
                        track_index=i, vibe_name="a",
                        grade="strong", reason="strong fit",
                    ))
            return vc.LLMVibeAssignmentResponse(assignments=assignments)
        if response_model is vc.LLMVibeBoundaryResponse:
            # Just commit each track to 'c' (Pass 1's pick) — server
            # validates final_vibe_name ∈ candidates so we MUST pick from
            # the prompt's candidate list.
            n = sum(
                1 for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            )
            return vc.LLMVibeBoundaryResponse(decisions=[
                vc.LLMPass2Decision(
                    track_index=i, final_vibe_name="c",
                    reason="confirmed",
                )
                for i in range(n)
            ])
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    assert seen_pass2_prompts, "Pass 2 must have run for the weak track"
    # Look at the first Pass 2 prompt. It must mention candidate 'c'.
    prompt = seen_pass2_prompts[0]
    assert "'c'" in prompt or 'c |' in prompt or '[c' in prompt, (
        f"Pass 2 prompt must include 'c' as a candidate; got:\n{prompt[:500]}"
    )
    # The prompt should also mention exactly 2 candidates per track.
    # Each track line ends with "Candidates: [name1 | name2]." — count "|".
    candidate_lines = [
        ln for ln in prompt.splitlines() if "Candidates:" in ln
    ]
    assert candidate_lines, "Pass 2 prompt must list Candidates per track"
    for ln in candidate_lines:
        cand_section = ln.rsplit("Candidates:", 1)[-1]
        # Expect "Candidates: [n1 | n2]." → exactly one " | " separator.
        assert cand_section.count("|") == 1, (
            f"Each Pass 2 entry must list EXACTLY 2 candidates; got: {ln}"
        )


@pytest.mark.asyncio
async def test_pass2_peer_context_strong_only(db_with_phase6, monkeypatch):
    """Pass 2 SYSTEM prompt's <peer_tracks> section contains ONLY strong-graded tracks."""
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)
    captured_pass2_system: list = []

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if kw.get("purpose") == "vibe_assign_pass2":
            captured_pass2_system.append(system_prompt)
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            # Mix grades: first 5 weak ('a'), next 5 uncertain ('b'), rest strong (rotating).
            assignments = []
            for i in range(n):
                if i < 5:
                    assignments.append(vc.LLMPass1Assignment(
                        track_index=i, vibe_name="a",
                        grade="weak", reason="weak",
                    ))
                elif i < 10:
                    assignments.append(vc.LLMPass1Assignment(
                        track_index=i, vibe_name="b",
                        grade="uncertain", reason="uncertain",
                    ))
                else:
                    assignments.append(vc.LLMPass1Assignment(
                        track_index=i,
                        vibe_name=["a", "b", "c"][i % 3],
                        grade="strong", reason="strong",
                    ))
            return vc.LLMVibeAssignmentResponse(assignments=assignments)
        if response_model is vc.LLMVibeBoundaryResponse:
            # Each boundary track committed to Pass 1's pick (which is always
            # one of the candidates per D-13).
            # Extract Pass 1 picks from user prompt.
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            decisions = []
            for ln in lines:
                # Pass 1 picked 'X' — extract X.
                import re
                m = re.search(r"Pass 1 picked '([^']+)'", ln)
                pick = m.group(1) if m else "a"
                local_idx = int(ln.split(":", 1)[0])
                decisions.append(vc.LLMPass2Decision(
                    track_index=local_idx, final_vibe_name=pick,
                    reason="confirmed",
                ))
            return vc.LLMVibeBoundaryResponse(decisions=decisions)
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    assert captured_pass2_system, "Pass 2 must have run"
    sysp = captured_pass2_system[0]
    # Peer-tracks section is bracketed by <peer_tracks> ... </peer_tracks>.
    import re
    m = re.search(r"<peer_tracks>(.*?)</peer_tracks>", sysp, re.DOTALL)
    assert m, "Pass 2 system prompt must contain <peer_tracks> XML section"
    peer_section = m.group(1)
    # Tracks i in [0..4] are titled T0..T4 (weak, vibe 'a'); i in [5..9] are
    # T5..T9 (uncertain, vibe 'b'). NONE of these should appear in peers.
    for weak_or_uncertain_idx in range(10):
        token = f"T{weak_or_uncertain_idx} "  # space disambiguates T1 vs T10
        # Sloppy check — accept if our title isn't found at all OR not in peer section.
        assert f"T{weak_or_uncertain_idx} —" not in peer_section, (
            f"Peer section must NOT include weak/uncertain track T{weak_or_uncertain_idx}"
        )


@pytest.mark.asyncio
async def test_pass2_batch_size_15(db_with_phase6, monkeypatch):
    """20 weak/uncertain tracks → 2 Pass 2 batches (15 + 5)."""
    from app.services import vibe_clusterer as vc

    # 60 rated tracks; mark exactly 20 as weak total. Pass 1 batches at 25,
    # so we need 10 weak in batch 1 (indices 0..9), 10 weak in batch 2
    # (indices 0..9 local → 25..34 global), 0 weak in batch 3 (only 10 tracks).
    agg = _make_pass1_aggregate(60)
    pass2_user_prompts: list = []
    pass1_batch_seen = [0]

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if kw.get("purpose") == "vibe_assign_pass2":
            pass2_user_prompts.append(user_prompt)
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            pass1_batch_seen[0] += 1
            assignments = []
            for i in range(n):
                # Only mark weak in batches 1 + 2; batch 3 is all strong.
                if pass1_batch_seen[0] <= 2 and i < 10:
                    assignments.append(vc.LLMPass1Assignment(
                        track_index=i, vibe_name="a",
                        grade="weak", reason="weak",
                    ))
                else:
                    assignments.append(vc.LLMPass1Assignment(
                        track_index=i, vibe_name=["a", "b", "c"][i % 3],
                        grade="strong", reason="strong",
                    ))
            return vc.LLMVibeAssignmentResponse(assignments=assignments)
        if response_model is vc.LLMVibeBoundaryResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            decisions = []
            for ln in lines:
                import re
                m = re.search(r"Pass 1 picked '([^']+)'", ln)
                pick = m.group(1) if m else "a"
                local_idx = int(ln.split(":", 1)[0])
                decisions.append(vc.LLMPass2Decision(
                    track_index=local_idx, final_vibe_name=pick,
                    reason="ok",
                ))
            return vc.LLMVibeBoundaryResponse(decisions=decisions)
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    def _count_tracks(p):
        return sum(
            1 for ln in p.splitlines()
            if ln and ln.split(":", 1)[0].strip().isdigit()
        )

    batch_sizes = [_count_tracks(p) for p in pass2_user_prompts]
    # Expected: 20 weak tracks → batches of 15 + 5.
    assert sorted(batch_sizes) == [5, 15], (
        f"Expected Pass 2 batches of 15 + 5; got {batch_sizes}"
    )


@pytest.mark.asyncio
async def test_pass2_adaptive_thinking_enabled(db_with_phase6, monkeypatch):
    """Pass 2 calls pass thinking='adaptive'; Pass 1 + preamble pass thinking='off'."""
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)
    seen_thinking_by_purpose: dict = {}

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        purpose = kw.get("purpose")
        thinking = kw.get("thinking", "off")
        seen_thinking_by_purpose.setdefault(purpose, []).append(thinking)
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            # One weak so Pass 2 runs.
            n = len(lines)
            assignments = [vc.LLMPass1Assignment(
                track_index=0, vibe_name="a", grade="weak", reason="weak",
            )]
            for i in range(1, n):
                assignments.append(vc.LLMPass1Assignment(
                    track_index=i, vibe_name=["a", "b", "c"][i % 3],
                    grade="strong", reason="strong",
                ))
            return vc.LLMVibeAssignmentResponse(assignments=assignments)
        if response_model is vc.LLMVibeBoundaryResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            return vc.LLMVibeBoundaryResponse(decisions=[
                vc.LLMPass2Decision(
                    track_index=int(ln.split(":", 1)[0]),
                    final_vibe_name="a", reason="ok",
                )
                for ln in lines
            ])
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    assert seen_thinking_by_purpose.get("vibe_definitions_preamble") == ["off"]
    assert all(
        t == "off" for t in seen_thinking_by_purpose.get("vibe_assign_pass1", [])
    ), f"Pass 1 must use thinking='off'; got {seen_thinking_by_purpose}"
    assert seen_thinking_by_purpose.get("vibe_assign_pass2"), (
        "Pass 2 must have run for weak track"
    )
    assert all(
        t == "adaptive" for t in seen_thinking_by_purpose["vibe_assign_pass2"]
    )


@pytest.mark.asyncio
async def test_pass2_max_tokens_uses_constant(db_with_phase6, monkeypatch):
    """Pass 2 calls use ``PASS2_MAX_TOKENS`` (RESEARCH §4.3 + hotfix 260512-kvs)."""
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)
    seen_max_tokens_by_purpose: dict = {}

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        purpose = kw.get("purpose")
        max_tokens = kw.get("max_tokens", 0)
        seen_max_tokens_by_purpose.setdefault(purpose, []).append(max_tokens)
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            assignments = [vc.LLMPass1Assignment(
                track_index=0, vibe_name="a", grade="weak", reason="weak",
            )]
            for i in range(1, n):
                assignments.append(vc.LLMPass1Assignment(
                    track_index=i, vibe_name=["a", "b", "c"][i % 3],
                    grade="strong", reason="strong",
                ))
            return vc.LLMVibeAssignmentResponse(assignments=assignments)
        if response_model is vc.LLMVibeBoundaryResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            return vc.LLMVibeBoundaryResponse(decisions=[
                vc.LLMPass2Decision(
                    track_index=int(ln.split(":", 1)[0]),
                    final_vibe_name="a", reason="ok",
                )
                for ln in lines
            ])
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    pass2_tokens = seen_max_tokens_by_purpose.get("vibe_assign_pass2", [])
    assert pass2_tokens, "Pass 2 must have run"
    assert all(t == vc.PASS2_MAX_TOKENS for t in pass2_tokens), (
        f"Pass 2 must use max_tokens={vc.PASS2_MAX_TOKENS}; got {pass2_tokens}"
    )


@pytest.mark.asyncio
async def test_vibe_centroid_recomputed_from_llm_members(db_with_phase6, monkeypatch):
    """D-20: VibeProposal.centroid is mean(features of LLM-assigned members).

    Setup: 6 rated tracks across 2 (passed as 3) vibes, all assigned by
    Pass 1 (no Pass 2). Build a deterministic 4-D feature matrix and verify
    the recomputed centroid numerically.
    """
    from app.services import vibe_clusterer as vc

    # 30 tracks (min for cold-start). Make it deterministic by overriding
    # the aggregate output.
    n_rated = 30
    feat = np.zeros((n_rated, 4))
    # 15 tracks → vibe 'a' centered at (0.2, 80, 0.2, 0.2)
    for i in range(15):
        feat[i] = [0.20 + i * 0.001, 80.0 + i * 0.1,
                   0.20 + i * 0.001, 0.20 + i * 0.001]
    # 15 tracks → vibe 'b' centered at (0.8, 140, 0.7, 0.7)
    for i in range(15, 30):
        feat[i] = [0.80 + (i - 15) * 0.001, 140.0 + (i - 15) * 0.1,
                   0.70 + (i - 15) * 0.001, 0.70 + (i - 15) * 0.001]
    agg = {
        "rated_track_count": n_rated,
        "feature_matrix": feat,
        "rated_track_index_map": [
            {"index": i, "rating_key": f"rk_{i}",
             "title": f"T{i}", "artist": "X"}
            for i in range(n_rated)
        ],
        "tracks_for_prompt": [
            {"title": f"T{i}", "artist": "X",
             "energy": feat[i, 0], "tempo": feat[i, 1],
             "danceability": feat[i, 2], "valence": feat[i, 3],
             "rating": 8.0, "genre": ""}
            for i in range(n_rated)
        ],
        "top_artists": [], "top_genres": [],
    }

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            assignments = []
            # Parse the global index by reading the title TX from the prompt.
            import re
            for ln in lines:
                local = int(ln.split(":", 1)[0])
                m = re.search(r"T(\d+)\s*—", ln)
                global_idx = int(m.group(1)) if m else local
                if global_idx < 15:
                    vibe = "a"
                else:
                    vibe = "b"
                assignments.append(vc.LLMPass1Assignment(
                    track_index=local, vibe_name=vibe,
                    grade="strong", reason="strong",
                ))
            return vc.LLMVibeAssignmentResponse(assignments=assignments)
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    # Bypass materialize_clusters' DB queries — they would clobber our centroid.
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    result = await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    prop_a = next(p for p in result.proposals if p.name == "a")
    prop_b = next(p for p in result.proposals if p.name == "b")

    # Expected centroid for 'a' = mean of feat[0..14].
    expected_a = feat[:15].mean(axis=0)
    assert prop_a.centroid is not None
    assert abs(prop_a.centroid["energy"] - expected_a[0]) < 1e-6, (
        f"vibe a energy centroid mismatch: {prop_a.centroid['energy']} vs {expected_a[0]}"
    )
    assert abs(prop_a.centroid["tempo"] - expected_a[1]) < 1e-6
    assert abs(prop_a.centroid["danceability"] - expected_a[2]) < 1e-6
    assert abs(prop_a.centroid["valence"] - expected_a[3]) < 1e-6

    # Expected centroid for 'b' = mean of feat[15..29].
    expected_b = feat[15:].mean(axis=0)
    assert abs(prop_b.centroid["energy"] - expected_b[0]) < 1e-6
    assert abs(prop_b.centroid["tempo"] - expected_b[1]) < 1e-6


@pytest.mark.asyncio
async def test_llm_usage_purpose_segmentation(db_with_phase6, monkeypatch):
    """All 3 Phase 6.2 purposes get logged; legacy vibe_clustering_user_led never appears."""
    from app.services import vibe_clusterer as vc
    from app.models.llm_usage import LLMUsage

    agg = _make_pass1_aggregate(30)

    # Use REAL anthropic client logging path via patched messages.create.
    from types import SimpleNamespace

    pass1_call = [0]

    def _build_anthropic_response(text):
        resp = MagicMock()
        resp.usage.input_tokens = 2500
        resp.usage.cache_creation_input_tokens = 2048
        resp.usage.cache_read_input_tokens = 0
        resp.usage.output_tokens = 120
        resp.stop_reason = "end_turn"
        resp.content = [SimpleNamespace(type="text", text=text)]
        return resp

    async def _messages_create(**kwargs):
        # Inspect the system prompt to figure out which call this is.
        sys_text = kwargs["system"][0]["text"]
        if "vibe definition writer" in sys_text:
            text = (
                '{"definitions": ['
                '{"vibe_name": "a", "definition": "def for a"},'
                '{"vibe_name": "b", "definition": "def for b"},'
                '{"vibe_name": "c", "definition": "def for c"}'
                "]}"
            )
        elif "vibe boundary reviewer" in sys_text:
            # Build decisions from user prompt.
            up = kwargs["messages"][0]["content"]
            lines = [
                ln for ln in up.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            import re
            decisions = []
            for ln in lines:
                local = int(ln.split(":", 1)[0])
                m = re.search(r"Pass 1 picked '([^']+)'", ln)
                pick = m.group(1) if m else "a"
                decisions.append({
                    "track_index": local,
                    "final_vibe_name": pick,
                    "reason": "ok",
                })
            import json as _json
            text = _json.dumps({"decisions": decisions})
        else:
            # Pass 1.
            up = kwargs["messages"][0]["content"]
            lines = [
                ln for ln in up.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            pass1_call[0] += 1
            assignments = []
            for ln in lines:
                local = int(ln.split(":", 1)[0])
                # Mark a few weak for Pass 2 to exist.
                if local < 4 and pass1_call[0] == 1:
                    grade = "weak"
                else:
                    grade = "strong"
                assignments.append({
                    "track_index": local,
                    "vibe_name": "a",
                    "grade": grade,
                    "reason": "ok",
                })
            import json as _json
            text = _json.dumps({"assignments": assignments})
        return _build_anthropic_response(text)

    # Patch AsyncAnthropic so REAL AnthropicClient is used; we monkeypatch
    # the factory in vibe_clusterer to return a manually-instantiated client.
    from app.services.anthropic_client import AnthropicClient

    real_client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
    real_client._client = MagicMock()
    real_client._client.messages.create = AsyncMock(side_effect=_messages_create)

    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: real_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    purposes_logged = list(db_with_phase6.exec(
        select(LLMUsage.purpose)
    ).all())
    purposes = set(purposes_logged)
    assert "vibe_definitions_preamble" in purposes, purposes
    assert "vibe_assign_pass1" in purposes, purposes
    assert "vibe_assign_pass2" in purposes, purposes
    # Legacy purpose must NOT appear.
    assert "vibe_clustering_user_led" not in purposes


def test_uncertainty_encouraged_framing_present_in_system_prompt():
    """D-10: 'uncertainty is encouraged' framing is present in the Pass 1 system prompt."""
    from app.services.vibe_clusterer import (
        LLMVibeDefinition, _build_pass1_system_prompt,
    )
    defs = [
        LLMVibeDefinition(vibe_name="a", definition="d"),
        LLMVibeDefinition(vibe_name="b", definition="d"),
        LLMVibeDefinition(vibe_name="c", definition="d"),
    ]
    prompt = _build_pass1_system_prompt(
        user_names=["a", "b", "c"],
        definitions=defs,
        cluster_summaries=[],
        n_rated=30,
        top_artists=[],
        top_genres=[],
    )
    assert "uncertain" in prompt.lower(), prompt
    assert "encouraged" in prompt.lower(), prompt
    assert "uncertainty is encouraged" in prompt.lower(), prompt


def test_pass1_definitions_in_system_prompt():
    """D-04: each user-typed vibe name + its definition is in the Pass 1 system prompt."""
    from app.services.vibe_clusterer import (
        LLMVibeDefinition, _build_pass1_system_prompt,
    )
    defs = [
        LLMVibeDefinition(vibe_name="Workout", definition="high energy synth tracks"),
        LLMVibeDefinition(vibe_name="Late Night", definition="dark valence-low ambient"),
        LLMVibeDefinition(vibe_name="Smooth Jazz", definition="warm relaxed jazz"),
    ]
    prompt = _build_pass1_system_prompt(
        user_names=["Workout", "Late Night", "Smooth Jazz"],
        definitions=defs,
        cluster_summaries=[],
        n_rated=30,
        top_artists=[],
        top_genres=[],
    )
    for name, definition in (
        ("Workout", "high energy synth tracks"),
        ("Late Night", "dark valence-low ambient"),
        ("Smooth Jazz", "warm relaxed jazz"),
    ):
        assert name in prompt, f"Pass 1 system prompt missing vibe name: {name}"
        assert definition in prompt, (
            f"Pass 1 system prompt missing definition for {name}"
        )


@pytest.mark.asyncio
async def test_assign_returns_vibe_proposal_set_compatible_with_finalize(
    db_with_phase6, monkeypatch
):
    """D-22: model_dump_json round-trips through model_validate_json.

    The producer changed (LLM-direct now); the consumer (finalize / recluster
    commit) did NOT — they parse the JSON back into VibeProposalSet. Ensure
    the schema is still round-trippable.
    """
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            return _build_pass1_response_all_strong(len(lines), "a")
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    result = await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    json_str = result.model_dump_json()
    roundtripped = vc.VibeProposalSet.model_validate_json(json_str)
    assert roundtripped.rated_track_count == result.rated_track_count
    assert len(roundtripped.proposals) == len(result.proposals)
    assert {p.name for p in roundtripped.proposals} == {p.name for p in result.proposals}


@pytest.mark.asyncio
async def test_pass2_tracks_carry_pass1_grade_and_reason(db_with_phase6, monkeypatch):
    """Tracks that went through Pass 2 land in proposal.pass2_tracks with
    the ORIGINAL Pass 1 grade + reason + the Pass 2 reason.
    """
    from app.services import vibe_clusterer as vc

    agg = _make_pass1_aggregate(30)

    async def _fake(*, system_prompt, user_prompt, response_model, **kw):
        if response_model is vc.LLMVibeDefinitionsResponse:
            return _build_definitions_response(["a", "b", "c"])
        if response_model is vc.LLMVibeAssignmentResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            n = len(lines)
            # Make track 0 weak with vibe 'a', everyone else strong.
            assignments = [vc.LLMPass1Assignment(
                track_index=0, vibe_name="a", grade="weak",
                reason="thin signal",
            )]
            for i in range(1, n):
                assignments.append(vc.LLMPass1Assignment(
                    track_index=i, vibe_name=["a", "b", "c"][i % 3],
                    grade="strong", reason="strong fit",
                ))
            return vc.LLMVibeAssignmentResponse(assignments=assignments)
        if response_model is vc.LLMVibeBoundaryResponse:
            lines = [
                ln for ln in user_prompt.splitlines()
                if ln and ln.split(":", 1)[0].strip().isdigit()
            ]
            decisions = []
            for ln in lines:
                import re
                local = int(ln.split(":", 1)[0])
                m = re.search(r"Pass 1 picked '([^']+)'", ln)
                pick = m.group(1) if m else "a"
                decisions.append(vc.LLMPass2Decision(
                    track_index=local, final_vibe_name=pick,
                    reason="boundary-confirmed",
                ))
            return vc.LLMVibeBoundaryResponse(decisions=decisions)
        raise AssertionError(response_model)

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(vc, "get_anthropic_client_v2", lambda s: fake_client)
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: agg)
    monkeypatch.setattr(vc, "materialize_clusters", lambda p: p)

    result = await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

    # Find proposal 'a' — track 0 (the boundary track) landed there.
    prop_a = next(p for p in result.proposals if p.name == "a")
    assert prop_a.pass2_tracks, (
        f"Proposal 'a' should carry a pass2_tracks entry; got {prop_a.pass2_tracks}"
    )
    # The boundary track T0 is in pass2_tracks (ordering inside the list
    # follows recomputed-centroid distance — find by rating_key, not index).
    entry = next(
        (e for e in prop_a.pass2_tracks if e["rating_key"] == "rk_0"),
        None,
    )
    assert entry is not None, (
        f"Expected rk_0 (the weak track) in pass2_tracks; got "
        f"{[e['rating_key'] for e in prop_a.pass2_tracks]}"
    )
    assert entry["pass1_grade"] == "weak"
    assert entry["pass1_reason"] == "thin signal"
    assert entry["pass2_reason"] == "boundary-confirmed"
    assert entry["title"] == "T0"


# ---------------------------------------------------------------------------
# Input validation tests for assign_tracks_to_user_vibes (mirror Phase 6.1
# map_user_vibes_to_clusters validation — same gates, new function name).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_tracks_rejects_count_below_3():
    from app.services.vibe_clusterer import assign_tracks_to_user_vibes
    with pytest.raises(ValueError, match="vibe count must be 3-7"):
        await assign_tracks_to_user_vibes(["workout", "focus"])


@pytest.mark.asyncio
async def test_assign_tracks_rejects_count_above_7():
    from app.services.vibe_clusterer import assign_tracks_to_user_vibes
    with pytest.raises(ValueError, match="vibe count must be 3-7"):
        await assign_tracks_to_user_vibes(
            ["a", "b", "c", "d", "e", "f", "g", "h"]
        )


@pytest.mark.asyncio
async def test_assign_tracks_rejects_blank_names():
    from app.services.vibe_clusterer import assign_tracks_to_user_vibes
    with pytest.raises(ValueError, match="must not be blank"):
        await assign_tracks_to_user_vibes(["workout", "  ", "focus"])


@pytest.mark.asyncio
async def test_assign_tracks_rejects_case_insensitive_duplicates():
    from app.services.vibe_clusterer import assign_tracks_to_user_vibes
    with pytest.raises(ValueError, match="duplicate vibe names"):
        await assign_tracks_to_user_vibes(["Workout", "workout", "focus"])


@pytest.mark.asyncio
async def test_assign_tracks_cold_start_under_30(monkeypatch):
    from app.services import vibe_clusterer as vc
    monkeypatch.setattr(vc, "_aggregate_rated_set_sync", lambda: {
        "rated_track_count": 20,
        "feature_matrix": vc.np.zeros((20, 4)),
        "rated_track_index_map": [],
        "tracks_for_prompt": [],
        "top_artists": [],
        "top_genres": [],
    })
    with pytest.raises(ValueError, match="cold-start"):
        await vc.assign_tracks_to_user_vibes(["a", "b", "c"])

