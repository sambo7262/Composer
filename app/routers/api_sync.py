from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.database import get_session
from app.services.settings_service import is_service_configured
from app.services.sync_service import (
    SyncStateEnum,
    get_last_sync_info,
    get_sync_status,
    run_sync,
)

router = APIRouter(prefix="/api/sync", tags=["sync"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


@router.post("/start", response_class=HTMLResponse)
async def start_sync(
    request: Request,
    session: Session = Depends(get_session),
):
    """Trigger a library sync. Returns the sync banner partial for HTMX swap.

    If sync is already running, returns current progress (no error).
    If Plex is not configured, returns an error banner.
    Otherwise, launches sync as background task and returns running banner.
    """
    templates = get_templates()
    status = get_sync_status()

    # T-02-06: If already running, just return current progress
    if status.state == SyncStateEnum.RUNNING:
        sync_info = get_last_sync_info(session)
        return templates.TemplateResponse(
            request,
            "partials/sync_banner.html",
            {
                "sync_status": status,
                "state": status.state.value,
                "last_synced": sync_info.get("last_sync_completed"),
                "track_count": sync_info.get("track_count", 0),
            },
        )

    # Check Plex is configured before attempting sync
    if not is_service_configured(session, "plex"):
        return templates.TemplateResponse(
            request,
            "partials/sync_banner.html",
            {
                "sync_status": status,
                "state": "error",
                "error_message": "Plex is not configured. Set up Plex in Settings first.",
                "last_synced": None,
                "track_count": 0,
            },
        )

    # Launch sync as background task
    asyncio.create_task(run_sync())

    # Return running banner with initial state
    status = get_sync_status()
    return templates.TemplateResponse(
        request,
        "partials/sync_banner.html",
        {
            "sync_status": status,
            "state": "running",
            "last_synced": None,
            "track_count": 0,
        },
    )


@router.get("/status", response_class=HTMLResponse)
async def sync_status(
    request: Request,
    session: Session = Depends(get_session),
):
    """Return current sync status as HTML partial for HTMX polling."""
    templates = get_templates()
    status = get_sync_status()
    sync_info = get_last_sync_info(session)

    return templates.TemplateResponse(
        request,
        "partials/sync_banner.html",
        {
            "sync_status": status,
            "state": status.state.value,
            "last_synced": sync_info.get("last_sync_completed"),
            "track_count": sync_info.get("track_count", 0),
        },
    )
