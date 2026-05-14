"""Phase 7 (Plan 01) — Suggestions queue service.

Module-level singleton + state pattern (Phase 5 D-08, mirrors
sync_service.py / analysis_service.py / vibe_service.py).

Public API:
  - bootstrap_suggestions_queue() — register the Composer · Suggestions
    ManagedPlaylist row. Idempotent: if the row already exists, returns
    silently. PLAN 01 DEVIATION: the Plex playlist itself is created during
    Plan 02's first refill (when there are tracks to seed it with), because
    PlexAPI rejects createPlaylist calls with an empty items list. Plan 01
    registers a ManagedPlaylist row with ``plex_rating_key=""`` sentinel;
    Plan 02 detects the sentinel, creates the Plex playlist, and updates
    the row.
  - drain_track_from_mirror(rating_key) — remove the track from
    SuggestionsMirror IF it is currently a member. Returns True if removed.
  - maybe_schedule_refill(target=30) — if mirror size < target, write an
    EventLog row with event_type='suggestions_refill_pending' for Plan 02 to
    consume. Returns the deficit (target - current_size); 0 means no
    refill needed.
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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session, select

from app.database import get_engine
from app.models.vibe import ManagedPlaylist, MigrationLog

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


def _insert_refill_pending_marker_sync(deficit: int) -> None:
    """Plan 01: write a marker row to EventLog so Plan 02 can pick it up.
    Plan 02 replaces this marker with a direct refill call; this keeps
    Plan 01 isolated from any LLM call.

    Uses INSERT OR IGNORE + the same 5-second-bucket dedupe pattern as
    Phase 5 D-07 so tight-loop callers collapse via UNIQUE(dedupe_key).
    """
    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp() // 5)
    dedupe_key = f"suggestions_refill_pending|{bucket}"
    with Session(get_engine()) as session:
        session.execute(
            text(
                """
                INSERT OR IGNORE INTO eventlog
                    (source, event_type, plex_rating_key, dedupe_key,
                     received_at, raw_payload)
                VALUES ('manual', 'suggestions_refill_pending', NULL,
                        :dk, :ra, :rp)
                """
            ),
            {
                "dk": dedupe_key,
                "ra": now.isoformat(),
                "rp": f'{{"deficit": {deficit}}}',
            },
        )
        session.commit()


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

    Returns None to signal "deferred — Plan 02 will create the playlist
    on the first refill". This indirection exists so tests can patch the
    helper to assert it is or is not called along the idempotent paths
    without touching real Plex.

    Plan 02 will replace this with a real call to
    ``plex_playlist_service.create_playlist(plex_url, plex_token,
    SUGGESTIONS_PLAYLIST_NAME, seed_rating_keys)`` once the first
    suggestions batch is available, and update the ManagedPlaylist row
    with the returned ratingKey.
    """
    # Plan 01: no-op. PlexAPI rejects createPlaylist with an empty items
    # list (BadRequest), and Plan 01 has no seed tracks to pass. The
    # ManagedPlaylist row gets the empty-string sentinel; Plan 02 fills
    # it in on first refill.
    return None


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
    """D-03 threshold gate — when SuggestionsMirror.size < target, write an
    EventLog marker for Plan 02 to consume. Returns the deficit (0 means no
    refill needed).
    """
    current = await asyncio.to_thread(_count_mirror_rows_sync)
    deficit = max(0, target - current)
    if deficit > 0:
        await asyncio.to_thread(_insert_refill_pending_marker_sync, deficit)
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
