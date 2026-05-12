"""Phase 6 wizard router (D-06, D-07, D-08; WIZ-01..07).

Multi-step HTMX wizard at /setup. State persisted in SetupState (single row id=1).
Server returns HX-Redirect headers to advance between steps; refresh-safe; back/forward works.

Step 3's refinement loop is the user-flagged "core of the app" (06-CONTEXT.md):
LLM proposes -> user types feedback -> LLM refines -> repeat up to 10 turns ->
"Looks good" commits. The HTMX swap inside step 3 uses morph:innerHTML on
#proposal-cards (Pitfall 15 / D-09 alpine-morph) so each card's Alpine x-data
state survives swaps.

Form parsing: Annotated[str, Form()] + json.loads. NEVER use pydantic-Json
inside Form() (D-05 / FastAPI bug #10997 silently coerces wrong).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, func, select

from app.database import get_engine, get_session
from app.models.llm_usage import LLMUsage
from app.models.settings import ServiceConfig
from app.models.track import Track
from app.models.vibe import ManagedPlaylist, SetupState, TrackVibe, Vibe

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["setup"])


# ---------------------------------------------------------------------------
# Lazy-import shims — keep tests monkeypatchable + avoid circular deps.
# Mirrors taste_profile_service.py:312-315 + vibe_clusterer.py:678 patterns.
# ---------------------------------------------------------------------------

def get_templates():
    """Lazy import to avoid circular dep with app.main (Pattern E)."""
    from app.main import templates
    return templates


async def initial_cluster_proposal(*args, **kwargs):
    from app.services.vibe_clusterer import (
        initial_cluster_proposal as _fn,
    )
    return await _fn(*args, **kwargs)


async def refine_proposals(*args, **kwargs):
    from app.services.vibe_clusterer import refine_proposals as _fn
    return await _fn(*args, **kwargs)


async def create_playlist(*args, **kwargs):
    from app.services.plex_playlist_service import create_playlist as _fn
    return await _fn(*args, **kwargs)


async def assign_tracks_to_user_vibes(*args, **kwargs):
    """Phase 6.2 Plan 01 D-18 — lazy import shim so tests can monkeypatch
    this attribute on the module without touching the underlying service.

    REPLACES the Phase 6.1 user-led-mapping shim with the LLM-direct
    two-pass pipeline (VIBE-13 + VIBE-14).
    """
    from app.services.vibe_clusterer import (
        assign_tracks_to_user_vibes as _fn,
    )
    return await _fn(*args, **kwargs)


async def reslot_all_rated_tracks(*args, **kwargs):
    """Phase 6.1 Blocker #7 Option A — lazy import shim so finalize() can
    AWAIT reslot synchronously after Vibes are committed.  Tests monkeypatch
    this attribute to avoid hitting real Plex via slot_track.
    """
    from app.services.vibe_service import (
        reslot_all_rated_tracks as _fn,
    )
    return await _fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Module-level finalize status — mirrors backfill_service._backfill_status.
# Used by GET /api/setup/finalize/status (Plan 03 Task 3).
# ---------------------------------------------------------------------------

@dataclass
class FinalizeStatus:
    state: str = "idle"  # idle | running | completed | failed
    created: int = 0
    total: int = 0
    last_error: Optional[str] = None


_finalize_status: FinalizeStatus = FinalizeStatus()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_create_setup_state(session: Session) -> SetupState:
    """Single-row id=1 pattern (D-08)."""
    state = session.exec(select(SetupState).where(SetupState.id == 1)).first()
    if state is None:
        state = SetupState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def _is_webhook_already_configured(session: Session) -> bool:
    """D-06 conditional Step 2 check: skip when ServiceConfig.url for webhook is set."""
    row = session.exec(
        select(ServiceConfig).where(ServiceConfig.service_name == "webhook")
    ).first()
    return row is not None and bool(row.url)


def _count_rated_tracks(session: Session) -> int:
    """SELECT COUNT(*) FROM Track WHERE user_rating > 0."""
    n = session.exec(
        select(func.count()).select_from(Track).where(Track.user_rating > 0)  # type: ignore[arg-type]
    ).one()
    if isinstance(n, tuple):
        n = n[0]
    return int(n)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_llm_call_id(session: Session, purpose_prefix: str) -> Optional[int]:
    """Return the most recent LLMUsage.id for purposes starting with purpose_prefix."""
    row = session.exec(
        select(LLMUsage)
        .where(LLMUsage.purpose.like(f"{purpose_prefix}%"))  # type: ignore[union-attr]
        .order_by(LLMUsage.id.desc())  # type: ignore[union-attr]
    ).first()
    return row.id if row else None


def _render_proposal_cards(
    request: Request, proposals_obj, refinement_turn_count: int
) -> HTMLResponse:
    """Render the proposal-cards container partial (HTMX morph swap target).

    Returns inner HTML for the #proposal-cards div + the refinement input.
    """
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/proposal_cards_swap.html",
        {
            "draft_proposals": proposals_obj,
            "refinement_turn_count": refinement_turn_count,
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/step1/submit")
async def step1_submit(session: Session = Depends(get_session)):
    """Step 1 -> webhook | proposing (D-06 conditional skip)."""
    state = _get_or_create_setup_state(session)
    n_rated = _count_rated_tracks(session)

    # D-10 / Pitfall 3 cold-start gate.
    if n_rated < 30:
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    if _is_webhook_already_configured(session):
        state.step = "proposing"
        next_url = "/setup/propose"
    else:
        state.step = "webhook"
        next_url = "/setup/webhook"

    if state.started_at is None:
        state.started_at = _now_iso()
    session.add(state)
    session.commit()

    return Response(status_code=204, headers={"HX-Redirect": next_url})


@router.get("/rated-count", response_class=HTMLResponse)
async def rated_count(request: Request, session: Session = Depends(get_session)):
    """Returns the rated-count partial (HTMX swap for cold-start Refresh button)."""
    n_rated = _count_rated_tracks(session)
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/rated_count.html",
        {"n_rated": n_rated},
    )


@router.post("/step2/submit")
async def step2_submit(session: Session = Depends(get_session)):
    """Step 2 -> proposing."""
    state = _get_or_create_setup_state(session)
    state.step = "proposing"
    session.add(state)
    session.commit()
    return Response(status_code=204, headers={"HX-Redirect": "/setup/propose"})


@router.post("/propose/init", response_class=HTMLResponse)
async def propose_init(
    request: Request,
    vibe_names: Annotated[str, Form()],
    session: Session = Depends(get_session),
):
    """User-led Step 3 cluster proposal (Phase 6.2 VIBE-13 + VIBE-14).

    ``vibe_names`` is a JSON-array string sent by the textbox-stack form. Server
    parses, trims, case-insensitive dedupes, validates count (3-7), then calls
    :func:`app.services.vibe_clusterer.assign_tracks_to_user_vibes` (Phase 6.2
    Plan 01) for the LLM-direct two-pass pipeline (Pass 1 self-graded
    assignment + Pass 2 peer-context boundary review). The LLM picks
    per-track membership directly; k-means survives only as a centroid-
    summary generator (D-19). Final centroid is mean of LLM-assigned
    members (D-20).

    Form-parsing convention (Phase 6 D-05 / Pitfall 1): Annotated[str, Form()]
    + json.loads — NEVER pydantic.Json[Model] inside Form() (FastAPI #10997).
    """
    templates = get_templates()
    state = _get_or_create_setup_state(session)

    # --- Parse vibe_names ---
    try:
        raw_names = json.loads(vibe_names)
    except (json.JSONDecodeError, TypeError):
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": "Couldn't parse vibe names. Try again",
                "refinement_turn_count": 0,
            },
        )
    if not isinstance(raw_names, list):
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": "Vibe names must be a list",
                "refinement_turn_count": 0,
            },
        )

    # --- Trim + drop blanks + dedupe case-insensitive ---
    trimmed: list[str] = []
    seen_cf: set[str] = set()
    had_duplicate = False
    for n in raw_names:
        if not isinstance(n, str):
            continue
        t = n.strip()
        if not t:
            continue
        key = t.casefold()
        if key in seen_cf:
            had_duplicate = True
            continue
        seen_cf.add(key)
        trimmed.append(t)

    if had_duplicate:
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": (
                    "Duplicate vibe names (case-insensitive). "
                    "Each vibe needs a unique name"
                ),
                "refinement_turn_count": 0,
            },
        )

    if len(trimmed) < 3 or len(trimmed) > 7:
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": (
                    f"Type 3 to 7 vibe names "
                    f"(after trimming blanks, got {len(trimmed)})"
                ),
                "refinement_turn_count": 0,
            },
        )

    # --- LLM-direct two-pass assignment (Phase 6.2 VIBE-13 + VIBE-14) ---
    try:
        proposals = await assign_tracks_to_user_vibes(trimmed)
    except ValueError as exc:
        logger.warning(
            "assign_tracks_to_user_vibes rejected input: %s", exc
        )
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": str(exc),
                "refinement_turn_count": 0,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("assign_tracks_to_user_vibes failed")
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": f"Couldn't cluster your library: {exc}",
                "refinement_turn_count": 0,
            },
        )

    state.draft_proposals_json = proposals.model_dump_json()
    state.refinement_turn_count = 0
    # Phase 6.2: extend the "Re-show last cluster proposal" diagnostic
    # prefix to cover the new purposes (vibe_definitions_preamble,
    # vibe_assign_pass1, vibe_assign_pass2) AND keep matching the legacy
    # vibe_clustering_refine / vibe_clustering_recluster purposes. The
    # "vibe_" prefix is the smallest common ancestor.
    state.last_llm_call_id = _latest_llm_call_id(session, "vibe_")
    session.add(state)
    session.commit()

    return _render_proposal_cards(request, proposals, 0)


@router.post("/refine", response_class=HTMLResponse)
async def refine(
    request: Request,
    message: Annotated[str, Form()],
    session: Session = Depends(get_session),
):
    """One conversational refinement turn (D-04 / D-12 / D-34)."""
    from app.services.vibe_clusterer import VibeProposalSet

    state = _get_or_create_setup_state(session)

    # WR-05: reject empty messages BEFORE any LLM call. An empty textarea
    # would otherwise trigger a full LLM round-trip with user_message=""
    # (wasting a daily-cap turn from the Phase 5 LLMUsage 50/day circuit
    # breaker) and uselessly increment refinement_turn_count. We render
    # refine_error.html and return HTTP 200 so HTMX swaps the partial
    # into #proposal-cards (the existing morph:innerHTML target) — same
    # convention as the LLM-failure exception handler below. The user
    # sees a visible error and the prior proposal is preserved.
    if not message.strip():
        prior = (
            VibeProposalSet.model_validate_json(state.draft_proposals_json)
            if state.draft_proposals_json
            else None
        )
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": "Please enter what to change",
                "refinement_turn_count": state.refinement_turn_count,
                "draft_proposals": prior,
            },
        )

    # D-04 hard cap at 10. Re-render with cap copy + same prior proposals.
    if state.refinement_turn_count >= 10:
        prior = (
            VibeProposalSet.model_validate_json(state.draft_proposals_json)
            if state.draft_proposals_json
            else None
        )
        return _render_proposal_cards(request, prior, state.refinement_turn_count)

    if not state.draft_proposals_json:
        # Defensive: refine without an init -> render error banner.
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": "No initial proposal yet. Reload the page.",
                "refinement_turn_count": state.refinement_turn_count,
            },
        )

    prior = VibeProposalSet.model_validate_json(state.draft_proposals_json)

    # Phase 6 Plan 04 (D-20 / D-34) — when SetupState.recluster_mode is True,
    # pass it down so vibe_clusterer.refine_proposals uses purpose=
    # vibe_clustering_recluster (lets the Phase 7 cost dashboard segment).
    try:
        proposals = await refine_proposals(
            prior, message, recluster_mode=bool(state.recluster_mode)
        )
    except Exception as exc:  # noqa: BLE001 — surface any LLM failure to the user
        logger.exception("refine_proposals failed")
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/refine_error.html",
            {
                "reason": str(exc),
                "refinement_turn_count": state.refinement_turn_count,
                # Show the prior cards on failure so the user keeps progress.
                "draft_proposals": prior,
            },
        )

    state.draft_proposals_json = proposals.model_dump_json()
    state.refinement_turn_count = state.refinement_turn_count + 1
    state.last_llm_call_id = _latest_llm_call_id(session, "vibe_")
    session.add(state)
    session.commit()

    return _render_proposal_cards(request, proposals, state.refinement_turn_count)


@router.post("/finalize", response_class=HTMLResponse)
async def finalize(request: Request, session: Session = Depends(get_session)):
    """Finalize the wizard: create Vibe + ManagedPlaylist + TrackVibe + Plex playlists.

    Sequential under asyncio.Semaphore(1) per D-25 (NAS-friendly, single
    concurrent Plex mutation).
    """
    from app.services.settings_service import (
        get_decrypted_credential,
        get_setting,
    )
    from app.services.vibe_clusterer import VibeProposalSet

    global _finalize_status

    # WR-09: re-entrancy guard. The "Push to Plex" button (and the wizard's
    # Retry on failed banner) is double-clickable, and HTMX does not debounce
    # on its own. Two concurrent finalize POSTs would both proceed and clobber
    # _finalize_status / _recluster_status, AND insert duplicate Vibe rows
    # because the per-proposal idempotency-by-name is name-match only —
    # interleaved inserts can both pass the check before either commits.
    # While running, refuse the new call by re-rendering the in-progress
    # banner with HTTP 200 (matches the existing error-partial swap
    # convention; HTMX morphs it into the same target the original click
    # was already polling).
    if _finalize_status.state == "running":
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            {
                "state": "running",
                "created": _finalize_status.created,
                "total": _finalize_status.total,
                "error": None,
            },
        )

    state = _get_or_create_setup_state(session)
    if not state.draft_proposals_json:
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            {
                "state": "failed",
                "created": 0,
                "total": 0,
                "error": "No proposals to finalize.",
            },
        )

    proposals_set = VibeProposalSet.model_validate_json(state.draft_proposals_json)

    # Plex creds.
    plex_setting = get_setting(session, "plex")
    if plex_setting is None or not plex_setting.is_configured:
        templates = get_templates()
        return templates.TemplateResponse(
            request,
            "partials/push_to_plex_banner.html",
            {
                "state": "failed",
                "created": 0,
                "total": 0,
                "error": "Plex is not configured.",
            },
        )
    plex_url = plex_setting.url
    plex_token = get_decrypted_credential(session, "plex") or ""

    # Idempotent Retry support: skip vibes whose ManagedPlaylist already links.
    existing_managed_vibe_ids = set(
        row.vibe_id
        for row in session.exec(select(ManagedPlaylist)).all()
        if row.vibe_id is not None
    )

    targets = [
        p for p in proposals_set.proposals if p.action != "dropped"
    ]
    total = len(targets)

    _finalize_status = FinalizeStatus(state="running", created=0, total=total)

    sema = asyncio.Semaphore(1)  # D-25
    created_count = 0
    last_error: Optional[str] = None

    # Build the ratingKey lookup: rated_track_index -> rating_key.
    index_to_key = {
        row["index"]: row["rating_key"] for row in proposals_set.rated_track_index_map
    }

    def _insert_vibe_sync(prop) -> int:
        with Session(get_engine()) as s2:
            v = Vibe(
                name=prop.name,
                description=prop.description,
                centroid_energy=(prop.centroid or {}).get("energy"),
                centroid_tempo=(prop.centroid or {}).get("tempo"),
                centroid_danceability=(prop.centroid or {}).get("danceability"),
                centroid_valence=(prop.centroid or {}).get("valence"),
                spread_energy=(prop.spread or {}).get("energy"),
                spread_tempo=(prop.spread or {}).get("tempo"),
                spread_danceability=(prop.spread or {}).get("danceability"),
                spread_valence=(prop.spread or {}).get("valence"),
                silhouette_score=prop.silhouette,
                created_at=_now_iso(),
                is_active=True,
            )
            s2.add(v)
            s2.commit()
            s2.refresh(v)
            return v.id  # type: ignore[return-value]

    def _link_managed_to_vibe_sync(playlist_rk: str, vibe_id: int) -> None:
        with Session(get_engine()) as s2:
            mp = s2.exec(
                select(ManagedPlaylist).where(
                    ManagedPlaylist.plex_rating_key == playlist_rk
                )
            ).first()
            if mp is not None:
                mp.vibe_id = vibe_id
                s2.add(mp)
                s2.commit()

    def _insert_trackvibes_sync(
        vibe_id: int, member_keys: list[str], distance: float = 0.0
    ) -> None:
        with Session(get_engine()) as s2:
            for rk in member_keys:
                t = s2.exec(
                    select(Track).where(Track.plex_rating_key == rk)
                ).first()
                if t is None or t.id is None:
                    continue
                # Avoid double-insert (composite PK).
                existing = s2.exec(
                    select(TrackVibe).where(
                        TrackVibe.track_id == t.id, TrackVibe.vibe_id == vibe_id
                    )
                ).first()
                if existing is not None:
                    continue
                s2.add(
                    TrackVibe(
                        track_id=t.id,
                        vibe_id=vibe_id,
                        distance=distance,
                        assigned_at=_now_iso(),
                        assigned_by="cluster",
                    )
                )
            s2.commit()

    for prop in targets:
        # Resolve member rating keys from seed_track_indices (and any other
        # indices materialize_clusters added — currently same set).
        member_keys: list[str] = []
        for idx in prop.seed_track_indices:
            rk = index_to_key.get(idx)
            if rk:
                member_keys.append(str(rk))

        if not member_keys:
            logger.warning("Skipping proposal %r — no member rating keys", prop.name)
            continue

        try:
            async with sema:
                vibe_id = await asyncio.to_thread(_insert_vibe_sync, prop)
                playlist_name = f"Composer · {prop.name}"
                playlist_rk = await create_playlist(
                    plex_url, plex_token, playlist_name, member_keys, vibe_id
                )
                await asyncio.to_thread(
                    _link_managed_to_vibe_sync, playlist_rk, vibe_id
                )
                await asyncio.to_thread(
                    _insert_trackvibes_sync, vibe_id, member_keys
                )
            created_count += 1
            _finalize_status.created = created_count
        except Exception as exc:  # noqa: BLE001
            logger.exception("Finalize failed on proposal %r", prop.name)
            last_error = str(exc)
            break

    if created_count == total:
        # Phase 6.1 D-NEW-01 / D-NEW-03 / Blocker #7 Option A:
        # Now that all Vibe + ManagedPlaylist rows exist, AWAIT
        # reslot_all_rated_tracks SYNCHRONOUSLY so EVERY rated track lands in
        # its nearest vibe via the existing slot_track logic (per-track lock,
        # soft-margin cap=2, additive Plex push). The HTTP response will NOT
        # return until slotting completes — on a 600-track library this can
        # take ~30-90s; the wizard's sticky CTA already shows a loading state
        # during the in-flight POST (Phase 6 D-09).
        #
        # Why synchronous (not fire-and-forget): success criterion "≥30
        # tracks/vibe after finalize" must be user-observable when /setup/done
        # renders. Fire-and-forget would let the user navigate to "All set!"
        # while slotting is still in progress.
        try:
            slotted = await reslot_all_rated_tracks()
            logger.info(
                "Phase 6.1 finalize: reslot_all_rated_tracks slotted %d tracks",
                slotted,
            )
        except Exception:  # noqa: BLE001 — never break finalize on reslot
            logger.exception(
                "Phase 6.1 finalize: reslot_all_rated_tracks failed; "
                "user can recover via /debug/vibes reslot button"
            )

        state.step = "done"
        state.completed_at = _now_iso()
        session.add(state)
        session.commit()
        _finalize_status = FinalizeStatus(
            state="completed", created=created_count, total=total
        )
        out_state = "completed"
    else:
        # Partial failure: keep step at confirming/proposing for retry.
        if state.step != "confirming":
            state.step = "confirming"
            session.add(state)
            session.commit()
        _finalize_status = FinalizeStatus(
            state="failed",
            created=created_count,
            total=total,
            last_error=last_error,
        )
        out_state = "failed"

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/push_to_plex_banner.html",
        {
            "state": out_state,
            "created": created_count,
            "total": total,
            "error": last_error,
        },
    )


@router.get("/finalize/status", response_class=HTMLResponse)
async def finalize_status(request: Request):
    """HTMX-poll endpoint for the push_to_plex_banner running state."""
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/push_to_plex_banner.html",
        {
            "state": _finalize_status.state,
            "created": _finalize_status.created,
            "total": _finalize_status.total,
            "error": _finalize_status.last_error,
        },
    )


@router.post("/reset")
async def reset(session: Session = Depends(get_session)):
    """Reset SetupState; redirect to /setup."""
    state = _get_or_create_setup_state(session)
    state.step = "rating_source"
    state.draft_proposals_json = ""
    state.refinement_turn_count = 0
    state.last_llm_call_id = None
    # Phase 6 Plan 04 (D-20) — clear recluster_mode on full reset; "Run setup
    # wizard again" should NOT silently leave us in re-cluster mode.
    state.recluster_mode = False
    session.add(state)
    session.commit()
    return Response(status_code=204, headers={"HX-Redirect": "/setup"})


@router.post("/start-over")
async def start_over(session: Session = Depends(get_session)):
    """WIZ-08 — user-triggered wizard reset (Phase 6.2 D-24..D-30).

    Mirrors :func:`run_phase_61_migration` in ``app/main.py`` minus the
    ``MigrationLog`` gate (D-27 — WIZ-08 is repeatable, not one-shot).

    Step-by-step:

    1. Read all ManagedPlaylist rows + Plex credentials (sync DB via to_thread).
    2. For each ManagedPlaylist with a non-null plex_rating_key: archive
       on Plex with a date-stamped suffix ``(archived YYYY-MM-DD)`` (D-26).
       **Best-effort** with TIGHT except clause — only PlexApiException /
       httpx.HTTPError / PermissionError / TypeError. Never bare
       ``except Exception`` (WARNING #9 / T-062-15).
    3. DB wipe in FK-safe order (SlotInLog → TrackVibe → ManagedPlaylist →
       Vibe) + SetupState reset + Track.pending_slot_in clear. ONE
       ``Session.commit()`` inside ONE ``asyncio.to_thread`` call (D-27 atomic).
    4. Return 204 + ``HX-Redirect: /setup`` so HTMX bounces the browser
       into a fresh wizard.

    Preserves (T3 / T-062-11): ServiceConfig (plex/anthropic/lidarr),
    MigrationLog (T-062-18), LLMUsage (cost history / T-062-19), EventLog
    (diagnostics).

    Idempotency (D-29) is naturally emergent — the second call finds zero
    ManagedPlaylist rows → zero archive calls; empty-table DELETEs are
    0-row affects; SetupState already at the reset values.
    """
    from datetime import date

    import httpx
    import plexapi.exceptions
    from sqlalchemy import delete, update

    from app.models.vibe import (
        ManagedPlaylist as MP,
        SetupState as SS,
        SlotInLog as SL,
        TrackVibe as TV,
        Vibe as VB,
    )
    from app.services.settings_service import (
        get_decrypted_credential,
        get_setting,
    )

    suffix = f"(archived {date.today().isoformat()})"

    # --- Step 1: read managed playlists + Plex credentials ---
    def _read_managed_sync() -> list:
        with Session(get_engine()) as s:
            return list(s.exec(select(MP)).all())

    def _get_plex_creds_sync() -> tuple[str, str]:
        with Session(get_engine()) as s:
            ps = get_setting(s, "plex")
            if ps is None or not ps.is_configured:
                return ("", "")
            tok = get_decrypted_credential(s, "plex") or ""
            return (ps.url or "", tok)

    managed = await asyncio.to_thread(_read_managed_sync)
    plex_url, plex_token = await asyncio.to_thread(_get_plex_creds_sync)

    # --- Step 2: archive Plex playlists (best-effort, tight except) ---
    # Lazy import so tests can monkeypatch
    # ``app.services.plex_playlist_service.archive_playlist`` on the module
    # before this line resolves it (mirrors Phase 6.1 migration pattern).
    from app.services import plex_playlist_service as _pps

    if managed and plex_url and plex_token:
        sema = asyncio.Semaphore(1)  # D-25 — single concurrent Plex mutation
        for mp in managed:
            if not mp.plex_rating_key:
                continue
            try:
                async with sema:
                    await _pps.archive_playlist(
                        plex_url,
                        plex_token,
                        mp.plex_rating_key,
                        mp.composer_name,
                        suffix=suffix,
                    )
            except (
                plexapi.exceptions.PlexApiException,
                httpx.HTTPError,
                PermissionError,
                TypeError,
            ):
                # WARNING #9 (Phase 6.1) — tight except; never bare Exception.
                # TypeError specifically catches future arity drift on
                # archive_playlist.
                logger.exception(
                    "WIZ-08 start-over: archive_playlist failed for "
                    "ManagedPlaylist id=%s rk=%s name=%r",
                    mp.id, mp.plex_rating_key, mp.composer_name,
                )

    # --- Step 3: DB wipe + SetupState reset + pending_slot_in clear ---
    # ONE Session.commit() inside ONE asyncio.to_thread for atomicity (D-27).
    def _wipe_sync() -> None:
        with Session(get_engine()) as s:
            # FK-safe DELETE order: SlotInLog → TrackVibe → ManagedPlaylist → Vibe.
            s.exec(delete(SL))
            s.exec(delete(TV))
            s.exec(delete(MP))
            s.exec(delete(VB))

            # Reset SetupState id=1 (defensive: create if missing).
            state = s.exec(select(SS).where(SS.id == 1)).first()
            if state is None:
                state = SS(id=1)
            state.step = "rating_source"
            state.draft_proposals_json = ""
            state.refinement_turn_count = 0
            state.last_llm_call_id = None
            state.recluster_mode = False
            state.completed_at = None
            state.started_at = None
            s.add(state)

            # Clear Track.pending_slot_in.
            s.exec(
                update(Track).where(Track.pending_slot_in == 1).values(
                    pending_slot_in=0
                )
            )

            s.commit()

    await asyncio.to_thread(_wipe_sync)

    return Response(status_code=204, headers={"HX-Redirect": "/setup"})
