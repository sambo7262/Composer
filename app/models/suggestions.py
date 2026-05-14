"""Phase 7 (Plan 01) — Suggestions queue mirror.

SuggestionsMirror is the source of truth for queue contents per SUGG-02.
The Plex 'Composer · Suggestions' playlist is the eventual-consistent
mirror; on read divergence, SuggestionsMirror wins. This dodges LLM-
latency races on consumption (PROJECT.md key decision: SQLite is read-
truth, Plex is write-target).
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
        ranking call (Plan 02 fills).
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
