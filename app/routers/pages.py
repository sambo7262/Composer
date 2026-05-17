from __future__ import annotations

import logging
import uuid
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select, func, col

logger = logging.getLogger(__name__)

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
    """Root page (UI-01).

    Phase 6 (D-07 / WIZ-01): when Plex is configured AND no Vibe rows exist
    AND there are >=1 rated tracks, auto-redirect to /setup.

    Phase 7 (UI-01): once vibes exist, render the v2 vibes home page as the
    landing surface. The legacy chat.html return is removed (UI-06 — chat
    retired).
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

    # Phase 7 UI-01 — render the vibes home as the landing page.
    # WR-09 fix: never let a crash in read_vibes_home (e.g. corrupt Vibe row)
    # blow up the root path. Fall back to welcome.html so the user can still
    # navigate; the exception is logged for the operator.
    try:
        return await read_vibes_home(request, session)
    except Exception:
        logger.exception(
            "home: read_vibes_home failed; falling back to welcome page",
        )
        return templates.TemplateResponse(
            request,
            "pages/welcome.html",
        )


def _compute_home_chip_context(session: Session) -> dict:
    """Phase 8 D-B4 / UI-10 — home-page LLM cost chip context.

    Returns a dict with four keys: ``this_week_cost_usd`` (float),
    ``days_until_refresh`` (int | None), ``breaker_paused`` (bool), and
    ``has_first_tick`` (bool). Read by ``partials/llm_cost_chip.html``.

    Filter shape (matches CONTEXT D-B4 "Integration Points line 272"):
    sum(LLMUsage.cost_estimate_usd) WHERE called_at >=
        max(CostMeterBaseline.deploy_at, WeeklyCronState.last_tick_at).
    The baseline floor is permanent — pre-deploy historical / testing rows
    are excluded forever. The last_tick_at floor moves on every successful
    weekly cron tick so the chip shows ONLY the post-tick spend (mirrors
    the "this week" framing).

    Defensive: missing CostMeterBaseline or WeeklyCronState rows fall back
    to the pre-first-tick state (chip shows the friendly message).
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    from app.models.discovery import CostMeterBaseline, WeeklyCronState
    from app.models.llm_usage import LLMUsage

    baseline = session.get(CostMeterBaseline, 1)
    weekly = session.get(WeeklyCronState, 1)

    has_first_tick = weekly is not None and weekly.last_tick_at is not None

    # WR-05 fix: when the baseline row is missing (bootstrap partial-success
    # path), force the chip into pre-first-tick state instead of silently
    # falling back to epoch and surfacing every historical / testing row.
    # has_first_tick is also forced False so the empty-state copy renders.
    if baseline is None:
        return {
            "this_week_cost_usd": 0.0,
            "days_until_refresh": None,
            "breaker_paused": False,
            "has_first_tick": False,
            "next_tick_local_str": (
                __import__(
                    "app.utils.jinja_filters", fromlist=["next_sunday_03_utc_in_la"],
                ).next_sunday_03_utc_in_la()
            ),
            "baseline_missing": True,
        }

    # Lexicographic ISO 8601 ordering matches chronological ordering — same
    # invariant used by the /settings 7-day window query. (WR-04 — known
    # fragile under microsecond drift; tracked in 08-REVIEW.md for Phase 8.1.)
    baseline_iso = baseline.deploy_at
    if has_first_tick:
        floor = max(weekly.last_tick_at, baseline_iso)
    else:
        floor = baseline_iso

    row = session.exec(
        select(func.coalesce(func.sum(LLMUsage.cost_estimate_usd), 0.0))
        .where(LLMUsage.called_at >= floor)  # type: ignore[arg-type]
    ).first()
    if isinstance(row, tuple):
        row = row[0]
    this_week_cost_usd = float(row or 0.0)

    days_until_refresh: Optional[int] = None
    if has_first_tick:
        try:
            last_dt = _dt.fromisoformat(weekly.last_tick_at)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=_tz.utc)
            age = _dt.now(_tz.utc) - last_dt
            remaining = _td(days=7) - age
            days_until_refresh = max(0, int(remaining.total_seconds() // 86400))
        except ValueError:
            days_until_refresh = None

    # Cost-breaker state: tripped within the last 60s = paused (mirrors the
    # /settings cost-meter card check).
    breaker_paused = False
    try:
        from app.services.llm_cost_breaker import get_state as _breaker_state

        breaker = _breaker_state()
        if breaker.last_tripped_at:
            try:
                tripped_dt = _dt.fromisoformat(breaker.last_tripped_at)
                if tripped_dt.tzinfo is None:
                    tripped_dt = tripped_dt.replace(tzinfo=_tz.utc)
                if (_dt.now(_tz.utc) - tripped_dt).total_seconds() < 60:
                    breaker_paused = True
            except ValueError:
                pass
    except Exception:
        logger.exception(
            "_compute_home_chip_context: breaker state read failed; "
            "rendering chip without paused modifier"
        )

    # Pre-format the "next tick" string in PT — shared helper handles DST.
    from app.utils.jinja_filters import next_sunday_03_utc_in_la
    next_tick_local_str = next_sunday_03_utc_in_la()

    return {
        "this_week_cost_usd": this_week_cost_usd,
        "days_until_refresh": days_until_refresh,
        "breaker_paused": breaker_paused,
        "has_first_tick": has_first_tick,
        "next_tick_local_str": next_tick_local_str,
    }


@router.get("/vibes", response_class=HTMLResponse)
async def read_vibes_home(
    request: Request, session: Session = Depends(get_session),
):
    """UI-01 — vibes home / landing page.

    Renders one card per ACTIVE vibe (archived vibes excluded — D-28).
    Each card shows name + track count + optional description, plus a
    SUGG-10 "Find candidates" CTA when track_count < 25 (the CONTEXT
    discretion threshold).

    Phase 8 UI-10 / D-B4 — also computes the home-page LLM cost chip
    context (this_week_cost_usd, days_until_refresh, breaker_paused,
    has_first_tick). The chip is the runaway-cost trip wire; details
    stay on /debug/suggestions.
    """
    from app.models.vibe import TrackVibe

    templates = get_templates()
    vibes = session.exec(
        select(Vibe)
        .where(Vibe.is_active == True)  # noqa: E712
        .order_by(col(Vibe.id).asc())
    ).all()
    enriched = []
    for v in vibes:
        n = session.exec(
            select(func.count())
            .select_from(TrackVibe)
            .where(TrackVibe.vibe_id == v.id)
        ).one()
        if isinstance(n, tuple):
            n = n[0]
        enriched.append(type("V", (), {
            "id": v.id,
            "name": v.name,
            "description": v.description,
            "track_count": int(n),
            "color": v.color,  # Phase 8 UI-09 — surface color to vibe_card.
        }))

    chip_context = _compute_home_chip_context(session)

    return templates.TemplateResponse(
        request,
        "pages/vibes_home.html",
        {
            "active_page": "vibes",
            "vibes": enriched,
            **chip_context,
        },
    )


@router.get("/suggestions", response_class=HTMLResponse)
async def read_suggestions(
    request: Request, session: Session = Depends(get_session),
):
    """The Suggestions queue page (D-08 compact list / D-09 tap-to-expand
    / D-10 dismiss inside expanded view).

    Reads SuggestionsMirror rows ordered by position (lower = top), joins
    each to Track for title/artist/plex_rating_key and to Vibe for the
    vibe chip. Empty state shows the existing llm_progress_card partial
    (CONTEXT discretion — bootstrap loader reuse).
    """
    from app.models.suggestions import SuggestionsMirror

    templates = get_templates()
    rows = session.exec(
        select(SuggestionsMirror).order_by(col(SuggestionsMirror.position).asc())
    ).all()
    enriched = []
    for r in rows:
        track = session.exec(
            select(Track).where(Track.id == r.track_id)
        ).first()
        vibe = None
        if r.vibe_id is not None:
            vibe = session.exec(
                select(Vibe).where(Vibe.id == r.vibe_id)
            ).first()
        if track is not None:
            enriched.append({"row": r, "track": track, "vibe": vibe})
    return templates.TemplateResponse(
        request,
        "pages/suggestions.html",
        {"active_page": "suggestions", "suggestions": enriched},
    )


@router.get("/chat", response_class=HTMLResponse)
async def read_chat_retired() -> HTMLResponse:
    """UI-06 — legacy chat surface returns 404."""
    return HTMLResponse(
        status_code=404,
        content=(
            "The mood-chat UI was retired in Phase 7. "
            "Use Suggestions or Vibes instead."
        ),
    )


@router.get("/discover", response_class=HTMLResponse)
async def read_discover(request: Request):
    """Phase 8 Plan 04 (DISC-03/05 / D-D2..D-D5) — vibe-grouped discovery surface.

    Reads the active candidate set from
    ``discovery_service.read_active_discover_data`` (subtracts
    DiscoveryDismissed at read time per D-B2, hides
    vibe_slotted DiscoveryAdds per D-D4) and renders
    ``pages/discover.html``.
    """
    from app.services import discovery_service

    from app.utils.jinja_filters import next_sunday_03_utc_in_la

    templates = get_templates()
    data = await discovery_service.read_active_discover_data()
    return templates.TemplateResponse(
        request,
        "pages/discover.html",
        {
            "active_page": "discover",
            "sections": data["sections"],
            "lidarr_configured": data["lidarr_configured"],
            "vibes_exist": data["vibes_exist"],
            "has_first_tick": data["has_first_tick"],
            "next_tick_local_str": next_sunday_03_utc_in_la(),
        },
    )


@router.get("/debug/discovery", response_class=HTMLResponse)
async def read_debug_discovery(request: Request):
    """DEBUG-04 / DEBUG-05 — /debug/discovery diagnostic surface.

    Five sections + cost panel + recent-LLM-calls table per
    CONTEXT §"Claude's Discretion: /debug/discovery page layout":

      1. Last weekly DiscoveryCandidate set with full provenance
      2. DiscoveryAdd lifecycle timeline
      3. Recent MusicBrainz queries (via MusicBrainzCache cached_at DESC)
      4. Recent Lidarr add_artist requests (DiscoveryAdd recent slice)
      5. Lidarr connection-test history (best-effort from EventLog)
      + Cost panel — SUM(cost_estimate_usd) WHERE purpose LIKE 'discovery_artist_%'
      + Last 20 LLMUsage rows for that same purpose-prefix

    Plain HTML per DEBUG-05 — no JS-only rendering. Plan 05 ADDITION-1
    adds a "Run weekly tick now" button + 5s state poll; both render
    server-side so a copy-paste of the page is self-contained.
    """
    from app.services import discovery_service

    templates = get_templates()
    data = await discovery_service.read_debug_discovery_data()
    return templates.TemplateResponse(
        request,
        "pages/debug_discovery.html",
        {"active_page": "settings", **data},
    )


@router.get("/debug", response_class=HTMLResponse)
async def read_debug_index(request: Request):
    """DEBUG-05 — index page linking to all debug surfaces.

    Plain HTML, no JS-only content. Linked from the settings footer.
    """
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "pages/debug_index.html",
        {"active_page": "settings"},
    )


@router.get("/debug/suggestions", response_class=HTMLResponse)
async def read_debug_suggestions(
    request: Request, session: Session = Depends(get_session),
):
    """DEBUG-03 — Suggestions queue diagnostic surface.

    Sections (in render order):
      1. Cost breaker state (in-process singleton from llm_cost_breaker).
      2. Current SuggestionsMirror queue (track + vibe + score + rationale).
      3. Last 20 RefillTriggerLog rows (DESC triggered_at).
      4. Last 20 LLMUsage rows where purpose LIKE 'suggestions_%'.
      5. Last 20 NegativeSignal rows (DESC created_at).

    Plain HTML; copy-friendly via <pre> + <code> + <table> blocks
    (DEBUG-05 invariant). T-07-03-01..03 — every Track / Vibe / artist
    / rationale field rendered with explicit `| e` for XSS escape.
    """
    from app.models.suggestions import (
        NegativeSignal, RefillTriggerLog, SuggestionsMirror,
    )
    from app.models.llm_usage import LLMUsage
    from app.services.llm_cost_breaker import get_state as breaker_state

    templates = get_templates()

    # 1. Current queue — ordered by position ascending (lower = top).
    rows = session.exec(
        select(SuggestionsMirror)
        .order_by(col(SuggestionsMirror.position).asc())
    ).all()
    queue = []
    for r in rows:
        track = session.exec(select(Track).where(Track.id == r.track_id)).first()
        vibe = None
        if r.vibe_id is not None:
            vibe = session.exec(
                select(Vibe).where(Vibe.id == r.vibe_id)
            ).first()
        if track is not None:
            queue.append({"row": r, "track": track, "vibe": vibe})

    # 2. Last 20 refill triggers — DESC by triggered_at.
    refill_log = session.exec(
        select(RefillTriggerLog)
        .order_by(col(RefillTriggerLog.triggered_at).desc())
        .limit(20)
    ).all()

    # 3. Last 20 LLMUsage rows with suggestions_* OR discovery_* purpose
    #    — DESC by called_at. Phase 7.1 (SUGG-13) added the weekly discovery
    #    LLM call under purpose='discovery_weekly' — surface it here too so
    #    the Suggestions debug page covers both refill-side (suggestions_*)
    #    and discovery-side (discovery_*) Anthropic activity.
    llm_calls = session.exec(
        select(LLMUsage)
        .where(
            col(LLMUsage.purpose).like("suggestions_%")
            | col(LLMUsage.purpose).like("discovery_%")
        )
        .order_by(col(LLMUsage.called_at).desc())
        .limit(20)
    ).all()

    # 4. Last 20 negative signals — DESC by created_at.
    negative_signals = session.exec(
        select(NegativeSignal)
        .order_by(col(NegativeSignal.created_at).desc())
        .limit(20)
    ).all()

    return templates.TemplateResponse(
        request,
        "pages/debug_suggestions.html",
        {
            "active_page": "settings",
            "queue": queue,
            "refill_log": refill_log,
            "llm_calls": llm_calls,
            "negative_signals": negative_signals,
            "breaker": breaker_state(),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Session = Depends(get_session)):
    """Settings page with three service configuration cards.

    Phase 6.2 Plan 02 (WIZ-08 / D-24): also passes ``vibe_count`` so the
    template can render the destructive "Start Over" button only when
    ``Vibe.count() > 0`` — never offer destruction when there's nothing
    to destroy.

    Phase 7 Plan 02 (OPS-05) → Phase 7.1 D-D3: passes this week's LLM
    spend (rolling 7-day window) + cache hit % + breaker-paused state for
    the cost meter card.
    """
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

    # WIZ-08 — vibe_count gates the destructive Start Over button (D-24).
    vibe_count = session.exec(select(func.count()).select_from(Vibe)).one()
    if isinstance(vibe_count, tuple):
        vibe_count = vibe_count[0]
    vibe_count = int(vibe_count)

    # Phase 7.1 D-D3 — Anthropic spend over a rolling 7-day window
    # (replaces Phase 7 OPS-05 midnight-of-today gate). Steady-state
    # listening costs $0/day (SQL refill is free); the only spend is one
    # discovery_call_weekly per week. The window matches the LLM cadence
    # so the cost meter is meaningful instead of always reading $0.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    from app.models.llm_usage import LLMUsage
    from app.services.llm_cost_breaker import (
        DAILY_QUOTA,
        WEEKLY_DISCOVERY_BUDGET_USD,
        get_state as breaker_state,
    )

    seven_days_ago_iso = (
        _dt.now(_tz.utc) - _td(days=7)
    ).isoformat()
    this_week_rows = session.exec(
        select(LLMUsage).where(LLMUsage.called_at >= seven_days_ago_iso)  # type: ignore[arg-type]
    ).all()
    this_week_calls = len(this_week_rows)
    this_week_cost_usd = sum(
        (r.cost_estimate_usd or 0.0) for r in this_week_rows
    )
    total_cache_creation = sum(
        r.cache_creation_input_tokens or 0 for r in this_week_rows
    )
    total_cache_read = sum(
        r.cache_read_input_tokens or 0 for r in this_week_rows
    )
    # Phase 7.1 — caching is moot at weekly cadence (every weekly call is
    # a cold start; cache_hit_pct == 0 by design). We still compute the
    # percentage so any future intra-week call (manual diagnostic, batch
    # rescore) can surface caching health when applicable.
    cache_hit_pct = None
    if (total_cache_creation + total_cache_read) > 0:
        cache_hit_pct = int(round(
            100.0 * total_cache_read / (total_cache_creation + total_cache_read)
        ))

    breaker = breaker_state()
    breaker_paused = False
    breaker_reason = None
    if breaker.last_tripped_at:
        try:
            tripped_dt = _dt.fromisoformat(breaker.last_tripped_at)
            if tripped_dt.tzinfo is None:
                tripped_dt = tripped_dt.replace(tzinfo=_tz.utc)
            if (_dt.now(_tz.utc) - tripped_dt).total_seconds() < 60:
                breaker_paused = True
                breaker_reason = breaker.last_tripped_reason
        except ValueError:
            pass

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
            "vibe_count": vibe_count,
            # Phase 7.1 D-D3 — cost meter context (DAILY → WEEKLY framing).
            # OPS-05 carry-forward: same LLMUsage queries, just over a 7-day
            # rolling window instead of midnight-of-today.
            "this_week_calls": this_week_calls,
            "this_week_cost_usd": this_week_cost_usd,
            "daily_quota": DAILY_QUOTA,                  # per-day burst gate (unchanged)
            "weekly_budget_usd": WEEKLY_DISCOVERY_BUDGET_USD,
            "cache_hit_pct": cache_hit_pct,
            "breaker_paused": breaker_paused,
            "breaker_reason": breaker_reason,
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
    """Deserialize SetupState.draft_proposals_json -> VibeProposalSet | None.

    Phase 8 UI-09 / D-E2: hydrate each proposal's ``proposed_color`` from
    the locked palette so the wizard preview matches the post-finalize
    accent. Index-based assignment (palette[(i+1) % len]) — the +1 mirrors
    the deterministic ``assign_vibe_color(vibe_id)`` mapping the
    Vibe-creation paths will use on commit. Best-effort: a color-hydration
    failure must NEVER break wizard rendering.
    """
    if not state.draft_proposals_json:
        return None
    try:
        from app.services.vibe_clusterer import VibeProposalSet
        proposals_set = VibeProposalSet.model_validate_json(
            state.draft_proposals_json
        )
    except Exception:
        return None
    try:
        from app.services.discovery_service import assign_vibe_color
        for i, p in enumerate(proposals_set.proposals):
            if getattr(p, "proposed_color", None) is None:
                p.proposed_color = assign_vibe_color(i + 1)
    except Exception:
        logger.exception(
            "_decode_draft: proposed_color hydration failed; rendering "
            "without per-proposal color preview"
        )
    return proposals_set


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
    """Wizard Step 4 — final review + Push to Plex CTA.

    Phase 6 Plan 04 (D-20): pass `setup_state` so the template can branch the
    Push CTA's hx-post target between /api/setup/finalize (initial wizard) and
    /api/vibes/recluster/commit (re-cluster — diff-based per D-21).
    """
    templates = get_templates()
    state = _get_or_init_setup_state(session)
    draft_proposals = _decode_draft(state)
    return templates.TemplateResponse(
        request,
        "pages/setup_step4.html",
        {
            "active_page": "setup",
            "draft_proposals": draft_proposals,
            "setup_state": state,
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
    """DEBUG-02 / D-36: per-vibe diagnostic + slot-in log + drift indicator.

    Phase 6 Plan 04 implementation — replaces the Plan 03 stub. Builds:

    - vibes: SELECT * FROM Vibe ORDER BY name (active + archived both shown
      via the per-card Active field).
    - vibe_member_counts: SELECT COUNT(*) per vibe FROM TrackVibe.
    - slot_in_log: last 20 SlotInLog rows hydrated with track title/artist
      + vibe names + comma-joined distance string for the diagnostic table.
    - drift: orphan_count + stale_count are placeholder zeros for Plan 04;
      the actual Plex-side cross-check is a deferred enhancement (the route
      would need to convert to async + wrap fetchItem in to_thread). The
      partial RENDERS correctly today (no-drift state) — future enhancement
      swaps in real counts via plex_playlist_service.
    """
    import json as _json

    from app.models.llm_usage import LLMUsage
    from app.models.vibe import (
        ManagedPlaylist,
        SlotInLog,
        TrackVibe,
        Vibe,
    )

    templates = get_templates()

    vibes = list(session.exec(select(Vibe).order_by(col(Vibe.name).asc())).all())

    # Member counts per vibe (powers the "Members" diagnostic card field).
    vibe_member_counts: dict = {}
    for v in vibes:
        n = session.exec(
            select(func.count())
            .select_from(TrackVibe)
            .where(TrackVibe.vibe_id == v.id)
        ).one()
        vibe_member_counts[v.id] = n[0] if isinstance(n, tuple) else int(n)

    # Last 20 slot-in decisions (DESC LIMIT 20). Hydrate vibe names + track
    # title/artist + distance string from JSON columns at render time.
    slot_log_rows = list(
        session.exec(
            select(SlotInLog)
            .order_by(col(SlotInLog.timestamp).desc())
            .limit(20)
        ).all()
    )

    # IN-06: batch-hydrate tracks + vibes referenced by the 20 SlotInLog rows.
    # Previously this loop fired ~20 Track lookups + up to 20*N Vibe lookups
    # (one per vibe id per row) — N+1 with N as high as 60 in the worst case.
    # Replace with two WHERE id IN (...) queries: total query count drops to
    # 1 (SlotInLog) + 1 (Track) + 1 (Vibe) regardless of row count.
    track_ids = {r.track_id for r in slot_log_rows if r.track_id is not None}
    referenced_vibe_ids: set[int] = set()
    parsed_per_row: list[tuple[list, list]] = []
    for r in slot_log_rows:
        try:
            vibe_ids = _json.loads(r.vibe_ids or "[]")
        except (ValueError, TypeError):
            vibe_ids = []
        try:
            distances = _json.loads(r.distances or "[]")
        except (ValueError, TypeError):
            distances = []
        parsed_per_row.append((vibe_ids, distances))
        for vid in vibe_ids:
            if isinstance(vid, int):
                referenced_vibe_ids.add(vid)

    track_by_id = {}
    if track_ids:
        track_by_id = {
            t.id: t
            for t in session.exec(
                select(Track).where(col(Track.id).in_(track_ids))
            ).all()
        }
    vibe_by_id = {}
    if referenced_vibe_ids:
        vibe_by_id = {
            v.id: v
            for v in session.exec(
                select(Vibe).where(col(Vibe.id).in_(referenced_vibe_ids))
            ).all()
        }

    slot_in_log = []
    for r, (vibe_ids, distances) in zip(slot_log_rows, parsed_per_row):
        track = track_by_id.get(r.track_id)
        vibe_names = [
            (vibe_by_id[vid].name if vid in vibe_by_id else f"#{vid}")
            for vid in vibe_ids
        ]
        slot_in_log.append({
            "timestamp": r.timestamp,
            "track_title": track.title if track else "—",
            "track_artist": track.artist if track else "—",
            "vibe_names": ", ".join(vibe_names),
            "distances_str": (
                ", ".join(f"{float(d):.2f}" for d in distances)
                if distances
                else "—"
            ),
            "soft_membership_applied": r.soft_membership_applied,
        })

    # Drift indicator state — Plan 04 ships placeholder counts. The Plex-side
    # cross-check (orphan_count for "Composer · " playlists with no
    # ManagedPlaylist row + stale_count for desynced last_pushed_at) is a
    # deferred enhancement noted in the SUMMARY. The partial renders the
    # green/no-drift branch correctly with these values.
    managed_count = session.exec(
        select(func.count()).select_from(ManagedPlaylist)
    ).one()
    managed_count = (
        managed_count[0] if isinstance(managed_count, tuple) else int(managed_count)
    )
    drift = {
        "vibe_count": len([v for v in vibes if v.is_active]),
        "managed_count": managed_count,
        "plex_count": managed_count,  # placeholder; deferred enhancement
        "orphan_count": 0,
        "stale_count": 0,
    }

    # Phase 6.2 D-32 / VIBE-13 — LLM cost segmentation by purpose.
    # Aggregates over LLMUsage rows whose purpose starts with "vibe_".
    # Covers the three Phase 6.2 purposes (vibe_definitions_preamble,
    # vibe_assign_pass1, vibe_assign_pass2) AND the legacy
    # vibe_clustering_refine / vibe_clustering_recluster purposes.
    cost_by_purpose_rows = list(
        session.exec(
            select(LLMUsage.purpose, func.sum(LLMUsage.cost_estimate_usd))
            .where(col(LLMUsage.purpose).like("vibe_%"))
            .group_by(LLMUsage.purpose)
        ).all()
    )
    vibe_cost_by_purpose: dict = {}
    for row in cost_by_purpose_rows:
        # SQLModel returns tuples for grouped SELECTs in this style.
        if isinstance(row, tuple):
            purpose, total = row
        else:
            purpose, total = row[0], row[1]
        vibe_cost_by_purpose[purpose] = float(total or 0.0)
    vibe_cost_total = sum(vibe_cost_by_purpose.values())

    return templates.TemplateResponse(
        request,
        "pages/debug_vibes.html",
        {
            "active_page": "debug_vibes",
            "vibes": vibes,
            "vibe_member_counts": vibe_member_counts,
            "slot_in_log": slot_in_log,
            "drift": drift,
            "vibe_cost_by_purpose": vibe_cost_by_purpose,
            "vibe_cost_total": vibe_cost_total,
        },
    )
