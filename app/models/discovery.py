"""Phase 8 — Discovery data model.

Six SQLModel ``table=True`` classes that hold the entire Phase 8 net-new state:

- :class:`DiscoveryCandidate` — weekly cron output (replaced wholesale each
  Sunday). Read by ``/discover`` render; subtract :class:`DiscoveryDismissed`
  at read time per D-B2 for instant dismiss.
- :class:`DiscoveryAdd` — per-Composer-add lifecycle. Drives the ``/discover``
  status row (D-D4) and the ``/debug/discovery`` timeline.
- :class:`DiscoveryDismissed` — UNIQUE(mb_id) artist-only exclude (D-D5).
- :class:`MusicBrainzCache` — indefinite cache; amortises repeat MB lookups
  across weeks (D-A1).
- :class:`CostMeterBaseline` — single-row id=1; ``deploy_at`` gate for the
  home-page cost chip (D-B4 — historical/testing :class:`LLMUsage` rows are
  excluded permanently so the runaway-cost canary isn't polluted).
- :class:`WeeklyCronState` — single-row id=1; ``last_tick_at`` updated on
  every successful ``_weekly_maintenance_tick`` (D-B4 + D-B3 catch-up).

Schema policy (OPS-01): tables registered in :func:`app.database.init_db`
before ``SQLModel.metadata.create_all``. Additive only. ``Vibe.color`` and
``Track.plex_artist_mbid`` columns go via ``_migrate_add_columns``.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class DiscoveryCandidate(SQLModel, table=True):
    """Weekly cron output (D-A1..D-A4).

    Replaced wholesale each Sunday by ``artist_discovery_call_weekly``. Read at
    ``/discover`` render time; ``DiscoveryDismissed.mb_id`` is subtracted per
    D-B2 (instant dismiss without re-running the LLM).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    mb_id: str = Field(index=True)
    artist_name: str
    seed_track_id: int = Field(foreign_key="track.id", index=True)
    seed_vibe_id: int = Field(foreign_key="vibe.id", index=True)
    mb_listener_count: Optional[int] = None
    popularity_gate_pass: bool = Field(default=False)
    llm_rank: Optional[int] = None
    llm_rationale: Optional[str] = None
    factual_hook: Optional[str] = None  # D-A4 MB-anchored provenance string
    created_at: str = Field(index=True)


class DiscoveryAdd(SQLModel, table=True):
    """Per-Composer-add lifecycle row (D-D4).

    Drives the ``/discover`` status row + ``/debug/discovery`` timeline. The
    nullable timestamps form a left-to-right progression:
    ``added_at`` → ``lidarr_status_polled_at`` → ``composer_sync_seen_at`` →
    ``essentia_complete_at`` → ``vibe_slotted_at`` → ``removed_from_discover_at``.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    mb_id: str = Field(index=True)
    artist_name: str
    added_at: str = Field(index=True)
    lidarr_artist_id: Optional[int] = Field(default=None, index=True)
    # "searching" | "downloading" | "imported, awaiting sync" |
    # "analyzed, slotted" | "complete"
    lidarr_status: Optional[str] = None
    lidarr_status_polled_at: Optional[str] = None
    composer_sync_seen_at: Optional[str] = None
    essentia_complete_at: Optional[str] = None
    vibe_slotted_at: Optional[str] = None
    removed_from_discover_at: Optional[str] = None


class DiscoveryDismissed(SQLModel, table=True):
    """D-D5 — artist-only exclude. UNIQUE(mb_id) so re-dismiss is a no-op.

    Read at ``/discover`` render time to subtract from :class:`DiscoveryCandidate`.
    Future weekly cron also filters dismissed artists out of MusicBrainz
    candidates BEFORE the LLM re-rank, so they never reappear.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    mb_id: str = Field(unique=True, index=True)
    artist_name: str
    dismissed_at: str


class MusicBrainzCache(SQLModel, table=True):
    """Indefinite cache of MusicBrainz artist lookups (D-A1).

    Amortises repeat MB queries across weeks. MB rate-limits at 1 req/sec on
    the free tier, so caching is critical for the candidate pipeline.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    mb_id: str = Field(unique=True, index=True)
    payload_json: str
    cached_at: str


class CostMeterBaseline(SQLModel, table=True):
    """D-B4 — single-row id=1 baseline.

    Set once at Phase 8 deploy by ``run_phase_08_discovery_bootstrap``. Home
    cost chip filters :class:`LLMUsage` rows to ``called_at >= deploy_at`` so
    historical / testing rows don't pollute the runaway-cost canary. The
    ``/debug/suggestions`` cost-meter card remains unfiltered.
    """

    id: Optional[int] = Field(default=1, primary_key=True)
    deploy_at: str  # ISO 8601 UTC


class WeeklyCronState(SQLModel, table=True):
    """D-B4 + D-B3 — single-row id=1.

    ``last_tick_at`` is updated on every successful
    ``_weekly_maintenance_tick``. Read by the home cost chip ("next refresh
    in Nd") and the Phase 8 discovery startup catch-up gate.
    """

    id: Optional[int] = Field(default=1, primary_key=True)
    last_tick_at: Optional[str] = Field(default=None)
