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
    # Phase 8 D-E2 — hex like "#3b82f6"; auto-assigned at creation time from
    # the Tailwind 4 -500 palette (discovery_service.VIBE_COLOR_PALETTE).
    # NULL until run_phase_08_discovery_bootstrap backfills existing rows
    # on first deploy after Phase 8.
    color: Optional[str] = Field(default=None)
    # Phase 8 D-A2 — round-robin seed-track rotation. Plan 02 selector
    # picks the next-id starred track in this vibe after last_seed_track_id
    # so each Sunday gets a fresh seed; wraps to smallest id when exhausted.
    last_seed_track_id: Optional[int] = Field(
        default=None, foreign_key="track.id",
    )


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
    # Phase 6 Plan 04 (D-20) — distinguishes wizard re-run from initial.
    # Set True by POST /api/vibes/recluster/start; read by /api/setup/propose/init
    # + /api/setup/refine to switch the LLM purpose to vibe_clustering_recluster
    # (D-34) and by /setup/confirm to dispatch the commit to
    # /api/vibes/recluster/commit (diff-based reconciliation per D-21) instead of
    # /api/setup/finalize (initial bulk push). Cleared on commit.
    # ``sa_column_kwargs={"server_default": "0"}`` ensures the SQLite column
    # has a SQL DEFAULT 0, so legacy DBs using raw INSERT (without specifying
    # this column) get the correct value — needed for the Plan 04 migration
    # test that simulates an old INSERT path on a fresh schema.
    recluster_mode: bool = Field(
        default=False, sa_column_kwargs={"server_default": "0"}
    )


class SlotInLog(SQLModel, table=True):
    """D-36 / Phase 6 Plan 04: feed for /debug/vibes 'Last 20 slot-in decisions'.

    Lightweight log table; rows inserted best-effort by
    :func:`app.services.vibe_service.slot_track` and
    :func:`app.services.vibe_service.unslot_track` (lazy import + try/except so
    log failure never breaks the slot path).

    Field semantics:

    - ``timestamp``: ISO 8601 UTC string. Indexed (``ix_slotinlog_timestamp``)
      for the ``ORDER BY timestamp DESC LIMIT 20`` query that powers the
      diagnostic table.
    - ``track_id``: soft FK to ``Track.id`` (no SQLite FK constraint — matches
      the existing convention; the diagnostic page hydrates ``Track.title`` and
      ``Track.artist`` at render time).
    - ``vibe_ids``: JSON list (encoded string) of vibe ids assigned on this
      decision (1 or 2 — soft membership cap=2 per D-19).
    - ``distances``: JSON list (encoded string) of computed distances, parallel
      to ``vibe_ids`` (empty for action="unslot" — no distance for removal).
    - ``soft_membership_applied``: True if a 2nd vibe was added via the
      1-std-dev margin (Pitfall 24 / VIBE-02).
    - ``action``: ``"slot"`` | ``"unslot"`` | ``"manual_override_lost"``
      (D-22 — re-cluster commit logs lost manual overrides here).
    - ``note``: free text for diagnostic context (e.g. "track 42 had manual
      override on 'Late Night' but vibe was dropped without successor").

    Rotation policy: D-36 deferred ideas list says "first version logs every
    slot-in. If it grows unbounded, add a 30-day rotation. Revisit when the
    table size hits 100k rows." Conservative estimate ~50 rows/year per the
    Plan 04 threat model — no rotation needed at Phase 6 scale.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: str = Field(index=True)  # ISO 8601 UTC; ix_slotinlog_timestamp
    track_id: int  # soft FK to Track.id (no FK constraint — matches convention)
    vibe_ids: str = Field(default="[]")  # JSON list of vibe ids (1 or 2)
    distances: str = Field(default="[]")  # JSON list of distances, parallel
    soft_membership_applied: bool = Field(default=False)
    action: str  # "slot" | "unslot" | "manual_override_lost"
    note: Optional[str] = None  # free text for diagnostic context


class MigrationLog(SQLModel, table=True):
    """Phase 6.1 D-NEW-09 gate: one row per phase migration that has run.

    Used to make one-shot migrations idempotent across container restarts.
    ``phase_id`` is the primary key; ``completed_at`` is NULL during in-flight
    migrations (defensive — never read in current code path, future migrations
    can use it for crash-recovery detection).
    """

    phase_id: str = Field(primary_key=True)
    completed_at: Optional[str] = Field(default=None)


class DiscoveryState(SQLModel, table=True):
    """Phase 7.1 D-A3 / D-C2 single-row discovery counter + last-run timestamp.

    Mirrors :class:`SetupState`'s single-row id=1 pattern. Holds two pieces of
    state for the weekly LLM discovery layer:

    - ``plays_since_last_discovery``: incremented on every
      ``handle_track_played`` (Phase 7.1 Plan 01); reset to 0 on a successful
      ``discovery_call_weekly`` run (Phase 7.1 Plan 02). Read by
      ``compute_adaptive_pick_count`` to map listening intensity to the 3-7
      adaptive pick range (D-A3).
    - ``last_discovery_run_at``: ISO 8601 UTC string of the most recent
      successful ``discovery_call_weekly`` execution. Read by the startup
      catch-up gate in ``sync_scheduler.start_scheduler`` (Phase 7.1 Plan 02 /
      D-C2): if ``now - last_discovery_run_at > 7 days`` (or NULL), fire one
      discovery immediately on Composer startup. Resilient to NAS-asleep-on-Sunday.

    Schema migration policy (OPS-01): additive table, registered in
    :func:`app.database.init_db` BEFORE ``SQLModel.metadata.create_all`` runs.
    No Alembic. Existing deployments (Phase 7 only) get the table created on
    first restart after 7.1 deploys.
    """

    id: Optional[int] = Field(default=1, primary_key=True)
    plays_since_last_discovery: int = Field(default=0)
    last_discovery_run_at: Optional[str] = Field(default=None)
