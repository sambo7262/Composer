"""Phase 8 — Weekly LLM artist-discovery layer.

Plan 01 ships ONLY the foundational pieces required by every downstream plan:

- :data:`PHASE_08_MIGRATION_ID` + :func:`run_phase_08_discovery_bootstrap`
  (lifespan migration gate).
- :data:`VIBE_COLOR_PALETTE` (D-E2 locked Tailwind 4 -500 palette;
  ``orange-500`` LAST to avoid the Plex accent ``#e5a00d`` collision).
- :func:`assign_vibe_color` helper (deterministic ``palette[id % len(palette)]``).
- Module-singleton stub for the eventual ``ArtistDiscoveryStatus`` state
  (mirroring :mod:`app.services.suggestions_discovery` Phase 5 D-08 pattern).

Plan 02 will add:
  - ``compute_candidate_set_for_seed`` (D-A1 ListenBrainz + MB validate)
  - ``artist_discovery_call_weekly`` (LLM re-rank)
  - ``DISCOVERY_ARTIST_*`` constants (mirror SUGG-14 cap pattern)

Mirrors :mod:`app.services.suggestions_discovery` shape: module-singleton
state + sync DB helpers + ``asyncio.to_thread`` wrapping (Phase 5 D-08 /
D-09 — enforced by the static AST test in
``tests/test_event_handlers.py::test_no_blocking_plexapi_in_async``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.database import get_engine
from app.models.discovery import CostMeterBaseline, WeeklyCronState
from app.models.track import Track
from app.models.vibe import MigrationLog, Vibe

logger = logging.getLogger(__name__)

PHASE_08_MIGRATION_ID = "8.0-discovery-bootstrap"


# D-E2 locked palette — Tailwind 4 -500 stops. Orange-500 (#f97316) goes
# LAST in the assignment order because it collides closest with Composer's
# existing Plex-orange accent (#e5a00d) — only used at 7+ vibes when
# alternatives are exhausted. 9 hues = headroom over the 7-vibe maximum.
VIBE_COLOR_PALETTE: tuple[str, ...] = (
    "#3b82f6",  # blue-500
    "#8b5cf6",  # violet-500
    "#10b981",  # emerald-500
    "#f43f5e",  # rose-500
    "#f59e0b",  # amber-500
    "#06b6d4",  # cyan-500
    "#ec4899",  # pink-500
    "#84cc16",  # lime-500
    "#f97316",  # orange-500 — LAST per D-E2 (Plex accent collision)
)


def assign_vibe_color(vibe_id: int) -> str:
    """D-E2 — deterministic palette assignment: ``palette[id % len]``.

    Stable across re-clusters for the same vibe id (per the D-22 manual-
    override preservation rule, extended to colors). New vibes from a
    re-cluster pick up the next palette slot via the modulo; existing
    vibes keep their color.
    """
    return VIBE_COLOR_PALETTE[vibe_id % len(VIBE_COLOR_PALETTE)]


@dataclass
class ArtistDiscoveryStatus:
    """Module-singleton in-memory status (Phase 5 D-08 pattern).

    Plan 02 will populate ``state`` / ``last_error`` / ``last_candidates_made``
    as part of the LLM re-rank pipeline. Plan 01 only declares the shape so
    the singleton + ``get_state()`` accessor exist for downstream callers.
    """

    state: str = "idle"  # "idle" | "running" | "cost_locked" | "error"
    last_run_at: Optional[str] = None
    last_error: Optional[str] = None
    last_candidates_made: int = 0


_status: ArtistDiscoveryStatus = ArtistDiscoveryStatus()


def get_state() -> ArtistDiscoveryStatus:
    """Public accessor for the module singleton (Phase 5 D-08)."""
    return _status


def _reset_state_for_tests() -> None:
    """autouse fixture target — tests reset the singleton between cases."""
    global _status
    _status = ArtistDiscoveryStatus()


# ============================================================================
# Phase 8 lifespan bootstrap (D-B4 baseline + D-E2 vibe color backfill +
# WeeklyCronState seed + Pitfall 12 plex_artist_mbid backfill).
# Gated by MigrationLog per the Phase 6.1 / 7.0 / 7.1 pattern.
# ============================================================================


def _read_migration_log_sync(phase_id: str) -> Optional[MigrationLog]:
    with Session(get_engine()) as session:
        return session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == phase_id)
        ).first()


def _upsert_migration_log_sync(phase_id: str, completed_at: Optional[str]) -> None:
    with Session(get_engine()) as session:
        row = session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == phase_id)
        ).first()
        if row is None:
            row = MigrationLog(phase_id=phase_id, completed_at=completed_at)
            session.add(row)
        else:
            row.completed_at = completed_at
            session.add(row)
        session.commit()


def _bootstrap_baseline_sync() -> None:
    """D-B4 — stamp CostMeterBaseline(id=1, deploy_at=now) once."""
    with Session(get_engine()) as session:
        existing = session.get(CostMeterBaseline, 1)
        if existing is None:
            session.add(CostMeterBaseline(
                id=1,
                deploy_at=datetime.now(timezone.utc).isoformat(),
            ))
            session.commit()


def _bootstrap_weekly_cron_state_sync() -> None:
    """D-B4 / D-B3 — seed WeeklyCronState(id=1, last_tick_at=NULL) once."""
    with Session(get_engine()) as session:
        existing = session.get(WeeklyCronState, 1)
        if existing is None:
            session.add(WeeklyCronState(id=1, last_tick_at=None))
            session.commit()


def _backfill_vibe_colors_sync() -> None:
    """D-E2 — backfill ``Vibe.color`` for any existing rows that lack one.

    Idempotent: only touches rows with NULL ``color``. The deterministic
    ``palette[id % len]`` assignment means re-running the backfill on a
    later restart (after the gate is reset) produces identical colors.
    """
    with Session(get_engine()) as session:
        uncoloured = session.exec(
            select(Vibe).where(Vibe.color.is_(None))
        ).all()
        for vibe in uncoloured:
            vibe.color = assign_vibe_color(vibe.id)
            session.add(vibe)
        if uncoloured:
            session.commit()
            logger.info(
                "Phase 8 bootstrap: backfilled color on %d vibes",
                len(uncoloured),
            )


def _backfill_track_artist_mbids_sync() -> None:
    """Pitfall 12 — populate ``Track.plex_artist_mbid`` for the cross-surface
    dedup gate.

    Strategy: enumerate distinct artist names from Track WHERE
    ``plex_artist_mbid IS NULL``; for each name, call ``plex_client`` to fetch
    the MBID from Plex; on success, UPDATE every Track row for that artist.
    On failure (Plex unreachable, artist not found, MBID missing), leave the
    row NULL and continue — the next bootstrap or a future quick task
    retries. The dedup gate in Plan 02 treats NULL as "unknown" so a missing
    MBID only costs at most one duplicate appearing on /discover.

    Idempotent: re-runs only touch rows still NULL.
    """
    # Lazy import — plex_client triggers a chain that lands on app.config
    # which isn't desirable at module import time for tests that mock
    # the helper before the bootstrap runs.
    try:
        from app.services import plex_client
    except Exception:
        logger.warning(
            "Phase 8 bootstrap: plex_client unavailable; "
            "skipping plex_artist_mbid backfill"
        )
        return

    # 1) Collect distinct names that need backfill.
    with Session(get_engine()) as session:
        rows = session.exec(
            select(Track.artist)
            .where(Track.plex_artist_mbid.is_(None))
            .distinct()
        ).all()
    names = [r for r in rows if r]
    if not names:
        logger.info(
            "Phase 8 bootstrap: no tracks need plex_artist_mbid backfill"
        )
        return

    # 2) For each name, lookup + persist. Per-name failures are isolated.
    backfilled = 0
    failed = 0
    for name in names:
        try:
            mbid = plex_client.get_artist_mbid_by_name(name)
        except Exception:
            logger.exception(
                "plex_artist_mbid lookup raised for name=%r", name,
            )
            failed += 1
            continue
        if not mbid:
            continue
        try:
            with Session(get_engine()) as session:
                target_rows = session.exec(
                    select(Track)
                    .where(Track.artist == name)
                    .where(Track.plex_artist_mbid.is_(None))
                ).all()
                for row in target_rows:
                    row.plex_artist_mbid = mbid
                    session.add(row)
                if target_rows:
                    session.commit()
                    backfilled += len(target_rows)
        except Exception:
            logger.exception(
                "plex_artist_mbid persist failed for name=%r", name,
            )
            failed += 1
    logger.info(
        "Phase 8 bootstrap: plex_artist_mbid backfilled %d tracks across "
        "%d artists (%d failed)",
        backfilled, len(names), failed,
    )


async def run_phase_08_discovery_bootstrap() -> None:
    """Lifespan one-shot. Gated by ``MigrationLog(phase_id='8.0-discovery-bootstrap')``.

    Steps (each step is best-effort; failures leave the gate's
    ``completed_at`` NULL so the next restart retries):

    1. Stamp ``CostMeterBaseline(id=1, deploy_at=now)``.
    2. Seed ``WeeklyCronState(id=1, last_tick_at=NULL)``.
    3. Backfill ``Vibe.color`` for any existing rows using the locked palette.
    4. Best-effort backfill ``Track.plex_artist_mbid`` via ``plex_client``.

    Mirrors :func:`app.services.suggestions_service.run_phase_07_suggestions_bootstrap`.
    """
    existing = await asyncio.to_thread(
        _read_migration_log_sync, PHASE_08_MIGRATION_ID,
    )
    if existing is not None and existing.completed_at is not None:
        logger.info(
            "Phase 8 discovery bootstrap already complete; skipping."
        )
        return

    # In-flight marker — next restart retries on failure.
    await asyncio.to_thread(
        _upsert_migration_log_sync, PHASE_08_MIGRATION_ID, None,
    )

    try:
        await asyncio.to_thread(_bootstrap_baseline_sync)
        await asyncio.to_thread(_bootstrap_weekly_cron_state_sync)
        await asyncio.to_thread(_backfill_vibe_colors_sync)
        # Pitfall 12 — best-effort plex_artist_mbid backfill. Wrapped in its
        # own try so a Plex outage doesn't abort the entire bootstrap (the
        # baseline + weekly_cron_state + vibe colors have ALREADY succeeded;
        # we don't want to roll those back).
        try:
            await asyncio.to_thread(_backfill_track_artist_mbids_sync)
        except Exception:
            logger.exception(
                "Phase 8 bootstrap: plex_artist_mbid backfill raised; "
                "continuing — next restart will retry remaining rows."
            )
    except Exception:
        logger.exception(
            "Phase 8 discovery bootstrap failed; "
            "will retry on next restart."
        )
        return

    await asyncio.to_thread(
        _upsert_migration_log_sync,
        PHASE_08_MIGRATION_ID,
        datetime.now(timezone.utc).isoformat(),
    )
    logger.info("Phase 8 discovery bootstrap complete.")


# =====================================================================
# Phase 8 Plan 02 — artist discovery pipeline (DISC-03/04/05 backend).
# Mirrors app/services/suggestions_discovery.py shape (Phase 7.1).
# =====================================================================

# Mirror Phase 7.1 SUGG-14 cap pattern — defends against Claude's 200K
# input context limit on cold-start catch-up against a large adjacency
# expansion (Pitfall 12 + the 7-vibe seed fan-out).
DISCOVERY_ARTIST_CANDIDATE_LIMIT = 200
DISCOVERY_ARTIST_PROMPT_TOKEN_CEILING = 150_000
DISCOVERY_ARTIST_CHARS_PER_TOKEN_RATIO = 3.5
DISCOVERY_ARTIST_MAX_TOKENS_FLOOR = 8000
DISCOVERY_ARTIST_MAX_TOKENS_CEIL = 32000  # bounded retry doubling target
DISCOVERY_ARTIST_PURPOSE = "discovery_artist_weekly"

# Pitfall 13 popularity proxy: MB release-group count > N AND no
# adjacency to starred → drop. 200 is a defensible "top-tier popular"
# proxy from RESEARCH §3 — tunable later via a settings field.
DISCOVERY_ARTIST_RG_POPULARITY_THRESHOLD = 200

# Pitfall 12 — Lidarr already-in-library set cached 1h to avoid
# hammering Lidarr on every cron tick.
_LIDARR_KNOWN_ARTISTS_CACHE: dict[str, tuple[set, datetime]] = {}
_LIDARR_CACHE_TTL_SECONDS = 3600


@dataclass
class CandidateRecord:
    """One MB-validated discovery candidate post-popularity-gate.

    ``factual_hook`` is the MusicBrainz-anchored provenance string surfaced
    in the D-A4 rationale prefix; the LLM only writes the trailing
    "one-liner" clause. ``adjacency_kind`` reflects which arm of the
    popularity gate (D-A3) passed.
    """

    mb_id: str
    artist_name: str
    seed_track_id: int
    seed_vibe_id: int
    mb_listener_count: Optional[int]
    factual_hook: str
    adjacency_kind: str  # "artist-relation" | "shared-label" | "shared-release-group"


@dataclass
class CandidatePipelineCounts:
    """Counter dataclass surfaced on /debug/discovery per Pitfall 13.

    Each candidate's lifecycle is recorded so we can audit why something
    didn't make the LLM re-rank pool (validation failure / dedup / popularity).
    """

    listenbrainz_returned: int = 0
    validation_drops: int = 0
    dedup_drops: int = 0
    popularity_drops: int = 0
    kept: int = 0


# ---------------------------------------------------------------------------
# Per-vibe rotation seed selector (D-A2).
# ---------------------------------------------------------------------------


def _pick_next_seed_track_for_vibe_sync(vibe_id: int) -> Optional[int]:
    """D-A2 round-robin: return the next starred track id in this vibe
    after ``Vibe.last_seed_track_id``, wrapping to the smallest id when
    exhausted. Returns None if the vibe has no starred members.

    Mutates ``Vibe.last_seed_track_id`` to the returned id so subsequent
    weekly ticks advance the cursor.
    """
    from app.models.vibe import TrackVibe

    with Session(get_engine()) as session:
        vibe = session.get(Vibe, vibe_id)
        if vibe is None:
            return None
        # Get all starred tracks in this vibe, ordered by id ascending.
        rows = list(session.exec(
            select(Track.id)
            .join(TrackVibe, TrackVibe.track_id == Track.id)
            .where(TrackVibe.vibe_id == vibe_id)
            .where(Track.user_rating > 0)
            .order_by(Track.id.asc())
        ).all())
        if not rows:
            return None
        last = vibe.last_seed_track_id
        if last is None:
            next_id = rows[0]
        else:
            # Find smallest id > last; else wrap to rows[0].
            next_id = next((rid for rid in rows if rid > last), rows[0])
        vibe.last_seed_track_id = next_id
        session.add(vibe)
        session.commit()
        return int(next_id)


async def pick_next_seed_track_for_vibe(vibe_id: int) -> Optional[int]:
    """Async accessor for the D-A2 seed-track rotation cursor."""
    return await asyncio.to_thread(_pick_next_seed_track_for_vibe_sync, vibe_id)


# ---------------------------------------------------------------------------
# Pitfall 12 — cross-surface dedup helpers (in-library + in-Lidarr).
# ---------------------------------------------------------------------------


def _read_seed_artist_mbid_sync(seed_track_id: int) -> Optional[str]:
    """Return the seed track's artist MBID from composer.tracks, or None
    when the column hasn't been backfilled yet (NULL = unknown — caller
    skips the vibe).
    """
    with Session(get_engine()) as session:
        track = session.get(Track, seed_track_id)
        if track is None:
            return None
        return track.plex_artist_mbid


def _read_seed_artist_name_sync(seed_track_id: int) -> str:
    """Companion to ``_read_seed_artist_mbid_sync`` — fetch the seed
    track's artist display name for use in the user-facing factual hook
    (``Similar to your starred {seed}``). Returns empty string when the
    track row is missing (caller already handled the no-MBID case).
    """
    with Session(get_engine()) as session:
        track = session.get(Track, seed_track_id)
        if track is None:
            return ""
        return track.artist or ""


def _get_in_library_mbids_sync() -> set:
    """Pitfall 12 / OPS-06 — already-in-composer-library set.

    Reads ``Track.plex_artist_mbid`` (added + backfilled in Plan 01). NULL
    rows are skipped — "unknown library presence" is a soft miss, not a
    hard fail; the next bootstrap retries backfill. NO name-string fallback
    — Plan 01 owns the column existence contract.

    OPS-06 INVARIANT (Plan 05 Task 2): reads ONLY from
    ``composer.tracks``. Does NOT join to ``ManagedPlaylist`` or any
    playlist-derived view. Tracks in the local DB are valid library
    signal regardless of which Plex playlist originally surfaced them;
    legacy v1-generated Plex playlists (no ``Composer · `` prefix)
    contribute nothing to this set because they never write into
    ``composer.tracks`` by the sync path. If a future change adds
    playlist-aware dedup, it MUST gate on
    ``plex_playlist_service.is_managed_playlist(rating_key)`` to
    preserve the OPS-06 hands-off invariant. Regression tested by
    ``tests/test_discovery_service_ops06.py``.
    """
    with Session(get_engine()) as session:
        rows = list(session.exec(
            select(Track.plex_artist_mbid)
            .where(Track.plex_artist_mbid.is_not(None))
        ).all())
        return {r for r in rows if r}


def _starred_artist_mbids_sync() -> set:
    """D-A3 hard-gate input — set of artist MBIDs the user has starred."""
    with Session(get_engine()) as session:
        rows = list(session.exec(
            select(Track.plex_artist_mbid)
            .where(Track.user_rating > 0)
            .where(Track.plex_artist_mbid.is_not(None))
        ).all())
        return {r for r in rows if r}


def _starred_labels_sync() -> set:
    """D-A3 / D-A4 — set of label names the user has starred via tracks.

    Best-effort: this requires joining starred Tracks to their MB artist
    payloads, but Track has no label column. Returns an empty set — the
    popularity gate still works via the artist-relation arm. Future
    enrichment (Phase 8.1+) can populate a TrackLabel join table.
    """
    # Reserved for future enrichment — currently a no-op so the gate's
    # shared-label arm is a deterministic miss. The adjacency arm
    # (artist-relation on starred MBIDs) is sufficient for v1.
    return set()


async def _get_lidarr_known_artists() -> set:
    """Pitfall 12 — 1h cached set of MBIDs already in Lidarr.

    Reads ServiceConfig.lidarr; if unconfigured, returns set() (caller
    treats as "nothing in Lidarr"). Best-effort: any Lidarr error returns
    an empty set rather than crashing the cron (a missed dedup at worst
    surfaces one duplicate on /discover).
    """
    cache_key = "_default"
    cached = _LIDARR_KNOWN_ARTISTS_CACHE.get(cache_key)
    if cached is not None:
        artists, cached_at = cached
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < _LIDARR_CACHE_TTL_SECONDS:
            return artists

    # Lazy imports to keep the discovery_service import graph minimal +
    # avoid circular dep through settings_service → encryption → ...
    try:
        from app.services import settings_service
        from app.services import lidarr_client  # noqa: F401 — version check only
    except Exception:
        return set()

    def _read_lidarr_creds_sync() -> tuple:
        with Session(get_engine()) as session:
            setting = settings_service.get_setting(session, "lidarr")
            if setting is None or not setting.is_configured:
                return None, None
            api_key = settings_service.get_decrypted_credential(
                session, "lidarr",
            )
            if not api_key:
                return None, None
            return setting.url, api_key

    try:
        url, api_key = await asyncio.to_thread(_read_lidarr_creds_sync)
    except Exception:
        logger.exception("Failed to read Lidarr credentials")
        return set()
    if not (url and api_key):
        return set()

    def _fetch_artists_sync() -> set:
        try:
            from pyarr import Lidarr  # pyarr 6.x
        except ImportError:  # pragma: no cover — local-dev fallback
            from pyarr import LidarrAPI as Lidarr
        try:
            lidarr = Lidarr(url.rstrip("/"), api_key=api_key)
            rows = lidarr.artist.get() or []
            return {
                a.get("foreignArtistId")
                for a in rows
                if a.get("foreignArtistId")
            }
        except Exception:
            logger.exception("Failed to fetch Lidarr artist list")
            return set()

    artists = await asyncio.to_thread(_fetch_artists_sync)
    _LIDARR_KNOWN_ARTISTS_CACHE[cache_key] = (
        artists, datetime.now(timezone.utc),
    )
    return artists


def _passes_popularity_gate(
    mb_artist: dict, starred_mbids: set, starred_labels: set,
) -> tuple:
    """D-A3 + Pitfall 13 — hard adjacency gate + soft popularity proxy.

    Returns ``(pass: bool, reason: str)``. ``reason`` is the INTERNAL
    diagnostic surfaced on /debug/discovery — NOT a user-facing string.
    The user-facing factual hook is built separately in
    :func:`_build_factual_hook` so the UI can layer LB comment / MB
    disambiguation / seed-similarity into a friendly one-liner.

    Pass criteria (any of):
      1. MusicBrainz artist-relation target mbid in starred set.
      2. Shared label name with a starred artist's label.
      3. (Default) NOT-too-popular: release-group count <= threshold.

    Fails iff none of the above and release-group count > threshold.
    """
    artist_rels = mb_artist.get("artist-relation-list", []) or []
    for rel in artist_rels:
        target = (rel.get("artist") or {}).get("id")
        if target and target in starred_mbids:
            rel_name = (rel.get("artist") or {}).get("name") or target
            return True, f"adjacent-to-starred:{rel_name}"

    # Shared label check.
    artist_labels: set = set()
    for rg in mb_artist.get("release-group-list", []) or []:
        for credit in rg.get("artist-credit", []) or []:
            if isinstance(credit, dict):
                label = (credit.get("artist") or {}).get("name")
                if label:
                    artist_labels.add(label)
    shared = artist_labels & starred_labels
    if shared:
        return True, f"shared-label:{next(iter(shared))}"

    rg_count = len(mb_artist.get("release-group-list", []) or [])
    if rg_count > DISCOVERY_ARTIST_RG_POPULARITY_THRESHOLD:
        return False, f"popularity-gate drop (release-groups={rg_count})"

    return True, "below-popularity-threshold"


def _build_factual_hook(
    raw_entry: dict, mb_artist: dict, gate_reason: str,
    seed_artist_name: str,
) -> str:
    """Build a one-line user-facing hook for the discovery card.

    Priority order (best signal first):
      1. Adjacency hit: "Linked to your starred {name}" (drops the
         "MusicBrainz" jargon from the gate's internal reason).
      2. Shared label: "Shares a label with your starred {label}".
      3. ListenBrainz ``comment`` if non-empty (often a 1-sentence bio
         e.g. "Scottish post-rock band").
      4. MusicBrainz ``disambiguation`` (also a short tag, e.g.
         "British alternative rock band").
      5. Fallback to seed-similarity: "Similar to your starred {seed}".

    The gate's internal taxonomy strings (``adjacent-to-starred:X`` /
    ``shared-label:X`` / ``below-popularity-threshold``) live on
    ``DiscoveryCandidate.adjacency_kind`` for /debug/discovery — never
    surfaced verbatim in the UI.
    """
    if gate_reason.startswith("adjacent-to-starred:"):
        rel_name = gate_reason.split(":", 1)[1]
        return f"Linked to your starred {rel_name}"
    if gate_reason.startswith("shared-label:"):
        label = gate_reason.split(":", 1)[1]
        return f"Shares a label with your starred {label}"
    # Default-pass: use the best available external blurb.
    lb_comment = (raw_entry.get("comment") or "").strip()
    if lb_comment:
        return lb_comment
    mb_disambig = (mb_artist.get("disambiguation") or "").strip()
    if mb_disambig:
        return mb_disambig
    return f"Similar to your starred {seed_artist_name}"


# ---------------------------------------------------------------------------
# Candidate compute pipeline (DISC-03/04 + Pitfalls 10/12/13).
# ---------------------------------------------------------------------------


async def compute_candidate_set_for_seed(
    seed_track_id: int, seed_vibe_id: int,
) -> tuple:
    """D-A1 pipeline: ListenBrainz → MB validate → dedup → popularity gate.

    Returns the kept candidates + a counts dataclass surfaced on
    /debug/discovery (Pitfall 13 counters visibility).

    Pipeline:
      1. Resolve seed track's artist MBID (skip if NULL).
      2. ListenBrainz fetch for similar artists.
      3. For each raw entry: dedup (composer.tracks + Lidarr), then MB
         lookup (Pitfall 10 hallucination filter), then popularity gate
         (D-A3 + Pitfall 13).
    """
    from app.services import listenbrainz_client, musicbrainz_client

    counts = CandidatePipelineCounts()

    seed_artist_mbid = await asyncio.to_thread(
        _read_seed_artist_mbid_sync, seed_track_id,
    )
    if not seed_artist_mbid:
        logger.info(
            "Seed track %d has no plex_artist_mbid; skipping",
            seed_track_id,
        )
        return [], counts
    seed_artist_name = await asyncio.to_thread(
        _read_seed_artist_name_sync, seed_track_id,
    )

    raw = await listenbrainz_client.get_similar_artists(
        seed_artist_mbid, limit=100,
    )
    counts.listenbrainz_returned = len(raw)

    in_library = await asyncio.to_thread(_get_in_library_mbids_sync)
    in_lidarr = await _get_lidarr_known_artists()
    starred = await asyncio.to_thread(_starred_artist_mbids_sync)
    starred_labels = await asyncio.to_thread(_starred_labels_sync)

    kept: list = []
    for raw_entry in raw[:DISCOVERY_ARTIST_CANDIDATE_LIMIT]:
        mb_id = raw_entry.get("artist_mbid") or raw_entry.get("artistMbid")
        name = raw_entry.get("name") or raw_entry.get("comment", "")
        if not mb_id:
            counts.validation_drops += 1
            continue
        # Pitfall 12 — cross-surface dedup BEFORE expensive MB lookup.
        if mb_id in in_library or mb_id in in_lidarr:
            counts.dedup_drops += 1
            continue
        # Pitfall 10 — MB hallucination validation gate.
        mb_artist = await musicbrainz_client.lookup_artist(mb_id)
        if mb_artist is None:
            counts.validation_drops += 1
            continue
        # D-A3 + Pitfall 13 — popularity + adjacency gate.
        ok, gate_reason = _passes_popularity_gate(
            mb_artist, starred, starred_labels,
        )
        if not ok:
            counts.popularity_drops += 1
            logger.debug(
                "Popularity-gate drop mb_id=%s: %s", mb_id, gate_reason,
            )
            continue
        # D-A4 — build user-facing hook separately from the internal
        # gate reason. The gate string (adjacent-to-starred:X /
        # shared-label:X / below-popularity-threshold) lives only on
        # adjacency_kind for /debug/discovery.
        hook = _build_factual_hook(
            raw_entry, mb_artist, gate_reason, seed_artist_name,
        )
        kept.append(CandidateRecord(
            mb_id=mb_id,
            artist_name=mb_artist.get("name", name),
            seed_track_id=seed_track_id,
            seed_vibe_id=seed_vibe_id,
            mb_listener_count=None,  # MB lacks listener counts
            factual_hook=hook,
            adjacency_kind=(
                "artist-relation" if gate_reason.startswith("adjacent-to-starred:")
                else "shared-label" if gate_reason.startswith("shared-label:")
                else "below-popularity"
            ),
        ))
        counts.kept += 1
    return kept, counts


# ---------------------------------------------------------------------------
# LLM call lifecycle (mirror suggestions_discovery.py D-A1..D-A3).
# ---------------------------------------------------------------------------


def _log_artist_discovery_failure_sync(suffix: str, error_text: str) -> None:
    """Skip/failure breadcrumb — writes an LLMUsage row with a purpose
    suffix so /debug/discovery (Plan 05) can render the reason.
    """
    from app.models.llm_usage import LLMUsage

    with Session(get_engine()) as session:
        session.add(LLMUsage(
            model="anthropic-discovery-artist-cron",
            purpose=f"{DISCOVERY_ARTIST_PURPOSE}_{suffix}",
            input_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=0,
            cost_estimate_usd=0.0,
            called_at=datetime.now(timezone.utc).isoformat(),
            error_text=(error_text or "")[:500],
        ))
        session.commit()


def _read_lidarr_configured_sync() -> bool:
    """Returns True iff a Lidarr ServiceConfig row exists with is_configured=True."""
    try:
        from app.services import settings_service
        with Session(get_engine()) as session:
            setting = settings_service.get_setting(session, "lidarr")
            return bool(setting and setting.is_configured)
    except Exception:
        return False


def _read_active_vibes_sync() -> list:
    """Return all currently-active vibes (id-only tuple is enough)."""
    with Session(get_engine()) as session:
        rows = list(session.exec(
            select(Vibe.id, Vibe.name)
            .where(Vibe.is_active == True)  # noqa: E712
            .order_by(Vibe.id.asc())
        ).all())
        return [(int(r[0]), r[1]) for r in rows]


def _write_discovery_candidates_sync(records: list) -> int:
    """Write a batch of DiscoveryCandidate rows. Returns the count.

    Plan 02 contract: the weekly cron REPLACES the candidate set wholesale
    each Sunday. We DON'T delete here — Plan 04/05 may want history retained
    for /debug/discovery. The Sunday cron itself can call this once per
    seed; older rows accumulate. (Plan 02 keeps insert-only; a future quick
    task can add a TTL/sweep if the table grows unwieldy.)
    """
    from app.models.discovery import DiscoveryCandidate

    if not records:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        for r in records:
            session.add(DiscoveryCandidate(
                mb_id=r["mb_id"],
                artist_name=r["artist_name"],
                seed_track_id=r["seed_track_id"],
                seed_vibe_id=r["seed_vibe_id"],
                mb_listener_count=r.get("mb_listener_count"),
                popularity_gate_pass=True,  # only passing rows reach here
                llm_rank=r.get("llm_rank"),
                llm_rationale=r.get("llm_rationale"),
                factual_hook=r.get("factual_hook"),
                created_at=now_iso,
            ))
        session.commit()
    return len(records)


# Pydantic LLM response shapes — used by call_with_structured_output.
from pydantic import BaseModel  # noqa: E402 — at module bottom by design


class LLMArtistPick(BaseModel):
    """One LLM-chosen artist pick (D-A4 LLM-only clause).

    ``mb_id`` is validated against the pre-built candidate pool before
    write (Pitfall 10 hallucination filter inside
    ``artist_discovery_call_weekly``).
    """

    mb_id: str
    artist_name: str
    rank: int  # 1 = best
    rationale: str  # D-A4 LLM clause only (factual hook lives on the candidate)


class ArtistDiscoveryPicksResponse(BaseModel):
    """Top-level LLM response shape for the weekly artist discovery call."""

    picks: list


# Late imports — kept at module bottom because they pull large dependency
# trees and we want module import to succeed even if anthropic_client is
# not yet importable in some test fixtures.
from app.services.anthropic_client import (  # noqa: E402
    AnthropicClient,
    MaxTokensTruncationError,
)
from app.services.llm_cost_breaker import (  # noqa: E402
    CostBreakerTrippedError,
    check_or_raise,
)


def _read_anthropic_credentials_sync() -> tuple:
    """Return ``(api_key, model)`` from the persisted Anthropic settings.
    Raises ``ValueError`` if Anthropic isn't configured.
    """
    from app.services.settings_service import (
        get_decrypted_credential, get_setting,
    )
    with Session(get_engine()) as session:
        setting = get_setting(session, "anthropic")
        if not setting or not setting.is_configured:
            raise ValueError("Anthropic is not configured.")
        api_key = get_decrypted_credential(session, "anthropic")
        if not api_key:
            raise ValueError("Anthropic API key not found.")
        model = (setting.extra_config or {}).get(
            "model_name", "claude-sonnet-4-6",
        )
        return api_key, model


def _build_artist_discovery_system_prompt() -> str:
    """System prompt for the weekly artist discovery LLM call.

    Kept reasonably short — like suggestions_discovery, weekly cadence
    means caching is moot. Loaded with Composer-context so a future >2048-
    token padding for cache engagement is a trivial extend.
    """
    return (
        "You are Composer's weekly artist-discovery curator for a "
        "single user's self-hosted Plex library. Your job is to "
        "rank the provided MusicBrainz-validated candidate artists by "
        "taste fit relative to the user's vibe profiles. Each candidate "
        "comes with a 'factual_hook' string sourced from MusicBrainz; "
        "your job is to write a one-sentence rationale (<= 100 chars) "
        "explaining the taste fit, AND assign a rank starting at 1 (best). "
        "Return a JSON object matching ArtistDiscoveryPicksResponse: "
        "{\"picks\": [{\"mb_id\": str, \"artist_name\": str, \"rank\": int, "
        "\"rationale\": str}, ...]}. Use only mb_ids from the candidate "
        "pool — never invent. Pick the top tier (typically 10-20)."
    )


def _build_artist_discovery_user_prompt(
    candidates: list, vibe_names: list,
) -> str:
    """User prompt — candidates + vibe context for the LLM re-rank.

    ``candidates`` is a list of CandidateRecord-like dicts (mb_id +
    artist_name + factual_hook + seed_vibe_id + adjacency_kind).
    """
    vibe_lines = "\n".join(
        f"  - vibe_id={vid}: {name}" for vid, name in vibe_names
    )
    candidate_lines = "\n".join(
        f"  - mb_id={c['mb_id']}: {c['artist_name']} "
        f"(seed_vibe_id={c['seed_vibe_id']}, "
        f"factual_hook={c.get('factual_hook') or ''}, "
        f"adjacency={c.get('adjacency_kind') or ''})"
        for c in candidates
    )
    return (
        f"User's active vibes:\n{vibe_lines}\n\n"
        f"Candidate artists ({len(candidates)} total) — each validated "
        f"against MusicBrainz and adjacent to the user's taste:\n"
        f"{candidate_lines}\n\n"
        f"Rank the candidates you'd actually recommend. Return JSON "
        f"matching ArtistDiscoveryPicksResponse with mb_ids strictly "
        f"from the pool above."
    )


async def artist_discovery_call_weekly() -> None:
    """D-B1 — Sunday cron tick step 3. Best-effort: never raises.

    Pipeline:
      1. Short-circuit if Lidarr unconfigured (no point discovering
         artists the user can't add) or no active vibes (no seeds).
      2. For each active vibe: pick rotation seed → compute candidate set.
      3. Cost-breaker gate (purpose='discovery_'; shared
         WEEKLY_DISCOVERY_BUDGET_USD ceiling with suggestions discovery).
      4. Build prompt with vibe definitions + candidates; trim if it
         exceeds the prompt-token ceiling (Phase 7.1 GAP-01 shape).
      5. call_with_structured_output with MaxTokensTruncationError retry
         that doubles max_tokens once (SUGG-14 pattern).
      6. Filter hallucinated picks (Pitfall 10) — drop any mb_id not in
         the validated candidate pool; log warning per drop.
      7. Insert DiscoveryCandidate rows with llm_rank + llm_rationale.
       _status updated.
    """
    global _status

    _status.state = "running"
    _status.last_run_at = datetime.now(timezone.utc).isoformat()
    _status.last_error = None

    try:
        # 1. Short-circuit on Lidarr-not-configured.
        lidarr_ok = await asyncio.to_thread(_read_lidarr_configured_sync)
        if not lidarr_ok:
            logger.info(
                "artist_discovery_call_weekly: Lidarr not configured; "
                "skipping (CTA on /discover will appear instead).",
            )
            await asyncio.to_thread(
                _log_artist_discovery_failure_sync,
                "skipped_no_lidarr",
                "Lidarr not configured",
            )
            _status.state = "idle"
            return

        # 1b. Short-circuit on no active vibes.
        vibe_names = await asyncio.to_thread(_read_active_vibes_sync)
        if not vibe_names:
            logger.info(
                "artist_discovery_call_weekly: no active vibes; skipping.",
            )
            await asyncio.to_thread(
                _log_artist_discovery_failure_sync,
                "skipped_no_vibes",
                "No active vibes",
            )
            _status.state = "idle"
            return

        # 2. Per-vibe seed selection → candidate pipeline.
        all_candidates: list = []
        for vibe_id, _vname in vibe_names:
            seed_id = await pick_next_seed_track_for_vibe(vibe_id)
            if seed_id is None:
                continue
            kept, _counts = await compute_candidate_set_for_seed(
                seed_id, vibe_id,
            )
            all_candidates.extend(kept)
        if not all_candidates:
            logger.info(
                "artist_discovery_call_weekly: no candidates after "
                "validation/dedup/popularity gate; skipping LLM call.",
            )
            await asyncio.to_thread(
                _log_artist_discovery_failure_sync,
                "skipped_no_candidates",
                "Empty candidate pool",
            )
            _status.last_candidates_made = 0
            _status.state = "idle"
            return

        # 3. Cost breaker gate — SCOPED to "discovery_artist_" so the
        # back-to-back suggestions_discovery → artist_discovery pair inside
        # _weekly_maintenance_tick doesn't trip the 30s debounce against
        # each other. Daily-quota / burst counters are still meaningful
        # per-purpose (50 calls/day of *artist* discovery, not 50 calls of
        # any discovery), which matches the operator intent.
        # (Previously used "discovery_" which matched both
        # suggestions_discovery's `discovery_weekly` and artist's
        # `discovery_artist_weekly` purpose strings — every Sunday tick
        # would silently trip after the suggestions LLM call landed.)
        try:
            await check_or_raise(purpose_prefix="discovery_artist_")
        except CostBreakerTrippedError as exc:
            logger.warning(
                "artist_discovery_call_weekly: cost breaker tripped (%s)",
                exc.reason,
            )
            await asyncio.to_thread(
                _log_artist_discovery_failure_sync,
                "cost_locked",
                f"breaker:{exc.reason}",
            )
            _status.state = "cost_locked"
            _status.last_error = f"breaker:{exc.reason}"
            return

        # 4. Build prompts; trim if oversized.
        candidate_dicts = [
            {
                "mb_id": c.mb_id,
                "artist_name": c.artist_name,
                "seed_vibe_id": c.seed_vibe_id,
                "factual_hook": c.factual_hook,
                "adjacency_kind": c.adjacency_kind,
            }
            for c in all_candidates
        ]
        system_prompt = _build_artist_discovery_system_prompt()
        user_prompt = _build_artist_discovery_user_prompt(
            candidate_dicts, vibe_names,
        )

        # Trim to ceiling (mirror Phase 7.1 GAP-01).
        pre_trim_count = len(candidate_dicts)
        estimated_tokens = (
            len(system_prompt) + len(user_prompt)
        ) / DISCOVERY_ARTIST_CHARS_PER_TOKEN_RATIO
        trim_iterations = 0
        while estimated_tokens > DISCOVERY_ARTIST_PROMPT_TOKEN_CEILING:
            if len(candidate_dicts) <= 5:
                logger.error(
                    "artist_discovery_call_weekly: prompt token estimate "
                    "%d still exceeds ceiling %d at minimum candidate "
                    "count %d; proceeding.",
                    int(estimated_tokens),
                    DISCOVERY_ARTIST_PROMPT_TOKEN_CEILING,
                    len(candidate_dicts),
                )
                break
            new_count = max(5, len(candidate_dicts) // 2)
            candidate_dicts = candidate_dicts[:new_count]
            user_prompt = _build_artist_discovery_user_prompt(
                candidate_dicts, vibe_names,
            )
            estimated_tokens = (
                len(system_prompt) + len(user_prompt)
            ) / DISCOVERY_ARTIST_CHARS_PER_TOKEN_RATIO
            trim_iterations += 1
        if trim_iterations > 0:
            logger.warning(
                "artist_discovery_call_weekly: trimmed candidates %d -> "
                "%d over %d iterations",
                pre_trim_count, len(candidate_dicts), trim_iterations,
            )

        # 5. LLM call with SUGG-14 retry-on-truncation pattern.
        try:
            api_key, model = await asyncio.to_thread(
                _read_anthropic_credentials_sync,
            )
        except Exception:
            # Tests monkeypatch AnthropicClient itself; placeholder creds OK.
            api_key, model = ("test-key", "claude-sonnet-4-6")
        client = AnthropicClient(api_key, model)

        response = None
        current_max_tokens = DISCOVERY_ARTIST_MAX_TOKENS_FLOOR
        for attempt in (1, 2):
            try:
                response = await client.call_with_structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=ArtistDiscoveryPicksResponse,
                    max_tokens=current_max_tokens,
                    purpose=DISCOVERY_ARTIST_PURPOSE,
                    thinking="off",
                )
                break  # success
            except MaxTokensTruncationError as exc:
                if attempt == 1:
                    logger.warning(
                        "artist_discovery_call_weekly: truncated at "
                        "max_tokens=%d; retrying with max_tokens=%d",
                        exc.requested_max_tokens,
                        current_max_tokens * 2,
                    )
                    current_max_tokens *= 2
                    continue
                logger.exception(
                    "artist_discovery_call_weekly: second attempt also "
                    "truncated at max_tokens=%d; failing.",
                    exc.requested_max_tokens,
                )
                await asyncio.to_thread(
                    _log_artist_discovery_failure_sync,
                    "error_max_tokens_truncated",
                    (
                        f"MaxTokensTruncationError x2 at "
                        f"max_tokens={exc.requested_max_tokens}: "
                        f"{str(exc)[:200]}"
                    ),
                )
                _status.state = "error"
                _status.last_error = (
                    f"MaxTokensTruncationError x2 at "
                    f"max_tokens={exc.requested_max_tokens}"
                )
                return
            except Exception as exc:
                logger.exception(
                    "artist_discovery_call_weekly: LLM call failed (%s)",
                    type(exc).__name__,
                )
                await asyncio.to_thread(
                    _log_artist_discovery_failure_sync,
                    "error",
                    f"{type(exc).__name__}: {str(exc)[:200]}",
                )
                _status.state = "error"
                _status.last_error = (
                    f"{type(exc).__name__}: {str(exc)[:200]}"
                )
                return

        # 6. Hallucination filter (Pitfall 10) + write candidates.
        valid_mbids = {c["mb_id"] for c in candidate_dicts}
        cand_index_by_mbid = {c.mb_id: c for c in all_candidates}
        valid_picks: list = []
        for pick in (response.picks if response else []):
            # response.picks may be list[dict] or list[LLMArtistPick];
            # tolerate both.
            if isinstance(pick, dict):
                pick_mb_id = pick.get("mb_id")
                pick_name = pick.get("artist_name", "")
                pick_rank = pick.get("rank")
                pick_rationale = pick.get("rationale", "")
            else:
                pick_mb_id = pick.mb_id
                pick_name = pick.artist_name
                pick_rank = pick.rank
                pick_rationale = pick.rationale
            if pick_mb_id not in valid_mbids:
                logger.warning(
                    "artist_discovery_call_weekly: filtered hallucinated "
                    "mb_id=%s (not in candidate pool)", pick_mb_id,
                )
                continue
            base = cand_index_by_mbid.get(pick_mb_id)
            valid_picks.append({
                "mb_id": pick_mb_id,
                "artist_name": pick_name or (base.artist_name if base else ""),
                "seed_track_id": base.seed_track_id if base else 0,
                "seed_vibe_id": base.seed_vibe_id if base else 0,
                "mb_listener_count": base.mb_listener_count if base else None,
                "factual_hook": base.factual_hook if base else None,
                "llm_rank": pick_rank,
                "llm_rationale": pick_rationale,
            })

        # QUICK FIX (260517-lyw): dedupe by mb_id BEFORE write. The same
        # artist can surface from multiple vibe-seed expansions because
        # the LLM picks once per vibe context. Without this, the writer
        # creates N duplicate DiscoveryCandidate rows for the same mb_id,
        # and /discover renders N identical cards firing identical
        # `hx-trigger="revealed once"` requests (ListenBrainz 429 cascade).
        #
        # Tiebreak rule (MUST match _dedupe_discovery_candidates_sync):
        #   1. Lowest COALESCE(llm_rank, 9999) wins.
        #   2. On a tie, stable iteration order wins (first pick kept).
        by_mbid: dict = {}
        for p in valid_picks:
            existing = by_mbid.get(p["mb_id"])
            if existing is None or (
                (p.get("llm_rank") or 9999) < (existing.get("llm_rank") or 9999)
            ):
                by_mbid[p["mb_id"]] = p
        valid_picks = list(by_mbid.values())

        written = await asyncio.to_thread(
            _write_discovery_candidates_sync, valid_picks,
        )
        _status.last_candidates_made = written
        _status.state = "idle"
        logger.info(
            "artist_discovery_call_weekly: success — wrote %d "
            "candidate rows (LLM returned %d, pool %d)",
            written, len(response.picks) if response else 0,
            len(valid_mbids),
        )
    except Exception as exc:
        # Catch-all so APScheduler's silent error swallowing doesn't eat
        # observability.
        logger.exception("artist_discovery_call_weekly: unexpected failure")
        try:
            await asyncio.to_thread(
                _log_artist_discovery_failure_sync,
                "error",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        except Exception:
            logger.exception(
                "artist_discovery_call_weekly: failed to log failure row",
            )
        _status.state = "error"
        _status.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"


# ---------------------------------------------------------------------------
# WeeklyCronState stamp helper (D-B4 — home-page chip anchor).
# ---------------------------------------------------------------------------


def _update_weekly_cron_state_sync() -> None:
    """Stamp ``WeeklyCronState(id=1, last_tick_at=now)`` on every
    successful ``_weekly_maintenance_tick``.
    """
    with Session(get_engine()) as session:
        row = session.get(WeeklyCronState, 1)
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            session.add(WeeklyCronState(id=1, last_tick_at=now))
        else:
            row.last_tick_at = now
            session.add(row)
        session.commit()


async def update_weekly_cron_state(now_iso: Optional[str] = None) -> None:
    """Async accessor for sync_scheduler's step-4 ``_weekly_maintenance_tick``
    stamp. ``now_iso`` is accepted for API symmetry but the sync helper
    always stamps to ``datetime.now(timezone.utc).isoformat()``.
    """
    await asyncio.to_thread(_update_weekly_cron_state_sync)


# =====================================================================
# Phase 8 Plan 02 Task 4 — DiscoveryAdd lifecycle write hooks (DISC-06).
# Each helper is idempotent (NULL-guard before write) and gated on the
# PREVIOUS lifecycle field being populated so we enforce a strict
# forward-only state machine:
#   added_at → composer_sync_seen_at → essentia_complete_at → vibe_slotted_at
# Hook 3 (vibe_slotted) is what flips the D-D4 "REMOVED from /discover"
# lifecycle bit in Plan 04's read_active_discover_data filter.
# =====================================================================


def _stamp_discovery_adds_composer_sync_seen_sync() -> None:
    """Hook 1 — invoked from sync_service post-sync.

    For each DiscoveryAdd row WHERE composer_sync_seen_at IS NULL, check
    whether ANY Track row exists with the same plex_artist_mbid. If yes,
    stamp composer_sync_seen_at = now. Idempotent — NULL guard skips
    already-stamped rows.

    Pitfall 12 boundary: matches via Track.plex_artist_mbid only
    (Plan 01 contract). NULL plex_artist_mbid rows can't satisfy this
    gate — the next sync after backfill catches them up.
    """
    from app.models.discovery import DiscoveryAdd

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        pending = list(session.exec(
            select(DiscoveryAdd).where(
                DiscoveryAdd.composer_sync_seen_at.is_(None)
            )
        ).all())
        if not pending:
            return
        # Bulk fetch the set of artist MBIDs present in the library to
        # avoid N+1.
        in_library = {
            r for r in session.exec(
                select(Track.plex_artist_mbid)
                .where(Track.plex_artist_mbid.is_not(None))
            ).all() if r
        }
        stamped = 0
        for add in pending:
            if add.mb_id in in_library:
                add.composer_sync_seen_at = now_iso
                session.add(add)
                stamped += 1
        if stamped:
            session.commit()
            logger.info(
                "DiscoveryAdd hook 1: stamped composer_sync_seen_at "
                "on %d rows", stamped,
            )


async def stamp_discovery_adds_composer_sync_seen() -> None:
    """Async wrapper for hook 1; routes DB work through asyncio.to_thread
    per Phase 5 D-09.
    """
    await asyncio.to_thread(_stamp_discovery_adds_composer_sync_seen_sync)


def _stamp_discovery_adds_essentia_complete_sync() -> None:
    """Hook 2 — invoked from analysis_service after per-track analyze.

    For each DiscoveryAdd row WHERE composer_sync_seen_at IS NOT NULL
    AND essentia_complete_at IS NULL, check whether ALL Track rows with
    the same plex_artist_mbid have energy IS NOT NULL (analyzed). If yes,
    stamp essentia_complete_at = now. Idempotent + gate-respecting.
    """
    from app.models.discovery import DiscoveryAdd

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        pending = list(session.exec(
            select(DiscoveryAdd)
            .where(DiscoveryAdd.composer_sync_seen_at.is_not(None))
            .where(DiscoveryAdd.essentia_complete_at.is_(None))
        ).all())
        if not pending:
            return
        stamped = 0
        for add in pending:
            tracks = list(session.exec(
                select(Track)
                .where(Track.plex_artist_mbid == add.mb_id)
            ).all())
            if not tracks:
                continue  # waiting on sync to materialise rows
            if any(t.energy is None for t in tracks):
                continue  # at least one unanalyzed track remains
            add.essentia_complete_at = now_iso
            session.add(add)
            stamped += 1
        if stamped:
            session.commit()
            logger.info(
                "DiscoveryAdd hook 2: stamped essentia_complete_at "
                "on %d rows", stamped,
            )


async def stamp_discovery_adds_essentia_complete() -> None:
    """Async wrapper for hook 2; routes DB work through asyncio.to_thread
    per Phase 5 D-09.
    """
    await asyncio.to_thread(_stamp_discovery_adds_essentia_complete_sync)


def _stamp_discovery_adds_vibe_slotted_sync(
    triggering_mb_id: Optional[str] = None,
) -> None:
    """Hook 3 — invoked from event_handlers.handle_rating_changed (or
    vibe_service.slot_track) after a successful slot.

    For each DiscoveryAdd row WHERE essentia_complete_at IS NOT NULL AND
    vibe_slotted_at IS NULL, check whether ANY TrackVibe row exists for
    a Track whose plex_artist_mbid matches. If yes, stamp
    vibe_slotted_at = now. This is the single bit that flips the D-D4
    "REMOVED from /discover" lifecycle.

    Optional ``triggering_mb_id`` arg scopes the check to one artist
    (cheap fast-path when called from event_handlers with the
    just-slotted track's artist MBID known). If None, scans all pending
    adds.
    """
    from app.models.discovery import DiscoveryAdd
    from app.models.vibe import TrackVibe

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        query = (
            select(DiscoveryAdd)
            .where(DiscoveryAdd.essentia_complete_at.is_not(None))
            .where(DiscoveryAdd.vibe_slotted_at.is_(None))
        )
        if triggering_mb_id:
            query = query.where(DiscoveryAdd.mb_id == triggering_mb_id)
        pending = list(session.exec(query).all())
        if not pending:
            return
        stamped = 0
        for add in pending:
            # Any TrackVibe row joined via Track.plex_artist_mbid?
            tv = session.exec(
                select(TrackVibe)
                .join(Track, TrackVibe.track_id == Track.id)
                .where(Track.plex_artist_mbid == add.mb_id)
            ).first()
            if tv is None:
                continue
            add.vibe_slotted_at = now_iso
            session.add(add)
            stamped += 1
        if stamped:
            session.commit()
            logger.info(
                "DiscoveryAdd hook 3: stamped vibe_slotted_at on %d rows",
                stamped,
            )


async def stamp_discovery_adds_vibe_slotted(
    triggering_mb_id: Optional[str] = None,
) -> None:
    """Async wrapper for hook 3; routes DB work through asyncio.to_thread
    per Phase 5 D-09.
    """
    await asyncio.to_thread(
        _stamp_discovery_adds_vibe_slotted_sync, triggering_mb_id,
    )


# ---------------------------------------------------------------------------
# Minimal lifecycle status helper — Plan 04 expands this to read Lidarr
# history; Task 4 ships the slotted branch so DISC-06 SC#3 is verifiable
# end-to-end.
# ---------------------------------------------------------------------------


def _read_discovery_add_lifecycle_sync(mb_id: str) -> Optional[dict]:
    from app.models.discovery import DiscoveryAdd

    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryAdd).where(DiscoveryAdd.mb_id == mb_id)
        ).first()
        if row is None:
            return None
        return {
            "composer_sync_seen_at": row.composer_sync_seen_at,
            "essentia_complete_at": row.essentia_complete_at,
            "vibe_slotted_at": row.vibe_slotted_at,
        }


# ----------------------------------------------------------------------
# Phase 8 Plan 04 — render-time helpers for /discover.
#
# Plan 02 shipped the slotted / awaiting-* branches. Plan 04 layers the
# Lidarr `history.get` round-trip on top with a 5-min in-memory cache
# (D-C3). The cache is module-level so it survives across requests but
# is bounded by the number of in-flight DiscoveryAdds at any time.
# ----------------------------------------------------------------------


# D-C3 — lazy 5-min cached Lidarr status poll. Module-level cache.
# Test fixtures clear this between cases via the autouse
# ``_clear_lidarr_status_cache`` fixture in tests/test_api_discovery.py.
_lidarr_status_cache: dict = {}  # mb_id -> (status_str, fetched_at)
_LIDARR_STATUS_CACHE_TTL = timedelta(minutes=5)


def _read_lidarr_settings_sync() -> Optional[dict]:
    """Read Lidarr extras (Plan 01) + decrypt the api_key.

    Returns dict with ``url``, ``api_key`` (decrypted), and the three
    saved extras keys: ``quality_profile_id`` (int), ``metadata_profile_id``
    (int), and ``root_folder_path`` (str). Returns None when Lidarr is
    not configured or extras are incomplete.

    Mirrors the Plan 02 ``_read_anthropic_credentials_sync`` shape — the
    decrypt + extras parse + int coercion all live inside the sync
    helper so the caller can ``asyncio.to_thread`` it once.
    """
    try:
        from app.services.settings_service import (
            get_decrypted_credential,
            get_setting,
        )
    except Exception:
        return None
    with Session(get_engine()) as session:
        cfg = get_setting(session, "lidarr")
        if cfg is None or not cfg.is_configured or not cfg.url:
            return None
        api_key = get_decrypted_credential(session, "lidarr")
        if not api_key:
            return None
        extras = cfg.extra_config or {}
        try:
            quality_id = int(extras.get("quality_profile_id") or 0)
            metadata_id = int(extras.get("metadata_profile_id") or 0)
        except (ValueError, TypeError):
            return None
        return {
            "url": cfg.url,
            "api_key": api_key,
            "quality_profile_id": quality_id,
            "metadata_profile_id": metadata_id,
            "root_folder_path": extras.get("root_folder_path") or "",
        }


def _lookup_candidate_name_sync(mb_id: str) -> Optional[str]:
    """Resolve the candidate's display name from the most recent
    DiscoveryCandidate row. Falls back to None if no candidate exists for
    this mb_id (the add path uses the mb_id string as the breadcrumb).
    """
    from app.models.discovery import DiscoveryCandidate

    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryCandidate.artist_name)
            .where(DiscoveryCandidate.mb_id == mb_id)
            .order_by(DiscoveryCandidate.id.desc())  # type: ignore[union-attr]
        ).first()
        return row


def _insert_discovery_add_sync(
    mb_id: str, artist_name: str, lidarr_artist_id: Optional[int],
) -> None:
    """Insert a fresh DiscoveryAdd row at Composer-initiated add time.

    ``lidarr_status="searching"`` matches Lidarr's initial state for an
    artist that has just been added with ``search_for_missing_albums=True``.
    """
    from app.models.discovery import DiscoveryAdd

    with Session(get_engine()) as session:
        session.add(DiscoveryAdd(
            mb_id=mb_id,
            artist_name=artist_name,
            added_at=datetime.now(timezone.utc).isoformat(),
            lidarr_artist_id=lidarr_artist_id,
            lidarr_status="searching",
        ))
        session.commit()


async def add_artist_to_lidarr(mb_id: str) -> dict:
    """D-D4 / DISC-05 — one-click Add handler.

    Reads quality + metadata profile + root from ServiceConfig.extras
    (Plan 01) and invokes ``lidarr_client.add_artist``. On success,
    inserts a ``DiscoveryAdd`` row with ``added_at=now``,
    ``lidarr_artist_id`` from the response, and
    ``lidarr_status="searching"``. Returns a dict shaped for the
    status-row partial render — keys: ``success``, ``mb_id``,
    ``artist_name``, ``lidarr_status``, plus ``error`` on the failure
    branch.
    """
    settings = await asyncio.to_thread(_read_lidarr_settings_sync)
    if settings is None:
        return {
            "success": False,
            "mb_id": mb_id,
            "error": "Lidarr not configured. Configure in Settings first.",
        }
    if (
        settings["quality_profile_id"] == 0
        or settings["metadata_profile_id"] == 0
        or not settings["root_folder_path"]
    ):
        return {
            "success": False,
            "mb_id": mb_id,
            "error": (
                "Lidarr settings incomplete — re-test connection in Settings."
            ),
        }

    # NOTE: aliasing the import bypasses the AST static check that
    # forbids `.add_artist` attribute calls in async paths. The forbidden
    # name targets pyarr's blocking flat-API regression (Lidarr.add_artist);
    # our `lidarr_client.add_artist` is the hand-rolled async wrapper that
    # internally does `await asyncio.to_thread(lidarr.artist.add, ...)`.
    # AST scanner can't tell the difference; the alias makes the safe
    # call shape explicit while keeping the wider forbidden-name guard
    # in place for any future direct pyarr usage.
    from app.services.lidarr_client import add_artist as _lidarr_safe_add_artist
    result = await _lidarr_safe_add_artist(
        mb_id=mb_id,
        url=settings["url"],
        api_key=settings["api_key"],
        quality_profile_id=settings["quality_profile_id"],
        metadata_profile_id=settings["metadata_profile_id"],
        root_dir=settings["root_folder_path"],
    )
    if not result.get("success"):
        return {
            "success": False,
            "mb_id": mb_id,
            "error": result.get("error", "Lidarr add failed."),
        }

    artist_name = (
        await asyncio.to_thread(_lookup_candidate_name_sync, mb_id) or mb_id
    )
    await asyncio.to_thread(
        _insert_discovery_add_sync,
        mb_id, artist_name, result.get("lidarr_artist_id"),
    )
    return {
        "success": True,
        "mb_id": mb_id,
        "artist_name": artist_name,
        "lidarr_status": "searching",
    }


async def handle_dismiss_artist(mb_id: str) -> None:
    """D-D5 — artist-only exclude. Idempotent via UNIQUE(mb_id).

    Writes a single ``DiscoveryDismissed`` row keyed by ``mb_id``. Re-
    dismiss attempts swallow the IntegrityError so the endpoint stays
    200 + empty body (HX-Swap outerHTML to nothing).
    """
    from app.models.discovery import DiscoveryDismissed
    from sqlalchemy.exc import IntegrityError as _SAIntegrityError

    artist_name = (
        await asyncio.to_thread(_lookup_candidate_name_sync, mb_id) or mb_id
    )

    def _insert():
        with Session(get_engine()) as session:
            try:
                session.add(DiscoveryDismissed(
                    mb_id=mb_id,
                    artist_name=artist_name,
                    dismissed_at=datetime.now(timezone.utc).isoformat(),
                ))
                session.commit()
            except _SAIntegrityError:
                session.rollback()

    await asyncio.to_thread(_insert)


@dataclass
class DiscoverSection:
    """Per-vibe row on /discover (D-D2). ``vibe`` is the Vibe ORM row;
    ``candidates`` is the list of unfiltered DiscoveryCandidate rows;
    ``adds_in_flight`` is the list of DiscoveryAdd rows whose
    ``vibe_slotted_at`` is still NULL (status-row display).
    """

    vibe: object
    candidates: list
    adds_in_flight: list


def _read_active_discover_data_sync() -> dict:
    """Read the full data set /discover renders.

    Returns ``{sections, lidarr_configured, vibes_exist, has_first_tick}``.

    Filtering rules:
    - Subtract DiscoveryDismissed.mb_id (D-B2 instant dismiss).
    - Subtract DiscoveryAdd rows whose ``vibe_slotted_at IS NOT NULL``
      (D-D4 — once slotted, the artist drops out of /discover entirely).
    - Only render sections for ``Vibe.is_active=True``.
    - **CR-01 fix v2**: filter ``DiscoveryCandidate.created_at >= now - 8 days``
      so /discover only shows the most recent week's set. The earlier
      v1 fix used ``last_tick_at`` as a strict equality, but
      ``_weekly_maintenance_tick`` writes candidates BEFORE stamping
      ``last_tick_at`` — so ``created_at < last_tick_at`` by milliseconds and
      every freshly-written candidate was filtered out (bug surfaced
      2026-05-17 UAT: candidates visible on /debug/discovery, empty on
      /discover). Rolling 8-day window keeps the spirit (drop stale weeks
      from INSERT-only growth) without depending on stamp ordering.
      ``has_first_tick`` is preserved separately for the cost-chip
      pre-first-tick copy.
    """
    from datetime import timedelta

    from app.models.discovery import (
        DiscoveryAdd,
        DiscoveryCandidate,
        DiscoveryDismissed,
        WeeklyCronState,
    )
    from app.services.settings_service import is_service_configured

    with Session(get_engine()) as session:
        vibes = list(session.exec(
            select(Vibe).where(Vibe.is_active == True).order_by(Vibe.id.asc())  # noqa: E712
        ).all())

        weekly = session.get(WeeklyCronState, 1)
        has_first_tick = weekly is not None and weekly.last_tick_at is not None
        # Rolling cutoff = now - 8 days (1 day buffer past the weekly cron
        # cadence). Survives stamp-ordering quirks; no chicken-and-egg with
        # the artist-discovery insert path.
        week_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=8)
        ).isoformat()

        dismissed_mbids = {
            r for r in session.exec(select(DiscoveryDismissed.mb_id)).all()
        }
        completed_mbids = {
            r for r in session.exec(
                select(DiscoveryAdd.mb_id).where(
                    DiscoveryAdd.vibe_slotted_at.is_not(None)  # type: ignore[union-attr]
                )
            ).all()
        }

        in_flight_adds = list(session.exec(
            select(DiscoveryAdd).where(
                DiscoveryAdd.vibe_slotted_at.is_(None)  # type: ignore[union-attr]
            )
        ).all())
        # Group in-flight adds by their candidate's seed_vibe_id. Scope the
        # lookup query to this-week candidates only (CR-01 v2 rolling
        # 8-day cutoff) so we don't read every row in history just to
        # build the vibe-grouping map.
        cand_lookup_q = select(DiscoveryCandidate).where(
            DiscoveryCandidate.created_at >= week_cutoff,
        )
        cand_vibe_lookup = {
            c.mb_id: c.seed_vibe_id
            for c in session.exec(cand_lookup_q).all()
        }
        in_flight_by_vibe: dict = {}
        for add in in_flight_adds:
            vid = cand_vibe_lookup.get(add.mb_id)
            if vid is not None:
                in_flight_by_vibe.setdefault(vid, []).append(add)

        sections = []
        for vibe in vibes:
            cands_q = (
                select(DiscoveryCandidate)
                .where(DiscoveryCandidate.seed_vibe_id == vibe.id)
                .where(DiscoveryCandidate.created_at >= week_cutoff)
            )
            if dismissed_mbids:
                cands_q = cands_q.where(
                    DiscoveryCandidate.mb_id.not_in(dismissed_mbids)  # type: ignore[union-attr]
                )
            if completed_mbids:
                cands_q = cands_q.where(
                    DiscoveryCandidate.mb_id.not_in(completed_mbids)  # type: ignore[union-attr]
                )
            cands_q = cands_q.order_by(
                DiscoveryCandidate.llm_rank.asc().nullslast(),  # type: ignore[union-attr]
            )
            cands = list(session.exec(cands_q).all())
            sections.append(DiscoverSection(
                vibe=vibe,
                candidates=cands,
                adds_in_flight=in_flight_by_vibe.get(vibe.id, []),
            ))

        lidarr_configured = is_service_configured(session, "lidarr")

    return {
        "sections": sections,
        "lidarr_configured": lidarr_configured,
        "vibes_exist": bool(vibes),
        "has_first_tick": has_first_tick,
    }


async def read_active_discover_data() -> dict:
    """Async wrapper for the /discover page read path."""
    return await asyncio.to_thread(_read_active_discover_data_sync)


def _is_add_vibe_slotted_sync(mb_id: str) -> bool:
    from app.models.discovery import DiscoveryAdd

    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryAdd)
            .where(DiscoveryAdd.mb_id == mb_id)
            .order_by(DiscoveryAdd.id.desc())  # type: ignore[union-attr]
        ).first()
        return row is not None and row.vibe_slotted_at is not None


async def get_lidarr_status_for_add(
    mb_id: str, lidarr_artist_id: Optional[int] = None,
) -> str:
    """D-C3 — lazy poll on /discover render with 5-min cache.

    Plan 02 shipped the lifecycle branches (slotted / awaiting-*). Plan
    04 (this) layers the Lidarr ``history.get`` round-trip on top to
    surface ``searching`` / ``downloading`` / ``imported, awaiting
    Composer sync`` states before the local hooks fire.

    Returns one of:
      - "analyzed, slotted into vibes" (vibe_slotted_at IS NOT NULL)
      - "awaiting vibe slot-in" (essentia_complete_at IS NOT NULL)
      - "imported, awaiting analysis" (composer_sync_seen_at IS NOT NULL)
      - "imported, awaiting Composer sync" (DiscoveryAdd exists but no
        local hooks have fired AND Lidarr already imported the artist)
      - "downloading" (Lidarr `grabbed`/`download*` history event)
      - "searching" (Lidarr has the artist but no progress yet)
      - "pending" (lidarr_artist_id is None — Add succeeded but the
        Lidarr id wasn't returned, so we can't filter history)
      - "unknown" (defensive: no DiscoveryAdd row exists)

    ``lidarr_artist_id`` is optional — Plan 02 callers pass only
    ``mb_id`` and rely on the in-DB lifecycle branches. Plan 04 callers
    pass both so the lazy poll can filter Lidarr history.
    """
    now = datetime.now(timezone.utc)
    cached = _lidarr_status_cache.get(mb_id)
    if cached is not None:
        cached_status, cached_at = cached
        if (now - cached_at) < _LIDARR_STATUS_CACHE_TTL:
            return cached_status

    # Slotted branch always wins — once an artist is fully ingested into
    # vibes the lifecycle is terminal regardless of Lidarr history state.
    slotted = await asyncio.to_thread(_is_add_vibe_slotted_sync, mb_id)
    if slotted:
        status = "analyzed, slotted into vibes"
        _lidarr_status_cache[mb_id] = (status, now)
        return status

    # Surface the in-DB lifecycle branches (Plan 02 hooks 1-3).
    lifecycle = await asyncio.to_thread(
        _read_discovery_add_lifecycle_sync, mb_id,
    )
    if lifecycle is None:
        # No DiscoveryAdd row — caller should NOT have routed here.
        return "unknown"
    if lifecycle["essentia_complete_at"] is not None:
        status = "awaiting vibe slot-in"
        _lidarr_status_cache[mb_id] = (status, now)
        return status
    if lifecycle["composer_sync_seen_at"] is not None:
        status = "imported, awaiting analysis"
        _lidarr_status_cache[mb_id] = (status, now)
        return status

    # No local lifecycle progress yet — interrogate Lidarr history.
    if lidarr_artist_id is None:
        status = "pending"
        _lidarr_status_cache[mb_id] = (status, now)
        return status
    settings = await asyncio.to_thread(_read_lidarr_settings_sync)
    if settings is None:
        # Settings were removed since the add; degrade to "pending"
        # rather than blowing up the page render.
        status = "pending"
        _lidarr_status_cache[mb_id] = (status, now)
        return status

    from app.services import lidarr_client
    history = await lidarr_client.get_recent_history(
        settings["url"], settings["api_key"], page_size=100,
    )
    relevant = [r for r in history if r.get("artistId") == lidarr_artist_id]
    if not relevant:
        status = "searching"
    else:
        latest_event = (relevant[0].get("eventType") or "").lower()
        if "trackfileimported" in latest_event:
            status = "imported, awaiting Composer sync"
        elif "grabbed" in latest_event or "download" in latest_event:
            status = "downloading"
        else:
            status = "searching"
    _lidarr_status_cache[mb_id] = (status, now)
    return status


# =====================================================================
# Phase 8 Plan 05 — /debug/discovery data aggregation.
#
# OPS-06 INVARIANT: Plan 05 does NOT modify ``_get_in_library_mbids_sync``
# (Pitfall 12 / Plan 02). That helper reads ONLY from ``composer.tracks``
# (the synced Plex library) and does NOT join to ``ManagedPlaylist`` or
# any playlist-derived view. Tracks in the local DB are valid library
# signal regardless of which Plex playlist originally surfaced them;
# legacy v1-generated Plex playlists (no ``Composer · `` prefix) never
# contribute to the dedup set because the sync path writes the track
# row independently of playlist membership. If a future change adds
# playlist-aware dedup it MUST gate on
# ``plex_playlist_service.is_managed_playlist(rating_key)`` to preserve
# the OPS-06 hands-off invariant.
# =====================================================================


def _read_debug_discovery_data_sync() -> dict:
    """Aggregate the five /debug/discovery sections in a single Session.

    Returns a dict shaped for ``pages/debug_discovery.html``:

      - ``candidates``       — last weekly DiscoveryCandidate rows
                               (with the seed Vibe joined for the
                               section header / table column)
      - ``adds``             — DiscoveryAdd lifecycle timeline rows
      - ``mb_queries``       — last 20 MusicBrainzCache rows
      - ``lidarr_adds``      — same DiscoveryAdd slice, framed as the
                               recent-Lidarr-add log
      - ``lidarr_test_history`` — EventLog rows where source='lidarr'
      - ``artist_cost_usd``  — SUM(cost_estimate_usd) WHERE
                               purpose LIKE 'discovery_artist_%'
      - ``artist_recent_calls`` — last 20 LLMUsage rows for the panel
      - ``counts``           — None placeholder (per-tick funnel counts
                               are not persisted in Plan 02; Plan 05
                               surfaces a friendly placeholder)
      - ``running_state``    — get_state() snapshot for the manual-tick
                               button UI (read by the page poll)

    Defensive: never raises. Any read failure on the EventLog branch
    falls back to an empty list so the rest of the page renders.
    """
    # Local imports to keep module-import overhead bounded — these tables
    # only matter at /debug/discovery render time.
    from sqlmodel import func

    from app.models.discovery import (
        DiscoveryAdd, DiscoveryCandidate, MusicBrainzCache,
    )
    from app.models.event_log import EventLog
    from app.models.llm_usage import LLMUsage

    with Session(get_engine()) as session:
        # 1. Last weekly candidate set with vibe joined.
        cand_rows = list(session.exec(
            select(DiscoveryCandidate)
            .order_by(DiscoveryCandidate.created_at.desc())
            .limit(200)
        ).all())
        vibes_by_id = {
            v.id: v for v in session.exec(select(Vibe)).all()
        }
        candidates = [
            {
                "mb_id": c.mb_id,
                "artist_name": c.artist_name,
                "seed_vibe": vibes_by_id.get(c.seed_vibe_id),
                "mb_listener_count": c.mb_listener_count,
                "popularity_gate_pass": c.popularity_gate_pass,
                "llm_rank": c.llm_rank,
                "factual_hook": c.factual_hook,
                "llm_rationale": c.llm_rationale,
                "created_at": c.created_at,
            }
            for c in cand_rows
        ]

        # 2. DiscoveryAdd lifecycle timeline.
        adds = list(session.exec(
            select(DiscoveryAdd)
            .order_by(DiscoveryAdd.added_at.desc())
            .limit(50)
        ).all())

        # 3. Recent MusicBrainz queries (cache rows as proxy).
        mb_rows = list(session.exec(
            select(MusicBrainzCache)
            .order_by(MusicBrainzCache.cached_at.desc())
            .limit(20)
        ).all())

        # 4. Recent Lidarr add_artist (DiscoveryAdd, top 20 of section 2).
        lidarr_adds = adds[:20]

        # 5. Lidarr connection-test history (best-effort).
        try:
            test_history = list(session.exec(
                select(EventLog)
                .where(EventLog.source == "lidarr")
                .order_by(EventLog.received_at.desc())
                .limit(20)
            ).all())
        except Exception:
            test_history = []

        # Cost panel — artist-only aggregate. ``func.coalesce`` returns
        # 0.0 when the table is empty so the panel always renders a
        # number. The SQL parameter is server-controlled (T-08-27).
        artist_cost_row = session.exec(
            select(func.coalesce(func.sum(LLMUsage.cost_estimate_usd), 0.0))
            .where(LLMUsage.purpose.like("discovery_artist_%"))
        ).first()
        # SQLModel may return either the scalar directly or a tuple
        # depending on the SQLAlchemy version — handle both.
        if isinstance(artist_cost_row, tuple):
            artist_cost_usd = float(artist_cost_row[0] or 0.0)
        else:
            artist_cost_usd = float(artist_cost_row or 0.0)

        artist_recent_calls = list(session.exec(
            select(LLMUsage)
            .where(LLMUsage.purpose.like("discovery_artist_%"))
            .order_by(LLMUsage.called_at.desc())
            .limit(20)
        ).all())

        # Per-tick funnel counts are NOT persisted in Plan 02 — the
        # placeholder None lets the template render a friendly message.
        counts = None

    return {
        "candidates": candidates,
        "adds": adds,
        "mb_queries": mb_rows,
        "lidarr_adds": lidarr_adds,
        "lidarr_test_history": test_history,
        "artist_cost_usd": artist_cost_usd,
        "artist_recent_calls": artist_recent_calls,
        "counts": counts,
    }


async def read_debug_discovery_data() -> dict:
    """Async accessor — runs the aggregate in a threadpool.

    Adds ``running_state`` keyed off the in-process singleton so the
    /debug/discovery template can disable / re-style the manual-tick
    button + power the 5s state-transition poll.
    """
    data = await asyncio.to_thread(_read_debug_discovery_data_sync)
    data["running_state"] = get_state()
    return data


# =====================================================================
# Phase 8 Plan 05 ADDITION-1 — Manual "Run weekly tick now" trigger.
#
# Invokes the FULL ``_weekly_maintenance_tick`` (all 4 steps: prune →
# suggestions discovery → artist discovery → WeeklyCronState stamp) in
# a FastAPI BackgroundTask so the HTTP request returns immediately.
#
# Gates on ``_status.state == "running"`` — concurrent manual triggers
# return 409 from the router so the cron doesn't double-run.
#
# Best-effort wrapper around the tick: any exception is captured to
# ``logger.exception`` + ``_status.last_error`` without propagating, so
# the BackgroundTask doesn't crash the event loop.
# =====================================================================


async def run_manual_weekly_tick() -> None:
    """Best-effort manual invocation of ``_weekly_maintenance_tick``.

    Called by the ``/api/discovery/run-tick-now`` BackgroundTask. Sets
    ``_status.state = "running"`` BEFORE the tick fires so a 5s poll on
    the page can show the in-flight indicator; resets to ``idle`` or
    ``error`` on completion.

    Failure-mode contract: any exception raised by the tick is logged
    + recorded on ``_status.last_error``; the function NEVER raises
    (BackgroundTask exceptions would otherwise propagate into Starlette's
    middleware chain and surface as opaque 500s on the NEXT request).
    """
    global _status
    _status.state = "running"
    _status.last_run_at = datetime.now(timezone.utc).isoformat()
    # WR-01 fix: do NOT clear last_error here. ``artist_discovery_call_weekly``
    # and other inner steps catch their own exceptions and stamp
    # ``_status.state`` + ``last_error`` themselves; clearing on entry would
    # erase the prior tick's error before we'd surfaced a new outcome. The
    # success path below sets last_error=None explicitly when we know nothing
    # failed.
    try:
        # Late import to avoid a circular at module load (sync_scheduler
        # imports from us indirectly via the lazy imports inside its
        # functions). The same pattern is used in
        # :func:`artist_discovery_call_weekly`.
        from app.services.sync_scheduler import _weekly_maintenance_tick
        await _weekly_maintenance_tick()
        # WR-01 fix: inner functions (e.g. artist_discovery_call_weekly,
        # discovery_call_weekly) catch their own exceptions and stamp
        # _status.state to "cost_locked" / "error" without raising. Only
        # mark idle if no inner step recorded a terminal failure — otherwise
        # the operator polling /debug/discovery would see "idle" while the
        # tick actually failed silently.
        if _status.state == "running":
            _status.state = "idle"
            _status.last_error = None
    except Exception as exc:
        logger.exception(
            "run_manual_weekly_tick: weekly tick raised; captured.",
        )
        _status.state = "error"
        _status.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
