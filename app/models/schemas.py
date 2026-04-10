"""Pydantic schemas for structured LLM I/O via Instructor.

These are NOT SQLModel table models -- they define the contract between
the LLM (via Instructor) and the playlist engine.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class FeatureCriteria(BaseModel):
    """Audio feature criteria extracted from a user's mood description.

    Used by Instructor to parse LLM output into structured search parameters.
    All numeric ranges are inclusive [min, max].
    """

    energy_min: float = Field(ge=0.0, le=1.0, description="Minimum energy level (0=calm, 1=intense)")
    energy_max: float = Field(ge=0.0, le=1.0, description="Maximum energy level (0=calm, 1=intense)")
    tempo_min: float = Field(ge=40.0, le=220.0, description="Minimum tempo in BPM (40=very slow, 220=very fast)")
    tempo_max: float = Field(ge=40.0, le=220.0, description="Maximum tempo in BPM (40=very slow, 220=very fast)")
    danceability_min: float = Field(ge=0.0, le=1.0, description="Minimum danceability (0=not danceable, 1=very danceable)")
    danceability_max: float = Field(ge=0.0, le=1.0, description="Maximum danceability (0=not danceable, 1=very danceable)")
    valence_min: float = Field(ge=0.0, le=1.0, description="Minimum valence/positivity (0=sad/dark, 1=happy/bright)")
    valence_max: float = Field(ge=0.0, le=1.0, description="Maximum valence/positivity (0=sad/dark, 1=happy/bright)")
    genres: list[str] = Field(default_factory=list, description="Include only these genres (empty=all)")
    artists: list[str] = Field(default_factory=list, description="Prefer these artists (empty=no preference)")
    exclude_genres: list[str] = Field(default_factory=list, description="Exclude these genres")
    explanation: str = Field(description="Brief explanation of how the mood maps to these criteria")

    @model_validator(mode="after")
    def validate_min_max_ranges(self) -> FeatureCriteria:
        """Ensure min <= max for all range pairs."""
        if self.energy_min > self.energy_max:
            raise ValueError("energy_min must be <= energy_max")
        if self.tempo_min > self.tempo_max:
            raise ValueError("tempo_min must be <= tempo_max")
        if self.danceability_min > self.danceability_max:
            raise ValueError("danceability_min must be <= danceability_max")
        if self.valence_min > self.valence_max:
            raise ValueError("valence_min must be <= valence_max")
        return self


class TrackSelection(BaseModel):
    """LLM's track selection from a list of candidates.

    Used by Instructor to parse the LLM's playlist picks into validated IDs.
    """

    track_ids: list[int] = Field(description="Database IDs of selected tracks, in playlist order")
    explanation: str = Field(description="Brief explanation of why these tracks were chosen")
