from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session

from app.database import get_engine
from app.services.settings_service import get_setting, is_service_configured
from app.services.sync_service import get_last_sync_info, run_sync

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the singleton AsyncIOScheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def schedule_sync(interval_hours: int) -> None:
    """Schedule (or reschedule) the recurring library sync job.

    Phase 8 D-E3 / DISC-08 — switched from IntervalTrigger to CronTrigger
    anchored at 03:00 UTC for the standard cadences (24h / 12h / 6h).
    Rationale: APScheduler's MemoryJobStore does NOT persist next_run_time
    across process restarts, so IntervalTrigger compounds drift on every
    container restart (NAS UAT 2026-05-16 confirmed: 48h stale on a 24h
    schedule). CronTrigger anchors to wall-clock so the schedule is stable
    across redeploys; coalesce=True + misfire_grace_time=3600 collapses
    missed ticks into ONE catch-up at boot; max_instances=1 prevents
    concurrent runs.

    Non-standard interval_hours values (anything other than 24/12/6) fall
    back to IntervalTrigger for back-compat with custom configurations.

    Uses replace_existing=True to ensure only one sync job exists (T-02-10).
    """
    scheduler = get_scheduler()
    # Remove existing job if present
    if scheduler.get_job("library_sync"):
        scheduler.remove_job("library_sync")

    if interval_hours == 24:
        trigger = CronTrigger(hour=3, minute=0, timezone="UTC")
    elif interval_hours == 12:
        trigger = CronTrigger(hour="3,15", minute=0, timezone="UTC")
    elif interval_hours == 6:
        trigger = CronTrigger(hour="3,9,15,21", minute=0, timezone="UTC")
    else:
        trigger = IntervalTrigger(hours=interval_hours)

    scheduler.add_job(
        _trigger_sync,
        trigger=trigger,
        id="library_sync",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
        name=f"Library sync ({interval_hours}h cadence)",
    )
    logger.info(
        "Scheduled library sync (%dh cadence) with trigger=%s",
        interval_hours, type(trigger).__name__,
    )


async def _trigger_sync() -> None:
    """Job function called by APScheduler. Launches run_sync as a task."""
    asyncio.create_task(run_sync())


def schedule_polling(interval_minutes: int = 5) -> None:
    """Register the Plex polling job alongside library_sync (D-08, EVT-03).

    Polls the SAME AsyncIOScheduler singleton — only one scheduler instance
    process-wide. Job id 'plex_polling' mirrors 'library_sync' naming.
    """
    scheduler = get_scheduler()
    if scheduler.get_job("plex_polling"):
        scheduler.remove_job("plex_polling")
    scheduler.add_job(
        _trigger_polling,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="plex_polling",
        replace_existing=True,
        name=f"Plex polling every {interval_minutes}m",
    )
    logger.info("Scheduled Plex polling every %d minutes", interval_minutes)


async def _trigger_polling() -> None:
    """Job function called by APScheduler. Fires-and-forgets a poll task."""
    # Lazy import to avoid module-load-time circular dep with poll_service
    from app.services.poll_service import run_poll

    asyncio.create_task(run_poll())


def schedule_soft_negative_sweep() -> None:
    """Phase 7 (Plan 02, B1) — daily SUGG-08 cron caller.

    Registers a cron job on the singleton AsyncIOScheduler that fires
    ``suggestions_service.handle_soft_negative_sweep`` at UTC 04:00 daily.
    Without this caller, ``NegativeSignal(signal_type='soft')`` rows are
    never written in production and SUGG-08 is not functionally delivered.

    UTC 04:00 chosen because:
      - Quiet hour for music listening (most users not actively scrobbling).
      - Avoids overlapping with TrackPlayed-driven refill bursts (D-03
        threshold-only refill cadence).
      - Same low-traffic window used by other arr-stack cron tasks in
        self-hosted setups.

    ``replace_existing=True`` mirrors ``schedule_sync`` / ``schedule_polling``
    so the job is idempotent across restarts AND across settings reloads.
    """
    # Lazy import to keep sync_scheduler import-graph small and avoid a
    # circular dep with suggestions_service (which imports from
    # plex_playlist_service which imports from settings_service, etc.).
    from app.services.suggestions_service import handle_soft_negative_sweep

    scheduler = get_scheduler()
    if scheduler.get_job("suggestions_soft_negative_sweep"):
        scheduler.remove_job("suggestions_soft_negative_sweep")
    scheduler.add_job(
        handle_soft_negative_sweep,
        trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
        id="suggestions_soft_negative_sweep",
        replace_existing=True,
        name="Suggestions soft-negative sweep (daily 04:00 UTC)",
    )
    logger.info(
        "Scheduled suggestions soft-negative sweep daily at 04:00 UTC"
    )


def schedule_discovery_call_weekly() -> None:
    """Phase 7.1 D-C1 — register the weekly LLM discovery call cron.

    Fires Sunday 03:00 UTC — off-peak for US timezones, aligned with
    existing infrastructure (matches ``sync_service`` /
    ``soft_negative_sweep`` cadence). Mirrors
    :func:`schedule_soft_negative_sweep` byte-for-byte; only the cron
    schedule + handler differ.

    ``replace_existing=True`` mirrors :func:`schedule_sync` /
    :func:`schedule_polling` / :func:`schedule_soft_negative_sweep` so the
    job is idempotent across restarts AND across settings reloads.

    The companion startup catch-up logic lives in :func:`start_scheduler`
    below (D-C2 — fire one immediately if ``last_discovery_run_at`` is
    NULL or > 7 days ago). The catch-up gate is placed AFTER the
    existing ``with Session(engine) as session:`` block (W9) so it
    never opens a nested Session.

    Kept available for direct callers / tests (the Phase 7.1
    ``TestScheduleDiscoveryCallWeekly`` exercises it). Production
    lifespan wiring uses :func:`schedule_weekly_maintenance` instead —
    that registers a combined prune-then-discovery tick at the same
    cron schedule with the SAME job id (``discovery_call_weekly``) so
    callers querying by job id continue to find a single registered job.
    """
    # Lazy import keeps the import-graph small and avoids a circular
    # dep with suggestions_discovery (which imports from
    # taste_profile_service, anthropic_client, etc.).
    from app.services.suggestions_discovery import discovery_call_weekly

    scheduler = get_scheduler()
    if scheduler.get_job("discovery_call_weekly"):
        scheduler.remove_job("discovery_call_weekly")
    scheduler.add_job(
        discovery_call_weekly,
        trigger=CronTrigger(
            day_of_week="sun", hour=3, minute=0, timezone="UTC",
        ),
        id="discovery_call_weekly",
        replace_existing=True,
        name="Discovery call weekly (Sundays 03:00 UTC)",
    )
    logger.info(
        "Scheduled discovery call weekly on Sundays at 03:00 UTC"
    )


async def _weekly_maintenance_tick() -> None:
    """Phase 7.1 follow-up — combined Sunday 03:00 UTC maintenance tick.

    Order (NON-NEGOTIABLE — prune MUST run before discovery so the fresh
    LLM picks land in a freshly-pruned Plex playlist):

      1. ``prune_suggestions_playlist_to_mirror(plex_url, plex_token)`` —
         best-effort. Wrapped in a try/except so a prune failure does
         NOT block the discovery call from running.
      2. ``discovery_call_weekly()`` — unchanged signature + semantics.

    Plex creds come from the same ``_read_plex_creds_sync`` helper used
    by ``suggestions_service``. If Plex is not configured (creds empty),
    the prune helper itself short-circuits with a log line.
    """
    # Lazy imports — keeps sync_scheduler's import graph minimal and
    # mirrors the existing :func:`schedule_discovery_call_weekly` pattern.
    from app.services.plex_playlist_service import (
        prune_suggestions_playlist_to_mirror,
    )
    from app.services.suggestions_discovery import discovery_call_weekly
    from app.services.suggestions_service import _read_plex_creds_sync

    plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)

    try:
        result = await prune_suggestions_playlist_to_mirror(
            plex_url, plex_token,
        )
        logger.info(
            "Weekly maintenance: prune removed=%d, mirror_size=%d, "
            "final_plex_count=%d, still_present=%d",
            len(result.removed),
            result.mirror_size,
            result.final_plex_count,
            len(result.still_present_after_remove),
        )
    except Exception:
        # Best-effort — prune failure must not block discovery.
        logger.exception(
            "Weekly maintenance: prune step failed; continuing to "
            "discovery_call_weekly anyway."
        )

    await discovery_call_weekly()

    # Phase 8 D-B1 — step 3: artist discovery. Best-effort.
    # Lazy import to keep the import-graph minimal and avoid circular dep
    # with discovery_service (which imports from anthropic_client / settings).
    try:
        from app.services.discovery_service import artist_discovery_call_weekly
        await artist_discovery_call_weekly()
        logger.info("Weekly maintenance step 3: artist discovery ok")
    except Exception:
        logger.exception(
            "Weekly maintenance step 3: artist discovery failed; "
            "continuing to step 4."
        )

    # Phase 8 D-B4 — step 4: stamp WeeklyCronState.last_tick_at.
    # Best-effort; failure surfaces in logs but doesn't break the rest of
    # the tick. Source of truth for the home-page "next refresh in Nd" chip.
    try:
        from app.services.discovery_service import update_weekly_cron_state
        await update_weekly_cron_state(
            datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Weekly maintenance step 4: WeeklyCronState stamp ok")
    except Exception:
        logger.exception(
            "Weekly maintenance step 4: WeeklyCronState stamp failed."
        )


def schedule_weekly_maintenance() -> None:
    """Phase 7.1 follow-up — register the combined weekly prune + discovery cron.

    Registers a SINGLE cron job at Sun 03:00 UTC pointing at
    :func:`_weekly_maintenance_tick`. Reuses the existing
    ``discovery_call_weekly`` job id so:

      - ``replace_existing=True`` cleanly evicts any prior registration
        from :func:`schedule_discovery_call_weekly` (back-compat with
        ``schedule_sync`` / ``schedule_polling`` /
        ``schedule_soft_negative_sweep`` idempotency contracts).
      - The existing
        ``TestLifespanRegistersDiscoveryCallWeekly`` test (which queries
        ``scheduler.get_job('discovery_call_weekly')``) keeps passing.

    The companion D-C2 startup catch-up still lives in
    :func:`start_scheduler` and now also runs prune before discovery
    on the catch-up path.
    """
    scheduler = get_scheduler()
    if scheduler.get_job("discovery_call_weekly"):
        scheduler.remove_job("discovery_call_weekly")
    scheduler.add_job(
        _weekly_maintenance_tick,
        trigger=CronTrigger(
            day_of_week="sun", hour=3, minute=0, timezone="UTC",
        ),
        id="discovery_call_weekly",
        replace_existing=True,
        name="Weekly maintenance: prune + discovery (Sundays 03:00 UTC)",
    )
    logger.info(
        "Scheduled weekly maintenance (prune + discovery) on Sundays at "
        "03:00 UTC"
    )


async def start_scheduler() -> None:
    """Start the scheduler and configure sync based on saved settings.

    - Loads sync interval from Plex extra_config (default 24h)
    - Schedules recurring sync
    - Triggers immediate auto-sync if Plex is configured and no prior sync exists (D-03)
    - Phase 7.1 D-C2 — schedules a 10s-delayed catch-up
      ``discovery_call_weekly`` if ``DiscoveryState.last_discovery_run_at``
      is NULL or > 7 days ago (W9 — gate placed OUTSIDE the with-Session
      block so it does not open a nested session via
      :func:`read_discovery_state`).
    """
    scheduler = get_scheduler()
    scheduler.start()
    logger.info("Sync scheduler started")

    # Load sync interval from settings
    interval_hours = 24  # default
    polling_enabled = False  # Phase 5: opt-in only — webhooks are the primary path
    try:
        engine = get_engine()
        with Session(engine) as session:
            setting = get_setting(session, "plex")
            if setting and setting.extra_config:
                interval_hours = setting.extra_config.get("sync_interval_hours", 24)
                polling_enabled = bool(setting.extra_config.get("plex_polling_enabled", False))

            # Schedule recurring sync
            schedule_sync(interval_hours)

            # Auto-sync on startup if Plex configured and no prior sync (D-03)
            # Delay auto-sync by 10 seconds to let the app fully start and serve requests first
            sync_info = get_last_sync_info(session)
            if sync_info["last_sync_completed"] is None and is_service_configured(session, "plex"):
                logger.info("No prior sync found and Plex is configured -- scheduling auto-sync in 10s")

                async def _delayed_auto_sync():
                    await asyncio.sleep(10)
                    await run_sync()

                asyncio.create_task(_delayed_auto_sync())

        # Phase 5 polling is OPT-IN. Set plex_polling_enabled=true in Plex extra_config
        # to enable the 5-min poll job. Default OFF — webhooks (Plex Pass) are the primary
        # event source, and the recurring library_sync at `interval_hours` covers reconciliation.
        if polling_enabled:
            schedule_polling(interval_minutes=5)
            logger.info("Plex polling enabled (5min interval)")
        else:
            logger.info("Plex polling disabled (webhooks + nightly library_sync handle reconciliation)")
    except Exception:
        # Schedule with default even if settings load fails
        schedule_sync(interval_hours)
        # Polling stays opt-in even on error path.
        logger.exception("Error loading sync settings, using default %dh interval", interval_hours)

    # ====================================================================
    # Phase 8 D-E3 / DISC-08 — library-sync missed-tick catch-up gate.
    # Mirrors Phase 7.1 D-C2 (discovery catch-up below) but for the
    # library_sync schedule. Placed AFTER the first-run auto-sync block
    # above (so first-run on a virgin SyncState takes precedence) and
    # BEFORE the discovery catch-up gate (so library state is fresh by
    # the time discovery catch-up fires).
    #
    # NAS UAT 2026-05-16: container restarts can leave the schedule 48h+
    # stale even on a 24h cadence (MemoryJobStore loses next_run_time on
    # process exit). Combined with the CronTrigger swap in schedule_sync,
    # this gate makes the daily sync observably reliable.
    # ====================================================================
    try:
        engine = get_engine()
        with Session(engine) as session:
            sync_info = get_last_sync_info(session)
            last_done = sync_info.get("last_sync_completed")
        if last_done is not None:
            try:
                last_dt = datetime.fromisoformat(last_done)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - last_dt
                threshold = timedelta(hours=interval_hours) + timedelta(hours=1)
                if age > threshold:
                    logger.warning(
                        "library_sync stale: last completed %s ago "
                        "(> %s + 1h grace); scheduling catch-up sync in 15s",
                        age, timedelta(hours=interval_hours),
                    )

                    async def _delayed_catch_up_sync():
                        await asyncio.sleep(15)
                        await run_sync()

                    asyncio.create_task(_delayed_catch_up_sync())
            except ValueError:
                logger.warning(
                    "library_sync catch-up: malformed "
                    "last_sync_completed=%r; skipping gate",
                    last_done,
                )
    except Exception:
        logger.exception(
            "Failed to evaluate library_sync catch-up gate; "
            "scheduler startup continues"
        )

    # ====================================================================
    # Phase 7.1 D-C2 (W9) — discovery startup catch-up gate.
    # PLACED OUTSIDE the with-Session block above so read_discovery_state()
    # (which opens its own Session via _read_discovery_state_sync) does
    # not create a nested-session pattern. Resilient to
    # NAS-asleep-on-Sunday — first restart after the missed cron picks
    # up immediately.
    # ====================================================================
    try:
        from app.services import suggestions_discovery as _sd

        state_row = await _sd.read_discovery_state()
        last_run = state_row.last_discovery_run_at
        needs_catch_up = False
        if last_run is None:
            needs_catch_up = True
        else:
            try:
                last_run_dt = datetime.fromisoformat(last_run)
                if last_run_dt.tzinfo is None:
                    last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - last_run_dt
                needs_catch_up = age >= timedelta(days=7)
            except ValueError:
                # Malformed timestamp → treat as needing catch-up.
                needs_catch_up = True

        if needs_catch_up:
            logger.info(
                "Last discovery run is NULL or > 7 days old — "
                "scheduling catch-up weekly maintenance (prune + "
                "discovery_call_weekly) in 10s"
            )

            async def _delayed_catch_up_discovery():
                await asyncio.sleep(10)
                # Re-resolve handlers at call time so tests that
                # monkeypatch ``suggestions_discovery.discovery_call_weekly``
                # (and the prune helper) work end-to-end. Lazy import
                # for the import-graph reasons noted on
                # schedule_discovery_call_weekly.
                from app.services import (
                    plex_playlist_service as _pps,
                    suggestions_discovery as _sd_inner,
                    suggestions_service as _ss,
                )
                # Phase 7.1 follow-up — prune runs FIRST so the catch-up
                # discovery picks land in a freshly-pruned playlist.
                # Best-effort: prune failure must not block discovery.
                try:
                    plex_url, plex_token = await asyncio.to_thread(
                        _ss._read_plex_creds_sync
                    )
                    await _pps.prune_suggestions_playlist_to_mirror(
                        plex_url, plex_token,
                    )
                except Exception:
                    logger.exception(
                        "Catch-up: prune step failed; continuing to "
                        "discovery_call_weekly anyway."
                    )
                await _sd_inner.discovery_call_weekly()

            asyncio.create_task(_delayed_catch_up_discovery())
    except Exception:
        # Best-effort — failure to read DiscoveryState must not
        # break the scheduler startup path.
        logger.exception(
            "Failed to evaluate discovery catch-up gate; "
            "scheduler startup continues"
        )


async def stop_scheduler() -> None:
    """Shut down the scheduler cleanly."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Sync scheduler stopped")


def update_sync_schedule(interval_hours: int) -> None:
    """Update the sync schedule interval dynamically (D-04).

    Called from settings endpoint when user changes the interval.
    Only updates if the scheduler is currently running.
    """
    scheduler = get_scheduler()
    if scheduler.running:
        schedule_sync(interval_hours)
        logger.info("Sync schedule updated to every %d hours", interval_hours)
