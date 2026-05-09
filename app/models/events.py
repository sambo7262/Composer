"""Pydantic event types for the asyncio.Queue event bus (D-06).

Pure pydantic — NOT SQLModel `table=True`. These ride on the queue and get
persisted by `event_handlers.dispatch_event` into the EventLog table via
INSERT OR IGNORE on the dedupe_key.

Naming convention: `*Event` suffix + Literal `type` discriminator.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class BaseEvent(BaseModel):
    """Base for all event-bus events. Subclasses MUST set `type` Literal."""

    plex_rating_key: Optional[str] = None
    source: Literal["webhook", "poll", "manual"] = "webhook"
    received_at: str  # ISO 8601 UTC
    raw_payload: Optional[str] = None  # JSON, truncated; for EventLog audit


class RatingChangedEvent(BaseEvent):
    type: Literal["rating_changed"] = "rating_changed"
    new_rating: Optional[float] = None  # raw 0-10; None means rating cleared (Pitfall 2)


class TrackPlayedEvent(BaseEvent):
    type: Literal["track_played"] = "track_played"
    last_viewed_at: str  # ISO 8601 UTC; from webhook payload, not re-fetched (Pitfall 7)


class LibraryAddedEvent(BaseEvent):
    type: Literal["library_added"] = "library_added"
    library_section_id: Optional[int] = None


class WebhookTestEvent(BaseEvent):
    type: Literal["webhook_test"] = "webhook_test"
