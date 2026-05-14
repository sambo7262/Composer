"""Phase 7 (Plan 01) — Suggestions queue service.

Module-level singleton + state pattern (Phase 5 D-08, mirrors
sync_service.py / analysis_service.py / vibe_service.py).

Public API:
  - bootstrap_suggestions_queue() — register the Composer · Suggestions
    ManagedPlaylist row. Idempotent: if the row already exists, returns
    silently. The Plex playlist itself is materialized on first non-empty
    refill (because PlexAPI rejects createPlaylist calls with an empty
    items list). Bootstrap registers a ManagedPlaylist row with
    ``plex_rating_key=""`` sentinel; refill detects the sentinel and calls
    :func:`_materialize_suggestions_plex_playlist` to create the Plex
    playlist and update the row (CR-01).
  - drain_track_from_mirror(rating_key) — remove the track from
    SuggestionsMirror IF it is currently a member. Returns True if removed.
  - maybe_schedule_refill(target=30) — if mirror size < target, directly
    await :func:`refill_suggestions_queue` (Plan 02 W4 replaced Plan 01's
    EventLog-marker pattern with an in-line LLM-ranking refill). Returns
    the deficit (target - current_size); 0 means no refill needed.
  - run_phase_07_suggestions_bootstrap() — lifespan migration entry point;
    gated by MigrationLog(phase_id='7.0-suggestions-bootstrap').
  - get_state() — return the module-level SuggestionsServiceStatus dataclass.

ALL PlexAPI work (when Plan 02 wires it in) routes through
``plex_playlist_service`` (which itself wraps in ``asyncio.to_thread`` per
Phase 5 D-09). The static AST test in
``tests/test_suggestions_service.py::TestSuggestionsServiceAstShape`` asserts
that every ``Session(get_engine())`` block in this module lives inside a
``_*_sync`` helper.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field as PydField
from sqlalchemy import text
from sqlmodel import Session, select

from app.database import get_engine
from app.models.vibe import ManagedPlaylist, MigrationLog
from app.services.plex_playlist_service import update_playlist_items

logger = logging.getLogger(__name__)

SUGGESTIONS_TARGET_SIZE = 30  # SUGG-01 default (configurable in later plan)
SUGGESTIONS_PLAYLIST_NAME = "Composer · Suggestions"  # Phase 6 D-25 namespace
PHASE_07_MIGRATION_ID = "7.0-suggestions-bootstrap"
# Sentinel for "Plex playlist not yet created" — Plan 02 first refill detects
# this and runs the real plex_playlist_service.create_playlist call with the
# first batch of suggestions.
DEFERRED_PLEX_RATING_KEY_SENTINEL = ""


@dataclass
class SuggestionsServiceStatus:
    state: str = "idle"  # "idle" | "bootstrapping" | "refilling" | "error"
    last_bootstrap_at: Optional[str] = None
    last_drain_at: Optional[str] = None
    last_error: Optional[str] = None


_status: SuggestionsServiceStatus = SuggestionsServiceStatus()


def get_state() -> SuggestionsServiceStatus:
    return _status


# ------------------------------------------------------------------
# Sync DB helpers — every Session(get_engine()) lives in a _*_sync
# function called via asyncio.to_thread (Phase 5 D-09).
# ------------------------------------------------------------------


def _find_suggestions_managed_playlist_sync() -> Optional[ManagedPlaylist]:
    with Session(get_engine()) as session:
        stmt = select(ManagedPlaylist).where(
            ManagedPlaylist.kind == "suggestions"
        )
        return session.exec(stmt).first()


def _insert_managed_playlist_sync(plex_rating_key: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        session.add(
            ManagedPlaylist(
                kind="suggestions",
                vibe_id=None,
                plex_rating_key=plex_rating_key,
                composer_name=SUGGESTIONS_PLAYLIST_NAME,
                last_pushed_at=now,
                track_count=0,
            )
        )
        session.commit()


def _delete_mirror_row_sync(rating_key: str) -> bool:
    """Delete SuggestionsMirror row WHERE track.plex_rating_key matches.
    Returns True if a row was deleted, False if no membership.
    """
    with Session(get_engine()) as session:
        result = session.execute(
            text(
                """
                DELETE FROM suggestionsmirror
                WHERE track_id = (
                    SELECT id FROM track WHERE plex_rating_key = :rk
                )
                """
            ),
            {"rk": rating_key},
        )
        session.commit()
        return (result.rowcount or 0) > 0


def _count_mirror_rows_sync() -> int:
    with Session(get_engine()) as session:
        return (
            session.execute(
                text("SELECT COUNT(*) FROM suggestionsmirror")
            ).scalar()
            or 0
        )


def _update_suggestions_managed_playlist_rk_sync(
    new_plex_rating_key: str, track_count: int,
) -> bool:
    """CR-01 fix — replace the deferred-sentinel plex_rating_key on the
    existing ManagedPlaylist(kind='suggestions') row in-place. Used by
    the first non-empty refill to materialize the Plex playlist.

    Returns True if a row was updated, False if no suggestions row exists
    (caller should treat as a bootstrap-skipped state).
    """
    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        row = session.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.kind == "suggestions"
            )
        ).first()
        if row is None:
            return False
        row.plex_rating_key = new_plex_rating_key
        row.track_count = track_count
        row.last_pushed_at = now
        session.add(row)
        session.commit()
        return True


def _read_migration_log_sync(phase_id: str) -> Optional[MigrationLog]:
    with Session(get_engine()) as session:
        return session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == phase_id)
        ).first()


def _upsert_migration_log_sync(
    phase_id: str, completed_at: Optional[str]
) -> None:
    with Session(get_engine()) as session:
        row = session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == phase_id)
        ).first()
        if row is None:
            session.add(MigrationLog(phase_id=phase_id, completed_at=completed_at))
        else:
            row.completed_at = completed_at
            session.add(row)
        session.commit()


# ------------------------------------------------------------------
# Async public API
# ------------------------------------------------------------------


async def _create_plex_suggestions_playlist() -> Optional[str]:
    """Plan 01 placeholder for the Plex playlist creation step.

    Returns None to signal "deferred — first refill will create the
    playlist". This indirection exists so tests can patch the helper to
    assert it is or is not called along the idempotent paths without
    touching real Plex.

    The deferred materialization lives in
    :func:`_materialize_suggestions_plex_playlist` (CR-01 fix), called by
    both refill entry points the first time they have at least one pick
    AND the existing ManagedPlaylist row still carries the empty-string
    sentinel.
    """
    # Bootstrap path: no-op. PlexAPI rejects createPlaylist with an empty
    # items list (BadRequest), and bootstrap has no seed tracks to pass.
    # The ManagedPlaylist row gets the empty-string sentinel; the first
    # refill calls _materialize_suggestions_plex_playlist to fill it in.
    return None


async def _materialize_suggestions_plex_playlist(
    plex_url: str, plex_token: str, seed_rating_keys: List[str],
) -> Optional[str]:
    """CR-01 fix — create the Composer · Suggestions Plex playlist on the
    first non-empty refill and update the existing ManagedPlaylist
    (kind='suggestions') row in-place with the returned ratingKey.

    Returns the new ratingKey on success, or None if the call failed (logged
    via ``logger.exception``) so the caller can continue without raising.

    Why this lives here and not in ``plex_playlist_service``:
      - ``plex_playlist_service.create_playlist`` inserts a new
        ``ManagedPlaylist(kind='vibe')`` row. Suggestions need to UPDATE
        the existing ``kind='suggestions'`` sentinel row instead — using
        the generic helper would leave us with two rows (one of the wrong
        kind) to clean up. A small inline PlexServer call keeps the row
        invariant clean.
      - Every PlexAPI call wrapped in ``asyncio.to_thread`` per
        Phase 5 D-09 / Pitfall 4.
    """
    if not plex_url or not plex_token:
        logger.info(
            "Suggestions Plex materialization skipped: Plex not configured "
            "(plex_url or plex_token empty)."
        )
        return None
    if not seed_rating_keys:
        return None  # nothing to seed; caller will retry on next refill

    from plexapi.server import PlexServer

    def _create_sync() -> Tuple[str, int]:
        plex = PlexServer(plex_url, plex_token, timeout=30)
        key_str = ",".join(str(k) for k in seed_rating_keys)
        tracks = plex.fetchItems(f"/library/metadata/{key_str}")
        new_pl = plex.createPlaylist(
            title=SUGGESTIONS_PLAYLIST_NAME, items=tracks,
        )
        return str(new_pl.ratingKey), len(tracks)

    try:
        new_rk, track_count = await asyncio.to_thread(_create_sync)
    except Exception:
        logger.exception(
            "First Suggestions refill: failed to materialize Plex playlist "
            "%r — will retry on next refill.",
            SUGGESTIONS_PLAYLIST_NAME,
        )
        return None

    updated = await asyncio.to_thread(
        _update_suggestions_managed_playlist_rk_sync, new_rk, track_count,
    )
    if not updated:
        logger.warning(
            "Suggestions Plex playlist created (ratingKey=%s) but no "
            "ManagedPlaylist(kind='suggestions') row found to update — "
            "bootstrap was likely skipped. Plex playlist is orphaned.",
            new_rk,
        )
        return new_rk

    logger.info(
        "First Suggestions refill: created Plex playlist %r "
        "(ratingKey=%s, %d seed tracks); ManagedPlaylist row updated.",
        SUGGESTIONS_PLAYLIST_NAME, new_rk, track_count,
    )
    return new_rk


async def bootstrap_suggestions_queue() -> None:
    """D-01 / D-02 — single bootstrap path called from BOTH wizard
    finalize and the Phase 7 lifespan migration.

    Steps:
    1. If a ManagedPlaylist(kind='suggestions') row already exists,
       return silently (idempotent).
    2. Otherwise attempt to create the Plex playlist (Plan 01 defers;
       returns None — see ``_create_plex_suggestions_playlist``).
    3. Register the ManagedPlaylist row. If Plex creation was deferred,
       use the empty-string sentinel; Plan 02 will update the row when
       it creates the Plex playlist on the first refill.
    """
    global _status
    existing = await asyncio.to_thread(_find_suggestions_managed_playlist_sync)
    if existing is not None:
        logger.info(
            "Suggestions bootstrap: ManagedPlaylist(kind=suggestions) already "
            "exists (plex_rating_key=%r); short-circuiting.",
            existing.plex_rating_key,
        )
        return

    _status = SuggestionsServiceStatus(
        state="bootstrapping",
        last_bootstrap_at=datetime.now(timezone.utc).isoformat(),
    )
    plex_rating_key = await _create_plex_suggestions_playlist()
    if plex_rating_key is None:
        plex_rating_key = DEFERRED_PLEX_RATING_KEY_SENTINEL
    await asyncio.to_thread(_insert_managed_playlist_sync, plex_rating_key)
    _status = SuggestionsServiceStatus(
        state="idle",
        last_bootstrap_at=datetime.now(timezone.utc).isoformat(),
    )
    if plex_rating_key == DEFERRED_PLEX_RATING_KEY_SENTINEL:
        logger.info(
            "Suggestions bootstrap (Plan 01 deferred): ManagedPlaylist row "
            "registered with sentinel plex_rating_key=''; Plan 02 first "
            "refill will create the Plex playlist '%s'.",
            SUGGESTIONS_PLAYLIST_NAME,
        )
    else:
        logger.info(
            "Suggestions bootstrap complete: Plex playlist '%s' "
            "(ratingKey=%s) registered as ManagedPlaylist(kind=suggestions).",
            SUGGESTIONS_PLAYLIST_NAME,
            plex_rating_key,
        )


async def drain_track_from_mirror(rating_key: str) -> bool:
    """SUGG-03 drain half — remove the track row if currently in the mirror.
    Returns True if a row was deleted, False if the played track was not in
    the queue (e.g. user played a track outside Suggestions; no-op).
    """
    if not rating_key:
        return False
    removed = await asyncio.to_thread(_delete_mirror_row_sync, rating_key)
    if removed:
        global _status
        _status = SuggestionsServiceStatus(
            state=_status.state,
            last_bootstrap_at=_status.last_bootstrap_at,
            last_drain_at=datetime.now(timezone.utc).isoformat(),
        )
    return removed


async def maybe_schedule_refill(
    target: int = SUGGESTIONS_TARGET_SIZE,
) -> int:
    """D-03 threshold gate — when SuggestionsMirror.size < target, directly
    await ``refill_suggestions_queue`` (Plan 02 W4: replaces the EventLog
    marker pattern from Plan 01 with an in-line LLM-ranking refill). Returns
    the deficit at gate-check time (0 means no refill triggered).

    The cost breaker is consulted inside ``refill_suggestions_queue`` — if it
    trips, refill returns a ``RefillResult(breaker_tripped=True)`` and sets
    ``_status.state='cost_locked'``. We still return the deficit unchanged so
    callers can surface "Suggestions paused" UI state.
    """
    current = await asyncio.to_thread(_count_mirror_rows_sync)
    deficit = max(0, target - current)
    if deficit > 0:
        # Plan 02 W4: in-line refill instead of EventLog marker. Best-effort:
        # exceptions are caught inside refill_suggestions_queue (breaker-
        # tripped is recorded on the result; we never raise from this path so
        # the caller's threshold gate remains a pure read-of-deficit semantic).
        try:
            await refill_suggestions_queue(target=target)
        except Exception:
            logger.exception(
                "refill_suggestions_queue raised during maybe_schedule_refill "
                "(deficit=%d); event_handlers caller will continue.",
                deficit,
            )
    return deficit


async def run_phase_07_suggestions_bootstrap() -> None:
    """Lifespan migration gate per D-02 — fires once per Composer install.

    Mirrors ``run_phase_61_migration``: read MigrationLog row, run if absent
    OR completed_at IS NULL, persist completed_at on success. Failure
    path: log + return without setting completed_at so the next restart
    retries.
    """
    existing = await asyncio.to_thread(
        _read_migration_log_sync, PHASE_07_MIGRATION_ID
    )
    if existing is not None and existing.completed_at is not None:
        logger.info(
            "Phase 7 suggestions bootstrap migration already complete "
            "(completed_at=%s); skipping.",
            existing.completed_at,
        )
        return

    # Defensive: insert in-flight marker (completed_at=NULL) BEFORE running,
    # so a crash mid-bootstrap doesn't leave the next restart in a "no row"
    # state confused with "never run". Mirrors run_phase_61_migration.
    await asyncio.to_thread(
        _upsert_migration_log_sync, PHASE_07_MIGRATION_ID, None
    )

    try:
        await bootstrap_suggestions_queue()
    except Exception:
        logger.exception(
            "Phase 7 suggestions bootstrap migration failed; will retry "
            "on next restart (completed_at remains NULL)."
        )
        return

    await asyncio.to_thread(
        _upsert_migration_log_sync,
        PHASE_07_MIGRATION_ID,
        datetime.now(timezone.utc).isoformat(),
    )
    logger.info("Phase 7 suggestions bootstrap migration complete.")


# ============================================================================
# Phase 7 Plan 02 — LLM-ranking refill pipeline, skip-tracking, cost breaker.
# ============================================================================

# ---------------------------------------------------------------------------
# Pydantic shapes (LLM contract + result types)
# ---------------------------------------------------------------------------


class SuggestionRankingPick(BaseModel):
    """One LLM-ranked pick. Integer index into the shortlist (Pitfall 10);
    server maps back to ``track_id``. ``rationale`` is required non-empty
    (Pydantic enforces ``str`` — empty fails validation per the
    ``min_length=1`` constraint).
    """

    candidate_index: int
    rationale: str = PydField(min_length=1)


class SuggestionRankingResponse(BaseModel):
    """LLM-returned ranked picks. Slim contract — only ``picks`` allowed."""

    picks: List[SuggestionRankingPick]


class RefillResult(BaseModel):
    """Outcome of one refill cycle."""

    candidates_evaluated: int = 0
    picks_returned: int = 0
    picks_validated: int = 0
    picks_inserted: int = 0
    latency_ms: int = 0
    cost_estimate_usd: float = 0.0
    cache_hit: bool = False
    cache_created: bool = False
    breaker_tripped: bool = False


# ---------------------------------------------------------------------------
# Sync DB helpers
# ---------------------------------------------------------------------------


def _read_active_vibes_sync() -> List[dict]:
    """Return active Vibe rows with centroid + spread as dicts (avoid lazy
    refresh outside the session)."""
    from app.models.vibe import Vibe

    with Session(get_engine()) as session:
        rows = session.exec(
            select(Vibe).where(Vibe.is_active == True)  # noqa: E712
        ).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description or "",
                "centroid_energy": r.centroid_energy,
                "centroid_tempo": r.centroid_tempo,
                "centroid_danceability": r.centroid_danceability,
                "centroid_valence": r.centroid_valence,
                "spread_energy": r.spread_energy,
                "spread_tempo": r.spread_tempo,
                "spread_danceability": r.spread_danceability,
                "spread_valence": r.spread_valence,
            }
            for r in rows
        ]


def _read_eligible_tracks_sync() -> List[dict]:
    """Return unrated tracks with full audio features, MINUS the 14-day
    SuggestionHistory window, MINUS hard-negative artists, MINUS
    hard_track ids, MINUS tracks currently in SuggestionsMirror. Returns
    dicts (detached from session).
    """
    from app.models.suggestions import (
        NegativeSignal, SuggestionHistory, SuggestionsMirror,
    )
    from app.models.track import Track

    fourteen_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=14)
    ).isoformat()

    with Session(get_engine()) as session:
        # Excluded track ids (hard_track + 14-day suggestion history +
        # currently-in-mirror).
        hard_track_ids = {
            r.track_id
            for r in session.exec(
                select(NegativeSignal).where(
                    NegativeSignal.signal_type == "hard_track"
                )
            ).all()
            if r.track_id is not None
        }
        recent_history_ids = {
            r.track_id
            for r in session.exec(
                select(SuggestionHistory).where(
                    SuggestionHistory.surfaced_at >= fourteen_days_ago
                )
            ).all()
        }
        in_mirror_ids = {
            r.track_id
            for r in session.exec(select(SuggestionsMirror)).all()
        }
        # Excluded artists (hard_artist with recovery_pending=True).
        hard_artists = {
            r.artist
            for r in session.exec(
                select(NegativeSignal).where(
                    NegativeSignal.signal_type == "hard_artist"
                ).where(NegativeSignal.recovery_pending == True)  # noqa: E712
            ).all()
            if r.artist
        }
        excluded = hard_track_ids | recent_history_ids | in_mirror_ids

        rows = session.exec(
            select(Track).where(
                Track.energy.is_not(None)  # type: ignore[union-attr]
            ).where(
                Track.tempo.is_not(None)  # type: ignore[union-attr]
            ).where(
                Track.danceability.is_not(None)  # type: ignore[union-attr]
            ).where(
                Track.valence.is_not(None)  # type: ignore[union-attr]
            )
        ).all()
        out: List[dict] = []
        for t in rows:
            if t.user_rating is not None and t.user_rating > 0:
                continue
            if t.id in excluded:
                continue
            if t.artist in hard_artists:
                continue
            out.append({
                "track_id": t.id,
                "plex_rating_key": t.plex_rating_key,
                "title": t.title,
                "artist": t.artist,
                "genre": t.genre or "",
                "energy": t.energy,
                "tempo": t.tempo,
                "danceability": t.danceability,
                "valence": t.valence,
            })
        return out


# Z-score normalization floor mirrors vibe_clusterer._STD_FLOOR pattern.
_STD_FLOOR = 1e-6


def _normalized_distance(
    track: dict, vibe: dict,
) -> Optional[float]:
    """Z-score normalized 4D Euclidean distance from track features to vibe
    centroid, using the vibe's per-dimension spread as the standard deviation.

    Returns None if any centroid/spread dimension is None (Vibe not yet
    populated by Phase 6.2 — caller should treat as unreachable)."""
    if any(
        vibe[k] is None
        for k in (
            "centroid_energy", "centroid_tempo",
            "centroid_danceability", "centroid_valence",
            "spread_energy", "spread_tempo",
            "spread_danceability", "spread_valence",
        )
    ):
        return None
    de = (track["energy"] - vibe["centroid_energy"]) / max(
        vibe["spread_energy"], _STD_FLOOR
    )
    dt = (track["tempo"] - vibe["centroid_tempo"]) / max(
        vibe["spread_tempo"], _STD_FLOOR
    )
    dd = (track["danceability"] - vibe["centroid_danceability"]) / max(
        vibe["spread_danceability"], _STD_FLOOR
    )
    dv = (track["valence"] - vibe["centroid_valence"]) / max(
        vibe["spread_valence"], _STD_FLOOR
    )
    return (de * de + dt * dt + dd * dd + dv * dv) ** 0.5


def _build_shortlist_sync(
    target_size: int = 50,
    target_vibe_id: Optional[int] = None,
) -> List[dict]:
    """D-05 / D-06 — build the candidate shortlist for LLM ranking.

    Pipeline:
    1. Load eligible tracks (unrated, analyzed, NOT in 14-day history, NOT
       hard-negative artist).
    2. Load active vibes.
    3. For each track, compute z-score-normalized distance to every vibe
       centroid. Pick the closest vibe as ``assigned_vibe_id``. Keep the
       track only if the closest distance is < 2σ (i.e. within ~2 z-score
       units in 4D — the "2σ pre-filter" of D-06).
    4. If ``target_vibe_id`` is set (W2 single-vibe partition):
         filter to tracks whose ``assigned_vibe_id == target_vibe_id``;
         sort ascending by distance; take the top ``target_size``.
       Otherwise (whole-queue refill, default):
         partition by ``assigned_vibe_id``; take ~``target_size/k`` from each
         bucket (D-05 balanced).

    Each returned row carries: ``track_id``, ``plex_rating_key``, ``title``,
    ``artist``, ``genre``, ``energy``, ``tempo``, ``danceability``,
    ``valence``, ``assigned_vibe_id``, ``closest_vibe_distance``.
    """
    eligible = _read_eligible_tracks_sync()
    vibes = _read_active_vibes_sync()
    if not eligible or not vibes:
        return []

    # 2σ threshold (z-score units, 4D); per D-06.
    SIGMA_THRESHOLD = 2.0

    annotated: List[dict] = []
    for t in eligible:
        best_vibe_id = None
        best_dist = None
        for v in vibes:
            d = _normalized_distance(t, v)
            if d is None:
                continue
            if best_dist is None or d < best_dist:
                best_dist = d
                best_vibe_id = v["id"]
        if best_dist is None or best_dist >= SIGMA_THRESHOLD:
            continue
        annotated.append({
            **t,
            "assigned_vibe_id": best_vibe_id,
            "closest_vibe_distance": best_dist,
        })

    if target_vibe_id is not None:
        # W2 — single-vibe partition.
        only = [r for r in annotated if r["assigned_vibe_id"] == target_vibe_id]
        only.sort(key=lambda r: r["closest_vibe_distance"])
        return only[:target_size]

    # D-05 balanced across vibes — ~target_size/k from each bucket.
    by_vibe: dict = {}
    for r in annotated:
        by_vibe.setdefault(r["assigned_vibe_id"], []).append(r)
    for v_id, bucket in by_vibe.items():
        bucket.sort(key=lambda r: r["closest_vibe_distance"])
    k = max(1, len(by_vibe))
    per_bucket = max(1, target_size // k)
    out: List[dict] = []
    for v_id in by_vibe:
        out.extend(by_vibe[v_id][:per_bucket])
    return out[:target_size + k]


def _read_soft_negatives_sync() -> List[dict]:
    """Return soft-negative entries with title + artist context for the LLM
    user-prompt addendum (D-12).
    """
    from app.models.suggestions import NegativeSignal
    from app.models.track import Track

    with Session(get_engine()) as session:
        rows = session.exec(
            select(NegativeSignal).where(
                NegativeSignal.signal_type == "soft"
            )
        ).all()
        out: List[dict] = []
        for r in rows:
            title = None
            artist = None
            if r.track_id is not None:
                t = session.exec(
                    select(Track).where(Track.id == r.track_id)
                ).first()
                if t is not None:
                    title = t.title
                    artist = t.artist
            out.append({
                "track_id": r.track_id,
                "title": title or "(unknown)",
                "artist": artist or r.artist or "(unknown)",
            })
        return out


def _read_latest_llm_usage_sync(purpose: str):
    """Read the most-recent LLMUsage row for the purpose, returning detached
    plain dict-like (we expose only the fields we need)."""
    from app.models.llm_usage import LLMUsage

    @dataclass
    class _Usage:
        cache_creation_input_tokens: int
        cache_read_input_tokens: int
        cost_estimate_usd: float

    with Session(get_engine()) as session:
        row = session.exec(
            select(LLMUsage).where(LLMUsage.purpose == purpose).order_by(
                LLMUsage.id.desc()  # type: ignore[union-attr]
            )
        ).first()
        if row is None:
            return None
        return _Usage(
            cache_creation_input_tokens=row.cache_creation_input_tokens or 0,
            cache_read_input_tokens=row.cache_read_input_tokens or 0,
            cost_estimate_usd=row.cost_estimate_usd or 0.0,
        )


def _count_llm_usage_by_purpose_sync(purpose: str) -> int:
    from app.models.llm_usage import LLMUsage

    with Session(get_engine()) as session:
        rows = session.exec(
            select(LLMUsage).where(LLMUsage.purpose == purpose)
        ).all()
        return len(rows)


def _insert_refill_trigger_log_sync(
    event_source: str,
    candidates: int,
    picks: int,
    latency_ms: int,
    cost_usd: float,
    breaker_tripped: bool,
    target_vibe_id: Optional[int] = None,
    error: Optional[str] = None,
) -> int:
    """Append one RefillTriggerLog row and return its id. Called from three
    sites in ``refill_suggestions_queue`` (deficit==0 short-circuit,
    breaker-tripped path, success path) and from
    ``refill_suggestions_for_vibe`` (W2).
    """
    from app.models.suggestions import RefillTriggerLog

    with Session(get_engine()) as session:
        row = RefillTriggerLog(
            triggered_at=datetime.now(timezone.utc).isoformat(),
            event_source=event_source,
            target_vibe_id=target_vibe_id,
            candidates_evaluated=candidates,
            picks_made=picks,
            latency_ms=latency_ms,
            cost_estimate_usd=cost_usd,
            breaker_tripped=breaker_tripped,
            error=error,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id  # type: ignore[return-value]


def _next_mirror_position_sync() -> int:
    """Next 0-based position for SuggestionsMirror inserts (append-at-tail)."""
    from app.models.suggestions import SuggestionsMirror

    with Session(get_engine()) as session:
        row = session.execute(
            text("SELECT COALESCE(MAX(position), -1) + 1 FROM suggestionsmirror")
        ).first()
        return int((row[0] if row else 0) or 0)


def _write_refill_results_sync(
    picks: List[SuggestionRankingPick],
    shortlist: List[dict],
    event_source: str = "track_played",
    target_vibe_id: Optional[int] = None,
    latency_ms: int = 0,
    cost_usd: float = 0.0,
) -> int:
    """Persist refill outcome inside one Session:

    1. Insert one RefillTriggerLog row → ``refill_id``.
    2. Insert one SuggestionsMirror row per pick (INSERT OR IGNORE on
       UNIQUE(track_id)).
    3. Insert one SuggestionHistory row per pick (refill_id matches).

    Returns the refill_id.
    """
    from app.models.suggestions import (
        RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        log = RefillTriggerLog(
            triggered_at=now_iso,
            event_source=event_source,
            target_vibe_id=target_vibe_id,
            candidates_evaluated=len(shortlist),
            picks_made=len(picks),
            latency_ms=latency_ms,
            cost_estimate_usd=cost_usd,
            breaker_tripped=False,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        refill_id = log.id

        # Next mirror position.
        max_pos = session.execute(
            text("SELECT COALESCE(MAX(position), -1) FROM suggestionsmirror")
        ).first()
        next_pos = int((max_pos[0] if max_pos else -1) or -1) + 1

        for p in picks:
            row = shortlist[p.candidate_index]
            # INSERT OR IGNORE on UNIQUE(track_id) — if the track is already
            # in the mirror, skip (race with drain).
            session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO suggestionsmirror
                        (track_id, position, added_at, rationale,
                         vibe_id, score)
                    VALUES (:tid, :pos, :added, :rat, :vid, :score)
                    """
                ),
                {
                    "tid": row["track_id"],
                    "pos": next_pos,
                    "added": now_iso,
                    "rat": p.rationale,
                    "vid": row.get("assigned_vibe_id"),
                    "score": row.get("closest_vibe_distance"),
                },
            )
            next_pos += 1
            session.add(SuggestionHistory(
                track_id=row["track_id"],
                surfaced_at=now_iso,
                refill_id=refill_id,
            ))
        session.commit()
        return refill_id  # type: ignore[return-value]


def _read_plex_creds_sync() -> Tuple[str, str]:
    """Read decrypted Plex URL + token. Returns ('', '') if not configured —
    callers should skip Plex push in that case (smoke-test compatibility)."""
    from app.services.settings_service import (
        get_decrypted_credential, get_setting,
    )

    with Session(get_engine()) as session:
        ps = get_setting(session, "plex")
        if ps is None or not ps.is_configured:
            return ("", "")
        tok = get_decrypted_credential(session, "plex") or ""
        return (ps.url or "", tok)


# ---------------------------------------------------------------------------
# Prompt builders + Anthropic client factory
# ---------------------------------------------------------------------------


def _get_anthropic_client_sync():
    """Lazy-construct AnthropicClient from settings_service decrypted api_key.

    Mirrors taste_profile_service pattern: raises ValueError if not
    configured (a missing api_key during refill is operator error and should
    surface). Suffixed ``_sync`` to satisfy the Phase 5 D-09 AST invariant
    (Session(get_engine()) blocks live in ``_*_sync`` helpers).
    """
    from app.services.anthropic_client import get_anthropic_client_v2

    with Session(get_engine()) as session:
        return get_anthropic_client_v2(session)


def _get_anthropic_client():
    """Async-friendly wrapper that runs the sync constructor in a worker
    thread (Phase 5 D-09). Tests can monkeypatch THIS attribute to inject a
    mock client without going through the settings table.
    """
    return _get_anthropic_client_sync()


def _composer_context_blurb() -> str:
    """Long, byte-identical Composer context tail (~1500-2500 tokens) that
    pads the shared system prompt above the 2048-token Sonnet 4.6 cache
    breakpoint. Identical across all ``suggestions_rank`` calls within the
    1h TTL — exactly what makes the cache engage (Pitfall 9 / D-07).

    Pattern lifted from taste_profile_service._build_system_prompt's tail
    block.
    """
    return (
        "## Composer context (cacheable; identical across calls within 1h TTL)\n"
        "Composer is a self-hosted music companion that turns Plex star ratings "
        "into living vibe playlists and a continuous Suggestions queue. Composer "
        "reads Plex userRating values (0-10 scale internally; 0-5 stars in the "
        "UI with half-star resolution) and treats them as the user's "
        "authoritative taste signal. Composer NEVER writes ratings back to Plex. "
        "Composer maintains 'vibes' (clusters of audio-feature-similar tracks) "
        "and a continuous Suggestions queue. The user listens via Plexamp on "
        "iOS or Plex Web. Slotting works on audio features computed by "
        "Essentia: energy from spectral RMS, tempo via beat tracking, "
        "danceability via spectral complexity, valence as a weighted "
        "combination of mode/danceability/brightness/pitch_salience. "
        "Suggestions ranking uses Anthropic Sonnet 4.6 with prompt caching for "
        "cost. The taste profile centroid is the mean over rated tracks' 4-D "
        "audio features. The user's full library lives on a Synology NAS, "
        "synced from Plex via the library_sync APScheduler job. New tracks "
        "arrive from Lidarr, are imported into Plex, and Composer auto-"
        "analyzes them via Essentia. Vibes are persistent — never recomputed "
        "automatically; user-triggered only. The user can rename, merge, or "
        "split vibes during the setup wizard. Composer is a single FastAPI "
        "process with a single SQLite file; no separate worker, no Redis, no "
        "Celery. Anthropic prompt caching uses ttl=1h explicitly because the "
        "default regressed to 5min in March 2026. The Sonnet 4.6 cache "
        "breakpoint is 2048 tokens minimum. Plex webhooks at "
        "/api/webhooks/plex deliver media.rate, media.scrobble, and "
        "library.new events; an APScheduler polling job catches whatever the "
        "webhook missed. The event bus is a single asyncio.Queue with a "
        "single dispatcher task for SQLite write serialization. Dedupe is "
        "sha256(event_type|ratingKey|user_rating|5s_bucket) with INSERT OR "
        "IGNORE on a UNIQUE constraint. The first-run auto-backfill populates "
        "user_rating + last_viewed_at + view_count from Plex on initial Phase "
        "5 deploy onto an existing v1 library. The Manual Resync button at "
        "/api/rating-sync/start shares the same singleton state machine as "
        "the auto-trigger. The Composer Suggestions queue holds 30 tracks by "
        "default and refills via threshold-only refill (when the queue drops "
        "below the target, a refill is triggered; D-03 invariant: no refill "
        "on every scrobble, only when the queue depletes). Each suggestion "
        "carries a one-line 'Why this track?' rationale that the user can "
        "expand inline. Dismissing a track is a two-tap action (expand row "
        "→ tap Dismiss). The dismiss action writes a hard-negative track "
        "signal AND a hard-negative artist signal; the artist deboost clears "
        "when the user rates 3+ stars on any track by that artist. A "
        "soft-negative signal is written for tracks that the user played "
        "from Suggestions but did not rate within 14 days — those are passed "
        "into the LLM user-prompt addendum as 'do not prioritize'. The "
        "shortlist for the LLM ranking call is built by: (a) selecting "
        "unrated tracks with all four audio features computed, (b) excluding "
        "tracks surfaced within the last 14 days, (c) excluding tracks by "
        "hard-negative artists, (d) pre-filtering to tracks within 2σ of at "
        "least one vibe centroid, (e) distributing balanced across vibes "
        "(~50/k candidates per vibe for k vibes). The user has multiple "
        "vibes carved out from their rated tracks via the wizard's "
        "clustering proposal flow. Each vibe is a 4-D centroid in audio "
        "feature space (energy, tempo, danceability, valence) plus a "
        "human-given name and description. The wizard runs once on first "
        "setup; the user can re-cluster from settings later if their taste "
        "drifts. The full Composer architecture leans on the arr stack: "
        "Plex for media playback, Lidarr for new-artist discovery, Sonarr "
        "for the TV side (out of Composer's scope), Radarr similarly. "
        "Composer ships as a single Docker container that runs alongside "
        "the arr stack on a NAS. The user configures three external "
        "services in Composer's wizard: Plex (URL + token), Anthropic "
        "(API key), and Lidarr (URL + API key — optional, only needed when "
        "the user wants to add new artists from suggestions). Composer "
        "does NOT touch existing user-created Plex playlists (hands-off "
        "rule); it only mutates playlists with the 'Composer · ' prefix "
        "AND a matching ManagedPlaylist DB row — both markers required "
        "(dual-marker invariant). Composer's UI is mobile-first: bottom "
        "tab bar, h-dvh root, safe-area-inset-bottom, ≥44px touch targets, "
        "no hover-only states. The Suggestions list uses a compact "
        "vertical-list layout — 48px album art + title + artist + tiny "
        "vibe chip per row; ~6 rows visible on iPhone portrait. The "
        "rationale appears inline below the row when the user taps it "
        "(Alpine.js x-show toggle, no separate page). The full Composer "
        "stack: Python 3.12 + FastAPI 0.135 + SQLModel + SQLite + Jinja2 "
        "+ HTMX + Alpine.js + Tailwind v4 CSS + Anthropic SDK + PlexAPI + "
        "Spotipy + pyarr + APScheduler. The development cadence emphasizes "
        "TDD: every behavior change has a failing test before the GREEN "
        "implementation. The phase numbering corresponds to a planning "
        "document at .planning/ROADMAP.md that the user iterates on as "
        "Composer evolves. Phase 7 (this phase) ships the Suggestions "
        "queue + retires the v1 mood-chat UI. Phase 8 will ship Lidarr-"
        "driven artist discovery. Phase 9 will revisit taste-profile "
        "richness once real listening data accumulates. The Suggestions "
        "ranking call you are participating in is one of two main LLM "
        "purposes in Composer: 'taste_profile_summary' and 'suggestions_rank'. "
        "Both share the same shared-system-prompt cache namespace so that "
        "the Composer-context blurb (this very paragraph and the surrounding "
        "context) is cached once and reused. The cache is keyed on the "
        "exact byte sequence of the system message — any deviation (a fresh "
        "deploy, a refactor that changes wording, a new field added) resets "
        "the cache cost. Composer's threat model treats the LLM as an "
        "untrusted boundary: every track id returned by the LLM is "
        "validated against the local Track table before insertion (Pitfall "
        "10), and integer indices into the candidate shortlist are used "
        "instead of raw track ids to keep the LLM's job simple and the "
        "validation cheap. The cost circuit breaker enforces three "
        "thresholds against today's accumulated LLMUsage: a daily quota "
        "of 50 calls per UTC day, a burst limit of 5 calls per rolling 60-"
        "second window, and a per-event debounce of 30 seconds. Any "
        "threshold trip raises CostBreakerTrippedError before any further "
        "LLM call, surfaces 'Suggestions paused — cost limit hit' on the "
        "settings page, and writes a RefillTriggerLog row tagged "
        "breaker_tripped=True. The /debug/suggestions page (shipping in "
        "Plan 03 of this phase) renders the last 20 RefillTriggerLog rows "
        "with their event_source, candidates_evaluated, picks_made, "
        "latency_ms, cost_estimate_usd, and breaker_tripped status. "
        "Composer's authentication posture is single-user / Tailscale-only; "
        "there is no per-user authn or authz. The user controls who can "
        "reach the FastAPI process at the network layer (Tailscale ACL or "
        "a reverse proxy on a private network). All Composer interactions "
        "with Plex use the user's plex_token (decrypted from settings on "
        "demand), and all Anthropic calls use the user's API key. Both "
        "credentials are encrypted at rest via the Composer encryption key "
        "stored on the data volume; they are decrypted only when needed and "
        "never logged. The token is sanitized out of any error message that "
        "bubbles up to logs (sanitize_token helper at the plex_playlist_"
        "service boundary). When recommending tracks, lean on the user's "
        "vibe definitions: a track that lands neatly inside one of the "
        "user's vibes is generally a better suggestion than a track that "
        "lands ambiguously between vibes, because the user has explicitly "
        "endorsed the vibe shape. When the candidate's audio features hint "
        "at a vibe (energy/tempo/danceability/valence within ~1σ of the "
        "centroid), the rationale should mention which vibe the candidate "
        "fits and why. When the candidate's metadata (artist or genre) "
        "matches one of the user's top artists or top genres, the rationale "
        "can lead with that connection — it is concrete and verifiable. "
        "Avoid generic phrases like 'great track' or 'fits your taste'; "
        "the user wants to know why THIS track was picked instead of any "
        "of the dozens of others on the shortlist. The rationale appears "
        "verbatim in the UI as 'Why this track?' so it must read like a "
        "thoughtful note from a friend who knows the user's library, not "
        "a marketing blurb. Avoid hedging language like 'might be' or "
        "'could be a good fit'; commit to a take. The user can always "
        "dismiss a suggestion with two taps, and dismissals are how the "
        "system learns. Soft negatives (heard but never rated within 14 "
        "days) are a weaker signal than hard negatives (explicit dismiss); "
        "honor both but let the user override if their taste shifted. "
        "When the soft-negative addendum lists a track, do not pick "
        "candidates by the same artist UNLESS the candidate is materially "
        "different in audio features or genre — explain the difference in "
        "the rationale.\n"
    )


def build_suggestions_ranking_system_prompt() -> str:
    """Plan 02 — shared longer preamble (D-07) for the suggestions ranking
    call. Composition:

    - Section A: enumerate active vibes (name + description).
    - Section B: taste profile summary text (TasteProfile.summary_text).
    - Section C: top artists / top genres (from vibe_clusterer aggregate).
    - Section D: long Composer-context tail (byte-identical across calls).

    Target: >= 8000 chars (~ >2048 tokens — proxy used by Phase 6.2 tests).
    Asserts at first call to surface misconfig early.
    """
    from app.services.taste_profile_service import get_current_profile
    from app.services.vibe_clusterer import _aggregate_rated_set_sync

    vibes = _read_active_vibes_sync()
    parts: List[str] = [
        "You are Composer's suggestions ranker. The user has defined the "
        "following persistent vibe playlists from their rated music. Use "
        "them as the lens through which you read the candidate tracks below.",
        "",
        "## Vibes",
    ]
    if vibes:
        for v in vibes:
            parts.append(f"- {v['name']}: {v['description']}")
    else:
        parts.append("- (no vibes defined yet — rank by general taste fit)")
    parts.append("")

    # Section B — taste profile summary.
    tp = None
    try:
        tp = get_current_profile()
    except Exception:
        tp = None
    parts.append("## Taste profile summary")
    if tp is not None and tp.summary_text:
        parts.append(tp.summary_text)
    else:
        parts.append("(no taste profile summary yet)")
    parts.append("")

    # Section C — top artists / genres.
    try:
        agg = _aggregate_rated_set_sync()
    except Exception:
        agg = {"top_artists": [], "top_genres": []}
    parts.append("## Top rated artists")
    for a in (agg.get("top_artists") or [])[:10]:
        parts.append(f"- {a['artist']} ({a['count']} rated)")
    parts.append("")
    parts.append("## Top rated genres")
    for g in (agg.get("top_genres") or [])[:10]:
        parts.append(f"- {g['genre']} ({g['count']})")
    parts.append("")

    parts.append("## Task")
    parts.append(
        "Given a numbered list of candidate tracks (provided in the user "
        "message), return ranked picks as JSON: "
        '{"picks": [{"candidate_index": N, "rationale": "one-line why"}]}. '
        "candidate_index MUST be an integer in [0, N-1]. rationale MUST be "
        "non-empty (one sentence is ideal). Up to 50 picks per response, "
        "ordered best-fit first. Use the user's vibes and taste profile to "
        "interpret 'best-fit'. Be specific in the rationale — reference an "
        "artist, a vibe, an audio-feature observation — not generic phrases."
    )
    parts.append("")
    parts.append(_composer_context_blurb())

    prompt = "\n".join(parts)
    # Defensive assertion: surface misconfig early before the Anthropic call.
    assert len(prompt) >= 8000, (
        f"suggestions ranking system prompt is {len(prompt)} chars "
        "(< 8000 — below Sonnet 4.6 cache threshold proxy)"
    )
    return prompt


def build_suggestions_ranking_user_prompt(
    shortlist: List[dict],
    soft_negatives: List[dict],
) -> str:
    """Per-call user prompt (uncached) with the integer-indexed candidates."""
    lines: List[str] = [
        f"There are {len(shortlist)} candidate tracks. Rank them.",
        "",
        "## Candidates",
    ]
    # Cache vibe name lookups.
    vibes = {v["id"]: v["name"] for v in _read_active_vibes_sync()}
    for idx, row in enumerate(shortlist):
        vname = vibes.get(row.get("assigned_vibe_id"), "?")
        lines.append(
            f"[{idx}]: {row['title']} — {row['artist']} "
            f"({row.get('genre') or 'unknown genre'}, "
            f"energy={row['energy']:.2f}, tempo={row['tempo']:.0f}, "
            f"danceability={row['danceability']:.2f}, "
            f"valence={row['valence']:.2f}) [vibe_hint: {vname}]"
        )
    if soft_negatives:
        lines.append("")
        lines.append("## DO NOT PRIORITIZE")
        lines.append(
            "The user heard the following tracks from Suggestions but did "
            "not rate them in 14 days — likely soft negatives. Do not pick "
            "candidates that sound similar to these:"
        )
        for sn in soft_negatives:
            lines.append(f"- {sn['title']} — {sn['artist']} (heard, not rated)")
    return "\n".join(lines)


def _validate_picks(
    picks: List[SuggestionRankingPick],
    shortlist_size: int,
) -> Tuple[List[SuggestionRankingPick], List[int]]:
    """Pitfall 10 — partition picks into (valid, list-of-invalid-indices).

    A pick is invalid if ``candidate_index < 0`` or
    ``candidate_index >= shortlist_size``.
    """
    valid: List[SuggestionRankingPick] = []
    invalid: List[int] = []
    for p in picks:
        if 0 <= p.candidate_index < shortlist_size:
            valid.append(p)
        else:
            invalid.append(p.candidate_index)
    return valid, invalid


# ---------------------------------------------------------------------------
# Refill entry points
# ---------------------------------------------------------------------------


async def refill_suggestions_queue(
    target: int = SUGGESTIONS_TARGET_SIZE,
) -> RefillResult:
    """Plan 02 — the LLM-ranking refill pipeline (SUGG-04..07, OPS-05).

    Steps:
    1. Compute deficit (target - current mirror size).
    2. Cost breaker (``check_or_raise``) — Pitfall 11 BEFORE any LLM activity.
    3. Build shortlist (D-05/D-06).
    4. Collect soft-negatives (D-12).
    5. Build system + user prompts.
    6. Call AnthropicClient with ``purpose='suggestions_rank'``, ``thinking='off'``.
    7. Pitfall 10 validate; retry once on failure with corrective addendum.
    8. Trim to deficit + dedupe candidate_index.
    9. Write SuggestionsMirror + SuggestionHistory + RefillTriggerLog rows.
    10. Push to Plex via ``update_playlist_items`` (additive).
    11. Compute cache-hit telemetry from the just-written LLMUsage row.
    """
    from app.services.llm_cost_breaker import (
        CostBreakerTrippedError, check_or_raise,
    )

    global _status
    start = time.monotonic()

    # 1. Compute deficit.
    current = await asyncio.to_thread(_count_mirror_rows_sync)
    deficit = max(0, target - current)
    if deficit == 0:
        await asyncio.to_thread(
            _insert_refill_trigger_log_sync,
            "track_played", 0, 0,
            int((time.monotonic() - start) * 1000),
            0.0, False,
        )
        return RefillResult(
            candidates_evaluated=0, picks_returned=0,
            picks_validated=0, picks_inserted=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            cost_estimate_usd=0.0, cache_hit=False, cache_created=False,
        )

    # 2. Cost breaker (Pitfall 11 — BEFORE any LLM activity).
    try:
        await check_or_raise(purpose_prefix="suggestions_")
    except CostBreakerTrippedError as exc:
        _status = SuggestionsServiceStatus(
            state="cost_locked",
            last_bootstrap_at=_status.last_bootstrap_at,
            last_drain_at=_status.last_drain_at,
            last_error=f"cost_breaker_tripped:{exc.reason}",
        )
        await asyncio.to_thread(
            _insert_refill_trigger_log_sync,
            "track_played", 0, 0,
            int((time.monotonic() - start) * 1000),
            0.0, True, None, f"breaker:{exc.reason}",
        )
        return RefillResult(
            candidates_evaluated=0, picks_returned=0,
            picks_validated=0, picks_inserted=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            cost_estimate_usd=0.0, cache_hit=False, cache_created=False,
            breaker_tripped=True,
        )

    _status = SuggestionsServiceStatus(
        state="refilling",
        last_bootstrap_at=_status.last_bootstrap_at,
        last_drain_at=_status.last_drain_at,
    )

    # 3. Shortlist.
    shortlist = await asyncio.to_thread(_build_shortlist_sync, 50, None)

    # 4. Soft-negatives.
    soft_negatives = await asyncio.to_thread(_read_soft_negatives_sync)

    # 5. Prompts.
    system_prompt = build_suggestions_ranking_system_prompt()
    user_prompt = build_suggestions_ranking_user_prompt(shortlist, soft_negatives)

    # 6. LLM call.
    client = _get_anthropic_client()
    response: SuggestionRankingResponse = await client.call_with_structured_output(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=SuggestionRankingResponse,
        max_tokens=2000,
        purpose="suggestions_rank",
        thinking="off",
    )

    # 6b. W1 — runtime warning on FIRST observed call when cache creation didn't engage.
    total_so_far = await asyncio.to_thread(
        _count_llm_usage_by_purpose_sync, "suggestions_rank"
    )
    if total_so_far == 1:
        first_row = await asyncio.to_thread(
            _read_latest_llm_usage_sync, "suggestions_rank"
        )
        if first_row is not None and first_row.cache_creation_input_tokens == 0:
            logger.warning(
                "suggestions ranking cache creation not engaged "
                "(system prompt may be below 2048 tokens; first "
                "'suggestions_rank' call returned "
                "cache_creation_input_tokens=0)"
            )

    # 7. Validate (Pitfall 10) + retry once if needed.
    valid_picks, invalid_indices = _validate_picks(
        response.picks, len(shortlist),
    )
    if invalid_indices and len(valid_picks) < deficit:
        retry_user_prompt = user_prompt + (
            f"\n\n## VALIDATION FAILURE\n"
            f"The previous response returned invalid candidate_index values: "
            f"{invalid_indices}. Valid range is 0..{max(0, len(shortlist) - 1)}. "
            f"Provide a fresh ranked list — integer indices only, within range."
        )
        response = await client.call_with_structured_output(
            system_prompt=system_prompt,
            user_prompt=retry_user_prompt,
            response_model=SuggestionRankingResponse,
            max_tokens=2000,
            purpose="suggestions_rank",
            thinking="off",
        )
        valid_picks, _ = _validate_picks(response.picks, len(shortlist))

    # 8. Trim to deficit + dedupe candidate_index.
    seen = set()
    kept: List[SuggestionRankingPick] = []
    for p in valid_picks:
        if p.candidate_index in seen:
            continue
        seen.add(p.candidate_index)
        kept.append(p)
        if len(kept) >= deficit:
            break

    # 9 + 10. Persist + Plex push.
    latency_ms = int((time.monotonic() - start) * 1000)
    last_usage = await asyncio.to_thread(
        _read_latest_llm_usage_sync, "suggestions_rank"
    )
    cost_usd = last_usage.cost_estimate_usd if last_usage else 0.0
    cache_created = bool(
        last_usage and last_usage.cache_creation_input_tokens > 0
    )
    cache_hit = bool(
        last_usage and last_usage.cache_read_input_tokens > 0
    )

    await asyncio.to_thread(
        _write_refill_results_sync,
        kept, shortlist, "track_played", None,
        latency_ms, cost_usd,
    )

    if kept:
        rating_keys = [shortlist[p.candidate_index]["plex_rating_key"] for p in kept]
        mp = await asyncio.to_thread(_find_suggestions_managed_playlist_sync)
        if mp is None:
            logger.warning(
                "refill_suggestions_queue: no ManagedPlaylist(kind=suggestions) "
                "row found; bootstrap was likely skipped. Skipping Plex push."
            )
        elif not mp.plex_rating_key:
            # CR-01 fix — first non-empty refill: materialize the deferred
            # Plex playlist and update the sentinel row in-place.
            plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)
            await _materialize_suggestions_plex_playlist(
                plex_url, plex_token, rating_keys,
            )
        else:
            plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)
            try:
                await update_playlist_items(
                    plex_url, plex_token, mp.plex_rating_key, rating_keys,
                )
            except Exception:
                logger.exception(
                    "Plex update_playlist_items failed during refill; "
                    "mirror state is the truth — Plex will reconcile on "
                    "next refill."
                )

    _status = SuggestionsServiceStatus(
        state="idle",
        last_bootstrap_at=_status.last_bootstrap_at,
        last_drain_at=_status.last_drain_at,
    )

    return RefillResult(
        candidates_evaluated=len(shortlist),
        picks_returned=len(response.picks),
        picks_validated=len(valid_picks),
        picks_inserted=len(kept),
        latency_ms=latency_ms,
        cost_estimate_usd=cost_usd,
        cache_hit=cache_hit,
        cache_created=cache_created,
    )


async def refill_suggestions_for_vibe(
    vibe_id: int, target: int = 15,
) -> RefillResult:
    """SUGG-10 — targeted refill for ONE vibe. Goes through the SAME cost
    breaker AND the SAME LLM-call shape as ``refill_suggestions_queue`` —
    just with a single-vibe shortlist partition (W2) and the
    ``event_source='vibe_coverage_cta'`` RefillTriggerLog tag.
    """
    from app.services.llm_cost_breaker import (
        CostBreakerTrippedError, check_or_raise,
    )

    global _status
    start = time.monotonic()

    # 1. Cost breaker FIRST.
    try:
        await check_or_raise(purpose_prefix="suggestions_")
    except CostBreakerTrippedError as exc:
        _status = SuggestionsServiceStatus(
            state="cost_locked",
            last_bootstrap_at=_status.last_bootstrap_at,
            last_drain_at=_status.last_drain_at,
            last_error=f"cost_breaker_tripped:{exc.reason}",
        )
        await asyncio.to_thread(
            _insert_refill_trigger_log_sync,
            "vibe_coverage_cta", 0, 0,
            int((time.monotonic() - start) * 1000),
            0.0, True, vibe_id, f"breaker:{exc.reason}",
        )
        return RefillResult(
            candidates_evaluated=0, picks_returned=0,
            picks_validated=0, picks_inserted=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            cost_estimate_usd=0.0, cache_hit=False, cache_created=False,
            breaker_tripped=True,
        )

    _status = SuggestionsServiceStatus(
        state="refilling",
        last_bootstrap_at=_status.last_bootstrap_at,
        last_drain_at=_status.last_drain_at,
    )

    # 2. Single-vibe shortlist (W2).
    shortlist = await asyncio.to_thread(
        _build_shortlist_sync, target, vibe_id,
    )

    # CR-02 fix — empty-shortlist short-circuit. Mirrors the deficit==0 guard
    # in refill_suggestions_queue: if the targeted vibe has no eligible
    # 2σ-in-band unrated tracks, calling Anthropic with an empty candidate
    # list would burn two daily-quota slots (initial + Pitfall 10 retry) for
    # guaranteed-invalid output. Log a zero-cost trigger row and bail.
    if not shortlist:
        await asyncio.to_thread(
            _insert_refill_trigger_log_sync,
            "vibe_coverage_cta", 0, 0,
            int((time.monotonic() - start) * 1000),
            0.0, False, vibe_id, "empty_shortlist",
        )
        _status = SuggestionsServiceStatus(
            state="idle",
            last_bootstrap_at=_status.last_bootstrap_at,
            last_drain_at=_status.last_drain_at,
        )
        return RefillResult(
            candidates_evaluated=0, picks_returned=0,
            picks_validated=0, picks_inserted=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            cost_estimate_usd=0.0, cache_hit=False, cache_created=False,
        )

    soft_negatives = await asyncio.to_thread(_read_soft_negatives_sync)
    system_prompt = build_suggestions_ranking_system_prompt()
    user_prompt = build_suggestions_ranking_user_prompt(shortlist, soft_negatives)

    client = _get_anthropic_client()
    response: SuggestionRankingResponse = await client.call_with_structured_output(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=SuggestionRankingResponse,
        max_tokens=2000,
        purpose="suggestions_rank",
        thinking="off",
    )

    valid_picks, invalid_indices = _validate_picks(
        response.picks, len(shortlist),
    )
    if invalid_indices and len(valid_picks) < target:
        retry_user_prompt = user_prompt + (
            f"\n\n## VALIDATION FAILURE\n"
            f"Invalid candidate_index values: {invalid_indices}. "
            f"Valid range is 0..{max(0, len(shortlist) - 1)}."
        )
        response = await client.call_with_structured_output(
            system_prompt=system_prompt,
            user_prompt=retry_user_prompt,
            response_model=SuggestionRankingResponse,
            max_tokens=2000,
            purpose="suggestions_rank",
            thinking="off",
        )
        valid_picks, _ = _validate_picks(response.picks, len(shortlist))

    seen = set()
    kept: List[SuggestionRankingPick] = []
    for p in valid_picks:
        if p.candidate_index in seen:
            continue
        seen.add(p.candidate_index)
        kept.append(p)
        if len(kept) >= target:
            break

    latency_ms = int((time.monotonic() - start) * 1000)
    last_usage = await asyncio.to_thread(
        _read_latest_llm_usage_sync, "suggestions_rank"
    )
    cost_usd = last_usage.cost_estimate_usd if last_usage else 0.0
    cache_created = bool(
        last_usage and last_usage.cache_creation_input_tokens > 0
    )
    cache_hit = bool(
        last_usage and last_usage.cache_read_input_tokens > 0
    )

    await asyncio.to_thread(
        _write_refill_results_sync,
        kept, shortlist, "vibe_coverage_cta", vibe_id,
        latency_ms, cost_usd,
    )

    if kept:
        rating_keys = [shortlist[p.candidate_index]["plex_rating_key"] for p in kept]
        mp = await asyncio.to_thread(_find_suggestions_managed_playlist_sync)
        if mp is None:
            logger.warning(
                "refill_suggestions_for_vibe: no ManagedPlaylist(kind="
                "suggestions) row found; bootstrap was likely skipped. "
                "Skipping Plex push."
            )
        elif not mp.plex_rating_key:
            # CR-01 fix — first non-empty refill (via the vibe CTA path):
            # materialize the deferred Plex playlist.
            plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)
            await _materialize_suggestions_plex_playlist(
                plex_url, plex_token, rating_keys,
            )
        else:
            plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)
            try:
                await update_playlist_items(
                    plex_url, plex_token, mp.plex_rating_key, rating_keys,
                )
            except Exception:
                logger.exception(
                    "Plex update_playlist_items failed during targeted "
                    "vibe refill; mirror state is the truth."
                )

    _status = SuggestionsServiceStatus(
        state="idle",
        last_bootstrap_at=_status.last_bootstrap_at,
        last_drain_at=_status.last_drain_at,
    )

    return RefillResult(
        candidates_evaluated=len(shortlist),
        picks_returned=len(response.picks),
        picks_validated=len(valid_picks),
        picks_inserted=len(kept),
        latency_ms=latency_ms,
        cost_estimate_usd=cost_usd,
        cache_hit=cache_hit,
        cache_created=cache_created,
    )


# ---------------------------------------------------------------------------
# Skip-tracking (D-11 / D-12 / D-13 / SUGG-08 / SUGG-09)
# ---------------------------------------------------------------------------


def _read_track_id_artist_sync(rating_key: str) -> Tuple[Optional[int], Optional[str]]:
    from app.models.track import Track

    with Session(get_engine()) as session:
        t = session.exec(
            select(Track).where(Track.plex_rating_key == rating_key)
        ).first()
        if t is None:
            return (None, None)
        return (t.id, t.artist)


def _insert_negative_signals_for_dismiss_sync(
    track_id: int, artist: Optional[str],
) -> None:
    from app.models.suggestions import NegativeSignal

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        session.add(NegativeSignal(
            track_id=track_id, artist=None,
            signal_type="hard_track",
            created_at=now_iso,
            recovery_pending=False,
        ))
        if artist:
            session.add(NegativeSignal(
                track_id=None, artist=artist,
                signal_type="hard_artist",
                created_at=now_iso,
                recovery_pending=True,
            ))
        session.commit()


def _clear_hard_artist_recovery_sync(artist: str) -> int:
    from app.models.suggestions import NegativeSignal

    with Session(get_engine()) as session:
        rows = session.exec(
            select(NegativeSignal).where(
                NegativeSignal.artist == artist
            ).where(NegativeSignal.signal_type == "hard_artist")
        ).all()
        cleared = 0
        for r in rows:
            if r.recovery_pending:
                r.recovery_pending = False
                session.add(r)
                cleared += 1
        session.commit()
        return cleared


def _soft_negative_sweep_sync() -> int:
    """Sweep SuggestionHistory for rows >= 14 days old whose matching Track
    is unrated AND was played after surfaced_at. For each such track, insert
    a NegativeSignal(signal_type='soft') if not already present.
    """
    from app.models.suggestions import NegativeSignal, SuggestionHistory
    from app.models.track import Track

    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=14)
    ).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    with Session(get_engine()) as session:
        old_rows = session.exec(
            select(SuggestionHistory).where(
                SuggestionHistory.surfaced_at < cutoff_iso
            )
        ).all()
        count = 0
        for sh in old_rows:
            t = session.exec(
                select(Track).where(Track.id == sh.track_id)
            ).first()
            if t is None:
                continue
            # Track must be unrated.
            if t.user_rating is not None and t.user_rating > 0:
                continue
            # Track must have been played after surfacing.
            if t.last_viewed_at is None or t.last_viewed_at <= sh.surfaced_at:
                continue
            # Already marked soft?
            existing = session.exec(
                select(NegativeSignal).where(
                    NegativeSignal.track_id == t.id
                ).where(NegativeSignal.signal_type == "soft")
            ).first()
            if existing is not None:
                continue
            session.add(NegativeSignal(
                track_id=t.id, artist=None,
                signal_type="soft",
                created_at=now_iso,
                recovery_pending=False,
            ))
            count += 1
        session.commit()
        return count


async def handle_dismiss_track(rating_key: str) -> None:
    """SUGG-09 / D-13 — write hard_track + hard_artist signals AND drain the
    track from the mirror.
    """
    if not rating_key:
        return
    track_id, artist = await asyncio.to_thread(
        _read_track_id_artist_sync, rating_key,
    )
    if track_id is None:
        logger.info(
            "handle_dismiss_track: unknown rating_key=%s; no signals written",
            rating_key,
        )
        return
    await asyncio.to_thread(
        _insert_negative_signals_for_dismiss_sync, track_id, artist,
    )
    await drain_track_from_mirror(rating_key)


async def handle_artist_rating_recovery(artist: str, rating: float) -> None:
    """D-13 — clear hard_artist recovery_pending when the user rates 3+ stars
    on any track by that artist. ``rating`` is the RAW 0-10 userRating
    (Phase 5 D-15 / Pitfall 2); 6.0 raw == 3.0 stars per
    ``stars_from_user_rating``.
    """
    if not artist:
        return
    if rating is None or rating < 6.0:
        return
    cleared = await asyncio.to_thread(
        _clear_hard_artist_recovery_sync, artist,
    )
    if cleared:
        logger.info(
            "Cleared hard-negative artist deboost for %r "
            "(user rated %.1f raw / %.1f stars) — %d rows updated",
            artist, rating, rating / 2.0, cleared,
        )


async def handle_soft_negative_sweep() -> int:
    """SUGG-08 daily sweep — written for the no-arg APScheduler contract.

    Marks unrated tracks played from Suggestions 14+ days ago as
    ``NegativeSignal(signal_type='soft')``. Returns the count of new rows.
    """
    count = await asyncio.to_thread(_soft_negative_sweep_sync)
    logger.info("Soft-negative sweep wrote %d NegativeSignal rows", count)
    return count
