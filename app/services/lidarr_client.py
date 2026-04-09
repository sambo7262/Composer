from __future__ import annotations

import asyncio

from pyarr import Lidarr


async def test_lidarr_connection(url: str, api_key: str) -> dict:
    """Test Lidarr connectivity and return quality profiles."""
    try:
        lidarr = Lidarr(host_url=url, api_key=api_key)
        profiles = await asyncio.to_thread(lidarr.get_quality_profile)
        return {
            "success": True,
            "profiles": [{"id": p["id"], "name": p["name"]} for p in profiles],
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
