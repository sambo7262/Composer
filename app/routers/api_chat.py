"""Chat API router: handles chat messages, track management, and session control.

Returns HTML partials for HTMX consumption. Templates are created in Plan 03.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session

from app.database import get_session
from app.services.chat_service import (
    clear_session,
    get_or_create_session,
    process_message,
    push_playlist_to_plex,
    save_playlist_to_history,
)
from app.services.settings_service import get_setting, get_decrypted_credential

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


def _validate_session_id(session_id: str | None) -> str:
    """Validate session_id is a reasonable UUID format or generate one (T-04-04)."""
    if not session_id:
        return str(uuid.uuid4())
    # Basic format check -- allow UUID-like strings
    cleaned = session_id.strip()
    if len(cleaned) > 64 or not cleaned.replace("-", "").replace("_", "").isalnum():
        return str(uuid.uuid4())
    return cleaned


def _validate_track_id(track_id: int) -> int | None:
    """Validate track_id is a positive integer (T-04-04)."""
    if track_id is not None and track_id > 0:
        return track_id
    return None


@router.post("/message", response_class=HTMLResponse)
async def chat_message(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(default=""),
    track_count: int = Form(default=20),
    db_session: Session = Depends(get_session),
):
    """Process a chat message and return HTML partials for HTMX swap.

    Returns two concatenated partials:
    1. User message bubble (role=user)
    2. AI response bubble (role=assistant) with optional playlist card
    """
    templates = get_templates()
    validated_session_id = _validate_session_id(session_id)

    # Clamp track_count to reasonable range
    track_count = max(1, min(track_count, 100))

    try:
        result = await process_message(
            session_id=validated_session_id,
            user_message=message,
            track_count=track_count,
            db_session=db_session,
        )
    except Exception as exc:
        logger.error("Chat message processing failed: %s", str(exc)[:200])
        result = {
            "tracks": [],
            "explanation": "Something went wrong. Please try again.",
            "criteria": None,
            "session_id": validated_session_id,
            "error": True,
        }

    # Render user message partial
    user_html = templates.get_template("partials/chat_message.html").render(
        role="user",
        content=message,
    )

    # Render assistant response partial
    assistant_html = templates.get_template("partials/chat_message.html").render(
        role="assistant",
        content=result.get("explanation", ""),
        tracks=result.get("tracks", []),
        session_id=result.get("session_id", validated_session_id),
        has_error=result.get("error", False),
    )

    return HTMLResponse(content=user_html + assistant_html)


@router.post("/remove-track", response_class=HTMLResponse)
async def remove_track(
    session_id: str = Form(...),
    track_id: int = Form(...),
):
    """Remove a track from the current playlist.

    Returns empty string so HTMX hx-swap='outerHTML' removes the <li> from DOM.
    """
    validated_id = _validate_track_id(track_id)
    if validated_id is None:
        return HTMLResponse(content="", status_code=400)

    session = get_or_create_session(_validate_session_id(session_id))
    if validated_id in session.current_playlist:
        session.current_playlist.remove(validated_id)

    return HTMLResponse(content="")


@router.post("/reorder", response_class=Response)
async def reorder_tracks(
    session_id: str = Form(...),
    track_id: int = Form(...),
    position: int = Form(...),
):
    """Move a track to a new position in the playlist.

    Returns 204 No Content -- Alpine.js SortableJS handles DOM reorder client-side.
    """
    validated_id = _validate_track_id(track_id)
    if validated_id is None:
        return Response(status_code=400)

    session = get_or_create_session(_validate_session_id(session_id))

    if validated_id in session.current_playlist:
        session.current_playlist.remove(validated_id)
        # Clamp position to valid range
        pos = max(0, min(position, len(session.current_playlist)))
        session.current_playlist.insert(pos, validated_id)

    return Response(status_code=204)


@router.post("/new", response_class=HTMLResponse)
async def new_conversation(
    request: Request,
    session_id: str = Form(default=""),
):
    """Start a new conversation by clearing the current session.

    Returns empty chat state HTML for HTMX to swap in.
    """
    if session_id:
        clear_session(_validate_session_id(session_id))

    return HTMLResponse(content="")


@router.post("/push-to-plex", response_class=HTMLResponse)
async def push_to_plex(
    request: Request,
    session_id: str = Form(...),
    playlist_name: str = Form(...),
    db_session: Session = Depends(get_session),
):
    """Push the current playlist to Plex and save to history (D-15, D-16, PLAY-06).

    Validates playlist name (T-04-09), creates playlist on Plex via batch fetch,
    saves to history database (D-17), returns success/error message partial.
    """
    templates = get_templates()
    validated_session_id = _validate_session_id(session_id)

    # Validate playlist name (T-04-09)
    name = playlist_name.strip()
    if not name:
        error_html = templates.get_template("partials/chat_message.html").render(
            role="assistant",
            content="Please enter a name for the playlist.",
            has_error=True,
        )
        return HTMLResponse(content=error_html, status_code=400)

    if len(name) > 200:
        error_html = templates.get_template("partials/chat_message.html").render(
            role="assistant",
            content="Playlist name must be under 200 characters.",
            has_error=True,
        )
        return HTMLResponse(content=error_html, status_code=400)

    # Validate session has a playlist
    session = get_or_create_session(validated_session_id)
    if not session.current_playlist:
        error_html = templates.get_template("partials/chat_message.html").render(
            role="assistant",
            content="No playlist to push. Generate a playlist first by describing a mood.",
            has_error=True,
        )
        return HTMLResponse(content=error_html, status_code=400)

    # Get Plex settings
    plex_setting = get_setting(db_session, "plex")
    if not plex_setting or not plex_setting.is_configured:
        error_html = templates.get_template("partials/chat_message.html").render(
            role="assistant",
            content="Plex is not configured. Go to Settings to set up your Plex connection.",
            has_error=True,
        )
        return HTMLResponse(content=error_html, status_code=400)

    plex_url = plex_setting.url
    plex_token = get_decrypted_credential(db_session, "plex")
    if not plex_token:
        error_html = templates.get_template("partials/chat_message.html").render(
            role="assistant",
            content="Plex token not found. Please reconfigure Plex in Settings.",
            has_error=True,
        )
        return HTMLResponse(content=error_html, status_code=400)

    # Get rating keys for tracks in the playlist
    from sqlmodel import select
    from app.models.track import Track

    tracks = db_session.exec(
        select(Track).where(Track.id.in_(session.current_playlist))  # type: ignore[union-attr]
    ).all()
    track_map = {t.id: t for t in tracks}
    rating_keys = [
        track_map[tid].plex_rating_key
        for tid in session.current_playlist
        if tid in track_map
    ]

    if not rating_keys:
        error_html = templates.get_template("partials/chat_message.html").render(
            role="assistant",
            content="Could not find tracks in the library. Try generating a new playlist.",
            has_error=True,
        )
        return HTMLResponse(content=error_html, status_code=400)

    # Push to Plex
    try:
        result = await push_playlist_to_plex(plex_url, plex_token, name, rating_keys)
    except Exception as exc:
        # Sanitize error to prevent token leaks (T-04-08)
        import re
        error_msg = str(exc)
        error_msg = re.sub(r"https?://[^\s]+", "[url-redacted]", error_msg)
        error_msg = re.sub(r"X-Plex-Token=[^\s&]+", "[token-redacted]", error_msg)
        logger.error("Push to Plex failed: %s", error_msg[:200])

        error_html = templates.get_template("partials/chat_message.html").render(
            role="assistant",
            content=f"Failed to push playlist to Plex. Please check your Plex connection and try again.",
            has_error=True,
        )
        return HTMLResponse(content=error_html, status_code=500)

    # Save to history (D-17)
    mood_description = ""
    if session.messages:
        for msg in session.messages:
            if msg.get("role") == "user":
                mood_description = msg.get("content", "")
                break

    try:
        save_playlist_to_history(
            db_session=db_session,
            name=name,
            mood_description=mood_description,
            track_ids=session.current_playlist,
        )
    except Exception as exc:
        logger.warning("Failed to save playlist history: %s", str(exc)[:200])
        # Don't fail the push -- Plex playlist was already created successfully

    # Success message (D-16) -- conversation continues
    track_count = result.get("track_count", len(rating_keys))
    success_html = templates.get_template("partials/chat_message.html").render(
        role="assistant",
        content=f"Playlist '{name}' pushed to Plex with {track_count} tracks! You can keep refining or start a new playlist.",
    )
    return HTMLResponse(content=success_html)
