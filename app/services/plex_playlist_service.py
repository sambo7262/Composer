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
from sqlalchemy import text

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
# Phase 7.1 follow-up — weekly Plex Suggestions playlist prune.
# ---------------------------------------------------------------------------

class PruneResult(BaseModel):
    """Outcome of :func:`prune_suggestions_playlist_to_mirror`.

    - ``removed``: ratingKeys removed from the Plex playlist on this call.
    - ``still_present_after_remove``: ratingKeys we asked to remove that are
      STILL on the Plex playlist after the post-remove verify (logged as a
      warning; no retry — removal retries are riskier than additive push).
    - ``mirror_size``: number of ratingKeys currently in SuggestionsMirror.
    - ``final_plex_count``: number of items in the Plex playlist after prune.
    """

    removed: List[str] = Field(default_factory=list)
    still_present_after_remove: List[str] = Field(default_factory=list)
    mirror_size: int = 0
    final_plex_count: int = 0


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

    # PlexAPI 4.18.1's fetchItem does naive URL concatenation when ekey is a
    # bare string without a leading `/` — `plex_url='http://host:32400'` +
    # ekey='77830' → `http://host:3240077830` → InvalidURL. Cast to int so
    # PlexAPI builds the canonical `/library/metadata/{int}` path internally.
    # (Bug introduced by Phase 7.1 D-D1's mirror of the deleted Phase 7 push
    # branch; managedplaylist.plex_rating_key is TEXT in SQLite, hence a str.)
    _playlist_key_int = int(playlist_rating_key)

    def _get_current_keys():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(_playlist_key_int)
        return {str(item.ratingKey) for item in playlist.items()}

    def _add_items(keys_to_add: list[str]):
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(_playlist_key_int)
        # Fetch each track item individually to avoid path-format coupling.
        items = [plex.fetchItem(int(k)) for k in keys_to_add]
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


# ---------------------------------------------------------------------------
# Phase 7.1 follow-up — weekly Plex Suggestions playlist prune.
#
# Background: ``update_playlist_items`` is ADDITIVE ONLY (Pitfall 5 — never
# remove tracks a user added). Phase 7.1's SQL refill drains the
# SuggestionsMirror on each play and refills it, but the Plex playlist still
# grows monotonically. This prune runs once a week (Sun 03:00 UTC, IMMEDIATELY
# before discovery_call_weekly) to collapse the Plex playlist down to the
# current SuggestionsMirror contents.
#
# Strict safety gate: ONLY ManagedPlaylist.kind='suggestions' may be touched.
# The SELECT filter alone enforces this, but we ALSO hard-assert ``mp.kind ==
# 'suggestions'`` before any Plex mutation. Belt-and-suspenders.
# ---------------------------------------------------------------------------


def _find_suggestions_managed_playlist_sync() -> ManagedPlaylist | None:
    """Return the single ManagedPlaylist row with kind='suggestions', if any.

    Mirrors :func:`app.services.suggestions_service._find_suggestions_managed_playlist_sync`
    so the prune helper does not depend on suggestions_service (avoids a
    circular import via suggestions_service → plex_playlist_service).
    """
    with Session(get_engine()) as session:
        return session.exec(
            select(ManagedPlaylist).where(
                ManagedPlaylist.kind == "suggestions"
            )
        ).first()


def _read_suggestions_mirror_rating_keys_sync() -> set[str]:
    """Return the set of plex_rating_keys currently in SuggestionsMirror.

    Joins SuggestionsMirror → Track to resolve track_id → plex_rating_key.
    Raw SQL mirrors the pattern used in
    :func:`app.services.suggestions_service._delete_mirror_row_sync` so we do
    not need to import the SuggestionsMirror SQLModel (which would pull in
    the suggestions module's full import graph).
    """
    with Session(get_engine()) as session:
        rows = session.execute(
            text(
                """
                SELECT t.plex_rating_key
                FROM suggestionsmirror sm
                JOIN track t ON t.id = sm.track_id
                """
            )
        ).all()
        return {str(r[0]) for r in rows if r[0] is not None}


# Sentinel for "Plex playlist not yet materialized" (mirror of the constant
# in suggestions_service). Hardcoded here to avoid a circular import.
_DEFERRED_PLEX_RATING_KEY_SENTINEL = ""


async def prune_suggestions_playlist_to_mirror(
    plex_url: str,
    plex_token: str,
) -> PruneResult:
    """Collapse the Composer · Suggestions Plex playlist down to mirror contents.

    Runs Sundays 03:00 UTC inside the combined weekly maintenance tick (see
    :func:`app.services.sync_scheduler._weekly_maintenance_tick`), immediately
    BEFORE :func:`app.services.suggestions_discovery.discovery_call_weekly`.
    The order matters: discovery picks land in a freshly-pruned playlist.

    Strict scope: only touches ``ManagedPlaylist.kind='suggestions'``. The
    SELECT filter is the first line of defense; the hard-assert
    ``mp.kind == 'suggestions'`` (raises :class:`PermissionError`) is the
    second. Every other playlist is left untouched.

    Pipeline:
      1. Find the suggestions ManagedPlaylist row. Missing → log warning + no-op
         (bootstrap not done yet).
      2. Hard-assert kind == 'suggestions'.
      3. Skip if plex_rating_key is the deferred-sentinel empty string
         (Plex playlist not yet materialized — first refill creates it).
      4. Read mirror rating-keys (set).
      5. Fetch current Plex playlist contents.
      6. ``to_remove = current_plex - mirror``. Empty → no-op.
      7. ``playlist.removeItems(items_to_remove)`` via to_thread.
      8. Re-fetch + compute ``still_present`` for the verify step. Warn if
         non-empty (NO retry — removal retries are riskier than additive push).
      9. Update ``ManagedPlaylist.last_pushed_at`` + ``track_count``.

    GAP-03 invariant: every ``fetchItem`` call casts rating keys to ``int``
    (PlexAPI 4.18.1 naive URL concat bug for bare-string ekeys).
    """
    mp = await asyncio.to_thread(_find_suggestions_managed_playlist_sync)
    if mp is None:
        logger.warning(
            "prune_suggestions_playlist_to_mirror: no ManagedPlaylist with "
            "kind='suggestions' (bootstrap not done yet); skipping."
        )
        return PruneResult()

    # Hard-assert (belt-and-suspenders — the SELECT filter already enforces).
    if mp.kind != "suggestions":
        raise PermissionError(
            "prune_suggestions_playlist_to_mirror refusing to mutate "
            f"ManagedPlaylist with kind={mp.kind!r} (expected 'suggestions'). "
            "This guard is a defensive second line — if it ever fires, the "
            "SELECT filter has been miswired."
        )

    if mp.plex_rating_key == _DEFERRED_PLEX_RATING_KEY_SENTINEL:
        logger.info(
            "prune_suggestions_playlist_to_mirror: Plex playlist not yet "
            "materialized (sentinel plex_rating_key=''); skipping. First "
            "refill will create the Plex playlist."
        )
        return PruneResult()

    if not plex_url or not plex_token:
        logger.info(
            "prune_suggestions_playlist_to_mirror: Plex not configured "
            "(plex_url or plex_token empty); skipping."
        )
        return PruneResult()

    mirror_keys = await asyncio.to_thread(
        _read_suggestions_mirror_rating_keys_sync
    )

    # GAP-03: cast to int once up-front so all fetchItem call sites use int.
    _playlist_key_int = int(mp.plex_rating_key)

    def _get_current_keys() -> set[str]:
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(_playlist_key_int)
        return {str(item.ratingKey) for item in playlist.items()}

    def _remove_items(keys_to_remove: list[str]) -> int:
        plex = PlexServer(plex_url, plex_token, timeout=30)
        playlist = plex.fetchItem(_playlist_key_int)
        # Fetch each track item individually with int cast (GAP-03).
        items = [plex.fetchItem(int(k)) for k in keys_to_remove]
        playlist.removeItems(items)
        return len(playlist.items())

    try:
        current_keys = await asyncio.to_thread(_get_current_keys)
        to_remove = sorted(current_keys - mirror_keys)

        if not to_remove:
            logger.info(
                "prune_suggestions_playlist_to_mirror: Plex playlist already "
                "a subset of mirror (current=%d, mirror=%d); no-op.",
                len(current_keys), len(mirror_keys),
            )
            # Still touch last_pushed_at so monitoring can see the prune ran.
            await asyncio.to_thread(
                _update_managed_playlist_sync,
                mp.plex_rating_key,
                len(current_keys),
            )
            return PruneResult(
                removed=[],
                still_present_after_remove=[],
                mirror_size=len(mirror_keys),
                final_plex_count=len(current_keys),
            )

        logger.info(
            "prune_suggestions_playlist_to_mirror: removing %d tracks "
            "(current=%d, mirror=%d).",
            len(to_remove), len(current_keys), len(mirror_keys),
        )
        await asyncio.to_thread(_remove_items, to_remove)

        # Post-remove verify (mirror of update_playlist_items post-push verify
        # in spirit, but WITHOUT retry — removal retries are riskier).
        after_keys = await asyncio.to_thread(_get_current_keys)
        still_present = sorted(set(to_remove) & after_keys)
        if still_present:
            logger.warning(
                "prune_suggestions_playlist_to_mirror: %d tracks STILL on "
                "Plex playlist after removeItems: %s — NOT retrying. User "
                "can manually clean up via Plexamp.",
                len(still_present), still_present,
            )

        await asyncio.to_thread(
            _update_managed_playlist_sync,
            mp.plex_rating_key,
            len(after_keys),
        )

        return PruneResult(
            removed=to_remove,
            still_present_after_remove=still_present,
            mirror_size=len(mirror_keys),
            final_plex_count=len(after_keys),
        )
    except PermissionError:
        raise
    except Exception as exc:
        raise type(exc)(_sanitize(str(exc), plex_token)) from None
