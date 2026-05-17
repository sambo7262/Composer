"""Shared Jinja2 filter registrations.

Single source of truth for custom filters so the FastAPI app
(``app.main.templates``) and ad-hoc test Jinja environments stay in sync.
Test fixtures that build their own ``Environment(...)`` must call
:func:`register_filters` immediately after construction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def local_time(value: Any, fmt: str = "%Y-%m-%d %H:%M %Z") -> str:
    """Render a UTC ISO 8601 timestamp (or datetime) in America/Los_Angeles.

    Composer stores all DB timestamps as UTC ISO 8601 (Phase 5 invariant) but
    the UI surface flips to LA per user preference. Apply via
    ``{{ value | local_time }}`` in any template that renders a timestamp.

    Returns "—" for None / empty / unparseable values so templates can drop
    their own fallback ternaries.
    """
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    la = dt.astimezone(ZoneInfo("America/Los_Angeles"))
    return la.strftime(fmt)


def register_filters(env) -> None:
    """Attach Composer filters to a Jinja Environment.

    Accepts either ``jinja2.Environment`` or ``Jinja2Templates`` — the
    latter's ``.env`` is forwarded transparently.
    """
    target = env.env if hasattr(env, "env") and not hasattr(env, "filters") else env
    target.filters["local_time"] = local_time
