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

    Uses replace_existing=True to ensure only one sync job exists (T-02-10).
    """
    scheduler = get_scheduler()
    # Remove existing job if present
    if scheduler.get_job("library_sync"):
        scheduler.remove_job("library_sync")
    scheduler.add_job(
        _trigger_sync,
        trigger=IntervalTrigger(hours=interval_hours),
        id="library_sync",
        replace_existing=True,
        name=f"Library sync every {interval_hours}h",
    )
    logger.info("Scheduled library sync every %d hours", interval_hours)


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
                "scheduling catch-up discovery_call_weekly in 10s"
            )

            async def _delayed_catch_up_discovery():
                await asyncio.sleep(10)
                # Re-resolve the handler at call time so tests that
                # monkeypatch ``suggestions_discovery.discovery_call_weekly``
                # work end-to-end. Lazy import for the import-graph
                # reasons noted on schedule_discovery_call_weekly.
                from app.services import (
                    suggestions_discovery as _sd_inner,
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
