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


class SyncState(SQLModel, table=True):
    """Tracks the last sync timestamp and total track count."""

    id: Optional[int] = Field(default=None, primary_key=True)
    last_sync_started: Optional[str] = Field(default=None)
    last_sync_completed: Optional[str] = Field(default=None)
    total_tracks: int = Field(default=0)
