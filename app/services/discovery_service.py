"""Phase 8 — Weekly LLM artist-discovery layer.

Plan 01 ships ONLY the foundational pieces required by every downstream plan:

- :data:`PHASE_08_MIGRATION_ID` + :func:`run_phase_08_discovery_bootstrap`
  (lifespan migration gate).
- :data:`VIBE_COLOR_PALETTE` (D-E2 locked Tailwind 4 -500 palette;
  ``orange-500`` LAST to avoid the Plex accent ``#e5a00d`` collision).
- :func:`assign_vibe_color` helper (deterministic ``palette[id % len(palette)]``).
- Module-singleton stub for the eventual ``ArtistDiscoveryStatus`` state
  (mirroring :mod:`app.services.suggestions_discovery` Phase 5 D-08 pattern).

Plan 02 will add:
  - ``compute_candidate_set_for_seed`` (D-A1 ListenBrainz + MB validate)
  - ``artist_discovery_call_weekly`` (LLM re-rank)
  - ``DISCOVERY_ARTIST_*`` constants (mirror SUGG-14 cap pattern)

Mirrors :mod:`app.services.suggestions_discovery` shape: module-singleton
state + sync DB helpers + ``asyncio.to_thread`` wrapping (Phase 5 D-08 /
D-09 — enforced by the static AST test in
``tests/test_event_handlers.py::test_no_blocking_plexapi_in_async``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.database import get_engine
from app.models.discovery import CostMeterBaseline, WeeklyCronState
from app.models.track import Track
from app.models.vibe import MigrationLog, Vibe

logger = logging.getLogger(__name__)

PHASE_08_MIGRATION_ID = "8.0-discovery-bootstrap"


# D-E2 locked palette — Tailwind 4 -500 stops. Orange-500 (#f97316) goes
# LAST in the assignment order because it collides closest with Composer's
# existing Plex-orange accent (#e5a00d) — only used at 7+ vibes when
# alternatives are exhausted. 9 hues = headroom over the 7-vibe maximum.
VIBE_COLOR_PALETTE: tuple[str, ...] = (
    "#3b82f6",  # blue-500
    "#8b5cf6",  # violet-500
    "#10b981",  # emerald-500
    "#f43f5e",  # rose-500
    "#f59e0b",  # amber-500
    "#06b6d4",  # cyan-500
    "#ec4899",  # pink-500
    "#84cc16",  # lime-500
    "#f97316",  # orange-500 — LAST per D-E2 (Plex accent collision)
)


def assign_vibe_color(vibe_id: int) -> str:
    """D-E2 — deterministic palette assignment: ``palette[id % len]``.

    Stable across re-clusters for the same vibe id (per the D-22 manual-
    override preservation rule, extended to colors). New vibes from a
    re-cluster pick up the next palette slot via the modulo; existing
    vibes keep their color.
    """
    return VIBE_COLOR_PALETTE[vibe_id % len(VIBE_COLOR_PALETTE)]


@dataclass
class ArtistDiscoveryStatus:
    """Module-singleton in-memory status (Phase 5 D-08 pattern).

    Plan 02 will populate ``state`` / ``last_error`` / ``last_candidates_made``
    as part of the LLM re-rank pipeline. Plan 01 only declares the shape so
    the singleton + ``get_state()`` accessor exist for downstream callers.
    """

    state: str = "idle"  # "idle" | "running" | "cost_locked" | "error"
    last_run_at: Optional[str] = None
    last_error: Optional[str] = None
    last_candidates_made: int = 0


_status: ArtistDiscoveryStatus = ArtistDiscoveryStatus()


def get_state() -> ArtistDiscoveryStatus:
    """Public accessor for the module singleton (Phase 5 D-08)."""
    return _status


def _reset_state_for_tests() -> None:
    """autouse fixture target — tests reset the singleton between cases."""
    global _status
    _status = ArtistDiscoveryStatus()


# ============================================================================
# Phase 8 lifespan bootstrap (D-B4 baseline + D-E2 vibe color backfill +
# WeeklyCronState seed + Pitfall 12 plex_artist_mbid backfill).
# Gated by MigrationLog per the Phase 6.1 / 7.0 / 7.1 pattern.
# ============================================================================


def _read_migration_log_sync(phase_id: str) -> Optional[MigrationLog]:
    with Session(get_engine()) as session:
        return session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == phase_id)
        ).first()


def _upsert_migration_log_sync(phase_id: str, completed_at: Optional[str]) -> None:
    with Session(get_engine()) as session:
        row = session.exec(
            select(MigrationLog).where(MigrationLog.phase_id == phase_id)
        ).first()
        if row is None:
            row = MigrationLog(phase_id=phase_id, completed_at=completed_at)
            session.add(row)
        else:
            row.completed_at = completed_at
            session.add(row)
        session.commit()


def _bootstrap_baseline_sync() -> None:
    """D-B4 — stamp CostMeterBaseline(id=1, deploy_at=now) once."""
    with Session(get_engine()) as session:
        existing = session.get(CostMeterBaseline, 1)
        if existing is None:
            session.add(CostMeterBaseline(
                id=1,
                deploy_at=datetime.now(timezone.utc).isoformat(),
            ))
            session.commit()


def _bootstrap_weekly_cron_state_sync() -> None:
    """D-B4 / D-B3 — seed WeeklyCronState(id=1, last_tick_at=NULL) once."""
    with Session(get_engine()) as session:
        existing = session.get(WeeklyCronState, 1)
        if existing is None:
            session.add(WeeklyCronState(id=1, last_tick_at=None))
            session.commit()


def _backfill_vibe_colors_sync() -> None:
    """D-E2 — backfill ``Vibe.color`` for any existing rows that lack one.

    Idempotent: only touches rows with NULL ``color``. The deterministic
    ``palette[id % len]`` assignment means re-running the backfill on a
    later restart (after the gate is reset) produces identical colors.
    """
    with Session(get_engine()) as session:
        uncoloured = session.exec(
            select(Vibe).where(Vibe.color.is_(None))
        ).all()
        for vibe in uncoloured:
            vibe.color = assign_vibe_color(vibe.id)
            session.add(vibe)
        if uncoloured:
            session.commit()
            logger.info(
                "Phase 8 bootstrap: backfilled color on %d vibes",
                len(uncoloured),
            )


def _backfill_track_artist_mbids_sync() -> None:
    """Pitfall 12 — populate ``Track.plex_artist_mbid`` for the cross-surface
    dedup gate.

    Strategy: enumerate distinct artist names from Track WHERE
    ``plex_artist_mbid IS NULL``; for each name, call ``plex_client`` to fetch
    the MBID from Plex; on success, UPDATE every Track row for that artist.
    On failure (Plex unreachable, artist not found, MBID missing), leave the
    row NULL and continue — the next bootstrap or a future quick task
    retries. The dedup gate in Plan 02 treats NULL as "unknown" so a missing
    MBID only costs at most one duplicate appearing on /discover.

    Idempotent: re-runs only touch rows still NULL.
    """
    # Lazy import — plex_client triggers a chain that lands on app.config
    # which isn't desirable at module import time for tests that mock
    # the helper before the bootstrap runs.
    try:
        from app.services import plex_client
    except Exception:
        logger.warning(
            "Phase 8 bootstrap: plex_client unavailable; "
            "skipping plex_artist_mbid backfill"
        )
        return

    # 1) Collect distinct names that need backfill.
    with Session(get_engine()) as session:
        rows = session.exec(
            select(Track.artist)
            .where(Track.plex_artist_mbid.is_(None))
            .distinct()
        ).all()
    names = [r for r in rows if r]
    if not names:
        logger.info(
            "Phase 8 bootstrap: no tracks need plex_artist_mbid backfill"
        )
        return

    # 2) For each name, lookup + persist. Per-name failures are isolated.
    backfilled = 0
    failed = 0
    for name in names:
        try:
            mbid = plex_client.get_artist_mbid_by_name(name)
        except Exception:
            logger.exception(
                "plex_artist_mbid lookup raised for name=%r", name,
            )
            failed += 1
            continue
        if not mbid:
            continue
        try:
            with Session(get_engine()) as session:
                target_rows = session.exec(
                    select(Track)
                    .where(Track.artist == name)
                    .where(Track.plex_artist_mbid.is_(None))
                ).all()
                for row in target_rows:
                    row.plex_artist_mbid = mbid
                    session.add(row)
                if target_rows:
                    session.commit()
                    backfilled += len(target_rows)
        except Exception:
            logger.exception(
                "plex_artist_mbid persist failed for name=%r", name,
            )
            failed += 1
    logger.info(
        "Phase 8 bootstrap: plex_artist_mbid backfilled %d tracks across "
        "%d artists (%d failed)",
        backfilled, len(names), failed,
    )


async def run_phase_08_discovery_bootstrap() -> None:
    """Lifespan one-shot. Gated by ``MigrationLog(phase_id='8.0-discovery-bootstrap')``.

    Steps (each step is best-effort; failures leave the gate's
    ``completed_at`` NULL so the next restart retries):

    1. Stamp ``CostMeterBaseline(id=1, deploy_at=now)``.
    2. Seed ``WeeklyCronState(id=1, last_tick_at=NULL)``.
    3. Backfill ``Vibe.color`` for any existing rows using the locked palette.
    4. Best-effort backfill ``Track.plex_artist_mbid`` via ``plex_client``.

    Mirrors :func:`app.services.suggestions_service.run_phase_07_suggestions_bootstrap`.
    """
    existing = await asyncio.to_thread(
        _read_migration_log_sync, PHASE_08_MIGRATION_ID,
    )
    if existing is not None and existing.completed_at is not None:
        logger.info(
            "Phase 8 discovery bootstrap already complete; skipping."
        )
        return

    # In-flight marker — next restart retries on failure.
    await asyncio.to_thread(
        _upsert_migration_log_sync, PHASE_08_MIGRATION_ID, None,
    )

    try:
        await asyncio.to_thread(_bootstrap_baseline_sync)
        await asyncio.to_thread(_bootstrap_weekly_cron_state_sync)
        await asyncio.to_thread(_backfill_vibe_colors_sync)
        # Pitfall 12 — best-effort plex_artist_mbid backfill. Wrapped in its
        # own try so a Plex outage doesn't abort the entire bootstrap (the
        # baseline + weekly_cron_state + vibe colors have ALREADY succeeded;
        # we don't want to roll those back).
        try:
            await asyncio.to_thread(_backfill_track_artist_mbids_sync)
        except Exception:
            logger.exception(
                "Phase 8 bootstrap: plex_artist_mbid backfill raised; "
                "continuing — next restart will retry remaining rows."
            )
    except Exception:
        logger.exception(
            "Phase 8 discovery bootstrap failed; "
            "will retry on next restart."
        )
        return

    await asyncio.to_thread(
        _upsert_migration_log_sync,
        PHASE_08_MIGRATION_ID,
        datetime.now(timezone.utc).isoformat(),
    )
    logger.info("Phase 8 discovery bootstrap complete.")
