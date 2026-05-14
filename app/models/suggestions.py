"""Phase 7 — Suggestions queue data model.

Plan 01 introduced :class:`SuggestionsMirror` (SUGG-02 source of truth for the
queue contents).

Plan 02 adds:

- :class:`SuggestionHistory` — SUGG-07 14-day exclusion window
- :class:`NegativeSignal`    — D-11/D-12/D-13 skip-tracking signals
- :class:`RefillTriggerLog`  — /debug/suggestions feed (Plan 03)

The Plex "Composer · Suggestions" playlist is the eventual-consistent mirror;
on read divergence, SuggestionsMirror wins. This dodges LLM-latency races on
consumption (PROJECT.md key decision: SQLite is read-truth, Plex is
write-target).
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class SuggestionsMirror(SQLModel, table=True):
    """One row per queue slot. UNIQUE(track_id) so a track cannot appear in
    the queue twice. Position is 0-based; lower = top of queue.

    Fields:
      - track_id: FK to Track.id (UNIQUE — a track is in the queue at most once).
      - position: 0-based slot index, lower = top.
      - added_at: ISO 8601 UTC string (when the slot was filled).
      - rationale: optional one-liner "Why this track?" from the LLM
        ranking call. Plan 02 fills this with a non-empty string for every
        ranked pick.
      - vibe_id: optional FK to Vibe.id — which vibe this suggestion
        landed under (Plan 02 fills).
      - score: distance-to-centroid at slot-in time (Plan 02 fills).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    track_id: int = Field(foreign_key="track.id", unique=True, index=True)
    position: int  # 0-based; lower = top
    added_at: str
    rationale: Optional[str] = None
    vibe_id: Optional[int] = Field(
        default=None, foreign_key="vibe.id", index=True
    )
    score: Optional[float] = None


class SuggestionHistory(SQLModel, table=True):
    """SUGG-07 14-day exclusion window. Append-only. Refill shortlist filters
    out track_ids with a ``surfaced_at >= NOW() - 14 days``.

    ``refill_id`` is a soft FK to :class:`RefillTriggerLog.id` — joining is
    a debug-time concern, not query-hot.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    track_id: int = Field(foreign_key="track.id", index=True)
    surfaced_at: str = Field(index=True)
    refill_id: int = Field(index=True)


class NegativeSignal(SQLModel, table=True):
    """D-11 / D-12 / D-13 skip-tracking signal.

    ``signal_type``:
      - ``"soft"`` — track played from Suggestions but not rated 14 days later.
        Injected into the LLM ranking user-prompt addendum ("DO NOT
        PRIORITIZE"). No automatic recovery — the 14-day SuggestionHistory
        window self-expires for whether a row gets a soft signal.
      - ``"hard_track"`` — user explicitly dismissed this track. Forever
        exclusion (SUGG-09).
      - ``"hard_artist"`` — companion to ``"hard_track"``: deboost the entire
        artist with ``recovery_pending=True``. Cleared when the user rates 3+
        stars on a track by that artist (D-13).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    track_id: Optional[int] = Field(
        default=None, foreign_key="track.id", index=True
    )
    artist: Optional[str] = Field(default=None, index=True)
    signal_type: str = Field(index=True)
    created_at: str
    recovery_pending: bool = Field(default=False)


class RefillTriggerLog(SQLModel, table=True):
    """One row per ``refill_suggestions_queue`` call (success or
    breaker-tripped). Feeds /debug/suggestions in Plan 03.

    ``event_source``:
      - ``"track_played"``      — whole-queue refill via maybe_schedule_refill
      - ``"vibe_coverage_cta"`` — single-vibe refill via SUGG-10 endpoint
      - ``"bootstrap"``         — first-deploy migration bootstrap (D-02)
      - ``"manual"``            — /debug-driven manual trigger (Plan 03)
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    triggered_at: str = Field(index=True)
    event_source: str
    target_vibe_id: Optional[int] = Field(default=None)
    candidates_evaluated: int = 0
    picks_made: int = 0
    latency_ms: int = 0
    cost_estimate_usd: float = 0.0
    breaker_tripped: bool = Field(default=False)
    error: Optional[str] = None
