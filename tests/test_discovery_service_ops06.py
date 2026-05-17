"""Phase 8 Plan 05 Task 2 — OPS-06 legacy-playlist guard.

OPS-06: legacy v1-generated Plex playlists (no ``Composer · `` prefix
+ no ``ManagedPlaylist`` row) must never be touched by Composer and
must never be cited as a "this artist is in your library" reason on
/discover.

Specifically:

1. ``is_managed_playlist(rating_key)`` is the dual-marker check. It
   returns True iff a ``ManagedPlaylist`` row exists for the rating
   key. Callers MUST also verify the Plex playlist's title starts
   with ``"Composer · "`` per the comment at line 161 of
   ``plex_playlist_service.py`` — both markers must hold or the
   playlist is left alone.

2. ``compute_candidate_set_for_seed`` (Plan 02) reads in-library
   artists from ``composer.tracks`` (``Track.plex_artist_mbid``), NOT
   from any playlist-derived view. Tracks in the local DB are valid
   library signal regardless of which Plex playlist surfaced them.
   This test affirms the existing Plan 02 behavior is OPS-06-aligned.

3. NO Phase 8 surface (discover.html, discover_*.html partials,
   debug_discovery.html) should display "found in playlist X" as a
   discovery dedup reason. A grep-based static check enforces.

4. The ``_get_in_library_mbids_sync`` helper carries an OPS-06
   INVARIANT docstring annotation documenting the constraint for
   future maintainers.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel


@pytest.fixture
def db_phase8(test_engine) -> Generator[Session, None, None]:
    """Phase 8 schema fixture — full set of tables incl. ManagedPlaylist
    + discovery_*."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist, MigrationLog, SetupState, SlotInLog, TrackVibe, Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
    )
    from app.models.discovery import (  # noqa: F401
        CostMeterBaseline,
        DiscoveryAdd,
        DiscoveryCandidate,
        DiscoveryDismissed,
        MusicBrainzCache,
        WeeklyCronState,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


def _seed_managed_playlist(session, *, rating_key, name="Composer · Mellow",
                           vibe_id=None, kind="vibe"):
    """Helper — write a ManagedPlaylist row.

    Production code writes ``kind="vibe"`` (Phase 6) or ``"suggestions"``
    (Phase 7). Field is ``composer_name`` per the actual model.
    """
    from app.models.vibe import ManagedPlaylist
    row = ManagedPlaylist(
        plex_rating_key=rating_key,
        composer_name=name,
        vibe_id=vibe_id,
        kind=kind,
    )
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------------------
# is_managed_playlist — dual-marker check
# ---------------------------------------------------------------------------


class TestIsManagedPlaylist:
    def test_managed_playlist_recognised(self, db_phase8):
        """A ManagedPlaylist row with a Composer · title → True at the
        DB-side half of the dual-marker check.

        Note: ``is_managed_playlist`` only checks the DB-side half
        (a ManagedPlaylist row exists). The TITLE half is the caller's
        responsibility per the docstring at line 161. The test pins
        the documented contract.
        """
        from app.services.plex_playlist_service import is_managed_playlist
        _seed_managed_playlist(
            db_phase8, rating_key="rk-1", name="Composer · Late Night",
        )
        assert is_managed_playlist("rk-1") is True

    def test_legacy_playlist_not_recognised_no_managedplaylist_row(
        self, db_phase8,
    ):
        """No ManagedPlaylist row → False, regardless of title.

        This is the primary OPS-06 guard: legacy v1-generated Plex
        playlists never have a corresponding ManagedPlaylist row
        (they were never written by v2 code), so the DB-side check
        returns False and they're left alone.
        """
        from app.services.plex_playlist_service import is_managed_playlist
        # No row seeded for rk-legacy.
        assert is_managed_playlist("rk-legacy") is False

    def test_callers_must_verify_composer_prefix(self, db_phase8):
        """A ManagedPlaylist row whose name lacks ``Composer · `` is a
        record violation — production code never writes such a row
        (D-24 invariant raises ValueError in :func:`create_playlist`).

        This test pins the BEHAVIOR of ``is_managed_playlist`` —
        because it only checks the DB-side half, it does NOT
        independently filter on title. The full dual-marker check
        requires the caller to verify the title prefix.
        """
        from app.services.plex_playlist_service import is_managed_playlist
        # Simulate a corrupt row (production code would have raised
        # ValueError before writing this).
        _seed_managed_playlist(
            db_phase8, rating_key="rk-corrupt", name="Legacy Mix",
        )
        # The DB-side half still returns True — the caller MUST
        # verify the title prefix to complete the dual-marker check.
        # This is by-design — the assertion documents the contract.
        assert is_managed_playlist("rk-corrupt") is True


# ---------------------------------------------------------------------------
# compute_candidate_set_for_seed — composer.tracks IS the library signal
# ---------------------------------------------------------------------------


class TestOps06LibrarySignalSource:
    def test_compute_candidate_set_uses_track_table_not_playlist(
        self, db_phase8,
    ):
        """``_get_in_library_mbids_sync`` reads ``Track.plex_artist_mbid``
        (composer.tracks) — NOT a Playlist or ManagedPlaylist join.

        Seeds a Track row with plex_artist_mbid='mb-known'. The OPS-06
        invariant says: this Track IS a valid library signal, regardless
        of which (or whether any) Plex playlist surfaced it.
        """
        from app.models.track import Track
        from app.services.discovery_service import _get_in_library_mbids_sync

        track = Track(
            plex_rating_key="rk-track-1", title="Known Track",
            artist="Known Artist", plex_artist_mbid="mb-known",
        )
        db_phase8.add(track)
        db_phase8.commit()

        # No ManagedPlaylist row exists — yet the Track is still in the
        # in-library set (because composer.tracks is the source of truth).
        in_library = _get_in_library_mbids_sync()
        assert "mb-known" in in_library

    def test_legacy_playlist_does_not_contribute_to_library_set(
        self, db_phase8,
    ):
        """OPS-06 / Pitfall 12 — a ManagedPlaylist row WITHOUT any Track
        rows in composer.tracks must NOT contribute MBIDs to the dedup
        set.

        Seeds a ManagedPlaylist with no associated tracks; the in-library
        set returned by ``_get_in_library_mbids_sync`` is empty.
        """
        from app.services.discovery_service import _get_in_library_mbids_sync

        _seed_managed_playlist(
            db_phase8, rating_key="rk-mp-1", name="Composer · Empty Vibe",
        )
        # No tracks seeded.
        in_library = _get_in_library_mbids_sync()
        assert in_library == set()


# ---------------------------------------------------------------------------
# OPS-06 docstring invariant — guard rail for future maintainers
# ---------------------------------------------------------------------------


class TestOps06DocstringAnnotation:
    def test_ops06_invariant_documented_in_service(self):
        """``app/services/discovery_service.py`` carries an OPS-06
        INVARIANT comment so future contributors don't accidentally
        join _get_in_library_mbids_sync to ManagedPlaylist.
        """
        path = Path("app/services/discovery_service.py")
        text = path.read_text()
        assert "OPS-06" in text, "OPS-06 mention missing from discovery_service"
        # The invariant token must be present somewhere in the module
        # so a regression refactor doesn't silently lose it.
        assert "INVARIANT" in text, "INVARIANT marker missing"


# ---------------------------------------------------------------------------
# Grep — no Phase 8 surface cites a Plex playlist as a library reason
# ---------------------------------------------------------------------------


class TestOps06InvariantGrep:
    """OPS-06 — no Phase 8 user-facing surface should display
    'in your playlist X' / 'from playlist Y' as a positive
    library-membership reason for a discovery candidate.

    The dedup signal in Phase 8 is composer.tracks (track-level),
    never playlist-level. Legacy v1 playlists never contribute.
    """

    SURFACES = [
        Path("app/templates/pages/discover.html"),
        Path("app/templates/partials/discover_artist_card.html"),
        Path("app/templates/partials/discover_vibe_section.html"),
        Path("app/templates/partials/discover_status_row.html"),
        Path("app/templates/pages/debug_discovery.html"),
    ]

    def test_no_template_cites_playlist_as_library_signal(self):
        forbidden_pattern = re.compile(
            r"(?i)\b(in|from)\s+(your\s+|the\s+)?(playlist|library\s+playlist)\b"
        )
        for tpl in self.SURFACES:
            if not tpl.exists():
                continue
            text = tpl.read_text()
            matches = forbidden_pattern.findall(text)
            assert not matches, (
                f"{tpl} cites 'in/from your playlist X' as a library "
                f"signal — violates OPS-06. Matches: {matches!r}"
            )

    def test_no_partial_uses_managedplaylist_name_in_user_facing_string(self):
        """Defensive — even if a partial reads `ManagedPlaylist.name`,
        it must not display the playlist title as a library-membership
        reason. Grep for the canonical Composer prefix in user-rendered
        contexts; the only allowed surfaces are /settings + tests."""
        for tpl in self.SURFACES:
            if not tpl.exists():
                continue
            text = tpl.read_text()
            # ``Composer · `` is allowed as documentation in comments
            # (Jinja {# ... #} comments are stripped at render time).
            # The forbidden case is a rendered Jinja expression that
            # outputs a playlist name as a "found in" reason.
            # We approximate by checking for the literal phrase
            # "found in" in non-comment contexts.
            # Strip Jinja comments first.
            stripped = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
            assert "found in playlist" not in stripped.lower(), (
                f"{tpl} contains 'found in playlist' outside a Jinja "
                f"comment — violates OPS-06."
            )
