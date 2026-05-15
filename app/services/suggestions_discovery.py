"""Phase 7.1 — Weekly LLM discovery layer + plays-since-last-discovery counter.

Companion to ``app/services/suggestions_service.py`` (which owns the SQL
hot-path refill — see ``refill_mirror_sql``). This module owns:

- The plays-since-last-discovery counter persistence (DiscoveryState
  single-row id=1 table). Incremented on every ``handle_track_played``
  best-effort hook (Plan 01); reset to 0 on a successful
  ``discovery_call_weekly`` run (Plan 02). Read by
  ``compute_adaptive_pick_count`` to decide how many discovery picks the
  next weekly call should fetch (3-7 per D-A3).

- Plan 02 will add: ``compute_discovery_eligible`` (D-A1 candidate pool
  filter — owned tracks unplayed in 90+ days), ``discovery_call_weekly``
  (the LLM cron handler), ``WEEKLY_DISCOVERY_PICKS_RANGE`` constant,
  ``DISCOVERY_MAX_TOKENS_FLOOR`` constant + retry guard catching
  ``MaxTokensTruncationError`` (added in Plan 01 to anthropic_client).

Phase 5 D-08 module-singleton + state pattern + Phase 5 D-09 sync-helper
invariant both apply. The AST test
``test_no_session_outside_sync_helper_in_suggestions_discovery`` enforces
D-09.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.database import get_engine
from app.models.vibe import DiscoveryState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 5 D-08 — module-singleton state pattern.
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryServiceStatus:
    """Last-known state of the weekly discovery service.

    Updated by ``increment_plays_since_last_discovery`` (counter bumps),
    ``reset_plays_since_last_discovery`` (Plan 02, after a successful
    weekly run), and ``discovery_call_weekly`` itself (Plan 02 — sets
    ``state`` to ``"running"`` / ``"idle"`` / ``"cost_locked"`` /
    ``"error"``).
    """

    state: str = "idle"  # "idle" | "running" | "cost_locked" | "error"
    last_run_at: Optional[str] = None  # mirrored from DiscoveryState.last_discovery_run_at
    last_error: Optional[str] = None
    last_picks_made: int = 0
    last_increment_at: Optional[str] = None


_status: DiscoveryServiceStatus = DiscoveryServiceStatus()


def get_state() -> DiscoveryServiceStatus:
    """Return the module-singleton DiscoveryServiceStatus. Mirrors
    ``suggestions_service.get_state()`` and ``vibe_service.get_state()``
    (Phase 5 D-08).
    """
    return _status


# ---------------------------------------------------------------------------
# D-A3 / D-C2 — counter persistence (DiscoveryState single-row id=1 table).
# ---------------------------------------------------------------------------


def _read_discovery_state_sync() -> DiscoveryState:
    """Read or initialize the singleton DiscoveryState row.

    Returns a session-detached DiscoveryState (expunged) so callers can
    read ``plays_since_last_discovery`` / ``last_discovery_run_at``
    without holding a Session open.
    """
    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        if row is None:
            row = DiscoveryState(id=1)
            session.add(row)
            session.commit()
            session.refresh(row)
        session.expunge(row)
        return row


def _increment_plays_since_discovery_sync() -> int:
    """Increment ``plays_since_last_discovery`` by 1 on the id=1 row;
    create the row with value=1 if absent. Returns the post-increment
    count.
    """
    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        if row is None:
            row = DiscoveryState(id=1, plays_since_last_discovery=1)
        else:
            row.plays_since_last_discovery = (
                (row.plays_since_last_discovery or 0) + 1
            )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.plays_since_last_discovery)


def _reset_plays_since_discovery_sync(run_at_iso: str) -> None:
    """Reset ``plays_since_last_discovery`` to 0 and stamp
    ``last_discovery_run_at = run_at_iso``. Called by Plan 02's
    ``discovery_call_weekly`` on successful completion.
    """
    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        if row is None:
            row = DiscoveryState(
                id=1,
                plays_since_last_discovery=0,
                last_discovery_run_at=run_at_iso,
            )
        else:
            row.plays_since_last_discovery = 0
            row.last_discovery_run_at = run_at_iso
        session.add(row)
        session.commit()


async def increment_plays_since_last_discovery() -> int:
    """Best-effort hook called by ``event_handlers.handle_track_played``.

    Returns the post-increment count for observability/testing. Wrapped
    in ``asyncio.to_thread`` per Phase 5 D-09 (no Session in async).
    """
    global _status
    new_count = await asyncio.to_thread(
        _increment_plays_since_discovery_sync
    )
    _status = DiscoveryServiceStatus(
        state=_status.state,
        last_run_at=_status.last_run_at,
        last_error=_status.last_error,
        last_picks_made=_status.last_picks_made,
        last_increment_at=datetime.now(timezone.utc).isoformat(),
    )
    return new_count


async def read_discovery_state() -> DiscoveryState:
    """Async accessor for the singleton DiscoveryState row. Plan 02's
    startup catch-up gate reads ``last_discovery_run_at`` via this
    accessor.
    """
    return await asyncio.to_thread(_read_discovery_state_sync)


# ---------------------------------------------------------------------------
# D-A3 — adaptive pick count (pure function, no DB access).
# ---------------------------------------------------------------------------


def compute_adaptive_pick_count(plays_since_last_discovery: int) -> int:
    """D-A3 — heavy listening week -> more discovery (up to 7); light
    week -> fewer (down to 3). Pure function. Plan 02 calls this with the
    DiscoveryState counter to size the weekly LLM call.

    Mapping (CONTEXT.md draft, tunable post-deploy):
      0-10 plays  -> 3 picks
      11-25 plays -> 5 picks
      26+ plays   -> 7 picks
    """
    if plays_since_last_discovery <= 10:
        return 3
    if plays_since_last_discovery <= 25:
        return 5
    return 7


# ---------------------------------------------------------------------------
# Plan 02 placeholders — implemented in 07.1-02-PLAN.md.
# ---------------------------------------------------------------------------
#
# def compute_discovery_eligible() -> List[dict]:
#     """D-A1 — owned tracks unplayed in 90+ days."""
#
# async def discovery_call_weekly() -> None:
#     """D-C1 — APScheduler-fired Sunday 03:00 UTC LLM discovery call."""
