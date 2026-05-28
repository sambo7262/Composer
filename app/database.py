from __future__ import annotations

import logging
import sqlite3
from typing import Generator

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from app import config

logger = logging.getLogger(__name__)

_engine = None


def get_engine():
    """Get or create the database engine. Lazy initialization allows test overrides."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.DATABASE_URL,
            echo=False,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            """Set SQLite pragmas on every connection: WAL mode and foreign keys."""
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def reset_engine():
    """Reset the engine singleton. Used by tests to pick up config changes."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_session() -> Generator[Session, None, None]:
    """Yield a database session."""
    with Session(get_engine()) as session:
        yield session


def _migrate_add_columns(engine) -> None:
    """Add any missing columns to existing tables (lightweight schema migration).

    WR-08: routes through engine.raw_connection() instead of opening a fresh
    sqlite3.connect(). The engine's @event.listens_for(_engine, "connect")
    pragma listener fires on the pooled connection (sets journal_mode=WAL +
    foreign_keys=ON), so the migration runs in the same WAL/FK regime as
    every other engine connection. Avoids brief journal-mode conflicts with
    pool readers and ensures any future migration that touches FK columns
    enforces them during the migration itself.
    """
    import logging
    import sqlite3
    conn = engine.raw_connection()
    cursor = conn.cursor()

    # Get existing columns for the track table
    cursor.execute("PRAGMA table_info(track)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # Columns added in Phase 3 (audio feature extraction) and Phase 5 (D-15: rating/listening)
    new_columns = {
        # Phase 3
        "file_path": "TEXT",
        "energy": "REAL",
        "tempo": "REAL",
        "danceability": "REAL",
        "valence": "REAL",
        "musical_key": "TEXT",
        "scale": "TEXT",
        "spectral_complexity": "REAL",
        "loudness": "REAL",
        "analyzed_at": "TEXT",
        "analysis_error": "TEXT",
        # Phase 5 (D-15) — raw user_rating 0-10 per Pitfall 2
        "user_rating": "REAL",
        "last_viewed_at": "TEXT",
        "view_count": "INTEGER DEFAULT 0",
        "rating_changed_at": "TEXT",
        # Phase 6 (D-29) — pending slot-in flag (D-17 retroactive auto-slot).
        # SQLite has no BOOL; INTEGER 0/1 with DEFAULT 0.
        "pending_slot_in": "INTEGER DEFAULT 0",
    }

    for col_name, col_type in new_columns.items():
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE track ADD COLUMN {col_name} {col_type}")

    # Phase 5 RATE-04: index on user_rating for the rated-set view.
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_track_user_rating ON track(user_rating)")
    # Phase 5 DEBUG-01: index on EventLog.received_at for /debug/events ORDER BY DESC LIMIT 50.
    # CREATE IF NOT EXISTS guards against re-running on existing DBs.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS ix_eventlog_received_at_desc ON eventlog(received_at)"
    )

    # Phase 6 (D-30) — vibe-clustering indexes.
    # ix_trackvibe_vibe_id: "show me a vibe's members" queries (/debug/vibes,
    # future vibe detail page). Composite-PK already indexes (track_id, vibe_id)
    # leftmost — this adds the right-leading index for the reverse direction.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS ix_trackvibe_vibe_id ON trackvibe(vibe_id)"
    )
    # ix_managedplaylist_kind: filter "vibe" vs "suggestions" (Phase 7) playlists.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS ix_managedplaylist_kind ON managedplaylist(kind)"
    )
    # ix_track_pending_slot_in: PARTIAL index on the analysis-service post-hook
    # selector "WHERE pending_slot_in = 1 AND user_rating > 0" (D-17). Partial
    # because the vast majority of tracks have pending_slot_in=0 — full index
    # would be wasted I/O.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS ix_track_pending_slot_in ON track(pending_slot_in) "
        "WHERE pending_slot_in = 1"
    )

    # Phase 6 Plan 04 (D-20) — recluster_mode flag for SetupState.
    # SetupState is a single-row table (id=1); ALTER ADD COLUMN with DEFAULT 0
    # is safe and additive (Pitfall 19). Guarded by column-existence check so
    # the migration is idempotent when re-run on already-migrated DBs.
    try:
        cursor.execute("PRAGMA table_info(setupstate)")
        setupstate_cols = {row[1] for row in cursor.fetchall()}
        if "recluster_mode" not in setupstate_cols:
            cursor.execute(
                "ALTER TABLE setupstate ADD COLUMN recluster_mode INTEGER DEFAULT 0"
            )
    except sqlite3.OperationalError:
        # Table doesn't exist yet (very early init order); create_all will make
        # it correctly from the SQLModel definition. Plan 04 ALTER is only for
        # legacy DBs that were created on Plan 01-03 schema.
        pass

    # Phase 7.1 (D-A3 / D-C3) — additive error_text column on LLMUsage
    # for diagnostic capture on failed discovery calls (Plan 02 +
    # Plan 03 consume). LLMUsage is small + append-only; the ALTER
    # TABLE ADD COLUMN with no DEFAULT is safe + non-blocking on SQLite.
    # Guarded by a column-existence check so the migration is
    # idempotent when re-run on already-migrated DBs.
    try:
        cursor.execute("PRAGMA table_info(llmusage)")
        llmusage_cols = {row[1] for row in cursor.fetchall()}
        if "error_text" not in llmusage_cols:
            cursor.execute(
                "ALTER TABLE llmusage ADD COLUMN error_text TEXT"
            )
    except sqlite3.OperationalError:
        # Table doesn't exist yet (very early init order); create_all
        # will materialize it from the SQLModel definition above with
        # the column already present. The ALTER is only for legacy
        # Phase 5/6/7 DBs.
        pass

    # Phase 8 D-E2 — additive Vibe.color column.
    # Hex string like "#3b82f6"; backfilled in run_phase_08_discovery_bootstrap
    # using the locked Tailwind 4 -500 palette (orange-500 LAST per D-E2).
    try:
        cursor.execute("PRAGMA table_info(vibe)")
        vibe_cols = {row[1] for row in cursor.fetchall()}
        if "color" not in vibe_cols:
            cursor.execute("ALTER TABLE vibe ADD COLUMN color TEXT")
            logging.getLogger(__name__).info(
                "Phase 8 migration: added vibe.color column"
            )
        # Phase 8 D-A2 — additive Vibe.last_seed_track_id column.
        # Round-robin rotation cursor for discovery_service per-vibe seed
        # selection (Plan 02). NULL means "never picked a seed yet";
        # selector wraps to smallest id when exhausted.
        if "last_seed_track_id" not in vibe_cols:
            cursor.execute(
                "ALTER TABLE vibe ADD COLUMN last_seed_track_id INTEGER"
            )
            logging.getLogger(__name__).info(
                "Phase 8 migration: added vibe.last_seed_track_id column"
            )
    except sqlite3.OperationalError:
        # vibe table doesn't exist yet; SQLModel.create_all will materialise
        # it with the color column from the model definition. No-op here.
        pass

    # Phase 8 Pitfall 12 — additive Track.plex_artist_mbid column.
    # MusicBrainz Artist Identifier (36-char UUID); backfilled best-effort in
    # run_phase_08_discovery_bootstrap via plex_client. Used by Plan 02's
    # _get_in_library_mbids_sync as the SINGLE source of truth for the
    # cross-surface dedup gate (Pitfall 12 is locked mandatory from day one).
    # Pre-backfill rows are NULL — the dedup gate treats NULL as "unknown",
    # so the worst case is a duplicate appearing once on /discover until the
    # backfill catches up.
    try:
        cursor.execute("PRAGMA table_info(track)")
        track_cols = {row[1] for row in cursor.fetchall()}
        if "plex_artist_mbid" not in track_cols:
            cursor.execute(
                "ALTER TABLE track ADD COLUMN plex_artist_mbid TEXT"
            )
            logging.getLogger(__name__).info(
                "Phase 8 migration: added track.plex_artist_mbid column"
            )
    except sqlite3.OperationalError:
        # track table doesn't exist yet; SQLModel.create_all will materialise
        # it with the field from the model definition once Plan 01 lands.
        pass

    # Phase 6 Plan 04 (D-36) — ix_slotinlog_timestamp for the
    # "Last 20 slot-in decisions" diagnostic feed (ORDER BY timestamp DESC LIMIT 20).
    # SQLModel ships a column-level index=True via the model definition; this
    # CREATE INDEX IF NOT EXISTS is the explicit migration pin so the index
    # exists even if create_all skipped it (legacy DB path).
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS ix_slotinlog_timestamp ON slotinlog(timestamp)"
    )

    # Recalculate energy as weighted combination of loudness, tempo, complexity.
    # Old energy was spectral_rms-only which is unreliable (mastering-dependent).
    # New formula uses loudness (35%), existing energy/rms (25%), tempo (25%), complexity (15%).
    # The existing energy column was already normalized to [0,1] by prior migration,
    # so we use it directly as the rms component.
    cursor.execute("""
        UPDATE track SET energy = ROUND(MIN(MAX(
            0.35 * MIN(MAX((COALESCE(loudness, -20) + 20) / 15.0, 0.0), 1.0)
            + 0.25 * COALESCE(energy, 0.0)
            + 0.25 * MIN(MAX((COALESCE(tempo, 100) - 60) / 140.0, 0.0), 1.0)
            + 0.15 * MIN(MAX(COALESCE(spectral_complexity, 0) / 20.0, 0.0), 1.0)
        , 0.0), 1.0), 4)
        WHERE analyzed_at IS NOT NULL AND loudness IS NOT NULL
    """)
    updated = cursor.rowcount
    if updated > 0:
        logging.getLogger(__name__).info("Recalculated weighted energy for %d tracks", updated)

    conn.commit()
    conn.close()


def init_db() -> None:
    """Create all database tables and migrate schema if needed."""
    # Import models to register them with SQLModel metadata
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import Track, SyncState  # noqa: F401
    from app.models.playlist import Playlist, PlaylistTrack  # noqa: F401
    # Phase 5 (D-19) — register new tables before create_all
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    # Phase 6 (D-28) — register vibe tables before create_all
    # Phase 6.1 (D-NEW-09) — register MigrationLog for the one-shot migration gate.
    # Phase 7.1 (D-A3 / D-C2) — register DiscoveryState for the discovery
    # counter + last-run timestamp.
    from app.models.vibe import (  # noqa: F401
        DiscoveryState,  # Phase 7.1 (D-A3 / D-C2) discovery counter + last-run
        ManagedPlaylist,
        MigrationLog,
        SetupState,
        SlotInLog,  # Phase 6 Plan 04 (D-36)
        TrackVibe,
        Vibe,
    )
    # Phase 7 Plan 01 (SUGG-02) — register SuggestionsMirror before create_all.
    # Phase 7 Plan 02 — register SuggestionHistory, NegativeSignal,
    # RefillTriggerLog (SUGG-07, D-11/D-12/D-13, /debug/suggestions feed).
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal,
        RefillTriggerLog,
        SuggestionHistory,
        SuggestionsMirror,
    )
    # Phase 8 — register discovery models (DiscoveryCandidate, DiscoveryAdd,
    # DiscoveryDismissed, MusicBrainzCache, CostMeterBaseline, WeeklyCronState)
    # so SQLModel.metadata.create_all materialises the tables on fresh DBs.
    from app.models.discovery import (  # noqa: F401
        CostMeterBaseline,
        DiscoveryAdd,
        DiscoveryCandidate,
        DiscoveryDismissed,
        MusicBrainzCache,
        WeeklyCronState,
    )

    engine = get_engine()
    # create_all MUST run before _migrate_add_columns so the eventlog table exists
    # when the CREATE INDEX statement targets it.
    SQLModel.metadata.create_all(engine)

    # Add any missing columns to existing tables.
    # WR-01 fix: log exceptions instead of swallowing them silently. By the
    # time we reach here, create_all has already created every table, so a
    # "table missing" error is no longer the expected case — surface it.
    try:
        _migrate_add_columns(engine)
    except (sqlite3.OperationalError, OperationalError) as exc:
        logger.warning(
            "init_db: lightweight migration encountered an OperationalError "
            "(likely a missing column / index on a non-critical legacy "
            "table); continuing. error=%s",
            exc,
        )
    except Exception:
        logger.exception("init_db: unexpected error in _migrate_add_columns")
        raise
