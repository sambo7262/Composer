"""Phase 6 (D-28) vibe-clustering data model.

Four SQLModel `table=True` classes that hold the entire Phase 6 + Phase 7+ vibe
state:

- :class:`Vibe`             — one row per persistent named vibe playlist.
- :class:`TrackVibe`        — composite-PK membership table (which tracks belong
                              to which vibe, with a distance + assignment source).
- :class:`ManagedPlaylist`  — Composer's DB-side ownership marker for Plex
                              playlists. The SECOND marker is the playlist title
                              prefix ``"Composer · "`` checked at every
                              read/write per OPS-06 / Pitfall 20. BOTH must hold
                              for the playlist to be considered managed; either
                              missing → leave the playlist alone (legacy /
                              user-renamed / archived).
- :class:`SetupState`       — single-row id=1 wizard progress + draft cluster
                              proposals (~50–100KB JSON; see D-08).

TrackVibe.assigned_by enum (D-19):

- ``"cluster"``   — initial wizard pass / re-cluster commit.
- ``"auto-slot"`` — RatingChanged event-driven slot-in (Phase 6 D-15).
- ``"manual"``    — user override (Phase 9+ UI; column reserved now).
                    MANUAL ROWS SURVIVE RE-CLUSTER (D-22 — snapshot/replay).

Schema migration policy (OPS-01): tables added via SQLModel.metadata.create_all
in app.database.init_db; new Track columns + indexes go through
_migrate_add_columns. Additive only; no Alembic.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class Vibe(SQLModel, table=True):
    """A persistent, user-named vibe (= one Composer-managed Plex playlist).

    Centroid + spread are stored at the Vibe level (not on Track) so re-cluster
    operates on the cluster set without touching individual track rows. Centroid
    is the z-score-normalized 4-D mean over assigned tracks; spread is the
    per-dimension std-dev. ``silhouette_score`` records the last clustering
    pass for diagnostic display on /debug/vibes.

    ``is_active=False`` marks a vibe whose Plex playlist was archived during
    re-cluster (D-21). The row is preserved for audit (manual overrides may
    still reference it via the snapshot/replay path in D-22).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    centroid_energy: Optional[float] = None
    centroid_tempo: Optional[float] = None
    centroid_danceability: Optional[float] = None
    centroid_valence: Optional[float] = None
    spread_energy: Optional[float] = None
    spread_tempo: Optional[float] = None
    spread_danceability: Optional[float] = None
    spread_valence: Optional[float] = None
    silhouette_score: Optional[float] = None  # last clustering pass
    created_at: str
    centroid_recomputed_at: Optional[str] = None
    is_active: bool = Field(default=True)  # D-28 — re-cluster archival audit


class TrackVibe(SQLModel, table=True):
    """Composite-PK membership table linking Track <-> Vibe.

    ``distance`` is the z-score-normalized 4-D Euclidean distance from the
    track to the vibe's centroid at slot-in time (used by /debug/vibes to
    explain assignments).

    ``assigned_by`` enum: ``"cluster"`` | ``"auto-slot"`` | ``"manual"``. Manual
    rows are sticky across re-cluster (D-19, D-22). Auto-slot rows can be
    overwritten by re-cluster.

    Indexed by ``vibe_id`` (ix_trackvibe_vibe_id, D-30) for fast "show me a
    vibe's members" queries on /debug/vibes and the future vibe detail page.
    """

    track_id: int = Field(foreign_key="track.id", primary_key=True)
    vibe_id: int = Field(foreign_key="vibe.id", primary_key=True)
    distance: float = Field(index=True)
    assigned_at: str
    assigned_by: str  # "cluster" | "auto-slot" | "manual"


class ManagedPlaylist(SQLModel, table=True):
    """Composer-side ownership marker for a Plex playlist (DB-side dual-marker).

    OPS-06 / D-27 / Pitfall 20: every read or write against a Plex playlist
    must verify BOTH markers — this row exists AND the Plex playlist title
    starts with ``"Composer · "``. Either marker missing → the playlist is
    legacy / user-renamed / archived; do not touch it.

    ``UNIQUE(plex_rating_key)`` enforces the DB side at the SQLite layer (a
    given Plex playlist can correspond to at most one ManagedPlaylist row).
    The title-prefix check is the second marker, enforced in
    ``app/services/plex_playlist_service.py::is_managed_playlist`` (Plans
    02-04).

    ``kind="vibe"`` is the only value Phase 6 writes; Phase 7 will add
    ``"suggestions"`` for the Composer · Suggestions playlist.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # "vibe" (Phase 6); "suggestions" added in Phase 7
    vibe_id: Optional[int] = Field(default=None, foreign_key="vibe.id", index=True)
    plex_rating_key: str = Field(unique=True, index=True)
    composer_name: str  # the "Composer · {name}" title — used by D-27 dual-marker check
    last_pushed_at: Optional[str] = None
    track_count: int = Field(default=0)


class SetupState(SQLModel, table=True):
    """Single-row (id=1) wizard progress cache.

    ``draft_proposals_json`` holds the full LLM proposal set + refinement-turn
    history (~50–100KB; D-08). Lives in SQLite once instead of being round-
    tripped through cookies on every request. ``last_llm_call_id`` is a soft
    FK (no SQLite-enforced constraint) to LLMUsage.id, used by the "Re-show
    last cluster proposal" diagnostic on /debug/vibes (D-36).

    Step values: ``"rating_source"`` | ``"webhook"`` | ``"proposing"`` |
    ``"confirming"`` | ``"done"`` (D-06).
    """

    id: Optional[int] = Field(default=1, primary_key=True)
    step: str = Field(default="rating_source")
    draft_proposals_json: str = ""  # full LLM proposal set + refinement-turn history
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    refinement_turn_count: int = 0   # D-04 / D-08
    last_llm_call_id: Optional[int] = None  # soft FK to LLMUsage.id (D-08, D-36)
