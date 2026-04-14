"""Chat service: orchestrates the LLM pipeline for playlist generation.

Uses plain text LLM prompts (no Instructor/JSON mode) for fast inference on
low-power hardware. Parses structured data from LLM responses with regex.

Session state tracks conversation history and current playlist (D-08, D-13).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session

from plexapi.server import PlexServer

from app.models.playlist import Playlist, PlaylistTrack
from app.models.schemas import FeatureCriteria
from app.models.track import Track
from app.services.llm_client import get_anthropic_client, chat_completion
from app.services.playlist_engine import filter_candidates, format_candidates_for_llm
from app.services.settings_service import get_setting, get_decrypted_credential

logger = logging.getLogger(__name__)

# Module-level session store
_sessions: dict[str, ChatSession] = {}

# System prompt — plain text, asks for a simple format
SYSTEM_PROMPT = """You are a music playlist curator. You interpret mood and vibe descriptions to search a personal music library.

When the user describes a mood, respond with TWO parts:

PART 1 - On the FIRST line, write a criteria line in this exact format:
CRITERIA: energy=LOW-HIGH tempo=LOW-HIGH dance=LOW-HIGH valence=LOW-HIGH genres=GENRE1,GENRE2 exclude=GENRE1,GENRE2

Where:
- energy: 0.0 (calm) to 1.0 (intense)
- tempo: 40 (slow) to 220 (fast) in BPM
- dance: 0.0 (not danceable) to 1.0 (very danceable)
- valence: 0.0 (sad/dark) to 1.0 (happy/bright)
- genres: comma-separated genre names to include (or "any" for all)
- exclude: comma-separated genres to exclude (or "none")

PART 2 - After the criteria line, write a brief friendly explanation of how you interpreted their mood.

Example response:
CRITERIA: energy=0.3-0.6 tempo=70-110 dance=0.2-0.5 valence=0.4-0.7 genres=jazz,soul,r&b exclude=metal,punk
I'm looking for mellow, warm tracks with a relaxed groove — think Sunday morning coffee vibes with some soul and jazz."""

CURATION_PROMPT = """Pick exactly {track_count} tracks from this list for a "{context}" playlist.

Candidates (ID|Title|Artist|Genre):
{candidates}

IMPORTANT: Your response MUST start with a PICKS line. No text before it.
Format: PICKS: 123,456,789,...

Then after the PICKS line, write a brief 2-3 sentence explanation of the overall mood and flow.
Do NOT reference track IDs or numbers in your explanation — use artist names and song titles instead.

Example:
PICKS: 42,17,89,203,55
This playlist flows from mellow Radiohead deep cuts into upbeat Tame Impala grooves, building energy gradually for a perfect afternoon session."""


@dataclass
class ChatSession:
    """In-memory session state for a chat conversation."""
    session_id: str
    messages: list[dict] = field(default_factory=list)
    current_playlist: list[int] = field(default_factory=list)
    track_count: int = 20


def get_or_create_session(session_id: Optional[str] = None) -> ChatSession:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    new_id = session_id or str(uuid.uuid4())
    session = ChatSession(session_id=new_id)
    _sessions[new_id] = session
    return session


def clear_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


    # _get_ollama_client removed — now using Anthropic via llm_client.py


def _parse_criteria(text: str) -> FeatureCriteria:
    """Parse a CRITERIA line from LLM plain text response."""
    # Find the CRITERIA line
    criteria_match = re.search(r'CRITERIA:\s*(.+)', text, re.IGNORECASE)
    if not criteria_match:
        # Fallback: return broad defaults
        return FeatureCriteria(
            energy_min=0.0, energy_max=1.0,
            tempo_min=60, tempo_max=180,
            danceability_min=0.0, danceability_max=1.0,
            valence_min=0.0, valence_max=1.0,
            genres=[], artists=[], exclude_genres=[],
            explanation=text[:200],
        )

    line = criteria_match.group(1)

    def _parse_range(key: str, default_min: float, default_max: float) -> tuple[float, float]:
        match = re.search(rf'{key}=([\d.]+)-([\d.]+)', line, re.IGNORECASE)
        if match:
            return float(match.group(1)), float(match.group(2))
        return default_min, default_max

    def _parse_list(key: str) -> list[str]:
        match = re.search(rf'{key}=([\w\s,&]+?)(?:\s+\w+=|$)', line, re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            if val.lower() in ("any", "none", ""):
                return []
            return [g.strip() for g in val.split(",") if g.strip()]
        return []

    e_min, e_max = _parse_range("energy", 0.0, 1.0)
    t_min, t_max = _parse_range("tempo", 60, 180)
    d_min, d_max = _parse_range("dance", 0.0, 1.0)
    v_min, v_max = _parse_range("valence", 0.0, 1.0)
    genres = _parse_list("genres")
    excludes = _parse_list("exclude")

    # Extract explanation (everything after the CRITERIA line)
    explanation = text[criteria_match.end():].strip()
    if not explanation:
        explanation = "Based on your mood description."

    return FeatureCriteria(
        energy_min=e_min, energy_max=e_max,
        tempo_min=t_min, tempo_max=t_max,
        danceability_min=d_min, danceability_max=d_max,
        valence_min=v_min, valence_max=v_max,
        genres=genres, artists=[], exclude_genres=excludes,
        explanation=explanation[:300],
    )


def _parse_picks(text: str, valid_ids: set[int]) -> list[int]:
    """Parse a PICKS line from LLM plain text response."""
    picks_match = re.search(r'PICKS:\s*(.+)', text, re.IGNORECASE)
    if not picks_match:
        # Fallback: try to find any numbers in the response
        numbers = re.findall(r'\b(\d+)\b', text)
        ids = [int(n) for n in numbers if int(n) in valid_ids]
        return ids

    ids_str = picks_match.group(1)
    numbers = re.findall(r'\d+', ids_str)
    ids = [int(n) for n in numbers if int(n) in valid_ids]
    return ids


def _sanitize_error(error_msg: str) -> str:
    sanitized = re.sub(r"https?://[^\s]+", "[url-redacted]", error_msg)
    sanitized = re.sub(r"/[a-zA-Z0-9_/.-]+\.(py|db|sqlite|conf|env)", "[path-redacted]", sanitized)
    return sanitized[:300]


async def process_message(
    session_id: str,
    user_message: str,
    track_count: int,
    db_session: Session,
    exclude_live: bool = False,
) -> dict:
    """Process a chat message through the LLM pipeline.

    Phase 1: Plain text mood interpretation -> parse CRITERIA
    Phase 2: Candidate filtering via playlist_engine (with dedup + live filter)
    Phase 3: Plain text LLM curation -> parse PICKS
    """
    chat_session = get_or_create_session(session_id)
    chat_session.track_count = track_count

    chat_session.messages.append({"role": "user", "content": user_message})

    # Check for Plex playlist request (deferred to Phase 5)
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
        api_key, model_name = get_anthropic_client(db_session)
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

    # Phase 1: Mood interpretation via Anthropic
    try:
        messages = chat_session.messages[-6:]  # Keep last 3 exchanges

        llm_text = await chat_completion(
            api_key=api_key,
            model=model_name,
            system=SYSTEM_PROMPT,
            messages=messages,
            max_tokens=300,
        )
        criteria = _parse_criteria(llm_text)
        logger.info(
            "Mood interpreted: energy=[%.1f,%.1f] tempo=[%.0f,%.0f] genres=%s",
            criteria.energy_min, criteria.energy_max,
            criteria.tempo_min, criteria.tempo_max,
            criteria.genres,
        )
    except Exception as exc:
        error_msg = _sanitize_error(str(exc))
        friendly = f"I had trouble understanding that mood. Could you try again? (Error: {error_msg})"
        chat_session.messages.append({"role": "assistant", "content": friendly})
        logger.error("Phase 1 failed: %s", error_msg)
        return {
            "tracks": [],
            "explanation": friendly,
            "criteria": None,
            "session_id": chat_session.session_id,
            "error": True,
        }

    # Phase 2: Candidate filtering
    candidates = filter_candidates(
        db_session, criteria, track_count=track_count, candidate_limit=300
    )
    logger.info("Phase 2: found %d raw candidates from library", len(candidates))

    # Filter out live tracks if requested
    if exclude_live:
        live_keywords = ["live", "concert", "unplugged", "acoustic live", "live at", "live in", "live from"]
        candidates = [
            (track, score) for track, score in candidates
            if not any(kw in (track.title or "").lower() or kw in (track.album or "").lower() for kw in live_keywords)
        ]
        logger.info("After live track filter: %d candidates", len(candidates))

    # Deduplicate: keep best-scored version of each title+artist combo
    seen = {}
    deduped = []
    for track, score in candidates:
        key = (track.title.lower().strip(), track.artist.lower().strip())
        if key not in seen:
            seen[key] = True
            deduped.append((track, score))
    if len(deduped) < len(candidates):
        logger.info("Deduplication removed %d duplicate tracks", len(candidates) - len(deduped))
    candidates = deduped

    if not candidates:
        no_tracks_msg = (
            f"{criteria.explanation}\n\n"
            "I couldn't find matching tracks in your library. "
            "Try a broader mood description."
        )
        chat_session.messages.append({"role": "assistant", "content": no_tracks_msg})
        return {
            "tracks": [],
            "explanation": no_tracks_msg,
            "criteria": criteria,
            "session_id": chat_session.session_id,
        }

    # If we have fewer candidates than requested, just use them all
    if len(candidates) <= track_count:
        validated_ids = [track.id for track, _ in candidates]
        chat_session.current_playlist = validated_ids
        explanation = f"{criteria.explanation}\n\nHere are the {len(validated_ids)} tracks that match."
        chat_session.messages.append({"role": "assistant", "content": explanation})
        candidate_map = {track.id: track for track, _ in candidates}
        ordered_tracks = [candidate_map[tid] for tid in validated_ids if tid in candidate_map]
        return {
            "tracks": ordered_tracks,
            "explanation": explanation,
            "criteria": criteria,
            "session_id": chat_session.session_id,
        }

    # Phase 3: LLM curation — plain text, no JSON mode
    candidates_text = format_candidates_for_llm(candidates, limit=100)
    curation_prompt = CURATION_PROMPT.format(
        track_count=min(track_count, len(candidates)),
        context=criteria.explanation[:100],
        candidates=candidates_text,
    )

    try:
        curation_text = await chat_completion(
            api_key=api_key,
            model=model_name,
            system="You are a playlist curator. Always start your response with PICKS: followed by track IDs. No other text before PICKS.",
            messages=[{"role": "user", "content": curation_prompt}],
            max_tokens=1000,
        )

        logger.info("Phase 3 curation response (first 200 chars): %s", curation_text[:200])

        valid_candidate_ids = {track.id for track, _ in candidates}
        validated_ids = _parse_picks(curation_text, valid_candidate_ids)
        logger.info("Phase 3: parsed %d valid track IDs from LLM picks", len(validated_ids))

        # If LLM picks failed, fall back to top scored candidates
        if len(validated_ids) < 3:
            logger.warning("LLM curation returned too few picks (%d), using top %d scored candidates", len(validated_ids), track_count)
            validated_ids = [track.id for track, _ in candidates[:track_count]]

        # Extract explanation from curation response
        curation_explanation = curation_text
        picks_match = re.search(r'PICKS:', curation_text, re.IGNORECASE)
        if picks_match:
            curation_explanation = curation_text[picks_match.end():].strip()
            # Skip past the IDs line
            curation_explanation = re.sub(r'^[\d,\s]+', '', curation_explanation).strip()

    except Exception as exc:
        error_msg = _sanitize_error(str(exc))
        logger.warning("Phase 3 (curation) failed, using top scored: %s", error_msg)
        validated_ids = [track.id for track, _ in candidates[:track_count]]
        curation_explanation = ""

    # Update session state
    chat_session.current_playlist = validated_ids[:track_count]

    explanation = criteria.explanation
    if curation_explanation:
        explanation += f"\n\n{curation_explanation}"

    chat_session.messages.append({"role": "assistant", "content": explanation})

    candidate_map = {track.id: track for track, _ in candidates}
    ordered_tracks = [candidate_map[tid] for tid in chat_session.current_playlist if tid in candidate_map]

    return {
        "tracks": ordered_tracks,
        "explanation": explanation,
        "criteria": criteria,
        "session_id": chat_session.session_id,
    }


async def push_playlist_to_plex(
    plex_url: str,
    plex_token: str,
    name: str,
    rating_keys: list[str],
) -> dict:
    """Push a playlist to Plex using batch fetch."""
    if not rating_keys:
        raise ValueError("No tracks to push")

    def _create():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        key_str = ",".join(str(k) for k in rating_keys)
        tracks = plex.fetchItems(f"/library/metadata/{key_str}")
        playlist = plex.createPlaylist(title=name, items=tracks)
        return {"success": True, "title": playlist.title, "track_count": len(tracks)}

    return await asyncio.to_thread(_create)


def save_playlist_to_history(
    db_session: Session,
    name: str,
    mood_description: str,
    track_ids: list[int],
) -> Playlist:
    """Save a generated playlist to the history database."""
    playlist = Playlist(
        name=name,
        mood_description=mood_description[:500],
        track_count=len(track_ids),
    )
    db_session.add(playlist)
    db_session.flush()

    for position, track_id in enumerate(track_ids):
        pt = PlaylistTrack(
            playlist_id=playlist.id,
            track_id=track_id,
            position=position,
        )
        db_session.add(pt)

    db_session.commit()
    db_session.refresh(playlist)
    return playlist
