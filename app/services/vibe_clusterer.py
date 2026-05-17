"""Phase 6 vibe clusterer (D-32, D-33, D-34, D-35; VIBE-05, VIBE-06).

scikit-learn k-means + silhouette + AnthropicClient LLM naming + conversational
refinement turn. Plan 02 ships this module; Plan 03's wizard ``/setup/propose``
imports ``initial_cluster_proposal`` and ``refine_proposals`` verbatim.

DESIGN INVARIANTS — DO NOT silently change:

1. **sklearn import is allowed in this file ONLY (D-33).** The static AST test
   ``tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module``
   enforces the allowlist. Any sklearn leak into another service file fails
   the test.

2. **z-score normalization is mandatory before any distance math (D-05).**
   Tempo's [60, 180] BPM range cannot co-exist with energy / danceability /
   valence's [0, 1] in raw Euclidean — the tempo dimension would dominate.
   ``_z_score_normalize`` is the single helper.

3. **n_rated < 30 → degraded_mode=True with a single Your Taste proposal AND
   no LLM call (Pitfall 3 / D-10).** Defense in depth — the wizard's gate
   matches the clusterer's gate. The LLM is never billed for cold-start.

4. **k_max = min(7, n_rated // 15); k loop is 3..k_max (Pitfall 3 / VIBE-06).**
   Best silhouette wins; ``degraded_mode=True`` if best score < 0.25.
   ``forced_k`` overrides the loop.

5. **AnthropicClient is the single LLM call site (D-04 / D-34).** Phase 5
   inheritance: explicit ``ttl="1h"`` cache, structured output via
   ``model_validate_json``, automatic ``LLMUsage`` row per call (cost
   telemetry). No Instructor; no httpx; no per-call cache config.

6. **VibeProposal.seed_track_indices are integer indices into the
   rated_track_index_map (Pitfall 10 / D-03).** NEVER raw ratingKey strings.
   Server-side validates ``0 <= idx < n_rated`` after every LLM response;
   one retry with corrective prompt; second failure raises ``ValueError``.

7. **purpose=vibe_clustering_initial / refine / recluster (D-34).** Lets the
   Phase 7 settings cost dashboard segment cost by feature.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from typing import List, Literal, Optional, Union

import numpy as np
from pydantic import BaseModel, Field
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sqlmodel import Session, select

from app.database import get_engine
from app.models.track import Track

logger = logging.getLogger(__name__)


# Numerical guard for z-score divide-by-zero on a constant column.
_STD_FLOOR = 1e-6

# Cold-start gate (D-10 / Pitfall 3).
COLD_START_FLOOR = 30
# Silhouette threshold for non-degraded clusters (Pitfall 3).
SILHOUETTE_THRESHOLD = 0.25


# ---------------------------------------------------------------------------
# Pydantic schemas — D-03 / D-32
# ---------------------------------------------------------------------------

class VibeProposal(BaseModel):
    """A single proposed vibe in the LLM cluster set.

    ``seed_track_indices`` are integer indices into the
    ``VibeProposalSet.rated_track_index_map`` — NEVER raw ratingKey strings
    (Pitfall 10). ``centroid`` and ``spread`` are server-computed during
    ``materialize_clusters`` — the LLM never sets these.

    Phase 6.1 extensions:
    - ``seed_tracks`` + ``members`` + ``member_count`` (Blocker #1): server-
      populated, JSON-serialisable lists used by ``vibe_proposal_card.html``
      (seed_tracks → "Closest tracks" rows) and ``vibe_members_disclosure.html``
      (members → "Show all N tracks" panel). Each entry is
      ``{"title": str, "artist": str, "rating_key": str}``.
    - ``fit`` + ``fit_reason`` (D-NEW-08/11): user-led mapping fit grade per
      proposal. ``fit`` is None for refinement-loop-derived proposals UNLESS
      carried over by case-insensitive name match in
      :func:`_call_llm_with_validation` via :func:`_carryover_fit_from_prior`
      (Blocker #5 Option A).

    Phase 6.2 extension:
    - ``pass2_tracks`` — per-track confidence chip data for tracks that went
      through Pass 2 boundary review (D-17). Each entry is
      ``{"rating_key": str, "title": str, "artist": str,
      "pass1_grade": "weak"|"uncertain", "pass1_reason": str,
      "pass2_reason": str}``. Rendered by ``vibe_proposal_card.html``'s
      Pass-2 disclosure section (Task 3).
    """

    name: str
    description: str
    action: Literal[
        "keep", "new", "merged_from", "split_from", "renamed_from", "dropped"
    ]
    source_vibe_ids: List[int] = Field(default_factory=list)
    seed_track_indices: List[int] = Field(default_factory=list)
    centroid: Optional[dict] = None
    spread: Optional[dict] = None
    silhouette: Optional[float] = None
    # Phase 6.1 Blocker #1 — server-populated, JSON-serialisable lists used
    # by vibe_proposal_card.html (seed_tracks → "Closest tracks" rows) and
    # vibe_members_disclosure.html (members → "Show all N tracks" panel).
    # Each entry is {"title": str, "artist": str, "rating_key": str}.
    seed_tracks: List[dict] = Field(default_factory=list)
    members: List[dict] = Field(default_factory=list)
    # member_count = len(members); used by the fit-chip display in
    # vibe_proposal_card.html. Falls back to len(seed_track_indices) in
    # template default-filter when this is None.
    member_count: Optional[int] = None
    # Phase 6.1 D-NEW-08 — fit grade per proposal (user-led mapping only;
    # None for refinement-loop-derived proposals UNLESS carried over by
    # name match in _call_llm_with_validation — Blocker #5 Option A).
    fit: Optional[Literal["strong", "weak", "no_match"]] = None
    # Phase 6.1 D-NEW-11 — LLM's rationale when fit == "no_match".
    fit_reason: Optional[str] = None
    # Phase 6.2 D-17 — per-track Pass-2 confidence chip data. Empty list
    # when no track in this proposal went through boundary review.
    pass2_tracks: List[dict] = Field(default_factory=list)
    # Phase 8 UI-09 / D-E2 — preview of the color the vibe will inherit on
    # commit. Populated by the proposal-rendering handler via
    # :func:`app.services.discovery_service.assign_vibe_color` with the
    # proposal's index+1 so the wizard card matches what /vibes will show
    # after finalize. ``None`` for legacy / pre-Phase-8 cached proposals.
    proposed_color: Optional[str] = None


class LLMVibeProposal(BaseModel):
    """LLM-side proposal — permissive on ``source_vibe_ids`` (quick-260510-i1q).

    Pre-finalize (during refinement turns), prior proposals have NO database IDs
    yet. The clusterer's user-prompt builder injects a stable positional integer
    ``id`` per prior proposal, but Claude has been observed to fall back to the
    vibe name string as the most stable identifier visible in the prompt — e.g.
    ``source_vibe_ids: ['Neon Nights']`` — which the strict canonical
    :class:`VibeProposal` schema (``List[int]``) rejects, raising a 7-error
    ``ValidationError`` at the structured-output boundary and crashing the
    wizard refinement endpoint (live prod stack trace, May 2026).

    The fix is two-layer:
    1. The LLM-side schema (THIS class) is permissive — ``List[Union[int, str]]``.
    2. The server resolves strings → integer positional indices via
       :func:`_resolve_source_vibe_ids` BEFORE assembling the canonical
       :class:`VibeProposalSet`, then drops any string that can't be name-matched
       against ``prior_proposals``.

    The canonical :class:`VibeProposal` stays strict (``source_vibe_ids:
    List[int]``) — the permissiveness lives ONLY at the LLM ingest boundary.
    """

    name: str
    description: str
    action: Literal[
        "keep", "new", "merged_from", "split_from", "renamed_from", "dropped"
    ]
    source_vibe_ids: List[Union[int, str]] = Field(default_factory=list)
    seed_track_indices: List[int] = Field(default_factory=list)
    centroid: Optional[dict] = None
    spread: Optional[dict] = None
    silhouette: Optional[float] = None


class VibeProposalSetLLMResponse(BaseModel):
    """Slim LLM contract — the only fields the LLM is asked to return.

    The server assembles the full :class:`VibeProposalSet` from this slim
    response plus aggregate / clustering parameters. Keeping the LLM-facing
    model narrow eliminates an entire class of LLM-shape-mismatch bugs at the
    structured-output boundary (e.g. Claude returning ``rated_track_index_map:
    {}`` for an empty collection — `quick-260510-das` regression).

    ``proposals`` is :class:`LLMVibeProposal` (permissive on ``source_vibe_ids``)
    — the server normalizes to canonical :class:`VibeProposal` inside
    :func:`_call_llm_with_validation` via :func:`_resolve_source_vibe_ids`
    (quick-260510-i1q).

    DO NOT add server-controlled fields here. The defensive test
    ``test_slim_llm_response_schema_has_no_rated_track_index_map_field`` will
    fail loudly if anything other than ``proposals`` lands on this model.
    """

    proposals: List[LLMVibeProposal]


# ---------------------------------------------------------------------------
# Phase 6.2 slim LLM schemas — D-04, D-09, D-17, D-33
#
# These REPLACE the Phase 6.1 user-led-mapping slim schemas (deleted with
# the function rewrite per D-18). Each new schema mirrors the slim →
# canonical pattern: the LLM is asked for the narrowest plausible response
# shape, server resolves identifiers and assembles the full VibeProposalSet
# from aggregate / clustering parameters.
# ---------------------------------------------------------------------------


class LLMVibeDefinition(BaseModel):
    """D-04 preamble entry — one definition per user-typed vibe name.

    The LLM is asked to write one sentence per vibe name describing the kind
    of track that belongs there. The result is baked into the cached Pass 1
    system prompt so the LLM has a stable anchor across all batches.
    """

    vibe_name: str
    definition: str


class LLMVibeDefinitionsResponse(BaseModel):
    """Slim LLM contract for the D-04 preamble call.

    Defensive test ``test_slim_definitions_response_has_no_server_controlled_fields``
    enforces narrowness.
    """

    definitions: List[LLMVibeDefinition]


class LLMPass1Assignment(BaseModel):
    """D-01..D-03 — one row per assigned track in a Pass 1 batch.

    ``track_index`` echoes the per-batch LOCAL integer index 0..batch_size-1
    that the server included in the user prompt (NOT the global rated-track
    index; the server resolves batch-local → global on receipt). ``vibe_name``
    is casefold-resolved server-side against the user-typed name set.
    ``reason`` is REQUIRED per D-03 (never Optional — Pydantic will raise on
    missing fields under output pressure).
    """

    track_index: int
    vibe_name: str
    grade: Literal["strong", "weak", "uncertain"]
    reason: str


class LLMVibeAssignmentResponse(BaseModel):
    """Slim Pass 1 contract. Defensive test forbids any other field."""

    assignments: List[LLMPass1Assignment]


class LLMPass2Decision(BaseModel):
    """D-17 — final commitment per Pass 2 boundary track.

    No grade re-issued. ``track_index`` is the per-batch LOCAL integer index
    0..batch_size-1 (NOT global). ``final_vibe_name`` is casefold-resolved
    server-side against the user-typed name set. The original Pass 1 grade
    survives in ``VibeProposal.pass2_tracks[].pass1_grade`` for the
    confidence-chip UI.
    """

    track_index: int
    final_vibe_name: str
    reason: str


class LLMVibeBoundaryResponse(BaseModel):
    """Slim Pass 2 contract. Defensive test forbids any other field."""

    decisions: List[LLMPass2Decision]


class VibeProposalSet(BaseModel):
    """The full LLM proposal — Plan 03's wizard consumes this verbatim.

    Server-controlled fields (``rated_track_count``, ``silhouette_avg``,
    ``forced_k``, ``degraded_mode``, ``rated_track_index_map``) are populated
    inside :func:`_call_llm_with_validation` after the slim LLM response is
    validated. The LLM never sees these fields — see
    :class:`VibeProposalSetLLMResponse`.
    """

    proposals: List[VibeProposal]
    rated_track_count: int
    silhouette_avg: Optional[float] = None
    forced_k: Optional[int] = None
    degraded_mode: bool = False
    rated_track_index_map: List[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sync helpers — all called via asyncio.to_thread (D-09 invariant)
# ---------------------------------------------------------------------------

def _aggregate_rated_set_sync() -> dict:
    """Query rated set, drop incomplete-feature rows, return clusterer-ready aggregate.

    Mirrors taste_profile_service._aggregate_rated_set_sync but additionally
    returns ``rated_track_index_map`` and ``feature_matrix`` for the LLM
    prompt + k-means inputs.

    Skip rows where ANY of energy / tempo / danceability / valence is None —
    the clusterer cannot reason about partial vectors. Such tracks are
    handled by the D-17 pending_slot_in retroactive path on the slot-in side.
    """
    with Session(get_engine()) as session:
        stmt = select(Track).where(Track.user_rating > 0)
        all_tracks: List[Track] = list(session.exec(stmt).all())

    rated_track_index_map: List[dict] = []
    feature_rows: List[List[float]] = []
    tracks_for_prompt: List[dict] = []

    for t in all_tracks:
        if (
            t.energy is None
            or t.tempo is None
            or t.danceability is None
            or t.valence is None
        ):
            continue
        idx = len(rated_track_index_map)
        rated_track_index_map.append(
            {
                "index": idx,
                "rating_key": t.plex_rating_key,
                "title": t.title,
                "artist": t.artist,
            }
        )
        feature_rows.append([t.energy, t.tempo, t.danceability, t.valence])
        tracks_for_prompt.append(
            {
                "title": t.title,
                "artist": t.artist,
                "energy": t.energy,
                "tempo": t.tempo,
                "danceability": t.danceability,
                "valence": t.valence,
                "rating": t.user_rating,
                # WARNING #2 (Phase 6.1): per-track genre, used by per-cluster
                # top_genres aggregation in the assignment pipeline. Track.genre
                # is a (possibly empty) comma-separated string.
                "genre": t.genre or "",
            }
        )

    feature_matrix = (
        np.array(feature_rows, dtype=float) if feature_rows else np.zeros((0, 4))
    )

    # Top 10 artists / genres for the cached system prompt.
    artist_counts = Counter(t.artist for t in all_tracks if t.artist)
    top_artists = [
        {"artist": a, "count": c} for a, c in artist_counts.most_common(10)
    ]
    all_genres: List[str] = []
    for t in all_tracks:
        if t.genre:
            all_genres.extend(g.strip() for g in t.genre.split(",") if g.strip())
    genre_counts = Counter(all_genres)
    top_genres = [
        {"genre": g, "count": c} for g, c in genre_counts.most_common(10)
    ]

    return {
        "rated_track_count": len(rated_track_index_map),
        "feature_matrix": feature_matrix,
        "rated_track_index_map": rated_track_index_map,
        "tracks_for_prompt": tracks_for_prompt,
        "top_artists": top_artists,
        "top_genres": top_genres,
    }


def _z_score_normalize(values: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """4-D z-score normalization (D-05).

    Tempo's [60, 180] BPM range cannot co-exist with energy / danceability /
    valence's [0, 1] in raw Euclidean — without normalization the tempo
    dimension would dominate distance calculations by ~120x.

    ``std`` is clamped to ``_STD_FLOOR`` to avoid divide-by-zero on a constant
    column (e.g., all 30 rated tracks happen to share the same tempo).
    """
    safe_std = np.where(std < _STD_FLOOR, _STD_FLOOR, std)
    return (values - mean) / safe_std


def _pick_best_k(
    X_normalized: np.ndarray,
    n_rated: int,
    forced_k: Optional[int],
):
    """Run k-means for k in 3..k_max=min(7, n_rated//15); pick silhouette argmax.

    Returns (chosen_k, labels, centroids_in_normalized_space, silhouette,
    degraded_mode_flag).

    forced_k overrides the loop and runs k-means at exactly that k.
    degraded_mode=True if best silhouette < SILHOUETTE_THRESHOLD (0.25).
    """
    k_max = min(7, n_rated // 15)
    if k_max < 3:
        # Defensive: shouldn't reach here because the cold-start gate fires
        # earlier, but keep a safety floor.
        k_max = 3

    if forced_k is not None:
        k = forced_k
        km = KMeans(n_clusters=k, n_init="auto", random_state=42).fit(X_normalized)
        sil = float(silhouette_score(X_normalized, km.labels_))
        degraded = sil < SILHOUETTE_THRESHOLD
        return k, km.labels_, km.cluster_centers_, sil, degraded

    best = None
    for k in range(3, k_max + 1):
        km = KMeans(n_clusters=k, n_init="auto", random_state=42).fit(X_normalized)
        sil = float(silhouette_score(X_normalized, km.labels_))
        if best is None or sil > best[3]:
            best = (k, km.labels_, km.cluster_centers_, sil)

    if best is None:
        # Should never happen; fail loudly.
        raise RuntimeError(
            f"_pick_best_k could not fit any k in [3, {k_max}] (n_rated={n_rated})"
        )
    chosen_k, labels, centroids, sil = best
    degraded = sil < SILHOUETTE_THRESHOLD
    return chosen_k, labels, centroids, sil, degraded


def _build_clustering_system_prompt(
    rated_count: int,
    top_artists: list,
    top_genres: list,
    tracks_for_prompt: list,
    rated_track_index_map: list,
) -> str:
    """Build a system prompt padded above 2048 tokens to engage Sonnet 4.6 caching.

    Mirrors taste_profile_service._build_system_prompt — the long Composer
    context block at the bottom is the bulk of the padding. Identical across
    refinement-turn calls within 1h TTL → cache hit → input cost ~10% of
    turn 1 in subsequent turns.
    """
    parts = [
        "You are Composer's vibe clusterer. Your job: name and describe each "
        "k-means cluster the system has already computed over the user's rated "
        "music library. You can also propose a refined cluster set: rename, "
        "merge, split, drop, or add new vibes. You operate at the cluster-set "
        "level only — never move individual tracks between clusters.",
        "",
        "## User library structured stats",
        f"Rated track count: {rated_count}",
    ]
    if top_artists:
        parts.append("Top 10 artists by rated count:")
        for a in top_artists:
            parts.append(f"  - {a['artist']} ({a['count']} rated tracks)")
    if top_genres:
        parts.append("Top 10 genres by rated count:")
        for g in top_genres:
            parts.append(f"  - {g['genre']} ({g['count']})")
    parts.append("")
    parts.append("## Numbered rated tracks (use these integer indices in seed_track_indices)")
    for i, t in enumerate(tracks_for_prompt):
        parts.append(
            f"{i}: {t['title']} — {t['artist']} "
            f"(energy={t['energy']:.2f}, tempo={t['tempo']:.0f}, "
            f"dance={t['danceability']:.2f}, valence={t['valence']:.2f})"
        )
    parts.append("")
    parts.append("## Output format")
    parts.append(
        'Return JSON matching VibeProposalSetLLMResponse: '
        '{"proposals": [{"name": "...", "description": "...", "action": "...", '
        '"source_vibe_ids": [...], "seed_track_indices": [...]}, ...]}'
    )
    parts.append(
        "action ∈ {keep, new, merged_from, split_from, renamed_from, dropped}. "
        "seed_track_indices MUST be integers in [0, rated_track_count). "
        "NEVER use raw Plex ratingKey strings (Pitfall 10). "
        "Do NOT set centroid / spread / silhouette — server computes those. "
        'Return ONLY {"proposals": [...]}. The server populates rated_track_count, '
        "silhouette_avg, forced_k, degraded_mode, and rated_track_index_map."
    )
    parts.append("")
    parts.append(
        "## Composer context (cacheable; identical across calls within 1h TTL)\n"
        "Composer is a self-hosted music companion that turns Plex star ratings "
        "into living vibe playlists and a continuous suggestions queue. The user "
        "has rated tracks in Plex (0-10 raw scale; binary in practice — 5★ or "
        "unrated). Composer reads userRating values from Plex via webhook + poll, "
        "computes audio features via Essentia (energy from spectral RMS, tempo "
        "via beat tracking, danceability via spectral complexity, valence from "
        "mode/danceability/brightness/pitch_salience). The 4-D feature space is "
        "(energy, tempo, danceability, valence) — z-score normalized before any "
        "distance math because tempo's 60-180 BPM range cannot co-exist with the "
        "0-1 scales of the others. Vibes are persistent named clusters that map "
        "1-to-1 to Plex playlists named 'Composer · {name}'. Phase 6 ships the "
        "first wizard that creates 3-7 vibes; the user can rename, merge, split, "
        "drop, or add vibes via natural-language refinement. After commit, every "
        "RatingChanged event auto-slots the track into matching vibes (closest "
        "centroid + soft 2nd vibe within 1 std-dev margin, capped at 2 per "
        "track). The user listens via Plexamp on iOS or Plex Web. The full "
        "library lives on a Synology NAS, synced from Plex via APScheduler. "
        "Vibes are persistent — never recomputed automatically; user-triggered "
        "only via the Re-cluster vibes button. Manual track moves between vibes "
        "(via direct DB tweaks for now) are sticky and survive re-cluster. "
        "Composer is a single FastAPI process with a single SQLite file; no "
        "separate worker, no Redis, no Celery. Anthropic prompt caching uses "
        "ttl=1h explicitly because the default silently regressed to 5min in "
        "March 2026. The Sonnet 4.6 cache breakpoint is 2048 tokens minimum. "
        "Plex webhooks at /api/webhooks/plex deliver media.rate, media.scrobble, "
        "and library.new events; an APScheduler polling job catches whatever the "
        "webhook missed. The event bus is a single asyncio.Queue with a single "
        "dispatcher task for SQLite write serialization. Dedupe is "
        "sha256(event_type|ratingKey|user_rating|5s_bucket) with INSERT OR "
        "IGNORE on a UNIQUE constraint. The taste profile (single-row TasteProfile "
        "id=1) is recomputed when the rated set grows or shrinks by ≥10% since "
        "last computed_at. The refinement loop is the defining UX of v2.0: "
        "LLM proposes → user gives natural-language feedback → LLM refines → "
        "commit. Soft cap of 10 refinement turns per session (counter shown in "
        "UI). After 10, surface a 'save what you have or start over' choice. "
        "The clusterer's job during refinement is to take the prior proposal "
        "set + user feedback and emit a revised proposal set with the same "
        "VibeProposalSet schema. Use action='keep' when a vibe survives "
        "unchanged; 'renamed_from' when only the name changes; 'merged_from' "
        "with source_vibe_ids when two vibes combine; 'split_from' when one "
        "becomes two (emit two proposals each with split_from + the same "
        "source_vibe_id); 'new' for brand-new vibes (provide 5-15 "
        "seed_track_indices that anchor the new cluster); 'dropped' to "
        "discard a vibe. Naming guidance: short evocative names (Late Night, "
        "Workout, Sunday Morning), one-line descriptions in the user's "
        "vernacular, no clinical labels like 'Cluster 3'."
    )
    return "\n".join(parts)


def _build_clustering_user_prompt(
    prior_proposals: Optional[VibeProposalSet],
    user_message: str,
) -> str:
    """Build the (uncached) user prompt for initial vs refinement turns.

    For refinement turns, each prior proposal in the JSON dump is labeled with a
    stable positional integer ``id`` field, and the prompt EXPLICITLY instructs
    Claude to use that integer (not the vibe name string) when populating
    ``source_vibe_ids`` on the revised proposal set. Without this anchor, Claude
    falls back to the most stable identifier visible — the name string — which
    crashes Pydantic validation on the strict canonical ``source_vibe_ids:
    List[int]`` schema (quick-260510-i1q regression).
    """
    if prior_proposals is None:
        return (
            "Propose 3-7 vibes for this user, naming each by feel + describing "
            "each in one line. Return as VibeProposalSet matching the Pydantic "
            "schema."
        )
    prior_with_ids = [
        {"id": i, **p.model_dump(exclude={"centroid", "spread", "silhouette"})}
        for i, p in enumerate(prior_proposals.proposals)
    ]
    return (
        f"Prior proposals (each labeled with a stable integer 'id'):\n"
        f"{json.dumps(prior_with_ids, indent=2)}\n\n"
        f"User feedback: {user_message}\n\n"
        f"When you reference a prior vibe in source_vibe_ids, use the integer "
        f"'id' field shown above (NOT the name string). Example: a new vibe "
        f"merging the vibes with id=2 and id=5 sets source_vibe_ids=[2, 5].\n\n"
        f"Return the revised VibeProposalSet matching the schema."
    )


def _validate_seed_indices(
    proposals: "List[LLMVibeProposal] | List[VibeProposal]",
    n_rated: int,
) -> None:
    """Raise ValueError if any seed_track_indices is out of [0, n_rated). Pitfall 10.

    Accepts either the slim LLM-side proposals or the canonical ones — they
    share an identically-typed ``seed_track_indices: List[int]`` field, so the
    range check works on either shape (quick-260510-i1q).
    """
    for prop in proposals:
        for idx in prop.seed_track_indices:
            if not (0 <= idx < n_rated):
                raise ValueError(
                    f"seed_track_indices contains out of range index {idx} "
                    f"(valid: 0..{n_rated - 1}); proposal name={prop.name!r}"
                )


# NOTE: Phase 6.1's user-led-mapping helpers were deleted in Phase 6.2
# Plan 01 (D-18 — "no sibling, no _v2 suffix, no dead code"). The two-pass
# LLM-direct pipeline in assign_tracks_to_user_vibes replaces them outright.
# The new private helpers live below near the existing
# _build_clustering_system_prompt.


def _carryover_fit_from_prior(
    new_proposal: "VibeProposal",
    prior_proposals: Optional[List["VibeProposal"]],
) -> "VibeProposal":
    """Blocker #5 Option A (Phase 6.1) — preserve fit + fit_reason through the
    refine round-trip.

    The LLM-side :class:`LLMVibeProposal` does NOT carry ``fit``/``fit_reason``;
    so during refine the canonicalization step
    (``LLMVibeProposal → VibeProposal``) drops those fields by default. This
    helper looks up the new proposal's ``name`` in ``prior_proposals`` via
    case-insensitive match and copies ``fit`` + ``fit_reason`` from the matched
    prior.

    Lookup is ``.strip().casefold()`` on ``name``. If the new proposal's name
    has no case-insensitive match in priors (e.g. action="new" → vibe added in
    this refine turn), fit stays None.

    Idempotent: if ``new_proposal`` already has ``fit`` set (e.g. the LLM
    unexpectedly populated it), DO NOT overwrite.
    """
    if new_proposal.fit is not None:
        return new_proposal
    if not prior_proposals:
        return new_proposal
    key = new_proposal.name.strip().casefold()
    for prior in prior_proposals:
        if prior.name.strip().casefold() == key and prior.fit is not None:
            # Pydantic v2: model_copy with update is the safe path.
            return new_proposal.model_copy(update={
                "fit": prior.fit,
                "fit_reason": prior.fit_reason,
            })
    return new_proposal


def _resolve_source_vibe_ids(
    llm_ids: List[Union[int, str]],
    prior_proposals: Optional[VibeProposalSet],
) -> List[int]:
    """Convert LLM-supplied source_vibe_ids → integer positional indices.

    The LLM-side :class:`LLMVibeProposal` accepts ``List[Union[int, str]]`` to
    tolerate Claude returning vibe names instead of integer IDs (quick-260510-i1q
    prod crash on 585-track library: 7 ValidationError entries like
    ``source_vibe_ids.0 / input_value='Neon Nights'``).

    Rules:
    - Integers pass through verbatim.
    - Strings are looked up case-insensitively (``.strip().casefold()``) against
      the prior_proposals' names → integer positional index.
    - Unmatched strings are dropped with ``logger.warning`` (defensive — better
      to lose a source attribution than crash the wizard).
    - If ``prior_proposals is None`` (initial turn — no priors), strings are
      dropped silently. Initial-turn proposals SHOULD use empty
      ``source_vibe_ids`` (every proposal is ``action="new"``), so this path is
      defensive against a wandering LLM.
    """
    resolved: List[int] = []
    name_to_index: dict = {}
    if prior_proposals is not None:
        name_to_index = {
            p.name.strip().casefold(): i
            for i, p in enumerate(prior_proposals.proposals)
        }
    for entry in llm_ids:
        if isinstance(entry, int):
            resolved.append(entry)
            continue
        if isinstance(entry, str):
            key = entry.strip().casefold()
            if key in name_to_index:
                resolved.append(name_to_index[key])
            else:
                logger.warning(
                    "vibe_clusterer: LLM source_vibe_ids contained "
                    "unresolvable name %r; dropping",
                    entry,
                )
    return resolved


def _canonicalize_llm_proposals(
    llm_proposals: List[LLMVibeProposal],
    prior_proposals: Optional[VibeProposalSet],
) -> List[VibeProposal]:
    """Convert a list of permissive LLM proposals → strict canonical proposals.

    Walks every :class:`LLMVibeProposal` and rewrites ``source_vibe_ids`` via
    :func:`_resolve_source_vibe_ids` (strings → positional indices); all other
    fields pass through unchanged. The canonical :class:`VibeProposal` stays
    strict on ``source_vibe_ids: List[int]`` — the permissiveness lives ONLY at
    the LLM ingest boundary (quick-260510-i1q).
    """
    canonical: List[VibeProposal] = []
    for p in llm_proposals:
        canonical.append(
            VibeProposal(
                name=p.name,
                description=p.description,
                action=p.action,
                source_vibe_ids=_resolve_source_vibe_ids(
                    p.source_vibe_ids, prior_proposals
                ),
                seed_track_indices=p.seed_track_indices,
                centroid=p.centroid,
                spread=p.spread,
                silhouette=p.silhouette,
            )
        )
    return canonical


# ---------------------------------------------------------------------------
# Cluster materialization (D-05) — pure numpy + sklearn
# ---------------------------------------------------------------------------

def _proposal_track_indices_for(
    proposal: VibeProposal,
    seed_indices_to_features: dict,
) -> List[int]:
    """Return the integer indices the proposal applies to.

    For ``new`` actions we use seed_track_indices; for the others we expect the
    server-side caller to have already populated source-vibe member indices.
    """
    return list(proposal.seed_track_indices)


def materialize_clusters(proposals: VibeProposalSet) -> VibeProposalSet:
    """Constrained k-means per D-05; fills centroid + spread + silhouette per proposal.

    Operates on the rated_track_index_map already attached to the
    VibeProposalSet. For each proposal:
      - keep / renamed_from: tracks come from source_vibe_ids' members
        (currently we use seed_track_indices since Plan 02's initial-pass
        flow populates them server-side from the k-means labels).
      - merged_from: union the source vibes' tracks; recompute single centroid.
      - split_from: KMeans(k=2) within the source vibe's tracks.
      - new: KMeans seeded from seed_track_indices' centroid; pull additional
        tracks by distance (1 sklearn KMeans fit with init=seed_centroid).
      - dropped: skip.
    Returns the same proposal list with centroid + spread + silhouette filled.
    """
    n_rated = proposals.rated_track_count
    index_map = {
        row["index"]: row for row in proposals.rated_track_index_map
    }

    # Build a feature matrix indexed by rated_track index (0..n_rated-1).
    # We need the raw feature values here; re-query the DB by rating_key.
    feature_by_index: dict[int, np.ndarray] = {}
    rating_keys_in_order = [
        index_map[i]["rating_key"] for i in range(n_rated) if i in index_map
    ]
    if rating_keys_in_order:
        with Session(get_engine()) as session:
            stmt = select(Track).where(
                Track.plex_rating_key.in_(rating_keys_in_order)  # type: ignore[union-attr]
            )
            tracks = list(session.exec(stmt).all())
        track_by_key = {t.plex_rating_key: t for t in tracks}
        for i in range(n_rated):
            if i not in index_map:
                continue
            t = track_by_key.get(index_map[i]["rating_key"])
            if t is None or any(
                v is None for v in (t.energy, t.tempo, t.danceability, t.valence)
            ):
                continue
            feature_by_index[i] = np.array(
                [t.energy, t.tempo, t.danceability, t.valence], dtype=float
            )

    if not feature_by_index:
        return proposals

    full_matrix = np.array(list(feature_by_index.values()))
    mean = full_matrix.mean(axis=0)
    std = full_matrix.std(axis=0)

    for prop in proposals.proposals:
        if prop.action == "dropped":
            continue
        member_indices = _proposal_track_indices_for(prop, feature_by_index)
        member_indices = [i for i in member_indices if i in feature_by_index]
        if len(member_indices) == 0:
            continue

        member_features = np.array(
            [feature_by_index[i] for i in member_indices], dtype=float
        )
        centroid_raw = member_features.mean(axis=0)
        spread_raw = member_features.std(axis=0)

        prop.centroid = {
            "energy": float(centroid_raw[0]),
            "tempo": float(centroid_raw[1]),
            "danceability": float(centroid_raw[2]),
            "valence": float(centroid_raw[3]),
        }
        prop.spread = {
            "energy": float(spread_raw[0]),
            "tempo": float(spread_raw[1]),
            "danceability": float(spread_raw[2]),
            "valence": float(spread_raw[3]),
        }

        # Silhouette per proposal — labels are 0 for in-cluster, 1 for
        # out-of-cluster. Need both labels present for silhouette_score.
        if len(feature_by_index) > len(member_indices) and len(member_indices) >= 2:
            X_norm = _z_score_normalize(full_matrix, mean, std)
            labels = np.zeros(len(full_matrix), dtype=int)
            indices_list = list(feature_by_index.keys())
            in_set = set(member_indices)
            for j, idx in enumerate(indices_list):
                labels[j] = 0 if idx in in_set else 1
            try:
                prop.silhouette = float(silhouette_score(X_norm, labels))
            except Exception:
                # Silhouette undefined for some degenerate cases; persist 0.0.
                prop.silhouette = 0.0
        else:
            prop.silhouette = 0.0

    return proposals


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def initial_cluster_proposal(
    forced_k: Optional[int] = None,
) -> VibeProposalSet:
    """Initial wizard step 3 cluster proposal (VIBE-05 + VIBE-06).

    Pipeline:
    1. Aggregate the rated set (drop incomplete-feature rows).
    2. Cold-start gate: n_rated < 30 → return degraded_mode set, NO LLM call.
    3. z-score normalize the 4-D feature matrix.
    4. _pick_best_k: silhouette-argmax (or forced_k override).
    5. Build cached system prompt + user prompt.
    6. Call AnthropicClient with purpose=vibe_clustering_initial.
    7. Validate seed_track_indices in range; one retry on out-of-range.
    8. Materialize clusters (centroid + spread + silhouette per proposal).
    9. Return.
    """
    agg = await asyncio.to_thread(_aggregate_rated_set_sync)
    n_rated = agg["rated_track_count"]

    # D-10 / Pitfall 3: cold-start single Your Taste degraded proposal.
    if n_rated < COLD_START_FLOOR:
        return VibeProposalSet(
            proposals=[
                VibeProposal(
                    name="Your Taste",
                    description=(
                        "Composer needs ~30 rated tracks to find distinct "
                        "vibes. Your full rated set is one cluster for now."
                    ),
                    action="new",
                    source_vibe_ids=[],
                    seed_track_indices=list(range(n_rated)),
                )
            ],
            rated_track_count=n_rated,
            silhouette_avg=None,
            forced_k=forced_k,
            degraded_mode=True,
            rated_track_index_map=agg["rated_track_index_map"],
        )

    feature_matrix: np.ndarray = agg["feature_matrix"]
    mean = feature_matrix.mean(axis=0)
    std = feature_matrix.std(axis=0)
    X_normalized = _z_score_normalize(feature_matrix, mean, std)

    chosen_k, labels, _centroids_norm, sil, degraded = _pick_best_k(
        X_normalized, n_rated, forced_k
    )
    logger.info(
        "vibe_clusterer initial: n_rated=%d k=%d silhouette=%.3f degraded=%s",
        n_rated, chosen_k, sil, degraded,
    )

    # Build prompts + LLM call.
    system_prompt = _build_clustering_system_prompt(
        n_rated,
        agg["top_artists"],
        agg["top_genres"],
        agg["tracks_for_prompt"],
        agg["rated_track_index_map"],
    )
    user_prompt = _build_clustering_user_prompt(prior_proposals=None, user_message="")

    proposals = await _call_llm_with_validation(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        purpose="vibe_clustering_initial",
        n_rated=n_rated,
        rated_track_index_map=agg["rated_track_index_map"],
        forced_k=forced_k,
        degraded=degraded,
        silhouette_avg=sil,
        prior_proposals=None,
    )

    proposals = materialize_clusters(proposals)
    return proposals


async def refine_proposals(
    prior: VibeProposalSet,
    user_message: str,
    recluster_mode: bool = False,
) -> VibeProposalSet:
    """One conversational refinement turn (D-04 / D-34).

    Same cached system prompt as initial (rebuilt fresh each call but
    identical content within 1h TTL → cache hit).
    """
    agg = await asyncio.to_thread(_aggregate_rated_set_sync)
    n_rated = agg["rated_track_count"]

    purpose = (
        "vibe_clustering_recluster" if recluster_mode else "vibe_clustering_refine"
    )

    system_prompt = _build_clustering_system_prompt(
        n_rated,
        agg["top_artists"],
        agg["top_genres"],
        agg["tracks_for_prompt"],
        agg["rated_track_index_map"],
    )
    user_prompt = _build_clustering_user_prompt(
        prior_proposals=prior, user_message=user_message
    )

    # degraded_mode and silhouette_avg propagate from prior unless caller
    # explicitly recomputes; refinement turns reuse the prior measurement.
    proposals = await _call_llm_with_validation(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        purpose=purpose,
        n_rated=n_rated,
        rated_track_index_map=agg["rated_track_index_map"],
        forced_k=prior.forced_k,
        degraded=prior.degraded_mode,
        silhouette_avg=prior.silhouette_avg,
        prior_proposals=prior,
    )

    proposals = materialize_clusters(proposals)
    return proposals


# ===========================================================================
# Phase 6.2 Plan 01 — LLM-direct vibe assignment (VIBE-13 + VIBE-14)
#
# Pipeline: preamble → Pass 1 (assign + self-grade) → Pass 2 (peer-context
# boundary review). K-means runs ONCE for centroid summaries that feed the
# Pass 1 system prompt; its `labels_` are DISCARDED after the Pass 1 prompt
# is built (D-19). Final ``Vibe.centroid_*`` is the mean of LLM-assigned
# members (D-20). The two-pass design fulfills VIBE-13 success criteria 1, 2,
# 3, 4, 5 from ROADMAP.
# ===========================================================================


# Phase 6.2 D-01 / D-16 — batch sizes locked per CONTEXT.md.
PASS1_BATCH_SIZE = 25
PASS2_BATCH_SIZE = 15
# Phase 6.2 D-07 + hotfix 260512-kvs — Pass 1 output token cap.
# NAS UAT (May 2026) observed stop_reason=max_tokens for purpose=vibe_assign_pass1
# at 3000 with 25-track batches; 6000 gives ~2× headroom.
PASS1_MAX_TOKENS = 6000
# Phase 6.2 RESEARCH §4.3 + hotfix 260512-kvs — Pass 2 output cap; thinking
# tokens roll into output. NAS UAT (May 2026) observed stop_reason=max_tokens
# for purpose=vibe_assign_pass2 with only a `thinking` block returned (no
# `text`). On Sonnet 4.6, thinking.budget_tokens=2000 + ~4000 JSON output
# needs >6000; 8000 gives headroom.
PASS2_MAX_TOKENS = 8000
# Phase 6.2 D-04 — preamble output cap (one definition per vibe; short).
PREAMBLE_MAX_TOKENS = 600
# Phase 6.2 D-14 — peer count per candidate vibe in Pass 2 system prompt.
PASS2_PEER_COUNT = 10


def _build_definitions_preamble_system_prompt(user_names: List[str]) -> str:
    """D-04 preamble system prompt. Padded above 2048 tokens for cache engagement.

    The preamble is one short call per re-cluster (cost ~$0.02 per D-32). The
    long Composer-context tail mirrors ``_build_clustering_system_prompt`` so
    the same prompt cache 1h TTL is engaged for prefix tokens that don't
    change between calls.
    """
    parts = [
        "You are Composer's vibe definition writer. The user typed N short "
        "vibe names. Your job: write ONE concise sentence per name describing "
        "the kind of track that belongs in that vibe.",
        "",
        "Definitions should be specific (mention defining audio qualities or "
        "genre cues), short (12-25 words), and useful as anchors for a "
        "downstream classifier that will assign individual tracks to these "
        "vibes by audio features, artist, and genre.",
        "",
        "## Output format",
        'Return JSON matching LLMVibeDefinitionsResponse: '
        '{"definitions": [{"vibe_name": "...", "definition": "..."}, ...]}. '
        "Echo each user-typed vibe_name verbatim (server resolves casing).",
        "",
        _composer_context_padding(),
    ]
    return "\n".join(parts)


def _build_definitions_preamble_user_prompt(user_names: List[str]) -> str:
    """D-04 preamble user prompt. Lists user-typed names; LLM emits one definition each."""
    return (
        "Write one definition per vibe name below. Return JSON matching "
        "LLMVibeDefinitionsResponse.\n\n"
        + "\n".join(f"- {n}" for n in user_names)
    )


def _build_pass1_system_prompt(
    user_names: List[str],
    definitions: List[LLMVibeDefinition],
    cluster_summaries: List[dict],
    n_rated: int,
    top_artists: list,
    top_genres: list,
) -> str:
    """D-04 + D-09 + D-10 + D-19 — cached Pass 1 system prompt.

    Contents:
      - User-typed vibe names + LLM-generated definitions (preamble output).
      - Confidence rubric (strict-both for ``strong`` per D-09).
      - "Uncertainty is encouraged" framing (D-10).
      - K-means cluster summaries (D-19 — context only, NOT membership).
      - Library structured stats (top artists, top genres).
      - Long Composer-context tail for cache-engagement padding.

    The same prompt builds the cached system message for every Pass 1 batch
    within one /propose/init call. Cache hits engage on batches 2..N.
    """
    # Definitions in vibe_name → definition order (echo user_names).
    def_lookup = {d.vibe_name.strip().casefold(): d.definition for d in definitions}
    parts = [
        "You are Composer's vibe assigner. Your job: for each track in the "
        "user prompt's batch, pick the ONE user-typed vibe name that best "
        "fits, grade the assignment as strong | weak | uncertain, and write "
        "a short reason.",
        "",
        "## User-typed vibes (with definitions)",
    ]
    for name in user_names:
        d = def_lookup.get(name.strip().casefold(), "")
        parts.append(f"- **{name}**: {d}")
    parts.append("")
    parts.append("## Confidence rubric (D-09 strict-both for 'strong')")
    parts.append(
        "- **strong**: Artist is a known fit for this vibe AND audio features "
        "clearly align with the vibe's definition. Or genre is a textbook "
        "match AND features align. STRICT-BOTH required — one signal alone "
        "is NOT enough to claim 'strong'."
    )
    parts.append(
        "- **weak**: Exactly one signal aligns. Features look right but "
        "artist is unknown to you, or artist is recognized but features are "
        "average. Track probably belongs here; peer comparison would settle it."
    )
    parts.append(
        "- **uncertain**: No clear signal. Features are middle-of-the-road, "
        "artist not recognized, genre absent or ambiguous, or the track "
        "could fit two vibes equally."
    )
    parts.append("")
    parts.append(
        "## Permission slip (D-10)\n"
        "Uncertainty is encouraged when the signal is mixed — uncertain "
        "tracks get reviewed in a second pass with more context. Over-"
        "confident wrong answers cost more than honest uncertainty."
    )
    parts.append("")
    parts.append(
        "## K-means cluster summaries (context only; D-19)\n"
        "The system pre-computed k-means clusters over the user's 4-D "
        "feature space. These are NOT vibes — they're feature-space scaffolds "
        "to help you triangulate where audio features sit. The user-typed "
        "vibe names above are the real targets. Do NOT echo cluster_index in "
        "your output."
    )
    parts.append(json.dumps(cluster_summaries, indent=2, ensure_ascii=False))
    parts.append("")
    parts.append("## User library structured stats")
    parts.append(f"Rated track count: {n_rated}")
    if top_artists:
        parts.append("Top 10 artists by rated count:")
        for a in top_artists:
            parts.append(f"  - {a['artist']} ({a['count']} rated tracks)")
    if top_genres:
        parts.append("Top 10 genres by rated count:")
        for g in top_genres:
            parts.append(f"  - {g['genre']} ({g['count']})")
    parts.append("")
    parts.append("## Output format")
    parts.append(
        'Return JSON matching LLMVibeAssignmentResponse: '
        '{"assignments": [{"track_index": <int>, "vibe_name": "...", '
        '"grade": "strong|weak|uncertain", "reason": "..."}, ...]}. '
        "track_index ECHOES the per-batch local integer 0..batch_size-1 "
        "from the user prompt — NOT a global library index. "
        "vibe_name is one of the user-typed names listed above (any casing). "
        "grade MUST be exactly strong, weak, or uncertain. "
        "reason MUST be 5-15 words; never empty, never null. "
        "Return EXACTLY one assignment per batch entry, in any order."
    )
    parts.append("")
    parts.append(_composer_context_padding())
    return "\n".join(parts)


def _build_pass1_batch_user_prompt(
    batch_tracks: List[dict],
) -> str:
    """Per-batch Pass 1 user prompt. Each track gets a LOCAL integer index.

    ``batch_tracks`` is a list of dicts (one per track in this batch) with
    title / artist / energy / tempo / danceability / valence / genre keys.
    The server maps local indices back to global rated indices on response.
    """
    parts = ["Assign each of the following tracks to one user-typed vibe. "
             "Use the local integer index in your track_index field."]
    for i, t in enumerate(batch_tracks):
        parts.append(
            f"{i}: {t.get('title', '?')} — {t.get('artist', '?')} "
            f"(genre={t.get('genre', '') or '<unknown>'}, "
            f"energy={t.get('energy', 0):.2f}, tempo={t.get('tempo', 0):.0f}, "
            f"dance={t.get('danceability', 0):.2f}, "
            f"valence={t.get('valence', 0):.2f})"
        )
    parts.append("")
    parts.append(
        'Return {"assignments": [...]} with EXACTLY '
        f"{len(batch_tracks)} entries, one per track index 0..{len(batch_tracks) - 1}."
    )
    return "\n".join(parts)


def _validate_pass1_batch_response(
    response: LLMVibeAssignmentResponse,
    batch_size: int,
    name_lookup: dict,
) -> Optional[str]:
    """Validate a single Pass 1 batch response. Returns None if valid; else
    a corrective-prompt-friendly error string.

    Pitfall 10 / T-062-02..04 checks:
    1. Exactly batch_size assignments.
    2. Each track_index is in [0, batch_size) and appears exactly once.
    3. Each vibe_name resolves via casefold lookup against name_lookup.
    4. Each reason is non-empty (Pydantic already enforces required; extra
       belt-and-suspenders for whitespace-only strings).
    """
    if len(response.assignments) != batch_size:
        return (
            f"Expected {batch_size} assignments (one per track in this batch); "
            f"got {len(response.assignments)}."
        )
    seen: set = set()
    valid_names = ", ".join(sorted(name_lookup.values()))
    for a in response.assignments:
        if not (0 <= a.track_index < batch_size):
            return (
                f"track_index={a.track_index} is out of range [0, {batch_size}). "
                f"Each batch entry has track_index 0..{batch_size - 1}."
            )
        if a.track_index in seen:
            return (
                f"track_index={a.track_index} appears more than once. "
                f"Each batch index must appear exactly once."
            )
        seen.add(a.track_index)
        key = a.vibe_name.strip().casefold()
        if key not in name_lookup:
            return (
                f"vibe_name='{a.vibe_name}' is not in the user-typed list. "
                f"Valid names: [{valid_names}]."
            )
        if not a.reason or not a.reason.strip():
            return (
                f"track_index={a.track_index} has empty reason. "
                f"Reason is REQUIRED (5-15 words)."
            )
    return None


def _compute_pass2_candidates(
    pass1_vibe_name: str,
    feature_vector_norm: np.ndarray,
    centroids_norm_by_name: dict,
    user_names: List[str],
) -> List[str]:
    """D-13 — pick 2 candidate vibes for a boundary track.

    Rule:
    - Candidate #1 is always Pass 1's pick.
    - Candidate #2 is the next-closest vibe by centroid distance, UNLESS
      Pass 1's pick is already the closest in which case #2 is the runner-up
      by distance.

    ``centroids_norm_by_name`` maps user_name → z-score-normalized centroid
    (4-D numpy array). When the user-typed vibe has no LLM-assigned members
    yet (extreme edge case — pre-recompute we may not have centroids), fall
    back to using all-zeros as the centroid which makes distances symmetric
    and the function returns a deterministic pair.
    """
    # Sort all vibes by distance ascending.
    dists: List[tuple] = []
    for name in user_names:
        c = centroids_norm_by_name.get(name)
        if c is None:
            d = float("inf")
        else:
            d = float(np.linalg.norm(feature_vector_norm - c))
        dists.append((name, d))
    dists.sort(key=lambda x: x[1])
    sorted_names = [name for name, _ in dists]
    # Pass 1's pick is candidate #1 always.
    candidate1 = pass1_vibe_name
    # Candidate #2: the first vibe in sorted_names that isn't candidate1.
    candidate2 = next(
        (n for n in sorted_names if n != candidate1),
        # Defensive: only happens with a single-vibe library (unreachable
        # under the 3-7 vibe validation gate).
        sorted_names[0] if sorted_names else candidate1,
    )
    return [candidate1, candidate2]


def _build_pass2_system_prompt(
    user_names: List[str],
    definitions: List[LLMVibeDefinition],
    peer_tracks_by_vibe: dict,
) -> str:
    """D-14, D-15, RESEARCH §4.1 — cached Pass 2 system prompt with peer context.

    XML format for the peer-tracks section per RESEARCH §4.1 (Anthropic
    models train on XML; closing tags are strong section-boundary signals).
    Cached for the entire Pass 2 phase via 1h TTL.

    ``peer_tracks_by_vibe`` maps user_name → list of peer dicts in ascending
    centroid-distance order (10 strong-graded members per D-14).
    """
    def_lookup = {d.vibe_name.strip().casefold(): d.definition for d in definitions}
    parts = [
        "You are Composer's vibe boundary reviewer. The system has already "
        "made a first-pass assignment for every rated track. Some tracks were "
        "graded weak or uncertain in Pass 1 — those are the boundary cases. "
        "Your job in this Pass 2: for each boundary track, pick the FINAL "
        "vibe it belongs to, given two candidate vibes and peer context "
        "(10 strong-graded representatives per candidate).",
        "",
        "<vibe_definitions>",
    ]
    for name in user_names:
        d = def_lookup.get(name.strip().casefold(), "")
        parts.append(f'  <vibe name="{name}">{d}</vibe>')
    parts.append("</vibe_definitions>")
    parts.append("")
    parts.append("<peer_tracks>")
    for name in user_names:
        peers = peer_tracks_by_vibe.get(name, [])
        parts.append(f'  <vibe name="{name}">')
        for peer in peers:
            parts.append(
                f"    <peer>{peer.get('title', '?')} — {peer.get('artist', '?')} "
                f"(energy={peer.get('energy', 0):.2f}, "
                f"tempo={peer.get('tempo', 0):.0f}, "
                f"dance={peer.get('danceability', 0):.2f}, "
                f"valence={peer.get('valence', 0):.2f})</peer>"
            )
        parts.append("  </vibe>")
    parts.append("</peer_tracks>")
    parts.append("")
    parts.append(
        "## Output format\n"
        'Return JSON matching LLMVibeBoundaryResponse: '
        '{"decisions": [{"track_index": <int>, "final_vibe_name": "...", '
        '"reason": "..."}, ...]}. '
        "track_index ECHOES the per-batch local integer 0..batch_size-1 "
        "from the user prompt. final_vibe_name must be one of the two "
        "candidates listed for that track. reason is 5-20 words explaining "
        "the final commitment. Return EXACTLY one decision per batch entry."
    )
    parts.append("")
    parts.append(_composer_context_padding())
    return "\n".join(parts)


def _build_pass2_batch_user_prompt(boundary_batch: List[dict]) -> str:
    """Per-batch Pass 2 user prompt. Each entry: features + Pass 1 verdict + candidates."""
    parts = [
        "Boundary review — pick the final vibe for each track below. Each "
        "entry lists two candidate vibes; pick ONE and explain in 5-20 words."
    ]
    for i, t in enumerate(boundary_batch):
        cands = " | ".join(t["candidates"])
        parts.append(
            f"{i}: {t.get('title', '?')} — {t.get('artist', '?')} "
            f"(genre={t.get('genre', '') or '<unknown>'}, "
            f"energy={t.get('energy', 0):.2f}, "
            f"tempo={t.get('tempo', 0):.0f}, "
            f"dance={t.get('danceability', 0):.2f}, "
            f"valence={t.get('valence', 0):.2f}) — "
            f"Pass 1 picked '{t['pass1_vibe_name']}' [{t['pass1_grade']}] "
            f'because: "{t["pass1_reason"]}". '
            f"Candidates: [{cands}]."
        )
    parts.append("")
    parts.append(
        'Return {"decisions": [...]} with EXACTLY '
        f"{len(boundary_batch)} entries (one per track index 0..{len(boundary_batch) - 1})."
    )
    return "\n".join(parts)


def _validate_pass2_batch_response(
    response: LLMVibeBoundaryResponse,
    batch_size: int,
    candidates_per_index: List[List[str]],
    name_lookup: dict,
) -> Optional[str]:
    """Validate a single Pass 2 batch response. Returns None if valid; else
    a corrective-prompt-friendly error string.

    Checks:
    1. Exactly batch_size decisions.
    2. Each track_index 0..batch_size-1 present exactly once.
    3. Each final_vibe_name resolves to a user-typed name AND is in the
       per-track candidate set (D-13 — Pass 2 must pick from the 2 candidates).
    4. Each reason is non-empty.
    """
    if len(response.decisions) != batch_size:
        return (
            f"Expected {batch_size} decisions; got {len(response.decisions)}."
        )
    seen: set = set()
    for d in response.decisions:
        if not (0 <= d.track_index < batch_size):
            return (
                f"track_index={d.track_index} is out of range [0, {batch_size})."
            )
        if d.track_index in seen:
            return (
                f"track_index={d.track_index} appears more than once."
            )
        seen.add(d.track_index)
        key = d.final_vibe_name.strip().casefold()
        if key not in name_lookup:
            valid = ", ".join(sorted(name_lookup.values()))
            return (
                f"final_vibe_name='{d.final_vibe_name}' is not in the "
                f"user-typed list. Valid names: [{valid}]."
            )
        resolved = name_lookup[key]
        # Check it's actually a candidate for this track.
        allowed = candidates_per_index[d.track_index]
        if resolved not in allowed:
            return (
                f"track_index={d.track_index}: final_vibe_name='{d.final_vibe_name}' "
                f"is not one of the two candidates {allowed}. Pick from the "
                f"candidates list shown in the user prompt."
            )
        if not d.reason or not d.reason.strip():
            return (
                f"track_index={d.track_index} has empty reason. "
                f"Reason is REQUIRED (5-20 words)."
            )
    return None


def _composer_context_padding() -> str:
    """Long Composer-context paragraph reused across Phase 6.2 cached prompts.

    Mirrors the tail in ``_build_clustering_system_prompt``. Padding pushes
    system prompts above Sonnet 4.6's 2048-token cache breakpoint so the 1h
    TTL cache engages on batches 2..N (Pitfall 9). Content is identical
    across calls within one re-cluster session.
    """
    return (
        "## Composer context (cacheable; identical across calls within 1h TTL)\n"
        "Composer is a self-hosted music companion that turns Plex star ratings "
        "into living vibe playlists and a continuous suggestions queue. The user "
        "has rated tracks in Plex (0-10 raw scale; binary in practice — 5★ or "
        "unrated). Composer reads userRating values from Plex via webhook + poll, "
        "computes audio features via Essentia (energy from spectral RMS, tempo "
        "via beat tracking, danceability via spectral complexity, valence from "
        "mode/danceability/brightness/pitch_salience). The 4-D feature space is "
        "(energy, tempo, danceability, valence) — z-score normalized before any "
        "distance math because tempo's 60-180 BPM range cannot co-exist with the "
        "0-1 scales of the others. Vibes are persistent named clusters that map "
        "1-to-1 to Plex playlists named 'Composer · {name}'. Phase 6.2 ships the "
        "LLM-direct membership pipeline: the LLM picks per-track membership "
        "directly from audio features + artist + genre, with a two-pass "
        "design — Pass 1 self-grades confidence, Pass 2 reviews boundary cases "
        "with peer-context anchors. K-means survives only as a centroid "
        "generator that scaffolds the LLM's mental model of the feature space; "
        "k-means labels are NOT used for membership. After commit, every "
        "RatingChanged event auto-slots the track into matching vibes (closest "
        "centroid + soft 2nd vibe within 1 std-dev margin, capped at 2 per "
        "track). The user listens via Plexamp on iOS or Plex Web. The full "
        "library lives on a Synology NAS, synced from Plex via APScheduler. "
        "Vibes are persistent — never recomputed automatically; user-triggered "
        "only via the Re-cluster vibes button. Manual track moves between vibes "
        "are sticky and survive re-cluster. Composer is a single FastAPI "
        "process with a single SQLite file; no separate worker, no Redis, no "
        "Celery. Anthropic prompt caching uses ttl=1h explicitly because the "
        "default silently regressed to 5min in March 2026. The Sonnet 4.6 cache "
        "breakpoint is 2048 tokens minimum. Plex webhooks at /api/webhooks/plex "
        "deliver media.rate, media.scrobble, and library.new events; an "
        "APScheduler polling job catches whatever the webhook missed. The event "
        "bus is a single asyncio.Queue with a single dispatcher task for SQLite "
        "write serialization. Dedupe is sha256(event_type|ratingKey|user_rating"
        "|5s_bucket) with INSERT OR IGNORE on a UNIQUE constraint. The taste "
        "profile (single-row TasteProfile id=1) is recomputed when the rated "
        "set grows or shrinks by >=10% since last computed_at. The refinement "
        "loop is the defining UX of v2.0: LLM proposes -> user gives "
        "natural-language feedback -> LLM refines -> commit. Soft cap of 10 "
        "refinement turns per session (counter shown in UI). After 10, surface "
        "a 'save what you have or start over' choice. Naming guidance: short "
        "evocative names (Late Night, Workout, Sunday Morning), one-line "
        "descriptions in the user's vernacular, no clinical labels like "
        "'Cluster 3'."
    )


async def assign_tracks_to_user_vibes(
    user_names: List[str],
) -> VibeProposalSet:
    """Phase 6.2 LLM-direct vibe assignment (VIBE-13 + VIBE-14).

    REPLACES Phase 6.1's user-led mapping function outright (D-18). The
    producer of :class:`VibeProposalSet` changes; the consumer
    (``/api/setup/finalize`` and ``/api/vibes/recluster/commit``) does not.

    Pipeline (D-04, D-09, D-10, D-13, D-14, D-15, D-17, D-19, D-20, D-31):
      1. Validate user_names (3-7, casefold dedupe, non-empty trims).
      2. Aggregate the rated set (asyncio.to_thread); cold-start gate
         raises ValueError at n_rated < COLD_START_FLOOR.
      3. Run k-means ONCE with forced_k=N for centroid summaries (D-19).
         ``labels_`` is DISCARDED after Pass 1 prompt construction —
         enforced by the AST test in tests/test_vibe_clusterer.py.
      4. Preamble (D-04, purpose=vibe_definitions_preamble): one definition
         per user-typed vibe. Retry-once; second failure raises.
      5. Pass 1 (D-01..D-07, purpose=vibe_assign_pass1): serial batches of 25.
         thinking="off". Retry-once per batch with corrective addendum.
      6. Pass 2 (D-12..D-17, purpose=vibe_assign_pass2): collect weak +
         uncertain Pass 1 assignments; for each, compute top 2 candidate
         vibes (including Pass 1's pick per D-13); build cached system prompt
         with all N vibes × 10 strong-graded peers (D-14, D-15); serial
         batches of 15 with thinking="adaptive" (RESEARCH §1.1 — adaptive
         replaces deprecated manual budget_tokens).
      7. Centroid recompute (D-20): per vibe, ``centroid = mean(LLM members)``.
      8. Build VibeProposalSet with one VibeProposal per user-typed vibe,
         seed_track_indices = all final-member indices, seed_tracks = 5
         closest to recomputed centroid, members = all in distance order,
         pass2_tracks = per-track Pass-2 entries for the confidence chip.
      9. ``materialize_clusters`` finishes centroid + spread + silhouette.

    /debug/vibes surfaces the cost breakdown by purpose (D-32).
    """
    # --- 1. Input validation (mirrors Phase 6.1 verbatim) ---
    if len(user_names) < 3 or len(user_names) > 7:
        raise ValueError(
            f"vibe count must be 3-7; got {len(user_names)}"
        )
    normalized = [n.strip() for n in user_names]
    if any(not n for n in normalized):
        raise ValueError("vibe names must not be blank")
    name_lookup: dict = {}
    for n in normalized:
        key = n.casefold()
        if key in name_lookup:
            raise ValueError(f"duplicate vibe names: '{n}'")
        name_lookup[key] = n
    n_targets = len(normalized)

    # --- 2. Aggregate + cold-start gate ---
    agg = await asyncio.to_thread(_aggregate_rated_set_sync)
    n_rated = agg["rated_track_count"]
    if n_rated < COLD_START_FLOOR:
        raise ValueError(
            f"cold-start: <{COLD_START_FLOOR} rated tracks "
            f"(have {n_rated}); rate more tracks in Plexamp first"
        )

    feature_matrix: np.ndarray = agg["feature_matrix"]
    rated_index_map = agg["rated_track_index_map"]
    tracks_for_prompt = agg["tracks_for_prompt"]
    mean = feature_matrix.mean(axis=0)
    std = feature_matrix.std(axis=0)
    X_normalized = _z_score_normalize(feature_matrix, mean, std)

    # --- 3. K-means ONCE for centroid summaries (D-19) ---
    chosen_k, kmeans_labels, kmeans_centroids_norm, sil, degraded = _pick_best_k(
        X_normalized, n_rated, forced_k=n_targets
    )
    logger.info(
        "vibe_clusterer Phase 6.2 assign: n_rated=%d k=%d silhouette=%.3f "
        "degraded=%s user_names=%s",
        n_rated, chosen_k, sil, degraded, normalized,
    )

    # Build per-cluster summaries for the Pass 1 system prompt. Uses
    # k-means labels HERE and HERE ONLY — labels are then discarded.
    cluster_summaries = _build_cluster_summaries_for_prompt(
        chosen_k, kmeans_labels, kmeans_centroids_norm,
        feature_matrix, X_normalized, tracks_for_prompt,
    )

    # The Pass 1 system prompt is now built. From this point, kmeans_labels
    # MUST NOT be read in any code path that produces final membership.
    # The AST test `test_no_kmeans_labels_used_for_membership` enforces this.
    del kmeans_labels  # belt-and-suspenders — make accidental reads NameError.

    # --- 4. Preamble call (D-04) ---
    with Session(get_engine()) as session:
        client = get_anthropic_client_v2(session)

    definitions_response = await _call_preamble(client, normalized)
    definitions = definitions_response.definitions

    # --- 5. Pass 1 system prompt (cached) ---
    pass1_system_prompt = _build_pass1_system_prompt(
        user_names=normalized,
        definitions=definitions,
        cluster_summaries=cluster_summaries,
        n_rated=n_rated,
        top_artists=agg["top_artists"],
        top_genres=agg["top_genres"],
    )

    # Batch the rated set into PASS1_BATCH_SIZE chunks. Each batch's user
    # prompt uses LOCAL track indices 0..batch_size-1; server maps back to
    # global rated index via ``batch_offset``.
    pass1_assignments: List[dict] = [None] * n_rated  # type: ignore[list-item]
    # ``pass1_assignments[global_idx]`` = {"vibe_name": str, "grade": str,
    # "reason": str} for the strict-canonical Pass 1 verdict.
    for batch_offset in range(0, n_rated, PASS1_BATCH_SIZE):
        end = min(n_rated, batch_offset + PASS1_BATCH_SIZE)
        batch_tracks = tracks_for_prompt[batch_offset:end]
        batch_size = len(batch_tracks)
        batch_response = await _call_pass1_batch(
            client=client,
            pass1_system_prompt=pass1_system_prompt,
            batch_tracks=batch_tracks,
            batch_size=batch_size,
            name_lookup=name_lookup,
        )
        # Map local → global indices.
        for a in batch_response.assignments:
            global_idx = batch_offset + a.track_index
            resolved_name = name_lookup[a.vibe_name.strip().casefold()]
            pass1_assignments[global_idx] = {
                "vibe_name": resolved_name,
                "grade": a.grade,
                "reason": a.reason,
            }

    # --- 6. Pass 2: identify weak + uncertain ---
    boundary_global_idxs = [
        i for i, a in enumerate(pass1_assignments)
        if a is not None and a["grade"] in ("weak", "uncertain")
    ]

    pass2_final_by_idx: dict = {}  # global_idx -> {"final_vibe_name", "reason", "pass1_grade", "pass1_reason"}

    if boundary_global_idxs:
        # Compute pre-Pass-2 centroids (in normalized space) FROM Pass 1
        # strong-graded LLM members. D-13's "centroid distance" is measured
        # against these — they're the best available approximation of where
        # the LLM thinks each vibe lives BEFORE Pass 2 finalization.
        strong_members_by_name: dict = {n: [] for n in normalized}
        for global_idx, a in enumerate(pass1_assignments):
            if a is None:
                continue
            if a["grade"] == "strong":
                strong_members_by_name[a["vibe_name"]].append(global_idx)

        pre_pass2_centroids_norm: dict = {}
        for name in normalized:
            members = strong_members_by_name[name]
            if members:
                pre_pass2_centroids_norm[name] = X_normalized[members].mean(axis=0)
            else:
                pre_pass2_centroids_norm[name] = None  # type: ignore[assignment]

        # Build peer_tracks_by_vibe per D-14: 10 strong-graded members per
        # vibe, sorted ascending by centroid distance.
        peer_tracks_by_vibe: dict = {}
        for name in normalized:
            members = strong_members_by_name[name]
            c = pre_pass2_centroids_norm.get(name)
            if c is None or not members:
                peer_tracks_by_vibe[name] = []
                continue
            sorted_members = sorted(
                members,
                key=lambda mi: float(np.linalg.norm(X_normalized[mi] - c)),
            )
            peers = []
            for mi in sorted_members[:PASS2_PEER_COUNT]:
                t = tracks_for_prompt[mi]
                peers.append({
                    "title": t.get("title", ""),
                    "artist": t.get("artist", ""),
                    "energy": t.get("energy", 0.0),
                    "tempo": t.get("tempo", 0.0),
                    "danceability": t.get("danceability", 0.0),
                    "valence": t.get("valence", 0.0),
                })
            peer_tracks_by_vibe[name] = peers

        pass2_system_prompt = _build_pass2_system_prompt(
            user_names=normalized,
            definitions=definitions,
            peer_tracks_by_vibe=peer_tracks_by_vibe,
        )

        # Batch boundary tracks.
        for batch_start in range(0, len(boundary_global_idxs), PASS2_BATCH_SIZE):
            batch_global_idxs = boundary_global_idxs[
                batch_start : batch_start + PASS2_BATCH_SIZE
            ]
            boundary_batch: List[dict] = []
            candidates_per_index: List[List[str]] = []
            for local_i, gi in enumerate(batch_global_idxs):
                a = pass1_assignments[gi]
                candidates = _compute_pass2_candidates(
                    pass1_vibe_name=a["vibe_name"],
                    feature_vector_norm=X_normalized[gi],
                    centroids_norm_by_name=pre_pass2_centroids_norm,
                    user_names=normalized,
                )
                candidates_per_index.append(candidates)
                t = tracks_for_prompt[gi]
                boundary_batch.append({
                    "title": t.get("title", ""),
                    "artist": t.get("artist", ""),
                    "genre": t.get("genre", ""),
                    "energy": t.get("energy", 0.0),
                    "tempo": t.get("tempo", 0.0),
                    "danceability": t.get("danceability", 0.0),
                    "valence": t.get("valence", 0.0),
                    "pass1_vibe_name": a["vibe_name"],
                    "pass1_grade": a["grade"],
                    "pass1_reason": a["reason"],
                    "candidates": candidates,
                })

            batch_response = await _call_pass2_batch(
                client=client,
                pass2_system_prompt=pass2_system_prompt,
                boundary_batch=boundary_batch,
                candidates_per_index=candidates_per_index,
                name_lookup=name_lookup,
            )
            for d in batch_response.decisions:
                gi = batch_global_idxs[d.track_index]
                a = pass1_assignments[gi]
                resolved_name = name_lookup[d.final_vibe_name.strip().casefold()]
                pass2_final_by_idx[gi] = {
                    "final_vibe_name": resolved_name,
                    "pass2_reason": d.reason,
                    "pass1_grade": a["grade"],
                    "pass1_reason": a["reason"],
                }

    # --- 7. Final membership: Pass 1 strong (kept) + Pass 1 weak/uncertain
    # not touched by Pass 2 (kept) + Pass 2 finals (override Pass 1).
    final_members_by_name: dict = {n: [] for n in normalized}
    for gi, a in enumerate(pass1_assignments):
        if a is None:
            continue
        if gi in pass2_final_by_idx:
            final_name = pass2_final_by_idx[gi]["final_vibe_name"]
        else:
            final_name = a["vibe_name"]
        final_members_by_name[final_name].append(gi)

    # --- D-20: recompute centroids as mean of LLM-assigned members (raw
    # feature space — for VibeProposal.centroid which downstream slot_track
    # measures against).
    proposals: List[VibeProposal] = []
    for name in normalized:
        member_idxs = final_members_by_name[name]
        # Build raw + normalized centroids; sort members by normalized
        # centroid distance for the seed_tracks/members order.
        if member_idxs:
            member_features = feature_matrix[member_idxs]
            centroid_raw = member_features.mean(axis=0)
            centroid_norm = X_normalized[member_idxs].mean(axis=0)
            dists = [
                (mi, float(np.linalg.norm(X_normalized[mi] - centroid_norm)))
                for mi in member_idxs
            ]
            dists.sort(key=lambda x: x[1])
            sorted_member_idxs = [mi for mi, _ in dists]
        else:
            centroid_raw = np.zeros(4)
            sorted_member_idxs = []

        def _mk_track_dict(mi: int) -> dict:
            t = tracks_for_prompt[mi]
            im = rated_index_map[mi]
            return {
                "title": t.get("title", "") or im.get("title", ""),
                "artist": t.get("artist", "") or im.get("artist", ""),
                "rating_key": im.get("rating_key", ""),
            }

        seed_tracks = [_mk_track_dict(mi) for mi in sorted_member_idxs[:5]]
        members = [_mk_track_dict(mi) for mi in sorted_member_idxs]

        # Pass 2 confidence-chip entries: only tracks that went through Pass
        # 2 AND landed in THIS vibe (Pass 2 may have moved them across vibes).
        pass2_tracks: List[dict] = []
        for mi in sorted_member_idxs:
            if mi in pass2_final_by_idx:
                p2 = pass2_final_by_idx[mi]
                # Pass 2 final committed this track to ``name`` (the current
                # vibe) — ALWAYS true for entries we list here because we
                # iterated sorted_member_idxs of name's final members.
                im = rated_index_map[mi]
                t = tracks_for_prompt[mi]
                pass2_tracks.append({
                    "rating_key": im.get("rating_key", ""),
                    "title": t.get("title", "") or im.get("title", ""),
                    "artist": t.get("artist", "") or im.get("artist", ""),
                    "pass1_grade": p2["pass1_grade"],
                    "pass1_reason": p2["pass1_reason"],
                    "pass2_reason": p2["pass2_reason"],
                })

        # Build description: use the LLM definition for this vibe (preamble
        # output). Fallback to empty string if missing (defensive).
        description = next(
            (d.definition for d in definitions
             if d.vibe_name.strip().casefold() == name.casefold()),
            "",
        )

        proposals.append(VibeProposal(
            name=name,
            description=description,
            action="new",
            source_vibe_ids=[],
            seed_track_indices=list(sorted_member_idxs),
            seed_tracks=seed_tracks,
            members=members,
            member_count=len(members),
            centroid={
                "energy": float(centroid_raw[0]),
                "tempo": float(centroid_raw[1]),
                "danceability": float(centroid_raw[2]),
                "valence": float(centroid_raw[3]),
            },
            pass2_tracks=pass2_tracks,
        ))

    proposal_set = VibeProposalSet(
        proposals=proposals,
        rated_track_count=n_rated,
        silhouette_avg=sil,
        forced_k=n_targets,
        degraded_mode=degraded,
        rated_track_index_map=rated_index_map,
    )

    proposal_set = materialize_clusters(proposal_set)
    return proposal_set


# ---------------------------------------------------------------------------
# Pass 1 / Pass 2 / preamble LLM call helpers — each retries ONCE on
# validation failure with a corrective addendum. Second failure raises
# ValueError (D-06 batch-scoped retry semantics).
# ---------------------------------------------------------------------------


async def _call_preamble(
    client, user_names: List[str]
) -> LLMVibeDefinitionsResponse:
    """D-04 preamble call. Returns one definition per user-typed vibe."""
    system_prompt = _build_definitions_preamble_system_prompt(user_names)
    user_prompt = _build_definitions_preamble_user_prompt(user_names)

    async def _one(prompt: str) -> LLMVibeDefinitionsResponse:
        return await client.call_with_structured_output(
            system_prompt=system_prompt,
            user_prompt=prompt,
            response_model=LLMVibeDefinitionsResponse,
            max_tokens=PREAMBLE_MAX_TOKENS,
            purpose="vibe_definitions_preamble",
            thinking="off",
        )

    try:
        response = await _one(user_prompt)
        if not response.definitions or len(response.definitions) != len(user_names):
            raise ValueError(
                f"preamble returned {len(response.definitions)} definitions; "
                f"expected {len(user_names)}"
            )
        return response
    except Exception as first_err:  # noqa: BLE001 — retry-once policy
        logger.warning(
            "preamble failed; retrying once: %s", first_err
        )
        corrective = (
            user_prompt
            + f"\n\nPREVIOUS RESPONSE WAS INVALID: {first_err}\n"
            "Please return EXACTLY one definition per user-typed vibe name."
        )
        try:
            response = await _one(corrective)
            if not response.definitions or len(response.definitions) != len(user_names):
                raise ValueError(
                    f"preamble retry returned {len(response.definitions)} "
                    f"definitions; expected {len(user_names)}"
                )
            return response
        except Exception as second_err:  # noqa: BLE001
            raise ValueError(
                f"vibe_definitions_preamble failed after retry: {second_err}"
            ) from second_err


async def _call_pass1_batch(
    client,
    pass1_system_prompt: str,
    batch_tracks: List[dict],
    batch_size: int,
    name_lookup: dict,
) -> LLMVibeAssignmentResponse:
    """One Pass 1 batch (D-01..D-07). Retry-once on validation failure."""
    user_prompt = _build_pass1_batch_user_prompt(batch_tracks)

    async def _one(prompt: str) -> LLMVibeAssignmentResponse:
        return await client.call_with_structured_output(
            system_prompt=pass1_system_prompt,
            user_prompt=prompt,
            response_model=LLMVibeAssignmentResponse,
            max_tokens=PASS1_MAX_TOKENS,
            purpose="vibe_assign_pass1",
            thinking="off",
        )

    response = await _one(user_prompt)
    err = _validate_pass1_batch_response(response, batch_size, name_lookup)
    if err is not None:
        logger.warning(
            "Pass 1 batch validation failed; retrying once: %s", err
        )
        corrective = (
            user_prompt
            + "\n\nPREVIOUS RESPONSE WAS INVALID: "
            + err
            + "\nPlease re-issue the FULL response satisfying the constraints."
        )
        response = await _one(corrective)
        err2 = _validate_pass1_batch_response(response, batch_size, name_lookup)
        if err2 is not None:
            raise ValueError(
                f"vibe_assign_pass1 failed after retry: {err2}"
            )
    return response


async def _call_pass2_batch(
    client,
    pass2_system_prompt: str,
    boundary_batch: List[dict],
    candidates_per_index: List[List[str]],
    name_lookup: dict,
) -> LLMVibeBoundaryResponse:
    """One Pass 2 batch (D-12..D-17). thinking='adaptive'; retry-once on failure."""
    user_prompt = _build_pass2_batch_user_prompt(boundary_batch)
    batch_size = len(boundary_batch)

    async def _one(prompt: str) -> LLMVibeBoundaryResponse:
        return await client.call_with_structured_output(
            system_prompt=pass2_system_prompt,
            user_prompt=prompt,
            response_model=LLMVibeBoundaryResponse,
            max_tokens=PASS2_MAX_TOKENS,
            purpose="vibe_assign_pass2",
            thinking="adaptive",
        )

    response = await _one(user_prompt)
    err = _validate_pass2_batch_response(
        response, batch_size, candidates_per_index, name_lookup
    )
    if err is not None:
        logger.warning(
            "Pass 2 batch validation failed; retrying once: %s", err
        )
        corrective = (
            user_prompt
            + "\n\nPREVIOUS RESPONSE WAS INVALID: "
            + err
            + "\nPlease re-issue the FULL response satisfying the constraints."
        )
        response = await _one(corrective)
        err2 = _validate_pass2_batch_response(
            response, batch_size, candidates_per_index, name_lookup
        )
        if err2 is not None:
            raise ValueError(
                f"vibe_assign_pass2 failed after retry: {err2}"
            )
    return response


def _build_cluster_summaries_for_prompt(
    chosen_k: int,
    kmeans_labels,
    kmeans_centroids_norm: np.ndarray,
    feature_matrix: np.ndarray,
    X_normalized: np.ndarray,
    tracks_for_prompt: List[dict],
) -> List[dict]:
    """D-19 — build per-cluster summaries (centroid + top artists + top
    genres + closest 10 tracks + member_count) for the Pass 1 system prompt.

    These are CONTEXT ONLY. After this function returns the caller MUST NOT
    read ``kmeans_labels`` again for membership (AST test enforces).
    """
    cluster_members: dict = {ci: [] for ci in range(chosen_k)}
    for i, lab in enumerate(kmeans_labels.tolist()):
        cluster_members[int(lab)].append(i)

    summaries: List[dict] = []
    for ci in range(chosen_k):
        members = cluster_members[ci]
        if not members:
            summaries.append({
                "cluster_index": ci,
                "centroid": {"energy": 0.0, "tempo": 0.0,
                             "danceability": 0.0, "valence": 0.0},
                "top_artists": [],
                "top_genres": [],
                "closest_tracks": [],
                "member_count": 0,
            })
            continue
        member_features = feature_matrix[members]
        c_raw = member_features.mean(axis=0)

        artist_counter: Counter = Counter()
        for mi in members:
            t = tracks_for_prompt[mi]
            if t.get("artist"):
                artist_counter[t["artist"]] += 1
        top_artists = [a for a, _ in artist_counter.most_common(5)]

        genre_counter: Counter = Counter()
        for mi in members:
            raw = (tracks_for_prompt[mi].get("genre") or "")
            for g in raw.split(","):
                g = g.strip()
                if g:
                    genre_counter[g] += 1
        top_genres = [g for g, _ in genre_counter.most_common(3)]

        centroid_norm = kmeans_centroids_norm[ci]
        dists = [
            (mi, float(np.linalg.norm(X_normalized[mi] - centroid_norm)))
            for mi in members
        ]
        dists.sort(key=lambda x: x[1])
        closest_tracks = []
        for mi, _d in dists[:10]:
            t = tracks_for_prompt[mi]
            closest_tracks.append(
                f"{t.get('title', '?')} — {t.get('artist', '?')}"
            )
        summaries.append({
            "cluster_index": ci,
            "centroid": {
                "energy": float(c_raw[0]),
                "tempo": float(c_raw[1]),
                "danceability": float(c_raw[2]),
                "valence": float(c_raw[3]),
            },
            "top_artists": top_artists,
            "top_genres": top_genres,
            "closest_tracks": closest_tracks,
            "member_count": len(members),
        })
    return summaries


async def _call_llm_with_validation(
    *,
    system_prompt: str,
    user_prompt: str,
    purpose: str,
    n_rated: int,
    rated_track_index_map: list,
    forced_k: Optional[int],
    degraded: bool,
    silhouette_avg: Optional[float],
    prior_proposals: Optional[VibeProposalSet] = None,
) -> VibeProposalSet:
    """LLM call + slim-schema Pydantic validation + server-side assembly.

    The LLM is constrained to :class:`VibeProposalSetLLMResponse` (only
    ``proposals``); server-controlled fields are assembled here from the
    aggregate / clustering parameters, NOT from the LLM. This eliminates the
    ``rated_track_index_map: {}`` shape-mismatch class of bugs (the LLM never
    sees the field).

    The LLM-side ``proposals`` use :class:`LLMVibeProposal` which is permissive
    on ``source_vibe_ids`` (``List[Union[int, str]]``). We canonicalize via
    :func:`_canonicalize_llm_proposals` AFTER slim-schema validation passes,
    using ``prior_proposals`` for case-insensitive name → positional-index
    resolution (quick-260510-i1q).

    Retries ONCE with a corrective user prompt on out-of-range
    ``seed_track_indices`` (Pitfall 10). Second failure raises ``ValueError``.
    """
    with Session(get_engine()) as session:
        client = get_anthropic_client_v2(session)

    # First attempt — slim LLM contract.
    llm_response = await client.call_with_structured_output(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=VibeProposalSetLLMResponse,
        purpose=purpose,
    )
    try:
        _validate_seed_indices(llm_response.proposals, n_rated)
    except ValueError as first_err:
        logger.warning(
            "LLM returned out-of-range seed_track_indices; retrying once: %s",
            first_err,
        )
        # Retry once with a corrective addendum.
        corrective_user_prompt = (
            f"{user_prompt}\n\nPREVIOUS RESPONSE FAILED VALIDATION: "
            f"seed_track_indices contained an integer outside the valid range "
            f"[0, {n_rated}). Reissue the response ensuring every index "
            f"is in that range."
        )
        llm_response = await client.call_with_structured_output(
            system_prompt=system_prompt,
            user_prompt=corrective_user_prompt,
            response_model=VibeProposalSetLLMResponse,
            purpose=purpose,
        )
        _validate_seed_indices(llm_response.proposals, n_rated)

    # Canonicalize: LLMVibeProposal → VibeProposal (strict List[int] on
    # source_vibe_ids). Uses prior_proposals for name → index lookup; strings
    # without a name match are dropped with a warning (quick-260510-i1q).
    canonical_proposals = _canonicalize_llm_proposals(
        llm_response.proposals, prior_proposals
    )

    # Phase 6.1 Blocker #5 Option A — refine round-trip: preserve fit +
    # fit_reason from prior_proposals by case-insensitive name match. The
    # canonicalization step above drops fit/fit_reason because the LLM-side
    # LLMVibeProposal schema doesn't carry them. Refine callers pass
    # prior_proposals so the prior fit grade survives the round-trip; initial
    # callers pass None (and the user-led entrypoint in Phase 6.2 sets fit
    # via different machinery, never via this helper).
    prior_list = (
        prior_proposals.proposals if prior_proposals is not None else None
    )
    canonical_proposals = [
        _carryover_fit_from_prior(p, prior_list) for p in canonical_proposals
    ]

    # Assemble the full VibeProposalSet server-side.
    return VibeProposalSet(
        proposals=canonical_proposals,
        rated_track_count=n_rated,
        silhouette_avg=silhouette_avg,
        forced_k=forced_k,
        degraded_mode=degraded,
        rated_track_index_map=rated_track_index_map,
    )


# ---------------------------------------------------------------------------
# Module-local re-export shim — tests monkeypatch this name.
# ---------------------------------------------------------------------------

def get_anthropic_client_v2(session):
    """Module-local re-export so tests can monkeypatch this name without import cycles."""
    from app.services.anthropic_client import get_anthropic_client_v2 as _factory
    return _factory(session)
