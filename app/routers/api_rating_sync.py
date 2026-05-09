"""Manual rating-sync endpoints (EVT-05).

POST /api/rating-sync/start — fires the backfill task and returns the
backfill banner HTMX partial. If a backfill is already RUNNING, returns the
current state without re-launching.

GET /api/rating-sync/status — HTMX-pollable partial; the banner self-polls
every 2s while in the running state.

Backfill itself lives in `app.services.backfill_service` — both this router
and the lifespan auto-trigger point at the same singleton.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services.backfill_service import (
    BackfillStateEnum,
    get_backfill_status,
    run_backfill,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rating-sync", tags=["rating-sync"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main (Pattern E)."""
    from app.main import templates

    return templates


def _state_str(status) -> str:
    """Map BackfillStateEnum to the lowercase strings the template branches on."""
    if status.state == BackfillStateEnum.RUNNING:
        return "running"
    if status.state == BackfillStateEnum.FAILED:
        return "failed"
    if status.state == BackfillStateEnum.COMPLETED:
        return "completed"
    return "idle"


@router.post("/start", response_class=HTMLResponse)
async def start_resync(request: Request):
    """EVT-05: 'Resync now' button. Starts (or returns current) backfill state."""
    templates = get_templates()
    status = get_backfill_status()
    if status.state != BackfillStateEnum.RUNNING:
        asyncio.create_task(run_backfill())
        # Re-read status so the banner reflects whichever state run_backfill flipped to.
        status = get_backfill_status()
    return templates.TemplateResponse(
        request,
        "partials/backfill_banner.html",
        {"backfill_status": status, "state": _state_str(status)},
    )


@router.get("/status", response_class=HTMLResponse)
async def status_endpoint(request: Request):
    """HTMX poll target while backfill is running."""
    templates = get_templates()
    status = get_backfill_status()
    return templates.TemplateResponse(
        request,
        "partials/backfill_banner.html",
        {"backfill_status": status, "state": _state_str(status)},
    )
