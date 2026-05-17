"""Phase 8 D-A1 — ListenBrainz similar-artists labs endpoint client.

Single async function. httpx (already a project dep) — no full library
needed for one read-only labs endpoint.

Endpoint contract: VERIFIED at Plan 02 task 1 via /tmp/listenbrainz_smoke.json
+ tests/fixtures/listenbrainz_similar_artists_sample.json. If the endpoint
shape changes upstream, the fixture canary catches it (the parser test asserts
against the fixture shape).

**Locked parser contract** (Task 1 user approval): each entry in the labs
response has these fields (snake_case):
- ``artist_mbid`` (string) — candidate MBID
- ``name`` (string) — display name
- ``score`` (**integer** — raw, NOT normalized to 0..1; preserve as-is)
- ``comment`` (string, often empty or short bio)
- ``type`` (string: "Group" | "Person")
- ``gender`` (nullable string, only set when type=Person)
- ``reference_mbid`` (string) — echoes the seed MBID back

This module returns the raw labs entries as-is (list-position ordering
preserved); downstream filters (``compute_candidate_set_for_seed``) read
``artist_mbid`` + ``name`` and optionally ``score`` for ordering tie-break.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

LISTENBRAINZ_SIMILAR_ARTISTS_URL = (
    "https://labs.api.listenbrainz.org/similar-artists/json"
)
# Algorithm string locked from the labs UI; same as the smoke test used
# (Task 1 captured the Four Tet fixture with this exact algorithm string).
LISTENBRAINZ_DEFAULT_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_"
    "limit_100_filter_True_skip_30"
)


async def get_similar_artists(seed_mbid: str, limit: int = 100) -> list[dict]:
    """Fetch similar artists for a seed MBID. Returns scored list.

    Each entry contains at least ``{artist_mbid, name, score}`` per the
    fixture committed in tests/fixtures/listenbrainz_similar_artists_sample.json.
    Empty list on any error (5xx, timeout, parse failure) — discovery
    cron must never crash on a single seed's lookup failure.

    ``score`` is preserved as the raw integer returned by the labs endpoint
    (Task 1 locked contract).
    """
    params = {
        "artist_mbids": seed_mbid,
        "algorithm": LISTENBRAINZ_DEFAULT_ALGORITHM,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                LISTENBRAINZ_SIMILAR_ARTISTS_URL, params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                logger.warning(
                    "ListenBrainz returned non-list for mbid=%s: %r",
                    seed_mbid, type(data).__name__,
                )
                return []
            return data[:limit]
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "ListenBrainz HTTP %d for mbid=%s",
            exc.response.status_code, seed_mbid,
        )
        return []
    except (httpx.TimeoutException, httpx.RequestError):
        logger.warning("ListenBrainz network error for mbid=%s", seed_mbid)
        return []
    except Exception:
        logger.exception("ListenBrainz parse error for mbid=%s", seed_mbid)
        return []
