from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, func, select

from app.database import get_session
from app.models.track import Track
from app.services.analysis_service import (
    AnalysisStateEnum,
    get_analysis_status,
    run_analysis,
    stop_analysis,
)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


def _get_analysis_db_stats(session: Session) -> dict:
    """Query DB for analysis stats: analyzed count, error count, unanalyzed count."""
    analyzed_count = session.exec(
        select(func.count()).select_from(Track).where(Track.analyzed_at.isnot(None))  # type: ignore[union-attr]
    ).one()
    error_count = session.exec(
        select(func.count()).select_from(Track).where(Track.analysis_error.isnot(None))  # type: ignore[union-attr]
    ).one()
    unanalyzed_count = session.exec(
        select(func.count()).select_from(Track).where(
            Track.analyzed_at.is_(None),  # type: ignore[union-attr]
            Track.file_path.isnot(None),  # type: ignore[union-attr]
        )
    ).one()
    return {
        "analyzed_count": analyzed_count,
        "error_count": error_count,
        "unanalyzed_count": unanalyzed_count,
    }


@router.post("/start", response_class=HTMLResponse)
async def start_analysis(
    request: Request,
    session: Session = Depends(get_session),
):
    """Trigger analysis. Returns the analysis banner partial for HTMX swap.

    If analysis is already running, returns current progress (no error).
    Otherwise, launches analysis as background task and returns running banner.
    """
    templates = get_templates()
    status = get_analysis_status()

    # T-03-04: If already RUNNING, just return current progress
    if status.state == AnalysisStateEnum.RUNNING:
        db_stats = _get_analysis_db_stats(session)
        return templates.TemplateResponse(
            request,
            "partials/analysis_banner.html",
            {
                "analysis_status": status,
                "state": status.state.value,
                **db_stats,
            },
        )

    # Launch analysis as background task
    asyncio.create_task(run_analysis())

    # Return running banner with initial state
    status = get_analysis_status()
    db_stats = _get_analysis_db_stats(session)
    return templates.TemplateResponse(
        request,
        "partials/analysis_banner.html",
        {
            "analysis_status": status,
            "state": "running",
            **db_stats,
        },
    )


@router.post("/stop", response_class=HTMLResponse)
async def stop_analysis_endpoint(
    request: Request,
    session: Session = Depends(get_session),
):
    """Pause analysis. Returns the analysis banner partial with paused state."""
    templates = get_templates()
    await stop_analysis()
    status = get_analysis_status()
    db_stats = _get_analysis_db_stats(session)

    return templates.TemplateResponse(
        request,
        "partials/analysis_banner.html",
        {
            "analysis_status": status,
            "state": "paused",
            **db_stats,
        },
    )


@router.get("/status", response_class=HTMLResponse)
async def analysis_status(
    request: Request,
    session: Session = Depends(get_session),
):
    """Return current analysis status as HTML partial for HTMX polling."""
    templates = get_templates()
    status = get_analysis_status()
    db_stats = _get_analysis_db_stats(session)

    return templates.TemplateResponse(
        request,
        "partials/analysis_banner.html",
        {
            "analysis_status": status,
            "state": status.state.value,
            **db_stats,
        },
    )
