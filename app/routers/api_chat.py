"""Phase 7 (UI-06) — v1 chat router retired.

The router is kept registered (rather than removed entirely) so that any
external consumer or future migration tooling sees explicit 404 responses
on the documented URL surface, NOT 405 (method-not-allowed) or generic
FastAPI fallthrough. Templates were moved to ``app/templates/_archived/``;
the underlying chat-data tables (``playlist`` etc.) and their model
imports remain intact (UI-06 deferred ideas — chat data archival policy).

Every endpoint returns the same retirement notice as a 404. Function
signatures are kept intentionally simple — the bodies do not parse or
validate the original request shapes; the URL alone signals retirement.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

_RETIRED_BODY = (
    "The chat API was retired in Phase 7. "
    "See /suggestions and /vibes for v2."
)


def _retired() -> HTMLResponse:
    return HTMLResponse(content=_RETIRED_BODY, status_code=404)


@router.post("/message", response_class=HTMLResponse)
async def chat_message() -> HTMLResponse:
    return _retired()


@router.post("/remove-track", response_class=HTMLResponse)
async def remove_track() -> HTMLResponse:
    return _retired()


@router.post("/reorder", response_class=HTMLResponse)
async def reorder_tracks() -> HTMLResponse:
    return _retired()


@router.post("/new", response_class=HTMLResponse)
async def new_conversation() -> HTMLResponse:
    return _retired()


@router.post("/push-to-plex", response_class=HTMLResponse)
async def push_to_plex() -> HTMLResponse:
    return _retired()
