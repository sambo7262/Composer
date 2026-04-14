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
CRITERIA: energy=LOW-HIGH tempo=LOW-HIGH dance=LOW-HIGH valence=LOW-HIGH genres=GENRE1,GENRE2 exclude=GENRE1,GENRE2 artists=ARTIST1,ARTIST2

Where:
- energy: 0.0 (calm) to 1.0 (intense)
- tempo: 40 (slow) to 220 (fast) in BPM
- dance: 0.0 (not danceable) to 1.0 (very danceable)
- valence: 0.0 (sad/dark) to 1.0 (happy/bright)
- genres: comma-separated genre names to include (or "any" for all)
- exclude: comma-separated genres to exclude (or "none")
- artists: comma-separated artist names the user specifically requested (or "any")

IMPORTANT: When the user asks to ADD a specific artist or band, use WIDE feature ranges (energy=0.0-1.0 etc.) so the search can find that artist's tracks regardless of their audio profile. Only narrow the ranges when the user describes a specific mood.

PART 2 - After the criteria line, write a brief friendly explanation of how you interpreted their mood.

Example response:
CRITERIA: energy=0.3-0.6 tempo=70-110 dance=0.2-0.5 valence=0.4-0.7 genres=jazz,soul,r&b exclude=metal,punk artists=any
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


REFINEMENT_PROMPT = """The user has an existing playlist and wants to modify it.

Current playlist:
{current_playlist}

User's request: {user_request}

Respond with an ACTION line on the FIRST line, then a brief explanation.

Actions:
- ADD: find new tracks to add. Format: ADD: artists=ARTIST1,ARTIST2 count=N
- REMOVE: remove tracks by artist or description. Format: REMOVE: artists=ARTIST1,ARTIST2
- REPLACE: swap some tracks. Format: REPLACE: remove_artists=ARTIST1 add_artists=ARTIST2 count=N
- REBUILD: start over with new criteria. Format: REBUILD

Example:
ADD: artists=Jungle,Tycho count=5
Adding 3 Jungle tracks and 2 Tycho tracks to complement the existing dreamy vibe."""


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
    artists = _parse_list("artists")

    # Extract explanation (everything after the CRITERIA line)
    explanation = text[criteria_match.end():].strip()
    if not explanation:
        explanation = "Based on your mood description."

    return FeatureCriteria(
        energy_min=e_min, energy_max=e_max,
        tempo_min=t_min, tempo_max=t_max,
        danceability_min=d_min, danceability_max=d_max,
        valence_min=v_min, valence_max=v_max,
        genres=genres, artists=artists, exclude_genres=excludes,
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

    # Check if this is a refinement of an existing playlist
    if chat_session.current_playlist:
        from sqlmodel import select
        current_tracks_db = db_session.exec(
            select(Track).where(Track.id.in_(chat_session.current_playlist))  # type: ignore[union-attr]
        ).all()
        track_map_current = {t.id: t for t in current_tracks_db}
        current_summary = "\n".join(
            f"- {track_map_current[tid].artist} - {track_map_current[tid].title}"
            for tid in chat_session.current_playlist if tid in track_map_current
        )

        # Ask LLM to classify the refinement intent
        try:
            refinement_prompt = REFINEMENT_PROMPT.format(
                current_playlist=current_summary,
                user_request=user_message,
            )
            action_text = await chat_completion(
                api_key=api_key,
                model=model_name,
                system="You classify playlist modification requests. Always respond with an ACTION line first.",
                messages=[{"role": "user", "content": refinement_prompt}],
                max_tokens=200,
            )
            logger.info("Refinement classification: %s", action_text[:100])

            action_line = action_text.split("\n")[0].strip().upper()

            # Handle ADD action — keep existing playlist, search for new tracks only
            if action_line.startswith("ADD:"):
                add_match = re.search(r'artists?=([\w\s,&\']+?)(?:\s+count=(\d+)|\s*$)', action_text, re.IGNORECASE)
                add_artists = []
                add_count = 5
                if add_match:
                    add_artists = [a.strip() for a in add_match.group(1).split(",") if a.strip()]
                    if add_match.group(2):
                        add_count = int(add_match.group(2))

                # Search for tracks from the requested artists
                all_tracks_db = db_session.exec(select(Track)).all()
                existing_ids = set(chat_session.current_playlist)
                new_candidates = []
                for track in all_tracks_db:
                    if track.id in existing_ids:
                        continue
                    track_artist = (track.artist or "").lower()
                    if add_artists and any(a.lower() in track_artist for a in add_artists):
                        new_candidates.append(track)

                # Apply dedup and album cap
                seen = set()
                deduped_new = []
                album_counts = {}
                for t in new_candidates:
                    title_key = (t.title.lower().strip(), t.artist.lower().strip())
                    album_key = ((t.album or "").lower().strip(), t.artist.lower().strip())
                    if title_key in seen:
                        continue
                    if album_counts.get(album_key, 0) >= 3:
                        continue
                    seen.add(title_key)
                    album_counts[album_key] = album_counts.get(album_key, 0) + 1
                    deduped_new.append(t)

                # Limit per artist based on request
                artist_alloc = {}
                if add_artists:
                    # Try to distribute count across requested artists
                    per_artist = max(1, add_count // len(add_artists))
                    for a in add_artists:
                        artist_alloc[a.lower()] = per_artist

                final_new = []
                artist_used = {}
                for t in deduped_new:
                    t_artist = (t.artist or "").lower()
                    for a_name, a_limit in artist_alloc.items():
                        if a_name in t_artist:
                            if artist_used.get(a_name, 0) < a_limit:
                                final_new.append(t)
                                artist_used[a_name] = artist_used.get(a_name, 0) + 1
                            break
                    if len(final_new) >= add_count:
                        break

                # Combine existing + new
                existing_tracks = [track_map_current[tid] for tid in chat_session.current_playlist if tid in track_map_current]
                all_playlist_tracks = existing_tracks + final_new
                chat_session.current_playlist = [t.id for t in all_playlist_tracks]

                new_names = ", ".join(f"{t.artist} - {t.title}" for t in final_new[:5])
                explanation_text = action_text.split("\n", 1)[1].strip() if "\n" in action_text else ""
                explanation = f"Added {len(final_new)} tracks: {new_names}\n\n{explanation_text}" if final_new else "Couldn't find matching tracks to add."
                chat_session.messages.append({"role": "assistant", "content": explanation})

                return {
                    "tracks": all_playlist_tracks,
                    "explanation": explanation,
                    "criteria": None,
                    "session_id": chat_session.session_id,
                }

            elif action_line.startswith("REMOVE:"):
                remove_match = re.search(r'artists?=([\w\s,&\']+)', action_text, re.IGNORECASE)
                if remove_match:
                    remove_artists = [a.strip().lower() for a in remove_match.group(1).split(",")]
                    kept = [tid for tid in chat_session.current_playlist
                            if tid in track_map_current and
                            not any(a in (track_map_current[tid].artist or "").lower() for a in remove_artists)]
                    removed_count = len(chat_session.current_playlist) - len(kept)
                    chat_session.current_playlist = kept
                    kept_tracks = [track_map_current[tid] for tid in kept if tid in track_map_current]
                    explanation = f"Removed {removed_count} tracks from {', '.join(remove_artists)}."
                    chat_session.messages.append({"role": "assistant", "content": explanation})
                    return {
                        "tracks": kept_tracks,
                        "explanation": explanation,
                        "criteria": None,
                        "session_id": chat_session.session_id,
                    }

            # REBUILD or unrecognized action — fall through to full pipeline
            logger.info("Refinement action: REBUILD or unrecognized, running full pipeline")

        except Exception as exc:
            logger.warning("Refinement classification failed, falling back to full pipeline: %s", str(exc)[:200])

    # Phase 1: Mood interpretation via Anthropic (full pipeline - new playlist or rebuild)
    playlist_context = ""
    try:
        messages = chat_session.messages[-6:]  # Keep last 3 exchanges

        llm_text = await chat_completion(
            api_key=api_key,
            model=model_name,
            system=SYSTEM_PROMPT + playlist_context,
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

    # Filter out very short tracks (interludes, intros, skits < 60 seconds)
    MIN_DURATION_MS = 60000  # 1 minute
    candidates = [
        (track, score) for track, score in candidates
        if (track.duration_ms or 0) >= MIN_DURATION_MS
    ]

    # Filter out live tracks if requested
    if exclude_live:
        live_keywords = ["live", "concert", "unplugged", "acoustic live", "live at", "live in", "live from"]
        candidates = [
            (track, score) for track, score in candidates
            if not any(kw in (track.title or "").lower() or kw in (track.album or "").lower() for kw in live_keywords)
        ]
        logger.info("After live track filter: %d candidates", len(candidates))

    # Deduplicate: keep best-scored version of each title+artist combo
    seen_titles = {}
    deduped = []
    for track, score in candidates:
        key = (track.title.lower().strip(), track.artist.lower().strip())
        if key not in seen_titles:
            seen_titles[key] = True
            deduped.append((track, score))
    if len(deduped) < len(candidates):
        logger.info("Deduplication removed %d duplicate tracks", len(candidates) - len(deduped))
    candidates = deduped

    # Album diversity: limit max tracks per album to avoid flooding from one release
    MAX_PER_ALBUM = 3
    album_counts = {}
    diverse = []
    for track, score in candidates:
        album_key = ((track.album or "unknown").lower().strip(), (track.artist or "").lower().strip())
        count = album_counts.get(album_key, 0)
        if count < MAX_PER_ALBUM:
            diverse.append((track, score))
            album_counts[album_key] = count + 1
    if len(diverse) < len(candidates):
        logger.info("Album diversity filter removed %d tracks (max %d per album)", len(candidates) - len(diverse), MAX_PER_ALBUM)
    candidates = diverse

    # Artist diversity: cap tracks per artist to ensure variety
    # Allow more from requested artists, fewer from others
    requested_artists_lower = [a.lower() for a in (criteria.artists or [])]
    MAX_PER_REQUESTED_ARTIST = max(5, track_count // 5)  # ~5 for 30 tracks
    MAX_PER_OTHER_ARTIST = max(3, track_count // 8)  # ~3-4 for 30 tracks
    artist_counts = {}
    artist_diverse = []
    for track, score in candidates:
        artist_key = (track.artist or "unknown").lower().strip()
        count = artist_counts.get(artist_key, 0)
        is_requested = any(a in artist_key for a in requested_artists_lower) if requested_artists_lower else False
        cap = MAX_PER_REQUESTED_ARTIST if is_requested else MAX_PER_OTHER_ARTIST
        if count < cap:
            artist_diverse.append((track, score))
            artist_counts[artist_key] = count + 1
    if len(artist_diverse) < len(candidates):
        logger.info("Artist diversity filter removed %d tracks (requested cap=%d, other cap=%d)",
                     len(candidates) - len(artist_diverse), MAX_PER_REQUESTED_ARTIST, MAX_PER_OTHER_ARTIST)
    candidates = artist_diverse

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
