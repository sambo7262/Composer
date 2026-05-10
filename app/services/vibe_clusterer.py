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


class LLMVibeFit(BaseModel):
    """LLM-side response: one user-typed name mapped to one k-means cluster.

    Phase 6.1 D-NEW-11. The LLM returns N of these (one per user-typed vibe name)
    in a permutation against [0..N-1]. ``fit`` grades how well the user's name
    matches the cluster's audio/artist/genre profile. ``reason`` is required when
    ``fit == 'no_match'`` (server-validates).
    """
    user_name: str
    cluster_index: int
    description: str
    fit: Literal["strong", "weak", "no_match"]
    reason: Optional[str] = None


class LLMVibeMappingResponse(BaseModel):
    """Slim LLM contract for the user-led mapping call (D-NEW-01).

    Sibling to :class:`VibeProposalSetLLMResponse`. The LLM is ONLY asked for
    the mappings list — server assembles the full :class:`VibeProposalSet` from
    the mappings plus the server-computed cluster labels + rated_track_index_map
    + seed_tracks + members.

    DO NOT add server-controlled fields here. The defensive test
    ``test_slim_user_led_response_schema_only_has_mappings_field`` will fail
    loudly if anything other than ``mappings`` lands on this model.
    """
    mappings: List[LLMVibeFit]


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
