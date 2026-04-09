from __future__ import annotations

import asyncio

from plexapi.server import PlexServer


def _map_track(t) -> dict:
    """Map a PlexAPI Track object to a dict with standard field names."""
    # Extract file path from media parts (D-12)
    file_path = None
    try:
        if hasattr(t, "media") and t.media:
            parts = t.media[0].parts
            if parts:
                file_path = parts[0].file
    except (IndexError, AttributeError):
        pass

    return {
        "plex_rating_key": str(t.ratingKey),
        "title": t.title or "",
        "artist": t.grandparentTitle or "",
        "album": t.parentTitle or "",
        "genre": ", ".join(g.tag for g in (t.genres or [])),
        "year": t.year,
        "duration_ms": t.duration or 0,
        "added_at": t.addedAt.isoformat() if t.addedAt else None,
        "updated_at": t.updatedAt.isoformat() if t.updatedAt else None,
        "file_path": file_path,
    }


async def get_library_tracks(
    url: str,
    token: str,
    library_id: str,
    container_start: int = 0,
    container_size: int = 200,
) -> tuple[list[dict], int]:
    """Fetch tracks from a Plex music library in paginated batches.

    Returns (tracks_list, total_count).
    """
    plex = await asyncio.to_thread(PlexServer, url, token, timeout=30)
    section = await asyncio.to_thread(lambda: plex.library.sectionByID(int(library_id)))
    total = section.totalSize
    tracks = await asyncio.to_thread(
        section.searchTracks,
        container_start=container_start,
        container_size=container_size,
    )
    return [_map_track(t) for t in tracks], total


async def get_tracks_since(
    url: str,
    token: str,
    library_id: str,
    since_date_str: str,
) -> tuple[list[dict], int]:
    """Fetch tracks added after a given date for delta sync.

    Returns (tracks_list, count).
    """
    plex = await asyncio.to_thread(PlexServer, url, token, timeout=30)
    section = await asyncio.to_thread(lambda: plex.library.sectionByID(int(library_id)))
    tracks = await asyncio.to_thread(
        section.searchTracks,
        filters={"addedAt>>": since_date_str},
    )
    return [_map_track(t) for t in tracks], len(tracks)


async def test_plex_connection(url: str, token: str) -> dict:
    """Test Plex connection and return server name + music libraries."""
    try:
        plex = await asyncio.to_thread(PlexServer, url, token, timeout=10)
        sections = await asyncio.to_thread(plex.library.sections)
        libraries = [
            {"key": str(s.key), "title": s.title}
            for s in sections
            if s.type == "artist"
        ]
        return {
            "success": True,
            "server_name": plex.friendlyName,
            "libraries": libraries,
        }
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            return {
                "success": False,
                "error": "Authentication failed. Double-check your token or API key.",
            }
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            return {
                "success": False,
                "error": "Connection timed out. Make sure the service is running and accessible from this container.",
            }
        return {
            "success": False,
            "error": "Could not connect. Check the URL and credentials, then try again.",
        }
