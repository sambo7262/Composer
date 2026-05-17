"""Phase 8 D-A1 — MusicBrainz API client with persistent cache + rate-limit.

Single sync-library wrap (musicbrainzngs >=0.7.1). All public functions
are async + wrap musicbrainzngs calls in asyncio.to_thread per Phase 5
D-09 (enforced by tests/test_event_handlers.py::test_no_blocking_plexapi_in_async).

Two-layer cache:
- MusicBrainzCache (SQLite) — indefinite, write-through; artists don't change.
- musicbrainzngs internal rate limiter — 1 req/sec self-throttle to honour
  MB's anonymous-IP rate limit (HTTP 503 on throttle).

Module-load side effects:
- ``musicbrainzngs.set_useragent("Composer", "2.0", contact_email)`` —
  required by the MB API rate-limit policy (refusal on unknown UAs).
- ``musicbrainzngs.set_rate_limit(limit_or_interval=1.0)`` — pre-throttle
  every call so we never see HTTP 503s in steady state.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import musicbrainzngs
from sqlmodel import Session, select

from app.database import get_engine
from app.models.discovery import MusicBrainzCache

logger = logging.getLogger(__name__)

# Module-load init — REQUIRED by musicbrainzngs (raises UsageError if missing).
# UA format: "App/version ( contact )" per MB API Rate_Limiting docs.
musicbrainzngs.set_useragent("Composer", "2.0", "sam.e.browning@gmail.com")
# 1 req/sec self-throttle — MB rate limit is 1 req/sec averaged per IP;
# the library handles spacing internally so we never see HTTP 503s in
# steady state.
musicbrainzngs.set_rate_limit(limit_or_interval=1.0)

# Includes hint for /artist/<mbid> — pulls artist-rels (D-A3 adjacency gate),
# release-groups (popularity proxy + shared-label hook), tags (D-A4 LLM hook),
# and ratings (cheap signal).
_MB_LOOKUP_INCLUDES = ["artist-rels", "release-groups", "tags", "ratings"]


def _cache_get_sync(mb_id: str) -> Optional[dict]:
    """Read a previously-cached artist payload from MusicBrainzCache."""
    with Session(get_engine()) as session:
        row = session.exec(
            select(MusicBrainzCache).where(MusicBrainzCache.mb_id == mb_id)
        ).first()
        if row is None:
            return None
        try:
            return json.loads(row.payload_json)
        except json.JSONDecodeError:
            logger.warning(
                "MusicBrainzCache row for mb_id=%s has invalid JSON", mb_id,
            )
            return None


def _cache_put_sync(mb_id: str, payload: dict) -> None:
    """UPSERT the MusicBrainzCache row for ``mb_id``."""
    with Session(get_engine()) as session:
        row = session.exec(
            select(MusicBrainzCache).where(MusicBrainzCache.mb_id == mb_id)
        ).first()
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            session.add(MusicBrainzCache(
                mb_id=mb_id,
                payload_json=json.dumps(payload),
                cached_at=now,
            ))
        else:
            row.payload_json = json.dumps(payload)
            row.cached_at = now
            session.add(row)
        session.commit()


async def lookup_artist(mb_id: str) -> Optional[dict]:
    """Validate + fetch an artist payload by MB ID. Cached forever.

    Returns the unwrapped artist dict (NOT the {"artist": {...}} envelope)
    or None on 404 / any error. The caller MUST tolerate None — never crash
    the discovery cron on one bad mb_id (Pitfall 10 hallucination gate at
    the candidate level).
    """
    cached = await asyncio.to_thread(_cache_get_sync, mb_id)
    if cached is not None:
        return cached
    try:
        envelope = await asyncio.to_thread(
            musicbrainzngs.get_artist_by_id,
            mb_id,
            includes=_MB_LOOKUP_INCLUDES,
        )
    except musicbrainzngs.ResponseError as exc:
        logger.warning("MB lookup failed for mbid=%s: %s", mb_id, exc)
        return None
    except Exception:
        logger.exception("MB lookup raised unexpectedly for mbid=%s", mb_id)
        return None
    artist = (envelope or {}).get("artist") if isinstance(envelope, dict) else None
    if artist is not None:
        await asyncio.to_thread(_cache_put_sync, mb_id, artist)
    return artist


async def lookup_artist_by_name(name: str, limit: int = 5) -> list[dict]:
    """Search MB by artist name; used by Pitfall 10 hallucination defense
    when the LLM returns a name without an MBID. Returns the
    ``artist-list`` array (possibly empty) or [] on any error.
    """
    try:
        envelope = await asyncio.to_thread(
            musicbrainzngs.search_artists,
            query=f"artist:{name}",
            limit=limit,
        )
    except Exception:
        logger.exception("MB search failed for name=%r", name)
        return []
    if not isinstance(envelope, dict):
        return []
    return envelope.get("artist-list", []) or []
