"""Phase 7 Plan 03 — Suggestions queue endpoints.

POST /api/suggestions/{rating_key}/dismiss   — D-10 / SUGG-09 user-dismiss action.

The dismiss endpoint awaits handle_dismiss_track (Plan 02), which writes
both a hard_track and a hard_artist NegativeSignal AND drains the row from
the SuggestionsMirror. The endpoint returns an empty HTML body with status
200; the calling row's hx-swap='outerHTML' replaces the row with nothing.

Best-effort error handling: if Plan 02 raises, we log and still return 200
so the user-facing row is removed (better than leaving a stuck row that the
user can't re-tap to re-dismiss).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


@router.post("/{rating_key}/dismiss", response_class=HTMLResponse)
async def dismiss_track(rating_key: str, request: Request) -> HTMLResponse:
    """D-10 / SUGG-09 — user dismisses a suggestion from the expanded panel.

    Calls handle_dismiss_track (Plan 02) which writes hard_track +
    hard_artist NegativeSignal rows AND drains the mirror.

    Returns: empty HTML body, status 200. The HTMX outerHTML swap on the
    row replaces the row element with nothing, so the visual effect is
    immediate row removal.
    """
    # Lazy import so the test fixtures can monkeypatch the symbol on the
    # module without circular-import races at app startup.
    from app.services import suggestions_service
    try:
        await suggestions_service.handle_dismiss_track(rating_key)
    except Exception:
        # Best-effort — never block the user from removing a row even if
        # the underlying handler raises. Logged so /debug/suggestions can
        # surface the error pattern over time.
        logger.exception(
            "dismiss_track: handle_dismiss_track raised for rating_key=%s",
            rating_key,
        )
    return HTMLResponse(content="", status_code=200)
