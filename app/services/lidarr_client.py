from __future__ import annotations

import asyncio
import logging

from pyarr import Lidarr

logger = logging.getLogger(__name__)


async def test_lidarr_connection(url: str, api_key: str) -> dict:
    """Test Lidarr connectivity and return quality profiles."""
    try:
        # Strip trailing slash if present
        url = url.rstrip("/")
        logger.info("Testing Lidarr connection at %s", url)
        lidarr = Lidarr(host_url=url, api_key=api_key)
        profiles = await asyncio.to_thread(lidarr.get_quality_profile)
        profile_list = [{"id": p["id"], "name": p["name"]} for p in (profiles or [])]
        logger.info("Lidarr connection successful, found %d quality profiles", len(profile_list))
        return {
            "success": True,
            "profiles": profile_list,
        }
    except Exception as e:
        error_msg = str(e)
        logger.error("Lidarr connection failed: %s", error_msg)
        if "401" in error_msg or "Unauthorized" in error_msg:
            return {
                "success": False,
                "error": "Authentication failed. Double-check your API key.",
            }
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            return {
                "success": False,
                "error": "Connection timed out. Make sure Lidarr is running and accessible.",
            }
        if "connection" in error_msg.lower() or "refused" in error_msg.lower():
            return {
                "success": False,
                "error": f"Connection refused at {url}. Check the URL.",
            }
        return {
            "success": False,
            "error": f"Could not connect: {error_msg[:200]}",
        }
