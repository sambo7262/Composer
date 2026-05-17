"""Phase 8 Plan 02 Task 3 — discovery_service compute pipeline +
artist_discovery_call_weekly tests.

Coverage (per Plan 02 Task 3 <behavior> block):
- Constants mirror Phase 7.1 SUGG-14 caps.
- Vibe.last_seed_track_id exists and defaults to None.
- D-A2 round-robin seed selector: rotates ids, skips unstarred, returns None
  on empty vibe.
- compute_candidate_set_for_seed validates via MB / dedups library + Lidarr /
  popularity-gates with adjacency.
- artist_discovery_call_weekly short-circuits on no-Lidarr / no-vibes,
  invokes the cost breaker, retries on MaxTokensTruncationError, filters
  hallucinations, writes DiscoveryCandidate rows.
- _weekly_maintenance_tick calls steps in order; step 3 failure does NOT
  block step 4 stamping.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _reset_discovery_service_state():
    from app.services import discovery_service

    discovery_service._reset_state_for_tests()
    discovery_service._LIDARR_KNOWN_ARTISTS_CACHE.clear()
    yield
    discovery_service._reset_state_for_tests()
    discovery_service._LIDARR_KNOWN_ARTISTS_CACHE.clear()


# ===========================================================================
# Constants + Vibe schema
# ===========================================================================


def test_constants_mirror_phase_71_caps():
    """Verify the DISCOVERY_ARTIST_* constants match the SUGG-14 cap shape."""
    from app.services.discovery_service import (
        DISCOVERY_ARTIST_CANDIDATE_LIMIT,
        DISCOVERY_ARTIST_CHARS_PER_TOKEN_RATIO,
        DISCOVERY_ARTIST_MAX_TOKENS_FLOOR,
        DISCOVERY_ARTIST_PROMPT_TOKEN_CEILING,
        DISCOVERY_ARTIST_PURPOSE,
    )

    assert DISCOVERY_ARTIST_CANDIDATE_LIMIT == 200
    assert DISCOVERY_ARTIST_PROMPT_TOKEN_CEILING == 150_000
    assert DISCOVERY_ARTIST_CHARS_PER_TOKEN_RATIO == 3.5
    assert DISCOVERY_ARTIST_MAX_TOKENS_FLOOR == 8000
    assert DISCOVERY_ARTIST_PURPOSE == "discovery_artist_weekly"


def test_vibe_last_seed_track_id_field_exists():
    """The Vibe model exposes ``last_seed_track_id: Optional[int]`` defaulting
    to None per D-A2.
    """
    from app.models.vibe import Vibe

    assert "last_seed_track_id" in Vibe.model_fields, (
        "Vibe.last_seed_track_id missing — D-A2 round-robin cursor"
    )


def test_migration_adds_vibe_last_seed_track_id(fresh_db):
    """``_migrate_add_columns`` adds ``vibe.last_seed_track_id`` additively."""
    from app.database import get_engine

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("PRAGMA table_info(vibe)")
        cols = {row[1] for row in cur.fetchall()}
        assert "last_seed_track_id" in cols, (
            "vibe.last_seed_track_id column missing after _migrate_add_columns"
        )
    finally:
        raw_conn.close()


# ===========================================================================
# D-A2 per-vibe seed-track selector (round-robin)
# ===========================================================================


def _seed_starred_tracks_in_vibe(session, vibe_id: int, track_ids: list[int]):
    """Helper — create Track rows for each id (idempotent: skip if exists)
    with user_rating=8.0 + a TrackVibe row tying each to ``vibe_id``.
    """
    from app.models.track import Track
    from app.models.vibe import TrackVibe

    for tid in track_ids:
        existing = session.get(Track, tid)
        if existing is None:
            session.add(Track(
                id=tid,
                plex_rating_key=f"rk-{tid}",
                title=f"T{tid}",
                artist=f"A{tid}",
                user_rating=8.0,
            ))
        else:
            # Make sure the pre-seeded track is starred (the selector's
            # filter is `user_rating > 0`).
            if not existing.user_rating:
                existing.user_rating = 8.0
                session.add(existing)
    session.commit()
    for tid in track_ids:
        session.add(TrackVibe(
            track_id=tid, vibe_id=vibe_id, distance=0.5,
            assigned_at="2026-05-17T00:00:00+00:00",
            assigned_by="cluster",
        ))
    session.commit()


def _seed_vibe(session, vibe_id: int):
    from app.models.vibe import Vibe
    session.add(Vibe(
        id=vibe_id, name=f"V{vibe_id}",
        created_at="2026-05-17T00:00:00+00:00",
    ))
    session.commit()


def test_seed_selector_round_robin_for_vibe(db_with_phase7):
    """Three starred tracks (ids 10, 20, 30) in vibe_id=1: first call returns
    10, then 20, then 30, then wraps to 10.
    """
    from app.services.discovery_service import (
        _pick_next_seed_track_for_vibe_sync,
    )

    _seed_vibe(db_with_phase7, 1)
    _seed_starred_tracks_in_vibe(db_with_phase7, 1, [10, 20, 30])

    first = _pick_next_seed_track_for_vibe_sync(1)
    second = _pick_next_seed_track_for_vibe_sync(1)
    third = _pick_next_seed_track_for_vibe_sync(1)
    fourth = _pick_next_seed_track_for_vibe_sync(1)

    assert first == 10
    assert second == 20
    assert third == 30
    assert fourth == 10  # wrap


def test_seed_selector_skips_unstarred(db_with_phase7):
    """Tracks with ``user_rating == 0`` (or NULL) never appear as seeds."""
    from app.models.track import Track
    from app.models.vibe import TrackVibe
    from app.services.discovery_service import (
        _pick_next_seed_track_for_vibe_sync,
    )

    _seed_vibe(db_with_phase7, 1)
    # Star one track, leave two unstarred.
    db_with_phase7.add(Track(
        id=100, plex_rating_key="rk-100", title="A", artist="A",
        user_rating=7.0,
    ))
    db_with_phase7.add(Track(
        id=200, plex_rating_key="rk-200", title="B", artist="B",
        user_rating=0.0,
    ))
    db_with_phase7.add(Track(
        id=300, plex_rating_key="rk-300", title="C", artist="C",
        user_rating=None,
    ))
    db_with_phase7.commit()
    for tid in (100, 200, 300):
        db_with_phase7.add(TrackVibe(
            track_id=tid, vibe_id=1, distance=0.5,
            assigned_at="2026-05-17T00:00:00+00:00",
            assigned_by="cluster",
        ))
    db_with_phase7.commit()

    s1 = _pick_next_seed_track_for_vibe_sync(1)
    s2 = _pick_next_seed_track_for_vibe_sync(1)
    # Only id=100 is starred — both calls return 100 (wrap on a single-row pool).
    assert s1 == 100
    assert s2 == 100


def test_seed_selector_returns_none_for_empty_vibe(db_with_phase7):
    """Vibe with zero starred members → returns None."""
    from app.services.discovery_service import (
        _pick_next_seed_track_for_vibe_sync,
    )

    _seed_vibe(db_with_phase7, 99)
    result = _pick_next_seed_track_for_vibe_sync(99)
    assert result is None


# ===========================================================================
# compute_candidate_set_for_seed pipeline
# ===========================================================================


def _make_track_with_artist_mbid(
    session, track_id: int, plex_artist_mbid: str, user_rating: float = 8.0,
):
    from app.models.track import Track

    session.add(Track(
        id=track_id,
        plex_rating_key=f"rk-{track_id}",
        title=f"T{track_id}",
        artist=f"A{track_id}",
        user_rating=user_rating,
        plex_artist_mbid=plex_artist_mbid,
    ))
    session.commit()


def test_compute_candidate_set_validates_via_mb(
    db_with_phase7, monkeypatch,
):
    """ListenBrainz returns 2 mbids; MB lookup returns payload for 'good'
    and None for 'bad'. Result list has 1 candidate; validation_drops=1.
    """
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client

    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-artist-mbid")

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {"artist_mbid": "good", "name": "Good Artist", "score": 1000},
            {"artist_mbid": "bad", "name": "Bad Artist", "score": 900},
        ]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        if mb_id == "good":
            return {
                "id": "good", "name": "Good Artist",
                "artist-relation-list": [], "release-group-list": [],
            }
        return None
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    # No Lidarr known → empty set; no in-library matches.
    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    kept, counts = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert counts.listenbrainz_returned == 2
    assert counts.validation_drops == 1
    assert counts.kept == 1
    assert len(kept) == 1
    assert kept[0].mb_id == "good"


def test_compute_candidate_set_dedups_in_library(
    db_with_phase7, monkeypatch,
):
    """Candidate mb_id matches an existing Track.plex_artist_mbid → dropped."""
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client

    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-artist-mbid")
    # A track in the library already has artist_mbid="already-in-library"
    _make_track_with_artist_mbid(
        db_with_phase7, 2, "already-in-library", user_rating=0.0,
    )

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {"artist_mbid": "already-in-library", "name": "Dupe", "score": 800},
        ]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        # Should never be called for the dedup'd candidate
        pytest.fail("MB lookup should not run for dedup'd mb_id")
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    kept, counts = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert counts.dedup_drops == 1
    assert counts.kept == 0
    assert kept == []


def test_compute_candidate_set_dedups_in_lidarr(
    db_with_phase7, monkeypatch,
):
    """Candidate mb_id matches the cached Lidarr 'already-in-library' set → dropped."""
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client

    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-artist-mbid")

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {"artist_mbid": "in-lidarr", "name": "Lidarr Dupe", "score": 700},
        ]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        pytest.fail("MB lookup should not run for dedup'd mb_id")
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _lidarr_dupe():
        return {"in-lidarr"}
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _lidarr_dupe,
    )

    kept, counts = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert counts.dedup_drops == 1
    assert counts.kept == 0


def test_compute_candidate_set_popularity_gate_drops_too_popular(
    db_with_phase7, monkeypatch,
):
    """MB release-group count > threshold + no adjacency → popularity_drops += 1."""
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client
    from app.services.discovery_service import (
        DISCOVERY_ARTIST_RG_POPULARITY_THRESHOLD,
    )

    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-artist-mbid")

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {"artist_mbid": "popular", "name": "Megastar", "score": 100},
        ]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    # No adjacency, many release groups → fails popularity gate.
    huge_rg_list = [{} for _ in range(DISCOVERY_ARTIST_RG_POPULARITY_THRESHOLD + 1)]

    async def _fake_lookup(mb_id):
        return {
            "id": "popular", "name": "Megastar",
            "artist-relation-list": [],
            "release-group-list": huge_rg_list,
        }
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    kept, counts = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert counts.popularity_drops == 1
    assert counts.kept == 0


def test_compute_candidate_set_popularity_gate_keeps_with_adjacency(
    db_with_phase7, monkeypatch,
):
    """Candidate has adjacency to starred → passes gate even if huge release-group count."""
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client
    from app.services.discovery_service import (
        DISCOVERY_ARTIST_RG_POPULARITY_THRESHOLD,
    )

    # Seed track + a starred track with MBID "starred-mbid"
    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-artist-mbid")
    _make_track_with_artist_mbid(db_with_phase7, 2, "starred-mbid")

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {"artist_mbid": "adj", "name": "Adjacent", "score": 100},
        ]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    huge_rg_list = [{} for _ in range(DISCOVERY_ARTIST_RG_POPULARITY_THRESHOLD + 5)]

    async def _fake_lookup(mb_id):
        return {
            "id": "adj", "name": "Adjacent",
            "artist-relation-list": [
                {"artist": {"id": "starred-mbid", "name": "Starred Artist"}},
            ],
            "release-group-list": huge_rg_list,
        }
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    kept, counts = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert counts.kept == 1
    assert kept[0].mb_id == "adj"
    assert kept[0].adjacency_kind == "artist-relation"
    # User-facing hook drops the "MusicBrainz" jargon — reads as a clean
    # "Linked to your starred X" instead of the prior gate diagnostic.
    assert kept[0].factual_hook == "Linked to your starred Starred Artist"


def test_factual_hook_uses_listenbrainz_comment_when_no_adjacency(
    db_with_phase7, monkeypatch,
):
    """Default-pass case (not popular, no adjacency): factual_hook uses the
    LB ``comment`` field instead of the internal gate diagnostic.
    """
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client

    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-mbid")

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {
                "artist_mbid": "candidate-mbid",
                "name": "Mogwai",
                "comment": "Scottish post-rock band",
                "score": 100,
            },
        ]
    monkeypatch.setattr(listenbrainz_client, "get_similar_artists", _fake_lb)

    async def _fake_lookup(mb_id):
        return {
            "id": "candidate-mbid", "name": "Mogwai",
            "artist-relation-list": [],
            "release-group-list": [{}],  # tiny catalog → passes gate by default
        }
    monkeypatch.setattr(musicbrainz_client, "lookup_artist", _fake_lookup)

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(discovery_service, "_get_lidarr_known_artists", _no_lidarr)

    kept, counts = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert counts.kept == 1
    # LB comment surfaces verbatim — no "below popularity threshold" leak.
    assert kept[0].factual_hook == "Scottish post-rock band"
    assert kept[0].adjacency_kind == "below-popularity"


def test_factual_hook_falls_back_to_mb_disambiguation(
    db_with_phase7, monkeypatch,
):
    """When LB comment is empty but MB has disambiguation, surface that."""
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client

    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-mbid")

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {"artist_mbid": "x", "name": "X", "comment": "", "score": 100},
        ]
    monkeypatch.setattr(listenbrainz_client, "get_similar_artists", _fake_lb)

    async def _fake_lookup(mb_id):
        return {
            "id": "x", "name": "X",
            "disambiguation": "British alternative rock band",
            "artist-relation-list": [],
            "release-group-list": [{}],
        }
    monkeypatch.setattr(musicbrainz_client, "lookup_artist", _fake_lookup)

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(discovery_service, "_get_lidarr_known_artists", _no_lidarr)

    kept, _ = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert kept[0].factual_hook == "British alternative rock band"


def test_factual_hook_seed_similarity_fallback(
    db_with_phase7, monkeypatch,
):
    """No LB comment AND no MB disambiguation → "Similar to your starred {seed}"."""
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client
    from app.models.track import Track
    from sqlmodel import Session

    # Seed track has a known artist name we can match against the fallback.
    with Session(db_with_phase7.bind) as s:
        seed = Track(
            id=1, plex_rating_key="rk-1", title="t",
            artist="Four Tet", plex_artist_mbid="seed-mbid",
        )
        s.add(seed)
        s.commit()

    async def _fake_lb(seed_mbid, limit=100):
        return [
            {"artist_mbid": "x", "name": "X", "comment": "", "score": 100},
        ]
    monkeypatch.setattr(listenbrainz_client, "get_similar_artists", _fake_lb)

    async def _fake_lookup(mb_id):
        return {
            "id": "x", "name": "X", "disambiguation": "",
            "artist-relation-list": [], "release-group-list": [{}],
        }
    monkeypatch.setattr(musicbrainz_client, "lookup_artist", _fake_lookup)

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(discovery_service, "_get_lidarr_known_artists", _no_lidarr)

    kept, _ = _run_async(
        discovery_service.compute_candidate_set_for_seed(1, 1)
    )
    assert kept[0].factual_hook == "Similar to your starred Four Tet"


# ===========================================================================
# artist_discovery_call_weekly lifecycle
# ===========================================================================


def _seed_lidarr_configured(db_session, configured: bool = True):
    """Stub a ServiceConfig row for Lidarr with is_configured=True."""
    from app.models.settings import ServiceConfig

    db_session.add(ServiceConfig(
        service_name="lidarr",
        url="http://lidarr:8686",
        encrypted_credential="dummy",
        is_configured=configured,
    ))
    db_session.commit()


def test_artist_discovery_call_weekly_skips_on_no_lidarr_config(
    db_with_phase7, monkeypatch,
):
    """Lidarr unconfigured → state='idle'; LLMUsage row with purpose
    suffix '_skipped_no_lidarr'; no Anthropic call.
    """
    from app.models.llm_usage import LLMUsage
    from app.services import discovery_service

    # No ServiceConfig row → unconfigured.
    _run_async(discovery_service.artist_discovery_call_weekly())

    state = discovery_service.get_state()
    assert state.state == "idle"
    rows = db_with_phase7.exec(
        select(LLMUsage).where(LLMUsage.purpose.like("discovery_artist_%"))
    ).all()
    skip_rows = [
        r for r in rows if "no_lidarr" in (r.purpose or "")
    ]
    assert len(skip_rows) == 1
    assert "Lidarr" in (skip_rows[0].error_text or "")


def test_artist_discovery_call_weekly_skips_on_no_vibes(
    db_with_phase7, monkeypatch,
):
    """Lidarr configured but 0 active vibes → state='idle'; LLMUsage
    row with purpose suffix '_skipped_no_vibes'.
    """
    from app.models.llm_usage import LLMUsage
    from app.services import discovery_service

    _seed_lidarr_configured(db_with_phase7)
    _run_async(discovery_service.artist_discovery_call_weekly())

    state = discovery_service.get_state()
    assert state.state == "idle"
    rows = db_with_phase7.exec(
        select(LLMUsage).where(LLMUsage.purpose.like("discovery_artist_%"))
    ).all()
    no_vibes_rows = [
        r for r in rows if "no_vibes" in (r.purpose or "")
    ]
    assert len(no_vibes_rows) == 1


def test_artist_discovery_call_weekly_invokes_cost_breaker_before_llm(
    db_with_phase7, monkeypatch,
):
    """check_or_raise(purpose_prefix='discovery_') tripping → state='cost_locked',
    LLMUsage row with purpose '_cost_locked', no Anthropic call.
    """
    from app.models.llm_usage import LLMUsage
    from app.services import discovery_service, listenbrainz_client, musicbrainz_client
    from app.services.llm_cost_breaker import CostBreakerTrippedError

    _seed_lidarr_configured(db_with_phase7)
    _seed_vibe(db_with_phase7, 1)
    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-mbid")
    _seed_starred_tracks_in_vibe(db_with_phase7, 1, [1])

    async def _fake_lb(seed_mbid, limit=100):
        return [{"artist_mbid": "x", "name": "X", "score": 100}]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        return {
            "id": "x", "name": "X",
            "artist-relation-list": [], "release-group-list": [],
        }
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    async def _trip(*args, **kwargs):
        raise CostBreakerTrippedError("daily_quota_50", "tomorrow")
    monkeypatch.setattr(discovery_service, "check_or_raise", _trip)

    # AnthropicClient should NEVER be constructed; assert by monkeypatching
    # it to a sentinel that crashes if called.
    def _fail_ctor(*a, **k):
        raise AssertionError("AnthropicClient must not be constructed when cost-locked")
    monkeypatch.setattr(discovery_service, "AnthropicClient", _fail_ctor)

    _run_async(discovery_service.artist_discovery_call_weekly())

    state = discovery_service.get_state()
    assert state.state == "cost_locked"
    rows = db_with_phase7.exec(
        select(LLMUsage).where(LLMUsage.purpose.like("discovery_artist_%"))
    ).all()
    cost_rows = [r for r in rows if "cost_locked" in (r.purpose or "")]
    assert len(cost_rows) == 1


def _make_fake_anthropic_client(call_mock):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def call_with_structured_output(self, **kw):
            return await call_mock(**kw)

    return _FakeClient


def test_artist_discovery_call_weekly_writes_candidates(
    db_with_phase7, monkeypatch,
):
    """End-to-end with mocked LB / MB / Anthropic returning 1 pick →
    DiscoveryCandidate row written with seed_track_id + seed_vibe_id.
    """
    from app.models.discovery import DiscoveryCandidate
    from app.services import (
        discovery_service, listenbrainz_client, musicbrainz_client,
    )

    _seed_lidarr_configured(db_with_phase7)
    _seed_vibe(db_with_phase7, 1)
    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-mbid")
    _seed_starred_tracks_in_vibe(db_with_phase7, 1, [1])

    async def _fake_lb(seed_mbid, limit=100):
        return [{"artist_mbid": "X", "name": "Xname", "score": 100}]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        return {
            "id": "X", "name": "Xname",
            "artist-relation-list": [], "release-group-list": [],
        }
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    async def _no_op(*a, **kw):
        return None
    monkeypatch.setattr(discovery_service, "check_or_raise", _no_op)

    # Return a single valid pick for mb_id="X".
    response_obj = discovery_service.ArtistDiscoveryPicksResponse(picks=[
        discovery_service.LLMArtistPick(
            mb_id="X", artist_name="Xname", rank=1, rationale="great",
        ),
    ])
    call_mock = AsyncMock(return_value=response_obj)
    monkeypatch.setattr(
        discovery_service, "AnthropicClient",
        _make_fake_anthropic_client(call_mock),
    )

    _run_async(discovery_service.artist_discovery_call_weekly())

    rows = db_with_phase7.exec(select(DiscoveryCandidate)).all()
    assert len(rows) == 1
    assert rows[0].mb_id == "X"
    assert rows[0].seed_track_id == 1
    assert rows[0].seed_vibe_id == 1
    assert rows[0].llm_rank == 1
    assert rows[0].llm_rationale == "great"
    state = discovery_service.get_state()
    assert state.state == "idle"
    assert state.last_candidates_made == 1


def test_artist_discovery_call_weekly_filters_hallucinated_picks(
    db_with_phase7, monkeypatch,
):
    """LLM returns a pick with mb_id NOT in the validated pool → row NOT
    written; warning logged.
    """
    from app.models.discovery import DiscoveryCandidate
    from app.services import (
        discovery_service, listenbrainz_client, musicbrainz_client,
    )

    _seed_lidarr_configured(db_with_phase7)
    _seed_vibe(db_with_phase7, 1)
    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-mbid")
    _seed_starred_tracks_in_vibe(db_with_phase7, 1, [1])

    async def _fake_lb(seed_mbid, limit=100):
        return [{"artist_mbid": "X", "name": "X", "score": 1}]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        return {
            "id": "X", "name": "X",
            "artist-relation-list": [], "release-group-list": [],
        }
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    async def _no_op(*a, **kw):
        return None
    monkeypatch.setattr(discovery_service, "check_or_raise", _no_op)

    response_obj = discovery_service.ArtistDiscoveryPicksResponse(picks=[
        discovery_service.LLMArtistPick(
            mb_id="HALLUCINATED", artist_name="Fake",
            rank=1, rationale="ai-fabricated",
        ),
    ])
    call_mock = AsyncMock(return_value=response_obj)
    monkeypatch.setattr(
        discovery_service, "AnthropicClient",
        _make_fake_anthropic_client(call_mock),
    )

    _run_async(discovery_service.artist_discovery_call_weekly())

    rows = db_with_phase7.exec(select(DiscoveryCandidate)).all()
    assert rows == []  # hallucinated picks are silently filtered


def test_artist_discovery_call_weekly_retries_on_max_tokens_truncation(
    db_with_phase7, monkeypatch,
):
    """First Anthropic call raises MaxTokensTruncationError; second
    succeeds with doubled max_tokens.
    """
    from app.services import (
        discovery_service, listenbrainz_client, musicbrainz_client,
    )
    from app.services.anthropic_client import MaxTokensTruncationError
    from app.services.discovery_service import (
        DISCOVERY_ARTIST_MAX_TOKENS_FLOOR,
    )

    _seed_lidarr_configured(db_with_phase7)
    _seed_vibe(db_with_phase7, 1)
    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-mbid")
    _seed_starred_tracks_in_vibe(db_with_phase7, 1, [1])

    async def _fake_lb(seed_mbid, limit=100):
        return [{"artist_mbid": "X", "name": "X", "score": 1}]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        return {
            "id": "X", "name": "X",
            "artist-relation-list": [], "release-group-list": [],
        }
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    async def _no_op(*a, **kw):
        return None
    monkeypatch.setattr(discovery_service, "check_or_raise", _no_op)

    response_obj = discovery_service.ArtistDiscoveryPicksResponse(picks=[
        discovery_service.LLMArtistPick(
            mb_id="X", artist_name="X", rank=1, rationale="ok",
        ),
    ])

    # First call raises, second succeeds.
    call_log: list = []

    async def _flaky(**kw):
        call_log.append(kw["max_tokens"])
        if len(call_log) == 1:
            raise MaxTokensTruncationError(
                purpose="discovery_artist_weekly",
                requested_max_tokens=kw["max_tokens"],
                truncated_text_length=400,
            )
        return response_obj

    monkeypatch.setattr(
        discovery_service, "AnthropicClient",
        _make_fake_anthropic_client(_flaky),
    )

    _run_async(discovery_service.artist_discovery_call_weekly())

    assert len(call_log) == 2
    assert call_log[0] == DISCOVERY_ARTIST_MAX_TOKENS_FLOOR
    assert call_log[1] == DISCOVERY_ARTIST_MAX_TOKENS_FLOOR * 2


def test_artist_discovery_call_weekly_fails_gracefully_on_anthropic_error(
    db_with_phase7, monkeypatch,
):
    """A generic Exception from Anthropic → state='error'; LLMUsage row
    with purpose '_error' + error_text; does NOT raise.
    """
    from app.models.llm_usage import LLMUsage
    from app.services import (
        discovery_service, listenbrainz_client, musicbrainz_client,
    )

    _seed_lidarr_configured(db_with_phase7)
    _seed_vibe(db_with_phase7, 1)
    _make_track_with_artist_mbid(db_with_phase7, 1, "seed-mbid")
    _seed_starred_tracks_in_vibe(db_with_phase7, 1, [1])

    async def _fake_lb(seed_mbid, limit=100):
        return [{"artist_mbid": "X", "name": "X", "score": 1}]
    monkeypatch.setattr(
        listenbrainz_client, "get_similar_artists", _fake_lb,
    )

    async def _fake_lookup(mb_id):
        return {
            "id": "X", "name": "X",
            "artist-relation-list": [], "release-group-list": [],
        }
    monkeypatch.setattr(
        musicbrainz_client, "lookup_artist", _fake_lookup,
    )

    async def _no_lidarr():
        return set()
    monkeypatch.setattr(
        discovery_service, "_get_lidarr_known_artists", _no_lidarr,
    )

    async def _no_op(*a, **kw):
        return None
    monkeypatch.setattr(discovery_service, "check_or_raise", _no_op)

    async def _boom(**kw):
        raise RuntimeError("anthropic exploded")

    monkeypatch.setattr(
        discovery_service, "AnthropicClient",
        _make_fake_anthropic_client(_boom),
    )

    # Must NOT raise
    _run_async(discovery_service.artist_discovery_call_weekly())

    state = discovery_service.get_state()
    assert state.state == "error"
    assert "anthropic exploded" in (state.last_error or "")
    rows = db_with_phase7.exec(
        select(LLMUsage).where(LLMUsage.purpose.like("discovery_artist_%"))
    ).all()
    err_rows = [r for r in rows if r.purpose.endswith("_error")]
    assert len(err_rows) == 1


# ===========================================================================
# _weekly_maintenance_tick — step order + failure isolation
# ===========================================================================


def test_weekly_tick_calls_artist_discovery_after_suggestions(monkeypatch):
    """Call order = prune → suggestions discovery → artist discovery →
    WeeklyCronState stamp.
    """
    from app.services import sync_scheduler

    call_log: list = []

    async def _fake_prune(*a, **k):
        call_log.append("prune")
        return MagicMock(
            removed=[], mirror_size=0, final_plex_count=0,
            still_present_after_remove=[],
        )

    async def _fake_discovery():
        call_log.append("suggestions_discovery")

    async def _fake_artist():
        call_log.append("artist_discovery")

    async def _fake_stamp(*a, **k):
        call_log.append("weekly_cron_stamp")

    def _fake_creds_sync():
        return ("http://plex", "token")

    monkeypatch.setattr(
        "app.services.plex_playlist_service.prune_suggestions_playlist_to_mirror",
        _fake_prune,
    )
    monkeypatch.setattr(
        "app.services.suggestions_discovery.discovery_call_weekly",
        _fake_discovery,
    )
    monkeypatch.setattr(
        "app.services.suggestions_service._read_plex_creds_sync",
        _fake_creds_sync,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.artist_discovery_call_weekly",
        _fake_artist,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.update_weekly_cron_state",
        _fake_stamp,
    )

    _run_async(sync_scheduler._weekly_maintenance_tick())

    assert call_log == [
        "prune", "suggestions_discovery",
        "artist_discovery", "weekly_cron_stamp",
    ], f"unexpected order: {call_log!r}"


def test_weekly_tick_continues_after_artist_discovery_failure(monkeypatch):
    """Step 3 (artist_discovery_call_weekly) raises → step 4 (stamp)
    STILL runs. Tick does not propagate.
    """
    from app.services import sync_scheduler

    call_log: list = []

    async def _fake_prune(*a, **k):
        call_log.append("prune")
        return MagicMock(
            removed=[], mirror_size=0, final_plex_count=0,
            still_present_after_remove=[],
        )

    async def _fake_discovery():
        call_log.append("suggestions_discovery")

    async def _boom_artist():
        call_log.append("artist_discovery_raised")
        raise RuntimeError("step 3 exploded")

    async def _fake_stamp(*a, **k):
        call_log.append("weekly_cron_stamp")

    def _fake_creds_sync():
        return ("http://plex", "token")

    monkeypatch.setattr(
        "app.services.plex_playlist_service.prune_suggestions_playlist_to_mirror",
        _fake_prune,
    )
    monkeypatch.setattr(
        "app.services.suggestions_discovery.discovery_call_weekly",
        _fake_discovery,
    )
    monkeypatch.setattr(
        "app.services.suggestions_service._read_plex_creds_sync",
        _fake_creds_sync,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.artist_discovery_call_weekly",
        _boom_artist,
    )
    monkeypatch.setattr(
        "app.services.discovery_service.update_weekly_cron_state",
        _fake_stamp,
    )

    # Must NOT raise — step 3 failure is isolated.
    _run_async(sync_scheduler._weekly_maintenance_tick())

    # Step 4 still ran.
    assert "weekly_cron_stamp" in call_log, (
        f"step 4 did NOT run after step 3 failure: {call_log!r}"
    )
    assert "artist_discovery_raised" in call_log


def test_weekly_cron_state_stamp_updates_last_tick_at(db_with_phase7):
    """After a successful update_weekly_cron_state, WeeklyCronState
    row id=1 has last_tick_at populated to an ISO 8601 UTC string.
    """
    from app.models.discovery import WeeklyCronState
    from app.services import discovery_service

    _run_async(discovery_service.update_weekly_cron_state())

    row = db_with_phase7.exec(
        select(WeeklyCronState).where(WeeklyCronState.id == 1)
    ).first()
    assert row is not None
    assert row.last_tick_at is not None
    # Parseable ISO 8601 (will raise ValueError on bad format).
    datetime.fromisoformat(row.last_tick_at)
