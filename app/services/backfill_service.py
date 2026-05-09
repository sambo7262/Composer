"""First-run auto-backfill of Plex rating fields onto existing Track rows (RATE-02).

When Phase 5 deploys onto a NAS that's already been running v1, the Track table
already has thousands of rows synced — but `user_rating` is NULL for all of
them. This service pages through Plex once, populating `user_rating`,
`last_viewed_at`, and `view_count` for every existing Track. After it
completes, the polling job + webhooks keep those columns fresh going forward.

State-machine pattern mirrors `app.services.analysis_service.trigger_post_sync_analysis`
exactly — IDLE→RUNNING→COMPLETED/FAILED. Auto-trigger gate is Pitfall 7:
backfill ONLY when (any track exists) AND (no track has user_rating populated)
— don't fire on a truly empty DB before the first library sync has run.

The same `run_backfill` function powers the manual "Resync now" button (EVT-05)
via `app.services.rating_sync_service` (a thin re-export shim).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Session, select

from app.database import get_engine
from app.models.track import Track
from app.services.plex_client import get_library_tracks
from app.services.settings_service import get_decrypted_credential, get_setting

logger = logging.getLogger(__name__)


class BackfillStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BackfillStatus:
    state: BackfillStateEnum = BackfillStateEnum.IDLE
    total_tracks: int = 0
    backfilled_tracks: int = 0
    last_started: Optional[str] = None
    last_completed: Optional[str] = None
    error: Optional[str] = None


_backfill_status = BackfillStatus()


def get_backfill_status() -> BackfillStatus:
    """Return the current in-memory backfill status."""
    return _backfill_status


# -----------------------------------------------------------------------------
# Sync helpers — all suffixed _sync; called via asyncio.to_thread (D-09).
# -----------------------------------------------------------------------------

def _check_needs_backfill_sync() -> bool:
    """Pitfall 7 gate: needs (any track exists) AND (no track rated yet).

    True when the DB has Track rows but every row has user_rating IS NULL —
    classic "first deploy onto an existing v1 library" signal. False on a
    truly empty DB (sync hasn't run yet — would be wasted Plex pagination).
    """
    with Session(get_engine()) as session:
        any_track = session.exec(select(Track.id).limit(1)).first()
        if not any_track:
            return False  # truly empty DB — sync hasn't run yet
        any_rated = session.exec(
            select(Track.id)
            .where(Track.user_rating.is_not(None))  # type: ignore[union-attr]
            .limit(1)
        ).first()
        return any_rated is None


def _count_tracks_in_db_sync() -> int:
    """Count Track rows in the local DB.

    Used as the backfill denominator. PlexAPI's section.totalSize on a music
    library returns the artist count (not track count), so we can't trust the
    Plex-side total. The local DB row count is the population we're actually
    backfilling against.
    """
    from sqlalchemy import func
    with Session(get_engine()) as session:
        result = session.exec(select(func.count()).select_from(Track)).one()
        return int(result if isinstance(result, int) else result[0])


def _upsert_rating_fields_sync(track_dicts: list) -> int:
    """Update user_rating + last_viewed_at + view_count on existing tracks.

    Matches by plex_rating_key. Does NOT create new tracks — that's the sync
    service's job. Returns count of rows actually updated.
    """
    updated = 0
    with Session(get_engine()) as session:
        for td in track_dicts:
            rk = td.get("plex_rating_key")
            if not rk:
                continue
            stmt = select(Track).where(Track.plex_rating_key == rk)
            track = session.exec(stmt).first()
            if track is None:
                continue  # track not in our DB; sync hasn't seen it yet
            track.user_rating = td.get("user_rating")
            track.last_viewed_at = td.get("last_viewed_at")
            track.view_count = td.get("view_count", 0) or 0
            session.add(track)
            updated += 1
        session.commit()
    return updated


# -----------------------------------------------------------------------------
# Public entry points
# -----------------------------------------------------------------------------

async def maybe_trigger_first_run_backfill() -> None:
    """Lifespan-startup auto-trigger. Idempotent — safe to call repeatedly.

    Pitfall 7: only fires when there are tracks AND no track has user_rating
    populated. Empty DB or already-rated DB → no-op.
    """
    global _backfill_status
    if _backfill_status.state == BackfillStateEnum.RUNNING:
        return
    needs = await asyncio.to_thread(_check_needs_backfill_sync)
    if needs:
        logger.info("Phase 5 first-run backfill needed; scheduling")
        asyncio.create_task(run_backfill())


async def run_backfill() -> None:
    """Page through Plex, populate rating columns on existing Track rows.

    Concurrency-guarded (mirrors sync_service.py:152-159). Token-sanitized
    error path. Pagination cursor advances by container_size each iteration
    until the batch is short or we've matched the reported total.
    """
    global _backfill_status

    if _backfill_status.state == BackfillStateEnum.RUNNING:
        return

    _backfill_status = BackfillStatus(
        state=BackfillStateEnum.RUNNING,
        last_started=datetime.now(timezone.utc).isoformat(),
    )
    token = ""
    try:
        with Session(get_engine()) as session:
            setting = get_setting(session, "plex")
            if setting is None or not setting.is_configured:
                raise ValueError("Plex is not configured")
            token = get_decrypted_credential(session, "plex") or ""
            url = setting.url
            extra = setting.extra_config or {}
            library_id = extra.get("library_id")
            if not (url and token and library_id):
                raise ValueError("Plex URL/token/library_id missing")

        # Use local DB track count as the denominator (NOT section.totalSize —
        # that returns the artist count for Plex music libraries, leading to
        # nonsensical "X of Y" display when X >> Y).
        total = await asyncio.to_thread(_count_tracks_in_db_sync)
        _backfill_status.total_tracks = total
        logger.info("Backfill started: %d tracks in local DB", total)

        batch_size = 200
        offset = 0
        backfilled = 0

        while True:
            batch, _ = await get_library_tracks(
                url,
                token,
                library_id,
                container_start=offset,
                container_size=batch_size,
            )

            count = await asyncio.to_thread(_upsert_rating_fields_sync, batch)
            backfilled += count
            _backfill_status.backfilled_tracks = backfilled

            # Yield control so HTMX banner status endpoint stays responsive
            await asyncio.sleep(0)

            # Plex music sections often return all tracks in one call ignoring
            # container_size; break when we got a short page OR when we've
            # processed at least the whole DB population.
            if len(batch) < batch_size or backfilled >= total:
                break
            offset += batch_size

        _backfill_status.state = BackfillStateEnum.COMPLETED
        _backfill_status.last_completed = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Backfill completed: %d / %d tracks", backfilled, total
        )
    except Exception as exc:
        _backfill_status.state = BackfillStateEnum.FAILED
        err = str(exc)
        if token:
            err = err.replace(token, "[REDACTED]")
        _backfill_status.error = err
        logger.exception("Backfill failed")
