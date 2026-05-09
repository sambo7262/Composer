from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class EventLog(SQLModel, table=True):
    """Dedupe + audit trail for inbound webhook + poll events.

    UNIQUE(dedupe_key) is the source of truth for dedupe — `INSERT OR IGNORE` pattern
    in `app/services/event_handlers._insert_event_log_sync`.

    See:
    - Phase 5 D-07: dedupe via SHA-256 of (event_type|ratingKey|user_rating|5s_bucket).
    - Pitfall 1: webhook + polling overlap resolves naturally through this UNIQUE.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)              # "webhook" | "poll" | "manual"
    event_type: str = Field(index=True)          # "rating_changed" | "track_played" | "library_added" | "webhook_test"
    plex_rating_key: Optional[str] = Field(default=None, index=True)
    dedupe_key: str = Field(unique=True, index=True)
    received_at: str = Field(index=True)         # ISO 8601 UTC
    processed_at: Optional[str] = Field(default=None)
    handler_error: Optional[str] = Field(default=None)
    raw_payload: Optional[str] = Field(default=None)  # JSON; truncated to 4KB
