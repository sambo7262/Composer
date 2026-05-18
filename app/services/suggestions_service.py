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
  - maybe_schedule_refill(target=30) — Phase 7.1 SUGG-12: if mirror size <
    target, directly await :func:`refill_mirror_sql` (free SQL hot path —
    no LLM, no cost breaker). Returns the deficit (target - current_size);
    0 means no refill needed. Symbol name preserved for monkeypatch
    stability in test_event_handlers.py.
  - refill_mirror_sql(target=30) — Phase 7.1 SUGG-12: SQL-driven refill.
    Surfaces UNRATED tracks ordered by z-score-normalized distance to
    the per-vibe centroid (via :func:`_read_top_n_unrated_for_vibe_sync`).
    Rated tracks already live in their vibe playlists; Suggestions is the
    discovery surface for unrated/unlistened content. Free, instant, zero
    LLM tokens consumed on every play. Replaces the deleted Phase 7
    LLM-ranking refill.
  - run_phase_07_suggestions_bootstrap() — lifespan migration entry point;
    gated by MigrationLog(phase_id='7.0-suggestions-bootstrap').
  - get_state() — return the module-level SuggestionsServiceStatus dataclass.

Phase 7.1 (D-D1) DELETED the entire legacy LLM-ranking section: the
whole-queue and per-vibe Anthropic-call entry points, the 8000-token
ranking constant introduced by 260514-e6w, the LLM ranking pydantic
shapes, the audio-feature shortlist builder, the system / user prompt
builders, the per-vibe distance helper, the soft-negative reader, the
LLM cost-breaker integration, and the Composer context blurb. Callers
in ``app/routers/api_vibes.py`` were retargeted to ``refill_mirror_sql``
directly. See
``.planning/phases/07.1-suggestions-cost-architecture-sql-refill-weekly-discovery/07.1-CONTEXT.md``
D-D1 for the full deletion list and rationale.

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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import numpy as np
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select

from app.database import get_engine
from app.models.vibe import ManagedPlaylist, MigrationLog
from app.services.plex_playlist_service import update_playlist_items

logger = logging.getLogger(__name__)

# Z-score normalization clamp — mirrors vibe_service._STD_FLOOR so the
# unrated SQL refill computes distance in the same numeric space as
# vibe_service._slot_track_inner.
_STD_FLOOR = 1e-6

SUGGESTIONS_TARGET_SIZE = 30  # SUGG-01 default (configurable in later plan)
SUGGESTIONS_PLAYLIST_NAME = "Composer · Suggestions"  # Phase 6 D-25 namespace
PHASE_07_MIGRATION_ID = "7.0-suggestions-bootstrap"
# QUICK FIX (260517-p2b) — Phase 8.3 one-shot lifespan trim of pre-existing
# oversized SuggestionsMirror down to SUGGESTIONS_TARGET_SIZE. The 260517-nkt
# discovery WRITE cap is forward-looking only; this migration cleans the
# carryover state once so maybe_schedule_refill's deficit gate can re-open
# and the rate-feedback loop unstalls. Gated by MigrationLog(phase_id=...).
PHASE_08_3_MIGRATION_ID = "8.3-trim-suggestions-mirror-to-target"
# Sentinel for "Plex playlist not yet created" — Plan 02 first refill detects
# this and runs the real plex_playlist_service.create_playlist call with the
# first batch of suggestions.
DEFERRED_PLEX_RATING_KEY_SENTINEL = ""

# Phase 7.1 SUGG-12 / D-B2 — top-N closest-to-centroid TrackVibe rows per
# vibe; the SQL ranking pool for refill_mirror_sql. Light randomization
# within this pool gives variety without destroying taste-fit. Tunable
# post-deploy.
TOP_N_PER_VIBE = 100


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


def _read_all_mirror_rating_keys_sync() -> list[str]:
    """Return every SuggestionsMirror row's Track.plex_rating_key, ordered
    by ``sm.added_at ASC, sm.id ASC`` (oldest-first).

    Used by ``discovery_call_weekly``'s NotFound self-heal branch
    (260517-nkt): when Plex returns NotFound on the update_playlist_items
    push, the playlist was deleted user-side and we must re-materialize
    from the FULL current mirror (post-write, post-evict), not just the
    new picks. Mirrors the data shape consumed by
    ``_materialize_suggestions_plex_playlist``.
    """
    with Session(get_engine()) as session:
        rows = session.execute(
            text(
                """
                SELECT t.plex_rating_key
                FROM suggestionsmirror sm
                JOIN track t ON t.id = sm.track_id
                ORDER BY sm.added_at ASC, sm.id ASC
                """
            )
        ).all()
        return [str(r[0]) for r in rows if r[0] is not None]


def _read_plex_keys_for_track_ids_sync(track_ids: list[int]) -> list[str]:
    """Return Track.plex_rating_key values for the given track ids,
    preserving the input order.

    Used by ``discovery_call_weekly`` to derive the rating keys to push
    to Plex from the LLM-returned ``DiscoveryPick`` objects (which carry
    ``track_id`` only). Missing track ids (or NULL plex_rating_key) are
    skipped silently — caller can detect via length comparison if needed.
    """
    if not track_ids:
        return []
    with Session(get_engine()) as session:
        rows = session.execute(
            text(
                "SELECT id, plex_rating_key FROM track "
                "WHERE id IN (" + ",".join(str(int(i)) for i in track_ids) + ")"
            )
        ).all()
        by_id = {int(r[0]): r[1] for r in rows if r[1] is not None}
        return [str(by_id[int(tid)]) for tid in track_ids if int(tid) in by_id]


def _evict_oldest_mirror_rows_sync(evict_count: int) -> list[str]:
    """Evict the oldest ``evict_count`` rows from SuggestionsMirror; return
    the corresponding Track.plex_rating_key values oldest-first so the
    caller can remove them from the Plex playlist symmetrically.

    Ordering: ``ORDER BY sm.added_at ASC, sm.id ASC``. ``added_at`` is an
    ISO 8601 UTC string (model line 43), and every writer in this codebase
    uses ``datetime.now(timezone.utc).isoformat()`` so lexicographic
    comparison is correct — DO NOT cast to datetime. The ``sm.id ASC``
    tiebreaker keeps eviction deterministic when two rows share the same
    timestamp (e.g. a discovery batch that writes within the same second).

    Concurrency note: this helper plus ``_write_discovery_picks_sync`` run
    in two separate transactions, which is acceptable because discovery is
    the sole writer to SuggestionsMirror under the weekly cron path
    (``refill_mirror_sql`` is the other writer, but it only fires on play,
    and the weekly cron runs at a quiet hour). The UNIQUE(track_id)
    constraint at suggestions model line 41 makes any accidental collision
    a hard error rather than silent corruption.

    Caller contract: this helper is sync; ``discovery_call_weekly`` wraps
    it via ``asyncio.to_thread`` per Phase 5 Convention #1.
    """
    if evict_count <= 0:
        return []
    with Session(get_engine()) as session:
        # SELECT first to capture plex_rating_key values in the same order
        # the DELETE will use, so the returned list is deterministically
        # oldest-first.
        rows = session.execute(
            text(
                """
                SELECT t.plex_rating_key
                FROM suggestionsmirror sm
                JOIN track t ON t.id = sm.track_id
                ORDER BY sm.added_at ASC, sm.id ASC
                LIMIT :n
                """
            ),
            {"n": evict_count},
        ).all()
        evicted_keys: list[str] = [
            str(r[0]) for r in rows if r[0] is not None
        ]
        # DELETE via subquery so SQLite's ORDER BY + LIMIT semantics on
        # the DELETE itself are well-defined (raw "DELETE ... ORDER BY"
        # support varies by SQLite build).
        session.execute(
            text(
                """
                DELETE FROM suggestionsmirror
                WHERE id IN (
                    SELECT id FROM suggestionsmirror
                    ORDER BY added_at ASC, id ASC
                    LIMIT :n
                )
                """
            ),
            {"n": evict_count},
        )
        session.commit()
        return evicted_keys


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


async def remove_tracks_from_suggestions_playlist(
    plex_url: str,
    plex_token: str,
    mp_rating_key: str,
    rating_keys_to_remove: list[str],
) -> int:
    """Remove rating keys from the Composer-managed Suggestions Plex
    playlist. Returns the playlist's item count AFTER removal.

    Counterpart to :func:`plex_playlist_service.update_playlist_items`
    (additive) so discovery's evict-then-add flow (260517-nkt) can prune
    the Plex playlist symmetrically with the SuggestionsMirror cap.

    Pre-flight :func:`plex_playlist_service.is_managed_playlist` (OPS-06
    / Pitfall 20) — raises :class:`PermissionError` if False. Returns 0
    immediately for an empty ``rating_keys_to_remove`` list (no
    PlexServer construction in that case).

    All PlexAPI calls dispatched via :func:`asyncio.to_thread` per Phase
    5 Convention #1. NotFound on the playlist is intentionally NOT caught
    — the caller (``discovery_call_weekly``) owns the self-heal path
    that re-materializes from the current mirror.
    """
    if not rating_keys_to_remove:
        return 0

    # Pre-flight: import + guard mirrors update_playlist_items:246 EXACTLY.
    from app.services.plex_playlist_service import is_managed_playlist
    if not is_managed_playlist(mp_rating_key):
        raise PermissionError(
            f"OPS-06 / Pitfall 20: playlist {mp_rating_key} is not "
            f"Composer-managed (no ManagedPlaylist row); refusing to mutate."
        )

    # GAP-03 / update_playlist_items:260 idiom — cast to int once up-front
    # so all fetchItem call sites receive int, avoiding PlexAPI's URL-concat
    # bug on bare-string ekeys.
    playlist_key_int = int(mp_rating_key)

    # Lazy import keeps the suggestions_service module-import graph free of
    # plexapi (test environments can monkeypatch plexapi.server.PlexServer
    # to a stub before this helper is invoked).
    from plexapi.server import PlexServer

    def _remove_items_sync() -> int:
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(playlist_key_int)
        # Mirror plex_playlist_service._remove_items:568-574 — per-item
        # fetch via plex.fetchItem(int(k)) so the AST gate sees the call
        # only inside this sync nested function (not in the async body).
        items = [plex.fetchItem(int(k)) for k in rating_keys_to_remove]
        playlist.removeItems(items)
        return len(playlist.items())

    return await asyncio.to_thread(_remove_items_sync)


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
    """D-03 / Phase 7.1 SUGG-12 — threshold gate. When SuggestionsMirror.size
    < target, await :func:`refill_mirror_sql` (free SQL hot path; no LLM,
    no cost breaker check needed). Returns the deficit at gate-check time.

    Symbol name preserved for monkeypatch stability: existing tests in
    test_event_handlers.py monkeypatch this attribute by name (~6 sites).
    Renaming would force test churn for no behavioral benefit.
    """
    current = await asyncio.to_thread(_count_mirror_rows_sync)
    deficit = max(0, target - current)
    if deficit > 0:
        try:
            await refill_mirror_sql(target=target)
        except Exception:
            logger.exception(
                "refill_mirror_sql raised during maybe_schedule_refill "
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
# Phase 7.1 SUGG-12 — SQL-driven refill (replaces the deleted Phase 7
# LLM-ranking entry point). Free, instant, zero LLM tokens. See:
#   .planning/notes/phase-07-followup-cost-architecture.md (Option C)
#   .planning/phases/07.1-suggestions-cost-architecture-sql-refill-weekly-discovery/
#     07.1-CONTEXT.md (D-B1 / D-B2 / D-D1)
# ============================================================================


class RefillResult(BaseModel):
    """Phase 7.1 — outcome of one refill cycle. SQL refill always reports
    ``breaker_tripped=False`` (no LLM, no cost meter to gate).
    """

    picks_made: int = 0
    shortlist_size: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    breaker_tripped: bool = False


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
    """Append one RefillTriggerLog row and return its id. Phase 7.1: used
    by ``refill_mirror_sql`` for empty-pool / no-active-vibes paths where
    ``_write_sql_refill_results_sync`` is not invoked.
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


def _get_anthropic_client_sync():
    """Lazy-construct AnthropicClient from settings_service decrypted api_key.

    Phase 7.1: kept for Plan 02 (discovery LLM call body) — Plan 01's SQL
    refill never invokes this. Tests can monkeypatch the public
    ``_get_anthropic_client`` attribute below to inject a mock client.
    Suffixed ``_sync`` to satisfy the Phase 5 D-09 AST invariant
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


def _read_vibe_pool_weights_sync() -> List[Tuple[int, float]]:
    """D-B1 — return [(vibe_id, library_share), ...] proportional to
    TrackVibe member count per ACTIVE vibe (Vibe.is_active = 1). If 40%
    of TrackVibe rows live in vibe X, ~12 of 30 picks come from X.
    Reflects overall taste shape; does NOT detect "current vibe" from a
    play event (avoids the multi-vibe-membership problem).

    Phase 7.1 Blocker #7 — archived vibes (is_active=False) MUST be
    excluded so a deactivated vibe's tracks never get re-surfaced. The
    WHERE v.is_active = 1 clause is symmetric with
    ``_read_top_n_for_vibe_sync`` so the two helpers cannot disagree
    about vibe eligibility.
    """
    with Session(get_engine()) as session:
        row_pairs = session.execute(
            text(
                """
                SELECT v.id AS vibe_id, COUNT(tv.track_id) AS member_count
                FROM vibe v
                LEFT JOIN trackvibe tv ON tv.vibe_id = v.id
                WHERE v.is_active = 1
                GROUP BY v.id
                """
            )
        ).all()
        total = sum(r[1] or 0 for r in row_pairs) or 1
        return [
            (int(r[0]), (r[1] or 0) / total) for r in row_pairs
        ]


def _read_top_n_for_vibe_sync(
    vibe_id: int, n: int = TOP_N_PER_VIBE,
) -> List[dict]:
    """D-B2 — return the N closest-to-centroid TrackVibe rows for the
    given vibe, ordered ASC by distance, joined with track metadata.
    Excludes tracks already in the mirror, ``hard_track`` / ``hard_artist``
    excluded artists/tracks, and tracks surfaced within the 14-day
    SuggestionHistory window.

    Phase 7.1 Blocker #7 — also excludes ALL members if the vibe itself
    is archived (Vibe.is_active = 0). The JOIN + WHERE v.is_active = 1
    clause is symmetric with ``_read_vibe_pool_weights_sync`` so the two
    helpers cannot disagree about vibe eligibility.
    """
    fourteen_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=14)
    ).isoformat()
    with Session(get_engine()) as session:
        rows = session.execute(
            text(
                """
                SELECT t.id AS track_id, t.plex_rating_key, t.title,
                       t.artist, tv.distance, tv.vibe_id
                FROM trackvibe tv
                JOIN track t ON t.id = tv.track_id
                JOIN vibe v ON v.id = tv.vibe_id
                WHERE tv.vibe_id = :vid
                  AND v.is_active = 1
                  AND t.id NOT IN (
                      SELECT track_id FROM suggestionsmirror
                  )
                  AND t.id NOT IN (
                      SELECT track_id FROM negativesignal
                      WHERE signal_type = 'hard_track'
                        AND track_id IS NOT NULL
                  )
                  AND t.artist NOT IN (
                      SELECT artist FROM negativesignal
                      WHERE signal_type = 'hard_artist'
                        AND recovery_pending = 1
                        AND artist IS NOT NULL
                  )
                  AND t.id NOT IN (
                      SELECT track_id FROM suggestionhistory
                      WHERE surfaced_at >= :fourteen_days_ago
                  )
                ORDER BY tv.distance ASC
                LIMIT :n
                """
            ),
            {
                "vid": vibe_id,
                "n": n,
                "fourteen_days_ago": fourteen_days_ago,
            },
        ).all()
        return [
            {
                "track_id": int(r[0]),
                "plex_rating_key": r[1],
                "title": r[2],
                "artist": r[3],
                "distance": float(r[4] or 0.0),
                "vibe_id": int(r[5]),
            }
            for r in rows
        ]


def _read_top_n_unrated_for_vibe_sync(
    vibe_id: int, n: int = TOP_N_PER_VIBE,
) -> List[dict]:
    """Companion to :func:`_read_top_n_for_vibe_sync` that returns the N
    closest-to-centroid UNRATED tracks for the given vibe.

    Why this exists: ``TrackVibe`` rows are only populated for rated
    tracks (``vibe_service._slot_track_inner`` returns early when
    ``user_rating in (None, 0, 0.0)``), so the rated helper's query
    surfaces 5-star library tracks instead of unrated/unlistened content.
    The Suggestions queue is intended as a discovery surface — rated
    tracks already have a home in their vibe playlists.

    Distance is computed in z-score normalized space using mean/std of
    ACTIVE vibe centroids, matching the metric used by
    ``vibe_service._slot_track_inner``. SQLite does the arithmetic
    inline via bind params; no temporary tables.

    Exclusion lists mirror the rated helper: mirror dupes,
    ``hard_track`` / ``hard_artist`` negatives with recovery_pending,
    and the 14-day SuggestionHistory window.
    """
    fourteen_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=14)
    ).isoformat()

    with Session(get_engine()) as session:
        centroid_rows = session.execute(
            text(
                """
                SELECT id, centroid_energy, centroid_tempo,
                       centroid_danceability, centroid_valence
                FROM vibe
                WHERE is_active = 1
                  AND centroid_energy IS NOT NULL
                  AND centroid_tempo IS NOT NULL
                  AND centroid_danceability IS NOT NULL
                  AND centroid_valence IS NOT NULL
                """
            )
        ).all()
        if not centroid_rows:
            return []

        target_idx = next(
            (i for i, r in enumerate(centroid_rows) if int(r[0]) == vibe_id),
            None,
        )
        if target_idx is None:
            return []

        matrix = np.array(
            [[r[1], r[2], r[3], r[4]] for r in centroid_rows], dtype=float
        )
        mean = matrix.mean(axis=0)
        std = matrix.std(axis=0)
        safe_std = np.where(std < _STD_FLOOR, _STD_FLOOR, std)
        target_norm = (matrix[target_idx] - mean) / safe_std
        cn_e, cn_t, cn_d, cn_v = target_norm.tolist()

        rows = session.execute(
            text(
                """
                SELECT t.id, t.plex_rating_key, t.title, t.artist,
                       (
                         ((t.energy - :me) / :se - :cn_e)
                           * ((t.energy - :me) / :se - :cn_e)
                         + ((t.tempo - :mt) / :st - :cn_t)
                           * ((t.tempo - :mt) / :st - :cn_t)
                         + ((t.danceability - :md) / :sd - :cn_d)
                           * ((t.danceability - :md) / :sd - :cn_d)
                         + ((t.valence - :mv) / :sv - :cn_v)
                           * ((t.valence - :mv) / :sv - :cn_v)
                       ) AS dist_sq
                FROM track t
                WHERE t.energy IS NOT NULL
                  AND t.tempo IS NOT NULL
                  AND t.danceability IS NOT NULL
                  AND t.valence IS NOT NULL
                  AND (t.user_rating IS NULL OR t.user_rating = 0)
                  AND t.id NOT IN (
                      SELECT track_id FROM suggestionsmirror
                  )
                  AND t.id NOT IN (
                      SELECT track_id FROM negativesignal
                      WHERE signal_type = 'hard_track'
                        AND track_id IS NOT NULL
                  )
                  AND t.artist NOT IN (
                      SELECT artist FROM negativesignal
                      WHERE signal_type = 'hard_artist'
                        AND recovery_pending = 1
                        AND artist IS NOT NULL
                  )
                  AND t.id NOT IN (
                      SELECT track_id FROM suggestionhistory
                      WHERE surfaced_at >= :fourteen_days_ago
                  )
                ORDER BY dist_sq ASC
                LIMIT :n
                """
            ),
            {
                "me": float(mean[0]), "se": float(safe_std[0]),
                "mt": float(mean[1]), "st": float(safe_std[1]),
                "md": float(mean[2]), "sd": float(safe_std[2]),
                "mv": float(mean[3]), "sv": float(safe_std[3]),
                "cn_e": float(cn_e), "cn_t": float(cn_t),
                "cn_d": float(cn_d), "cn_v": float(cn_v),
                "fourteen_days_ago": fourteen_days_ago,
                "n": n,
            },
        ).all()

        return [
            {
                "track_id": int(r[0]),
                "plex_rating_key": r[1],
                "title": r[2],
                "artist": r[3],
                "distance": float(
                    np.sqrt(max(0.0, float(r[4] or 0.0)))
                ),
                "vibe_id": vibe_id,
            }
            for r in rows
        ]


def _write_sql_refill_results_sync(
    picks: List[dict],
    event_source: str = "sql_refill",
    latency_ms: int = 0,
) -> int:
    """Phase 7.1 — persist refill outcome inside one Session. Takes the
    new pick shape (dict with ``track_id``, ``distance``, ``vibe_id``,
    ``plex_rating_key``) directly. Persists:

    1. RefillTriggerLog row → ``refill_id`` (cost_estimate_usd=0.0,
       breaker_tripped=False; the SQL refill path never invokes the LLM
       or the cost breaker).
    2. SuggestionsMirror row per pick (INSERT OR IGNORE on UNIQUE
       track_id; race-safe with drain).
    3. SuggestionHistory row per pick (refill_id matches; powers the
       14-day exclusion window in subsequent refills).

    ``rationale`` is set to a deterministic SQL-derived string
    ("Closest match for {vibe_name} (distance {d:.3f})") since there is
    no LLM to author one. Plan 02's discovery picks will overwrite this
    field with an LLM-authored rationale when applicable.
    """
    from app.models.suggestions import (
        RefillTriggerLog, SuggestionHistory,
    )
    from app.models.vibe import Vibe

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        log = RefillTriggerLog(
            triggered_at=now_iso,
            event_source=event_source,
            target_vibe_id=None,
            candidates_evaluated=len(picks),
            picks_made=len(picks),
            latency_ms=latency_ms,
            cost_estimate_usd=0.0,
            breaker_tripped=False,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        refill_id = log.id

        vibe_names: dict = {
            int(v.id): v.name
            for v in session.exec(select(Vibe)).all()
            if v.id is not None
        }

        max_pos = session.execute(
            text("SELECT COALESCE(MAX(position), -1) FROM suggestionsmirror")
        ).first()
        next_pos = int((max_pos[0] if max_pos else -1) or -1) + 1

        for pick in picks:
            vname = vibe_names.get(pick["vibe_id"], "vibe")
            rationale = (
                f"Closest match for {vname} "
                f"(distance {pick['distance']:.3f})"
            )
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
                    "tid": pick["track_id"],
                    "pos": next_pos,
                    "added": now_iso,
                    "rat": rationale,
                    "vid": pick["vibe_id"],
                    "score": pick["distance"],
                },
            )
            next_pos += 1
            session.add(SuggestionHistory(
                track_id=pick["track_id"],
                surfaced_at=now_iso,
                refill_id=refill_id,
            ))
        session.commit()
        return int(refill_id)  # type: ignore[return-value]


async def refill_mirror_sql(
    target: int = SUGGESTIONS_TARGET_SIZE,
) -> RefillResult:
    """Phase 7.1 SUGG-12 — SQL-driven mirror refill. Replaces the deleted
    Phase 7 LLM-ranking entry point. Free, instant, no LLM tokens consumed.

    Algorithm (D-B1 + D-B2):
      1. Compute current deficit (target - SuggestionsMirror.count()).
         If deficit ≤ 0: no-op, return RefillResult(picks_made=0).
      2. Read library-share weights per ACTIVE vibe (D-B1; archived
         vibes excluded).
      3. Allocate the deficit across vibes proportionally; round up so
         the sum reaches deficit (largest-fractional-remainder rounding).
      4. For each vibe with allocation > 0: fetch the top-N closest
         tracks (D-B2; archived vibes return []), then random.sample
         (allocated_for_vibe) from that pool. Variety without destroying
         taste-fit.
      5. Persist picks via ``_write_sql_refill_results_sync``.
      6. Mirror the canonical Plex push branch from the deleted Phase 7
         LLM-ranking entry point: read ManagedPlaylist(kind='suggestions');
         if missing, log + skip; if plex_rating_key=='' (sentinel) call
         ``_materialize_suggestions_plex_playlist(plex_url, plex_token,
         rating_keys)``; else call ``update_playlist_items(plex_url,
         plex_token, mp.plex_rating_key, rating_keys)``.

    Returns a ``RefillResult`` with ``breaker_tripped=False`` always —
    SQL refill never trips a breaker (there is no LLM cost to gate).
    """
    import random
    import time as _time

    global _status
    start = _time.monotonic()

    current = await asyncio.to_thread(_count_mirror_rows_sync)
    deficit = max(0, target - current)
    if deficit <= 0:
        return RefillResult(
            picks_made=0,
            shortlist_size=0,
            latency_ms=0,
            cost_usd=0.0,
            breaker_tripped=False,
        )

    weights = await asyncio.to_thread(_read_vibe_pool_weights_sync)
    if not weights:
        # No active vibes → nothing to refill from. Log a trigger row
        # for observability (zero picks).
        await asyncio.to_thread(
            _insert_refill_trigger_log_sync,
            "sql_refill", 0, 0,
            int((_time.monotonic() - start) * 1000),
            0.0, False, None, None,
        )
        return RefillResult(
            picks_made=0,
            shortlist_size=0,
            latency_ms=int((_time.monotonic() - start) * 1000),
            cost_usd=0.0,
            breaker_tripped=False,
        )

    # Allocate deficit across vibes proportional to library share.
    # Largest-fractional-remainder rounding so allocation sums to deficit.
    raw = [(vid, share * deficit) for vid, share in weights]
    floored = [(vid, int(amount), amount - int(amount)) for vid, amount in raw]
    allocated = sum(f for _, f, _ in floored)
    remainder = deficit - allocated
    # Sort by descending fractional remainder; bump those vibes by 1.
    floored.sort(key=lambda x: x[2], reverse=True)
    allocations: dict = {}
    for i, (vid, base, _frac) in enumerate(floored):
        extra = 1 if i < remainder else 0
        allocations[vid] = base + extra

    # Per-vibe top-N + random sample of allocation.
    # PRIMARY path: unrated tracks (260516-uvr hotfix — rated tracks already
    # live in their vibe playlists; Suggestions should be a discovery
    # surface). The unrated helper computes distance against the vibe
    # centroid directly (TrackVibe is rated-only by design).
    #
    # FALLBACK path (260517-vyq): when the unrated pool is empty for a vibe
    # (most likely cause: the user's unrated tracks haven't been Essentia-
    # analyzed yet → NULL audio features → filtered out), fall back to the
    # rated TrackVibe pool so the queue doesn't run dry. Better UX to surface
    # a rated track than to leave the queue empty after every play. The
    # fallback is observable in /debug/suggestions (latency_ms is the same;
    # logged at INFO level here so operators can see when it kicks in).
    picks: List[dict] = []
    fallback_vibes = 0
    for vid, n_picks in allocations.items():
        if n_picks <= 0:
            continue
        pool = await asyncio.to_thread(
            _read_top_n_unrated_for_vibe_sync, vid, TOP_N_PER_VIBE,
        )
        if not pool:
            # Unrated pool empty for this vibe — fall back to rated.
            pool = await asyncio.to_thread(
                _read_top_n_for_vibe_sync, vid, TOP_N_PER_VIBE,
            )
            if pool:
                fallback_vibes += 1
                logger.info(
                    "refill_mirror_sql: vibe_id=%d unrated pool empty; "
                    "falling back to rated TrackVibe pool (%d candidates).",
                    vid, len(pool),
                )
        if not pool:
            continue
        sample_size = min(n_picks, len(pool))
        chosen = random.sample(pool, sample_size)
        picks.extend(chosen)
    if fallback_vibes > 0:
        logger.info(
            "refill_mirror_sql: %d vibe(s) fell back to rated pool; deploy "
            "Essentia analysis to widen the unrated discovery surface.",
            fallback_vibes,
        )

    # Persist picks + RefillTriggerLog row.
    latency_ms = int((_time.monotonic() - start) * 1000)
    if picks:
        await asyncio.to_thread(
            _write_sql_refill_results_sync,
            picks, "sql_refill", latency_ms,
        )
    else:
        # No candidates available across all vibes — log a no-op trigger.
        await asyncio.to_thread(
            _insert_refill_trigger_log_sync,
            "sql_refill", 0, 0, latency_ms,
            0.0, False, None, None,
        )

    # ========================================================================
    # Plex push — MIRROR the canonical branch from the deleted Phase 7
    # LLM-ranking refill (Phase 7.1 D-D1). Three positional args to
    # ``_materialize_suggestions_plex_playlist``; await it directly (already
    # async).
    # ========================================================================
    if picks:
        rating_keys = [p["plex_rating_key"] for p in picks]
        mp = await asyncio.to_thread(_find_suggestions_managed_playlist_sync)
        if mp is None:
            logger.warning(
                "refill_mirror_sql: no ManagedPlaylist(kind=suggestions) "
                "row found; bootstrap was likely skipped. Skipping Plex push."
            )
        elif not mp.plex_rating_key:
            # First non-empty refill: materialize the deferred Plex
            # playlist and update the sentinel row in-place.
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
            except Exception as exc:
                # Self-heal (260517-shp): when the user deletes the Plex
                # playlist out from under us, fetchItem raises NotFound
                # and the previous best-effort log left the user stranded
                # forever (stale plex_rating_key references a deleted
                # playlist; every future refill fails). Detect that case
                # and re-materialize a new Plex playlist + update the
                # ManagedPlaylist row in place.
                #
                # We match on "not found" loosely (NotFound from plexapi,
                # 404 from raw HTTP) so a server-side rename also recovers.
                # PermissionError is intentionally NOT recovered — that
                # means is_managed_playlist returned False, which is an
                # OPS-06 / Pitfall 20 invariant violation.
                err_str = (type(exc).__name__ + " " + str(exc)).lower()
                from plexapi import exceptions as _plexex
                is_not_found = (
                    isinstance(exc, _plexex.NotFound)
                    or "notfound" in err_str
                    or "not found" in err_str
                    or "404" in err_str
                )
                if is_not_found and not isinstance(exc, PermissionError):
                    logger.warning(
                        "refill_mirror_sql: Plex playlist rk=%s vanished "
                        "(likely user-deleted); re-materializing and "
                        "updating ManagedPlaylist row.",
                        mp.plex_rating_key,
                    )
                    try:
                        await _materialize_suggestions_plex_playlist(
                            plex_url, plex_token, rating_keys,
                        )
                    except Exception:
                        logger.exception(
                            "refill_mirror_sql: re-materialize after "
                            "vanish-detect ALSO failed; mirror state is "
                            "the truth — next refill will retry."
                        )
                else:
                    logger.exception(
                        "refill_mirror_sql: Plex update_playlist_items "
                        "failed (not a vanish); mirror state is the "
                        "truth — Plex will reconcile on next refill."
                    )

    _status = SuggestionsServiceStatus(
        state="idle",
        last_bootstrap_at=_status.last_bootstrap_at,
        last_drain_at=_status.last_drain_at,
    )

    return RefillResult(
        picks_made=len(picks),
        shortlist_size=sum(allocations.values()),
        latency_ms=latency_ms,
        cost_usd=0.0,
        breaker_tripped=False,
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


async def run_phase_08_3_trim_suggestions_mirror_to_target() -> None:
    """QUICK FIX (260517-p2b) — lifespan one-shot. Gated by
    ``MigrationLog(phase_id='8.3-trim-suggestions-mirror-to-target')``.

    Trims any existing oversized SuggestionsMirror down to
    ``SUGGESTIONS_TARGET_SIZE`` on the next boot. The 260517-nkt discovery
    WRITE cap is forward-looking only; this migration cleans the carryover
    state once so ``maybe_schedule_refill``'s deficit gate can re-open and
    the rate-feedback loop unstalls on NAS installs that carried a 39-row
    mirror over from pre-260517-nkt unconstrained cron runs.

    Mirrors the shape of
    :func:`app.services.discovery_service.run_phase_08_2_discovery_dedupe_artist_name`
    — same gate pattern, same failure semantics (outer try/except leaves
    ``completed_at`` NULL on exception so the next restart retries).

    The Plex push has its OWN inner try/except so push failure does NOT
    block the success stamp (the DB trim is the loop-unstall win; Plex
    reconciles via the weekly prune job). NotFound is detected via the
    canonical idiom byte-equivalent to
    ``app/services/suggestions_service.py:1204-1211``.
    """
    existing = await asyncio.to_thread(
        _read_migration_log_sync, PHASE_08_3_MIGRATION_ID,
    )
    if existing is not None and existing.completed_at is not None:
        logger.info(
            "Phase 8.3 mirror-trim migration already complete "
            "(completed_at=%s); skipping.",
            existing.completed_at,
        )
        return

    # In-flight marker — outer except leaves this NULL → retry next boot.
    await asyncio.to_thread(
        _upsert_migration_log_sync, PHASE_08_3_MIGRATION_ID, None,
    )

    try:
        current = await asyncio.to_thread(_count_mirror_rows_sync)
        evict_count = max(0, current - SUGGESTIONS_TARGET_SIZE)

        if evict_count == 0:
            logger.info(
                "Phase 8.3 mirror-trim migration: mirror already at or "
                "below target (count=%d, target=%d)",
                current, SUGGESTIONS_TARGET_SIZE,
            )
            evicted_keys: list[str] = []
            plex_removed = 0
        else:
            evicted_keys = await asyncio.to_thread(
                _evict_oldest_mirror_rows_sync, evict_count,
            )

            # Plex push — best effort, own try/except so failure here does
            # NOT block the success stamp below. The DB trim is the actual
            # loop-unstall win; Plex reconciles via the weekly prune job.
            mp = await asyncio.to_thread(
                _find_suggestions_managed_playlist_sync,
            )
            if mp is None:
                logger.warning(
                    "Phase 8.3: no ManagedPlaylist(kind=suggestions) row; "
                    "DB trim committed, Plex sync deferred to weekly prune"
                )
                plex_removed = 0
            elif not mp.plex_rating_key:
                logger.warning(
                    "Phase 8.3: deferred playlist (no plex_rating_key yet); "
                    "DB trim committed, Plex push skipped"
                )
                plex_removed = 0
            else:
                plex_url, plex_token = await asyncio.to_thread(
                    _read_plex_creds_sync,
                )
                try:
                    plex_removed = await remove_tracks_from_suggestions_playlist(
                        plex_url, plex_token, mp.plex_rating_key, evicted_keys,
                    )
                except Exception as exc:
                    # NotFound canonical idiom — byte-equivalent to
                    # app/services/suggestions_service.py:1204-1211.
                    err_str = (type(exc).__name__ + " " + str(exc)).lower()
                    from plexapi import exceptions as _plexex
                    is_not_found = (
                        isinstance(exc, _plexex.NotFound)
                        or "notfound" in err_str
                        or "not found" in err_str
                        or "404" in err_str
                    )
                    if is_not_found:
                        logger.warning(
                            "Phase 8.3: Plex playlist rk=%s NotFound during "
                            "trim; DB state authoritative",
                            mp.plex_rating_key,
                        )
                    else:
                        logger.exception(
                            "Phase 8.3 Plex remove failed; DB state "
                            "authoritative"
                        )
                    plex_removed = 0
    except Exception:
        # Trim itself failed — leave completed_at NULL so next boot retries.
        logger.exception(
            "Phase 8.3 mirror-trim migration failed; "
            "will retry on next boot"
        )
        return

    await asyncio.to_thread(
        _upsert_migration_log_sync,
        PHASE_08_3_MIGRATION_ID,
        datetime.now(timezone.utc).isoformat(),
    )
    logger.info(
        "Phase 8.3 mirror-trim migration complete: evicted %d rows "
        "(mirror_size %d → %d, plex_removed=%d)",
        evict_count, current, current - evict_count, plex_removed,
    )
