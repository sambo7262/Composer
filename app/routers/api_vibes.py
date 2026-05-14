"""Phase 6 Plan 04 vibes router (D-20, D-21, D-22, D-23, D-36; VIBE-11).

Five endpoints:

- POST /api/vibes/recluster/start  — enters re-cluster mode → /setup/propose
- POST /api/vibes/recluster/commit — diff-based reconciliation per D-21
- GET  /api/vibes/recluster/status — HTMX-poll banner (mirrors api_setup pattern)
- POST /api/vibes/reslot-all       — D-36 manual recovery from /debug/vibes
- GET  /api/vibes/last-llm-call    — D-36 "Re-show last cluster proposal"

Pitfall invariants inherited from Plan 02:

- Pitfall 5 (additive only): plex_playlist_service.update_playlist_items
  never removes user-added tracks.
- Pitfall 20 / OPS-06: every Plex playlist mutation is dual-marker-gated
  (Composer · prefix on the title + ManagedPlaylist row).
- D-23 idempotency: each commit operation try/except → log + continue;
  partial state is recoverable on re-run via the existing-Vibe-by-name
  short-circuit.
- D-25 (NAS-friendly): asyncio.Semaphore(1) wraps every Plex mutation.

Form parsing convention (D-05): NEVER use pydantic-Json inside Form() —
FastAPI bug #10997. The recluster endpoints take no form bodies; this is
documented for future extension.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, select

from app.database import get_engine, get_session
from app.models.llm_usage import LLMUsage
from app.models.track import Track
from app.models.vibe import (
    ManagedPlaylist,
    SetupState,
    SlotInLog,
    TrackVibe,
    Vibe,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vibes", tags=["vibes"])


# ---------------------------------------------------------------------------
# Lazy-import shims — keep tests monkeypatchable + avoid circular deps.
# Mirrors api_setup.py shim style.
# ---------------------------------------------------------------------------

def get_templates():
    """Lazy import to avoid circular dep with app.main (Pattern E)."""
    from app.main import templates
    return templates


async def initial_cluster_proposal(*args, **kwargs):
    from app.services.vibe_clusterer import (
        initial_cluster_proposal as _fn,
    )
    return await _fn(*args, **kwargs)


async def refine_proposals(*args, **kwargs):
    from app.services.vibe_clusterer import refine_proposals as _fn
    return await _fn(*args, **kwargs)


async def create_playlist(*args, **kwargs):
    from app.services.plex_playlist_service import create_playlist as _fn
    return await _fn(*args, **kwargs)


async def archive_playlist(*args, **kwargs):
    from app.services.plex_playlist_service import archive_playlist as _fn
    return await _fn(*args, **kwargs)


async def rename_playlist(*args, **kwargs):
    from app.services.plex_playlist_service import rename_playlist as _fn
    return await _fn(*args, **kwargs)


async def update_playlist_items(*args, **kwargs):
    from app.services.plex_playlist_service import update_playlist_items as _fn
    return await _fn(*args, **kwargs)


async def reslot_all_rated_tracks(*args, **kwargs):
    from app.services.vibe_service import reslot_all_rated_tracks as _fn
    return await _fn(*args, **kwargs)


async def slot_track(*args, **kwargs):
    from app.services.vibe_service import slot_track as _fn
    return await _fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Module-level recluster status — mirrors api_setup._finalize_status.
# ---------------------------------------------------------------------------

@dataclass
class ReclusterStatus:
    state: str = "idle"  # idle | running | completed | failed
    created: int = 0
    archived: int = 0
    renamed: int = 0
    total: int = 0
    last_error: Optional[str] = None


_recluster_status: ReclusterStatus = ReclusterStatus()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_or_create_setup_state(session: Session) -> SetupState:
    """Single-row id=1 pattern (mirrors api_setup helper)."""
    state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
    if state is None:
        state = SetupState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def _state_to_banner_payload() -> dict:
    """Map _recluster_status to the push_to_plex_banner partial context."""
    total = _recluster_status.total
    # Banner shows created+archived+renamed as "operations completed"; for
    # the simple banner use created as the headline count.
    done = (
        _recluster_status.created
        + _recluster_status.archived
        + _recluster_status.renamed
    )
    return {
        "state": _recluster_status.state,
        "created": done,
        "total": total,
        "error": _recluster_status.last_error,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/recluster/start")
async def recluster_start(session: Session = Depends(get_session)):
    """D-20: enter re-cluster mode → /setup/propose.

    Resets SetupState.draft_proposals_json (forces a fresh /api/setup/propose/init
    call), zeros refinement_turn_count, and sets recluster_mode=True so the
    LLM purpose switches to vibe_clustering_recluster (D-34) and /setup/confirm
    dispatches to /api/vibes/recluster/commit instead of /api/setup/finalize.
    """
    state = _get_or_create_setup_state(session)
    state.step = "proposing"
    state.draft_proposals_json = ""
    state.refinement_turn_count = 0
    state.recluster_mode = True
    state.last_llm_call_id = None
    if state.started_at is None:
        state.started_at = _now_iso()
    session.add(state)
    session.commit()

    return Response(
        status_code=204, headers={"HX-Redirect": "/setup/propose"}
    )


@router.post("/recluster/commit", response_class=HTMLResponse)
async def recluster_commit(
    request: Request, session: Session = Depends(get_session)
):
    """D-21 diff-based reconciliation for re-cluster.

    Reads SetupState.draft_proposals_json + the existing Vibe table; snapshots
    manual TrackVibe overrides per D-22; applies each VibeProposal sequentially
    under asyncio.Semaphore(1) per D-25 with try/except per D-23 idempotency:

      - keep / renamed_from: UPDATE Vibe in place; rename_playlist if name
        changed; update_playlist_items for additive member reconcile.
      - dropped: archive_playlist (deletes ManagedPlaylist row inside);
        SET Vibe.is_active=False (audit trail per D-21).
      - new: INSERT Vibe + create_playlist + INSERT TrackVibe rows.
      - merged_from: archive each source vibe + INSERT new Vibe + create_playlist.
      - split_from: archive source vibe + INSERT new Vibe + create_playlist.

    After commit: replay manual overrides (D-22). For each snapshot row, find
    the new vibe by name match (or via VibeProposal.source_vibe_ids); if found,
    INSERT TrackVibe(assigned_by="manual"). If no successor, write SlotInLog
    row with action="manual_override_lost" so the operator sees what was lost
    on /debug/vibes.

    Returns the push_to_plex_banner partial with the final state.
    """
    from app.services.settings_service import (
        get_decrypted_credential,
        get_setting,
    )
    from app.services.vibe_clusterer import VibeProposalSet

    global _recluster_status

    # WR-09: re-entrancy guard. The "Push to Plex" / Retry button on the
    # recluster banner is double-clickable; HTMX does not debounce. Two
    # concurrent commits would race on _recluster_status AND on the
    # name-based idempotency check (interleaved INSERTs can both pass the
    # check before either commits). While running, refuse the new call by
    # re-rendering the in-progress banner with HTTP 200.
    if _recluster_status.state == "running":
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            _state_to_banner_payload(),
        )

    state = _get_or_create_setup_state(session)
    if not state.recluster_mode:
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            {
                "state": "failed",
                "created": 0,
                "total": 0,
                "error": (
                    "This endpoint is for re-cluster only; use /api/setup/"
                    "finalize for initial wizard."
                ),
            },
        )
    if not state.draft_proposals_json:
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            {
                "state": "failed",
                "created": 0,
                "total": 0,
                "error": "No proposals to commit.",
            },
        )

    proposals_set = VibeProposalSet.model_validate_json(
        state.draft_proposals_json
    )

    plex_setting = get_setting(session, "plex")
    if plex_setting is None or not plex_setting.is_configured:
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            {
                "state": "failed",
                "created": 0,
                "total": 0,
                "error": "Plex is not configured.",
            },
        )
    plex_url = plex_setting.url
    plex_token = get_decrypted_credential(session, "plex") or ""

    # ----------- Snapshot manual overrides (D-22) -----------
    # Build map (track_id, source_vibe_name) -> (track_id, source_vibe_id, distance).
    manual_snapshot: List[dict] = []
    manual_rows = session.exec(
        select(TrackVibe).where(TrackVibe.assigned_by == "manual")
    ).all()
    for tv in manual_rows:
        v = session.exec(select(Vibe).where(Vibe.id == tv.vibe_id)).first()
        manual_snapshot.append(
            {
                "track_id": tv.track_id,
                "source_vibe_id": tv.vibe_id,
                "source_vibe_name": v.name if v else f"#{tv.vibe_id}",
                "distance": tv.distance,
            }
        )

    # ----------- Capture existing active vibes -----------
    existing_vibes_by_id = {
        v.id: v
        for v in session.exec(select(Vibe).where(Vibe.is_active == True)).all()  # noqa: E712
    }

    targets = list(proposals_set.proposals)
    total = len(targets)
    _recluster_status = ReclusterStatus(state="running", total=total)

    sema = asyncio.Semaphore(1)  # D-25 single concurrent Plex mutation
    last_error: Optional[str] = None

    # Build rated_track_index_map lookup for member resolution.
    index_to_key = {
        row["index"]: row["rating_key"]
        for row in proposals_set.rated_track_index_map
    }

    # ----------- Sync DB helpers (called via asyncio.to_thread) -----------

    def _update_existing_vibe_sync(
        vibe_id: int, prop, change_name: bool
    ) -> Optional[str]:
        """UPDATE existing Vibe row; return old name (for rename detection)."""
        with Session(get_engine()) as s2:
            row = s2.exec(select(Vibe).where(Vibe.id == vibe_id)).first()
            if row is None:
                return None
            old_name = row.name
            if change_name:
                row.name = prop.name
            if prop.description is not None:
                row.description = prop.description
            if prop.centroid:
                row.centroid_energy = prop.centroid.get("energy")
                row.centroid_tempo = prop.centroid.get("tempo")
                row.centroid_danceability = prop.centroid.get("danceability")
                row.centroid_valence = prop.centroid.get("valence")
            if prop.spread:
                row.spread_energy = prop.spread.get("energy")
                row.spread_tempo = prop.spread.get("tempo")
                row.spread_danceability = prop.spread.get("danceability")
                row.spread_valence = prop.spread.get("valence")
            if prop.silhouette is not None:
                row.silhouette_score = prop.silhouette
            row.centroid_recomputed_at = _now_iso()
            row.is_active = True  # ensure re-cluster reactivates if needed
            s2.add(row)
            s2.commit()
            return old_name

    def _archive_existing_vibe_sync(vibe_id: int):
        """SET is_active=False on the Vibe row; return (mp_key, mp_name) tuple
        so the caller can dispatch archive_playlist on the Plex side."""
        with Session(get_engine()) as s2:
            row = s2.exec(select(Vibe).where(Vibe.id == vibe_id)).first()
            if row is None:
                return (None, None)
            row.is_active = False
            s2.add(row)
            mp = s2.exec(
                select(ManagedPlaylist).where(
                    ManagedPlaylist.vibe_id == vibe_id
                )
            ).first()
            mp_key = mp.plex_rating_key if mp else None
            mp_name = mp.composer_name if mp else None
            s2.commit()
            return (mp_key, mp_name)

    def _insert_vibe_sync(prop) -> int:
        with Session(get_engine()) as s2:
            v = Vibe(
                name=prop.name,
                description=prop.description,
                centroid_energy=(prop.centroid or {}).get("energy"),
                centroid_tempo=(prop.centroid or {}).get("tempo"),
                centroid_danceability=(prop.centroid or {}).get("danceability"),
                centroid_valence=(prop.centroid or {}).get("valence"),
                spread_energy=(prop.spread or {}).get("energy"),
                spread_tempo=(prop.spread or {}).get("tempo"),
                spread_danceability=(prop.spread or {}).get("danceability"),
                spread_valence=(prop.spread or {}).get("valence"),
                silhouette_score=prop.silhouette,
                created_at=_now_iso(),
                centroid_recomputed_at=_now_iso(),
                is_active=True,
            )
            s2.add(v)
            s2.commit()
            s2.refresh(v)
            return v.id  # type: ignore[return-value]

    def _link_managed_to_vibe_sync(playlist_rk: str, vibe_id: int) -> None:
        with Session(get_engine()) as s2:
            mp = s2.exec(
                select(ManagedPlaylist).where(
                    ManagedPlaylist.plex_rating_key == playlist_rk
                )
            ).first()
            if mp is not None:
                mp.vibe_id = vibe_id
                s2.add(mp)
                s2.commit()

    def _insert_trackvibes_sync(
        vibe_id: int, member_keys: list, distance: float = 0.0
    ) -> None:
        with Session(get_engine()) as s2:
            for rk in member_keys:
                t = s2.exec(
                    select(Track).where(Track.plex_rating_key == rk)
                ).first()
                if t is None or t.id is None:
                    continue
                existing = s2.exec(
                    select(TrackVibe).where(
                        TrackVibe.track_id == t.id,
                        TrackVibe.vibe_id == vibe_id,
                    )
                ).first()
                if existing is not None:
                    continue
                s2.add(
                    TrackVibe(
                        track_id=t.id,
                        vibe_id=vibe_id,
                        distance=distance,
                        assigned_at=_now_iso(),
                        assigned_by="cluster",
                    )
                )
            s2.commit()

    def _read_existing_vibe_by_name_sync(name: str) -> Optional[int]:
        """Idempotency support (D-23) — skip already-created proposals on retry."""
        with Session(get_engine()) as s2:
            row = s2.exec(
                select(Vibe)
                .where(Vibe.name == name, Vibe.is_active == True)  # noqa: E712
            ).first()
            return row.id if row else None

    def _vibe_member_rating_keys_sync(vibe_id: int) -> List[str]:
        with Session(get_engine()) as s2:
            rows = list(
                s2.exec(
                    select(Track.plex_rating_key)
                    .join(TrackVibe, TrackVibe.track_id == Track.id)
                    .where(TrackVibe.vibe_id == vibe_id)
                ).all()
            )
            return [str(r) for r in rows]

    def _insert_manual_trackvibe_sync(
        track_id: int, vibe_id: int, distance: float
    ) -> None:
        with Session(get_engine()) as s2:
            existing = s2.exec(
                select(TrackVibe).where(
                    TrackVibe.track_id == track_id,
                    TrackVibe.vibe_id == vibe_id,
                )
            ).first()
            if existing is None:
                s2.add(
                    TrackVibe(
                        track_id=track_id,
                        vibe_id=vibe_id,
                        distance=distance,
                        assigned_at=_now_iso(),
                        assigned_by="manual",
                    )
                )
            else:
                existing.assigned_by = "manual"
                s2.add(existing)
            s2.commit()

    def _insert_lost_manual_log_sync(track_id: int, source_name: str) -> None:
        """D-22 — log a lost manual override to SlotInLog."""
        with Session(get_engine()) as s2:
            s2.add(
                SlotInLog(
                    timestamp=_now_iso(),
                    track_id=track_id,
                    vibe_ids="[]",
                    distances="[]",
                    soft_membership_applied=False,
                    action="manual_override_lost",
                    note=(
                        f"track {track_id} had manual override on "
                        f"'{source_name}' but vibe was dropped without successor"
                    ),
                )
            )
            s2.commit()

    # ----------- Track which existing vibe ids ended up where (for D-22 replay) -----------
    # Map: source_vibe_id -> new_vibe_id (or None if dropped without successor).
    source_to_new_vibe_id: dict = {}
    # Also map: source_vibe_name -> new_vibe_id (for name-match fallback).
    source_name_to_new_vibe_id: dict = {}

    # ----------- Apply each proposal -----------
    for prop in targets:
        try:
            async with sema:
                action = prop.action

                if action in ("keep", "renamed_from"):
                    # Find existing Vibe by source_vibe_ids[0] (D-21).
                    src_id = (
                        prop.source_vibe_ids[0] if prop.source_vibe_ids else None
                    )
                    existing = (
                        existing_vibes_by_id.get(src_id) if src_id else None
                    )
                    if existing is None:
                        # No source vibe found — defensive fallback: treat as "new".
                        logger.warning(
                            "recluster commit: action=%s but no source vibe id %s; "
                            "creating as new",
                            action, src_id,
                        )
                        action = "new"
                    else:
                        old_name = existing.name
                        change_name = (action == "renamed_from") or (
                            existing.name != prop.name
                        )
                        await asyncio.to_thread(
                            _update_existing_vibe_sync,
                            existing.id, prop, change_name,
                        )
                        if change_name:
                            try:
                                playlist_key = await asyncio.to_thread(
                                    _read_managed_playlist_for_vibe_sync_local,
                                    existing.id,
                                )
                                if playlist_key is not None:
                                    new_title = f"Composer · {prop.name}"
                                    await rename_playlist(
                                        plex_url, plex_token,
                                        playlist_key, new_title,
                                    )
                                    _recluster_status.renamed += 1
                            except Exception:
                                logger.exception(
                                    "rename_playlist failed for vibe_id=%s",
                                    existing.id,
                                )
                        # Additive member reconcile via update_playlist_items.
                        try:
                            playlist_key = await asyncio.to_thread(
                                _read_managed_playlist_for_vibe_sync_local,
                                existing.id,
                            )
                            if playlist_key is not None:
                                # Preserve existing members; this is additive.
                                member_keys = await asyncio.to_thread(
                                    _vibe_member_rating_keys_sync, existing.id
                                )
                                # Add seed members from the proposal (in case the
                                # LLM proposed new members for the kept vibe).
                                for idx in prop.seed_track_indices:
                                    rk = index_to_key.get(idx)
                                    if rk:
                                        member_keys.append(str(rk))
                                if member_keys:
                                    await update_playlist_items(
                                        plex_url, plex_token,
                                        playlist_key, member_keys,
                                    )
                        except Exception:
                            logger.exception(
                                "update_playlist_items failed for vibe_id=%s",
                                existing.id,
                            )
                        source_to_new_vibe_id[existing.id] = existing.id
                        source_name_to_new_vibe_id[old_name] = existing.id
                        # Track the (possibly new) name as well
                        source_name_to_new_vibe_id[prop.name] = existing.id
                        # CR-02: explicit continue prevents fall-through into
                        # the elif chain below. The fallback path (existing is
                        # None) does NOT continue — it reassigns action="new"
                        # so the elif action == "new" branch handles it.
                        continue

                elif action == "dropped":
                    src_id = (
                        prop.source_vibe_ids[0] if prop.source_vibe_ids else None
                    )
                    existing = (
                        existing_vibes_by_id.get(src_id) if src_id else None
                    )
                    if existing is not None:
                        result = await asyncio.to_thread(
                            _archive_existing_vibe_sync, existing.id
                        )
                        if result:
                            mp_key, mp_name = result
                            if mp_key is not None:
                                try:
                                    await archive_playlist(
                                        plex_url, plex_token,
                                        mp_key,
                                        mp_name or f"Composer · {existing.name}",
                                    )
                                except Exception:
                                    logger.exception(
                                        "archive_playlist failed for vibe_id=%s",
                                        existing.id,
                                    )
                        _recluster_status.archived += 1
                        # Mark in source map: dropped without successor.
                        source_to_new_vibe_id.setdefault(existing.id, None)

                elif action == "merged_from":
                    # Archive each source vibe, then INSERT one new Vibe.
                    for src_id in prop.source_vibe_ids:
                        existing = existing_vibes_by_id.get(src_id)
                        if existing is None:
                            continue
                        result = await asyncio.to_thread(
                            _archive_existing_vibe_sync, existing.id
                        )
                        if result:
                            mp_key, mp_name = result
                            if mp_key is not None:
                                try:
                                    await archive_playlist(
                                        plex_url, plex_token,
                                        mp_key,
                                        mp_name
                                        or f"Composer · {existing.name}",
                                    )
                                except Exception:
                                    logger.exception(
                                        "archive_playlist failed for src vibe %s",
                                        existing.id,
                                    )
                        _recluster_status.archived += 1
                    # INSERT new merged Vibe.
                    member_keys = []
                    for idx in prop.seed_track_indices:
                        rk = index_to_key.get(idx)
                        if rk:
                            member_keys.append(str(rk))
                    if member_keys:
                        # D-23 idempotency: skip if a same-named active Vibe already exists.
                        existing_id = await asyncio.to_thread(
                            _read_existing_vibe_by_name_sync, prop.name
                        )
                        if existing_id is None:
                            new_vibe_id = await asyncio.to_thread(
                                _insert_vibe_sync, prop
                            )
                            playlist_name = f"Composer · {prop.name}"
                            new_pl_key = await create_playlist(
                                plex_url, plex_token,
                                playlist_name, member_keys, new_vibe_id,
                            )
                            # WR-06 fix: create_playlist already inserted the
                            # ManagedPlaylist row with vibe_id; the previous
                            # _link_managed_to_vibe_sync follow-up was a
                            # no-op write — dropped.
                            await asyncio.to_thread(
                                _insert_trackvibes_sync,
                                new_vibe_id, member_keys,
                            )
                        else:
                            new_vibe_id = existing_id
                        _recluster_status.created += 1
                        # Map every source -> this new vibe (D-22 replay).
                        for src_id in prop.source_vibe_ids:
                            existing = existing_vibes_by_id.get(src_id)
                            if existing is not None:
                                source_to_new_vibe_id[existing.id] = new_vibe_id
                                source_name_to_new_vibe_id[
                                    existing.name
                                ] = new_vibe_id
                        source_name_to_new_vibe_id[prop.name] = new_vibe_id

                elif action == "split_from":
                    # Archive source (idempotent — only first split target archives).
                    src_id = (
                        prop.source_vibe_ids[0]
                        if prop.source_vibe_ids
                        else None
                    )
                    existing = (
                        existing_vibes_by_id.get(src_id) if src_id else None
                    )
                    # Idempotency: only archive on the FIRST split target. Use
                    # source_to_new_vibe_id keys as the "already processed"
                    # marker (set sentinel value 0 if not mapped to a new vibe yet).
                    if existing is not None and existing.id not in source_to_new_vibe_id:
                        result = await asyncio.to_thread(
                            _archive_existing_vibe_sync, existing.id
                        )
                        if result:
                            mp_key, mp_name = result
                            if mp_key is not None:
                                try:
                                    await archive_playlist(
                                        plex_url, plex_token,
                                        mp_key,
                                        mp_name
                                        or f"Composer · {existing.name}",
                                    )
                                except Exception:
                                    logger.exception(
                                        "archive_playlist failed for split src %s",
                                        existing.id,
                                    )
                        _recluster_status.archived += 1
                        # Mark source as "archived for split" — sentinel 0
                        # prevents re-archiving on the next split target. Will
                        # be overwritten with the actual new_vibe_id below.
                        source_to_new_vibe_id[existing.id] = 0
                    member_keys = []
                    for idx in prop.seed_track_indices:
                        rk = index_to_key.get(idx)
                        if rk:
                            member_keys.append(str(rk))
                    if member_keys:
                        existing_id = await asyncio.to_thread(
                            _read_existing_vibe_by_name_sync, prop.name
                        )
                        if existing_id is None:
                            new_vibe_id = await asyncio.to_thread(
                                _insert_vibe_sync, prop
                            )
                            playlist_name = f"Composer · {prop.name}"
                            new_pl_key = await create_playlist(
                                plex_url, plex_token,
                                playlist_name, member_keys, new_vibe_id,
                            )
                            # WR-06 fix: create_playlist already inserted the
                            # ManagedPlaylist row with vibe_id; the previous
                            # _link_managed_to_vibe_sync follow-up was a
                            # no-op write — dropped.
                            await asyncio.to_thread(
                                _insert_trackvibes_sync,
                                new_vibe_id, member_keys,
                            )
                        else:
                            new_vibe_id = existing_id
                        _recluster_status.created += 1
                        if existing is not None:
                            # Map source -> THIS split target (last-writer wins
                            # for replay; both targets are reasonable destinations).
                            source_name_to_new_vibe_id[
                                existing.name
                            ] = new_vibe_id
                        source_name_to_new_vibe_id[prop.name] = new_vibe_id

                elif action == "new":
                    member_keys = []
                    for idx in prop.seed_track_indices:
                        rk = index_to_key.get(idx)
                        if rk:
                            member_keys.append(str(rk))
                    if not member_keys:
                        logger.warning(
                            "recluster commit: 'new' proposal %r has no member "
                            "rating keys; skipping",
                            prop.name,
                        )
                        continue
                    # D-23 idempotency: skip if a same-named active Vibe already exists.
                    existing_id = await asyncio.to_thread(
                        _read_existing_vibe_by_name_sync, prop.name
                    )
                    if existing_id is None:
                        new_vibe_id = await asyncio.to_thread(
                            _insert_vibe_sync, prop
                        )
                        playlist_name = f"Composer · {prop.name}"
                        new_pl_key = await create_playlist(
                            plex_url, plex_token,
                            playlist_name, member_keys, new_vibe_id,
                        )
                        # WR-06 fix: create_playlist already inserted the
                        # ManagedPlaylist row with vibe_id; redundant
                        # _link_managed_to_vibe_sync call dropped.
                        await asyncio.to_thread(
                            _insert_trackvibes_sync,
                            new_vibe_id, member_keys,
                        )
                    else:
                        new_vibe_id = existing_id
                    _recluster_status.created += 1
                    source_name_to_new_vibe_id[prop.name] = new_vibe_id
        except Exception as exc:  # noqa: BLE001 — D-23 idempotency
            logger.exception(
                "recluster_commit: proposal %r failed", prop.name
            )
            last_error = str(exc)
            # Don't break — D-23 says continue + log. Operator retries via
            # POSTing back to /api/vibes/recluster/commit; idempotency by name.
            continue

    # ----------- Replay manual overrides (D-22) -----------
    for snap in manual_snapshot:
        # Try source_vibe_id mapping first (D-21 source_vibe_ids audit).
        new_vid = source_to_new_vibe_id.get(snap["source_vibe_id"])
        if new_vid is None:
            # Fallback: name match.
            new_vid = source_name_to_new_vibe_id.get(snap["source_vibe_name"])
        if new_vid is not None:
            try:
                await asyncio.to_thread(
                    _insert_manual_trackvibe_sync,
                    snap["track_id"], new_vid, snap["distance"],
                )
            except Exception:
                logger.exception(
                    "manual override replay failed for track %s",
                    snap["track_id"],
                )
        else:
            # No successor — log to SlotInLog as 'manual_override_lost'.
            try:
                await asyncio.to_thread(
                    _insert_lost_manual_log_sync,
                    snap["track_id"], snap["source_vibe_name"],
                )
            except Exception:
                logger.exception(
                    "manual_override_lost log write failed for track %s",
                    snap["track_id"],
                )

    # ----------- Finalize state machine -----------
    if last_error is None:
        _recluster_status.state = "completed"
    else:
        _recluster_status.state = "failed"
        _recluster_status.last_error = last_error

    # Always exit recluster mode after commit attempt (D-23 — operator can
    # re-enter via /settings if a retry is needed; the existing-vibe-by-name
    # short-circuit makes the second pass safe).
    state.recluster_mode = False
    state.step = "done"
    state.completed_at = _now_iso()
    session.add(state)
    session.commit()

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/push_to_plex_banner.html",
        _state_to_banner_payload(),
    )


@router.get("/recluster/status", response_class=HTMLResponse)
async def recluster_status(request: Request):
    """HTMX poll target while recluster commit is running."""
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/push_to_plex_banner.html",
        _state_to_banner_payload(),
    )


@router.post("/reslot-all", response_class=HTMLResponse)
async def reslot_all(request: Request):
    """D-36 manual recovery from /debug/vibes — fire-and-forget reslot.

    Behind hx-confirm in the UI per the Plan 04 threat model T-06-04-03.
    Returns a small status partial; actual completion is observable via the
    SlotInLog table on the same /debug/vibes page.
    """
    asyncio.create_task(reslot_all_rated_tracks())
    return HTMLResponse(
        '<div class="text-[14px] text-text-secondary">Reslotting tracks…</div>'
    )


@router.get("/last-llm-call", response_class=HTMLResponse)
async def last_llm_call(
    request: Request, session: Session = Depends(get_session)
):
    """D-36 'Re-show last cluster proposal' — surface the latest LLMUsage row.

    Phase 5 LLMUsage stores token counts + cost only (no prompt/response text);
    Plan 04 surfaces the aggregates the schema HAS. Future enhancement may
    persist truncated prompts/responses for full debug surface.
    """
    row = session.exec(
        select(LLMUsage)
        .where(
            LLMUsage.purpose.in_(  # type: ignore[union-attr]
                [
                    "vibe_clustering_initial",
                    "vibe_clustering_refine",
                    "vibe_clustering_recluster",
                ]
            )
        )
        .order_by(LLMUsage.id.desc())  # type: ignore[union-attr]
    ).first()

    if row is None:
        return HTMLResponse(
            '<div class="text-[14px] text-text-secondary">'
            'No cluster-proposal LLM calls recorded yet.'
            '</div>'
        )

    cost = float(row.cost_estimate_usd or 0.0)
    return HTMLResponse(
        f'<div class="bg-surface-elevated rounded p-3 text-[12px] font-mono">'
        f'<p class="text-text-secondary">Last cluster-proposal LLM call '
        f'(id={row.id}, purpose={row.purpose}, called_at={row.called_at}):</p>'
        f'<pre class="mt-2 text-text-primary whitespace-pre-wrap">'
        f'model: {row.model}\n'
        f'input_tokens: {row.input_tokens}\n'
        f'cache_creation_input_tokens: {row.cache_creation_input_tokens}\n'
        f'cache_read_input_tokens: {row.cache_read_input_tokens}\n'
        f'output_tokens: {row.output_tokens}\n'
        f'cost_estimate_usd: {cost:.4f}'
        f'</pre>'
        f'<p class="text-text-secondary mt-2">'
        f'Note: Phase 5 LLMUsage stores token counts + cost only — not the '
        f'system+user+response text. Future enhancement may persist truncated '
        f'prompts/responses for full debug surface.</p>'
        f'</div>'
    )


@router.get("/last-llm-call/progress")
async def last_llm_call_progress(
    request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    """D-NEW (Phase 6.2 hotfix 260512-k3n) — JSON progress endpoint.

    Returns the latest vibe_* LLMUsage row as JSON for the propose page's
    live "Calling Anthropic..." progress card. Polled every 2s by the
    Alpine.js component in partials/llm_progress_card.html while an
    /api/setup/propose/init or /api/setup/propose/refine HTMX request is
    in-flight.

    Why LIKE 'vibe_%' instead of an IN(...) allowlist: the existing HTML
    endpoint /last-llm-call filters on the legacy
    vibe_clustering_initial/refine/recluster trio (Phase 6 / D-36); Phase
    6.2 introduced new purpose labels (vibe_definitions_preamble,
    vibe_assign_pass1, vibe_assign_pass2). Matching the 'vibe_' prefix
    captures both generations without a code change next time labels evolve.

    Response shape (always JSON, never 4xx):
      - When at least one vibe_* row exists:
        {"purpose": str, "called_at": ISO8601 str, "elapsed_seconds": int}
      - When no rows yet (cold start):
        {"purpose": null, "called_at": null, "elapsed_seconds": null}

    NOTE: 'inflight' is intentionally client-local state (we don't store
    request-in-flight server-side because there is no cheap signal — a row
    appears AFTER the call returns, not during). The client polls only
    while it has an in-flight HTMX request.

    Phase 5 convention: tight except clauses only — the try/except below
    is scoped to datetime.fromisoformat for malformed legacy rows.
    """
    # Phase 7 Plan 02 (OPS-05) — extend the LIKE clause to also match
    # ``suggestions_%`` purposes so the polling card surfaces refill calls
    # alongside vibe_clustering / vibe_assign calls.
    from sqlalchemy import or_

    row = session.exec(
        select(LLMUsage)
        .where(or_(
            LLMUsage.purpose.like("vibe_%"),  # type: ignore[union-attr]
            LLMUsage.purpose.like("suggestions_%"),  # type: ignore[union-attr]
        ))
        .order_by(LLMUsage.id.desc())  # type: ignore[union-attr]
    ).first()

    if row is None:
        return JSONResponse(
            {"purpose": None, "called_at": None, "elapsed_seconds": None}
        )

    called_at_dt = None
    try:
        called_at_dt = datetime.fromisoformat(row.called_at)
    except ValueError:
        called_at_dt = None

    elapsed = None
    if called_at_dt is not None:
        now = datetime.now(timezone.utc)
        if called_at_dt.tzinfo is None:
            called_at_dt = called_at_dt.replace(tzinfo=timezone.utc)
        elapsed = max(0, int((now - called_at_dt).total_seconds()))

    return JSONResponse(
        {
            "purpose": row.purpose,
            "called_at": row.called_at,
            "elapsed_seconds": elapsed,
        }
    )


# ---------------------------------------------------------------------------
# Phase 7 Plan 02 (SUGG-10) — targeted vibe coverage CTA endpoint.
# ---------------------------------------------------------------------------


@router.post("/{vibe_id}/find-candidates", response_class=HTMLResponse)
async def find_vibe_candidates(
    request: Request,
    vibe_id: int,
    session: Session = Depends(get_session),
):
    """SUGG-10 — targeted refill restricted to ONE vibe (CTA only).

    The CTA only fires on explicit user tap (no automatic call) per
    CONTEXT.md "Claude's Discretion".

    CR-03 fix: fire-and-forget the LLM-heavy refill so the response returns
    immediately with the progress card. The polling endpoint
    ``/api/vibes/last-llm-call/progress`` (extended in Plan 02 to also match
    ``suggestions_%`` purposes) surfaces in-flight progress. Mirrors the
    ``/api/vibes/reslot-all`` pattern.
    """
    from app.services import suggestions_service

    vibe = session.exec(select(Vibe).where(Vibe.id == vibe_id)).first()
    if vibe is None:
        return HTMLResponse(status_code=404, content="Vibe not found")

    # CR-03 fix — fire-and-forget. Targeted refill (W2 single-vibe partition);
    # micro-batch size 15 per CONTEXT.md "Claude's Discretion" (smaller than
    # the whole-queue 30). Returning immediately lets the HTMX swap render
    # the progress card before the LLM round-trip completes.
    asyncio.create_task(
        suggestions_service.refill_suggestions_for_vibe(
            vibe_id, target=15,
        )
    )

    return get_templates().TemplateResponse(
        request,
        "partials/llm_progress_card.html",
        # CR-03 fix — `autostart` tells the card's Alpine init to call
        # start() immediately on swap-in. The default htmx:beforeRequest
        # listener already fired by the time this partial reaches the DOM,
        # so we cannot rely on it to start polling.
        {"autostart": True},
    )


# ---------------------------------------------------------------------------
# Local sync helper (used inside recluster_commit closures)
# ---------------------------------------------------------------------------

def _read_managed_playlist_for_vibe_sync_local(vibe_id: int) -> Optional[str]:
    """Module-level helper for recluster_commit closures (avoids late-binding)."""
    with Session(get_engine()) as s2:
        row = s2.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.vibe_id == vibe_id)
        ).first()
        return row.plex_rating_key if row else None
