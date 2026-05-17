from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class Track(SQLModel, table=True):
    """Database model for a music track synced from Plex."""

    id: Optional[int] = Field(default=None, primary_key=True)
    plex_rating_key: str = Field(unique=True, index=True)
    title: str = Field(index=True)
    artist: str = Field(index=True)
    album: str = Field(default="")
    genre: str = Field(default="")
    year: Optional[int] = Field(default=None)
    duration_ms: int = Field(default=0)
    added_at: Optional[str] = Field(default=None)
    updated_at: Optional[str] = Field(default=None)
    synced_at: Optional[str] = Field(default=None)

    # File path from Plex media parts (D-12)
    file_path: Optional[str] = Field(default=None)

    # Audio features (all nullable -- NULL means not yet analyzed)
    energy: Optional[float] = Field(default=None, index=True)
    tempo: Optional[float] = Field(default=None)
    danceability: Optional[float] = Field(default=None, index=True)
    valence: Optional[float] = Field(default=None, index=True)
    musical_key: Optional[str] = Field(default=None)
    scale: Optional[str] = Field(default=None)
    spectral_complexity: Optional[float] = Field(default=None)
    loudness: Optional[float] = Field(default=None)

    # Analysis tracking (AUDIO-04)
    analyzed_at: Optional[str] = Field(default=None)
    analysis_error: Optional[str] = Field(default=None)

    # Phase 5 (D-15): Plex rating + listening signals.
    # user_rating is RAW 0-10 (Pitfall 2). Convert to display via
    # app.services.rating_helpers.stars_from_user_rating().
    user_rating: Optional[float] = Field(default=None, index=True)
    last_viewed_at: Optional[str] = Field(default=None)
    view_count: Optional[int] = Field(default=0)
    rating_changed_at: Optional[str] = Field(default=None)

    # Phase 6 (D-29 / D-17) — retroactive auto-slot flag. SQLite has no BOOL,
    # so we store INTEGER 0/1; SQLModel maps Optional[int] over the column.
    # The DB-side column was added in Plan 01's _migrate_add_columns shim;
    # Plan 02 promotes it to a SQLModel-visible field so vibe_service can
    # read/write it via the ORM. The partial index ix_track_pending_slot_in
    # (added in Plan 01) covers WHERE pending_slot_in = 1 lookups.
    pending_slot_in: Optional[int] = Field(default=0)

    # Phase 8 Pitfall 12 — MusicBrainz Artist Identifier for cross-surface dedup.
    # Populated from plex_client during Plan 01 backfill + on subsequent syncs.
    # NULL until backfilled; Plan 02 _get_in_library_mbids_sync filters out NULLs.
    plex_artist_mbid: Optional[str] = Field(default=None, index=True)


class SyncState(SQLModel, table=True):
    """Tracks the last sync timestamp and total track count."""

    id: Optional[int] = Field(default=None, primary_key=True)
    last_sync_started: Optional[str] = Field(default=None)
    last_sync_completed: Optional[str] = Field(default=None)
    total_tracks: int = Field(default=0)
