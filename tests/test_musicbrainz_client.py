"""Phase 8 Plan 02 Task 2 — MusicBrainz client tests.

Coverage:
- Module-load side-effects: set_useragent + set_rate_limit fire exactly once
  on import.
- ``lookup_artist`` unwraps the {"artist": {...}} envelope to the bare artist
  dict; caches indefinitely; returns None on 404 / unexpected errors.
- ``lookup_artist_by_name`` calls musicbrainzngs.search_artists with
  ``query=f"artist:{name}"``.
- Cache write-through persists to SQLite (MusicBrainzCache row exists +
  payload_json round-trips).

All ``musicbrainzngs.*`` symbols are mocked — no real network round-trips.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from app.models.discovery import MusicBrainzCache


# ---------------------------------------------------------------------------
# Module-load side effects
# ---------------------------------------------------------------------------


def _reset_musicbrainz_module(monkeypatch):
    """Capture the set_useragent + set_rate_limit calls fired at import time."""
    import musicbrainzngs

    useragent_calls = []
    rate_limit_calls = []

    monkeypatch.setattr(
        musicbrainzngs, "set_useragent",
        lambda *args, **kwargs: useragent_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        musicbrainzngs, "set_rate_limit",
        lambda *args, **kwargs: rate_limit_calls.append((args, kwargs)),
    )

    # Force re-import so the module-load side effects fire under the mocks.
    if "app.services.musicbrainz_client" in sys.modules:
        del sys.modules["app.services.musicbrainz_client"]
    importlib.import_module("app.services.musicbrainz_client")
    return useragent_calls, rate_limit_calls


def test_useragent_set_at_module_load(monkeypatch):
    """``musicbrainzngs.set_useragent`` is called EXACTLY ONCE at module
    import with the locked Composer ("Composer", "2.0", email) tuple.
    """
    ua, _rl = _reset_musicbrainz_module(monkeypatch)
    assert len(ua) == 1, f"expected exactly 1 set_useragent call, got {ua!r}"
    args, kwargs = ua[0]
    assert args[0] == "Composer"
    assert args[1] == "2.0"
    # Third arg is the contact email — must be a string with @ (sanity check)
    assert isinstance(args[2], str) and "@" in args[2]


def test_rate_limit_set_at_module_load(monkeypatch):
    """``musicbrainzngs.set_rate_limit`` is called exactly once at module
    load with the 1 req/sec interval (MB rate-limit policy).
    """
    _ua, rl = _reset_musicbrainz_module(monkeypatch)
    assert len(rl) == 1, f"expected exactly 1 set_rate_limit call, got {rl!r}"
    args, kwargs = rl[0]
    # Accept either positional or keyword form
    if args:
        interval = args[0]
    else:
        interval = kwargs.get("limit_or_interval")
    assert interval == 1.0


# ---------------------------------------------------------------------------
# lookup_artist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_artist_returns_payload(db_with_phase7, monkeypatch):
    """``lookup_artist("abc")`` returns the unwrapped artist dict (not the
    {"artist": {...}} envelope) and persists the bare payload to the cache.
    """
    from app.services import musicbrainz_client

    fake_call = MagicMock(return_value={
        "artist": {"id": "abc", "name": "Four Tet"},
    })
    monkeypatch.setattr(
        musicbrainz_client.musicbrainzngs, "get_artist_by_id", fake_call,
    )

    result = await musicbrainz_client.lookup_artist("abc")
    assert result == {"id": "abc", "name": "Four Tet"}
    assert fake_call.call_count == 1


@pytest.mark.asyncio
async def test_lookup_artist_cached_on_first_call(db_with_phase7, monkeypatch):
    """First call hits ``musicbrainzngs.get_artist_by_id`` once; second call
    for the SAME mb_id does NOT hit the network (cache hit).
    """
    from app.services import musicbrainz_client

    fake_call = MagicMock(return_value={
        "artist": {"id": "abc", "name": "Four Tet"},
    })
    monkeypatch.setattr(
        musicbrainz_client.musicbrainzngs, "get_artist_by_id", fake_call,
    )

    r1 = await musicbrainz_client.lookup_artist("abc")
    r2 = await musicbrainz_client.lookup_artist("abc")

    assert r1 == r2
    assert fake_call.call_count == 1, (
        f"expected exactly 1 network call after 2 lookups; got "
        f"{fake_call.call_count} — cache miss"
    )


@pytest.mark.asyncio
async def test_lookup_artist_returns_none_on_404(db_with_phase7, monkeypatch):
    """``musicbrainzngs.ResponseError`` (e.g. 404) → returns None, doesn't
    cache, doesn't raise.
    """
    from app.services import musicbrainz_client

    fake_call = MagicMock(
        side_effect=musicbrainz_client.musicbrainzngs.ResponseError("404 not found"),
    )
    monkeypatch.setattr(
        musicbrainz_client.musicbrainzngs, "get_artist_by_id", fake_call,
    )

    result = await musicbrainz_client.lookup_artist("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_artist_returns_none_on_unexpected_exception(
    db_with_phase7, monkeypatch,
):
    """A surprise exception (e.g. ValueError) → returns None (logged but
    NEVER raised — discovery cron must keep running).
    """
    from app.services import musicbrainz_client

    fake_call = MagicMock(side_effect=ValueError("malformed"))
    monkeypatch.setattr(
        musicbrainz_client.musicbrainzngs, "get_artist_by_id", fake_call,
    )

    result = await musicbrainz_client.lookup_artist("any")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_artist_by_name_uses_search_endpoint(
    db_with_phase7, monkeypatch,
):
    """``lookup_artist_by_name("Four Tet")`` calls ``search_artists`` with
    ``query="artist:Four Tet"`` and returns the ``artist-list``.
    """
    from app.services import musicbrainz_client

    captured = {}
    fake_envelope = {
        "artist-list": [{"id": "abc", "name": "Four Tet"}],
    }

    def _fake_search(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake_envelope

    monkeypatch.setattr(
        musicbrainz_client.musicbrainzngs, "search_artists", _fake_search,
    )

    result = await musicbrainz_client.lookup_artist_by_name("Four Tet")
    assert isinstance(result, list)
    assert result == fake_envelope["artist-list"]
    # query was a kwarg
    assert captured["kwargs"].get("query") == "artist:Four Tet"


# ---------------------------------------------------------------------------
# Cache write-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_write_through_persists_to_sqlite(
    db_with_phase7, monkeypatch,
):
    """After ``lookup_artist("abc")``, a MusicBrainzCache row exists with
    mb_id="abc" and payload_json containing the artist payload.
    """
    from app.database import get_engine
    from app.services import musicbrainz_client

    fake_call = MagicMock(return_value={
        "artist": {"id": "abc", "name": "Four Tet"},
    })
    monkeypatch.setattr(
        musicbrainz_client.musicbrainzngs, "get_artist_by_id", fake_call,
    )

    await musicbrainz_client.lookup_artist("abc")

    with Session(get_engine()) as session:
        row = session.exec(
            select(MusicBrainzCache).where(MusicBrainzCache.mb_id == "abc")
        ).first()
        assert row is not None, "cache row not persisted"
        payload = json.loads(row.payload_json)
        assert payload["name"] == "Four Tet"
        assert payload["id"] == "abc"


@pytest.mark.asyncio
async def test_cache_hit_returns_payload_without_network(
    db_with_phase7, monkeypatch,
):
    """Pre-seed a MusicBrainzCache row; ``lookup_artist`` reads it and does
    NOT call ``musicbrainzngs.get_artist_by_id``.
    """
    from app.database import get_engine
    from app.services import musicbrainz_client

    with Session(get_engine()) as session:
        session.add(MusicBrainzCache(
            mb_id="preseed",
            payload_json=json.dumps({"id": "preseed", "name": "Cached Artist"}),
            cached_at="2026-05-17T00:00:00+00:00",
        ))
        session.commit()

    fake_call = MagicMock(return_value=None)
    monkeypatch.setattr(
        musicbrainz_client.musicbrainzngs, "get_artist_by_id", fake_call,
    )

    result = await musicbrainz_client.lookup_artist("preseed")
    assert result == {"id": "preseed", "name": "Cached Artist"}
    assert fake_call.call_count == 0, "cache miss when row was preseeded"
