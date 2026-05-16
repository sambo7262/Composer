# Phase 8: Lidarr Discovery + Polish — Pattern Map

**Mapped:** 2026-05-16
**Files analyzed:** 26 (7 NEW services/models, 1 NEW router, 7 NEW templates, 11 MODIFIED)
**Analogs found:** 24 / 26 (two NEW files — `MusicBrainzCache` write-through + the home cost chip query — have partial analogs only; see "No analog found")

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|-------------------|------|-----------|----------------|-------|
| **NEW** `app/services/discovery_service.py` | service | event-driven (cron) + CRUD | `app/services/suggestions_discovery.py` | exact |
| **NEW** `app/services/musicbrainz_client.py` | service (external API) | request-response | `app/services/lidarr_client.py` + `app/services/plex_client.py` | role-match |
| **NEW** `app/services/listenbrainz_client.py` | service (external API) | request-response | `app/services/lidarr_client.py` (sync-lib wrap pattern) | role-match |
| **NEW** `app/routers/api_discovery.py` | router | request-response | `app/routers/api_suggestions.py` + `app/routers/api_vibes.py` | exact |
| **NEW** `app/models/discovery.py` (DiscoveryCandidate, DiscoveryAdd, DiscoveryDismissed, MusicBrainzCache) | model | CRUD | `app/models/suggestions.py` (SuggestionsMirror, SuggestionHistory, NegativeSignal) | exact |
| **NEW** `app/models/discovery.py` (CostMeterBaseline, WeeklyCronState — single-row id=1) | model | CRUD | `app/models/vibe.py::DiscoveryState` (Phase 7.1) + `app/models/vibe.py::SetupState` | exact |
| **NEW** `app/templates/pages/discover.html` | template | server-render | `app/templates/pages/suggestions.html` (list shell) + novel vibe-grouped sections | role-match |
| **NEW** `app/templates/pages/library.html` (rewrite — same path, full mobile-first replacement of the wide-table version) | template | server-render + HTMX-swap | `app/templates/pages/suggestions.html` (card list + sticky search + alpine-morph) | role-match |
| **NEW** `app/templates/pages/debug_discovery.html` | template | server-render | `app/templates/pages/debug_suggestions.html` + `debug_vibes.html` | exact |
| **NEW** `app/templates/partials/discover_artist_card.html` | template | HTMX-swap (tap-to-expand) | `app/templates/partials/suggestions_row.html` (compact + alpine x-show) | exact |
| **NEW** `app/templates/partials/discover_status_row.html` | template | HTMX-swap (lazy poll) | `app/templates/partials/sync_banner.html` + `suggestion_expanded.html` | role-match |
| **NEW** `app/templates/partials/discover_vibe_section.html` | template | server-render | `app/templates/partials/vibe_card.html` (loop wrapper) | role-match |
| **NEW** `app/templates/partials/llm_cost_chip.html` | template | server-render | `app/templates/partials/llm_cost_meter.html` (smaller/chip variant) | role-match |
| **MOD** `app/services/lidarr_client.py` | service | request-response | itself (extend `test_lidarr_connection` shape) | exact |
| **MOD** `app/services/sync_scheduler.py` | service (cron) | event-driven | itself (`_weekly_maintenance_tick` add-step + CronTrigger swap from RESEARCH §6) | exact |
| **MOD** `app/services/sync_service.py` lines 226-229 | service | event-driven | `app/services/event_handlers.py::_insert_event_log_sync` (EventLog INSERT OR IGNORE) | role-match |
| **MOD** `app/routers/api_settings.py` lines 212-266 | router | request-response | itself (`test_lidarr` / `save_lidarr` extension) | exact |
| **MOD** `app/templates/partials/connection_status.html` | template | server-render | itself (existing quality-profile dropdown — duplicate for metadata + root folder) | exact |
| **MOD** `app/templates/pages/debug_index.html` | template | server-render | itself (unmask the placeholder slot at lines 44-50) | exact |
| **MOD** `app/templates/pages/vibes_home.html` | template | server-render | itself (`{% include "partials/llm_cost_chip.html" %}` insert) | trivial |
| **MOD** `app/templates/partials/recluster_modal.html` + `suggestions_row.html` + `vibe_card.html` + `debug_vibes.html` | template | server-render | itself (add `{{ v.color }}` accent tokens) | trivial |
| **MOD** `app/main.py` lifespan | infra | startup | `app/services/suggestions_service.py::run_phase_07_suggestions_bootstrap` | exact |
| **MOD** `app/database.py::_migrate_add_columns` | infra | startup | itself (additive `ALTER TABLE vibe ADD COLUMN color TEXT` block, guarded) | exact |
| **MOD** `tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async` | test | static AST scan | itself (extend `paths` + `forbidden_names`) | exact |

---

## Pattern Assignments

### `app/services/discovery_service.py` (service, event-driven cron)

**Analog:** `app/services/suggestions_discovery.py` (Phase 7.1 SUGG-13 weekly LLM discovery — closest sibling by purpose AND data flow).

**Imports + module docstring shape** (suggestions_discovery.py lines 1-60):
```python
"""Phase 8 — Weekly LLM artist-discovery layer.

Mirrors app/services/suggestions_discovery.py shape: module-singleton
state + sync DB helpers + asyncio.to_thread wrapping (Phase 5 D-08/D-09).

Plan 02 will add: compute_candidate_set_for_seed (D-A1 ListenBrainz
fetch + MusicBrainz validate + Pitfall 13 popularity gate),
artist_discovery_call_weekly (the LLM re-rank), DISCOVERY_*_LIMIT
constants for the artist-side adjacency cap (mirror SUGG-14).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_engine
from app.models.llm_usage import LLMUsage
# ... etc.
```

**Module-level constants pattern** (suggestions_discovery.py lines 64-119):
```python
# Borrow the SUGG-14 input-side cap shape. ListenBrainz can return
# ~50-100 similar artists per seed * 7 vibes; with MB lookup expansion
# the candidate set can blow past the 200K Claude context.
DISCOVERY_ARTIST_CANDIDATE_LIMIT = 200
DISCOVERY_ARTIST_PROMPT_TOKEN_CEILING = 150_000
DISCOVERY_ARTIST_CHARS_PER_TOKEN_RATIO = 3.5
DISCOVERY_ARTIST_MAX_TOKENS_FLOOR = 8000

# Distinct from "discovery_weekly" (Phase 7.1 suggestions discovery)
# so the cost meter card + /debug/discovery can split the two.
DISCOVERY_ARTIST_PURPOSE = "discovery_artist_weekly"
```

**Module-singleton + get_state pattern** (suggestions_discovery.py lines 152-178):
```python
@dataclass
class ArtistDiscoveryStatus:
    state: str = "idle"  # "idle" | "running" | "cost_locked" | "error"
    last_run_at: Optional[str] = None
    last_error: Optional[str] = None
    last_candidates_made: int = 0

_status: ArtistDiscoveryStatus = ArtistDiscoveryStatus()

def get_state() -> ArtistDiscoveryStatus:
    return _status
```

**Sync DB helper pattern (`_*_sync` + `asyncio.to_thread`)** (suggestions_discovery.py lines 186-274):
Every Session opens inside a `_*_sync` helper. Async public accessors wrap via `asyncio.to_thread`. Enforce by extending the AST test in `tests/test_event_handlers.py` (see below).

**LLM call lifecycle with bounded retry** (suggestions_discovery.py lines 601-947):
- Mark `_status.state = "running"`
- Read candidates (per-vibe seed expansion via ListenBrainz + MB validate)
- Read cached taste profile (`_read_cached_taste_profile_sync`)
- Cost-breaker gate via `check_or_raise(purpose_prefix="discovery_")` — **shared with Phase 7.1**, per CONTEXT discretion the `WEEKLY_DISCOVERY_BUDGET_USD` is a shared ceiling
- Build prompts, run input-side trim loop until estimated tokens ≤ ceiling
- `for attempt in (1, 2):` retry loop — only `MaxTokensTruncationError` triggers retry with `max_tokens *= 2`; every other exception goes to failure path on attempt 1
- Hallucinated-MBID filter mirrors Pitfall 10 (lines 887-898): drop returned artists whose `mb_id` isn't in the validated candidate pool

**Failure path writes LLMUsage row** (lines 573-598):
```python
def _log_discovery_failure_sync(purpose_suffix: str, error_text: str) -> None:
    with Session(get_engine()) as session:
        session.add(LLMUsage(
            model="anthropic-discovery-cron",
            purpose=f"{DISCOVERY_ARTIST_PURPOSE}_{purpose_suffix}",
            input_tokens=0, cache_creation_input_tokens=0,
            cache_read_input_tokens=0, output_tokens=0,
            cost_estimate_usd=0.0,
            called_at=datetime.now(timezone.utc).isoformat(),
            error_text=error_text[:500],
        ))
        session.commit()
```

---

### `app/services/musicbrainz_client.py` (service, external sync-lib wrap)

**Analog:** `app/services/lidarr_client.py` (pyarr — same "sync 3rd-party lib wrapped in `asyncio.to_thread`" shape).

**Imports + async wrapper pattern** (lidarr_client.py lines 1-12, 14-49):
```python
from __future__ import annotations

import asyncio
import logging

import musicbrainzngs  # ADD to requirements.txt — Phase 8 dep

logger = logging.getLogger(__name__)

# One-time module-load setup. Mirrors the pattern of registering the
# User-Agent before any call (musicbrainzngs raises UsageError otherwise).
# RESEARCH §3 — UA format is "App/version ( contact )"; throttle to 1 req/sec.
musicbrainzngs.set_useragent(
    "Composer", "2.0", "sam.e.browning@gmail.com",
)
musicbrainzngs.set_rate_limit(limit_or_interval=1.0, new_requests=1)


async def lookup_artist_by_mbid(mbid: str) -> dict | None:
    """RESEARCH §3 — validate a candidate MBID exists with relations + tags.
    Wrapped in to_thread because musicbrainzngs is sync.
    """
    try:
        result = await asyncio.to_thread(
            musicbrainzngs.get_artist_by_id,
            mbid,
            includes=["artist-rels", "release-groups", "tags", "ratings"],
        )
        return result.get("artist") if result else None
    except musicbrainzngs.ResponseError as exc:
        logger.warning("MB lookup failed for mbid=%s: %s", mbid, exc)
        return None
    except Exception:
        logger.exception("MB lookup raised unexpectedly for mbid=%s", mbid)
        return None
```

**Error-tier pattern (mirrored from lidarr_client.py lines 28-49):**
```python
except Exception as e:
    error_msg = str(e)
    if "401" in error_msg or "Unauthorized" in error_msg:
        return {"success": False, "error": "Authentication failed."}
    if "timeout" in error_msg.lower():
        return {"success": False, "error": "Connection timed out."}
    if "connection" in error_msg.lower() or "refused" in error_msg.lower():
        return {"success": False, "error": "Connection refused."}
    return {"success": False, "error": f"Could not connect: {error_msg[:200]}"}
```

**Persistent cache write-through** (MusicBrainzCache — write inside `lookup_artist_by_mbid` after a successful response; read at top of function). Pattern is novel — no exact analog. Closest is the `TasteProfile` single-row read in `app/services/suggestions_discovery.py::_read_cached_taste_profile_sync` (lines 423-437).

---

### `app/services/listenbrainz_client.py` (service, external HTTP)

**Analog:** No internal analog uses `httpx` for an external one-shot read. Closest shape is `app/services/lidarr_client.py` (small surface, single async function, defensive error tiering).

**Pattern (from RESEARCH §3 + lidarr_client.py shape):**
```python
from __future__ import annotations

import asyncio
import logging

import httpx  # already in requirements.txt

logger = logging.getLogger(__name__)

LISTENBRAINZ_SIMILAR_ARTISTS_URL = (
    "https://labs.api.listenbrainz.org/similar-artists/json"
)
LISTENBRAINZ_DEFAULT_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_"
    "limit_100_filter_True_skip_30"
)


async def get_similar_artists(seed_mbid: str, limit: int = 100) -> list[dict]:
    """D-A1 — fetch similar artists for a seed MBID. Returns scored list.
    Smoke-tested at Plan 02 task 1 — fixture written for the labs response
    shape (CONTEXT D-A1 amended note).
    """
    params = {"artist_mbids": seed_mbid, "algorithm": LISTENBRAINZ_DEFAULT_ALGORITHM}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(LISTENBRAINZ_SIMILAR_ARTISTS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            # labs endpoint returns [{score, name, comment, type, gender, reference_mbid, artist_mbid}, ...]
            return (data or [])[:limit]
    except httpx.HTTPStatusError as exc:
        logger.warning("ListenBrainz HTTP %d for mbid=%s", exc.response.status_code, seed_mbid)
        return []
    except Exception:
        logger.exception("ListenBrainz call raised for mbid=%s", seed_mbid)
        return []
```

---

### `app/routers/api_discovery.py` (router, request-response)

**Analog:** `app/routers/api_suggestions.py` (smallest analog — single-endpoint shape) + `app/routers/api_vibes.py` (multi-endpoint module structure with lazy-import shims).

**Module shape** (api_suggestions.py lines 1-23):
```python
"""Phase 8 — Discovery surface endpoints.

GET  /api/discovery                       — render full /discover page partial
POST /api/discovery/{mb_id}/expand        — D-D3 tap-to-expand
POST /api/discovery/{mb_id}/add           — D-D4 one-click Lidarr add
POST /api/discovery/{mb_id}/dismiss       — D-D5 artist-only exclude
GET  /api/discovery/{mb_id}/status-row    — D-D4 lazy lifecycle poll (5min cache)

Best-effort error handling: never block the user from dismissing a row
even if the underlying handler raises (mirrors api_suggestions.dismiss_track).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/discovery", tags=["discovery"])
```

**Lazy-import shim pattern** (api_vibes.py lines 54-79):
```python
def get_templates():
    """Lazy import to avoid circular dep with app.main (Pattern E)."""
    from app.main import templates
    return templates

# Inside endpoint body, lazy-import the service:
async def dismiss_artist(mb_id: str, request: Request) -> HTMLResponse:
    from app.services import discovery_service
    try:
        await discovery_service.handle_dismiss_artist(mb_id)
    except Exception:
        logger.exception("dismiss_artist: handler raised for mb_id=%s", mb_id)
    return HTMLResponse(content="", status_code=200)
```

**HTMX swap response pattern** (api_suggestions.py lines 26-50): return empty 200 for delete-style endpoints (`hx-swap="outerHTML"` swaps the row to nothing). For add/expand endpoints, return a rendered partial via `templates.TemplateResponse(request, "partials/discover_status_row.html", {...})`.

**Settings router endpoint pattern for the add-to-Lidarr endpoint** (api_settings.py lines 212-235 — `test_lidarr` is a clean Form() + service-call + partial-render template):
```python
@router.post("/{mb_id}/add", response_class=HTMLResponse)
async def add_to_lidarr(mb_id: str, request: Request):
    from app.services import discovery_service
    result = await discovery_service.add_artist_to_lidarr(mb_id)
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/discover_status_row.html",
        {"mb_id": mb_id, **result},
    )
```

---

### `app/models/discovery.py` (model module, CRUD)

**Analog:** `app/models/suggestions.py` (Phase 7 sibling — multiple co-tenant models in one file, mix of audit + state + cache shapes).

**File header + imports** (suggestions.py lines 1-22):
```python
"""Phase 8 — Discovery data model.

Plan 02 introduces:
- DiscoveryCandidate — weekly cron output (replaced wholesale each Sunday).
- DiscoveryAdd — per-Composer-add lifecycle (drives /discover status row).
- DiscoveryDismissed — single-column UNIQUE on mb_id (D-D5 artist-only exclude).
- MusicBrainzCache — indefinite cache; amortizes repeat lookups (D-A1).
- CostMeterBaseline — single-row id=1; deploy_at gate for home cost chip (D-B4).
- WeeklyCronState — single-row id=1; last_tick_at, updated on every successful
  _weekly_maintenance_tick (D-B4 + D-B3 catch-up).

Schema policy (OPS-01): tables registered in app.database.init_db before
SQLModel.metadata.create_all. Additive only. Vibe.color column goes via
_migrate_add_columns.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel
```

**Append-only audit shape** (DiscoveryCandidate — closest analog is SuggestionsMirror lines 24-48 + RefillTriggerLog lines 90-110):
```python
class DiscoveryCandidate(SQLModel, table=True):
    """Weekly cron output. Replaced wholesale each Sunday — read at /discover render.
    Subtract DiscoveryDismissed.mb_id at read time per D-B2 (instant dismiss).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    mb_id: str = Field(index=True)  # MusicBrainz artist ID
    artist_name: str
    seed_track_id: int = Field(foreign_key="track.id", index=True)
    seed_vibe_id: int = Field(foreign_key="vibe.id", index=True)
    mb_listener_count: Optional[int] = None
    popularity_gate_pass: bool = Field(default=False)
    llm_rank: Optional[int] = None
    llm_rationale: Optional[str] = None
    factual_hook: Optional[str] = None  # D-A4 MB-anchored provenance string
    created_at: str = Field(index=True)  # = cron tick timestamp
```

**Single-column UNIQUE pattern** (NegativeSignal lines 65-87 — has the `artist` index + per-column constraints):
```python
class DiscoveryDismissed(SQLModel, table=True):
    """D-D5 — artist-only exclude. UNIQUE(mb_id) so re-dismiss is a no-op."""
    id: Optional[int] = Field(default=None, primary_key=True)
    mb_id: str = Field(unique=True, index=True)
    artist_name: str
    dismissed_at: str
```

**Single-row id=1 pattern** (DiscoveryState lines 208-233 of `app/models/vibe.py` — the canonical Phase 7.1 reference):
```python
class CostMeterBaseline(SQLModel, table=True):
    """D-B4 — single-row id=1 baseline. Set once at Phase 8 deploy by
    run_phase_08_discovery_bootstrap. Home cost chip filters LLMUsage to
    rows >= this timestamp so historical/testing rows don't pollute.
    """
    id: Optional[int] = Field(default=1, primary_key=True)
    deploy_at: str  # ISO 8601 UTC, stamped at lifespan migration


class WeeklyCronState(SQLModel, table=True):
    """D-B4 + D-B3 — single-row id=1. last_tick_at updated on every
    successful _weekly_maintenance_tick. Read by home cost chip ("next
    refresh in Nd") and the discovery catch-up gate in start_scheduler.
    """
    id: Optional[int] = Field(default=1, primary_key=True)
    last_tick_at: Optional[str] = Field(default=None)
```

**Lifecycle row pattern** (DiscoveryAdd — nullable timestamp columns mirror the SyncState shape used in `app/models/track.py`; novel composite — see "No Analog Found").

---

### `app/templates/pages/discover.html` (template, server-render)

**Analog:** `app/templates/pages/suggestions.html` (smallest list page with header + empty-state + body partial loop).

**Page shell pattern** (suggestions.html lines 1-24):
```jinja
{% extends "base.html" %}
{% set active_page = "discover" %}
{% block title %}Discover · Composer{% endblock %}
{% block content %}
  <div class="pt-2 pb-4">
    <h1 class="text-[22px] font-semibold text-text-primary mb-3">Discover</h1>
    {% if vibe_sections|length == 0 %}
      <p class="text-text-secondary text-[14px] mb-3">
        {% if not lidarr_configured %}
          Configure Lidarr in <a href="/settings" class="text-accent underline">Settings</a> to enable Discovery.
        {% elif not vibes_exist %}
          Finish the <a href="/setup" class="text-accent underline">setup wizard</a> to enable Discovery.
        {% else %}
          Your first discoveries land Sunday at 03:00 UTC.
        {% endif %}
      </p>
    {% else %}
      {% for section in vibe_sections %}
        {% include "partials/discover_vibe_section.html" %}
      {% endfor %}
    {% endif %}
  </div>
{% endblock %}
```

Vibe-grouped horizontal-scroll section is novel — pattern for the inner row uses Tailwind `flex overflow-x-auto snap-x snap-mandatory` (CONTEXT D-D2). The section header uses `style="border-left: 4px solid {{ section.vibe.color }};"` (D-E2 — inline style chosen because Tailwind 4 utility classes don't accept dynamic hex values without arbitrary-value syntax).

---

### `app/templates/partials/discover_artist_card.html` (template, tap-to-expand)

**Analog:** `app/templates/partials/suggestions_row.html` (verbatim — D-D3 says "tap-to-expand mirrors Phase 7").

**Compact + expanded pattern** (suggestions_row.html lines 9-47):
```jinja
<li id="discover-card-{{ artist.mb_id }}"
    x-data="{ expanded: false }"
    class="bg-surface-card border border-border rounded-lg overflow-hidden">
  <button type="button"
          @click="expanded = !expanded"
          class="w-full flex items-center gap-3 px-3 py-2 min-h-11 text-left
                 hover:bg-surface-elevated focus:bg-surface-elevated transition-colors">
    <div class="w-12 h-12 bg-surface-muted rounded flex-shrink-0">
      {# Album-art placeholder per CONTEXT deferred-ideas note #}
    </div>
    <div class="flex-1 min-w-0">
      <div class="text-[14px] font-medium text-text-primary truncate">
        {{ artist.artist_name | e }}
      </div>
      <div class="text-[13px] text-text-secondary truncate">
        {{ artist.factual_hook | e }}
      </div>
    </div>
    <span class="text-[11px] text-text-secondary rounded-full px-2 py-0.5 whitespace-nowrap"
          style="background-color: {{ vibe.color }}33; color: {{ vibe.color }};">
      {{ vibe.name | e }}
    </span>
  </button>
  <div x-show="expanded" x-cloak class="px-3 py-2 border-t border-border">
    <p class="text-[13px] text-text-primary mb-2">
      <span class="text-text-secondary">Why this artist? </span>
      {{ artist.llm_rationale | e }}
    </p>
    <div class="flex gap-2 mt-1">
      <button type="button"
              hx-post="/api/discovery/{{ artist.mb_id }}/add"
              hx-target="#discover-card-{{ artist.mb_id }}"
              hx-swap="outerHTML"
              class="flex-1 min-h-11 rounded-md bg-accent text-surface-primary px-3 py-1.5 text-[14px] font-semibold">
        Add to Lidarr
      </button>
      <button type="button"
              hx-post="/api/discovery/{{ artist.mb_id }}/dismiss"
              hx-target="#discover-card-{{ artist.mb_id }}"
              hx-swap="outerHTML"
              class="flex-1 min-h-11 rounded-md border border-border bg-surface-card text-text-secondary px-3 py-1.5 text-[14px]">
        Dismiss
      </button>
    </div>
  </div>
</li>
```

**Alpine-morph** preservation comes from `<body hx-ext="alpine-morph">` in `base.html` — no per-element opt-in needed.

---

### `app/templates/pages/library.html` (full rewrite — mobile-first)

**Analog:** `app/templates/pages/suggestions.html` (card list + alpine-morph swap pattern) + `app/templates/pages/library.html` itself (existing search + sync banner shells to preserve).

**Existing structure to retain** (current library.html lines 5-31):
- `{% include "partials/sync_banner.html" %}` and `{% include "partials/analysis_banner.html" %}` stay
- Sticky search input with `hx-trigger="input changed delay:300ms"`, `hx-target="#track-table"`, `hx-swap="outerHTML"` stays in concept but moves into a sticky top bar

**New patterns (per D-D1):**
- Filter chips row: Alpine-driven local state, mirrors no existing surface — closest is the bottom_tab_bar.html active-page chip styling
- Card-per-track (replaces `track_table.html`): mirror `suggestions_row.html` compact shape (12-line item, 48px art slot, 14px title, 13px artist)
- Sort dropdown → bottom sheet: mirror `recluster_modal.html` modal pattern (Alpine `x-show`, fixed-position overlay, `@keydown.escape.window`)
- `h-dvh`, `env(safe-area-inset-bottom)`, ≥44px targets — already enforced by `tests/test_mobile_first_conventions.py` (verify-only, no new test)

---

### `app/templates/pages/debug_discovery.html` (template, server-render diagnostic)

**Analog:** `app/templates/pages/debug_suggestions.html` (closest by section count + table shape) + `app/templates/pages/debug_vibes.html` (LLM cost panel + library-stats header).

**Section shell** (debug_suggestions.html lines 1-9):
```jinja
{% extends "base.html" %}
{% set active_page = "settings" %}
{% block title %}Debug: Discovery · Composer{% endblock %}
{% block content %}
  <div class="pt-2 pb-4 space-y-6">
    <h1 class="text-[22px] font-semibold text-text-primary">/debug/discovery</h1>
    <!-- sections per CONTEXT discretion -->
  </div>
{% endblock %}
```

**Plain-table pattern** (debug_suggestions.html lines 49-83 — applied per section: weekly candidate set / DiscoveryAdd timeline / recent MB queries / recent Lidarr add_artist / Lidarr test history):
```jinja
<section>
  <h2 class="text-[15px] font-semibold text-text-primary mb-2">
    Last weekly candidate set ({{ candidates|length }} artists)
  </h2>
  {% if candidates|length == 0 %}
    <p class="text-text-secondary text-[14px]">None recorded.</p>
  {% else %}
    <table class="w-full text-[12px] font-mono">
      <thead><tr class="text-text-secondary">
        <th class="text-left">mb_id</th>
        <th class="text-left">artist</th>
        <th class="text-left">seed_vibe</th>
        <th class="text-right">listener_count</th>
        <th class="text-left">pop_gate</th>
        <th class="text-right">llm_rank</th>
        <th class="text-left">hook</th>
      </tr></thead>
      <tbody>
        {% for c in candidates %}
          <tr>
            <td>{{ c.mb_id }}</td>
            <td>{{ c.artist_name | e }}</td>
            <td>{{ c.seed_vibe.name | e }}</td>
            <td class="text-right">{{ c.mb_listener_count or '—' }}</td>
            <td>{{ 'yes' if c.popularity_gate_pass else 'NO' }}</td>
            <td class="text-right">{{ c.llm_rank or '—' }}</td>
            <td>{{ c.factual_hook | e }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% endif %}
</section>
```

**Pages router handler** (pages.py lines 204-285 — `read_debug_suggestions` shape). New `read_debug_discovery` mirrors verbatim with the appropriate model queries.

---

### `app/templates/partials/llm_cost_chip.html` (template, slimmed cost chip)

**Analog:** `app/templates/partials/llm_cost_meter.html` (full Phase 7.1 D-D3 weekly card — chip is the compact variant).

**Pattern** (llm_cost_meter.html lines 4-32, slimmed):
```jinja
{# Phase 8 D-B4 / UI-10 — runaway-cost trip wire chip for home page.
   Anchored to last_weekly_cron_tick; filtered to LLMUsage >= CostMeterBaseline.deploy_at
   so historical rows don't pollute. Tap opens /debug/suggestions for breakdown. #}
<a href="/debug/suggestions"
   class="inline-flex items-center gap-2 min-h-11 rounded-full
          border border-border bg-surface-card px-3 py-1.5
          text-[13px] text-text-secondary no-underline">
  <span class="text-text-primary font-medium">
    This week: ${{ "%.2f"|format(this_week_cost_usd) }}
  </span>
  {% if days_until_refresh is not none %}
    <span class="text-text-muted">·</span>
    <span>next refresh in {{ days_until_refresh }}d</span>
  {% endif %}
  {% if breaker_paused %}
    <span class="text-error font-medium ml-1">·  paused</span>
  {% endif %}
</a>
```

**Query** (pages.py home/vibes endpoint — uses the WeeklyCronState + CostMeterBaseline filter per CONTEXT Integration Points line 272):
```python
# SUM cost since the later of (a) last weekly cron tick (b) baseline deploy
# Filter to LLMUsage.called_at >= max(baseline.deploy_at, cron.last_tick_at)
```

---

### `app/services/lidarr_client.py` (MODIFIED — extend test_lidarr_connection)

**Analog:** itself. Extend the existing function per RESEARCH §2 (lines 248-273).

**Imports unchanged** (lines 1-12). **Extension shape:**
```python
async def test_lidarr_connection(url: str, api_key: str) -> dict:
    """Test Lidarr connectivity and return quality + metadata profiles + root folders.

    Phase 8 D-E1 extension: fetch all three in ONE asyncio.to_thread so we
    hold the GIL once and round-trip the network once. Mirrors Pitfall 14
    / DISC-07.
    """
    try:
        url = url.rstrip("/")
        logger.info("Testing Lidarr connection at %s", url)
        lidarr = Lidarr(host_url=url, api_key=api_key)
        quality_profiles, metadata_profiles, root_folders = await asyncio.to_thread(
            _fetch_lidarr_test_payload, lidarr,
        )
        return {
            "success": True,
            "quality_profiles": [{"id": p["id"], "name": p["name"]} for p in (quality_profiles or [])],
            "metadata_profiles": [{"id": p["id"], "name": p["name"]} for p in (metadata_profiles or [])],
            "root_folders": [{"id": r["id"], "path": r["path"]} for r in (root_folders or [])],
        }
    except Exception as e:
        # ... existing error-tier pattern unchanged (lines 28-49) ...


def _fetch_lidarr_test_payload(lidarr):
    return (
        lidarr.get_quality_profile(),
        lidarr.get_metadata_profile(),
        lidarr.get_root_folder(),
    )


async def add_artist(
    mb_id: str, url: str, api_key: str,
    quality_profile_id: int, metadata_profile_id: int, root_dir: str,
) -> dict:
    """D-E1 / Pitfall 14 — one-click add. Both profile IDs are MANDATORY.
    search_for_missing_albums=True so post-add monitoring (Pitfall 14) has
    real activity to observe.
    """
    lidarr = Lidarr(host_url=url.rstrip("/"), api_key=api_key)
    lookup_result = await asyncio.to_thread(lidarr.lookup_artist, mb_id)
    if not lookup_result:
        return {"success": False, "error": f"Artist {mb_id} not found via Lidarr lookup."}
    artist_dict = lookup_result[0]  # top hit; MB validation already passed upstream
    response = await asyncio.to_thread(
        lidarr.add_artist,
        artist=artist_dict,
        root_dir=root_dir,
        quality_profile_id=quality_profile_id,
        metadata_profile_id=metadata_profile_id,
        monitored=True,
        artist_monitor="all",
        search_for_missing_albums=True,
    )
    return {"success": True, "lidarr_artist_id": response.get("id")}


async def get_recent_history(
    url: str, api_key: str, page_size: int = 50,
) -> list[dict]:
    """D-C3 — populate /discover status row + /debug/discovery timeline.
    Cache for 5min at the caller (NOT a cron — lazy poll per CONTEXT)."""
    lidarr = Lidarr(host_url=url.rstrip("/"), api_key=api_key)
    result = await asyncio.to_thread(lidarr.get_history, page_size=page_size)
    return result.get("records", []) if result else []
```

---

### `app/services/sync_scheduler.py` (MODIFIED — CronTrigger swap + add 3rd step + catch-up)

**Analog:** itself (`schedule_sync` + `_weekly_maintenance_tick` + `start_scheduler`).

**D-E3 Part A — CronTrigger swap** (RESEARCH §6 Part A lines 395-431). Replace `schedule_sync` (lines 30-46) with the CronTrigger variant. Pattern for the trigger selection:
```python
if interval_hours == 24:
    trigger = CronTrigger(hour=3, minute=0, timezone="UTC")
elif interval_hours == 12:
    trigger = CronTrigger(hour="3,15", minute=0, timezone="UTC")
elif interval_hours == 6:
    trigger = CronTrigger(hour="3,9,15,21", minute=0, timezone="UTC")
else:
    trigger = IntervalTrigger(hours=interval_hours)

scheduler.add_job(
    _trigger_sync, trigger=trigger,
    id="library_sync", replace_existing=True,
    coalesce=True, misfire_grace_time=3600, max_instances=1,
    name=f"Library sync ({interval_hours}h cadence)",
)
```

**D-E3 Part B — missed-tick catch-up** (mirror of Phase 7.1 catch-up at lines 308-377 — patterns are visible in the existing `start_scheduler` block; lift verbatim per RESEARCH §6 Part B). Place library catch-up BEFORE discovery catch-up so library state is fresh when discovery runs.

**D-B1 — Add 3rd step to `_weekly_maintenance_tick`** (existing lines 167-211):
```python
async def _weekly_maintenance_tick() -> None:
    # ... existing prune step (lines 192-209, unchanged) ...
    await discovery_call_weekly()  # existing — Phase 7.1 suggestions

    # Phase 8 D-B1 — third step. Best-effort: artist discovery failure must
    # NOT block the next tick.
    try:
        from app.services.discovery_service import artist_discovery_call_weekly
        await artist_discovery_call_weekly()
    except Exception:
        logger.exception(
            "Weekly maintenance: artist_discovery step failed; tick continues."
        )

    # D-B4 — stamp WeeklyCronState.last_tick_at on full success. Best-effort.
    try:
        from app.services.discovery_service import update_weekly_cron_state
        await update_weekly_cron_state(datetime.now(timezone.utc).isoformat())
    except Exception:
        logger.exception("Weekly maintenance: failed to stamp WeeklyCronState.")
```

---

### `app/services/sync_service.py` lines 226-229 (MODIFIED — surface silent failures)

**Analog:** `app/services/event_handlers.py::_insert_event_log_sync` (EventLog `INSERT OR IGNORE` with dedupe_key — Phase 5 D-07 pattern).

**Pattern (RESEARCH §6 Part C lines 472-495):**
```python
except Exception as exc:
    _sync_status.state = SyncStateEnum.FAILED
    sanitized = _sanitize_error(str(exc), token)
    _sync_status.error = sanitized
    logger.exception("Sync failed")
    # DISC-08 — surface to /debug/events so silent failures are observable.
    try:
        await _record_sync_failure_event(sanitized)
    except Exception:
        logger.exception("Failed to record sync_failed EventLog row")


async def _record_sync_failure_event(error_text: str) -> None:
    """Write an EventLog row with event_type='sync_failed'. Best-effort —
    dedupe key uses a 5-minute bucket so repeated burst failures from the
    same root cause don't spam the log.
    """
    import hashlib
    from app.models.event_log import EventLog

    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp() // 300)  # 5-min bucket like Phase 5 D-07
    dedupe = hashlib.sha256(
        f"sync_failed|{error_text[:200]}|{bucket}".encode()
    ).hexdigest()

    def _insert():
        with Session(get_engine()) as session:
            try:
                session.add(EventLog(
                    source="sync",
                    event_type="sync_failed",
                    plex_rating_key=None,
                    dedupe_key=dedupe,
                    received_at=now.isoformat(),
                    handler_error=error_text[:500],
                ))
                session.commit()
            except IntegrityError:
                session.rollback()  # dedupe_key collision — expected within bucket

    await asyncio.to_thread(_insert)
```

---

### `app/routers/api_settings.py` lines 212-266 (MODIFIED — Lidarr endpoints)

**Analog:** itself (existing `test_lidarr` / `save_lidarr`).

**Extension pattern:** `test_lidarr` accepts the same Form() params, calls the extended `test_lidarr_connection`, and renders `connection_status.html` with the new keys `quality_profiles`, `metadata_profiles`, `root_folders`. `save_lidarr` adds the new Form fields:
```python
@router.post("/lidarr/save", response_class=HTMLResponse)
async def save_lidarr(
    request: Request,
    url: str = Form(...),
    api_key: str = Form(...),
    quality_profile_id: str = Form(...),
    quality_profile_name: str = Form(...),
    metadata_profile_id: str = Form(...),
    metadata_profile_name: str = Form(...),
    root_folder_path: str = Form(...),
    session: Session = Depends(get_session),
):
    save_setting(
        session, "lidarr", url, api_key,
        {
            "quality_profile_id": quality_profile_id,
            "quality_profile_name": quality_profile_name,
            "metadata_profile_id": metadata_profile_id,
            "metadata_profile_name": metadata_profile_name,
            "root_folder_path": root_folder_path,
        },
    )
    # ... rest unchanged ...
```

**Form() invariant** (CLAUDE.md Phase 5 D-05): never use `pydantic.Json[Model]` inside `Form()` — FastAPI bug #10997. Form params are `Annotated[str, Form()]` (already the case at lines 240-246).

---

### `app/templates/partials/connection_status.html` (MODIFIED)

**Analog:** itself (existing `{% elif service == "lidarr" %}` block at lines 47-63).

**Extension pattern:** duplicate the quality-profile `<select>` for `metadata_profile_id` (always shown) and `root_folder_path` (conditional `{% if root_folders|length > 1 %}`). The hidden `<select name="X_profile_name">` mirror-block pattern (lines 58-62) repeats for each dropdown so the name value travels alongside the id on Save.

---

### `app/main.py` lifespan (MODIFIED — add `run_phase_08_discovery_bootstrap`)

**Analog:** `app/services/suggestions_service.py::run_phase_07_suggestions_bootstrap` (lines 395-435 — MigrationLog gate pattern).

**Pattern:**
```python
# Add new bootstrap function inside discovery_service.py (mirror of
# run_phase_07_suggestions_bootstrap shape):

PHASE_08_MIGRATION_ID = "8.0-discovery-bootstrap"

async def run_phase_08_discovery_bootstrap() -> None:
    """Lifespan gate. One-shot:
    1. Stamp CostMeterBaseline(id=1, deploy_at=now)
    2. Backfill Vibe.color for existing vibes (palette[vibe_id % len(palette)])
    3. Create WeeklyCronState(id=1, last_tick_at=NULL) row if absent
    """
    existing = await asyncio.to_thread(
        _read_migration_log_sync, PHASE_08_MIGRATION_ID,
    )
    if existing is not None and existing.completed_at is not None:
        logger.info("Phase 8 discovery bootstrap already complete; skipping.")
        return
    await asyncio.to_thread(
        _upsert_migration_log_sync, PHASE_08_MIGRATION_ID, None,
    )
    try:
        await asyncio.to_thread(_bootstrap_baseline_sync)
        await asyncio.to_thread(_bootstrap_weekly_cron_state_sync)
        await asyncio.to_thread(_backfill_vibe_colors_sync)
    except Exception:
        logger.exception("Phase 8 discovery bootstrap failed; will retry.")
        return
    await asyncio.to_thread(
        _upsert_migration_log_sync, PHASE_08_MIGRATION_ID,
        datetime.now(timezone.utc).isoformat(),
    )
```

**Lifespan call site** (app/main.py lines 252-257 — insert AFTER `run_phase_07_suggestions_bootstrap`):
```python
from app.services.discovery_service import run_phase_08_discovery_bootstrap
await run_phase_08_discovery_bootstrap()
```

---

### `app/database.py::_migrate_add_columns` (MODIFIED — Vibe.color)

**Analog:** itself (existing additive ALTER blocks at lines 73-95 and 132-164 — the `recluster_mode` / `error_text` additions show the exact guarded-ALTER shape).

**Pattern (line 152 onwards is the closest sibling block):**
```python
# Phase 8 D-E2 — additive Vibe.color column. Hex string like "#3b82f6".
# Backfill via run_phase_08_discovery_bootstrap (palette[id % len] assignment).
try:
    cursor.execute("PRAGMA table_info(vibe)")
    vibe_cols = {row[1] for row in cursor.fetchall()}
    if "color" not in vibe_cols:
        cursor.execute("ALTER TABLE vibe ADD COLUMN color TEXT")
except sqlite3.OperationalError:
    pass  # vibe table doesn't exist yet; create_all will materialize it
```

**SQLModel definition update** (`app/models/vibe.py::Vibe` lines 37-65 — add field):
```python
color: Optional[str] = Field(default=None)  # Phase 8 D-E2 hex like "#3b82f6"
```

---

### `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async` (MODIFIED)

**Analog:** itself (lines 293-456-ish).

**Extension pattern** — add to the `paths` list (line 311-317):
```python
paths = [
    services_dir / "event_handlers.py",
    services_dir / "plex_playlist_service.py",
    services_dir / "vibe_service.py",
    services_dir / "suggestions_service.py",
    # Phase 8 additions:
    services_dir / "discovery_service.py",
    services_dir / "lidarr_client.py",
    services_dir / "musicbrainz_client.py",
    services_dir / "listenbrainz_client.py",
    Path(__file__).parent.parent / "app" / "routers" / "api_discovery.py",
]
```

Add to `forbidden_names` (line 319-329):
```python
forbidden_names = {
    # ... existing PlexAPI symbols ...
    # Phase 8 — pyarr blocking calls (Lidarr class methods):
    "add_artist",
    "lookup_artist",
    "get_quality_profile",
    "get_metadata_profile",
    "get_root_folder",
    "get_history",
    # MusicBrainz blocking calls:
    "get_artist_by_id",
    # (set_useragent / set_rate_limit are module-init only — exempt
    # by being module-level Call nodes, not inside async funcs)
}
```

The existing `collect_to_thread_targets` + `call_is_to_thread` helpers (lines 331-356) handle the wrapping detection — no logic change.

---

## Shared Patterns

### Phase 5 D-09: `asyncio.to_thread` wrapping for sync libraries
**Source:** `app/services/lidarr_client.py` line 21, `app/services/plex_client.py` lines 52-59.
**Apply to:** Every async function in `discovery_service.py`, `musicbrainz_client.py`, `lidarr_client.py`, `api_discovery.py` that calls pyarr/musicbrainzngs. ListenBrainz uses httpx async — exempt.
**Enforcement:** Extended AST test above.
```python
result = await asyncio.to_thread(lidarr.add_artist, artist=..., root_dir=...)
```

### Phase 5 D-08: Module-singleton + `get_state()` accessor
**Source:** `app/services/suggestions_discovery.py` lines 152-178, `app/services/event_bus.py` lines 22-32.
**Apply to:** `discovery_service.py`. Reset via test fixtures with `autouse=True`.
```python
@dataclass
class ArtistDiscoveryStatus: ...
_status: ArtistDiscoveryStatus = ArtistDiscoveryStatus()
def get_state() -> ArtistDiscoveryStatus: return _status
```

### Phase 7.1 SUGG-14: `MaxTokensTruncationError` bounded retry
**Source:** `app/services/suggestions_discovery.py` lines 799-885, `app/services/anthropic_client.py` lines 59-80.
**Apply to:** `discovery_service.artist_discovery_call_weekly`. **Only** `MaxTokensTruncationError` triggers retry with doubled `max_tokens`; other exceptions go to failure path on attempt 1.

### Phase 7 D-09/D-10: Tap-to-expand dismiss vocabulary
**Source:** `app/templates/partials/suggestions_row.html` + `suggestion_expanded.html`.
**Apply to:** `discover_artist_card.html`. Alpine `x-data="{ expanded: false }"` on the wrapper `<li>`, button toggles `@click="expanded = !expanded"`, expanded panel uses `x-show="expanded" x-cloak`.

### Pitfall 10: Hallucinated-output filter
**Source:** `app/services/suggestions_discovery.py` lines 887-898 (validates `pick.track_id in valid_ids`).
**Apply to:** `discovery_service`. After LLM re-rank returns candidates, filter `pick.mb_id in {c["mb_id"] for c in mb_validated_candidates}` and log warnings for filtered rows.

### CLAUDE.md Phase 5 D-07: EventLog dedupe via SHA-256 + 5-min bucket
**Source:** `app/services/event_handlers.py::_insert_event_log_sync` (UNIQUE(dedupe_key) + `INSERT OR IGNORE`).
**Apply to:** `sync_service.py::_record_sync_failure_event` (sync_failed event with 5-min bucket to prevent burst spam).

### CLAUDE.md Phase 5 D-05: Multipart Form parsing
**Source:** `app/routers/api_settings.py` lines 212-247 (Annotated `str = Form(...)`).
**Apply to:** `api_discovery.py` and the modified `api_settings.py::save_lidarr`. NEVER use `pydantic.Json[Model]` inside `Form()` (FastAPI bug #10997). For JSON bodies inside multipart, use `Annotated[str, Form()]` + `json.loads()`.

### Phase 6.1 MigrationLog gate-row pattern
**Source:** `app/services/suggestions_service.py::run_phase_07_suggestions_bootstrap` lines 395-435, `app/main.py::run_phase_61_migration`.
**Apply to:** `discovery_service.run_phase_08_discovery_bootstrap`. Insert in-flight marker (completed_at=NULL) BEFORE running, stamp completed_at on success, leave NULL on failure so next restart retries.

### Tight per-handler `except` clauses
**Source:** every service/router post-Phase 5. NO `except Exception:` in route handlers.
**Apply to:** `api_discovery.py`, `musicbrainz_client.py`, `listenbrainz_client.py`. Allowed: bare `except Exception` for "best-effort never block user" patterns (e.g. `dismiss_track` style), with explicit `logger.exception(...)` and a 200 response.

### `_*_sync` helper pattern (Session always inside threadpool)
**Source:** `app/services/suggestions_discovery.py` lines 186-274, `app/services/suggestions_service.py` (every Session opens in a `_*_sync` function called via `asyncio.to_thread`).
**Apply to:** `discovery_service.py`. The test pattern is `tests/test_suggestions_service.py::TestSuggestionsServiceAstShape` (referenced in suggestions_service.py docstring lines 43-46). Consider extending or adding sibling test for discovery_service.

### LLMUsage row on every Anthropic call
**Source:** `app/services/anthropic_client.py` (writes LLMUsage), `app/services/suggestions_discovery.py::_log_discovery_failure_sync` lines 573-598 (writes failure rows too).
**Apply to:** `discovery_service`. Use `purpose="discovery_artist_weekly"` for the artist call; failure suffixes follow `discovery_artist_weekly_{skipped_no_candidates|cost_locked|error|error_max_tokens_truncated}`.

### Settings extras JSON pattern
**Source:** `app/services/settings_service.py::save_setting` (extras dict persisted as JSON). Existing Lidarr extras: `{"profile_id", "profile_name"}` (lines 248-251).
**Apply to:** modified `save_lidarr`. New extras keys: `quality_profile_id`, `quality_profile_name`, `metadata_profile_id`, `metadata_profile_name`, `root_folder_path`. **`discovery_service.add_artist_to_lidarr` reads all three at call time** (no re-query of Lidarr per add).

---

## No Analog Found

| File / Concern | Role / Data Flow | Reason | Fallback |
|------|------|-----------|----------|
| `MusicBrainzCache` write-through cache pattern | model + write-through helper | No existing service in the codebase persists a permanent cache of an external-API response payload. `TasteProfile` is single-row + recomputed; `LLMUsage` is append-only audit. | Use the single-row `TasteProfile` read shape for the read path (`_read_cached_mb_artist_sync(mb_id) -> dict | None`); use SuggestionHistory's INSERT pattern for the write path with `INSERT OR REPLACE` on UNIQUE(mb_id). RESEARCH §3 confirms 1 req/sec rate limit so indefinite cache is correct. |
| Vibe-grouped horizontal-scroll layout in `discover.html` | template, novel UI | No existing v2 page uses horizontal-scroll-per-section. Closest is the wizard `proposal_cards_swap.html` but that's a vertical grid. | Use Tailwind 4 `flex overflow-x-auto snap-x snap-mandatory gap-3 -mx-3 px-3` on the inner section row (mobile-first horizontal scroll). Section header uses `style="border-left: 4px solid {{ section.vibe.color }};"` (inline because Tailwind 4 utility classes don't accept dynamic hex without arbitrary-value `[color:#xxx]` syntax). |
| Mobile-first library card list filter chips | template, novel | No existing surface has a "filter chip row" pattern (Composer's chips elsewhere are read-only labels). | Mirror the Phase 7 Alpine pattern: `x-data="{ filter: 'all' }"` on the chip container, chip buttons set `filter`, the `<input search>` includes `hx-include` to ship the active filter as a query param, server filters at render time. Filter chip styling mirrors `vibe_card.html` rounded-md + 13px text + min-h-11 tap targets. |

---

## Metadata

**Analog search scope:** `app/services/`, `app/routers/`, `app/models/`, `app/templates/`, `tests/`.
**Files scanned:** ~30 (full enumeration done via `ls`, depth-1 only for templates).
**Pattern extraction date:** 2026-05-16
**Total lines of source/research read:** ~3,400
**Key dependency: RESEARCH.md §1-6** — pyarr add_artist signature + ListenBrainz endpoint + MusicBrainz rate limit + APScheduler CronTrigger swap are all sourced verbatim from there; planner does NOT need to re-research.
