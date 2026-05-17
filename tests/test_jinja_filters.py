"""Tests for app.utils.jinja_filters — the local_time filter that renders
UTC ISO timestamps in America/Los_Angeles time across the UI.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.utils.jinja_filters import local_time


def test_local_time_renders_iso_utc_in_la_pdt_summer():
    # 2026-07-15 12:00 UTC = 05:00 PDT (UTC-7, summer)
    out = local_time("2026-07-15T12:00:00+00:00")
    assert "2026-07-15 05:00 PDT" == out


def test_local_time_renders_iso_utc_in_la_pst_winter():
    # 2026-12-15 12:00 UTC = 04:00 PST (UTC-8, winter)
    out = local_time("2026-12-15T12:00:00+00:00")
    assert "2026-12-15 04:00 PST" == out


def test_local_time_dst_boundary_spring_forward():
    # 2026-03-08 10:00 UTC is just after the DST shift (US springs forward
    # the second Sunday in March at 02:00 local). 10:00 UTC == 03:00 PDT.
    out = local_time("2026-03-08T10:00:00+00:00")
    assert out == "2026-03-08 03:00 PDT"


def test_local_time_naive_datetime_assumed_utc():
    # No tzinfo → treat as UTC (matches Composer DB ISO 8601 convention).
    out = local_time("2026-05-16T20:00:00")
    # 20:00 UTC = 13:00 PDT
    assert out.endswith("13:00 PDT")


def test_local_time_accepts_datetime_object():
    dt = datetime(2026, 5, 16, 20, 0, 0, tzinfo=timezone.utc)
    out = local_time(dt)
    assert out == "2026-05-16 13:00 PDT"


def test_local_time_none_returns_em_dash():
    assert local_time(None) == "—"


def test_local_time_empty_string_returns_em_dash():
    assert local_time("") == "—"


def test_local_time_invalid_string_returns_original():
    # Unparseable strings pass through unmodified so the UI never explodes
    # on a bad fixture.
    assert local_time("not-a-date") == "not-a-date"


def test_local_time_custom_format():
    out = local_time("2026-05-16T20:00:00+00:00", fmt="%I:%M %p %Z")
    assert out == "01:00 PM PDT"


def test_register_filters_attaches_local_time_to_env():
    from jinja2 import Environment
    from app.utils.jinja_filters import register_filters

    env = Environment()
    register_filters(env)
    assert "local_time" in env.filters
    template = env.from_string("{{ '2026-05-16T20:00:00+00:00' | local_time }}")
    assert template.render() == "2026-05-16 13:00 PDT"
