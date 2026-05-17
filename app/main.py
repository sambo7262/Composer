from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Configure logging so our logger.info() calls show up in container logs
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.routers import (
    api_analysis,
    api_chat,  # Phase 7 — retired (UI-06): every endpoint returns 404 for an explicit signal.
    api_discovery,  # Phase 8 Plan 04 — /api/discovery/{mb_id}/{add,dismiss,status-row}
    api_health,
    api_library,
    api_rating_sync,
    api_settings,
    api_setup,  # Phase 6
    api_suggestions,  # Phase 7 (Plan 03) — POST /api/suggestions/{rk}/dismiss
    api_sync,
    api_vibes,  # Phase 6 Plan 04
    api_webhooks,
    pages,
)
from app.services.encryption import get_encryptor
from app.services.event_bus import get_event_bus, start_dispatcher, stop_dispatcher
from app.services.sync_scheduler import start_scheduler, stop_scheduler


async def run_phase_61_migration() -> None:
    """Phase 6.1 D-NEW-09 — one-shot wipe of Phase 6 state on first deploy.

    Idempotent via MigrationLog(phase_id='6.1') gate row. Steps:

    1. Check gate: SELECT MigrationLog WHERE phase_id='6.1' AND
       completed_at IS NOT NULL. If present → return immediately.
    2. Insert MigrationLog(phase_id='6.1', completed_at=NULL) — defensive
       crash-recovery marker.
    3. Read all ManagedPlaylist rows. For each row with a non-null
       plex_rating_key, call plex_playlist_service.archive_playlist with
       ALL FOUR ARGS (plex_url, plex_token, plex_rating_key, composer_name)
       (Blocker #2). archive_playlist renames the Plex playlist to
       ``{name} (archived)`` AND deletes the ManagedPlaylist row.
       Best-effort: TIGHT except clause catches only the expected error
       types (Blocker #2 — WARNING #9). Bare ``except Exception`` is FORBIDDEN.
    4. DELETE FROM tables in FK-safe order:
       SlotInLog → TrackVibe → ManagedPlaylist (defensive) → Vibe.
    5. Reset SetupState id=1 row.
    6. UPDATE Track SET pending_slot_in=0 WHERE pending_slot_in=1.
    7. UPDATE MigrationLog SET completed_at=<iso-now> WHERE phase_id='6.1'.
    8. Log summary.

    All Plex API calls dispatched through asyncio.to_thread (Phase 5 D-09 /
    Pitfall 4). All DB work uses sync Session inside asyncio.to_thread. Plex
    playlist mutations serialized via asyncio.Semaphore(1) (D-25).

    Note on D-NEW-09 wording: the existing archive_playlist renames to
    ``(archived)``, not ``(legacy)``. This plan accepts the existing wording
    rather than introduce a separate rename_playlist + DELETE flow.
    """
    from datetime import datetime, timezone

    import httpx
    import plexapi.exceptions
    from sqlalchemy import delete, update
    from sqlmodel import Session, select

    from app.database import get_engine
    from app.models.track import Track
    from app.models.vibe import (
        ManagedPlaylist,
        MigrationLog,
        SetupState,
        SlotInLog,
        TrackVibe,
        Vibe,
    )
    from app.services.settings_service import (
        get_decrypted_credential,
        get_setting,
    )

    logger = logging.getLogger(__name__)

    # --- Step 1: gate check ---
    def _check_gate_sync() -> bool:
        with Session(get_engine()) as s:
            existing = s.exec(
                select(MigrationLog).where(MigrationLog.phase_id == "6.1")
            ).first()
            return existing is not None and existing.completed_at is not None

    if await asyncio.to_thread(_check_gate_sync):
        return

    # --- Step 2: insert in-flight gate marker ---
    def _insert_gate_sync() -> None:
        with Session(get_engine()) as s:
            existing = s.exec(
                select(MigrationLog).where(MigrationLog.phase_id == "6.1")
            ).first()
            if existing is None:
                s.add(MigrationLog(phase_id="6.1", completed_at=None))
                s.commit()

    await asyncio.to_thread(_insert_gate_sync)

    # --- Step 3: archive existing Composer · playlists ---
    def _read_managed_playlists_sync() -> list:
        with Session(get_engine()) as s:
            return list(s.exec(select(ManagedPlaylist)).all())

    def _get_plex_creds_sync() -> tuple:
        with Session(get_engine()) as s:
            ps = get_setting(s, "plex")
            if ps is None or not ps.is_configured:
                return ("", "")
            tok = get_decrypted_credential(s, "plex") or ""
            return (ps.url or "", tok)

    managed = await asyncio.to_thread(_read_managed_playlists_sync)
    plex_url, plex_token = await asyncio.to_thread(_get_plex_creds_sync)

    archived_count = 0
    if managed and plex_url and plex_token:
        # Lazy import so patch("app.services.plex_playlist_service.archive_playlist", ...)
        # in tests can swap the attribute on the module before this line resolves it.
        from app.services import plex_playlist_service as _pps

        sema = asyncio.Semaphore(1)
        for mp in managed:
            if not mp.plex_rating_key:
                continue
            try:
                async with sema:
                    # Blocker #2: FULL 4-arg signature. Calling with 3 args
                    # would raise TypeError silently if swallowed; tight
                    # except below surfaces the bug.
                    # The ManagedPlaylist title field is ``composer_name``
                    # (not ``name``) — Plan refers to ``mp.name`` but the
                    # actual SQLModel field is ``composer_name``.
                    await _pps.archive_playlist(
                        plex_url,
                        plex_token,
                        mp.plex_rating_key,
                        mp.composer_name,
                    )
                archived_count += 1
            except (
                plexapi.exceptions.PlexApiException,
                httpx.HTTPError,
                PermissionError,
                TypeError,
            ):
                # WARNING #9: tight except clause — NOT bare Exception.
                # TypeError specifically catches future arity drift on
                # archive_playlist (Blocker #2 regression guard) AND surfaces
                # it in logs.
                logger.exception(
                    "Phase 6.1 migration: archive_playlist failed for "
                    "ManagedPlaylist id=%s rk=%s name=%r",
                    mp.id, mp.plex_rating_key, mp.composer_name,
                )

    # --- Steps 4-7: clear DB rows + reset SetupState + clear pending_slot_in +
    #     mark migration complete. All sync under one Session for atomicity. ---
    def _wipe_sync() -> tuple:
        with Session(get_engine()) as s:
            slot_log_count = len(s.exec(select(SlotInLog)).all())
            tv_count = len(s.exec(select(TrackVibe)).all())
            mp_count = len(s.exec(select(ManagedPlaylist)).all())
            v_count = len(s.exec(select(Vibe)).all())

            # FK-safe order: SlotInLog → TrackVibe → ManagedPlaylist → Vibe.
            s.exec(delete(SlotInLog))
            s.exec(delete(TrackVibe))
            s.exec(delete(ManagedPlaylist))
            s.exec(delete(Vibe))

            # Reset SetupState id=1 (defensive: create if missing).
            state = s.exec(
                select(SetupState).where(SetupState.id == 1)
            ).first()
            if state is None:
                state = SetupState(id=1)
            state.step = "rating_source"
            state.draft_proposals_json = ""
            state.refinement_turn_count = 0
            state.last_llm_call_id = None
            state.recluster_mode = False
            state.completed_at = None
            s.add(state)

            # Clear Track.pending_slot_in.
            s.exec(
                update(Track).where(Track.pending_slot_in == 1).values(
                    pending_slot_in=0
                )
            )

            # Mark migration complete.
            gate = s.exec(
                select(MigrationLog).where(MigrationLog.phase_id == "6.1")
            ).first()
            if gate is not None:
                gate.completed_at = datetime.now(timezone.utc).isoformat()
                s.add(gate)

            s.commit()
            return (v_count, tv_count, mp_count, slot_log_count)

    v_count, tv_count, mp_count, slot_log_count = await asyncio.to_thread(
        _wipe_sync
    )

    logger.info(
        "Phase 6.1 migration: archived %d Plex playlists, cleared %d vibes, "
        "%d trackvibes, %d managed playlists (post-archive), %d slot-in logs.",
        archived_count, v_count, tv_count, mp_count, slot_log_count,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database, encryption key, event bus + dispatcher, sync scheduler.

    Order matters (Phase 5):
    1. init_db() — schema + migrations.
    2. get_encryptor() — generate encryption key on first startup.
    3. run_phase_61_migration() — one-shot Phase 6.1 D-NEW-09 wipe (gate-checked).
    4. get_event_bus() — ensure asyncio.Queue exists.
    5. start_dispatcher() — start consumer BEFORE any producer (scheduler/webhooks).
    6. start_scheduler() — APScheduler registers library_sync + plex_polling jobs.
    7. maybe_trigger_first_run_backfill() — fires once if Phase 5 just deployed
       onto a populated DB whose user_rating column is still NULL across the board.
    8. schedule_soft_negative_sweep() + schedule_weekly_maintenance() —
       Phase 7 / 7.1 cron jobs registered on the same scheduler singleton.
       ``schedule_weekly_maintenance`` is the Phase 7.1 follow-up combined
       prune + discovery tick (replaces the bare
       ``schedule_discovery_call_weekly`` registration; uses the same
       ``discovery_call_weekly`` job id so monitoring keeps working).
    """
    init_db()
    get_encryptor()
    # Phase 6.1 D-NEW-09: one-shot wipe of Phase 6 vibe state, gated by
    # MigrationLog(phase_id='6.1'). No-op on subsequent restarts.
    await run_phase_61_migration()
    # Phase 7 D-02: one-shot Suggestions queue bootstrap, gated by
    # MigrationLog(phase_id='7.0-suggestions-bootstrap'). No-op on
    # subsequent restarts and on fresh installs that bootstrapped via the
    # wizard finalize hook (D-01).
    from app.services.suggestions_service import run_phase_07_suggestions_bootstrap
    await run_phase_07_suggestions_bootstrap()
    # Phase 8 D-B4 + D-E2 + Pitfall 12 — discovery bootstrap. Stamps
    # CostMeterBaseline.deploy_at (home cost chip baseline), seeds
    # WeeklyCronState(id=1, last_tick_at=NULL), backfills Vibe.color from
    # the locked Tailwind 4 -500 palette, and best-effort backfills
    # Track.plex_artist_mbid via plex_client. Idempotent via MigrationLog
    # gate (phase_id='8.0-discovery-bootstrap').
    from app.services.discovery_service import run_phase_08_discovery_bootstrap
    await run_phase_08_discovery_bootstrap()
    # QUICK FIX (260517-lyw): one-shot collapse of pre-existing duplicate
    # DiscoveryCandidate rows (same mb_id, multiple rows from cross-vibe
    # LLM picks before write-time dedup was added). Idempotent via
    # MigrationLog gate (phase_id='8.1-discovery-dedupe-mb-id').
    from app.services.discovery_service import run_phase_08_1_discovery_dedupe_mb_id
    await run_phase_08_1_discovery_dedupe_mb_id()
    # Phase 5: queue → dispatcher → scheduler order is mandatory.
    get_event_bus()
    await start_dispatcher()
    await start_scheduler()
    # Phase 7 Plan 02 (B1) — register the SUGG-08 daily soft-negative
    # sweep job on the same AsyncIOScheduler singleton that
    # start_scheduler() just started. MUST be after start_scheduler() so
    # the scheduler is running before add_job runs.
    from app.services.sync_scheduler import schedule_soft_negative_sweep

    schedule_soft_negative_sweep()
    # Phase 7.1 D-C1 + follow-up — register the combined weekly maintenance
    # tick (prune Plex Suggestions playlist down to SuggestionsMirror, then
    # discovery_call_weekly). Same AsyncIOScheduler singleton; SAME ordering
    # requirement (must be after start_scheduler()). Job id is still
    # ``discovery_call_weekly`` so monitoring + existing tests querying by
    # job id keep working.
    from app.services.sync_scheduler import schedule_weekly_maintenance

    schedule_weekly_maintenance()
    # Phase 5 (D-10 / Pitfall 7): auto-trigger backfill if we have tracks but
    # nothing is rated — classic "first deploy onto an existing v1 library".
    from app.services.backfill_service import maybe_trigger_first_run_backfill

    asyncio.create_task(maybe_trigger_first_run_backfill())
    yield
    await stop_scheduler()
    await stop_dispatcher()


app = FastAPI(title="Composer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# UI timestamps render in America/Los_Angeles (user preference). Filter
# definition + registration helper live in app/utils/jinja_filters.py so
# test fixtures with their own Environment can stay in sync.
from app.utils.jinja_filters import register_filters as _register_filters
_register_filters(templates.env)

# Include routers
app.include_router(api_analysis.router)
app.include_router(api_chat.router)
app.include_router(api_health.router)
app.include_router(api_library.router)
app.include_router(api_settings.router)
app.include_router(api_sync.router)
app.include_router(api_webhooks.router)  # Phase 5
app.include_router(api_rating_sync.router)  # Phase 5
app.include_router(api_setup.router)  # Phase 6 — MUST be before pages.router
app.include_router(api_vibes.router)  # Phase 6 Plan 04 — MUST be before pages.router
app.include_router(api_suggestions.router)  # Phase 7 Plan 03 (UI-06 / D-10)
app.include_router(api_discovery.router)  # Phase 8 Plan 04 — discovery surface

# Register feature_chip_text helper as a Jinja2 global so templates can call it.
from app.services.vibe_helpers import feature_chip_text  # noqa: E402

templates.env.globals["feature_chip_text"] = feature_chip_text

app.include_router(pages.router)
