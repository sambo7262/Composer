"""Database models for playlist history (D-17).

Records playlists that have been generated and pushed to Plex,
enabling Phase 5 history browsing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class Playlist(SQLModel, table=True):
    """A generated playlist that was pushed to Plex."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    mood_description: str = Field(default="")
    track_count: int = Field(default=0)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PlaylistTrack(SQLModel, table=True):
    """A track within a saved playlist, with ordering."""

    id: Optional[int] = Field(default=None, primary_key=True)
    playlist_id: int = Field(foreign_key="playlist.id", index=True)
    track_id: int = Field(foreign_key="track.id", index=True)
    position: int = Field(default=0)
