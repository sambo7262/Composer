from __future__ import annotations

import asyncio
from typing import Optional

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

    # Phase 5 additions (D-15 + Pitfall 5): defensive getattr because PlexAPI
    # partial responses may omit these attributes entirely. user_rating is RAW
    # 0-10 — display conversion happens via app.services.rating_helpers only.
    last_viewed = getattr(t, "lastViewedAt", None)
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
        "user_rating": getattr(t, "userRating", None),
        "last_viewed_at": last_viewed.isoformat() if last_viewed else None,
        "view_count": getattr(t, "viewCount", 0) or 0,
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
    # Plex expects date only (YYYY-MM-DD), not full ISO timestamp
    date_only = since_date_str[:10]
    tracks = await asyncio.to_thread(
        section.searchTracks,
        filters={"addedAt>>": date_only},
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


def get_artist_mbid_by_name(name: str) -> Optional[str]:
    """Phase 8 Pitfall 12 — sync helper to fetch a Plex artist's MBID by name.

    Used by ``discovery_service._backfill_track_artist_mbids_sync`` during the
    Phase 8 lifespan bootstrap. The caller wraps invocation in
    ``asyncio.to_thread`` so PlexAPI's sync calls don't block the event loop.

    Plex stores MusicBrainz artist identifiers in either:
      - the ``.guid`` field as ``"mbid://<uuid>"``, or
      - the ``.guids`` collection (multiple identifiers) as
        ``[Guid(id='mbid://<uuid>'), Guid(id='spotify:...'), ...]``.
    We accept both shapes and extract the 36-char UUID. Returns ``None`` if
    Plex is unreachable, the artist is not found, or no MBID is on file.

    NOTE: connection state is read from ``ServiceConfig`` settings; the helper
    is intentionally credentials-aware so the lifespan bootstrap doesn't have
    to plumb url/token through every call site.
    """
    import re

    try:
        from sqlmodel import Session

        from app.database import get_engine
        from app.services.settings_service import (
            get_decrypted_credential, get_setting,
        )

        with Session(get_engine()) as s:
            setting = get_setting(s, "plex")
            if setting is None or not setting.url:
                return None
            token = get_decrypted_credential(s, "plex") or ""
            if not token:
                return None
            extras = setting.extra_config or {}
            library_id = extras.get("library_id")
        if not library_id:
            return None

        plex = PlexServer(setting.url, token, timeout=15)
        # PlexAPI: section.searchArtists(title=...) returns a list of Artist objects.
        section = plex.library.sectionByID(int(library_id))
        artists = section.searchArtists(title=name)
        if not artists:
            return None
        artist = artists[0]

        # Try .guid first (singular), then .guids collection.
        mbid_re = re.compile(
            r"mbid://([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
        )
        candidate_strings = []
        single = getattr(artist, "guid", None)
        if single:
            candidate_strings.append(str(single))
        multi = getattr(artist, "guids", None) or []
        for g in multi:
            candidate_strings.append(str(getattr(g, "id", g)))
        for s_value in candidate_strings:
            m = mbid_re.search(s_value)
            if m:
                return m.group(1)
        return None
    except Exception:
        # Best-effort lookup: bootstrap caller logs the failure and proceeds.
        return None
