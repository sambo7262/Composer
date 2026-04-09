from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.database import get_session
from app.services.settings_service import get_all_settings, is_service_configured

router = APIRouter(tags=["pages"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, session: Session = Depends(get_session)):
    """Root page. Shows welcome if Plex not configured, else home."""
    templates = get_templates()
    plex_configured = is_service_configured(session, "plex")

    if not plex_configured:
        return templates.TemplateResponse(
            request,
            "pages/welcome.html",
        )

    return templates.TemplateResponse(
        request,
        "pages/home.html",
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Session = Depends(get_session)):
    """Settings page -- placeholder for Plan 02 full implementation."""
    templates = get_templates()
    plex_configured = is_service_configured(session, "plex")
    ollama_configured = is_service_configured(session, "ollama")
    lidarr_configured = is_service_configured(session, "lidarr")
    all_settings = get_all_settings(session)

    return templates.TemplateResponse(
        request,
        "pages/settings.html",
        {
            "plex_configured": plex_configured,
            "ollama_configured": ollama_configured,
            "lidarr_configured": lidarr_configured,
            "settings": all_settings,
        },
    )
