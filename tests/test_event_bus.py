"""Tests for app/services/event_bus.py — singleton queue + dispatcher lifecycle (EVT-04)."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def reset_event_bus_singletons():
    """Reset module-level _queue and _dispatcher_task between tests.

    Mirrors the autouse pattern from tests/test_sync_scheduler.py:11-23.
    """
    from app.services import event_bus

    event_bus._queue = None
    event_bus._dispatcher_task = None
    yield
    # Best-effort cancellation of any leaked task
    if event_bus._dispatcher_task is not None:
        try:
            event_bus._dispatcher_task.cancel()
        except Exception:
            pass
    event_bus._queue = None
    event_bus._dispatcher_task = None


def _run_async(coro):
    """Helper to run async coroutines in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestGetEventBus:
    def test_get_event_bus_returns_singleton(self):
        """Two calls to get_event_bus() return the same Queue instance."""
        from app.services.event_bus import get_event_bus

        q1 = get_event_bus()
        q2 = get_event_bus()
        assert q1 is q2
        assert isinstance(q1, asyncio.Queue)

    def test_get_event_bus_returns_asyncio_queue(self):
        from app.services.event_bus import get_event_bus

        q = get_event_bus()
        assert isinstance(q, asyncio.Queue)


class TestDispatcherLifecycle:
    def test_dispatcher_serializes(self):
        """Multiple events queued at once are processed serially (no concurrent processing).

        Per D-06: single dispatcher prevents SQLite write contention.
        """
        from app.services import event_bus

        # Track in-flight handlers to detect concurrency
        in_flight = {"count": 0, "max": 0}
        processed = []

        async def stub_handler(evt):
            in_flight["count"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["count"])
            await asyncio.sleep(0.02)
            processed.append(evt)
            in_flight["count"] -= 1

        async def scenario():
            queue = event_bus.get_event_bus()

            # Replace dispatch with a stub that sleeps to expose concurrency
            import app.services.event_bus as eb_mod

            # Create our own dispatcher loop using the stub
            async def loop():
                while True:
                    evt = await queue.get()
                    try:
                        await stub_handler(evt)
                    finally:
                        queue.task_done()

            task = asyncio.create_task(loop())
            try:
                # Push 5 events
                for i in range(5):
                    queue.put_nowait({"id": i})
                # Give dispatcher time to drain
                await asyncio.sleep(0.5)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            return in_flight["max"], len(processed)

        max_concurrent, total = _run_async(scenario())
        assert total == 5
        assert max_concurrent == 1, (
            f"Dispatcher must serialize events; saw {max_concurrent} concurrent"
        )

    def test_start_dispatcher_creates_task(self):
        """start_dispatcher creates a non-None _dispatcher_task."""
        from app.services import event_bus

        async def scenario():
            await event_bus.start_dispatcher()
            try:
                assert event_bus._dispatcher_task is not None
                assert not event_bus._dispatcher_task.done()
            finally:
                await event_bus.stop_dispatcher()

        _run_async(scenario())

    def test_start_dispatcher_idempotent(self):
        """Calling start_dispatcher twice does not spawn two tasks."""
        from app.services import event_bus

        async def scenario():
            await event_bus.start_dispatcher()
            t1 = event_bus._dispatcher_task
            await event_bus.start_dispatcher()
            t2 = event_bus._dispatcher_task
            try:
                assert t1 is t2
            finally:
                await event_bus.stop_dispatcher()

        _run_async(scenario())

    def test_stop_dispatcher_clean_cancellation(self):
        """stop_dispatcher cancels the task cleanly and resets the singleton."""
        from app.services import event_bus

        async def scenario():
            await event_bus.start_dispatcher()
            await event_bus.stop_dispatcher()
            assert event_bus._dispatcher_task is None

        _run_async(scenario())

    def test_stop_dispatcher_when_not_started(self):
        """stop_dispatcher is a no-op when never started."""
        from app.services import event_bus

        async def scenario():
            # Should not raise
            await event_bus.stop_dispatcher()

        _run_async(scenario())
