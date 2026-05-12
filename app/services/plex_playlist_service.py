"""Phase 6 Plex playlist service (D-24, D-27, OPS-06; VIBE-04, VIBE-12).

Composer-managed Plex playlist CRUD. Every public function wraps PlexAPI in
``asyncio.to_thread`` (D-09 / Pitfall 4 — enforced by the AST static test
``tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async``
which now also walks this module).

Hands-off rule (D-27 / OPS-06 / Pitfall 20): every read or write checks BOTH
markers — ``playlist.title.startswith('Composer · ')`` AND a ``ManagedPlaylist``
row exists. The :func:`is_managed_playlist` helper checks the DB-side marker;
callers verify the title prefix.

Pitfall 5 invariant: :func:`update_playlist_items` is ADDITIVE ONLY. Never
remove tracks the user added via Plexamp. The ONE Composer-driven remove path
is :func:`remove_from_playlist` (called only from
``vibe_service.unslot_track`` when a rating is cleared 10→0).

Pitfall 6 / VIBE-12 post-push verify: after every ``addItems`` the playlist
contents are re-fetched and diffed against intent; silently dropped tracks
are retried once and reported in :class:`ReconcileResult.silently_dropped`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import List

from plexapi.server import PlexServer
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.database import get_engine
from app.models.vibe import ManagedPlaylist

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic result shape (D-24)
# ---------------------------------------------------------------------------

class ReconcileResult(BaseModel):
    """Outcome of an :func:`update_playlist_items` call.

    - ``added``: ratingKeys newly added to the playlist on this call.
    - ``unchanged``: ratingKeys already present (intersection desired ∩ current).
    - ``silently_dropped``: ratingKeys we asked to add that are STILL missing
      after both the initial push and the one-shot retry (Pitfall 6).
    - ``retried``: ratingKeys re-attempted on the second push.
    """

    added: List[str] = Field(default_factory=list)
    unchanged: List[str] = Field(default_factory=list)
    silently_dropped: List[str] = Field(default_factory=list)
    retried: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Token sanitization helper (T-06-02-03)
# ---------------------------------------------------------------------------

def _sanitize(message: str, token: str) -> str:
    """Replace token (when non-empty) with [REDACTED] in any message string."""
    if not token:
        return message
    return message.replace(token, "[REDACTED]")


# ---------------------------------------------------------------------------
# Sync DB helpers — all called via asyncio.to_thread (D-09 invariant)
# ---------------------------------------------------------------------------

def _insert_managed_playlist_sync(
    plex_rating_key: str,
    composer_name: str,
    track_count: int,
    vibe_id: int | None = None,
) -> None:
    """Insert a ManagedPlaylist row marking the new playlist as Composer-managed."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        session.add(
            ManagedPlaylist(
                kind="vibe",
                vibe_id=vibe_id,
                plex_rating_key=plex_rating_key,
                composer_name=composer_name,
                last_pushed_at=now,
                track_count=track_count,
            )
        )
        session.commit()


def _update_managed_playlist_sync(
    plex_rating_key: str,
    track_count: int | None = None,
    composer_name: str | None = None,
) -> None:
    """Update last_pushed_at and optionally track_count / composer_name."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        row = session.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.plex_rating_key == plex_rating_key
            )
        ).first()
        if row is None:
            return
        row.last_pushed_at = now
        if track_count is not None:
            row.track_count = track_count
        if composer_name is not None:
            row.composer_name = composer_name
        session.add(row)
        session.commit()


def _delete_managed_playlist_sync(plex_rating_key: str) -> None:
    """Delete the ManagedPlaylist row — used by archive_playlist (D-21)."""
    with Session(get_engine()) as session:
        row = session.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.plex_rating_key == plex_rating_key
            )
        ).first()
        if row is None:
            return
        session.delete(row)
        session.commit()


# ---------------------------------------------------------------------------
# Public dual-marker helper (D-27 / OPS-06 / Pitfall 20)
# ---------------------------------------------------------------------------

def is_managed_playlist(plex_rating_key: str) -> bool:
    """Return True iff a :class:`ManagedPlaylist` row exists for the given key.

    This is the DB-side half of the dual-marker check. Callers MUST also verify
    that the Plex playlist's title starts with ``"Composer · "``. Both markers
    must hold or the playlist is left alone (legacy / user-renamed / archived).

    Pure synchronous helper — DB-only, no Plex round-trip.
    """
    with Session(get_engine()) as session:
        row = session.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.plex_rating_key == plex_rating_key
            )
        ).first()
        return row is not None


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def create_playlist(
    plex_url: str,
    plex_token: str,
    name: str,
    rating_keys: List[str],
    vibe_id: int | None = None,
) -> str:
    """Create a Composer-owned Plex playlist + persist :class:`ManagedPlaylist`.

    ``name`` MUST start with ``"Composer · "`` (raises :class:`ValueError`).
    Returns the new playlist's ratingKey as a string.

    Reuses the batch-fetchItems pattern from
    :func:`app.services.chat_service.push_playlist_to_plex` (line 562).
    Every PlexAPI call wrapped in :func:`asyncio.to_thread` per D-09.
    """
    if not name.startswith("Composer · "):
        raise ValueError(
            f"D-24 invariant: playlist name must start with 'Composer · '; got {name!r}"
        )
    if not rating_keys:
        raise ValueError("No tracks to push")

    def _create():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        key_str = ",".join(str(k) for k in rating_keys)
        tracks = plex.fetchItems(f"/library/metadata/{key_str}")
        new_pl = plex.createPlaylist(title=name, items=tracks)
        return str(new_pl.ratingKey), len(tracks)

    try:
        new_key, track_count = await asyncio.to_thread(_create)
    except Exception as exc:
        raise type(exc)(_sanitize(str(exc), plex_token)) from None

    await asyncio.to_thread(
        _insert_managed_playlist_sync, new_key, name, track_count, vibe_id
    )
    logger.info(
        "Created Composer-managed playlist key=%s name=%s tracks=%d",
        new_key, name, track_count,
    )
    return new_key


async def update_playlist_items(
    plex_url: str,
    plex_token: str,
    playlist_rating_key: str,
    desired_rating_keys: List[str],
) -> ReconcileResult:
    """Push ``desired_rating_keys`` ADDITIVELY (Pitfall 5) + post-push verify.

    Pre-flight: :func:`is_managed_playlist` MUST return True; else raises
    :class:`PermissionError` citing OPS-06 / Pitfall 20.

    Pipeline:
      1. Fetch current playlist contents via to_thread.
      2. delta = desired - current. NEVER remove tracks user added.
      3. addItems(delta) via to_thread.
      4. Re-fetch playlist contents (Pitfall 6 / VIBE-12 post-push verify).
      5. silently_dropped = desired - re-fetched. Retry once for those.
      6. Update ManagedPlaylist.last_pushed_at + track_count.
    """
    if not is_managed_playlist(playlist_rating_key):
        raise PermissionError(
            f"OPS-06 / Pitfall 20: playlist {playlist_rating_key} is not "
            f"Composer-managed (no ManagedPlaylist row); refusing to mutate."
        )

    desired_set = {str(k) for k in desired_rating_keys}

    def _get_current_keys():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(playlist_rating_key)
        return {str(item.ratingKey) for item in playlist.items()}

    def _add_items(keys_to_add: list[str]):
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(playlist_rating_key)
        # Fetch each track item individually to avoid path-format coupling.
        items = [plex.fetchItem(k) for k in keys_to_add]
        playlist.addItems(items)

    try:
        current_keys = await asyncio.to_thread(_get_current_keys)
        delta = list(desired_set - current_keys)
        unchanged = list(desired_set & current_keys)

        if delta:
            await asyncio.to_thread(_add_items, delta)

        # Post-push verify (Pitfall 6 / VIBE-12).
        actual_keys = await asyncio.to_thread(_get_current_keys)
        silently_dropped = list(desired_set - actual_keys)
        retried_keys: list[str] = []
        if silently_dropped:
            retried_keys = list(silently_dropped)
            await asyncio.to_thread(_add_items, retried_keys)
            actual_keys = await asyncio.to_thread(_get_current_keys)
            silently_dropped = list(desired_set - actual_keys)

        await asyncio.to_thread(
            _update_managed_playlist_sync,
            playlist_rating_key,
            len(actual_keys),
        )

        return ReconcileResult(
            added=sorted(delta),
            unchanged=sorted(unchanged),
            silently_dropped=sorted(silently_dropped),
            retried=sorted(retried_keys),
        )
    except PermissionError:
        raise
    except Exception as exc:
        raise type(exc)(_sanitize(str(exc), plex_token)) from None


async def remove_from_playlist(
    plex_url: str,
    plex_token: str,
    playlist_rating_key: str,
    rating_key: str,
) -> None:
    """Remove a SINGLE track from a Composer-managed playlist (D-18).

    Used by :func:`app.services.vibe_service.unslot_track` on rating cleared
    (10→0). This is the ONE Composer-driven remove path; document the
    convention so future phases don't accidentally use it as a precedent for
    other auto-removal.

    Pre-flight :func:`is_managed_playlist` applies.
    """
    if not is_managed_playlist(playlist_rating_key):
        raise PermissionError(
            f"OPS-06 / Pitfall 20: playlist {playlist_rating_key} is not "
            f"Composer-managed (no ManagedPlaylist row); refusing to mutate."
        )

    def _remove():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(playlist_rating_key)
        track_item = plex.fetchItem(rating_key)
        playlist.removeItems([track_item])
        return len(playlist.items())

    try:
        new_count = await asyncio.to_thread(_remove)
    except Exception as exc:
        raise type(exc)(_sanitize(str(exc), plex_token)) from None

    await asyncio.to_thread(
        _update_managed_playlist_sync, playlist_rating_key, new_count
    )


async def archive_playlist(
    plex_url: str,
    plex_token: str,
    playlist_rating_key: str,
    current_name: str,
    suffix: str = "(archived)",
) -> None:
    """Rename to ``{current_name} {suffix}`` + delete ManagedPlaylist row.

    Used by:

    - Re-cluster drop path (D-21 — Plan 04). 4-arg signature; default suffix
      ``"(archived)"`` is preserved for back-compat.
    - Phase 6.1 first-deploy migration (``app/main.py::run_phase_61_migration``)
      — same 4-arg shape; default suffix preserves byte-identical rename
      behavior. T5 regression guard test
      ``test_archive_playlist_default_suffix_unchanged`` covers this.
    - Phase 6.2 Plan 02 WIZ-08 "Start Over" reset (D-26). Caller passes a
      date-stamped suffix ``"(archived YYYY-MM-DD)"`` so repeated wizard
      resets do not collide on Plex with ``"(archived) (archived)"``.

    After this runs, the dual-marker check (D-27) stops Composer from
    managing the playlist; the user can manually delete the archived
    playlist in Plexamp.
    """
    if not is_managed_playlist(playlist_rating_key):
        raise PermissionError(
            f"OPS-06 / Pitfall 20: playlist {playlist_rating_key} is not "
            f"Composer-managed (no ManagedPlaylist row); refusing to rename."
        )

    new_title = f"{current_name} {suffix}"

    def _rename():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(playlist_rating_key)
        playlist.editTitle(new_title)

    try:
        await asyncio.to_thread(_rename)
    except Exception as exc:
        raise type(exc)(_sanitize(str(exc), plex_token)) from None

    await asyncio.to_thread(_delete_managed_playlist_sync, playlist_rating_key)
    logger.info(
        "Archived Composer-managed playlist key=%s renamed to %r",
        playlist_rating_key, new_title,
    )


async def rename_playlist(
    plex_url: str,
    plex_token: str,
    playlist_rating_key: str,
    new_name: str,
) -> None:
    """Single rename via ``editTitle``. ``new_name`` must start with ``Composer · ``."""
    if not new_name.startswith("Composer · "):
        raise ValueError(
            f"Rename target must start with 'Composer · '; got {new_name!r}"
        )
    if not is_managed_playlist(playlist_rating_key):
        raise PermissionError(
            f"OPS-06 / Pitfall 20: playlist {playlist_rating_key} is not "
            f"Composer-managed (no ManagedPlaylist row); refusing to rename."
        )

    def _rename():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(playlist_rating_key)
        playlist.editTitle(new_name)

    try:
        await asyncio.to_thread(_rename)
    except Exception as exc:
        raise type(exc)(_sanitize(str(exc), plex_token)) from None

    await asyncio.to_thread(
        _update_managed_playlist_sync,
        playlist_rating_key,
        None,
        new_name,
    )
