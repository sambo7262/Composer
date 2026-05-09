"""Wizard's "test webhook" armed-mode flag (D-13, Pitfall 6).

Plex doesn't include a canonical discriminator in test events vs real events.
The wizard flow:
1. User clicks "Test webhook" — calls `arm_test()`.
2. Plex sends a test event within ~5s.
3. Webhook receiver checks `is_test_armed()` — if armed, treats next event as a test.
4. Receiver calls `disarm_test()` after consuming.
5. The 60s TTL guards against stale arming if Plex never delivers.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

# Module-level state
_armed_at: Optional[float] = None
ARM_TTL_SECONDS = 60


def arm_test() -> None:
    """Mark the system as expecting a test event within ARM_TTL_SECONDS."""
    global _armed_at
    _armed_at = time.time()


def is_test_armed() -> bool:
    """Return True if test mode is armed and the TTL has not expired."""
    if _armed_at is None:
        return False
    return (time.time() - _armed_at) < ARM_TTL_SECONDS


def disarm_test() -> None:
    """Clear the armed-test flag. Called after consuming the test event."""
    global _armed_at
    _armed_at = None


def get_armed_at_iso() -> Optional[str]:
    """ISO 8601 of the arm-time, for HTMX poll URL `?since=` param."""
    if _armed_at is None:
        return None
    return datetime.fromtimestamp(_armed_at, tz=timezone.utc).isoformat()
