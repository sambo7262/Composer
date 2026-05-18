"""Phase 8.2 quick fix (260517-n7j) — one-shot DiscoveryCandidate
artist_name dedup migration tests.

Mirrors :mod:`tests.test_phase_08_1_discovery_dedupe_mb_id` structure
(same imports style, same ``_run_async`` helper, same ``fresh_db``
fixture from ``tests/conftest.py:131``).

Stacks on top of the Phase 8.1 mb_id dedup — the artist_name dedup is a
strict SUPERSET: it catches the MusicBrainz alias/split case where a
single human artist surfaces with multiple distinct mb_ids (group/solo
aliases, splits, disambiguations, regional variants) that 8.1 cannot
collapse.

Covers:
- Same-artist_name + different-mb_ids → collapse to lowest llm_rank winner.
- Normalization is case-insensitive AND strips whitespace (casefold + strip).
- Idempotency: second run is a no-op via MigrationLog gate.
- Empty / whitespace-only artist_names are NEVER deleted (pass-through).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlmodel import select

from app.models.discovery import DiscoveryCandidate
from app.models.track import Track
from app.models.vibe import MigrationLog, Vibe
from app.services.discovery_service import (
    PHASE_08_2_MIGRATION_ID,
    run_phase_08_2_discovery_dedupe_artist_name,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _seed_parent_rows(session) -> None:
    """Seed the Track + Vibe parents the DiscoveryCandidate FK constraints
    require. SQLite has PRAGMA foreign_keys=ON in both the test engine
    (conftest.py:52) and the prod engine (database.py:33), so missing
    parents would raise IntegrityError on insert.
    """
    now = datetime.now(timezone.utc).isoformat()
    session.add(Vibe(id=1, name="V1", created_at=now))
    session.add(Track(
        id=1, plex_rating_key="rk-1", title="T1", artist="A1",
        user_rating=8.0,
    ))
    session.commit()


def _make_candidate(
    mb_id: str,
    llm_rank,
    artist_name: str,
    seed_track_id: int = 1,
    seed_vibe_id: int = 1,
):
    """Helper — build a DiscoveryCandidate row sharing the same seed
    track + vibe so the FK constraints stay satisfied with one parent
    pair (defined in _seed_parent_rows).

    Unlike the Phase 8.1 helper this takes ``artist_name`` as a parameter
    so tests can exercise the normalization rule
    (``(artist_name or "").strip().casefold()``) explicitly.
    """
    return DiscoveryCandidate(
        mb_id=mb_id,
        artist_name=artist_name,
        seed_track_id=seed_track_id,
        seed_vibe_id=seed_vibe_id,
        mb_listener_count=None,
        popularity_gate_pass=True,
        llm_rank=llm_rank,
        llm_rationale=f"rank={llm_rank!r} name={artist_name!r}",
        factual_hook=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def test_dedupe_collapses_same_artist_name_with_different_mb_ids_and_stamps(
    fresh_db,
):
    """Three rows share ``artist_name='Bonobo'`` but have distinct mb_ids
    (the MusicBrainz alias/split case the Phase 8.1 mb_id dedup cannot
    catch). They collapse to ONE survivor — the lowest ``llm_rank`` row.
    The separately-unique 'Aphex Twin' row is untouched. MigrationLog
    stamped.
    """
    _seed_parent_rows(fresh_db)
    # 3 Bonobo rows with distinct mb_ids; ranks 3, 1, 2.
    fresh_db.add(_make_candidate("bono-1", 3, "Bonobo"))
    fresh_db.add(_make_candidate("bono-2", 1, "Bonobo"))
    fresh_db.add(_make_candidate("bono-3", 2, "Bonobo"))
    # 1 unique row.
    fresh_db.add(_make_candidate("aphex-1", 4, "Aphex Twin"))
    fresh_db.commit()

    _run_async(run_phase_08_2_discovery_dedupe_artist_name())

    fresh_db.expire_all()
    rows = fresh_db.exec(select(DiscoveryCandidate)).all()
    assert len(rows) == 2, (
        f"Expected 2 rows after artist_name dedup; got {len(rows)}: "
        f"{[(r.mb_id, r.artist_name, r.llm_rank) for r in rows]!r}"
    )

    by_name = {r.artist_name: r for r in rows}
    assert "Bonobo" in by_name and "Aphex Twin" in by_name, (
        f"Expected survivors {{Bonobo, Aphex Twin}}; got {sorted(by_name)!r}"
    )
    assert by_name["Bonobo"].mb_id == "bono-2", (
        f"Bonobo winner must be the rank=1 row (mb_id='bono-2'); "
        f"got mb_id={by_name['Bonobo'].mb_id!r}"
    )
    assert by_name["Bonobo"].llm_rank == 1
    # Untouched row.
    assert by_name["Aphex Twin"].mb_id == "aphex-1"
    assert by_name["Aphex Twin"].llm_rank == 4

    log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_2_MIGRATION_ID
        )
    ).first()
    assert log is not None
    assert log.completed_at is not None, (
        "MigrationLog.completed_at must be stamped after successful run."
    )


def test_dedupe_is_case_insensitive_and_strip(fresh_db):
    """Four rows with ``artist_name`` values that differ only in casing /
    surrounding whitespace MUST normalize to one bucket. The lowest-rank
    row wins (rank=1), proving sort runs and a non-first row can win.
    """
    _seed_parent_rows(fresh_db)
    # Each row has a distinct mb_id (so the 8.1 mb_id dedup would NOT
    # catch them) and a distinct rank to prove the sort works.
    fresh_db.add(_make_candidate("mb-1", 4, "Bonobo"))
    fresh_db.add(_make_candidate("mb-2", 1, "BONOBO"))
    fresh_db.add(_make_candidate("mb-3", 2, " bonobo "))
    fresh_db.add(_make_candidate("mb-4", 3, "  Bonobo\t"))
    fresh_db.commit()

    _run_async(run_phase_08_2_discovery_dedupe_artist_name())

    fresh_db.expire_all()
    rows = fresh_db.exec(select(DiscoveryCandidate)).all()
    assert len(rows) == 1, (
        f"All 4 case/whitespace variants must collapse to ONE row; "
        f"got {len(rows)}: "
        f"{[(r.mb_id, r.artist_name, r.llm_rank) for r in rows]!r}"
    )
    winner = rows[0]
    assert winner.llm_rank == 1, (
        f"Lowest llm_rank must win; got rank={winner.llm_rank!r}"
    )
    assert winner.mb_id == "mb-2"


def test_dedupe_is_idempotent(fresh_db):
    """Second run via the MigrationLog gate is a no-op — row count and
    completed_at stamp both unchanged. Mirrors the Phase 8.1 idempotency
    test exactly.
    """
    _seed_parent_rows(fresh_db)
    # Same 4-Bonobo + 1-Aphex setup as test 1.
    fresh_db.add(_make_candidate("bono-1", 3, "Bonobo"))
    fresh_db.add(_make_candidate("bono-2", 1, "Bonobo"))
    fresh_db.add(_make_candidate("bono-3", 2, "Bonobo"))
    fresh_db.add(_make_candidate("aphex-1", 4, "Aphex Twin"))
    fresh_db.commit()

    _run_async(run_phase_08_2_discovery_dedupe_artist_name())
    fresh_db.expire_all()
    rows_after_first = fresh_db.exec(select(DiscoveryCandidate)).all()
    first_count = len(rows_after_first)
    first_log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_2_MIGRATION_ID
        )
    ).first()
    first_completed_at = first_log.completed_at

    # Run a second time — gate must short-circuit.
    _run_async(run_phase_08_2_discovery_dedupe_artist_name())
    fresh_db.expire_all()
    rows_after_second = fresh_db.exec(select(DiscoveryCandidate)).all()
    second_log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_2_MIGRATION_ID
        )
    ).first()

    assert len(rows_after_second) == first_count, (
        f"Second run mutated row count: {first_count} → "
        f"{len(rows_after_second)}; gate failed to short-circuit."
    )
    assert second_log.completed_at == first_completed_at, (
        "Second run must not re-stamp completed_at; gate is broken."
    )


def test_dedupe_skips_empty_artist_names(fresh_db):
    """Rows with empty / whitespace-only ``artist_name`` are NEVER
    deleted — they pass through untouched. The migration still stamps
    the gate.

    NOTE: ``DiscoveryCandidate.artist_name`` is declared ``str`` (not
    Optional) so this test does not insert None — SQLModel/SQLAlchemy
    would reject it on flush. Empty + whitespace-only is sufficient
    coverage for the ``(artist_name or "").strip().casefold()`` empty-key
    skip branch.
    """
    _seed_parent_rows(fresh_db)
    fresh_db.add(_make_candidate("mb-1", 1, ""))
    fresh_db.add(_make_candidate("mb-2", 2, "   "))
    fresh_db.add(_make_candidate("mb-3", 3, "\t\n"))
    fresh_db.commit()

    _run_async(run_phase_08_2_discovery_dedupe_artist_name())

    fresh_db.expire_all()
    rows = fresh_db.exec(select(DiscoveryCandidate)).all()
    assert len(rows) == 3, (
        f"Empty/whitespace artist_name rows must NEVER be deleted; "
        f"got {len(rows)}: "
        f"{[(r.mb_id, repr(r.artist_name)) for r in rows]!r}"
    )
    mbids = {r.mb_id for r in rows}
    assert mbids == {"mb-1", "mb-2", "mb-3"}

    log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_2_MIGRATION_ID
        )
    ).first()
    assert log is not None
    assert log.completed_at is not None, (
        "Even when nothing is deleted, the migration must stamp the gate "
        "so subsequent boots short-circuit."
    )
