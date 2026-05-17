"""Phase 8 Plan 04 — Discovery surface endpoints.

POST /api/discovery/{mb_id}/add           — D-D5 / DISC-05 one-click Lidarr add
POST /api/discovery/{mb_id}/dismiss       — D-D5 artist-only exclude
GET  /api/discovery/{mb_id}/status-row    — D-C3 lazy lifecycle poll (5min cache)
GET  /api/discovery/{mb_id}/top-tracks    — UAT iter: ListenBrainz top recordings (lazy-load on expand)

Best-effort error handling: never block the user from dismissing a row
even if the underlying handler raises (mirrors api_suggestions.dismiss_track).

D-C3 / Pitfall 14 — when a Lidarr add has sat in ``searching`` / ``pending``
for >48h, the status-row partial surfaces a soft warning chip ("No releases
found after Nd"). The chip context (``is_stale``, ``stale_days``) is computed
here so the template stays template-only.

Form parsing convention (D-05): the endpoints take no form bodies. Path
segments only. Any future Form-body extension MUST use
``Annotated[str, Form()]`` + ``json.loads`` (NEVER ``pydantic.Json[Model]``
inside Form — FastAPI bug #10997).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/discovery", tags=["discovery"])


# Lidarr statuses that trip the "added Nd ago, no releases" stale warning
# per D-C3 / Pitfall 14. Anything past "searching" / "pending" indicates
# Lidarr has made progress — no warning needed.
_STALE_WARNING_STATUSES = {"searching", "pending"}
_STALE_WARNING_THRESHOLD_HOURS = 48


def _compute_stale_context(
    added_at_iso: Optional[str], lidarr_status: Optional[str],
) -> dict:
    """D-C3 / Pitfall 14 — compute is_stale + stale_days for status-row template.

    Returns ``{"is_stale": bool, "stale_days": int | None}``. Conservative on
    parse failure: returns ``is_stale=False, stale_days=None``.

    Chip fires only when:
      - added_at_iso and lidarr_status are both present, AND
      - lidarr_status is in {"searching", "pending"}, AND
      - age > 48h.
    """
    if not added_at_iso or not lidarr_status:
        return {"is_stale": False, "stale_days": None}
    if lidarr_status.lower() not in _STALE_WARNING_STATUSES:
        return {"is_stale": False, "stale_days": None}
    try:
        added_dt = datetime.fromisoformat(added_at_iso)
        if added_dt.tzinfo is None:
            added_dt = added_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - added_dt
        hours = age.total_seconds() / 3600
        if hours <= _STALE_WARNING_THRESHOLD_HOURS:
            return {"is_stale": False, "stale_days": None}
        return {"is_stale": True, "stale_days": max(1, int(age.days))}
    except (ValueError, TypeError):
        return {"is_stale": False, "stale_days": None}


def get_templates():
    """Lazy import to avoid circular dep with app.main (Pattern E).

    Mirrors api_vibes.get_templates / api_setup.get_templates.
    """
    from app.main import templates
    return templates


@router.post("/{mb_id}/add", response_class=HTMLResponse)
async def add_to_lidarr(mb_id: str, request: Request) -> HTMLResponse:
    """D-D5 / DISC-05 — one-click Lidarr Add.

    Delegates to ``discovery_service.add_artist_to_lidarr`` which reads
    Plan 01's persisted profiles + root_dir from ServiceConfig.extras and
    invokes ``lidarr_client.add_artist``. Returns the status-row partial
    so the HTMX outerHTML swap replaces the artist card with the new
    status row.
    """
    from app.services import discovery_service
    try:
        result = await discovery_service.add_artist_to_lidarr(mb_id)
    except Exception:
        logger.exception("add_to_lidarr: handler raised for mb_id=%s", mb_id)
        result = {"success": False, "mb_id": mb_id, "error": "Internal error."}

    # On successful add, added_at = "now" so the stale chip never fires —
    # we still pass the context for template uniformity.
    stale_ctx = _compute_stale_context(
        datetime.now(timezone.utc).isoformat() if result.get("success") else None,
        result.get("lidarr_status"),
    )
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/discover_status_row.html",
        {**result, **stale_ctx},
    )


@router.post("/{mb_id}/dismiss", response_class=HTMLResponse)
async def dismiss_artist(mb_id: str, request: Request) -> HTMLResponse:
    """D-D5 — artist-only exclude.

    Writes DiscoveryDismissed(mb_id, dismissed_at). UNIQUE(mb_id) makes
    re-dismiss a no-op. Returns empty 200 so the HTMX outerHTML swap
    removes the card from view (immediate dismiss per D-B2).
    """
    from app.services import discovery_service
    try:
        await discovery_service.handle_dismiss_artist(mb_id)
    except Exception:
        logger.exception("dismiss_artist: handler raised for mb_id=%s", mb_id)
    return HTMLResponse(content="", status_code=200)


@router.get("/{mb_id}/status-row", response_class=HTMLResponse)
async def get_status_row(mb_id: str, request: Request) -> HTMLResponse:
    """D-C3 / D-D4 — lazy-poll Lidarr status for an in-flight DiscoveryAdd.

    Reads the DiscoveryAdd row by mb_id, invokes the cached
    ``get_lidarr_status_for_add`` helper, and renders the status row
    partial with the computed stale-warning context.
    """
    from app.services import discovery_service
    from app.models.discovery import DiscoveryAdd
    from app.database import get_engine
    from sqlmodel import Session, select
    import asyncio as _aio

    ctx: dict
    try:
        def _read():
            with Session(get_engine()) as session:
                return session.exec(
                    select(DiscoveryAdd)
                    .where(DiscoveryAdd.mb_id == mb_id)
                    .order_by(DiscoveryAdd.id.desc())  # type: ignore[union-attr]
                ).first()

        add_row = await _aio.to_thread(_read)
        if add_row is None:
            return HTMLResponse(content="", status_code=200)
        status = await discovery_service.get_lidarr_status_for_add(
            mb_id, add_row.lidarr_artist_id,
        )
        stale_ctx = _compute_stale_context(add_row.added_at, status)
        ctx = {
            "success": True,
            "mb_id": mb_id,
            "artist_name": add_row.artist_name,
            "lidarr_status": status,
            **stale_ctx,
        }
    except Exception:
        logger.exception("get_status_row: handler raised for mb_id=%s", mb_id)
        ctx = {
            "success": False,
            "mb_id": mb_id,
            "error": "Status unavailable.",
            "is_stale": False,
            "stale_days": None,
        }

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/discover_status_row.html",
        ctx,
    )


# ---------------------------------------------------------------------------
# Plan 05 ADDITION-1 — Manual "Run weekly tick now" trigger.
# ---------------------------------------------------------------------------
#
# POST /api/discovery/run-tick-now invokes the FULL weekly maintenance tick
# (prune → suggestions discovery → artist discovery → WeeklyCronState stamp)
# in a FastAPI BackgroundTask. Returns 202 immediately so the page poll can
# pick up the state transition.
#
# 409 if ``discovery_service.get_state().state == "running"`` — concurrent
# manual triggers are rejected to avoid double-running the cron.
#
# GET /api/discovery/tick-state — small JSON status endpoint the page polls
# every 5s. Read-only; never blocks. Same /debug/* gating (no public exposure
# beyond the existing pattern).
#
# IMPORTANT: routes placed BEFORE the ``/{mb_id}/...`` catch-all routes so
# FastAPI matches the literal paths first instead of treating "run-tick-now"
# / "tick-state" as an ``mb_id`` path segment.


@router.post("/run-tick-now")
async def run_tick_now(
    request: Request, background_tasks: BackgroundTasks,
) -> Response:
    """Plan 05 ADDITION-1 — manually invoke ``_weekly_maintenance_tick``.

    Returns:
      - 202 with JSON {"status": "started"} if the background task was
        scheduled (the tick will run asynchronously).
      - 409 with JSON {"status": "already_running"} if the discovery
        service singleton state is "running".

    The endpoint NEVER blocks on the tick itself — the BackgroundTask
    runs after the response is flushed. Any tick exception is captured
    inside :func:`discovery_service.run_manual_weekly_tick` so the
    BackgroundTask never propagates an unhandled exception.
    """
    from app.services import discovery_service

    state = discovery_service.get_state()
    if state.state == "running":
        return JSONResponse(
            content={
                "status": "already_running",
                "last_run_at": state.last_run_at,
            },
            status_code=409,
        )

    background_tasks.add_task(discovery_service.run_manual_weekly_tick)
    return JSONResponse(
        content={"status": "started"},
        status_code=202,
    )


@router.get("/tick-state")
async def tick_state(request: Request) -> JSONResponse:
    """Plan 05 ADDITION-1 — read-only JSON status for the 5s page poll.

    Returns the discovery_service module singleton snapshot:
    ``{"state": str, "last_run_at": str|None, "last_error": str|None,
       "last_candidates_made": int}``.
    """
    from app.services import discovery_service

    state = discovery_service.get_state()
    return JSONResponse(content={
        "state": state.state,
        "last_run_at": state.last_run_at,
        "last_error": state.last_error,
        "last_candidates_made": state.last_candidates_made,
    })


@router.get("/{mb_id}/top-tracks", response_class=HTMLResponse)
async def get_top_tracks(mb_id: str, request: Request) -> HTMLResponse:
    """UAT iter — fetch 5 famous tracks for an artist from ListenBrainz.

    Lazy-loaded by the tap-to-expand card via hx-trigger="revealed once".
    Best-effort: any failure renders an empty partial (UI shows nothing,
    user can still add/dismiss). The endpoint is read-only and idempotent.
    """
    from app.services.listenbrainz_client import get_top_recordings_for_artist
    try:
        tracks = await get_top_recordings_for_artist(mb_id, limit=5)
    except Exception:
        logger.exception("get_top_tracks: handler raised for mb_id=%s", mb_id)
        tracks = []

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/discover_top_tracks.html",
        {"tracks": tracks},
    )
