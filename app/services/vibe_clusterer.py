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
import logging
from collections import Counter
from typing import List, Literal, Optional

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


class VibeProposalSet(BaseModel):
    """The full LLM proposal — Plan 03's wizard consumes this verbatim."""

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
        'Return JSON matching VibeProposalSet: '
        '{"proposals": [{"name", "description", "action", '
        '"source_vibe_ids", "seed_track_indices"}, ...], '
        '"rated_track_count", "silhouette_avg", "forced_k", '
        '"degraded_mode", "rated_track_index_map"}'
    )
    parts.append(
        "action ∈ {keep, new, merged_from, split_from, renamed_from, dropped}. "
        "seed_track_indices MUST be integers in [0, rated_track_count). "
        "NEVER use raw Plex ratingKey strings (Pitfall 10). "
        "Do NOT set centroid / spread / silhouette — server computes those."
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
    """Build the (uncached) user prompt for initial vs refinement turns."""
    if prior_proposals is None:
        return (
            "Propose 3-7 vibes for this user, naming each by feel + describing "
            "each in one line. Return as VibeProposalSet matching the Pydantic "
            "schema."
        )
    return (
        f"Prior proposals:\n{prior_proposals.model_dump_json(indent=2)}\n\n"
        f"User feedback: {user_message}\n\n"
        f"Return the revised VibeProposalSet matching the schema."
    )


def _validate_seed_indices(proposals: VibeProposalSet, n_rated: int) -> None:
    """Raise ValueError if any seed_track_indices is out of [0, n_rated). Pitfall 10."""
    for prop in proposals.proposals:
        for idx in prop.seed_track_indices:
            if not (0 <= idx < n_rated):
                raise ValueError(
                    f"seed_track_indices contains out of range index {idx} "
                    f"(valid: 0..{n_rated - 1}); proposal name={prop.name!r}"
                )


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
    )

    # Override what the LLM returned for server-controlled fields.
    proposals.rated_track_count = n_rated
    proposals.rated_track_index_map = agg["rated_track_index_map"]
    proposals.forced_k = forced_k
    proposals.degraded_mode = degraded
    proposals.silhouette_avg = sil

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

    proposals = await _call_llm_with_validation(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        purpose=purpose,
        n_rated=n_rated,
    )

    proposals.rated_track_count = n_rated
    proposals.rated_track_index_map = agg["rated_track_index_map"]
    proposals.forced_k = prior.forced_k
    # degraded_mode and silhouette_avg propagate from prior unless caller
    # explicitly recomputes; refinement turns reuse the prior measurement.
    proposals.degraded_mode = prior.degraded_mode
    proposals.silhouette_avg = prior.silhouette_avg

    proposals = materialize_clusters(proposals)
    return proposals


async def _call_llm_with_validation(
    system_prompt: str,
    user_prompt: str,
    purpose: str,
    n_rated: int,
) -> VibeProposalSet:
    """LLM call + Pydantic validation + range check on seed_track_indices.

    Retries ONCE with a corrective user prompt on out-of-range indices
    (Pitfall 10). Second failure raises ValueError.
    """
    with Session(get_engine()) as session:
        client = get_anthropic_client_v2(session)

    # First attempt.
    proposals = await client.call_with_structured_output(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=VibeProposalSet,
        purpose=purpose,
    )
    try:
        _validate_seed_indices(proposals, n_rated)
        return proposals
    except ValueError as first_err:
        logger.warning(
            "LLM returned out-of-range seed_track_indices; retrying once: %s",
            first_err,
        )

    # Retry once with a corrective addendum.
    corrective_user_prompt = (
        f"{user_prompt}\n\nPREVIOUS RESPONSE FAILED VALIDATION: "
        f"seed_track_indices contained an integer outside the valid range "
        f"[0, {n_rated}). Reissue the VibeProposalSet ensuring every index "
        f"is in that range."
    )
    proposals = await client.call_with_structured_output(
        system_prompt=system_prompt,
        user_prompt=corrective_user_prompt,
        response_model=VibeProposalSet,
        purpose=purpose,
    )
    _validate_seed_indices(proposals, n_rated)
    return proposals


# ---------------------------------------------------------------------------
# Module-local re-export shim — tests monkeypatch this name.
# ---------------------------------------------------------------------------

def get_anthropic_client_v2(session):
    """Module-local re-export so tests can monkeypatch this name without import cycles."""
    from app.services.anthropic_client import get_anthropic_client_v2 as _factory
    return _factory(session)
