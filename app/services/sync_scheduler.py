from __future__ import annotations

import asyncio
import logging
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


async def start_scheduler() -> None:
    """Start the scheduler and configure sync based on saved settings.

    - Loads sync interval from Plex extra_config (default 24h)
    - Schedules recurring sync
    - Triggers immediate auto-sync if Plex is configured and no prior sync exists (D-03)
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
