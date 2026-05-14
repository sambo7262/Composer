"""Phase 7 Plan 02 Task 1 — LLM cost circuit breaker (SUGG-11 / Pitfall 11).

Three thresholds enforced via aggregate SELECT on LLMUsage:
- daily_quota: 50 LLM calls per UTC day (purpose starts with 'suggestions_')
- burst_window: 5 calls in any rolling 60s window
- debounce: no two calls within 30s of each other

Tests use direct SQL inserts to seed LLMUsage with known timestamps so the
``datetime.now()`` in ``check_or_raise`` is always "later" than the seeded
rows — no freezegun dependency needed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlmodel import Session, SQLModel, select


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def db_breaker(test_engine) -> Generator[Session, None, None]:
    """Create Phase 5 LLMUsage table for the breaker tests."""
    from app.models.llm_usage import LLMUsage  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def reset_breaker_state():
    """Reset module-level _status between tests (Phase 5 D-08 pattern)."""
    try:
        from app.services import llm_cost_breaker
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from app.services import llm_cost_breaker
        llm_cost_breaker._status = llm_cost_breaker.CostBreakerStatus()
    except (ImportError, AttributeError):
        pass


def _insert_usage(
    session: Session,
    purpose: str,
    called_at_iso: str,
    input_tokens: int = 100,
    cache_creation: int = 0,
    cache_read: int = 0,
    output_tokens: int = 50,
    cost_usd: float = 0.001,
) -> None:
    from app.models.llm_usage import LLMUsage

    session.add(
        LLMUsage(
            called_at=called_at_iso,
            purpose=purpose,
            model="claude-sonnet-4-6",
            input_tokens=input_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            output_tokens=output_tokens,
            cost_estimate_usd=cost_usd,
        )
    )
    session.commit()


class TestCheckOrRaise:
    def test_check_or_raise_passes_when_no_recent_calls(self, db_breaker):
        from app.services.llm_cost_breaker import check_or_raise

        # Empty LLMUsage table → must not raise.
        _run_async(check_or_raise())
        # Call several times rapidly — still no raise (no rows in table).
        _run_async(check_or_raise())
        _run_async(check_or_raise())

    def test_check_or_raise_blocks_when_daily_quota_50_reached(self, db_breaker):
        from app.services.llm_cost_breaker import (
            check_or_raise, CostBreakerTrippedError,
        )

        # Seed 50 rows with purpose='suggestions_rank' dated this UTC day,
        # but spaced > 60s apart so burst/debounce do NOT trip first.
        now = datetime.now(timezone.utc)
        # Anchor near midnight so all 50 rows fit "today" without hitting burst.
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(50):
            # Distribute across the day at 5min intervals; oldest is fresh-today.
            ts = midnight + timedelta(minutes=5 * i)
            # Ensure each row is older than the burst+debounce windows from "now".
            if (now - ts).total_seconds() < 120:
                ts = now - timedelta(seconds=300)
            _insert_usage(db_breaker, "suggestions_rank", ts.isoformat())

        with pytest.raises(CostBreakerTrippedError) as excinfo:
            _run_async(check_or_raise())
        assert excinfo.value.reason == "daily_quota_50"
        # `until` is the next UTC midnight.
        next_midnight = (
            now.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        assert excinfo.value.until.startswith(next_midnight.date().isoformat())

    def test_check_or_raise_blocks_when_burst_5_in_60s(self, db_breaker):
        from app.services.llm_cost_breaker import (
            check_or_raise, CostBreakerTrippedError,
        )

        # Seed 5 rows in the last 60 seconds.
        now = datetime.now(timezone.utc)
        for i in range(5):
            ts = now - timedelta(seconds=10 + i * 5)  # 10, 15, 20, 25, 30 ago
            _insert_usage(db_breaker, "suggestions_rank", ts.isoformat())

        with pytest.raises(CostBreakerTrippedError) as excinfo:
            _run_async(check_or_raise())
        assert excinfo.value.reason == "burst_5_per_60s"

    def test_check_or_raise_blocks_when_within_30s_debounce(self, db_breaker):
        from app.services.llm_cost_breaker import (
            check_or_raise, CostBreakerTrippedError,
        )

        # 1 row 5 seconds ago → debounce hit.
        now = datetime.now(timezone.utc)
        recent = now - timedelta(seconds=5)
        _insert_usage(db_breaker, "suggestions_rank", recent.isoformat())

        with pytest.raises(CostBreakerTrippedError) as excinfo:
            _run_async(check_or_raise())
        assert excinfo.value.reason == "debounce_30s"

    def test_check_or_raise_passes_after_debounce_window_expires(self, db_breaker):
        from app.services.llm_cost_breaker import check_or_raise

        # 1 row 35 seconds ago → debounce window passed; no other constraints hit.
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=35)
        _insert_usage(db_breaker, "suggestions_rank", old.isoformat())
        # Must not raise.
        _run_async(check_or_raise())

    def test_check_or_raise_only_counts_suggestions_purposes(self, db_breaker):
        from app.services.llm_cost_breaker import (
            check_or_raise, CostBreakerTrippedError,
        )

        # Seed 50 rows with the WRONG purpose (taste_profile_summary).
        now = datetime.now(timezone.utc)
        for i in range(50):
            ts = now - timedelta(minutes=2 + i)  # all > 60s + > 30s ago
            _insert_usage(db_breaker, "taste_profile_summary", ts.isoformat())

        # Must NOT raise — we're filtering on purpose LIKE 'suggestions_%'.
        _run_async(check_or_raise(purpose_prefix="suggestions_"))

        # Now seed 50 with suggestions_rank.
        for i in range(50):
            ts = now - timedelta(minutes=3 + i)
            _insert_usage(db_breaker, "suggestions_rank", ts.isoformat())

        with pytest.raises(CostBreakerTrippedError):
            _run_async(check_or_raise(purpose_prefix="suggestions_"))


class TestCostBreakerStatus:
    def test_get_state_returns_dataclass(self, db_breaker):
        from app.services.llm_cost_breaker import get_state, CostBreakerStatus

        state = get_state()
        assert isinstance(state, CostBreakerStatus)
        assert state.today_calls == 0
        assert state.today_cost_usd == 0.0
        assert state.last_call_at is None
        assert state.last_tripped_at is None
        assert state.last_tripped_reason is None

    def test_status_updates_after_trip(self, db_breaker):
        from app.services.llm_cost_breaker import (
            check_or_raise, get_state, CostBreakerTrippedError,
        )

        # Trip debounce.
        now = datetime.now(timezone.utc)
        recent = now - timedelta(seconds=5)
        _insert_usage(db_breaker, "suggestions_rank", recent.isoformat())

        with pytest.raises(CostBreakerTrippedError):
            _run_async(check_or_raise())

        state = get_state()
        assert state.last_tripped_reason == "debounce_30s"
        assert state.last_tripped_at is not None
        # last_tripped_at parses as ISO.
        datetime.fromisoformat(state.last_tripped_at)


class TestUtcMidnightReset:
    def test_cost_breaker_resets_at_utc_midnight(self, db_breaker):
        from app.services.llm_cost_breaker import check_or_raise

        # Seed 50 rows dated YESTERDAY UTC (older than the most-recent midnight).
        now = datetime.now(timezone.utc)
        yesterday_noon = (now - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        for i in range(50):
            ts = yesterday_noon + timedelta(minutes=i)
            _insert_usage(db_breaker, "suggestions_rank", ts.isoformat())

        # Today's counter is empty, so check_or_raise must NOT raise.
        _run_async(check_or_raise())
