from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Session, select

from app.database import get_engine
from app.models.track import SyncState, Track
from app.services.plex_client import get_library_tracks, get_tracks_since
from app.services.settings_service import get_decrypted_credential, get_setting

logger = logging.getLogger(__name__)


class SyncStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SyncStatus:
    state: SyncStateEnum = SyncStateEnum.IDLE
    total_tracks: int = 0
    synced_tracks: int = 0
    last_synced: Optional[str] = None
    error: Optional[str] = None


_sync_status = SyncStatus()


def get_sync_status() -> SyncStatus:
    """Return the current in-memory sync status."""
    return _sync_status


def _upsert_tracks_sync(track_dicts: list[dict]) -> None:
    """Upsert a batch of tracks into the database (synchronous, called via to_thread)."""
    engine = get_engine()
    with Session(engine) as session:
        now = datetime.now(timezone.utc).isoformat()
        for td in track_dicts:
            statement = select(Track).where(
                Track.plex_rating_key == td["plex_rating_key"]
            )
            existing = session.exec(statement).first()
            if existing:
                existing.title = td["title"]
                existing.artist = td["artist"]
                existing.album = td["album"]
                existing.genre = td["genre"]
                existing.year = td["year"]
                existing.duration_ms = td["duration_ms"]
                existing.added_at = td["added_at"]
                existing.updated_at = td["updated_at"]
                existing.synced_at = now
                if "file_path" in td:
                    existing.file_path = td["file_path"]
                session.add(existing)
            else:
                track = Track(
                    plex_rating_key=td["plex_rating_key"],
                    title=td["title"],
                    artist=td["artist"],
                    album=td["album"],
                    genre=td["genre"],
                    year=td["year"],
                    duration_ms=td["duration_ms"],
                    added_at=td["added_at"],
                    updated_at=td["updated_at"],
                    synced_at=now,
                    file_path=td.get("file_path"),
                )
                session.add(track)
        session.commit()


async def _upsert_tracks(track_dicts: list[dict]) -> None:
    """Upsert tracks in a background thread to avoid blocking the event loop."""
    await asyncio.to_thread(_upsert_tracks_sync, track_dicts)


def _update_sync_state_sync(total_tracks: int) -> None:
    """Update the SyncState record in the database (synchronous)."""
    engine = get_engine()
    with Session(engine) as session:
        now = datetime.now(timezone.utc).isoformat()
        statement = select(SyncState)
        state = session.exec(statement).first()
        if state:
            state.last_sync_completed = now
            state.total_tracks = total_tracks
            session.add(state)
        else:
            state = SyncState(
                last_sync_started=now,
                last_sync_completed=now,
                total_tracks=total_tracks,
            )
            session.add(state)
        session.commit()


def _get_last_sync_completed_sync() -> Optional[str]:
    """Get last_sync_completed from SyncState table (synchronous)."""
    engine = get_engine()
    with Session(engine) as session:
        statement = select(SyncState)
        state = session.exec(statement).first()
        if state and state.last_sync_completed:
            return state.last_sync_completed
    return None


def _set_sync_started_sync() -> None:
    """Set last_sync_started in SyncState table (synchronous)."""
    engine = get_engine()
    with Session(engine) as session:
        now = datetime.now(timezone.utc).isoformat()
        statement = select(SyncState)
        state = session.exec(statement).first()
        if state:
            state.last_sync_started = now
            session.add(state)
        else:
            state = SyncState(last_sync_started=now)
            session.add(state)
        session.commit()


def _sanitize_error(error_msg: str, token: str) -> str:
    """Remove Plex token from error messages (T-02-02 mitigation)."""
    if token:
        error_msg = error_msg.replace(token, "[REDACTED]")
    return error_msg


async def run_sync() -> None:
    """Main sync entry point. Orchestrates full or delta sync with Plex.

    Prevents concurrent execution by checking _sync_status.state.
    Updates in-memory SyncStatus for UI progress tracking.
    """
    global _sync_status

    # T-02-03: Prevent concurrent sync execution
    if _sync_status.state == SyncStateEnum.RUNNING:
        return

    _sync_status = SyncStatus(state=SyncStateEnum.RUNNING)
    token = ""

    try:
        # Get Plex credentials from settings
        engine = get_engine()
        with Session(engine) as session:
            setting = get_setting(session, "plex")
            token = get_decrypted_credential(session, "plex") or ""
            if not setting or not token:
                _sync_status.state = SyncStateEnum.FAILED
                _sync_status.error = "Plex is not configured. Set up Plex in Settings first."
                return

            url = setting.url
            extra = setting.extra_config or {}
            library_id = extra.get("library_id", "")

        if not library_id:
            _sync_status.state = SyncStateEnum.FAILED
            _sync_status.error = "No Plex library selected. Re-configure Plex in Settings."
            return

        # Record sync start
        await asyncio.to_thread(_set_sync_started_sync)

        # Check for delta sync possibility
        last_completed = await asyncio.to_thread(_get_last_sync_completed_sync)

        if last_completed:
            # Try delta sync first
            delta_tracks, delta_count = await get_tracks_since(
                url, token, library_id, last_completed
            )
            if delta_count > 0:
                # Delta sync: upsert only new tracks
                await _upsert_tracks(delta_tracks)
                _sync_status.synced_tracks = delta_count

                # Get total from a quick check
                _, total = await get_library_tracks(
                    url, token, library_id, container_start=0, container_size=1
                )
                _sync_status.total_tracks = total

                now = datetime.now(timezone.utc).isoformat()
                _sync_status.state = SyncStateEnum.COMPLETED
                _sync_status.last_synced = now
                await asyncio.to_thread(_update_sync_state_sync, total)
                return
            else:
                # Delta returned 0 -- check if library actually has tracks
                _, total = await get_library_tracks(
                    url, token, library_id, container_start=0, container_size=1
                )
                if total == 0:
                    # Library genuinely empty
                    _sync_status.state = SyncStateEnum.COMPLETED
                    _sync_status.total_tracks = 0
                    _sync_status.last_synced = datetime.now(timezone.utc).isoformat()
                    await asyncio.to_thread(_update_sync_state_sync, 0)
                    return
                # Fall through to full sync

        # Full sync: paginate through all tracks
        batch_size = 200
        offset = 0
        total = None

        while True:
            batch, batch_total = await get_library_tracks(
                url, token, library_id,
                container_start=offset,
                container_size=batch_size,
            )
            if total is None:
                total = batch_total
                _sync_status.total_tracks = total

            await _upsert_tracks(batch)
            _sync_status.synced_tracks += len(batch)

            if len(batch) < batch_size or offset + len(batch) >= total:
                break
            offset += batch_size

        now = datetime.now(timezone.utc).isoformat()
        _sync_status.state = SyncStateEnum.COMPLETED
        _sync_status.last_synced = now
        await asyncio.to_thread(_update_sync_state_sync, total or 0)

    except Exception as exc:
        _sync_status.state = SyncStateEnum.FAILED
        _sync_status.error = _sanitize_error(str(exc), token)
        logger.exception("Sync failed")


def get_last_sync_info(session: Session) -> dict:
    """Query SyncState table and track count for UI display.

    Returns {"last_sync_completed": str|None, "total_tracks": int, "track_count": int}.
    """
    statement = select(SyncState)
    state = session.exec(statement).first()

    from sqlmodel import func
    track_count = session.exec(select(func.count()).select_from(Track)).one()

    return {
        "last_sync_completed": state.last_sync_completed if state else None,
        "total_tracks": state.total_tracks if state else 0,
        "track_count": track_count,
    }
