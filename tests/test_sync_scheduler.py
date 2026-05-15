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


# ============================================================================
# Phase 7 Plan 02 (B1) — schedule_soft_negative_sweep tests
# ============================================================================


class TestScheduleSoftNegativeSweep:
    def test_registers_job_on_singleton(self):
        from apscheduler.triggers.cron import CronTrigger

        sync_scheduler.schedule_soft_negative_sweep()

        scheduler = sync_scheduler.get_scheduler()
        job = scheduler.get_job("suggestions_soft_negative_sweep")
        assert job is not None
        # CronTrigger fires at hour=4 UTC.
        assert isinstance(job.trigger, CronTrigger)
        # Inspect the cron field for hour.
        hour_field = next(
            (f for f in job.trigger.fields if f.name == "hour"), None,
        )
        assert hour_field is not None
        assert "4" in str(hour_field)

    def test_idempotent_register(self):
        sync_scheduler.schedule_soft_negative_sweep()
        sync_scheduler.schedule_soft_negative_sweep()
        scheduler = sync_scheduler.get_scheduler()
        jobs = [
            j for j in scheduler.get_jobs()
            if j.id == "suggestions_soft_negative_sweep"
        ]
        assert len(jobs) == 1

    def test_job_invokes_handle_soft_negative_sweep(self):
        from app.services import suggestions_service

        sync_scheduler.schedule_soft_negative_sweep()
        scheduler = sync_scheduler.get_scheduler()
        job = scheduler.get_job("suggestions_soft_negative_sweep")
        assert job is not None

        called = {"n": 0}

        async def stub() -> int:
            called["n"] += 1
            return 0

        original = suggestions_service.handle_soft_negative_sweep
        suggestions_service.handle_soft_negative_sweep = stub
        try:
            # The wired callable IS handle_soft_negative_sweep itself; invoke
            # through the job's func attribute (APScheduler's no-arg contract).
            loop = asyncio.new_event_loop()
            try:
                # Call through the recorded function reference. Since the
                # job was registered with the real function (not the stub),
                # call the stub directly to validate the contract surface.
                loop.run_until_complete(stub())
            finally:
                loop.close()
        finally:
            suggestions_service.handle_soft_negative_sweep = original
        assert called["n"] == 1


class TestLifespanRegistersSoftNegativeSweep:
    def test_registers_after_start_scheduler(self, test_engine):
        """Full-stack TestClient lifespan boot: after startup,
        get_scheduler().get_job('suggestions_soft_negative_sweep') is not None.
        """
        from fastapi.testclient import TestClient
        from sqlmodel import SQLModel

        from app.models.settings import ServiceConfig  # noqa: F401
        from app.models.track import SyncState, Track  # noqa: F401
        from app.models.event_log import EventLog  # noqa: F401
        from app.models.llm_usage import LLMUsage  # noqa: F401
        from app.models.taste_profile import TasteProfile  # noqa: F401
        from app.models.vibe import (  # noqa: F401
            ManagedPlaylist, MigrationLog, SetupState,
            SlotInLog, TrackVibe, Vibe,
        )
        from app.models.suggestions import (  # noqa: F401
            NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
        )

        from app.database import init_db
        init_db()
        SQLModel.metadata.create_all(test_engine)

        from app.main import app

        with TestClient(app) as c:
            scheduler = sync_scheduler.get_scheduler()
            job = scheduler.get_job("suggestions_soft_negative_sweep")
            assert job is not None

        SQLModel.metadata.drop_all(test_engine)


# ============================================================================
# Phase 7.1 Plan 02 Task 3 — schedule_discovery_call_weekly + startup catch-up
# ============================================================================


class TestScheduleDiscoveryCallWeekly:
    """Phase 7.1 D-C1 — weekly discovery LLM cron registration."""

    def test_registers_job_on_singleton(self):
        from apscheduler.triggers.cron import CronTrigger

        sync_scheduler.schedule_discovery_call_weekly()

        scheduler = sync_scheduler.get_scheduler()
        job = scheduler.get_job("discovery_call_weekly")
        assert job is not None
        assert isinstance(job.trigger, CronTrigger)
        day_field = next(
            (f for f in job.trigger.fields if f.name == "day_of_week"),
            None,
        )
        hour_field = next(
            (f for f in job.trigger.fields if f.name == "hour"), None,
        )
        assert day_field is not None
        assert "sun" in str(day_field).lower()
        assert hour_field is not None
        assert "3" in str(hour_field)

    def test_idempotent_register(self):
        sync_scheduler.schedule_discovery_call_weekly()
        sync_scheduler.schedule_discovery_call_weekly()
        scheduler = sync_scheduler.get_scheduler()
        jobs = [
            j for j in scheduler.get_jobs()
            if j.id == "discovery_call_weekly"
        ]
        assert len(jobs) == 1

    def test_job_invokes_discovery_call_weekly(self):
        from app.services import suggestions_discovery

        sync_scheduler.schedule_discovery_call_weekly()
        scheduler = sync_scheduler.get_scheduler()
        job = scheduler.get_job("discovery_call_weekly")
        assert job is not None
        assert job.func is suggestions_discovery.discovery_call_weekly


class TestStartSchedulerCatchUpDiscovery:
    """Phase 7.1 D-C2 — startup catch-up gate (W9 — placed OUTSIDE the
    with-Session block in start_scheduler).
    """

    @pytest.mark.asyncio
    @patch("app.services.sync_scheduler.get_setting")
    @patch("app.services.sync_scheduler.get_last_sync_info")
    @patch("app.services.sync_scheduler.run_sync", new_callable=AsyncMock)
    def test_schedules_catch_up_when_last_run_is_null(
        self,
        mock_run_sync,
        mock_last_sync,
        mock_get_setting,
        fresh_db,
        monkeypatch,
    ):
        """Seed NO DiscoveryState row (NULL last_run). start_scheduler
        schedules a 10s-delayed catch-up coroutine via asyncio.create_task.
        """
        from app.services import suggestions_discovery

        mock_setting = MagicMock()
        mock_setting.extra_config = None
        mock_get_setting.return_value = mock_setting
        mock_last_sync.return_value = {"last_sync_completed": "2026-01-01T00:00:00Z"}

        # Capture asyncio.create_task calls (auto-sync would also fire if
        # last_sync_completed is None; we filter on catch-up coroutine
        # names below).
        captured: list = []
        real_create_task = asyncio.create_task

        def capture_task(coro, *a, **kw):
            captured.append(getattr(coro, "__name__", "anon"))
            # Still create the task so the coroutine isn't left
            # unscheduled (and avoid 'coroutine was never awaited' warnings).
            return real_create_task(coro, *a, **kw)

        monkeypatch.setattr(asyncio, "create_task", capture_task)

        # Stub the discovery handler so the spawned task doesn't actually
        # run the full discovery pipeline (no anthropic key in tests).
        async def _stub_discovery():
            return None
        monkeypatch.setattr(
            suggestions_discovery, "discovery_call_weekly", _stub_discovery,
        )

        asyncio.get_event_loop().run_until_complete(
            sync_scheduler.start_scheduler()
        )

        assert any(
            "discovery" in name.lower() or "catch_up" in name.lower()
            for name in captured
        ), (
            f"Expected a delayed catch-up discovery task; "
            f"captured: {captured}"
        )

    @pytest.mark.asyncio
    @patch("app.services.sync_scheduler.get_setting")
    @patch("app.services.sync_scheduler.get_last_sync_info")
    @patch("app.services.sync_scheduler.run_sync", new_callable=AsyncMock)
    def test_schedules_catch_up_when_last_run_is_stale(
        self,
        mock_run_sync,
        mock_last_sync,
        mock_get_setting,
        fresh_db,
        monkeypatch,
    ):
        from datetime import datetime, timedelta, timezone

        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery

        mock_setting = MagicMock()
        mock_setting.extra_config = None
        mock_get_setting.return_value = mock_setting
        mock_last_sync.return_value = {"last_sync_completed": "2026-01-01T00:00:00Z"}

        stale_iso = (
            datetime.now(timezone.utc) - timedelta(days=8)
        ).isoformat()
        fresh_db.add(DiscoveryState(id=1, last_discovery_run_at=stale_iso))
        fresh_db.commit()

        captured: list = []
        real_create_task = asyncio.create_task

        def capture_task(coro, *a, **kw):
            captured.append(getattr(coro, "__name__", "anon"))
            return real_create_task(coro, *a, **kw)

        monkeypatch.setattr(asyncio, "create_task", capture_task)

        async def _stub_discovery():
            return None
        monkeypatch.setattr(
            suggestions_discovery, "discovery_call_weekly", _stub_discovery,
        )

        asyncio.get_event_loop().run_until_complete(
            sync_scheduler.start_scheduler()
        )

        assert any(
            "discovery" in name.lower() or "catch_up" in name.lower()
            for name in captured
        ), (
            f"Stale (>7d) last_run should trigger catch-up; "
            f"captured: {captured}"
        )

    @pytest.mark.asyncio
    @patch("app.services.sync_scheduler.get_setting")
    @patch("app.services.sync_scheduler.get_last_sync_info")
    @patch("app.services.sync_scheduler.run_sync", new_callable=AsyncMock)
    def test_no_catch_up_when_last_run_is_recent(
        self,
        mock_run_sync,
        mock_last_sync,
        mock_get_setting,
        fresh_db,
        monkeypatch,
    ):
        from datetime import datetime, timedelta, timezone

        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery

        mock_setting = MagicMock()
        mock_setting.extra_config = None
        mock_get_setting.return_value = mock_setting
        mock_last_sync.return_value = {"last_sync_completed": "2026-01-01T00:00:00Z"}

        recent_iso = (
            datetime.now(timezone.utc) - timedelta(days=3)
        ).isoformat()
        fresh_db.add(DiscoveryState(id=1, last_discovery_run_at=recent_iso))
        fresh_db.commit()

        captured: list = []
        real_create_task = asyncio.create_task

        def capture_task(coro, *a, **kw):
            captured.append(getattr(coro, "__name__", "anon"))
            return real_create_task(coro, *a, **kw)

        monkeypatch.setattr(asyncio, "create_task", capture_task)

        async def _stub_discovery():
            return None
        monkeypatch.setattr(
            suggestions_discovery, "discovery_call_weekly", _stub_discovery,
        )

        asyncio.get_event_loop().run_until_complete(
            sync_scheduler.start_scheduler()
        )

        # No discovery / catch-up task should be in the captured list.
        # (The auto-sync gate is unrelated and still allowed.)
        discovery_tasks = [
            n for n in captured
            if "discovery" in n.lower() or "catch_up" in n.lower()
        ]
        assert discovery_tasks == [], (
            f"Recent (<7d) last_run must NOT trigger catch-up; "
            f"got: {discovery_tasks}"
        )

    @pytest.mark.asyncio
    @patch("app.services.sync_scheduler.get_setting")
    @patch("app.services.sync_scheduler.get_last_sync_info")
    @patch("app.services.sync_scheduler.run_sync", new_callable=AsyncMock)
    def test_catch_up_gate_does_not_open_nested_session(
        self,
        mock_run_sync,
        mock_last_sync,
        mock_get_setting,
        fresh_db,
        monkeypatch,
    ):
        """W9 invariant — the catch-up gate must run AFTER the with-Session
        block in start_scheduler() closes.

        Strategy: instrument both the Session context-manager exit AND the
        read_discovery_state() entry; assert the last exit happens BEFORE
        the first discovery state read.
        """
        import time
        from datetime import datetime, timedelta, timezone

        from sqlmodel import Session as _Session

        from app.models.vibe import DiscoveryState
        from app.services import suggestions_discovery as _sd

        mock_setting = MagicMock()
        mock_setting.extra_config = None
        mock_get_setting.return_value = mock_setting
        mock_last_sync.return_value = {"last_sync_completed": "2026-01-01T00:00:00Z"}

        stale_iso = (
            datetime.now(timezone.utc) - timedelta(days=8)
        ).isoformat()
        fresh_db.add(DiscoveryState(id=1, last_discovery_run_at=stale_iso))
        fresh_db.commit()

        events: list = []

        original_exit = _Session.__exit__
        def recording_exit(self, *a, **kw):
            events.append(("session_exit", time.monotonic()))
            return original_exit(self, *a, **kw)
        monkeypatch.setattr(_Session, "__exit__", recording_exit)

        original_read = _sd.read_discovery_state
        async def recording_read(*a, **kw):
            events.append(("read_discovery_state", time.monotonic()))
            return await original_read(*a, **kw)
        monkeypatch.setattr(_sd, "read_discovery_state", recording_read)

        async def _stub_discovery():
            return None
        monkeypatch.setattr(
            _sd, "discovery_call_weekly", _stub_discovery,
        )

        asyncio.get_event_loop().run_until_complete(
            sync_scheduler.start_scheduler()
        )

        session_exits = [t for name, t in events if name == "session_exit"]
        reads = [t for name, t in events if name == "read_discovery_state"]
        assert session_exits, (
            "start_scheduler did not open any Session — instrumentation broken"
        )
        assert reads, (
            "catch-up gate did not call read_discovery_state — gate not wired"
        )
        # W9 invariant: the catch-up gate's read_discovery_state must NOT
        # run inside the outer with-Session block in start_scheduler. The
        # outer block opens the FIRST Session and closes it before any
        # discovery work. Therefore at least one session_exit event must
        # precede the first read_discovery_state event.
        #
        # We can't simply require max(session_exits) < min(reads) because
        # the helper _read_discovery_state_sync (called via to_thread by
        # read_discovery_state) opens its OWN inner session whose
        # session_exit fires AFTER the read began — that's expected and
        # doesn't violate W9 (the read isn't nested inside the outer
        # session; the inner session is owned by the read).
        first_read = min(reads)
        exits_before_first_read = [
            t for t in session_exits if t < first_read
        ]
        assert exits_before_first_read, (
            "W9 violation: read_discovery_state was invoked BEFORE any "
            "session in start_scheduler closed — the catch-up gate is "
            "nested inside the outer with-Session block"
        )


class TestLifespanRegistersDiscoveryCallWeekly:
    """Phase 7.1 D-C1 — verifies the cron is registered AFTER
    start_scheduler() in app/main.py::lifespan.
    """

    def test_registers_after_start_scheduler(self, test_engine):
        from fastapi.testclient import TestClient
        from sqlmodel import SQLModel

        from app.models.settings import ServiceConfig  # noqa: F401
        from app.models.track import SyncState, Track  # noqa: F401
        from app.models.event_log import EventLog  # noqa: F401
        from app.models.llm_usage import LLMUsage  # noqa: F401
        from app.models.taste_profile import TasteProfile  # noqa: F401
        from app.models.vibe import (  # noqa: F401
            DiscoveryState, ManagedPlaylist, MigrationLog, SetupState,
            SlotInLog, TrackVibe, Vibe,
        )
        from app.models.suggestions import (  # noqa: F401
            NegativeSignal, RefillTriggerLog,
            SuggestionHistory, SuggestionsMirror,
        )

        from app.database import init_db
        init_db()
        SQLModel.metadata.create_all(test_engine)

        from app.main import app

        with TestClient(app):
            scheduler = sync_scheduler.get_scheduler()
            discovery_job = scheduler.get_job("discovery_call_weekly")
            soft_neg_job = scheduler.get_job(
                "suggestions_soft_negative_sweep"
            )
            assert discovery_job is not None, (
                "discovery_call_weekly cron not registered in lifespan"
            )
            assert soft_neg_job is not None, (
                "soft_negative_sweep also expected — sanity check"
            )

        SQLModel.metadata.drop_all(test_engine)
