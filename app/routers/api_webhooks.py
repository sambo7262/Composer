"""Plex webhook receiver (D-05, EVT-01).

Contract:
- POST /api/webhooks/plex
  - Multipart form with `payload` JSON string field. (Annotated[str, Form()] +
    json.loads — NEVER pydantic.Json[Model] — FastAPI bug #10997.)
  - ALWAYS returns 200, even on parse failure (Pitfall 1 — Plex retries on non-2xx).
  - Push-and-return: builds a typed Pydantic event, queue.put_nowait(event),
    Response(status_code=200). Target latency <50ms.
  - Does ZERO PlexAPI / DB work inline (D-09 / EVT-06).

- GET /api/webhooks/plex/last-test
  - HTMX-polled by the wizard. Returns an HTML partial that turns "✓ received"
    when a test event has arrived since the last `?since=` cursor.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from app.models.events import (
    LibraryAddedEvent,
    RatingChangedEvent,
    TrackPlayedEvent,
    WebhookTestEvent,
)
from app.services.event_bus import get_event_bus
from app.services.webhook_test_state import (
    arm_test,
    disarm_test,
    get_armed_at_iso,
    is_test_armed,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Module-level cache for the wizard's "last test event" indicator (D-13).
_last_test_received_at: Optional[str] = None
_last_test_payload: Optional[Dict[str, Any]] = None


def get_templates():
    """Lazy import to avoid circular dep with app.main (Pattern E)."""
    from app.main import templates

    return templates


@router.post("/plex")
async def plex_webhook(
    payload: Annotated[str, Form()],
    thumb: Annotated[Optional[UploadFile], File()] = None,
):
    """Push-and-return webhook handler.

    NEVER use pydantic.Json[Model] inside Form() (FastAPI bug #10997).
    NEVER make PlexAPI calls in this handler (D-09 — defer to dispatcher).
    ALWAYS return 200 (Pitfall 1 — Plex retries on non-2xx).
    """
    global _last_test_received_at, _last_test_payload

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Webhook payload not valid JSON; returning 200 anyway")
        return Response(status_code=200)

    if not isinstance(data, dict):
        logger.warning("Webhook payload was not a JSON object; ignoring")
        return Response(status_code=200)

    event_type = data.get("event", "")
    metadata = data.get("Metadata", {}) or {}
    rating_key = metadata.get("ratingKey")
    raw_payload_str = json.dumps(data)[:4096]
    received_at = datetime.now(timezone.utc).isoformat()
    bus = get_event_bus()

    # Wizard test mode: if armed in the last ARM_TTL_SECONDS, also emit a
    # WebhookTestEvent so the wizard's HTMX poll picks up the indicator.
    if is_test_armed():
        _last_test_received_at = received_at
        _last_test_payload = data
        disarm_test()
        bus.put_nowait(
            WebhookTestEvent(
                plex_rating_key=str(rating_key) if rating_key else None,
                source="webhook",
                received_at=received_at,
                raw_payload=raw_payload_str,
            )
        )

    if event_type == "media.rate":
        # Rating field can be at the top level or nested in Metadata
        new_rating = (
            data.get("Rating") if "Rating" in data else metadata.get("userRating")
        )
        try:
            rating_value = float(new_rating) if new_rating is not None else None
        except (TypeError, ValueError):
            rating_value = None
        bus.put_nowait(
            RatingChangedEvent(
                plex_rating_key=str(rating_key) if rating_key else None,
                new_rating=rating_value,
                source="webhook",
                received_at=received_at,
                raw_payload=raw_payload_str,
            )
        )
    elif event_type == "media.scrobble":
        last_viewed = metadata.get("lastViewedAt") or received_at
        bus.put_nowait(
            TrackPlayedEvent(
                plex_rating_key=str(rating_key) if rating_key else None,
                last_viewed_at=str(last_viewed),
                source="webhook",
                received_at=received_at,
                raw_payload=raw_payload_str,
            )
        )
    elif event_type == "library.new":
        section_id = metadata.get("librarySectionID")
        try:
            section_int = int(section_id) if section_id is not None else None
        except (TypeError, ValueError):
            section_int = None
        bus.put_nowait(
            LibraryAddedEvent(
                plex_rating_key=str(rating_key) if rating_key else None,
                library_section_id=section_int,
                source="webhook",
                received_at=received_at,
                raw_payload=raw_payload_str,
            )
        )
    # All other event types ignored silently. ALWAYS 200.
    return Response(status_code=200)


@router.post("/plex/test-arm", response_class=HTMLResponse)
async def arm_test_webhook(request: Request):
    """Wizard's 'Test webhook' button arms the receiver to treat the next inbound
    event as the test event (Pitfall 6).

    Returns the indicator partial in the 'waiting' state (HTMX swaps it in
    place of the per-candidate placeholder div).
    """
    arm_test()
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/webhook_test_indicator.html",
        {
            "test_armed_at": get_armed_at_iso() or "",
            "last_test_received_at": _last_test_received_at,
        },
    )


@router.get("/plex/last-test", response_class=HTMLResponse)
async def last_test_event(request: Request, since: Optional[str] = None):
    """HTMX-polled by the wizard. Returns the test-indicator partial.

    The partial self-polls every 2s. The ``since`` query param is the timestamp
    of when the wizard armed test mode — the partial flips to ✓ only when a
    test event has arrived strictly AFTER ``since``.
    """
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/webhook_test_indicator.html",
        {
            "test_armed_at": since or get_armed_at_iso() or "",
            "last_test_received_at": _last_test_received_at,
        },
    )
