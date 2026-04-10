from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.database import get_session
from app.services.plex_client import test_plex_connection
from app.services.llm_client import test_anthropic_connection
from app.services.lidarr_client import test_lidarr_connection
from app.services.settings_service import (
    get_all_settings,
    get_setting,
    is_service_configured,
    save_setting,
)
from app.services.sync_scheduler import update_sync_schedule

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


# --- Plex endpoints ---


@router.post("/plex/test", response_class=HTMLResponse)
async def test_plex(
    request: Request,
    url: str = Form(...),
    token: str = Form(...),
):
    """Test Plex connection. Returns HTMX partial with result."""
    result = await test_plex_connection(url, token)
    templates = get_templates()

    return templates.TemplateResponse(
        request,
        "partials/connection_status.html",
        {
            "service": "plex",
            "success": result["success"],
            "server_name": result.get("server_name"),
            "options": result.get("libraries", []),
            "option_label": "Music Library",
            "option_name": "library",
            "error": result.get("error"),
            "url": url,
            "token": token,
        },
    )


@router.post("/plex/save", response_class=HTMLResponse)
async def save_plex(
    request: Request,
    url: str = Form(...),
    token: str = Form(...),
    library_id: str = Form(...),
    session: Session = Depends(get_session),
):
    """Save Plex configuration. Resolves library_name server-side (D-12 fix)."""
    # Resolve library name from Plex server instead of relying on form field
    library_name = library_id  # fallback
    result = await test_plex_connection(url, token)
    if result["success"]:
        for lib in result.get("libraries", []):
            if lib["key"] == library_id:
                library_name = lib["title"]
                break
    save_setting(
        session, "plex", url, token,
        {"library_id": library_id, "library_name": library_name},
    )
    setting = get_setting(session, "plex")
    sync_interval = 24
    if setting and setting.extra_config:
        sync_interval = setting.extra_config.get("sync_interval_hours", 24)
    templates = get_templates()

    return templates.TemplateResponse(
        request,
        "partials/service_card.html",
        {
            "service": "plex",
            "heading": "Plex Server",
            "description": "Connect to your Plex Media Server to access your music library.",
            "configured": True,
            "enabled": True,
            "setting": setting,
            "sync_interval": sync_interval,
        },
    )


# --- Plex sync schedule endpoint ---


@router.post("/plex/sync-schedule", response_class=HTMLResponse)
async def update_plex_sync_schedule(
    request: Request,
    sync_interval_hours: int = Form(...),
    session: Session = Depends(get_session),
):
    """Update the Plex sync schedule interval (D-04).

    Validates interval against allowlist [6, 12, 24] (T-02-09 mitigation).
    """
    # T-02-09: Validate against allowlist
    allowed_intervals = [6, 12, 24]
    if sync_interval_hours not in allowed_intervals:
        sync_interval_hours = 24  # safe default

    # Load current Plex setting and update extra_config
    setting = get_setting(session, "plex")
    if not setting:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Plex is not configured")

    # Preserve existing extra_config, update sync_interval_hours
    extra_config = setting.extra_config or {}
    extra_config["sync_interval_hours"] = sync_interval_hours

    # Re-save with updated extra_config (need credential for save_setting)
    from app.services.settings_service import get_decrypted_credential
    credential = get_decrypted_credential(session, "plex") or ""
    save_setting(session, "plex", setting.url, credential, extra_config)

    # Update the running scheduler
    update_sync_schedule(sync_interval_hours)

    # Return updated card
    updated_setting = get_setting(session, "plex")
    templates = get_templates()

    return templates.TemplateResponse(
        request,
        "partials/service_card.html",
        {
            "service": "plex",
            "heading": "Plex Server",
            "description": "Connect to your Plex Media Server to access your music library.",
            "configured": True,
            "enabled": True,
            "setting": updated_setting,
            "sync_interval": sync_interval_hours,
        },
    )


# --- Anthropic endpoints ---


@router.post("/anthropic/test", response_class=HTMLResponse)
async def test_anthropic(
    request: Request,
    api_key: str = Form(...),
):
    """Test Anthropic API connection. Returns HTMX partial with result."""
    result = await test_anthropic_connection(api_key)
    templates = get_templates()

    return templates.TemplateResponse(
        request,
        "partials/connection_status.html",
        {
            "service": "anthropic",
            "success": result["success"],
            "options": result.get("models", []),
            "option_label": "Model",
            "option_name": "model",
            "error": result.get("error"),
            "api_key": api_key,
        },
    )


@router.post("/anthropic/save", response_class=HTMLResponse)
async def save_anthropic(
    request: Request,
    api_key: str = Form(...),
    model: str = Form(...),
    session: Session = Depends(get_session),
):
    """Save Anthropic configuration."""
    save_setting(session, "anthropic", "https://api.anthropic.com", api_key, {"model_name": model})
    setting = get_setting(session, "anthropic")
    templates = get_templates()

    return templates.TemplateResponse(
        request,
        "partials/service_card.html",
        {
            "service": "anthropic",
            "heading": "Anthropic",
            "description": "Connect to Anthropic Claude for AI-powered playlist generation.",
            "configured": True,
            "enabled": True,
            "setting": setting,
        },
    )


# --- Lidarr endpoints ---


@router.post("/lidarr/test", response_class=HTMLResponse)
async def test_lidarr(
    request: Request,
    url: str = Form(...),
    api_key: str = Form(...),
):
    """Test Lidarr connection. Returns HTMX partial with result."""
    result = await test_lidarr_connection(url, api_key)
    templates = get_templates()

    return templates.TemplateResponse(
        request,
        "partials/connection_status.html",
        {
            "service": "lidarr",
            "success": result["success"],
            "options": result.get("profiles", []),
            "option_label": "Quality Profile",
            "option_name": "profile",
            "error": result.get("error"),
            "url": url,
            "api_key": api_key,
        },
    )


@router.post("/lidarr/save", response_class=HTMLResponse)
async def save_lidarr(
    request: Request,
    url: str = Form(...),
    api_key: str = Form(...),
    profile_id: str = Form(...),
    profile_name: str = Form(...),
    session: Session = Depends(get_session),
):
    """Save Lidarr configuration."""
    save_setting(
        session, "lidarr", url, api_key,
        {"profile_id": profile_id, "profile_name": profile_name},
    )
    setting = get_setting(session, "lidarr")
    templates = get_templates()

    return templates.TemplateResponse(
        request,
        "partials/service_card.html",
        {
            "service": "lidarr",
            "heading": "Lidarr",
            "description": "Connect to Lidarr for artist discovery and library management. Optional -- you can add this later.",
            "configured": True,
            "enabled": True,
            "setting": setting,
        },
    )


# --- Status endpoint ---


@router.get("")
async def get_settings_status(session: Session = Depends(get_session)):
    """Return all service statuses. No credentials in response."""
    settings = get_all_settings(session)
    return [s.dict() for s in settings]


# --- Reconfigure endpoints ---


@router.post("/{service}/reconfigure", response_class=HTMLResponse)
async def reconfigure_service(
    request: Request,
    service: str,
    session: Session = Depends(get_session),
):
    """Return the unconfigured form partial for re-entry. Does NOT delete config."""
    templates = get_templates()

    service_meta = {
        "plex": {
            "heading": "Plex Server",
            "description": "Connect to your Plex Media Server to access your music library.",
        },
        "anthropic": {
            "heading": "Anthropic",
            "description": "Connect to Anthropic Claude for AI-powered playlist generation.",
        },
        "lidarr": {
            "heading": "Lidarr",
            "description": "Connect to Lidarr for artist discovery and library management. Optional -- you can add this later.",
        },
    }

    meta = service_meta.get(service, {"heading": service, "description": ""})

    return templates.TemplateResponse(
        request,
        "partials/service_card.html",
        {
            "service": service,
            "heading": meta["heading"],
            "description": meta["description"],
            "configured": False,
            "enabled": True,
            "setting": None,
        },
    )
