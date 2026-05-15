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
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_engine
from app.models.vibe import DiscoveryState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 7.1 D-A1 / D-A3 / D-C1 / SUGG-14 — module-level constants.
# ---------------------------------------------------------------------------

# D-A1 — discovery-eligible window. A track is "unplayed enough to
# rediscover" if it has never been played OR its last play is older than
# this many days. 90 days matches typical rediscovery cadence per
# 07.1-CONTEXT.md.
DISCOVERY_UNPLAYED_DAYS = 90

# D-A3 — adaptive pick count bounds. ``compute_adaptive_pick_count`` maps
# ``plays_since_last_discovery`` to an int in this closed range. Tunable
# post-deploy if heavy/light listening week heuristics need adjustment.
WEEKLY_DISCOVERY_PICKS_RANGE = (3, 7)

# SUGG-14 — defensive max_tokens floor for the weekly discovery LLM call.
# Sized generously so structured DiscoveryPicksResponse JSON for 7 picks +
# per-pick rationales never trips stop_reason=max_tokens at the floor. The
# retry guard inside ``discovery_call_weekly`` catches the typed
# ``MaxTokensTruncationError`` (added by Plan 01 STEP 4 to
# ``anthropic_client.py``) and doubles this budget on observed truncation.
# Plan 03 adds the AST regression test that forbids ``max_tokens=2000``
# literals from re-entering this module (lesson folded in from quick task
# ``260514-e6w``).
DISCOVERY_MAX_TOKENS_FLOOR = 8000

# D-A2 — anthropic purpose prefix for the discovery call. Distinct from
# the (now-deleted) ``suggestions_`` family so cost-breaker accounting can
# separate the two layers, and so the cost meter card can report
# "discovery this week" specifically.
DISCOVERY_PURPOSE = "discovery_weekly"


# ---------------------------------------------------------------------------
# SUGG-13 — Pydantic shapes for the weekly LLM discovery response.
# ---------------------------------------------------------------------------


class DiscoveryPick(BaseModel):
    """One LLM-chosen discovery track. ``track_id`` is validated by
    ``discovery_call_weekly`` against the candidate pool returned by
    ``compute_discovery_eligible`` BEFORE writing to the mirror —
    mirrors the v1-burned hallucinated-ID lesson (Pitfall 10).
    """

    track_id: int
    rationale: str  # one-line "Why this track?" — surfaced in /suggestions UI


class DiscoveryPicksResponse(BaseModel):
    """Top-level LLM response shape. ``picks`` length is bounded by
    ``compute_adaptive_pick_count`` (3-7 per D-A3); the LLM is instructed
    to return exactly that many picks via the user prompt.
    """

    picks: list[DiscoveryPick]


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
# D-A1 — discovery-eligible candidate pool.
# ---------------------------------------------------------------------------


def _read_discovery_eligible_sync(
    unplayed_days: int = DISCOVERY_UNPLAYED_DAYS,
) -> list[dict]:
    """D-A1 — owned tracks unplayed in ``unplayed_days`` (default 90)
    days, MINUS tracks currently in :class:`SuggestionsMirror`, MINUS hard
    negatives (track + artist with ``recovery_pending=True``), MINUS tracks
    surfaced within the 14-day SuggestionHistory window.

    Returns dicts with ``track_id``, ``plex_rating_key``, ``title``,
    ``artist``, ``last_viewed_at`` — enough for the LLM user prompt + post-
    call ID validation.

    ISO 8601 ordering note (W12): the ``t.last_viewed_at < :cutoff``
    parameterized comparison is correct ONLY because ISO 8601 timestamp
    strings sort chronologically as strings (lexicographic order matches
    calendar order for valid ISO 8601). The boundary is STRICT less-than,
    so a track at exactly the cutoff is NOT included (verified by the
    W12 boundary test ``test_track_at_exactly_90_day_threshold_is_excluded``).
    If the DB ever stores non-ISO-8601 timestamps, this comparison silently
    breaks — current invariants (``sync_service`` writes only via
    ``datetime.now(timezone.utc).isoformat()``) prevent that.
    """
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=unplayed_days)).isoformat()
    fourteen_days_ago = (now - timedelta(days=14)).isoformat()
    with Session(get_engine()) as session:
        rows = session.execute(
            text(
                """
                SELECT t.id AS track_id, t.plex_rating_key, t.title,
                       t.artist, t.last_viewed_at
                FROM track t
                WHERE (
                    t.last_viewed_at IS NULL
                    OR t.last_viewed_at < :cutoff
                )
                AND t.id NOT IN (
                    SELECT track_id FROM suggestionsmirror
                )
                AND t.id NOT IN (
                    SELECT track_id FROM negativesignal
                    WHERE signal_type = 'hard_track'
                      AND track_id IS NOT NULL
                )
                AND t.artist NOT IN (
                    SELECT artist FROM negativesignal
                    WHERE signal_type = 'hard_artist'
                      AND recovery_pending = 1
                      AND artist IS NOT NULL
                )
                AND t.id NOT IN (
                    SELECT track_id FROM suggestionhistory
                    WHERE surfaced_at >= :fourteen_days_ago
                )
                """
            ),
            {
                "cutoff": cutoff,
                "fourteen_days_ago": fourteen_days_ago,
            },
        ).all()
        return [
            {
                "track_id": int(r[0]),
                "plex_rating_key": r[1],
                "title": r[2],
                "artist": r[3],
                "last_viewed_at": r[4],
            }
            for r in rows
        ]


async def compute_discovery_eligible(
    unplayed_days: int = DISCOVERY_UNPLAYED_DAYS,
) -> list[dict]:
    """Async accessor for the D-A1 discovery candidate pool. Consumers
    (``discovery_call_weekly``) feed this directly into the LLM user prompt.
    """
    return await asyncio.to_thread(
        _read_discovery_eligible_sync, unplayed_days,
    )
