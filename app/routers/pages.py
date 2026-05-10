from __future__ import annotations

import uuid
from math import ceil

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select, func, col

from app.database import get_session
from app.models.event_log import EventLog
from app.models.track import Track
from app.models.vibe import SetupState, Vibe
from app.routers import api_webhooks
from app.services.analysis_service import get_analysis_status
from app.services.event_bus import get_event_bus
from app.services.poll_service import get_poll_status
from app.services.settings_service import get_setting, is_service_configured
from app.services.sync_scheduler import get_scheduler
from app.services.sync_service import get_last_sync_info, get_sync_status

router = APIRouter(tags=["pages"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, session: Session = Depends(get_session)):
    """Root page. Shows welcome if Plex not configured, else compose chat.

    Phase 6 (D-07 / WIZ-01): when Plex is configured AND no Vibe rows exist
    AND there are >=1 rated tracks, auto-redirect to /setup. Once any Vibe row
    exists, this redirect no longer fires.
    """
    templates = get_templates()
    plex_configured = is_service_configured(session, "plex")

    if not plex_configured:
        return templates.TemplateResponse(
            request,
            "pages/welcome.html",
        )

    # Phase 6 D-07: auto-redirect to /setup wizard.
    rated_count = session.exec(
        select(func.count()).select_from(Track).where(Track.user_rating > 0)  # type: ignore[arg-type]
    ).one()
    if isinstance(rated_count, tuple):
        rated_count = rated_count[0]
    vibe_count = session.exec(
        select(func.count()).select_from(Vibe)
    ).one()
    if isinstance(vibe_count, tuple):
        vibe_count = vibe_count[0]
    if vibe_count == 0 and rated_count >= 1:
        return RedirectResponse("/setup", status_code=302)

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


@router.get("/debug/events", response_class=HTMLResponse)
async def debug_events(request: Request, session: Session = Depends(get_session)):
    """DEBUG-01 / D-21: list last 50 EventLog rows + queue depth + poll info
    + last test event + configured webhook URL.

    Plain HTML, screenshot-readable. No auth — Composer assumes Tailscale-only
    access (T-05-21). The page header explicitly warns about this.
    """
    templates = get_templates()
    rows = session.exec(
        select(EventLog).order_by(col(EventLog.received_at).desc()).limit(50)
    ).all()
    queue = get_event_bus()
    queue_depth = queue.qsize()
    scheduler = get_scheduler()
    poll_job = scheduler.get_job("plex_polling") if scheduler else None
    poll_status = get_poll_status()
    poll_info = {
        "enabled": poll_job is not None,
        "interval_minutes": 5,
        "next_run": (
            poll_job.next_run_time.isoformat()
            if poll_job and poll_job.next_run_time
            else None
        ),
        "last_completed": poll_status.last_completed if poll_job else None,
        "last_changes": poll_status.last_changes_seen if poll_job else 0,
        "error": poll_status.error if poll_job else None,
    }
    last_test_received_at = api_webhooks._last_test_received_at
    last_test_payload = api_webhooks._last_test_payload
    webhook_setting = get_setting(session, "webhook")
    webhook_url = webhook_setting.url if webhook_setting else None
    return templates.TemplateResponse(
        request,
        "pages/debug_events.html",
        {
            "active_page": "debug_events",
            "events": rows,
            "queue_depth": queue_depth,
            "poll_info": poll_info,
            "last_test_received_at": last_test_received_at,
            "last_test_payload": last_test_payload,
            "webhook_url": webhook_url,
        },
    )


# ---------------------------------------------------------------------------
# Phase 6 Plan 03 — Setup wizard pages (D-06).
# ---------------------------------------------------------------------------

def _get_or_init_setup_state(session: Session) -> SetupState:
    """Single-row id=1 helper (mirrors api_setup._get_or_create_setup_state)."""
    state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
    if state is None:
        state = SetupState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def _count_rated(session: Session) -> int:
    n = session.exec(
        select(func.count()).select_from(Track).where(Track.user_rating > 0)  # type: ignore[arg-type]
    ).one()
    if isinstance(n, tuple):
        n = n[0]
    return int(n)


def _decode_draft(state: SetupState):
    """Deserialize SetupState.draft_proposals_json -> VibeProposalSet | None."""
    if not state.draft_proposals_json:
        return None
    try:
        from app.services.vibe_clusterer import VibeProposalSet
        return VibeProposalSet.model_validate_json(state.draft_proposals_json)
    except Exception:
        return None


@router.get("/setup", response_class=HTMLResponse)
async def setup_step1(request: Request, session: Session = Depends(get_session)):
    """Wizard Step 1 — confirm rated tracks (D-06 / WIZ-01)."""
    templates = get_templates()
    n_rated = _count_rated(session)
    return templates.TemplateResponse(
        request,
        "pages/setup_step1.html",
        {"n_rated": n_rated, "active_page": "setup"},
    )


@router.get("/setup/webhook", response_class=HTMLResponse)
async def setup_step2(request: Request, session: Session = Depends(get_session)):
    """Wizard Step 2 — webhook config (D-06 conditional)."""
    templates = get_templates()
    webhook_setting = get_setting(session, "webhook")
    webhook_url = webhook_setting.url if webhook_setting else None
    return templates.TemplateResponse(
        request,
        "pages/setup_step2.html",
        {
            "active_page": "setup",
            "webhook_url": webhook_url,
        },
    )


@router.get("/setup/propose", response_class=HTMLResponse)
async def setup_step3(request: Request, session: Session = Depends(get_session)):
    """Wizard Step 3 — initial proposal + refinement loop (THE flagship UX)."""
    templates = get_templates()
    state = _get_or_init_setup_state(session)
    draft_proposals = _decode_draft(state)
    return templates.TemplateResponse(
        request,
        "pages/setup_step3.html",
        {
            "active_page": "setup",
            "draft_proposals": draft_proposals,
            "refinement_turn_count": state.refinement_turn_count,
        },
    )


@router.get("/setup/confirm", response_class=HTMLResponse)
async def setup_step4(request: Request, session: Session = Depends(get_session)):
    """Wizard Step 4 — final review + Push to Plex CTA."""
    templates = get_templates()
    state = _get_or_init_setup_state(session)
    draft_proposals = _decode_draft(state)
    return templates.TemplateResponse(
        request,
        "pages/setup_step4.html",
        {
            "active_page": "setup",
            "draft_proposals": draft_proposals,
        },
    )


@router.get("/setup/done", response_class=HTMLResponse)
async def setup_done(request: Request, session: Session = Depends(get_session)):
    """Wizard Step 5 — done page."""
    templates = get_templates()
    vibe_count = session.exec(select(func.count()).select_from(Vibe)).one()
    if isinstance(vibe_count, tuple):
        vibe_count = vibe_count[0]
    return templates.TemplateResponse(
        request,
        "pages/setup_done.html",
        {
            "active_page": "setup",
            "vibe_count": int(vibe_count),
        },
    )


@router.get("/debug/vibes", response_class=HTMLResponse)
async def debug_vibes(request: Request, session: Session = Depends(get_session)):
    """Plan 04 stub — Step 5 'View vibes diagnostics' link target.

    Plan 04 will fully populate this page; for now we render a minimal page so
    the link doesn't 404.
    """
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "pages/debug_vibes_stub.html",
        {"active_page": "debug_vibes"},
    )
