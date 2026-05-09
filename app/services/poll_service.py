"""Plex polling fallback (EVT-03).

The polling job is a defensive partner to the webhook receiver — webhooks may
miss events when the network is partitioned, when the user reboots either side,
or when Plex itself drops a delivery on the floor. Polling closes that gap.

Architecture per CONTEXT D-08 + RESEARCH "Pattern 3":
- Bounded queries ONLY (Pitfall 21 — full library scans peg the NAS).
  Uses `searchTracks(filters={...}, limit=200, sort='lastRatedAt:desc')`.
- Default 5-min interval; registered alongside library_sync via the existing
  AsyncIOScheduler singleton at sync_scheduler.py.
- Emits the SAME typed events through the SAME asyncio.Queue + dispatcher path
  as webhooks. Dedupe via UNIQUE(dedupe_key) at the DB layer handles overlap
  naturally — no app-level coordination needed.
- ALL PlexAPI calls wrapped in `asyncio.to_thread` (D-09 / EVT-06 invariant,
  enforced by the AST static test in tests/test_event_handlers.py).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from plexapi.server import PlexServer
from sqlmodel import Session, select

from app.database import get_engine
from app.models.events import RatingChangedEvent, TrackPlayedEvent
from app.models.track import Track
from app.services.event_bus import get_event_bus
from app.services.settings_service import get_decrypted_credential, get_setting

logger = logging.getLogger(__name__)


class PollStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PollStatus:
    state: PollStateEnum = PollStateEnum.IDLE
    last_started: Optional[str] = None
    last_completed: Optional[str] = None
    last_changes_seen: int = 0
    error: Optional[str] = None


# Module-level singleton (Composer convention — leading underscore)
_poll_status = PollStatus()


def get_poll_status() -> PollStatus:
    """Return the current in-memory poll status."""
    return _poll_status


# -----------------------------------------------------------------------------
# Sync helpers — all suffixed _sync; called via asyncio.to_thread (D-09).
# -----------------------------------------------------------------------------

def _get_existing_ratings_sync() -> dict:
    """Snapshot {plex_rating_key: user_rating} from DB for diff detection."""
    with Session(get_engine()) as session:
        rows = session.exec(
            select(Track.plex_rating_key, Track.user_rating)  # type: ignore[arg-type]
        ).all()
        return {row[0]: row[1] for row in rows}


def _poll_recently_rated_sync(plex: PlexServer, library_id) -> List:
    """Bounded query for tracks with userRating > 0, sorted newest-rated first.

    Per D-08 + Pitfall 21 — limit 200, NEVER an unbounded full-library scan.
    """
    section = plex.library.sectionByID(int(library_id))
    return section.searchTracks(
        filters={"track.userRating>>": 0},
        sort="lastRatedAt:desc",
        limit=200,
    )


def _poll_recently_played_sync(plex: PlexServer, library_id) -> List:
    """Bounded query for recently-played tracks (lastViewedAt-sorted)."""
    section = plex.library.sectionByID(int(library_id))
    return section.searchTracks(
        sort="lastViewedAt:desc",
        limit=200,
    )


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------

async def run_poll() -> None:
    """Plex polling pass. EVT-03 / D-08.

    1. Concurrency guard (mirrors sync_service.py:152-159).
    2. Pulls credentials from settings.
    3. Bounded query for rated tracks; emits RatingChangedEvent on diff.
    4. Bounded query for recently-played tracks; emits TrackPlayedEvent.
    5. Records status; redacts Plex token from any error string.
    """
    global _poll_status

    if _poll_status.state == PollStateEnum.RUNNING:
        return

    _poll_status = PollStatus(
        state=PollStateEnum.RUNNING,
        last_started=datetime.now(timezone.utc).isoformat(),
    )
    token = ""
    changes = 0
    try:
        # Read Plex config
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

        # PlexServer construction is sync — wrap (D-09)
        plex = await asyncio.to_thread(PlexServer, url, token, 30)
        existing = await asyncio.to_thread(_get_existing_ratings_sync)
        received_at = datetime.now(timezone.utc).isoformat()
        bus = get_event_bus()

        # 1. Rating diffs
        rated_tracks = await asyncio.to_thread(
            _poll_recently_rated_sync, plex, library_id
        )
        for t in rated_tracks:
            rk = str(t.ratingKey)
            new_rating = getattr(t, "userRating", None)
            if new_rating is None:
                continue
            new_rating_f = float(new_rating)
            if existing.get(rk) != new_rating_f:
                bus.put_nowait(
                    RatingChangedEvent(
                        plex_rating_key=rk,
                        new_rating=new_rating_f,
                        source="poll",
                        received_at=received_at,
                    )
                )
                changes += 1

        # 2. Play diffs
        played_tracks = await asyncio.to_thread(
            _poll_recently_played_sync, plex, library_id
        )
        for t in played_tracks:
            lv = getattr(t, "lastViewedAt", None)
            if lv is None:
                continue
            rk = str(t.ratingKey)
            lv_iso = lv.isoformat() if hasattr(lv, "isoformat") else str(lv)
            bus.put_nowait(
                TrackPlayedEvent(
                    plex_rating_key=rk,
                    last_viewed_at=lv_iso,
                    source="poll",
                    received_at=received_at,
                )
            )
            # Bus dedupe handles webhook-vs-poll overlap; not counted as a "real" change.

        _poll_status.state = PollStateEnum.COMPLETED
        _poll_status.last_changes_seen = changes
        _poll_status.last_completed = datetime.now(timezone.utc).isoformat()
        logger.info("Poll completed: %d rating changes seen", changes)
    except Exception as exc:
        _poll_status.state = PollStateEnum.FAILED
        err = str(exc)
        if token:
            err = err.replace(token, "[REDACTED]")
        _poll_status.error = err
        logger.exception("Poll failed")
