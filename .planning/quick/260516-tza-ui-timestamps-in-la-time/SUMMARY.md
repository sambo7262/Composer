---
phase: quick
plan: 260516-tza-ui-timestamps-in-la-time
subsystem: ui / templates
tags: [ui, timezone, jinja, hotfix, la-time, cost-chip, debug-pages]
requires:
  - app/main.py
  - app/templates/
provides:
  - app/utils/jinja_filters.py
  - local_time Jinja filter
  - register_filters helper
affects:
  - Cost chip "next tick" message renders in PT (was UTC)
  - Sync banner last-synced renders in PT
  - Library stats last-rating-event renders in PT
  - Vibe diagnostic card last-clustered renders in PT
  - Webhook test indicator received-at renders in PT
  - debug_suggestions cost-breaker + queue + refill-log + LLM-calls + negative-signals all PT
  - debug_events received_at + processed_at + last-test-received-at all PT
tech-stack:
  added: []
  patterns:
    - "Single Jinja filter for all timestamp rendering — DRY across templates"
    - "Shared filter module so test Environments stay in sync with app.main"
    - "DST-aware via stdlib zoneinfo (America/Los_Angeles)"
    - "Defensive: None/empty → '—', invalid string → pass-through"
key-files:
  created:
    - app/utils/jinja_filters.py
    - app/utils/__init__.py
    - tests/test_jinja_filters.py
    - .planning/quick/260516-tza-ui-timestamps-in-la-time/SUMMARY.md
  modified:
    - app/main.py (registers filter on the FastAPI Jinja2Templates env)
    - app/routers/pages.py (_compute_home_chip_context computes next_tick_local_str)
    - app/templates/partials/llm_cost_chip.html (dynamic PT message instead of hardcoded UTC)
    - app/templates/partials/sync_banner.html
    - app/templates/partials/library_stats.html
    - app/templates/partials/vibe_diagnostic_card.html
    - app/templates/partials/webhook_test_indicator.html
    - app/templates/pages/debug_suggestions.html
    - app/templates/pages/debug_events.html
    - tests/test_vibe_color_propagation.py (registers filter on test env)
    - tests/test_pages_suggestions.py (registers filter on test env)
    - tests/test_mobile_first_conventions.py (registers filter on test env)
    - tests/test_pages_home_chip.py (updated cost-chip assertion shape)
decisions:
  - "Extract filter to app/utils/jinja_filters.py — single source for FastAPI + test envs"
  - "Render cost-chip 'next tick' in router context (Python) not Jinja — DST handled by zoneinfo, no template logic"
  - "Apply filter to ALL timestamps including debug pages (user said 'all times in UI')"
  - "Filter returns '—' for None/empty (replaces in-template ternaries)"
  - "DB stays UTC ISO 8601 (Phase 5 invariant unchanged)"
---

# UI timestamps now render in America/Los_Angeles

## Problem

User flagged after Wave 2 deploy: the cost chip rendered "Your first weekly
tick lands Sunday at 03:00 UTC" — they wanted PT-localized. Then expanded to
"all times in UI as well" — apply consistently across every surface.

## Fix

Added `local_time` Jinja filter in a shared module so the FastAPI app and any
ad-hoc test Environment register the same way:

```python
# app/utils/jinja_filters.py
def local_time(value, fmt="%Y-%m-%d %H:%M %Z") -> str:
    # Parse → assume UTC if naive → convert to America/Los_Angeles → strftime
    ...

def register_filters(env) -> None:
    env.filters["local_time"] = local_time
```

`app/main.py` calls `register_filters(templates.env)` after Jinja2Templates
construction. Test fixtures with their own `Environment(...)` call the same
helper — keeps the filter table identical across all rendering paths.

For the cost chip's hardcoded "Sunday at 03:00 UTC" message, the conversion
happens in the router context (`_compute_home_chip_context`) rather than the
template — computes the next Sun 03:00 UTC occurrence, converts to LA via
`zoneinfo`, formats as e.g. "Saturday May 17 at 8:00 PM PDT". DST handled
automatically.

## Render sites swapped (12 templates)

- `partials/llm_cost_chip.html` — new `next_tick_local_str` from router
- `partials/sync_banner.html` — `last_synced | local_time`
- `partials/library_stats.html` — `last_rating_event_at | local_time`
- `partials/vibe_diagnostic_card.html` — `centroid_recomputed_at | local_time`
- `partials/webhook_test_indicator.html` — `last_test_received_at | local_time`
- `pages/debug_suggestions.html` — 5 columns (breaker, mirror, refill log, LLM
  calls, negative signals)
- `pages/debug_events.html` — 3 columns (received, processed, last-test)

## Tests

- 10 new `test_jinja_filters.py` tests — covers DST boundaries, naive
  datetimes, None/empty, invalid input, custom format, register_filters
  contract.
- 179 affected-path tests still pass (`test_pages_*`, `test_api_*`,
  `test_vibe_color_propagation`, `test_event_handlers`,
  `test_mobile_first_conventions`, `test_setup_templates`).
- Updated `test_pages_home_chip.py` cost-chip assertions: the partial now
  contains `{{ next_tick_local_str }}` and rendered pages assert either PDT
  or PST in the chip text.

## Out of scope

- DB columns stay UTC ISO 8601 (Phase 5 D-15 invariant — never break)
- Outbound API request timestamps stay UTC (Plex, MusicBrainz, Anthropic)
- Logging internals stay UTC for grep'ability against logs
- The `webhook_test_indicator.html` query param `?since={{ test_armed_at }}`
  intentionally NOT filtered — it's a URL param, not user-visible text;
  needs the raw ISO for server-side comparison
