"""Chat service: orchestrates the two-phase LLM pipeline for playlist generation.

Phase 1: Instructor interprets mood description into FeatureCriteria (PLAY-02)
Phase 2: Playlist engine filters/scores candidates from library
Phase 3: Instructor curates final track selection from candidates

Session state tracks conversation history and current playlist (D-08, D-13).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session

from app.models.schemas import FeatureCriteria, TrackSelection
from app.models.track import Track
from app.services.ollama_client import get_instructor_client
from app.services.playlist_engine import filter_candidates, format_candidates_for_llm

logger = logging.getLogger(__name__)

# Module-level session store (singleton pattern, consistent with sync_service/analysis_service)
_sessions: dict[str, ChatSession] = {}

# System prompt for mood interpretation (<500 tokens per research guidance)
SYSTEM_PROMPT = """You are a music playlist curator. You interpret mood and vibe descriptions into audio feature criteria to search a personal music library.

Audio feature ranges:
- energy: 0.0 (calm, ambient) to 1.0 (intense, aggressive)
- tempo: 40 BPM (very slow) to 220 BPM (very fast)
- danceability: 0.0 (not danceable) to 1.0 (very danceable)
- valence: 0.0 (sad, dark, melancholy) to 1.0 (happy, bright, uplifting)

You can also filter by genre names and artist names. Use exclude_genres to remove unwanted styles.

When interpreting a mood, set appropriate min/max ranges for each feature. Use wider ranges for vague requests and narrower ranges for specific ones. Always provide a brief explanation of your interpretation."""

CURATION_PROMPT_TEMPLATE = """You are selecting tracks for a playlist from the candidates below. Pick exactly {track_count} tracks that best match the mood and order them for a cohesive listening experience.

Current request context: {context}

Candidates (ID|Title|Artist|Genre|Energy|Tempo|Dance|Valence):
{candidates}

Select exactly {track_count} tracks by their ID. If fewer candidates are available, select all of them. Order them for a smooth listening flow. Explain your selections briefly."""


@dataclass
class ChatSession:
    """In-memory session state for a chat conversation."""

    session_id: str
    messages: list[dict] = field(default_factory=list)
    current_playlist: list[int] = field(default_factory=list)
    track_count: int = 20


def get_or_create_session(session_id: Optional[str] = None) -> ChatSession:
    """Get an existing session or create a new one.

    Args:
        session_id: Existing session ID. If None or not found, creates new.

    Returns:
        ChatSession instance.
    """
    if session_id and session_id in _sessions:
        return _sessions[session_id]

    new_id = session_id or str(uuid.uuid4())
    session = ChatSession(session_id=new_id)
    _sessions[new_id] = session
    return session


def clear_session(session_id: str) -> None:
    """Remove a session from the store."""
    _sessions.pop(session_id, None)


def _sanitize_error(error_msg: str) -> str:
    """Sanitize error messages to prevent leaking sensitive info (T-04-05).

    Strips URLs, tokens, file paths, and other potentially sensitive data.
    """
    # Remove anything that looks like a URL with credentials
    sanitized = re.sub(r"https?://[^\s]+", "[url-redacted]", error_msg)
    # Remove file paths
    sanitized = re.sub(r"/[a-zA-Z0-9_/.-]+\.(py|db|sqlite|conf|env)", "[path-redacted]", sanitized)
    # Truncate to reasonable length
    return sanitized[:300]


async def process_message(
    session_id: str,
    user_message: str,
    track_count: int,
    db_session: Session,
) -> dict:
    """Process a chat message through the two-phase LLM pipeline.

    Phase 1: Mood interpretation -> FeatureCriteria (via Instructor)
    Phase 2: Candidate filtering (via playlist_engine)
    Phase 3: LLM curation -> TrackSelection (via Instructor)

    Args:
        session_id: Chat session identifier.
        user_message: The user's message text.
        track_count: Number of tracks to include in playlist.
        db_session: Database session for queries.

    Returns:
        Dict with keys: tracks, explanation, criteria, session_id, error (if any).
    """
    chat_session = get_or_create_session(session_id)
    chat_session.track_count = track_count

    # Append user message to conversation history
    chat_session.messages.append({"role": "user", "content": user_message})

    # Check for Plex playlist request (D-10 Flow 2 -- deferred to Phase 5)
    plex_keywords = ["existing playlist", "plex playlist", "modify playlist", "edit playlist"]
    if any(kw in user_message.lower() for kw in plex_keywords):
        decline_msg = (
            "I can't modify existing Plex playlists yet, but I can create a new playlist "
            "with a similar vibe. What mood are you going for?"
        )
        chat_session.messages.append({"role": "assistant", "content": decline_msg})
        return {
            "tracks": [],
            "explanation": decline_msg,
            "criteria": None,
            "session_id": chat_session.session_id,
        }

    try:
        instructor_client, model_name = get_instructor_client(db_session)
    except ValueError as exc:
        error_msg = str(exc)
        chat_session.messages.append({"role": "assistant", "content": error_msg})
        return {
            "tracks": [],
            "explanation": error_msg,
            "criteria": None,
            "session_id": chat_session.session_id,
            "error": True,
        }

    # Build context for refinement messages (D-13 smart edits)
    playlist_context = ""
    if chat_session.current_playlist:
        playlist_context = (
            f"\n\nCurrent playlist has {len(chat_session.current_playlist)} tracks "
            f"(IDs: {chat_session.current_playlist[:10]}{'...' if len(chat_session.current_playlist) > 10 else ''})."
            f" The user may want to modify this playlist."
        )

    # Phase 1: Mood interpretation via Instructor
    try:
        interpretation_messages = [
            {"role": "system", "content": SYSTEM_PROMPT + playlist_context},
            *chat_session.messages,
        ]

        criteria: FeatureCriteria = await asyncio.to_thread(
            instructor_client.chat.completions.create,
            model=model_name,
            response_model=FeatureCriteria,
            messages=interpretation_messages,
            max_retries=2,
        )
        logger.info(
            "Mood interpreted: energy=[%.1f,%.1f] tempo=[%.0f,%.0f] valence=[%.1f,%.1f] genres=%s",
            criteria.energy_min, criteria.energy_max,
            criteria.tempo_min, criteria.tempo_max,
            criteria.valence_min, criteria.valence_max,
            criteria.genres,
        )
    except Exception as exc:
        error_msg = _sanitize_error(str(exc))
        friendly = f"I had trouble understanding that mood. Could you try describing it differently? (Error: {error_msg})"
        chat_session.messages.append({"role": "assistant", "content": friendly})
        logger.error("Phase 1 (mood interpretation) failed: %s", error_msg)
        return {
            "tracks": [],
            "explanation": friendly,
            "criteria": None,
            "session_id": chat_session.session_id,
            "error": True,
        }

    # Phase 2: Candidate filtering via playlist engine
    candidates = filter_candidates(
        db_session, criteria, track_count=track_count, candidate_limit=300
    )

    if not candidates:
        no_tracks_msg = (
            f"I interpreted your mood as: {criteria.explanation}\n\n"
            "Unfortunately, I couldn't find any matching tracks in your library. "
            "Try a broader mood description or check that your library has been synced and analyzed."
        )
        chat_session.messages.append({"role": "assistant", "content": no_tracks_msg})
        return {
            "tracks": [],
            "explanation": no_tracks_msg,
            "criteria": criteria,
            "session_id": chat_session.session_id,
        }

    # Check if enough high-quality matches
    good_matches = sum(1 for _, score in candidates if score <= 0.5)
    low_quality_warning = ""
    if good_matches < track_count * 0.5:
        low_quality_warning = (
            " Note: Your library has limited tracks matching this exact mood. "
            "Some selections may be approximate matches."
        )

    # Phase 3: LLM curation via Instructor
    candidates_text = format_candidates_for_llm(candidates, limit=300)
    curation_prompt = CURATION_PROMPT_TEMPLATE.format(
        track_count=min(track_count, len(candidates)),
        context=criteria.explanation,
        candidates=candidates_text,
    )

    try:
        curation_messages = [
            {"role": "system", "content": "You are a playlist curator selecting and ordering tracks."},
            {"role": "user", "content": curation_prompt},
        ]

        selection: TrackSelection = await asyncio.to_thread(
            instructor_client.chat.completions.create,
            model=model_name,
            response_model=TrackSelection,
            messages=curation_messages,
            max_retries=2,
        )
    except Exception as exc:
        error_msg = _sanitize_error(str(exc))
        friendly = (
            f"I found {len(candidates)} matching tracks but had trouble curating the final playlist. "
            f"(Error: {error_msg})"
        )
        chat_session.messages.append({"role": "assistant", "content": friendly})
        logger.error("Phase 3 (curation) failed: %s", error_msg)
        return {
            "tracks": [],
            "explanation": friendly,
            "criteria": criteria,
            "session_id": chat_session.session_id,
            "error": True,
        }

    # Validate track IDs (T-04-03: prevent LLM hallucination attacks)
    valid_candidate_ids = {track.id for track, _ in candidates}
    validated_ids = [tid for tid in selection.track_ids if tid in valid_candidate_ids]

    if len(validated_ids) < len(selection.track_ids):
        removed = len(selection.track_ids) - len(validated_ids)
        logger.warning("Removed %d invalid track IDs from LLM selection", removed)

    # Update session state
    chat_session.current_playlist = validated_ids

    # Build explanation
    explanation = f"{criteria.explanation}\n\n{selection.explanation}{low_quality_warning}"
    chat_session.messages.append({"role": "assistant", "content": explanation})

    # Fetch full Track objects in playlist order
    candidate_map: dict[int, Track] = {track.id: track for track, _ in candidates}
    ordered_tracks = [candidate_map[tid] for tid in validated_ids if tid in candidate_map]

    return {
        "tracks": ordered_tracks,
        "explanation": explanation,
        "criteria": criteria,
        "session_id": chat_session.session_id,
    }
