from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class TasteProfile(SQLModel, table=True):
    """Single-row cache (id=1). Upserted by taste_profile_service on >=10% rated-set delta.

    Per D-17 + D-18:
    - Structured part: 4-D centroid + top artists/genres + counts.
    - LLM-generated summary text: ~200-word prose; populated in Plan 03 via
      anthropic_client (with prompt caching).
    """

    id: Optional[int] = Field(default=1, primary_key=True)
    rated_track_count: int = 0
    centroid_energy: Optional[float] = None
    centroid_tempo: Optional[float] = None
    centroid_danceability: Optional[float] = None
    centroid_valence: Optional[float] = None
    top_artists_json: str = ""        # JSON array of {"artist": str, "count": int}
    top_genres_json: str = ""         # JSON array of {"genre": str, "count": int}
    summary_text: str = ""            # ~200-word LLM summary; populated by Plan 03
    computed_at: Optional[str] = None  # ISO 8601 UTC
