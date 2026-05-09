"""Phase 5 taste profile service (D-17, RATE-05).

Computes the FULL taste profile in Phase 5:
- 4-D centroid over (energy, tempo, danceability, valence) of rated tracks
- Top 10 artists by rated count
- Top 10 genres by rated count
- ~200-word LLM-generated summary text via the new AnthropicClient (verifies
  prompt caching end-to-end before Phase 6/7 depend on it)

Storage: single-row TasteProfile id=1 (Pattern 5 — upsert in place).

Recompute trigger (D-18): when RatingChanged events accumulate to >=10% delta of the
prior rated set size since `last_computed_at`. Counter on the dispatcher path, not a
scheduled job. The accumulation happens naturally because each RatingChanged event
inserts/updates a Track row before this hook runs; the hook then compares
`current_rated_count` (queried fresh) against `prior.rated_track_count` (snapshot
from the last recompute) — a true accumulation across events.

NOTE: Phase 5 does NOT do clustering. The 4-D centroid is a single numpy.mean over
rated tracks — NOT k-means. scikit-learn import is forbidden in this file
(RESEARCH Anti-Patterns; clustering = Phase 6 only). A static AST test in
tests/test_taste_profile_service.py enforces this.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_engine
from app.models.taste_profile import TasteProfile
from app.models.track import Track

logger = logging.getLogger(__name__)


class TasteProfileSummary(BaseModel):
    """LLM response shape — just the summary text. Returned by AnthropicClient."""

    summary_text: str


# -----------------------------------------------------------------------------
# Sync helpers — all called via asyncio.to_thread (D-09 invariant)
# -----------------------------------------------------------------------------

def _aggregate_rated_set_sync() -> dict:
    """Query rated set, compute centroid + top artists/genres. Returns a dict for upsert."""
    with Session(get_engine()) as session:
        stmt = select(Track).where(Track.user_rating > 0)
        tracks: List[Track] = list(session.exec(stmt).all())

    if not tracks:
        return {
            "rated_track_count": 0,
            "centroid": None,
            "top_artists": [],
            "top_genres": [],
            "tracks_for_prompt": [],
        }

    # 4-D centroid over (energy, tempo, danceability, valence). Skip rows with any None.
    feats = []
    for t in tracks:
        row = (t.energy, t.tempo, t.danceability, t.valence)
        if all(v is not None for v in row):
            feats.append(row)

    centroid = None
    if feats:
        arr = np.array(feats, dtype=float)
        mean = arr.mean(axis=0)
        centroid = {
            "energy": float(mean[0]),
            "tempo": float(mean[1]),
            "danceability": float(mean[2]),
            "valence": float(mean[3]),
        }

    # Top 10 artists by rated count.
    artist_counts = Counter(t.artist for t in tracks if t.artist)
    top_artists = [
        {"artist": a, "count": c} for a, c in artist_counts.most_common(10)
    ]

    # Top 10 genres (split comma-separated genre strings).
    all_genres = []
    for t in tracks:
        if t.genre:
            all_genres.extend(g.strip() for g in t.genre.split(",") if g.strip())
    genre_counts = Counter(all_genres)
    top_genres = [
        {"genre": g, "count": c} for g, c in genre_counts.most_common(10)
    ]

    # Compact track list for the LLM prompt — title + artist + rating; top 50 by rating then year.
    tracks_for_prompt = [
        {"title": t.title, "artist": t.artist, "rating": t.user_rating}
        for t in sorted(
            tracks, key=lambda x: (-(x.user_rating or 0), -(x.year or 0))
        )[:50]
    ]

    return {
        "rated_track_count": len(tracks),
        "centroid": centroid,
        "top_artists": top_artists,
        "top_genres": top_genres,
        "tracks_for_prompt": tracks_for_prompt,
    }


def _upsert_taste_profile_sync(
    rated_count: int,
    centroid: Optional[dict],
    top_artists: list,
    top_genres: list,
    summary_text: str,
) -> None:
    """Single-row upsert (id=1). Pattern 5."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        stmt = select(TasteProfile).where(TasteProfile.id == 1)
        row = session.exec(stmt).first()
        if row is None:
            row = TasteProfile(id=1)
        row.rated_track_count = rated_count
        row.centroid_energy = centroid["energy"] if centroid else None
        row.centroid_tempo = centroid["tempo"] if centroid else None
        row.centroid_danceability = centroid["danceability"] if centroid else None
        row.centroid_valence = centroid["valence"] if centroid else None
        row.top_artists_json = json.dumps(top_artists)
        row.top_genres_json = json.dumps(top_genres)
        row.summary_text = summary_text
        row.computed_at = now
        session.add(row)
        session.commit()


def _build_system_prompt(
    centroid: Optional[dict],
    top_artists: list,
    top_genres: list,
    tracks_for_prompt: list,
) -> str:
    """Build a system prompt padded above 2048 tokens to actually engage caching (Pitfall 4).

    The padding is real Composer context (description of the app + user's library) — not
    lorem ipsum. This block is identical across recompute calls within the 1h TTL, which
    is exactly what enables the cache to work.
    """
    parts = [
        "You are a music critic helping a Composer user understand their taste profile.",
        "Composer is a self-hosted music companion that turns Plex star ratings into living "
        "vibe playlists and a continuous suggestions queue. Your job: write a ~200-word summary "
        "of the user's taste based on the structured data below. Be specific (mention real "
        "artists, real genres). Avoid generic phrases like 'eclectic taste' or 'wide variety'. "
        "The user knows their own library — surface insights they might not have noticed.",
        "",
        "## User library structured stats",
        f"Rated track count: {sum(a['count'] for a in top_artists) if top_artists else 0}",
    ]
    if centroid:
        parts.append(
            f"Audio-feature centroid: energy={centroid['energy']:.2f}, "
            f"tempo={centroid['tempo']:.1f} BPM, danceability={centroid['danceability']:.2f}, "
            f"valence={centroid['valence']:.2f} (0-1 scale; valence is positive-affect)"
        )
    if top_artists:
        parts.append("Top 10 artists by rated count:")
        for a in top_artists:
            parts.append(f"  - {a['artist']} ({a['count']} rated tracks)")
    if top_genres:
        parts.append("Top 10 genres by rated count:")
        for g in top_genres:
            parts.append(f"  - {g['genre']} ({g['count']})")
    parts.append("")
    parts.append("## Top 50 rated tracks (highest rating first)")
    for t in tracks_for_prompt:
        parts.append(f"  - {t['title']} — {t['artist']} ({t['rating']:.1f}/10)")
    parts.append("")
    parts.append("## Output format")
    parts.append('Return JSON: {"summary_text": "<your 200-word summary>"}')
    parts.append("")
    parts.append(
        "## Composer context (cacheable; identical across calls within 1h TTL)\n"
        "Composer reads Plex userRating values (0-10 scale) and treats them as the user's "
        "authoritative taste signal. Composer NEVER writes ratings back to Plex. Composer "
        "maintains 'vibes' (clusters of audio-feature-similar tracks) and a continuous "
        "Suggestions queue. The user listens via Plexamp on iOS or Plex Web. Slotting works "
        "on audio features computed by Essentia: energy from spectral RMS, tempo via beat "
        "tracking, danceability via spectral complexity, valence as a weighted combination "
        "of mode/danceability/brightness/pitch_salience. Suggestions ranking uses Anthropic "
        "Sonnet with prompt caching for cost. The taste profile centroid is the mean over "
        "rated tracks' 4-D audio features. The user's full library lives on a Synology NAS, "
        "synced from Plex via the library_sync APScheduler job. New tracks arrive from "
        "Lidarr, are imported into Plex, and Composer auto-analyzes them via Essentia. "
        "Vibes are persistent — never recomputed automatically; user-triggered only. The "
        "user can rename, merge, or split vibes during the setup wizard. Composer is a "
        "single FastAPI process with a single SQLite file; no separate worker, no Redis, "
        "no Celery. Anthropic prompt caching uses ttl=1h explicitly because the default "
        "regressed to 5min in March 2026. The Sonnet 4.6 cache breakpoint is 2048 tokens "
        "minimum. Plex webhooks at /api/webhooks/plex deliver media.rate, media.scrobble, "
        "and library.new events; an APScheduler polling job catches whatever the webhook "
        "missed. The event bus is a single asyncio.Queue with a single dispatcher task "
        "for SQLite write serialization. Dedupe is sha256(event_type|ratingKey|user_rating|"
        "5s_bucket) with INSERT OR IGNORE on a UNIQUE constraint. The first-run auto-backfill "
        "populates user_rating + last_viewed_at + view_count from Plex on initial Phase 5 "
        "deploy onto an existing v1 library. Manual Resync now button at /api/rating-sync/start "
        "shares the same singleton state machine as the auto-trigger (D-11)."
    )
    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Async public API
# -----------------------------------------------------------------------------

async def recompute(triggered_by: str = "manual") -> None:
    """RATE-05 + D-17: full taste profile build.

    Queries rated set, computes structured aggregates, calls Anthropic for summary text,
    upserts the single-row TasteProfile (id=1).
    """
    agg = await asyncio.to_thread(_aggregate_rated_set_sync)
    if agg["rated_track_count"] == 0:
        logger.info(
            "No rated tracks — taste profile recompute skipped (triggered_by=%s)",
            triggered_by,
        )
        return

    # LLM call for summary text. Best-effort: persist structured aggregates even on failure.
    summary_text = ""
    try:
        with Session(get_engine()) as session:
            client = get_anthropic_client_v2(session)
        system_prompt = _build_system_prompt(
            agg["centroid"],
            agg["top_artists"],
            agg["top_genres"],
            agg["tracks_for_prompt"],
        )
        summary = await client.call_with_structured_output(
            system_prompt=system_prompt,
            user_prompt="Write the summary now.",
            response_model=TasteProfileSummary,
            purpose="taste_profile_summary",
        )
        summary_text = summary.summary_text
    except Exception:
        logger.exception(
            "Anthropic summary call failed; persisting structured aggregates without LLM text"
        )

    await asyncio.to_thread(
        _upsert_taste_profile_sync,
        agg["rated_track_count"],
        agg["centroid"],
        agg["top_artists"],
        agg["top_genres"],
        summary_text,
    )
    logger.info(
        "Taste profile recomputed (triggered_by=%s, n=%d)",
        triggered_by,
        agg["rated_track_count"],
    )


def get_current_profile() -> Optional[TasteProfile]:
    """Read the current TasteProfile row id=1 (or None if not yet computed)."""
    with Session(get_engine()) as session:
        stmt = select(TasteProfile).where(TasteProfile.id == 1)
        return session.exec(stmt).first()


async def maybe_recompute_after_rating_change() -> None:
    """D-18: trigger recompute when rated set grew/shrunk by >=10% since last computed_at.

    Uses prior.rated_track_count as the snapshot baseline (set on the last recompute).
    A current-rated-count vs baseline diff is computed each time and compared against
    a 0.10 (10%) threshold. Accumulates naturally across events because each event
    inserts/updates a Track row before this hook runs.
    """
    def _check_delta() -> bool:
        with Session(get_engine()) as session:
            profile = session.exec(
                select(TasteProfile).where(TasteProfile.id == 1)
            ).first()
            current_count = len(
                session.exec(select(Track.id).where(Track.user_rating > 0)).all()
            )
            if profile is None:
                # No profile yet — first compute when any rated tracks exist.
                return current_count > 0
            prior = max(profile.rated_track_count, 1)
            return abs(current_count - profile.rated_track_count) / prior >= 0.10

    should = await asyncio.to_thread(_check_delta)
    if should:
        await recompute(triggered_by="rating_change_threshold")


def get_anthropic_client_v2(session):
    """Module-local re-export so tests can monkeypatch this name without import cycles."""
    from app.services.anthropic_client import get_anthropic_client_v2 as _factory
    return _factory(session)
