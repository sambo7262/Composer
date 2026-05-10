"""Phase 6 vibe service (D-15 - D-19; VIBE-02, VIBE-09, VIBE-10).

slot_track is the always-on hot path: every RatingChanged event with
new_rating > 0 fans into vibe membership and Plex playlist mutation within
seconds. unslot_track is the cleared path (10 to 0).

Per-track asyncio.Lock (D-16 / Pitfall 23) keyed on plex_rating_key —
concurrent rate-correct events for the same track serialize. The lock dict
is a ``weakref.WeakValueDictionary``: an entry survives only while a holder
(an ``async with lock:`` block, or a callsite with a strong reference)
keeps the lock alive. Once no holder remains, GC reclaims the entry. This
preserves the per-track serialization invariant under burst — a previous
LRU-eviction scheme could drop an in-use lock and let a second arrival
construct a fresh, unheld lock for the same key (WR-01).

Soft-membership rule (Pitfall 24 / VIBE-02): a track joins a second vibe
only when its distance to the second-closest centroid is within 1 std-dev of
the closest. Cap at 2 vibes per track.

Manual override stickiness (D-19): TrackVibe(assigned_by='manual') rows are
never overwritten by auto-slot. They survive re-cluster (Plan 04 D-22
preserves them by source-vibe matching).

D-17 retroactive path: if a rated track lacks audio features (Essentia
hasn't run yet), slot_track sets pending_slot_in=TRUE and returns. The
analysis_service post-track hook (Plan 02 extends it) calls
maybe_reslot_pending_track once features land. User never sees a rated
track miss its vibe.

Every PlexAPI call routes through plex_playlist_service (which itself wraps
in asyncio.to_thread). The static AST test
``tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async``
walks this module — direct PlexServer/fetchItem/etc calls in any async path
fail the test.
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np
from sqlmodel import Session, select

from app.database import get_engine
from app.models.track import Track
from app.models.vibe import ManagedPlaylist, SlotInLog, TrackVibe, Vibe
from app.services.plex_playlist_service import (
    remove_from_playlist,
    update_playlist_items,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state (D-16) — singleton lock dict + status
# ---------------------------------------------------------------------------

# WR-01: WeakValueDictionary so locks held by an `async with` block (strong
# refs on the awaiting coroutine's stack) keep themselves alive. An in-use
# lock cannot be evicted; once no holder remains, GC reclaims the entry.
_slot_in_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)
_STD_FLOOR = 1e-6


@dataclass
class SlotInResult:
    track_id: int
    rating_key: str
    primary_vibe_id: Optional[int]
    primary_distance: Optional[float]
    secondary_vibe_id: Optional[int]
    secondary_distance: Optional[float]
    pending: bool
    skipped_manual: bool = False


@dataclass
class VibeServiceStatus:
    state: str = "idle"
    last_slot_at: Optional[str] = None
    last_error: Optional[str] = None


_status: VibeServiceStatus = VibeServiceStatus()


def get_vibe_service_status() -> VibeServiceStatus:
    """Return the current in-memory vibe service status snapshot."""
    return _status


def _get_lock(rating_key: str) -> asyncio.Lock:
    """Per-track lock (D-16) backed by a WeakValueDictionary.

    Returns the existing lock for the key if any holder still references it,
    or creates and registers a new one. The caller's `async with lock:` block
    holds a strong reference for the duration of the critical section, so
    concurrent arrivals for the same key receive the SAME lock instance and
    serialize properly. Once all holders complete, GC reclaims the entry.

    WR-01 fix: the previous bounded OrderedDict could evict an in-use lock
    when the dict exceeded its cap, causing a second arrival to receive a
    fresh, unheld lock and race the original holder.
    """
    lock = _slot_in_locks.get(rating_key)
    if lock is None:
        lock = asyncio.Lock()
        _slot_in_locks[rating_key] = lock
    return lock


# ---------------------------------------------------------------------------
# Math helper — duplicated from vibe_clusterer for module independence (D-05)
# ---------------------------------------------------------------------------

def _z_score_normalize(
    values: np.ndarray, mean: np.ndarray, std: np.ndarray
) -> np.ndarray:
    """4-D z-score (D-05). std clamped to >= _STD_FLOOR.

    Duplicated from ``vibe_clusterer._z_score_normalize`` intentionally — keeps
    the two services importable without a circular cross-module dependency.
    Pure math, ~5 lines; the cost of duplication is negligible.
    """
    safe_std = np.where(std < _STD_FLOOR, _STD_FLOOR, std)
    return (values - mean) / safe_std


# ---------------------------------------------------------------------------
# Sync helpers — all called via asyncio.to_thread (D-09 invariant)
# ---------------------------------------------------------------------------

def _read_track_and_vibes_sync(
    rating_key: str,
) -> Tuple[Optional[Track], List[Vibe]]:
    """Read latest Track row + all active Vibes (D-16 — read latest from DB inside lock)."""
    with Session(get_engine()) as session:
        track = session.exec(
            select(Track).where(Track.plex_rating_key == rating_key)
        ).first()
        vibes = list(
            session.exec(select(Vibe).where(Vibe.is_active == True)).all()  # noqa: E712
        )
        # Detach: caller will read attributes after the session closes.
        if track is not None:
            session.expunge(track)
        for v in vibes:
            session.expunge(v)
        return track, vibes


def _read_track_by_id_sync(track_id: int) -> Optional[Track]:
    """Read a single Track by primary key — used by maybe_reslot_pending_track."""
    with Session(get_engine()) as session:
        track = session.get(Track, track_id)
        if track is not None:
            session.expunge(track)
        return track


def _read_existing_trackvibe_sync(track_id: int) -> List[TrackVibe]:
    """Return all TrackVibe rows for the given track."""
    with Session(get_engine()) as session:
        rows = list(
            session.exec(
                select(TrackVibe).where(TrackVibe.track_id == track_id)
            ).all()
        )
        for r in rows:
            session.expunge(r)
        return rows


def _upsert_trackvibe_sync(
    track_id: int, vibe_id: int, distance: float, assigned_by: str
) -> None:
    """INSERT or UPDATE the (track_id, vibe_id) row in TrackVibe."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        existing = session.exec(
            select(TrackVibe).where(
                TrackVibe.track_id == track_id,
                TrackVibe.vibe_id == vibe_id,
            )
        ).first()
        if existing is None:
            session.add(
                TrackVibe(
                    track_id=track_id,
                    vibe_id=vibe_id,
                    distance=float(distance),
                    assigned_at=now,
                    assigned_by=assigned_by,
                )
            )
        else:
            existing.distance = float(distance)
            existing.assigned_at = now
            existing.assigned_by = assigned_by
            session.add(existing)
        session.commit()


def _delete_trackvibes_sync(track_id: int) -> List[int]:
    """DELETE all TrackVibe rows for the track. Returns list of affected vibe_ids."""
    with Session(get_engine()) as session:
        rows = list(
            session.exec(
                select(TrackVibe).where(TrackVibe.track_id == track_id)
            ).all()
        )
        affected = [r.vibe_id for r in rows]
        for r in rows:
            session.delete(r)
        session.commit()
        return affected


def _set_pending_slot_in_sync(track_id: int, value: bool) -> None:
    """UPDATE Track SET pending_slot_in = value (1/0). D-17."""
    with Session(get_engine()) as session:
        track = session.get(Track, track_id)
        if track is None:
            return
        track.pending_slot_in = 1 if value else 0
        session.add(track)
        session.commit()


def _read_managed_playlist_for_vibe_sync(vibe_id: int) -> Optional[str]:
    """Return the Plex ratingKey for the ManagedPlaylist tied to this vibe (or None)."""
    with Session(get_engine()) as session:
        row = session.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.vibe_id == vibe_id)
        ).first()
        return row.plex_rating_key if row else None


def _read_vibe_member_rating_keys_sync(vibe_id: int) -> List[str]:
    """Return all Plex ratingKeys currently in a vibe (for additive playlist push)."""
    with Session(get_engine()) as session:
        rows = list(
            session.exec(
                select(Track.plex_rating_key)
                .join(TrackVibe, TrackVibe.track_id == Track.id)
                .where(TrackVibe.vibe_id == vibe_id)
            ).all()
        )
        # rows is a list of ratingKey strings (column-only select).
        return [str(r) for r in rows]


def _read_all_rated_track_keys_sync() -> List[str]:
    """Return all plex_rating_keys for tracks with user_rating > 0."""
    with Session(get_engine()) as session:
        rows = list(
            session.exec(
                select(Track.plex_rating_key).where(Track.user_rating > 0)
            ).all()
        )
        return [str(r) for r in rows]


def _get_plex_credentials_sync() -> Tuple[str, str]:
    """Read Plex url + token from settings_service (mirrors backfill_service)."""
    from app.services.settings_service import get_decrypted_credential, get_setting

    with Session(get_engine()) as session:
        setting = get_setting(session, "plex")
        if setting is None or not setting.is_configured:
            raise ValueError("Plex is not configured")
        token = get_decrypted_credential(session, "plex") or ""
        url = setting.url
        if not (url and token):
            raise ValueError("Plex URL or token missing")
        return url, token


def _insert_slot_in_log_sync(
    track_id: int,
    vibe_ids_json: str,
    distances_json: str,
    soft_membership_applied: bool,
    action: str,
    note: Optional[str],
) -> None:
    """Best-effort INSERT into the SlotInLog diagnostic feed (D-36).

    Phase 6 Plan 04 — feeds /debug/vibes "Last 20 slot-in decisions" table.
    Single INSERT, single commit. Caller wraps in try/except so a log-write
    failure NEVER breaks the slot/unslot path (Test 7 of Plan 04 enforces).
    """
    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        session.add(
            SlotInLog(
                timestamp=now,
                track_id=track_id,
                vibe_ids=vibe_ids_json,
                distances=distances_json,
                soft_membership_applied=soft_membership_applied,
                action=action,
                note=note,
            )
        )
        session.commit()


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def slot_track(rating_key: str) -> SlotInResult:
    """Slot a track into matching vibes (D-15 hot path).

    Per-track asyncio.Lock-guarded (D-16 / Pitfall 23 — read latest from DB
    inside the lock). Inside the lock:

    1. Read latest Track + active Vibes.
    2. user_rating in (None, 0) → return without slotting (caller should be
       calling unslot_track for the cleared case; safety net only).
    3. Audio features missing → set pending_slot_in=TRUE; return pending=True
       (D-17 retroactive path).
    4. No vibes exist → return primary_vibe_id=None (pre-wizard state).
    5. z-score normalize features + each vibe centroid; compute distances.
    6. Closest vibe; soft 2nd vibe within 1 std-dev margin (Pitfall 24); cap=2.
    7. Skip any (track, vibe) with assigned_by='manual' (D-19 sticky).
    8. UPSERT TrackVibe rows for chosen vibes.
    9. For each affected vibe with a ManagedPlaylist row, push the FULL member
       set additively via plex_playlist_service.update_playlist_items
       (Pitfall 5 — never removes user-added tracks).
    """
    lock = _get_lock(rating_key)
    async with lock:
        try:
            return await _slot_track_inner(rating_key)
        except Exception as exc:
            logger.exception("slot_track failed for ratingKey=%s", rating_key)
            _status.last_error = f"{type(exc).__name__}: {exc}"[:500]
            raise


async def _slot_track_inner(rating_key: str) -> SlotInResult:
    track, vibes = await asyncio.to_thread(_read_track_and_vibes_sync, rating_key)

    if track is None:
        logger.info("slot_track: unknown ratingKey=%s; skipping", rating_key)
        return SlotInResult(
            track_id=-1,
            rating_key=rating_key,
            primary_vibe_id=None,
            primary_distance=None,
            secondary_vibe_id=None,
            secondary_distance=None,
            pending=False,
        )

    # 2. Cleared rating safety net.
    if track.user_rating in (None, 0, 0.0):
        return SlotInResult(
            track_id=track.id or -1,
            rating_key=rating_key,
            primary_vibe_id=None,
            primary_distance=None,
            secondary_vibe_id=None,
            secondary_distance=None,
            pending=False,
        )

    # 3. Missing audio features → pending_slot_in=TRUE; D-17 path.
    feats = (track.energy, track.tempo, track.danceability, track.valence)
    if any(f is None for f in feats):
        await asyncio.to_thread(_set_pending_slot_in_sync, track.id, True)
        return SlotInResult(
            track_id=track.id,
            rating_key=rating_key,
            primary_vibe_id=None,
            primary_distance=None,
            secondary_vibe_id=None,
            secondary_distance=None,
            pending=True,
        )

    # 4. No vibes yet — pre-wizard state.
    if not vibes:
        logger.debug(
            "No vibes yet for ratingKey=%s; rating recorded; will slot after wizard.",
            rating_key,
        )
        return SlotInResult(
            track_id=track.id,
            rating_key=rating_key,
            primary_vibe_id=None,
            primary_distance=None,
            secondary_vibe_id=None,
            secondary_distance=None,
            pending=False,
        )

    # Filter to vibes with complete centroid columns.
    usable_vibes: List[Vibe] = []
    for v in vibes:
        if (
            v.centroid_energy is None
            or v.centroid_tempo is None
            or v.centroid_danceability is None
            or v.centroid_valence is None
        ):
            continue
        usable_vibes.append(v)
    if not usable_vibes:
        return SlotInResult(
            track_id=track.id,
            rating_key=rating_key,
            primary_vibe_id=None,
            primary_distance=None,
            secondary_vibe_id=None,
            secondary_distance=None,
            pending=False,
        )

    # 5. Build per-dimension mean/std from the vibe centroids; z-score
    # normalize the track's features and each vibe centroid in the same
    # basis. (D-05) Spreads are used as a crude per-dimension std anchor;
    # in z-score space the soft-membership margin is 1.0 by construction.
    centroid_matrix = np.array(
        [
            [v.centroid_energy, v.centroid_tempo, v.centroid_danceability, v.centroid_valence]
            for v in usable_vibes
        ],
        dtype=float,
    )
    mean = centroid_matrix.mean(axis=0)
    std = centroid_matrix.std(axis=0)

    track_features = np.array(list(feats), dtype=float)
    track_norm = _z_score_normalize(track_features, mean, std)

    # 6. Distance per vibe + sort ascending.
    distances: List[Tuple[Vibe, float]] = []
    for v, raw_centroid in zip(usable_vibes, centroid_matrix):
        v_norm = _z_score_normalize(raw_centroid, mean, std)
        d = float(np.linalg.norm(track_norm - v_norm))
        distances.append((v, d))
    distances.sort(key=lambda pair: pair[1])

    closest_vibe, closest_distance = distances[0]
    secondary_vibe = None
    secondary_distance = None
    chosen: List[Tuple[Vibe, float]] = [(closest_vibe, closest_distance)]
    if len(distances) >= 2:
        candidate_v, candidate_d = distances[1]
        # Soft-margin: in z-score space, 1 std-dev = 1.0 by construction.
        if candidate_d <= closest_distance + 1.0:
            chosen.append((candidate_v, candidate_d))
            secondary_vibe = candidate_v
            secondary_distance = candidate_d
    # Cap at 2 — never insert a third vibe even if margin would allow.

    # 7. Manual stickiness — read existing TrackVibe rows for this track.
    existing_tv = await asyncio.to_thread(
        _read_existing_trackvibe_sync, track.id
    )
    manual_vibe_ids = {tv.vibe_id for tv in existing_tv if tv.assigned_by == "manual"}

    skipped_manual_flag = False
    for chosen_v, chosen_d in chosen:
        if chosen_v.id in manual_vibe_ids:
            skipped_manual_flag = True
            continue
        await asyncio.to_thread(
            _upsert_trackvibe_sync, track.id, chosen_v.id, chosen_d, "auto-slot"
        )

    # 8. Push to Plex playlists — additive (Pitfall 5).
    affected_vibe_ids = {chosen_v.id for chosen_v, _ in chosen}
    affected_vibe_ids.update(manual_vibe_ids & {chosen_v.id for chosen_v, _ in chosen})
    if affected_vibe_ids:
        try:
            url, token = await asyncio.to_thread(_get_plex_credentials_sync)
        except ValueError as exc:
            logger.warning("Skipping Plex playlist push: %s", exc)
            url, token = None, None

        if url and token:
            for vibe_id in affected_vibe_ids:
                playlist_key = await asyncio.to_thread(
                    _read_managed_playlist_for_vibe_sync, vibe_id
                )
                if playlist_key is None:
                    continue
                member_keys = await asyncio.to_thread(
                    _read_vibe_member_rating_keys_sync, vibe_id
                )
                try:
                    await update_playlist_items(
                        url, token, playlist_key, member_keys
                    )
                except Exception:
                    # Sanitized at the boundary by plex_playlist_service.
                    logger.exception(
                        "update_playlist_items failed for vibe_id=%s", vibe_id
                    )

    _status.last_slot_at = datetime.now(timezone.utc).isoformat()

    # Phase 6 Plan 04 (D-36) — best-effort SlotInLog write for the
    # /debug/vibes "Last 20 slot-in decisions" feed. Wrapped in try/except so
    # a log-write failure NEVER breaks the slot path (Plan 04 Task 1 Test 7).
    try:
        import json as _json
        logged_vibe_ids = [closest_vibe.id]
        logged_distances = [closest_distance]
        if secondary_vibe is not None and secondary_distance is not None:
            logged_vibe_ids.append(secondary_vibe.id)
            logged_distances.append(secondary_distance)
        await asyncio.to_thread(
            _insert_slot_in_log_sync,
            track.id,
            _json.dumps(logged_vibe_ids),
            _json.dumps(logged_distances),
            secondary_vibe is not None,
            "slot",
            None,
        )
    except Exception:
        logger.exception("SlotInLog insert failed; slot succeeded")

    return SlotInResult(
        track_id=track.id,
        rating_key=rating_key,
        primary_vibe_id=closest_vibe.id,
        primary_distance=closest_distance,
        secondary_vibe_id=(secondary_vibe.id if secondary_vibe else None),
        secondary_distance=secondary_distance,
        pending=False,
        skipped_manual=skipped_manual_flag,
    )


async def unslot_track(rating_key: str) -> None:
    """Remove a track from ALL vibes + each affected Plex playlist (D-18 / VIBE-10).

    Per-track lock applies (same lock as slot_track). Removes ALL TrackVibe
    rows including manual ones — rating cleared = full removal.
    """
    lock = _get_lock(rating_key)
    async with lock:
        try:
            await _unslot_track_inner(rating_key)
        except Exception as exc:
            logger.exception("unslot_track failed for ratingKey=%s", rating_key)
            _status.last_error = f"{type(exc).__name__}: {exc}"[:500]
            raise


async def _unslot_track_inner(rating_key: str) -> None:
    track, _vibes = await asyncio.to_thread(_read_track_and_vibes_sync, rating_key)
    if track is None:
        return
    existing_tv = await asyncio.to_thread(
        _read_existing_trackvibe_sync, track.id
    )
    if not existing_tv:
        return

    affected_vibe_ids = [tv.vibe_id for tv in existing_tv]

    try:
        url, token = await asyncio.to_thread(_get_plex_credentials_sync)
    except ValueError as exc:
        logger.warning("Skipping Plex remove on unslot: %s", exc)
        url, token = None, None

    if url and token:
        for vibe_id in affected_vibe_ids:
            playlist_key = await asyncio.to_thread(
                _read_managed_playlist_for_vibe_sync, vibe_id
            )
            if playlist_key is None:
                continue
            try:
                await remove_from_playlist(
                    url, token, playlist_key, rating_key
                )
            except Exception:
                logger.exception(
                    "remove_from_playlist failed for vibe_id=%s key=%s",
                    vibe_id, rating_key,
                )

    await asyncio.to_thread(_delete_trackvibes_sync, track.id)

    # Phase 6 Plan 04 (D-36) — best-effort SlotInLog write on unslot. Same
    # try/except convention as slot_track — log failure must NEVER break the
    # unslot path. Distances list is empty (no distance for removal).
    try:
        import json as _json
        await asyncio.to_thread(
            _insert_slot_in_log_sync,
            track.id,
            _json.dumps(affected_vibe_ids),
            _json.dumps([]),
            False,
            "unslot",
            None,
        )
    except Exception:
        logger.exception("SlotInLog insert failed; unslot succeeded")


async def maybe_reslot_pending_track(track_id: int) -> None:
    """D-17 retroactive slot-in. Called by analysis_service after each track.

    Reads Track by id; if pending_slot_in is False or user_rating is None / 0,
    return. Otherwise call slot_track and clear the flag on success.
    """
    track = await asyncio.to_thread(_read_track_by_id_sync, track_id)
    if track is None:
        return
    pending_flag = track.pending_slot_in or 0
    if not pending_flag:
        return
    if track.user_rating in (None, 0, 0.0):
        # Track lost its rating between events — leave the flag in place; the
        # next RatingChanged event will either re-slot or clear via unslot.
        return
    result = await slot_track(track.plex_rating_key)
    if result.pending is False:
        # Slot succeeded (or vibes don't exist yet). Clear the flag either
        # way — slot_track already handled the no-vibes-yet case.
        await asyncio.to_thread(_set_pending_slot_in_sync, track_id, False)


async def reslot_all_rated_tracks() -> int:
    """D-36 manual recovery — re-slot every Track with user_rating > 0.

    Bounded concurrency via semaphore (max 1 — keeps Plex API quiet on the
    NAS, matches D-25). Returns the count slotted.
    """
    sema = asyncio.Semaphore(1)
    keys = await asyncio.to_thread(_read_all_rated_track_keys_sync)
    count = 0
    for rk in keys:
        async with sema:
            try:
                await slot_track(rk)
            except Exception:
                logger.exception("reslot_all_rated_tracks: slot_track failed for %s", rk)
                continue
        count += 1
    return count
