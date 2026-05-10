"""Phase 6 Plan 02 plex_playlist_service tests (D-24, D-27, OPS-06; VIBE-04, VIBE-12).

Tests cover:
- create_playlist persists ManagedPlaylist row + rejects missing Composer · prefix.
- update_playlist_items is additive only (Pitfall 5) and runs the post-push
  re-fetch verify (Pitfall 6 / VIBE-12).
- is_managed_playlist DB-side dual-marker check (D-27).
- remove_from_playlist single-track removal pre-flight check.
- archive_playlist renames + deletes ManagedPlaylist row (D-21).
- rename_playlist enforces Composer · prefix.

PlexServer is mocked at the boundary via monkeypatch.setattr —
``app.services.plex_playlist_service.PlexServer = FakePlexServer``.
"""
from __future__ import annotations

from typing import Generator
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, select


# ---------------------------------------------------------------------------
# Fake PlexServer + FakePlaylist + FakeTrack
# ---------------------------------------------------------------------------

class FakeTrack:
    def __init__(self, rating_key: str):
        self.ratingKey = rating_key


class FakePlaylist:
    def __init__(self, rating_key: str, title: str, items: list):
        self.ratingKey = rating_key
        self.title = title
        self._items = list(items)
        self.add_calls = []
        self.remove_calls = []
        self.edit_title_calls = []

    def items(self):
        return list(self._items)

    def addItems(self, items_to_add):
        self.add_calls.append(list(items_to_add))
        for it in items_to_add:
            self._items.append(it)

    def removeItems(self, items_to_remove):
        self.remove_calls.append(list(items_to_remove))
        keys_to_remove = {it.ratingKey for it in items_to_remove}
        self._items = [it for it in self._items if it.ratingKey not in keys_to_remove]

    def editTitle(self, new_title):
        self.edit_title_calls.append(new_title)
        self.title = new_title


class FakePlexServer:
    """Class fixture: subclasses tweak _playlists / _tracks before instantiation."""
    _playlists: dict = {}
    _tracks: dict = {}
    _create_returns_key: str = "999"
    _drop_keys_on_create: set = set()
    _drop_keys_on_addItems: set = set()

    def __init__(self, url, token, timeout=30):
        self.url = url
        self.token = token

    def fetchItem(self, key):
        # Either a playlist (when called with a playlist's ratingKey) or a track.
        key = str(key)
        if key in self._playlists:
            return self._playlists[key]
        if key in self._tracks:
            return self._tracks[key]
        raise KeyError(f"FakePlexServer.fetchItem: unknown key {key}")

    def fetchItems(self, path_or_keys):
        # Accepts either a path like /library/metadata/k1,k2,k3 or list of keys.
        if isinstance(path_or_keys, str) and "/" in path_or_keys:
            key_str = path_or_keys.rstrip("/").split("/")[-1]
            keys = key_str.split(",")
        elif isinstance(path_or_keys, str):
            keys = path_or_keys.split(",")
        else:
            keys = [str(k) for k in path_or_keys]
        return [self._tracks[str(k)] for k in keys if str(k) in self._tracks]

    def createPlaylist(self, title, items):
        # On success, register the new playlist with the configured key.
        actual_items = [
            it for it in items
            if it.ratingKey not in type(self)._drop_keys_on_create
        ]
        new = FakePlaylist(
            rating_key=type(self)._create_returns_key,
            title=title,
            items=actual_items,
        )
        type(self)._playlists[type(self)._create_returns_key] = new
        return new


@pytest.fixture
def fake_plex_module(monkeypatch):
    """Monkeypatch PlexServer in plex_playlist_service to use a fresh FakePlexServer subclass."""
    # Make a fresh subclass for each test so state doesn't leak.
    class _FreshFakePlex(FakePlexServer):
        _playlists = {}
        _tracks = {}
        _create_returns_key = "999"
        _drop_keys_on_create = set()
        _drop_keys_on_addItems = set()

    monkeypatch.setattr(
        "app.services.plex_playlist_service.PlexServer",
        _FreshFakePlex,
    )
    return _FreshFakePlex


@pytest.fixture
def db_with_phase6(test_engine) -> Generator[Session, None, None]:
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        SetupState,
        TrackVibe,
        Vibe,
    )

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


# ---------------------------------------------------------------------------
# Test 1: create_playlist persists ManagedPlaylist row.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_playlist_persists_managed_playlist_row(db_with_phase6, fake_plex_module):
    fake_plex_module._tracks = {
        "10": FakeTrack("10"),
        "11": FakeTrack("11"),
        "12": FakeTrack("12"),
    }
    fake_plex_module._create_returns_key = "999"

    from app.models.vibe import ManagedPlaylist
    from app.services.plex_playlist_service import create_playlist

    new_key = await create_playlist(
        plex_url="http://plex.local",
        plex_token="token-X",
        name="Composer · Test",
        rating_keys=["10", "11", "12"],
    )
    assert new_key == "999"

    from app.database import get_engine
    with Session(get_engine()) as fresh:
        row = fresh.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.plex_rating_key == "999")
        ).first()
        assert row is not None
        assert row.kind == "vibe"
        assert row.composer_name == "Composer · Test"
        assert row.track_count == 3
        assert row.last_pushed_at is not None


# ---------------------------------------------------------------------------
# Test 2: create_playlist rejects missing Composer · prefix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_playlist_rejects_missing_composer_prefix(db_with_phase6, fake_plex_module):
    fake_plex_module._tracks = {"10": FakeTrack("10")}
    from app.services.plex_playlist_service import create_playlist

    with pytest.raises(ValueError, match="Composer · "):
        await create_playlist(
            plex_url="http://plex.local",
            plex_token="token-X",
            name="Just A Name",
            rating_keys=["10"],
        )


# ---------------------------------------------------------------------------
# Test 3: update_playlist_items is additive only (Pitfall 5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_playlist_items_is_additive_only(db_with_phase6, fake_plex_module):
    """desired={b, d, e} on current={a, b, c} → addItems called with [d, e] only.

    Tracks `a` and `c` MUST stay (Pitfall 5 — never remove user-added tracks).
    """
    fake_plex_module._tracks = {
        k: FakeTrack(k) for k in ("a", "b", "c", "d", "e")
    }
    pl = FakePlaylist(
        rating_key="500",
        title="Composer · Workout",
        items=[FakeTrack("a"), FakeTrack("b"), FakeTrack("c")],
    )
    fake_plex_module._playlists = {"500": pl}

    # Persist ManagedPlaylist row so dual-marker check passes.
    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="500",
            composer_name="Composer · Workout",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import update_playlist_items

    result = await update_playlist_items(
        plex_url="http://plex.local",
        plex_token="token-X",
        playlist_rating_key="500",
        desired_rating_keys=["b", "d", "e"],
    )

    # addItems was invoked once with [d, e] (NOT [a, c] removal).
    assert len(pl.add_calls) == 1
    added_keys = sorted(t.ratingKey for t in pl.add_calls[0])
    assert added_keys == ["d", "e"]
    # No removeItems calls.
    assert pl.remove_calls == []

    assert sorted(result.added) == ["d", "e"]
    assert "b" in result.unchanged
    # a and c are not in desired BUT they remain in the playlist (additive only).


# ---------------------------------------------------------------------------
# Test 4: post-push verify finds silently_dropped (Pitfall 6 / VIBE-12)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_playlist_items_post_push_verify_finds_silently_dropped(
    db_with_phase6, fake_plex_module
):
    """addItems succeeds but post-push re-fetch shows e missing → silently_dropped=[e]."""
    fake_plex_module._tracks = {k: FakeTrack(k) for k in ("a", "b", "c", "d", "e")}
    pl = FakePlaylist(
        rating_key="600",
        title="Composer · Late Night",
        items=[FakeTrack("a"), FakeTrack("b"), FakeTrack("c")],
    )

    # Override addItems on this specific instance to drop `e`.
    original_add = pl.addItems
    def _add_drop_e(items_to_add):
        # Skip any item whose ratingKey is "e"
        actually_added = [it for it in items_to_add if it.ratingKey != "e"]
        original_add(actually_added)
    pl.addItems = _add_drop_e

    fake_plex_module._playlists = {"600": pl}

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="600",
            composer_name="Composer · Late Night",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import update_playlist_items

    result = await update_playlist_items(
        plex_url="http://plex.local",
        plex_token="token-X",
        playlist_rating_key="600",
        desired_rating_keys=["b", "d", "e"],
    )

    # e was silently dropped on first push; retry tried again and still missing.
    assert "e" in result.silently_dropped
    assert "e" in result.retried


# ---------------------------------------------------------------------------
# Test 5: pre-flight blocks unmanaged playlists (D-27 / OPS-06 / Pitfall 20)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_playlist_items_pre_flight_blocks_unmanaged(db_with_phase6, fake_plex_module):
    """is_managed_playlist returns False → update_playlist_items raises PermissionError."""
    fake_plex_module._tracks = {"x": FakeTrack("x")}
    fake_plex_module._playlists = {
        "777": FakePlaylist("777", "Some Random Playlist", [])
    }
    # No ManagedPlaylist row inserted — dual-marker check fails.

    from app.services.plex_playlist_service import update_playlist_items

    with pytest.raises(PermissionError, match="OPS-06|Pitfall 20"):
        await update_playlist_items(
            plex_url="http://plex.local",
            plex_token="token-X",
            playlist_rating_key="777",
            desired_rating_keys=["x"],
        )


# ---------------------------------------------------------------------------
# Test 6: is_managed_playlist DB row check
# ---------------------------------------------------------------------------
def test_is_managed_playlist_checks_db_row(db_with_phase6):
    """Without a ManagedPlaylist row → False; insert one → True."""
    from app.models.vibe import ManagedPlaylist
    from app.services.plex_playlist_service import is_managed_playlist

    assert is_managed_playlist("555") is False

    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="555",
            composer_name="Composer · X",
        )
    )
    db_with_phase6.commit()

    assert is_managed_playlist("555") is True


# ---------------------------------------------------------------------------
# Test 7: remove_from_playlist pre-flight blocks unmanaged
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remove_from_playlist_pre_flight_blocks_unmanaged(db_with_phase6, fake_plex_module):
    fake_plex_module._tracks = {"x": FakeTrack("x")}
    fake_plex_module._playlists = {"888": FakePlaylist("888", "Random", [FakeTrack("x")])}

    from app.services.plex_playlist_service import remove_from_playlist

    with pytest.raises(PermissionError, match="OPS-06|Pitfall 20"):
        await remove_from_playlist(
            plex_url="http://plex.local",
            plex_token="token-X",
            playlist_rating_key="888",
            rating_key="x",
        )


# ---------------------------------------------------------------------------
# Test 8: archive_playlist renames + deletes ManagedPlaylist row (D-21)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archive_playlist_renames_with_archived_suffix(db_with_phase6, fake_plex_module):
    pl = FakePlaylist("700", "Composer · Workout", [])
    fake_plex_module._playlists = {"700": pl}

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="700",
            composer_name="Composer · Workout",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import archive_playlist

    await archive_playlist(
        plex_url="http://plex.local",
        plex_token="token-X",
        playlist_rating_key="700",
        current_name="Composer · Workout",
    )
    assert pl.edit_title_calls == ["Composer · Workout (archived)"]

    from app.database import get_engine
    with Session(get_engine()) as fresh:
        row = fresh.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.plex_rating_key == "700")
        ).first()
        assert row is None, "ManagedPlaylist row must be deleted on archive (D-21)"


# ---------------------------------------------------------------------------
# Test 9: rename_playlist validates Composer · prefix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rename_playlist_validates_composer_prefix(db_with_phase6, fake_plex_module):
    pl = FakePlaylist("800", "Composer · Old", [])
    fake_plex_module._playlists = {"800": pl}

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="800",
            composer_name="Composer · Old",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import rename_playlist

    with pytest.raises(ValueError, match="Composer · "):
        await rename_playlist(
            plex_url="http://plex.local",
            plex_token="token-X",
            playlist_rating_key="800",
            new_name="My Renamed Playlist",
        )

    # Valid prefix → succeeds + updates ManagedPlaylist.composer_name.
    await rename_playlist(
        plex_url="http://plex.local",
        plex_token="token-X",
        playlist_rating_key="800",
        new_name="Composer · Renamed",
    )
    assert "Composer · Renamed" in pl.edit_title_calls

    from app.database import get_engine
    with Session(get_engine()) as fresh:
        row = fresh.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.plex_rating_key == "800")
        ).first()
        assert row is not None
        assert row.composer_name == "Composer · Renamed"
