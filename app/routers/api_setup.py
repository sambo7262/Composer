"""Phase 6 wizard router (D-06, D-07, D-08; WIZ-01..07).

Multi-step HTMX wizard at /setup. State persisted in SetupState (single row id=1).
Server returns HX-Redirect headers to advance between steps; refresh-safe; back/forward works.

Step 3's refinement loop is the user-flagged "core of the app" (06-CONTEXT.md):
LLM proposes -> user types feedback -> LLM refines -> repeat up to 10 turns ->
"Looks good" commits. The HTMX swap inside step 3 uses morph:innerHTML on
#proposal-cards (Pitfall 15 / D-09 alpine-morph) so each card's Alpine x-data
state survives swaps.

Form parsing: Annotated[str, Form()] + json.loads (NEVER pydantic.Json[Model] --
D-05 / FastAPI #10997).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, func, select

from app.database import get_engine, get_session
from app.models.llm_usage import LLMUsage
from app.models.settings import ServiceConfig
from app.models.track import Track
from app.models.vibe import ManagedPlaylist, SetupState, TrackVibe, Vibe

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["setup"])


# ---------------------------------------------------------------------------
# Lazy-import shims — keep tests monkeypatchable + avoid circular deps.
# Mirrors taste_profile_service.py:312-315 + vibe_clusterer.py:678 patterns.
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


# ---------------------------------------------------------------------------
# Module-level finalize status — mirrors backfill_service._backfill_status.
# Used by GET /api/setup/finalize/status (Plan 03 Task 3).
# ---------------------------------------------------------------------------

@dataclass
class FinalizeStatus:
    state: str = "idle"  # idle | running | completed | failed
    created: int = 0
    total: int = 0
    last_error: Optional[str] = None


_finalize_status: FinalizeStatus = FinalizeStatus()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_create_setup_state(session: Session) -> SetupState:
    """Single-row id=1 pattern (D-08)."""
    state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
    if state is None:
        state = SetupState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def _is_webhook_already_configured(session: Session) -> bool:
    """D-06 conditional Step 2 check: skip when ServiceConfig.url for webhook is set."""
    row = session.exec(
        select(ServiceConfig).where(ServiceConfig.service_name == "webhook")
    ).first()
    return row is not None and bool(row.url)


def _count_rated_tracks(session: Session) -> int:
    """SELECT COUNT(*) FROM Track WHERE user_rating > 0."""
    n = session.exec(
        select(func.count()).select_from(Track).where(Track.user_rating > 0)  # type: ignore[arg-type]
    ).one()
    if isinstance(n, tuple):
        n = n[0]
    return int(n)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_llm_call_id(session: Session, purpose_prefix: str) -> Optional[int]:
    """Return the most recent LLMUsage.id for purposes starting with purpose_prefix."""
    row = session.exec(
        select(LLMUsage)
        .where(LLMUsage.purpose.like(f"{purpose_prefix}%"))  # type: ignore[union-attr]
        .order_by(LLMUsage.id.desc())  # type: ignore[union-attr]
    ).first()
    return row.id if row else None


def _render_proposal_cards(
    request: Request, proposals_obj, refinement_turn_count: int
) -> HTMLResponse:
    """Render the proposal-cards container partial (HTMX morph swap target).

    Returns inner HTML for the #proposal-cards div + the refinement input.
    """
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/proposal_cards_swap.html",
        {
            "draft_proposals": proposals_obj,
            "refinement_turn_count": refinement_turn_count,
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/step1/submit")
async def step1_submit(session: Session = Depends(get_session)):
    """Step 1 -> webhook | proposing (D-06 conditional skip)."""
    state = _get_or_create_setup_state(session)
    n_rated = _count_rated_tracks(session)

    # D-10 / Pitfall 3 cold-start gate.
    if n_rated < 30:
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    if _is_webhook_already_configured(session):
        state.step = "proposing"
        next_url = "/setup/propose"
    else:
        state.step = "webhook"
        next_url = "/setup/webhook"

    if state.started_at is None:
        state.started_at = _now_iso()
    session.add(state)
    session.commit()

    return Response(status_code=204, headers={"HX-Redirect": next_url})


@router.get("/rated-count", response_class=HTMLResponse)
async def rated_count(request: Request, session: Session = Depends(get_session)):
    """Returns the rated-count partial (HTMX swap for cold-start Refresh button)."""
    n_rated = _count_rated_tracks(session)
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/rated_count.html",
        {"n_rated": n_rated},
    )


@router.post("/step2/submit")
async def step2_submit(session: Session = Depends(get_session)):
    """Step 2 -> proposing."""
    state = _get_or_create_setup_state(session)
    state.step = "proposing"
    session.add(state)
    session.commit()
    return Response(status_code=204, headers={"HX-Redirect": "/setup/propose"})


@router.post("/propose/init", response_class=HTMLResponse)
async def propose_init(
    request: Request,
    forced_k: Optional[int] = Form(None),
    session: Session = Depends(get_session),
):
    """Initial cluster proposal (D-13 force-k picker on initial run only).

    Idempotent: if SetupState.draft_proposals_json is already populated AND
    forced_k is None, returns the existing cards WITHOUT calling the LLM
    (cost guard — T-06-03-07 mitigation).
    """
    from app.services.vibe_clusterer import VibeProposalSet

    state = _get_or_create_setup_state(session)

    if state.draft_proposals_json and forced_k is None:
        # Idempotent re-render: reuse existing proposals (cost guard).
        existing = VibeProposalSet.model_validate_json(state.draft_proposals_json)
        return _render_proposal_cards(request, existing, state.refinement_turn_count)

    proposals = await initial_cluster_proposal(forced_k=forced_k)

    state.draft_proposals_json = proposals.model_dump_json()
    state.refinement_turn_count = 0
    state.last_llm_call_id = _latest_llm_call_id(session, "vibe_clustering_initial")
    session.add(state)
    session.commit()

    return _render_proposal_cards(request, proposals, 0)


@router.post("/refine", response_class=HTMLResponse)
async def refine(
    request: Request,
    message: Annotated[str, Form()],
    session: Session = Depends(get_session),
):
    """One conversational refinement turn (D-04 / D-12 / D-34)."""
    from app.services.vibe_clusterer import VibeProposalSet

    state = _get_or_create_setup_state(session)

    # D-04 hard cap at 10. Re-render with cap copy + same prior proposals.
    if state.refinement_turn_count >= 10:
        prior = (
            VibeProposalSet.model_validate_json(state.draft_proposals_json)
            if state.draft_proposals_json
            else None
        )
        return _render_proposal_cards(request, prior, state.refinement_turn_count)

    if not state.draft_proposals_json:
        # Defensive: refine without an init -> render error banner.
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": "No initial proposal yet. Reload the page.",
                "refinement_turn_count": state.refinement_turn_count,
            },
        )

    prior = VibeProposalSet.model_validate_json(state.draft_proposals_json)

    try:
        proposals = await refine_proposals(prior, message, recluster_mode=False)
    except Exception as exc:  # noqa: BLE001 — surface any LLM failure to the user
        logger.exception("refine_proposals failed")
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": str(exc),
                "refinement_turn_count": state.refinement_turn_count,
                # Show the prior cards on failure so the user keeps progress.
                "draft_proposals": prior,
            },
        )

    state.draft_proposals_json = proposals.model_dump_json()
    state.refinement_turn_count = state.refinement_turn_count + 1
    state.last_llm_call_id = _latest_llm_call_id(session, "vibe_clustering_")
    session.add(state)
    session.commit()

    return _render_proposal_cards(request, proposals, state.refinement_turn_count)


@router.post("/finalize", response_class=HTMLResponse)
async def finalize(request: Request, session: Session = Depends(get_session)):
    """Finalize the wizard: create Vibe + ManagedPlaylist + TrackVibe + Plex playlists.

    Sequential under asyncio.Semaphore(1) per D-25 (NAS-friendly, single
    concurrent Plex mutation).
    """
    from app.services.settings_service import (
        get_decrypted_credential,
        get_setting,
    )
    from app.services.vibe_clusterer import VibeProposalSet

    global _finalize_status

    state = _get_or_create_setup_state(session)
    if not state.draft_proposals_json:
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            {
                "state": "failed",
                "created": 0,
                "total": 0,
                "error": "No proposals to finalize.",
            },
        )

    proposals_set = VibeProposalSet.model_validate_json(state.draft_proposals_json)

    # Plex creds.
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

    # Idempotent Retry support: skip vibes whose ManagedPlaylist already links.
    existing_managed_vibe_ids = set(
        row.vibe_id
        for row in session.exec(select(ManagedPlaylist)).all()
        if row.vibe_id is not None
    )

    targets = [
        p for p in proposals_set.proposals if p.action != "dropped"
    ]
    total = len(targets)

    _finalize_status = FinalizeStatus(state="running", created=0, total=total)

    sema = asyncio.Semaphore(1)  # D-25
    created_count = 0
    last_error: Optional[str] = None

    # Build the ratingKey lookup: rated_track_index -> rating_key.
    index_to_key = {
        row["index"]: row["rating_key"] for row in proposals_set.rated_track_index_map
    }

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
        vibe_id: int, member_keys: list[str], distance: float = 0.0
    ) -> None:
        with Session(get_engine()) as s2:
            for rk in member_keys:
                t = s2.exec(
                    select(Track).where(Track.plex_rating_key == rk)
                ).first()
                if t is None or t.id is None:
                    continue
                # Avoid double-insert (composite PK).
                existing = s2.exec(
                    select(TrackVibe).where(
                        TrackVibe.track_id == t.id, TrackVibe.vibe_id == vibe_id
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

    for prop in targets:
        # Resolve member rating keys from seed_track_indices (and any other
        # indices materialize_clusters added — currently same set).
        member_keys: list[str] = []
        for idx in prop.seed_track_indices:
            rk = index_to_key.get(idx)
            if rk:
                member_keys.append(str(rk))

        if not member_keys:
            logger.warning("Skipping proposal %r — no member rating keys", prop.name)
            continue

        try:
            async with sema:
                vibe_id = await asyncio.to_thread(_insert_vibe_sync, prop)
                playlist_name = f"Composer · {prop.name}"
                playlist_rk = await create_playlist(
                    plex_url, plex_token, playlist_name, member_keys, vibe_id
                )
                await asyncio.to_thread(
                    _link_managed_to_vibe_sync, playlist_rk, vibe_id
                )
                await asyncio.to_thread(
                    _insert_trackvibes_sync, vibe_id, member_keys
                )
            created_count += 1
            _finalize_status.created = created_count
        except Exception as exc:  # noqa: BLE001
            logger.exception("Finalize failed on proposal %r", prop.name)
            last_error = str(exc)
            break

    if created_count == total:
        state.step = "done"
        state.completed_at = _now_iso()
        session.add(state)
        session.commit()
        _finalize_status = FinalizeStatus(
            state="completed", created=created_count, total=total
        )
        out_state = "completed"
    else:
        # Partial failure: keep step at confirming/proposing for retry.
        if state.step != "confirming":
            state.step = "confirming"
            session.add(state)
            session.commit()
        _finalize_status = FinalizeStatus(
            state="failed",
            created=created_count,
            total=total,
            last_error=last_error,
        )
        out_state = "failed"

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/push_to_plex_banner.html",
        {
            "state": out_state,
            "created": created_count,
            "total": total,
            "error": last_error,
        },
    )


@router.get("/finalize/status", response_class=HTMLResponse)
async def finalize_status(request: Request):
    """HTMX-poll endpoint for the push_to_plex_banner running state."""
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/push_to_plex_banner.html",
        {
            "state": _finalize_status.state,
            "created": _finalize_status.created,
            "total": _finalize_status.total,
            "error": _finalize_status.last_error,
        },
    )


@router.post("/reset")
async def reset(session: Session = Depends(get_session)):
    """Reset SetupState; redirect to /setup."""
    state = _get_or_create_setup_state(session)
    state.step = "rating_source"
    state.draft_proposals_json = ""
    state.refinement_turn_count = 0
    state.last_llm_call_id = None
    session.add(state)
    session.commit()
    return Response(status_code=204, headers={"HX-Redirect": "/setup"})
