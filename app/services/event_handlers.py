"""Event-bus consumer: dispatch + INSERT OR IGNORE dedupe + per-type handlers.

Architecture (D-06, D-07, D-09):
- `dispatch_event(event)` is the single entry called by `event_bus._dispatch_loop`.
- It computes a SHA-256 dedupe_key, INSERTs an EventLog row via INSERT OR IGNORE
  (atomic dedupe — Pitfall 1), then routes to the typed handler.
- Each handler runs DB / PlexAPI work via asyncio.to_thread (D-09 / EVT-06).

Phase 5 scope:
- handle_rating_changed: full implementation (RATE-03).
- handle_track_played / handle_library_added / handle_webhook_test: stubs that
  log only. Plan 02 will implement track_played; library_added stays a logging
  stub through Phase 8; webhook_test surfaces via webhook_test_state.

Dispatch uses `if/elif` on `event.type` rather than `match` (Python 3.10+
structural pattern matching). Both forms are functionally equivalent for the
discriminator-only routing used here; `if/elif` keeps the file parseable on
older Python interpreters used by some local development setups.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session, select

from app.database import get_engine
from app.models.events import (
    BaseEvent,
    LibraryAddedEvent,
    RatingChangedEvent,
    TrackPlayedEvent,
    WebhookTestEvent,
)
from app.models.track import Track

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Dedupe key
# -----------------------------------------------------------------------------

def _compute_dedupe_key(
    event_type: str,
    rating_key: Optional[str],
    user_rating: Optional[float],
    received_at_epoch: float,
) -> str:
    """SHA-256 of (event_type | ratingKey | user_rating | 5s_bucket).

    The 5-second bucket means duplicate webhook+poll events for the same logical
    state change land on the same key (Pitfall 1). UNIQUE constraint at the DB
    layer ensures only one persists.
    """
    bucket = int(received_at_epoch // 5)
    rating_repr = "_" if user_rating is None else str(user_rating)
    seed = f"{event_type}|{rating_key or '_'}|{rating_repr}|{bucket}"
    return hashlib.sha256(seed.encode()).hexdigest()


# -----------------------------------------------------------------------------
# DB helpers — all suffixed _sync; called via asyncio.to_thread (D-09 / EVT-06)
# -----------------------------------------------------------------------------

def _insert_event_log_sync(
    source: str,
    event_type: str,
    rating_key: Optional[str],
    dedupe_key: str,
    received_at: str,
    raw_payload: Optional[str],
) -> int:
    """INSERT OR IGNORE; returns 1 on insert, 0 on duplicate (silent dedupe)."""
    with Session(get_engine()) as session:
        result = session.execute(
            text(
                """
                INSERT OR IGNORE INTO eventlog
                    (source, event_type, plex_rating_key, dedupe_key, received_at, raw_payload)
                VALUES (:s, :t, :rk, :dk, :ra, :rp)
                """
            ),
            {
                "s": source,
                "t": event_type,
                "rk": rating_key,
                "dk": dedupe_key,
                "ra": received_at,
                "rp": raw_payload,
            },
        )
        session.commit()
        return result.rowcount or 0


def _mark_event_processed_sync(dedupe_key: str, error: Optional[str] = None) -> None:
    """Set processed_at + handler_error on the EventLog row matching dedupe_key."""
    with Session(get_engine()) as session:
        session.execute(
            text(
                "UPDATE eventlog SET processed_at = :pa, handler_error = :err "
                "WHERE dedupe_key = :dk"
            ),
            {
                "pa": datetime.now(timezone.utc).isoformat(),
                "err": error,
                "dk": dedupe_key,
            },
        )
        session.commit()


def _update_track_rating_sync(
    rating_key: str,
    new_rating: Optional[float],
    rating_changed_at: str,
) -> None:
    """RATE-03: persist Track.user_rating + rating_changed_at."""
    with Session(get_engine()) as session:
        statement = select(Track).where(Track.plex_rating_key == rating_key)
        track = session.exec(statement).first()
        if track is None:
            logger.info(
                "RatingChanged for unknown ratingKey=%s; ignoring", rating_key
            )
            return
        track.user_rating = new_rating  # raw 0-10; Pitfall 2
        track.rating_changed_at = rating_changed_at
        session.add(track)
        session.commit()


# -----------------------------------------------------------------------------
# Per-type handlers — all async, all touching DB via asyncio.to_thread.
# -----------------------------------------------------------------------------

async def handle_rating_changed(event: RatingChangedEvent) -> None:
    """RATE-03: update Track.user_rating + rating_changed_at."""
    if event.plex_rating_key is None:
        logger.warning("RatingChangedEvent without ratingKey; skipping")
        return
    await asyncio.to_thread(
        _update_track_rating_sync,
        event.plex_rating_key,
        event.new_rating,
        datetime.now(timezone.utc).isoformat(),
    )


async def handle_track_played(event: TrackPlayedEvent) -> None:
    """Stub for Plan 02. Phase 5 logs only — Plan 02 implements view_count + last_viewed_at."""
    logger.info(
        "TrackPlayed event received for ratingKey=%s; deferred to Plan 02",
        event.plex_rating_key,
    )


async def handle_library_added(event: LibraryAddedEvent) -> None:
    """Phase 5 logs only (Pitfall 8). Phase 8 will act on it (Lidarr enrichment etc.)."""
    logger.info(
        "LibraryAdded event received: ratingKey=%s sectionID=%s",
        event.plex_rating_key,
        event.library_section_id,
    )


async def handle_webhook_test(event: WebhookTestEvent) -> None:
    """Wizard test indicator reads from webhook_test_state. No DB work here."""
    logger.info("WebhookTest event received at %s", event.received_at)


# -----------------------------------------------------------------------------
# Dispatch entry point — called by event_bus._dispatch_loop.
# -----------------------------------------------------------------------------

async def dispatch_event(event: BaseEvent) -> None:
    """Single entry point: INSERT OR IGNORE dedupe, then route to handler.

    On dedupe drop, logs and returns silently. On handler error, records the
    error string on the EventLog row but does NOT raise (the dispatcher loop
    must keep running for subsequent events).
    """
    dedupe_key = _compute_dedupe_key(
        event.type,
        event.plex_rating_key,
        getattr(event, "new_rating", None),
        time.time(),
    )

    rowcount = await asyncio.to_thread(
        _insert_event_log_sync,
        event.source,
        event.type,
        event.plex_rating_key,
        dedupe_key,
        event.received_at,
        event.raw_payload,
    )
    if rowcount == 0:
        logger.debug(
            "Dedupe drop: %s key=%s...", event.type, dedupe_key[:12]
        )
        return

    # Match dispatch on event.type (equivalent to Python 3.10+ `match event.type`).
    handler_error: Optional[str] = None
    try:
        if event.type == "rating_changed":
            await handle_rating_changed(event)
        elif event.type == "track_played":
            await handle_track_played(event)
        elif event.type == "library_added":
            await handle_library_added(event)
        elif event.type == "webhook_test":
            await handle_webhook_test(event)
        else:
            logger.warning("Unhandled event type: %s", event.type)
    except Exception as exc:
        handler_error = f"{type(exc).__name__}: {exc}"[:500]
        logger.exception("Handler failed for %s", event.type)

    await asyncio.to_thread(_mark_event_processed_sync, dedupe_key, handler_error)
