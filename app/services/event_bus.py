"""Event bus singleton + dispatcher (D-06, EVT-04).

Module-level state per Composer convention (mirrors sync_service / sync_scheduler):
- `_queue` — the asyncio.Queue events ride on
- `_dispatcher_task` — the single long-running consumer asyncio.Task

Started from `app/main.py` lifespan; consumed by `_dispatch_loop()` which routes
each event through `app.services.event_handlers.dispatch_event` (lazy-imported
to avoid circular dependency).

A single dispatcher consumes events serially — prevents SQLite write contention.
NOT a per-request FastAPI dependency.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level singletons — leading underscore convention
_queue: Optional[asyncio.Queue] = None
_dispatcher_task: Optional[asyncio.Task] = None


def get_event_bus() -> asyncio.Queue:
    """Return the global event bus. Created lazily on first call from lifespan."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def start_dispatcher() -> None:
    """Start the single long-running dispatcher task.

    Idempotent — calling repeatedly is a no-op while a task is running.
    """
    global _dispatcher_task
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return
    _dispatcher_task = asyncio.create_task(_dispatch_loop(), name="event_dispatcher")
    logger.info("Event dispatcher started")


async def stop_dispatcher() -> None:
    """Cancel and await the dispatcher task. Safe to call repeatedly.

    Also resets `_queue` to None — asyncio.Queue captures the event loop at
    creation time, so a stale queue from a closed loop will raise "Future
    attached to a different loop" when the next loop tries to use it. Resetting
    here ensures `get_event_bus()` constructs a fresh Queue bound to the next
    loop's lifecycle.
    """
    global _dispatcher_task, _queue
    if _dispatcher_task is None:
        # Still reset the queue in case it was created without a dispatcher
        # (e.g., a test that called get_event_bus() then closed the loop).
        _queue = None
        return
    _dispatcher_task.cancel()
    try:
        await _dispatcher_task
    except asyncio.CancelledError:
        pass
    _dispatcher_task = None
    _queue = None
    logger.info("Event dispatcher stopped")


async def _dispatch_loop() -> None:
    """Infinite consumer loop. One event at a time — handlers serialize naturally."""
    queue = get_event_bus()
    # Lazy import to avoid circular dep with event_handlers
    from app.services.event_handlers import dispatch_event

    while True:
        try:
            event = await queue.get()
            try:
                await dispatch_event(event)
            except Exception:
                logger.exception("Handler error for event %r", event)
            finally:
                queue.task_done()
        except asyncio.CancelledError:
            logger.info("Dispatcher cancelled, draining queue")
            raise
