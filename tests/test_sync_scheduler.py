from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import sync_scheduler


@pytest.fixture(autouse=True)
def reset_scheduler_singleton():
    """Reset the module-level scheduler singleton between tests."""
    sync_scheduler._scheduler = None
    yield
    # Shutdown scheduler if it was started during the test
    if sync_scheduler._scheduler is not None:
        try:
            if sync_scheduler._scheduler.running:
                sync_scheduler._scheduler.shutdown(wait=False)
        except Exception:
            pass
    sync_scheduler._scheduler = None


class TestGetScheduler:
    def test_returns_asyncio_scheduler(self):
        scheduler = sync_scheduler.get_scheduler()
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        assert isinstance(scheduler, AsyncIOScheduler)

    def test_returns_singleton(self):
        s1 = sync_scheduler.get_scheduler()
        s2 = sync_scheduler.get_scheduler()
        assert s1 is s2


class TestScheduleSync:
    def test_adds_job_with_correct_interval(self):
        scheduler = sync_scheduler.get_scheduler()
        sync_scheduler.schedule_sync(12)
        job = scheduler.get_job("library_sync")
        assert job is not None
        assert "12h" in job.name

    def test_replaces_existing_job(self):
        scheduler = sync_scheduler.get_scheduler()
        sync_scheduler.schedule_sync(6)
        sync_scheduler.schedule_sync(24)
        job = scheduler.get_job("library_sync")
        assert job is not None
        assert "24h" in job.name
        # Only one job with that ID should exist
        jobs = scheduler.get_jobs()
        library_jobs = [j for j in jobs if j.id == "library_sync"]
        assert len(library_jobs) == 1


class TestStartScheduler:
    @pytest.mark.asyncio
    @patch("app.services.sync_scheduler.get_setting")
    @patch("app.services.sync_scheduler.get_last_sync_info")
    @patch("app.services.sync_scheduler.run_sync", new_callable=AsyncMock)
    def test_starts_and_schedules_with_configured_interval(
        self, mock_run_sync, mock_last_sync, mock_get_setting
    ):
        mock_setting = MagicMock()
        mock_setting.extra_config = {"sync_interval_hours": 12}
        mock_get_setting.return_value = mock_setting
        mock_last_sync.return_value = {"last_sync_completed": "2026-01-01T00:00:00Z"}

        asyncio.get_event_loop().run_until_complete(sync_scheduler.start_scheduler())

        scheduler = sync_scheduler.get_scheduler()
        assert scheduler.running
        job = scheduler.get_job("library_sync")
        assert job is not None
        assert "12h" in job.name
        # Should NOT auto-sync since last_sync_completed is set
        mock_run_sync.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.sync_scheduler.get_setting")
    @patch("app.services.sync_scheduler.get_last_sync_info")
    @patch("app.services.sync_scheduler.run_sync", new_callable=AsyncMock)
    @patch("app.services.sync_scheduler.is_service_configured")
    def test_triggers_auto_sync_when_no_prior_sync(
        self, mock_is_configured, mock_run_sync, mock_last_sync, mock_get_setting
    ):
        mock_setting = MagicMock()
        mock_setting.extra_config = None
        mock_get_setting.return_value = mock_setting
        mock_last_sync.return_value = {"last_sync_completed": None}
        mock_is_configured.return_value = True

        asyncio.get_event_loop().run_until_complete(sync_scheduler.start_scheduler())

        # Should trigger auto-sync since Plex is configured and no prior sync
        mock_run_sync.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.sync_scheduler.get_setting")
    @patch("app.services.sync_scheduler.get_last_sync_info")
    @patch("app.services.sync_scheduler.run_sync", new_callable=AsyncMock)
    def test_defaults_to_24h_when_no_interval_configured(
        self, mock_run_sync, mock_last_sync, mock_get_setting
    ):
        mock_setting = MagicMock()
        mock_setting.extra_config = None
        mock_get_setting.return_value = mock_setting
        mock_last_sync.return_value = {"last_sync_completed": "2026-01-01T00:00:00Z"}

        asyncio.get_event_loop().run_until_complete(sync_scheduler.start_scheduler())

        job = sync_scheduler.get_scheduler().get_job("library_sync")
        assert job is not None
        assert "24h" in job.name


class TestStopScheduler:
    @pytest.mark.asyncio
    def test_shuts_down_without_error(self):
        scheduler = sync_scheduler.get_scheduler()
        scheduler.start()
        assert scheduler.running

        asyncio.get_event_loop().run_until_complete(sync_scheduler.stop_scheduler())

        assert not scheduler.running

    @pytest.mark.asyncio
    def test_handles_no_scheduler(self):
        # Should not raise even if scheduler was never started
        sync_scheduler._scheduler = None
        asyncio.get_event_loop().run_until_complete(sync_scheduler.stop_scheduler())


class TestUpdateSyncSchedule:
    def test_updates_running_scheduler(self):
        scheduler = sync_scheduler.get_scheduler()
        scheduler.start()
        sync_scheduler.schedule_sync(6)

        sync_scheduler.update_sync_schedule(12)

        job = scheduler.get_job("library_sync")
        assert job is not None
        assert "12h" in job.name

    def test_noop_when_scheduler_not_running(self):
        # Should not raise when scheduler exists but is not running
        sync_scheduler.get_scheduler()
        sync_scheduler.update_sync_schedule(12)
        # No job should exist since scheduler not running
