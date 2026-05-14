"""Phase 7 (Plan 02) — LLM cost circuit breaker (SUGG-11 / Pitfall 11).

Three thresholds enforced via one aggregate SELECT on LLMUsage:
- daily_quota: 50 LLM calls per UTC day (purpose starts with 'suggestions_')
- burst_window: 5 calls in any rolling 60s window
- debounce: no two calls within 30s of each other

Phase 5 anthropic_client.py DESIGN NOTE: aggregate SELECT against LLMUsage is
adequate for Phase 7's threshold-only refill cadence (D-03). Revisit if
profiling shows it hot.

Pitfall 11 invariant: the breaker MUST exist and be checked from
``refill_suggestions_queue`` in the SAME commit as the first ranking call.
This module is imported and called by ``app.services.suggestions_service``
before every ``AnthropicClient.call_with_structured_output``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import text
from sqlmodel import Session

from app.database import get_engine

logger = logging.getLogger(__name__)

DAILY_QUOTA = 50               # SUGG-11
BURST_WINDOW_SECONDS = 60      # SUGG-11
BURST_LIMIT = 5                # SUGG-11
DEBOUNCE_SECONDS = 30          # D-03 (per CONTEXT.md "no refill within 30s")


class CostBreakerTrippedError(Exception):
    """Raised when a refill call exceeds daily/burst/debounce thresholds.

    suggestions_service catches and sets ``_status.state='cost_locked'``
    without invoking Anthropic.
    """

    def __init__(self, reason: str, until: str):
        self.reason = reason
        self.until = until
        super().__init__(
            f"Cost breaker tripped: reason={reason}, retry_after={until}"
        )


@dataclass
class CostBreakerStatus:
    today_calls: int = 0
    today_cost_usd: float = 0.0
    last_call_at: Optional[str] = None
    last_tripped_at: Optional[str] = None
    last_tripped_reason: Optional[str] = None


_status: CostBreakerStatus = CostBreakerStatus()


def get_state() -> CostBreakerStatus:
    return _status


def _aggregate_counters_sync(
    purpose_prefix: str,
) -> Tuple[int, float, Optional[str], int, int]:
    """One round-trip aggregate SELECT. Returns:
    (today_calls, today_cost_usd, last_call_at, burst_count, debounce_count)

    - today_calls: count of rows since UTC midnight.
    - today_cost_usd: sum of cost_estimate_usd since UTC midnight.
    - last_call_at: max(called_at) across all rows matching the purpose prefix.
    - burst_count: count in last BURST_WINDOW_SECONDS.
    - debounce_count: count in last DEBOUNCE_SECONDS.
    """
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    burst_cutoff = (now - timedelta(seconds=BURST_WINDOW_SECONDS)).isoformat()
    debounce_cutoff = (
        now - timedelta(seconds=DEBOUNCE_SECONDS)
    ).isoformat()
    like = f"{purpose_prefix}%"
    with Session(get_engine()) as session:
        row = session.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM llmusage
                     WHERE purpose LIKE :like AND called_at >= :midnight) AS today_calls,
                  (SELECT COALESCE(SUM(cost_estimate_usd), 0) FROM llmusage
                     WHERE purpose LIKE :like AND called_at >= :midnight) AS today_cost,
                  (SELECT MAX(called_at) FROM llmusage
                     WHERE purpose LIKE :like) AS last_at,
                  (SELECT COUNT(*) FROM llmusage
                     WHERE purpose LIKE :like AND called_at >= :burst) AS burst_count,
                  (SELECT COUNT(*) FROM llmusage
                     WHERE purpose LIKE :like AND called_at >= :debounce) AS debounce_count
                """
            ),
            {
                "like": like,
                "midnight": midnight,
                "burst": burst_cutoff,
                "debounce": debounce_cutoff,
            },
        ).first()
    today_calls = int(row[0] or 0)
    today_cost = float(row[1] or 0.0)
    last_at = row[2]
    burst_count = int(row[3] or 0)
    debounce_count = int(row[4] or 0)
    return today_calls, today_cost, last_at, burst_count, debounce_count


async def check_or_raise(purpose_prefix: str = "suggestions_") -> None:
    """Raise :class:`CostBreakerTrippedError` on any threshold hit; otherwise
    return silently. Call this BEFORE every Anthropic call inside
    ``refill_suggestions_queue`` (Pitfall 11 invariant).
    """
    global _status
    today_calls, today_cost, last_at, burst_count, debounce_count = (
        await asyncio.to_thread(_aggregate_counters_sync, purpose_prefix)
    )
    _status = CostBreakerStatus(
        today_calls=today_calls,
        today_cost_usd=today_cost,
        last_call_at=last_at,
        last_tripped_at=_status.last_tripped_at,
        last_tripped_reason=_status.last_tripped_reason,
    )

    now = datetime.now(timezone.utc)
    if today_calls >= DAILY_QUOTA:
        until = (
            now.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        ).isoformat()
        _trip("daily_quota_50", until)
        raise CostBreakerTrippedError("daily_quota_50", until)

    if burst_count >= BURST_LIMIT:
        until = (now + timedelta(seconds=BURST_WINDOW_SECONDS)).isoformat()
        _trip("burst_5_per_60s", until)
        raise CostBreakerTrippedError("burst_5_per_60s", until)

    if debounce_count >= 1:
        until = (now + timedelta(seconds=DEBOUNCE_SECONDS)).isoformat()
        _trip("debounce_30s", until)
        raise CostBreakerTrippedError("debounce_30s", until)


def _trip(reason: str, until: str) -> None:
    global _status
    _status = CostBreakerStatus(
        today_calls=_status.today_calls,
        today_cost_usd=_status.today_cost_usd,
        last_call_at=_status.last_call_at,
        last_tripped_at=datetime.now(timezone.utc).isoformat(),
        last_tripped_reason=reason,
    )
    logger.warning(
        "LLM cost breaker tripped: reason=%s, retry_after=%s",
        reason,
        until,
    )
