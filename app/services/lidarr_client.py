from __future__ import annotations

import asyncio
import logging

try:
    from pyarr import Lidarr  # pyarr 6.x — class renamed
except ImportError:  # pragma: no cover
    from pyarr import LidarrAPI as Lidarr  # pyarr 5.x — backwards-compat alias

logger = logging.getLogger(__name__)


def _fetch_lidarr_test_payload(lidarr):
    """Single sync helper so test_lidarr_connection can to_thread ONCE.

    Phase 8 D-E1 — fetches quality + metadata profiles + root folders in a
    single GIL-bound thread + a single round-trip to Lidarr. The three
    pyarr calls each open their own HTTP request internally but they all
    share one threadpool slot, which is the point: fewer event-loop hops.
    """
    return (
        lidarr.get_quality_profile(),
        lidarr.get_metadata_profile(),
        lidarr.get_root_folder(),
    )


async def test_lidarr_connection(url: str, api_key: str) -> dict:
    """Test Lidarr connectivity and return quality + metadata profiles + root folders.

    Phase 8 D-E1 (DISC-07) — extended from quality-only to fetch all three in
    ONE asyncio.to_thread so we hold the GIL once and round-trip the network
    once. Required because pyarr 6.6 add_artist() needs BOTH profile IDs AND
    root_dir (Pitfall 14 — the v1 bug).
    """
    try:
        # Strip trailing slash if present
        url = url.rstrip("/")
        logger.info("Testing Lidarr connection at %s", url)
        lidarr = Lidarr(host_url=url, api_key=api_key)
        quality_profiles, metadata_profiles, root_folders = await asyncio.to_thread(
            _fetch_lidarr_test_payload, lidarr,
        )
        logger.info(
            "Lidarr connection ok: %d quality, %d metadata, %d root",
            len(quality_profiles or []),
            len(metadata_profiles or []),
            len(root_folders or []),
        )
        return {
            "success": True,
            "quality_profiles": [
                {"id": p["id"], "name": p["name"]}
                for p in (quality_profiles or [])
            ],
            "metadata_profiles": [
                {"id": p["id"], "name": p["name"]}
                for p in (metadata_profiles or [])
            ],
            "root_folders": [
                {"id": r["id"], "path": r["path"]}
                for r in (root_folders or [])
            ],
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


async def add_artist(
    mb_id: str,
    url: str,
    api_key: str,
    quality_profile_id: int,
    metadata_profile_id: int,
    root_dir: str,
) -> dict:
    """D-E1 / Pitfall 14 — one-click add. ALL FOUR primary params required.

    Phase 8 D-E1: the v1 bug was missing metadata_profile_id. pyarr 6.6's
    add_artist() signature requires BOTH profile IDs AND a root_dir, and
    will raise (or silently fail to add) without them.

    Returns ``{"success": True, "lidarr_artist_id": int}`` or
    ``{"success": False, "error": str}``.
    """
    try:
        url = url.rstrip("/")
        lidarr = Lidarr(host_url=url, api_key=api_key)
        # 1) Look up by MBID (Lidarr's lookup_artist accepts "mbid:<MBID>" query).
        #    Pitfall 10 — the candidate was already MB-validated upstream, so we
        #    just need the dict shape pyarr.add_artist requires.
        candidates = await asyncio.to_thread(lidarr.lookup_artist, f"mbid:{mb_id}")
        if not candidates:
            return {
                "success": False,
                "error": f"Artist {mb_id} not found via Lidarr lookup.",
            }
        # Prefer the candidate whose foreignArtistId matches the requested MBID;
        # fall back to the top hit. Lidarr returns the canonical match first
        # for an "mbid:<...>" query, so the fallback is rarely needed but
        # guards against API drift.
        artist_dict = next(
            (c for c in candidates if c.get("foreignArtistId") == mb_id),
            candidates[0],
        )
        response = await asyncio.to_thread(
            lidarr.add_artist,
            artist=artist_dict,
            root_dir=root_dir,
            quality_profile_id=quality_profile_id,
            metadata_profile_id=metadata_profile_id,
            monitored=True,
            artist_monitor="all",
            search_for_missing_albums=True,
        )
        return {
            "success": True,
            "lidarr_artist_id": (response or {}).get("id"),
        }
    except Exception as e:
        error_msg = str(e)
        logger.exception("add_artist failed for mb_id=%s", mb_id)
        return {
            "success": False,
            "error": f"Lidarr add failed: {error_msg[:200]}",
        }


async def get_recent_history(
    url: str, api_key: str, page_size: int = 50,
) -> list[dict]:
    """D-C3 — used by /discover lazy-poll (5min cache) + /debug/discovery timeline.

    Best-effort: returns an empty list on any error so the lazy-poll caller
    never blocks the page render. Caller filters by artistId.
    """
    try:
        url = url.rstrip("/")
        lidarr = Lidarr(host_url=url, api_key=api_key)
        result = await asyncio.to_thread(lidarr.get_history, page_size=page_size)
        return (result or {}).get("records", []) or []
    except Exception:
        logger.exception("get_recent_history failed")
        return []
