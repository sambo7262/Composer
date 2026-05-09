from __future__ import annotations

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
    try:
        engine = get_engine()
        with Session(engine) as session:
            setting = get_setting(session, "plex")
            if setting and setting.extra_config:
                interval_hours = setting.extra_config.get("sync_interval_hours", 24)

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

        # Phase 5 (D-08, EVT-03): register the Plex polling job alongside library_sync.
        # 5-minute default interval; bounded queries inside run_poll guard against load.
        schedule_polling(interval_minutes=5)
    except Exception:
        # Schedule with default even if settings load fails
        schedule_sync(interval_hours)
        # Polling is additive — register it even if settings load failed.
        schedule_polling(interval_minutes=5)
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
