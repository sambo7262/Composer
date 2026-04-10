from __future__ import annotations

import uuid
from math import ceil

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select, func, col

from app.database import get_session
from app.models.track import Track
from app.services.analysis_service import get_analysis_status
from app.services.settings_service import get_setting, is_service_configured
from app.services.sync_service import get_last_sync_info, get_sync_status

router = APIRouter(tags=["pages"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, session: Session = Depends(get_session)):
    """Root page. Shows welcome if Plex not configured, else compose chat."""
    templates = get_templates()
    plex_configured = is_service_configured(session, "plex")

    if not plex_configured:
        return templates.TemplateResponse(
            request,
            "pages/welcome.html",
        )

    anthropic_configured = is_service_configured(session, "anthropic")

    return templates.TemplateResponse(
        request,
        "pages/chat.html",
        {
            "active_page": "compose",
            "anthropic_configured": anthropic_configured,
            "session_id": str(uuid.uuid4()),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Session = Depends(get_session)):
    """Settings page with three service configuration cards."""
    templates = get_templates()
    plex_configured = is_service_configured(session, "plex")
    anthropic_configured = is_service_configured(session, "anthropic")
    lidarr_configured = is_service_configured(session, "lidarr")

    plex_setting = get_setting(session, "plex")
    anthropic_setting = get_setting(session, "anthropic")
    lidarr_setting = get_setting(session, "lidarr")

    # Extract sync interval from Plex extra_config (default 24h)
    sync_interval = 24
    if plex_setting and plex_setting.extra_config:
        sync_interval = plex_setting.extra_config.get("sync_interval_hours", 24)

    return templates.TemplateResponse(
        request,
        "pages/settings.html",
        {
            "active_page": "settings",
            "plex_configured": plex_configured,
            "anthropic_configured": anthropic_configured,
            "lidarr_configured": lidarr_configured,
            "plex_setting": plex_setting,
            "anthropic_setting": anthropic_setting,
            "lidarr_setting": lidarr_setting,
            "sync_interval": sync_interval,
        },
    )


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request, session: Session = Depends(get_session)):
    """Library browse page with track table, sync banner, and search."""
    templates = get_templates()

    # Get sync status for the banner
    sync_status = get_sync_status()
    sync_info = get_last_sync_info(session)

    # Get analysis status for the analysis banner
    analysis_status = get_analysis_status()
    analyzed_count = session.exec(
        select(func.count()).select_from(Track).where(Track.analyzed_at.isnot(None))  # type: ignore[union-attr]
    ).one()
    unanalyzed_count = session.exec(
        select(func.count()).select_from(Track).where(
            Track.analyzed_at.is_(None),  # type: ignore[union-attr]
            Track.file_path.isnot(None),  # type: ignore[union-attr]
        )
    ).one()

    # Query initial page of tracks (page 1, 50 per page, sorted by title asc)
    per_page = 50
    query = select(Track).order_by(col(Track.title).asc())

    # Count total tracks
    count_query = select(func.count()).select_from(Track)
    total = session.exec(count_query).one()

    tracks = session.exec(query.offset(0).limit(per_page)).all()

    total_pages = ceil(total / per_page) if per_page > 0 and total > 0 else 1
    has_prev = False
    has_next = 1 < total_pages

    return templates.TemplateResponse(
        request,
        "pages/library.html",
        {
            "active_page": "library",
            "tracks": tracks,
            "page": 1,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
            "search": "",
            "sort": "title",
            "order": "asc",
            "sync_status": sync_status,
            "state": sync_status.state.value,
            "last_synced": sync_info.get("last_sync_completed"),
            "track_count": sync_info.get("track_count", 0),
            "analysis_status": analysis_status,
            "analysis_state": analysis_status.state.value,
            "analyzed_count": analyzed_count,
            "unanalyzed_count": unanalyzed_count,
        },
    )
