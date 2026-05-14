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
    return await read_vibes_home(request, session)


@router.get("/vibes", response_class=HTMLResponse)
async def read_vibes_home(
    request: Request, session: Session = Depends(get_session),
):
    """UI-01 — vibes home / landing page.

    Renders one card per ACTIVE vibe (archived vibes excluded — D-28).
    Each card shows name + track count + optional description, plus a
    SUGG-10 "Find candidates" CTA when track_count < 25 (the CONTEXT
    discretion threshold).
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
        }))
    return templates.TemplateResponse(
        request,
        "pages/vibes_home.html",
        {"active_page": "vibes", "vibes": enriched},
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


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Session = Depends(get_session)):
    """Settings page with three service configuration cards.

    Phase 6.2 Plan 02 (WIZ-08 / D-24): also passes ``vibe_count`` so the
    template can render the destructive "Start Over" button only when
    ``Vibe.count() > 0`` — never offer destruction when there's nothing
    to destroy.

    Phase 7 Plan 02 (OPS-05): also passes today's LLM spend + cache hit %
    + breaker-paused state for the cost meter card.
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

    # Phase 7 Plan 02 (OPS-05) — Anthropic spend today + cache hit %.
    from datetime import datetime as _dt, timezone as _tz

    from app.models.llm_usage import LLMUsage
    from app.services.llm_cost_breaker import (
        DAILY_QUOTA, get_state as breaker_state,
    )

    midnight_iso = _dt.now(_tz.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()
    today_rows = session.exec(
        select(LLMUsage).where(LLMUsage.called_at >= midnight_iso)  # type: ignore[arg-type]
    ).all()
    today_calls = len(today_rows)
    today_cost_usd = sum((r.cost_estimate_usd or 0.0) for r in today_rows)
    total_cache_creation = sum(r.cache_creation_input_tokens or 0 for r in today_rows)
    total_cache_read = sum(r.cache_read_input_tokens or 0 for r in today_rows)
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
            # OPS-05 cost meter context
            "today_calls": today_calls,
            "today_cost_usd": today_cost_usd,
            "daily_quota": DAILY_QUOTA,
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
