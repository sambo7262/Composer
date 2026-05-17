"""Phase 8.1 quick fix (260517-lyw) — one-shot DiscoveryCandidate mb_id
dedup migration tests.

Mirrors :mod:`tests.test_phase_08_discovery_bootstrap` structure (same
imports style, same ``_run_async`` helper at lines 192-197, same
``fresh_db`` fixture from ``tests/conftest.py:131``).

Covers:
- Lowest-llm_rank-wins collapse with NULL → 9999 coercion (id tiebreak).
- Idempotency: second run is a no-op via MigrationLog gate.
- Zero-duplicates case: stamps the gate even when nothing to delete.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlmodel import select

from app.models.discovery import DiscoveryCandidate
from app.models.track import Track
from app.models.vibe import MigrationLog, Vibe
from app.services.discovery_service import (
    PHASE_08_1_MIGRATION_ID,
    run_phase_08_1_discovery_dedupe_mb_id,
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
    seed_track_id: int = 1,
    seed_vibe_id: int = 1,
):
    """Helper — build a DiscoveryCandidate row sharing the same seed
    track + vibe so the FK constraints stay satisfied with one parent
    pair (defined in _seed_parent_rows).
    """
    return DiscoveryCandidate(
        mb_id=mb_id,
        artist_name=f"Artist-{mb_id}",
        seed_track_id=seed_track_id,
        seed_vibe_id=seed_vibe_id,
        mb_listener_count=None,
        popularity_gate_pass=True,
        llm_rank=llm_rank,
        llm_rationale=f"rank={llm_rank!r}",
        factual_hook=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def test_dedupe_keeps_lowest_llm_rank_and_stamps(fresh_db):
    """Four rows for mb_id='A' with llm_ranks [3, 1, 2, None] collapse
    to one — winner has rank=1 (NOT the None row which COALESCEs to
    9999). Unique mb_id='B' row is untouched. MigrationLog stamped.
    """
    _seed_parent_rows(fresh_db)
    # Pre-seed 4 duplicates for mb_id='A' (ranks 3, 1, 2, None).
    fresh_db.add(_make_candidate("A", 3))
    fresh_db.add(_make_candidate("A", 1))
    fresh_db.add(_make_candidate("A", 2))
    fresh_db.add(_make_candidate("A", None))
    # 1 unique mb_id='B' row.
    fresh_db.add(_make_candidate("B", 5))
    fresh_db.commit()

    _run_async(run_phase_08_1_discovery_dedupe_mb_id())

    fresh_db.expire_all()
    rows = fresh_db.exec(select(DiscoveryCandidate)).all()
    assert len(rows) == 2, (
        f"Expected 2 rows after dedup (A, B); got {len(rows)}: "
        f"{[(r.mb_id, r.llm_rank) for r in rows]!r}"
    )

    by_mbid = {r.mb_id: r for r in rows}
    assert by_mbid["A"].llm_rank == 1, (
        f"Winner for mb_id='A' must be the rank=1 row "
        f"(NULL → 9999); got rank={by_mbid['A'].llm_rank!r}"
    )
    # seed_vibe_id preserved on the winner — migration MUST NOT mutate it.
    assert by_mbid["A"].seed_vibe_id == 1
    # Untouched unique mb_id='B'.
    assert by_mbid["B"].llm_rank == 5

    log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_1_MIGRATION_ID
        )
    ).first()
    assert log is not None
    assert log.completed_at is not None, (
        "MigrationLog.completed_at must be stamped after successful run."
    )


def test_dedupe_is_idempotent(fresh_db):
    """Second run via the MigrationLog gate is a no-op — row count and
    completed_at stamp both unchanged.
    """
    _seed_parent_rows(fresh_db)
    fresh_db.add(_make_candidate("A", 3))
    fresh_db.add(_make_candidate("A", 1))
    fresh_db.add(_make_candidate("A", 2))
    fresh_db.add(_make_candidate("A", None))
    fresh_db.add(_make_candidate("B", 5))
    fresh_db.commit()

    _run_async(run_phase_08_1_discovery_dedupe_mb_id())
    fresh_db.expire_all()
    rows_after_first = fresh_db.exec(select(DiscoveryCandidate)).all()
    first_count = len(rows_after_first)
    first_log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_1_MIGRATION_ID
        )
    ).first()
    first_completed_at = first_log.completed_at

    # Run a second time — gate must short-circuit.
    _run_async(run_phase_08_1_discovery_dedupe_mb_id())
    fresh_db.expire_all()
    rows_after_second = fresh_db.exec(select(DiscoveryCandidate)).all()
    second_log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_1_MIGRATION_ID
        )
    ).first()

    assert len(rows_after_second) == first_count, (
        f"Second run mutated row count: {first_count} → "
        f"{len(rows_after_second)}; gate failed to short-circuit."
    )
    assert second_log.completed_at == first_completed_at, (
        "Second run must not re-stamp completed_at; gate is broken."
    )


def test_dedupe_handles_zero_duplicates(fresh_db):
    """Three rows with distinct mb_ids — migration is effectively a
    no-op on the data, but MUST still stamp the gate so subsequent
    boots short-circuit.
    """
    _seed_parent_rows(fresh_db)
    fresh_db.add(_make_candidate("A", 1))
    fresh_db.add(_make_candidate("B", 2))
    fresh_db.add(_make_candidate("C", 3))
    fresh_db.commit()

    _run_async(run_phase_08_1_discovery_dedupe_mb_id())

    fresh_db.expire_all()
    rows = fresh_db.exec(select(DiscoveryCandidate)).all()
    assert len(rows) == 3, (
        f"Zero-duplicates pre-state must remain at 3 rows; got {len(rows)}"
    )
    mbids = {r.mb_id for r in rows}
    assert mbids == {"A", "B", "C"}

    log = fresh_db.exec(
        select(MigrationLog).where(
            MigrationLog.phase_id == PHASE_08_1_MIGRATION_ID
        )
    ).first()
    assert log is not None
    assert log.completed_at is not None, (
        "Even with zero duplicates, the migration must stamp the gate "
        "so subsequent boots short-circuit."
    )
