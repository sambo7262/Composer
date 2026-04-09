from __future__ import annotations

import asyncio

from plexapi.server import PlexServer


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
