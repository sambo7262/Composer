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
    # Real Plex rating keys are integers; use numeric strings so int() casts
    # succeed (the production code path passes these through int() before
    # fetchItem — see plex_playlist_service.py:_playlist_key_int).
    fake_plex_module._tracks = {
        k: FakeTrack(k) for k in ("101", "102", "103", "104", "105")
    }
    pl = FakePlaylist(
        rating_key="500",
        title="Composer · Workout",
        items=[FakeTrack("101"), FakeTrack("102"), FakeTrack("103")],
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
        desired_rating_keys=["102", "104", "105"],
    )

    # addItems was invoked once with [104, 105] (NOT [101, 103] removal).
    assert len(pl.add_calls) == 1
    added_keys = sorted(t.ratingKey for t in pl.add_calls[0])
    assert added_keys == ["104", "105"]
    # No removeItems calls.
    assert pl.remove_calls == []

    assert sorted(result.added) == ["104", "105"]
    assert "102" in result.unchanged
    # 101 and 103 are not in desired BUT they remain in the playlist (additive only).


# ---------------------------------------------------------------------------
# Test 4: post-push verify finds silently_dropped (Pitfall 6 / VIBE-12)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_playlist_items_post_push_verify_finds_silently_dropped(
    db_with_phase6, fake_plex_module
):
    """addItems succeeds but post-push re-fetch shows 205 missing → silently_dropped=[205]."""
    # Real Plex rating keys are integers (production code casts via int() before
    # fetchItem); use numeric strings here so the cast succeeds.
    fake_plex_module._tracks = {k: FakeTrack(k) for k in ("201", "202", "203", "204", "205")}
    pl = FakePlaylist(
        rating_key="600",
        title="Composer · Late Night",
        items=[FakeTrack("201"), FakeTrack("202"), FakeTrack("203")],
    )

    # Override addItems on this specific instance to drop 205.
    original_add = pl.addItems
    def _add_drop_205(items_to_add):
        # Skip any item whose ratingKey is "205"
        actually_added = [it for it in items_to_add if it.ratingKey != "205"]
        original_add(actually_added)
    pl.addItems = _add_drop_205

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
        desired_rating_keys=["202", "204", "205"],
    )

    # 205 was silently dropped on first push; retry tried again and still missing.
    assert "205" in result.silently_dropped
    assert "205" in result.retried


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
# Phase 6.2 Plan 02 Task 1 — archive_playlist suffix kwarg (WIZ-08 / D-26).
#
# T5 regression guard: default suffix is "(archived)" so the Phase 6.1
# first-deploy migration's 4-arg call site in app/main.py continues working
# byte-identically (the migration renames to "<name> (archived)").
#
# WIZ-08 caller (Plan 02 Task 2) passes a date-stamped suffix
# "(archived YYYY-MM-DD)" to avoid Plex playlist name collisions on
# repeated wizard resets.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_playlist_default_suffix_unchanged(db_with_phase6, fake_plex_module):
    """T5 / Phase 6.1 migration regression: 4-arg call (no suffix kwarg)
    MUST still rename to "<current_name> (archived)" byte-identically.
    """
    pl = FakePlaylist("710", "Composer · MigrationTarget", [])
    fake_plex_module._playlists = {"710": pl}

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="710",
            composer_name="Composer · MigrationTarget",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import archive_playlist

    # 4-arg signature (the Phase 6.1 migration shape) — no suffix kwarg.
    await archive_playlist(
        "http://plex.local",
        "token-X",
        "710",
        "Composer · MigrationTarget",
    )
    assert pl.edit_title_calls == ["Composer · MigrationTarget (archived)"]


@pytest.mark.asyncio
async def test_archive_playlist_custom_suffix(db_with_phase6, fake_plex_module):
    """WIZ-08 / D-26: caller-supplied suffix is used verbatim."""
    pl = FakePlaylist("720", "Composer · Workout", [])
    fake_plex_module._playlists = {"720": pl}

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="720",
            composer_name="Composer · Workout",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import archive_playlist

    await archive_playlist(
        plex_url="http://plex.local",
        plex_token="token-X",
        playlist_rating_key="720",
        current_name="Composer · Workout",
        suffix="(archived 2026-05-12)",
    )
    assert pl.edit_title_calls == ["Composer · Workout (archived 2026-05-12)"]


@pytest.mark.asyncio
async def test_archive_playlist_with_suffix_still_deletes_managed_playlist_row(
    db_with_phase6, fake_plex_module
):
    """The ManagedPlaylist row MUST be deleted regardless of suffix kwarg —
    archive semantics are unchanged.
    """
    pl = FakePlaylist("730", "Composer · ToArchive", [])
    fake_plex_module._playlists = {"730": pl}

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="730",
            composer_name="Composer · ToArchive",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import archive_playlist

    await archive_playlist(
        plex_url="http://plex.local",
        plex_token="token-X",
        playlist_rating_key="730",
        current_name="Composer · ToArchive",
        suffix="(archived 2026-05-12)",
    )

    from app.database import get_engine
    with Session(get_engine()) as fresh:
        row = fresh.exec(
            select(ManagedPlaylist).where(ManagedPlaylist.plex_rating_key == "730")
        ).first()
        assert row is None


@pytest.mark.asyncio
async def test_archive_playlist_token_sanitization_preserved(db_with_phase6, fake_plex_module):
    """T-06-02-03: error messages must redact the Plex token. Regression guard
    so the suffix-kwarg extension does not regress the existing _sanitize path.
    """
    pl = FakePlaylist("740", "Composer · SanitizeMe", [])
    fake_plex_module._playlists = {"740": pl}

    # Make editTitle raise an exception whose str() contains the token.
    token = "super-secret-token-XYZ"

    def _boom(_new_title):
        raise RuntimeError(f"plex error involving token={token}")

    pl.editTitle = _boom

    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="740",
            composer_name="Composer · SanitizeMe",
        )
    )
    db_with_phase6.commit()

    from app.services.plex_playlist_service import archive_playlist

    with pytest.raises(RuntimeError) as exc_info:
        await archive_playlist(
            plex_url="http://plex.local",
            plex_token=token,
            playlist_rating_key="740",
            current_name="Composer · SanitizeMe",
            suffix="(archived 2026-05-12)",
        )
    msg = str(exc_info.value)
    assert token not in msg, f"Token leaked in exception: {msg}"
    assert "[REDACTED]" in msg


@pytest.mark.asyncio
async def test_archive_playlist_signature_accepts_kwarg_at_position_5(
    db_with_phase6, fake_plex_module
):
    """The function signature MUST accept `suffix` as positional-or-keyword
    so the Phase 6.1 migration's 4-arg call AND Plan 02's keyword-arg call
    both compile.
    """
    import inspect

    from app.services.plex_playlist_service import archive_playlist

    sig = inspect.signature(archive_playlist)
    params = list(sig.parameters.values())
    # 5 params: plex_url, plex_token, playlist_rating_key, current_name, suffix
    assert len(params) == 5, (
        f"archive_playlist must have exactly 5 parameters (4 existing + suffix); "
        f"got {len(params)}: {[p.name for p in params]}"
    )
    suffix_param = params[4]
    assert suffix_param.name == "suffix"
    # Must be POSITIONAL_OR_KEYWORD (not KEYWORD_ONLY) so existing 4-arg callers
    # remain valid (they don't pass suffix at all, defaulting to "(archived)").
    assert suffix_param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD, (
        f"suffix must be POSITIONAL_OR_KEYWORD; got {suffix_param.kind}"
    )
    assert suffix_param.default == "(archived)", (
        f"suffix default must be '(archived)' for Phase 6.1 migration "
        f"back-compat; got {suffix_param.default!r}"
    )

    # Sanity check: the function works when called with no suffix (4-arg form).
    pl = FakePlaylist("750", "Composer · SignatureCheck", [])
    fake_plex_module._playlists = {"750": pl}
    from app.models.vibe import ManagedPlaylist
    db_with_phase6.add(
        ManagedPlaylist(
            kind="vibe",
            plex_rating_key="750",
            composer_name="Composer · SignatureCheck",
        )
    )
    db_with_phase6.commit()
    await archive_playlist(
        "http://plex.local", "token-X", "750", "Composer · SignatureCheck",
    )
    assert pl.edit_title_calls == ["Composer · SignatureCheck (archived)"]


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


# ---------------------------------------------------------------------------
# Regression: update_playlist_items MUST pass int (not bare str) to fetchItem
# ---------------------------------------------------------------------------
#
# PlexAPI 4.18.1's `PlexServer.fetchItem(ekey)` does naive URL concatenation
# when ekey is a bare string without a leading `/`, producing broken URLs like
# `http://host:32400` + `'77830'` → `http://host:3240077830` → InvalidURL.
# With an int, PlexAPI builds the canonical `/library/metadata/{int}` path.
#
# `ManagedPlaylist.plex_rating_key` is TEXT in SQLite, so naive callers pass
# the raw str → silent prod failure (caught at suggestions_service.py:853 by
# the broad except, logged as `update_playlist_items failed`, no further
# attempt). Discovered on 2026-05-16 NAS UAT after Phase 7.1's SQL refill mirror
# of the deleted Phase 7 LLM-ranking push branch went live; root-caused via
# the [PLEX-PUSH-DEBUG] instrumentation. See
# .planning/debug/plex-push-not-firing.md.
#
# The existing FakePlexServer.fetchItem accepts any arg (does `key = str(key)`
# on entry) — that's why the bug slipped through unit tests. This regression
# test uses a strict fake that rejects bare-string keys, mimicking PlexAPI's
# real behavior. If anyone ever drops the `int(...)` cast in
# plex_playlist_service.py:_get_current_keys or :_add_items, this test fails.

class StrictFakePlexServer(FakePlexServer):
    """Fake that rejects bare-string keys, mimicking PlexAPI URL concat behavior."""
    _fetchitem_arg_types: list = []

    def fetchItem(self, key):
        # Record the argument type for assertion.
        type(self)._fetchitem_arg_types.append(type(key).__name__)
        # Real PlexAPI 4.18.1 builds a broken URL for bare-string ekey lacking
        # a `/` prefix. Simulate by rejecting str inputs that aren't paths.
        if isinstance(key, str) and not key.startswith("/"):
            raise ValueError(
                f"StrictFakePlexServer.fetchItem: bare-string key {key!r} "
                "would trigger naive URL concat in real PlexAPI. "
                "Pass int or path-prefixed str."
            )
        # int → look up by str() in the existing maps (test convenience).
        lookup = str(key) if isinstance(key, int) else key
        if lookup in self._playlists:
            return self._playlists[lookup]
        if lookup in self._tracks:
            return self._tracks[lookup]
        raise KeyError(f"StrictFakePlexServer.fetchItem: unknown key {key!r}")


@pytest.fixture
def strict_fake_plex_module(monkeypatch):
    class _FreshStrict(StrictFakePlexServer):
        _playlists = {}
        _tracks = {}
        _create_returns_key = "999"
        _drop_keys_on_create = set()
        _drop_keys_on_addItems = set()
        _fetchitem_arg_types = []

    monkeypatch.setattr(
        "app.services.plex_playlist_service.PlexServer",
        _FreshStrict,
    )
    return _FreshStrict


@pytest.mark.asyncio
async def test_update_playlist_items_passes_int_to_fetchitem(
    db_with_phase6, strict_fake_plex_module
):
    """REGRESSION (2026-05-16 NAS UAT): every fetchItem call inside
    update_playlist_items MUST receive an int rating key. The strict fake
    raises ValueError on bare-string keys to mimic PlexAPI's naive URL concat.
    """
    strict_fake_plex_module._tracks = {
        k: FakeTrack(k) for k in ("1001", "1002", "1003", "1004", "1005")
    }
    pl = FakePlaylist(
        rating_key="500",
        title="Composer · Workout",
        items=[FakeTrack("1001"), FakeTrack("1002"), FakeTrack("1003")],
    )
    strict_fake_plex_module._playlists = {"500": pl}

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

    # Note: caller passes STRING rating keys (this is the production shape —
    # the values come from a TEXT column). The fix lives INSIDE
    # update_playlist_items, which must cast to int before fetchItem.
    result = await update_playlist_items(
        plex_url="http://plex.local",
        plex_token="token-X",
        playlist_rating_key="500",        # str, as production passes
        desired_rating_keys=["1002", "1004", "1005"],   # str, as production
    )

    # The strict fake would have raised ValueError if any fetchItem call got
    # a bare str. Reaching this point means all fetchItem calls received int.
    # Additionally assert it explicitly via the recorded arg types.
    assert strict_fake_plex_module._fetchitem_arg_types, \
        "Expected at least one fetchItem call; recorded zero."
    assert all(t == "int" for t in strict_fake_plex_module._fetchitem_arg_types), \
        f"Expected every fetchItem call to receive int; got {strict_fake_plex_module._fetchitem_arg_types}"

    # Sanity: additive push still works correctly.
    assert sorted(result.added) == ["1004", "1005"]
    assert "1002" in result.unchanged
