from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class LLMUsage(SQLModel, table=True):
    """One row per Anthropic API call. Cost circuit breaker (Phase 7) reads from this table.

    Phase 5 (D-04) ships the table empty — no client writes to it yet. The table
    is in place so Phase 7's first ranking call already has the breaker storage
    available with no migration under fire.

    Phase 7.1 (D-A3 / D-C3): adds optional ``error_text`` for diagnostic
    capture on failed calls (discovery_weekly_error rows + future
    retry-with-truncation rows). Defaults to NULL on existing rows so the
    ALTER TABLE ADD COLUMN migration is backwards-compatible.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    called_at: str = Field(index=True)              # ISO 8601 UTC
    purpose: str = Field(index=True)                # "taste_profile_summary" | "vibe_naming" | "suggestions_ranking" | "discovery_weekly" | "discovery_weekly_<suffix>"
    model: str
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate_usd: float = 0.0
    # Phase 7.1 — populated only on failure paths (truncation retry,
    # discovery cron failure, breaker trip). Capped to 500 chars by
    # callers. NULL on every successful call.
    error_text: Optional[str] = Field(default=None)
