"""Shared Jinja2 filter registrations.

Single source of truth for custom filters so the FastAPI app
(``app.main.templates``) and ad-hoc test Jinja environments stay in sync.
Test fixtures that build their own ``Environment(...)`` must call
:func:`register_filters` immediately after construction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def next_sunday_03_utc_in_la() -> str:
    """Return the next "Sunday 03:00 UTC" weekly cron firing, rendered in LA.

    The weekly maintenance tick is anchored to Sun 03:00 UTC (CronTrigger in
    sync_scheduler.py). For UI surfaces that show "your next tick fires at..."
    we render the next occurrence in America/Los_Angeles so the user sees the
    familiar weekday/time (e.g. "Saturday May 17 at 8:00 PM PDT"). DST is
    handled automatically by zoneinfo.
    """
    now_utc = datetime.now(timezone.utc)
    days_ahead = (6 - now_utc.weekday()) % 7  # Mon=0 ... Sun=6
    next_tick_utc = (now_utc + timedelta(days=days_ahead)).replace(
        hour=3, minute=0, second=0, microsecond=0,
    )
    if next_tick_utc <= now_utc:
        next_tick_utc += timedelta(days=7)
    next_tick_la = next_tick_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    # %-I drops leading zero on hour; %Z = PDT/PST.
    return next_tick_la.strftime("%A %b %-d at %-I:%M %p %Z")


def usd_cost(value: Any) -> str:
    """Render a USD cost with precision that surfaces sub-cent values.

    Why: prompt caching makes a single Anthropic call cost ~$0.001-$0.02.
    The default 2-decimal display ("%.2f") truncates anything < $0.005 to
    "$0.00", which makes the cost chip read as "no costs" right after a
    cache-hit-heavy call.

    Tiers:
      - 0 → "$0.00"
      - > 0 and < 0.01 → 4 decimals (e.g. "$0.0023")
      - >= 0.01 → 2 decimals (e.g. "$0.42")

    Always prefixed with "$"; returns "$0.00" for None / non-numeric input.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "$0.00"
    if v <= 0:
        return "$0.00"
    if v < 0.01:
        return "$" + f"{v:.4f}"
    return "$" + f"{v:.2f}"


def register_filters(env) -> None:
    """Attach Composer filters to a Jinja Environment.

    Accepts either ``jinja2.Environment`` or ``Jinja2Templates`` — the
    latter's ``.env`` is forwarded transparently.
    """
    target = env.env if hasattr(env, "env") and not hasattr(env, "filters") else env
    target.filters["local_time"] = local_time
    target.filters["usd_cost"] = usd_cost
