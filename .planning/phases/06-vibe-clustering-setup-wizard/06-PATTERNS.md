# Phase 6: Vibe Clustering + Setup Wizard - Pattern Map

**Mapped:** 2026-05-09
**Files analyzed:** 32 (4 service modules, 1 helpers, 4 model classes, 3 routers, 6 page templates, 15 partials, 8 modify-in-place targets)
**Analogs found:** 32 / 32 (every new file has a clear in-repo analog; only `setup_step3.html` builds primarily from primitives)

---

## File Classification

| New / Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------------|------|-----------|----------------|---------------|
| `app/services/vibe_service.py` | service (singleton) | event-driven | `app/services/backfill_service.py` + `app/services/sync_service.py` | exact |
| `app/services/vibe_clusterer.py` | service (function module) | batch transform | `app/services/taste_profile_service.py` | exact |
| `app/services/plex_playlist_service.py` | service (PlexAPI wrapper) | request-response | `app/services/chat_service.py::push_playlist_to_plex` (line 562) + `app/services/plex_client.py` | exact |
| `app/services/vibe_helpers.py` | utility | pure (no I/O) | `app/services/rating_helpers.py` | exact |
| `app/models/vibe.py` (Vibe, TrackVibe, ManagedPlaylist, SetupState) | models | n/a (schema) | `app/models/track.py` + `app/models/event_log.py` + `app/models/taste_profile.py` | exact |
| `app/routers/api_setup.py` | router | request-response (HTMX HTML) | `app/routers/api_settings.py` (HTMX partial pattern) + `app/routers/api_webhooks.py` (Form parsing) | exact |
| `app/routers/api_vibes.py` | router | request-response (HTMX HTML) | `app/routers/api_rating_sync.py` | exact |
| `app/routers/pages.py` extensions (`/setup`, `/setup/*`, `/debug/vibes`) | router (pages) | request-response | `app/routers/pages.py::debug_events` (line 150) + `home` (line 30) | exact |
| `app/templates/pages/setup_step1.html` | template (page) | server-render | `app/templates/pages/welcome.html` | role-match |
| `app/templates/pages/setup_step2.html` | template (page) | server-render | `app/templates/pages/settings.html` (webhook section ~lines 31-40) | role-match |
| `app/templates/pages/setup_step3.html` | template (page) | server-render + HTMX morph | (no exact analog — build from `vibe_proposal_card` partial + `sync_banner` HTMX swap pattern) | partial — primitives only |
| `app/templates/pages/setup_step4.html` | template (page) | server-render | `app/templates/pages/chat.html` (playlist preview pattern, lines ~80-) | role-match |
| `app/templates/pages/setup_done.html` | template (page) | server-render | `app/templates/pages/welcome.html` | exact |
| `app/templates/pages/debug_vibes.html` | template (page) | server-render | `app/templates/pages/debug_events.html` | exact |
| `app/templates/partials/wizard_layout.html` | partial (layout wrapper) | server-render | `app/templates/base.html` block override pattern | role-match |
| `app/templates/partials/wizard_step_indicator.html` | partial | server-render | (none — small bespoke component; mirrors `webhook_test_indicator.html` for size) | partial |
| `app/templates/partials/vibe_proposal_card.html` | partial | server-render | `app/templates/partials/service_card.html` (card primitive) | role-match |
| `app/templates/partials/vibe_name_input.html` | partial (Alpine click-to-edit) | client interaction | `app/templates/pages/chat.html` Alpine pattern (lines 6-47) | role-match |
| `app/templates/partials/feature_chip.html` | partial (text-only) | server-render | (trivial — no analog needed; just `<span>` with utilities) | n/a |
| `app/templates/partials/seed_track_row.html` | partial (single line) | server-render | `app/templates/partials/track_table.html` row pattern | role-match |
| `app/templates/partials/vibe_members_disclosure.html` | partial (Alpine x-collapse) | client interaction | `app/templates/partials/sync_banner.html` (HTMX swap) + chat.html Alpine state | partial |
| `app/templates/partials/refinement_input.html` | partial (form + HTMX) | request-response | `app/templates/partials/webhook_url_radio.html` (form + HTMX swap) | role-match |
| `app/templates/partials/wizard_sticky_cta.html` | partial (sticky bottom) | n/a (presentation) | (none in repo — first sticky-bottom pattern; uses Tailwind primitives only) | partial |
| `app/templates/partials/recluster_modal.html` | partial (Alpine modal) | client interaction | (none — first modal in repo; mirrors `webhook_url_radio` + Alpine x-data | partial |
| `app/templates/partials/push_to_plex_banner.html` | partial (HTMX poll banner) | streaming/poll | `app/templates/partials/backfill_banner.html` | exact |
| `app/templates/partials/vibe_diagnostic_card.html` | partial | server-render | `app/templates/pages/debug_events.html` (per-section dl/grid pattern) | role-match |
| `app/templates/partials/slot_in_log_table.html` | partial (table) | server-render | `app/templates/pages/debug_events.html` (events table, lines 56-89) | exact |
| `app/templates/partials/drift_indicator.html` | partial (state banner) | server-render | `app/templates/partials/webhook_test_indicator.html` (success/error) | role-match |
| `app/templates/partials/cold_start_panel.html` | partial | server-render | `app/templates/pages/welcome.html` (centered single-CTA layout) | role-match |
| `app/database.py::_migrate_add_columns()` (modify) | migration shim | n/a | self (existing) — extend `new_columns` dict + `CREATE INDEX` block (lines 62-93) | self |
| `app/services/event_handlers.py::handle_rating_changed` (modify) | handler | event-driven | self — append `vibe_service.slot_track` after `maybe_recompute_after_rating_change` (lines 173-182) | self |
| `app/services/analysis_service.py::trigger_post_sync_analysis` (modify) | post-hook | batch | self — extend after analysis loop completes; reslot pending tracks (lines 285-309) | self |
| `app/templates/base.html` (modify) | layout | n/a | self — body classes + viewport meta (lines 5, 12) | self |
| `app/templates/pages/settings.html` (modify) | page | server-render | self — add Vibes section after Rating Sync (line 48) + footer link (line 52) | self |
| `app/main.py` (modify) | bootstrap | n/a | self — register `api_setup` + `api_vibes` routers after line 72 | self |
| `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async` (extend) | static AST test | n/a | self — extend or add sibling sklearn-allowlist test (lines 292-338) | self |

---

## Pattern Assignments

### `app/services/vibe_service.py` (service singleton; slot-in lock dict + slot/unslot/reslot)

**Analog:** `app/services/backfill_service.py` (singleton state + entry-point function pattern) + `app/services/sync_service.py` (concurrency guard + token-sanitized error handling).

**Singleton state pattern** (from `backfill_service.py` lines 36-58):
```python
class BackfillStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BackfillStatus:
    state: BackfillStateEnum = BackfillStateEnum.IDLE
    total_tracks: int = 0
    backfilled_tracks: int = 0
    last_started: Optional[str] = None
    last_completed: Optional[str] = None
    error: Optional[str] = None


_backfill_status = BackfillStatus()


def get_backfill_status() -> BackfillStatus:
    """Return the current in-memory backfill status."""
    return _backfill_status
```

**`vibe_service.py` adapts this to:**
- A `_slot_in_locks: dict[str, asyncio.Lock] = {}` module-level dict (D-16 per-track lock keyed on `plex_rating_key`).
- A `_status: VibeServiceStatus` for the rare batched paths (`reslot_all`, `recluster_commit`).
- Public accessors `get_vibe_service_status()` (snake-case mirroring `get_backfill_status`).

**Async + sync helper convention** (`backfill_service.py` lines 65-120):
```python
def _check_needs_backfill_sync() -> bool:
    """Pitfall 7 gate: needs (any track exists) AND (no track rated yet)."""
    with Session(get_engine()) as session:
        any_track = session.exec(select(Track.id).limit(1)).first()
        ...

# Usage from async context:
needs = await asyncio.to_thread(_check_needs_backfill_sync)
```

`vibe_service.py` MUST use the same `_<verb>_sync()` naming and dispatch via `asyncio.to_thread`. Lock dict access is the only synchronous DB-free state and lives at module scope.

**PlexAPI-from-async invariant** (D-09, enforced by `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async`):
Every call to `plex_playlist_service.update_playlist_items` from `slot_track` is already async (the wrapping happens inside `plex_playlist_service`), but if `vibe_service` ever calls `PlexServer(...)` directly it MUST go through `asyncio.to_thread`.

---

### `app/services/vibe_clusterer.py` (function module; sklearn k-means + AnthropicClient)

**Analog:** `app/services/taste_profile_service.py` — same combo of numpy + AnthropicClient + structured-output pattern. Phase 5's only pre-existing LLM-aggregating module.

**Imports + module convention** (`taste_profile_service.py` lines 1-41):
```python
"""Phase 5 taste profile service (D-17, RATE-05).
[multi-line docstring describing decisions, storage, recompute trigger, and
 explicit notes on what is FORBIDDEN — e.g. 'sklearn import is forbidden in this file']
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_engine
from app.models.taste_profile import TasteProfile
from app.models.track import Track

logger = logging.getLogger(__name__)
```

`vibe_clusterer.py` adds `from sklearn.cluster import KMeans` and `from sklearn.metrics import silhouette_score`, and the docstring MUST explicitly note "sklearn import is allowed in this file ONLY (D-33). The static AST test `tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module` enforces the allowlist."

**Pydantic response shape** (`taste_profile_service.py` lines 44-47):
```python
class TasteProfileSummary(BaseModel):
    """LLM response shape — just the summary text. Returned by AnthropicClient."""
    summary_text: str
```

`vibe_clusterer.py` defines `VibeProposal(BaseModel)` and `VibeProposalSet(BaseModel)` per D-03 (action discriminator + `seed_track_indices: list[int]` — never raw ratingKeys).

**Cacheable system-prompt build pattern** (`taste_profile_service.py` lines 147-219, especially the long padding block at the bottom that pushes the prompt above 2048 tokens):
```python
def _build_system_prompt(
    centroid: Optional[dict],
    top_artists: list,
    top_genres: list,
    tracks_for_prompt: list,
) -> str:
    """Build a system prompt padded above 2048 tokens to actually engage caching (Pitfall 4)."""
    parts = [
        "You are a music critic helping a Composer user understand their taste profile.",
        ...,
        "## User library structured stats",
        f"Rated track count: {sum(a['count'] for a in top_artists) if top_artists else 0}",
    ]
    ...
    parts.append(
        "## Composer context (cacheable; identical across calls within 1h TTL)\n"
        "Composer reads Plex userRating values (0-10 scale)..."  # ~1500 tokens of fixed context
    )
    return "\n".join(parts)
```

`vibe_clusterer.py::_build_clustering_system_prompt` mirrors this — taste profile summary text + numbered track list with audio features + the Composer context block. Caching engages because the prefix is identical across the refinement-loop turns within 1h TTL.

**LLM call + structured output** (`taste_profile_service.py` lines 240-261):
```python
try:
    with Session(get_engine()) as session:
        client = get_anthropic_client_v2(session)
    system_prompt = _build_system_prompt(...)
    summary = await client.call_with_structured_output(
        system_prompt=system_prompt,
        user_prompt="Write the summary now.",
        response_model=TasteProfileSummary,
        purpose="taste_profile_summary",
    )
    summary_text = summary.summary_text
except Exception:
    logger.exception("Anthropic summary call failed; persisting structured aggregates without LLM text")
```

`vibe_clusterer.py::initial_cluster_proposal` and `refine_proposals` use `purpose="vibe_clustering_initial"`, `"vibe_clustering_refine"`, `"vibe_clustering_recluster"` per D-34. Per-call LLMUsage logging is automatic via `AnthropicClient._log_usage` — no extra code needed.

**Module-local re-export shim for monkeypatch** (`taste_profile_service.py` lines 312-315):
```python
def get_anthropic_client_v2(session):
    """Module-local re-export so tests can monkeypatch this name without import cycles."""
    from app.services.anthropic_client import get_anthropic_client_v2 as _factory
    return _factory(session)
```

`vibe_clusterer.py` MUST include the same shim (test files monkeypatch it the same way — see `tests/test_taste_profile_service.py` line 40).

**Forbidden-import AST test** (`tests/test_taste_profile_service.py` lines 293-307):
```python
def test_no_sklearn_import():
    """Static AST scan: scikit-learn is forbidden in Phase 5 (clustering = Phase 6 only)."""
    path = Path(__file__).parent.parent / "app" / "services" / "taste_profile_service.py"
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "sklearn" not in alias.name, ...
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "sklearn" not in node.module, ...
```

**Phase 6 inverts this** — extend with a sibling test that walks every file in `app/services/` and asserts sklearn imports appear ONLY in files matching `vibe_clusterer.py` or `clustering*.py`. Keep the original `test_no_sklearn_import` for `taste_profile_service.py` intact (Phase 5 invariant).

---

### `app/services/plex_playlist_service.py` (createPlaylist / update_playlist_items / archive / rename)

**Analog:** `app/services/chat_service.py::push_playlist_to_plex` (line 562) for `createPlaylist`; `app/services/plex_client.py` for the `asyncio.to_thread` invariant on every PlexAPI call.

**Existing `createPlaylist` pattern** (`chat_service.py` lines 562-579):
```python
async def push_playlist_to_plex(
    plex_url: str,
    plex_token: str,
    name: str,
    rating_keys: list[str],
) -> dict:
    """Push a playlist to Plex using batch fetch."""
    if not rating_keys:
        raise ValueError("No tracks to push")

    def _create():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        key_str = ",".join(str(k) for k in rating_keys)
        tracks = plex.fetchItems(f"/library/metadata/{key_str}")
        playlist = plex.createPlaylist(title=name, items=tracks)
        return {"success": True, "title": playlist.title, "track_count": len(tracks)}

    return await asyncio.to_thread(_create)
```

**`plex_playlist_service.create_playlist` follows this shape** but renames per D-24 (`Composer · {name}` prefix), persists `ManagedPlaylist(plex_rating_key, kind="vibe", composer_name)`, and returns the new ratingKey instead of a dict.

**`asyncio.to_thread` PlexAPI pattern** (`plex_client.py` lines 41-60):
```python
async def get_library_tracks(
    url: str, token: str, library_id: str,
    container_start: int = 0, container_size: int = 200,
) -> tuple[list[dict], int]:
    plex = await asyncio.to_thread(PlexServer, url, token, timeout=30)
    section = await asyncio.to_thread(lambda: plex.library.sectionByID(int(library_id)))
    total = section.totalSize
    tracks = await asyncio.to_thread(
        section.searchTracks,
        container_start=container_start,
        container_size=container_size,
    )
    return [_map_track(t) for t in tracks], total
```

`plex_playlist_service` MUST wrap every PlexAPI call (`fetchItem`, `createPlaylist`, `playlist.editTitle`, `playlist.addItems`, `playlist.removeItems`, `playlist.items()`) the same way. Either via the inner-`def _xxx()` + `await asyncio.to_thread(_xxx)` style (chat_service.py:572) or the per-call style (plex_client.py:52-59) — both are accepted; pick whichever is clearest per function.

**Dual-marker check helper** (D-27 — new pattern, no exact prior, but follows the "small helper" idiom of `rating_helpers.stars_from_user_rating`):
```python
def is_managed_playlist(plex_rating_key: str) -> bool:
    """OPS-06 / Pitfall 20 — both markers must hold or the playlist is left alone."""
    with Session(get_engine()) as session:
        stmt = select(ManagedPlaylist).where(
            ManagedPlaylist.plex_rating_key == plex_rating_key
        )
        return session.exec(stmt).first() is not None
```
Combine with `playlist.title.startswith("Composer · ")` at every call site (the helper checks the DB-side marker; the title-prefix check happens in the caller after fetching the playlist).

**Post-push verify (Pitfall 6 / VIBE-12)** — after `playlist.addItems`, re-fetch and diff to detect silent track drops:
```python
# After mutation, re-fetch contents and diff against intent.
playlist.reload()
actual_keys = {str(item.ratingKey) for item in playlist.items()}
silently_dropped = set(intended_rating_keys) - actual_keys
return ReconcileResult(added=..., unchanged=..., silently_dropped=list(silently_dropped), retried=...)
```

---

### `app/services/vibe_helpers.py` (centroid → human-readable chip text)

**Analog:** `app/services/rating_helpers.py` — the entire file is the analog (it's a one-function pure helper module).

**Full file pattern** (`rating_helpers.py` lines 1-23):
```python
"""Rating display helpers (RATE-01).

Plex stores `userRating` on a 0-10 scale internally to support half-stars.
A userRating of 7.0 means 3.5 stars in the UI. We persist the RAW 0-10 value
in SQLite (Pitfall 2) and only convert at display boundaries via these helpers.
"""
from __future__ import annotations

from typing import Optional


def stars_from_user_rating(raw: Optional[float]) -> str:
    """Convert raw Plex userRating (0-10 scale) to display string '<n.n> stars'.

    Examples:
        7.0  -> "3.5 stars"  (canonical RATE-01 sanity check)
        ...
    """
    stars = (raw or 0.0) / 2.0
    return f"{stars:.1f} stars"
```

`vibe_helpers.py::feature_chip_text(centroid: dict[str, float]) -> str` follows the same shape:
- Multi-line docstring referencing D-11.
- Single pure function. No I/O.
- Includes "Examples:" block in the docstring with concrete cases.
- Output format is the chip text from UI-SPEC: `"High energy · Fast tempo · Low danceability"` with the `·` (middle dot) separator.

Test file `tests/test_vibe_helpers.py` mirrors `tests/test_rating_helpers.py` (single-file, table-driven assertions on the boundary cases — energy >0.7, tempo >120, etc.).

---

### `app/models/vibe.py` (Vibe, TrackVibe, ManagedPlaylist, SetupState)

**Analogs:**
- `app/models/track.py` — Field-with-defaults + `index=True` pattern, optional columns
- `app/models/event_log.py` — UNIQUE constraint, JSON-as-text column convention
- `app/models/taste_profile.py` — single-row id=1 pattern (used for SetupState)
- `app/models/llm_usage.py` — composite-PK-via-FK + simple int columns (used for TrackVibe)

**SQLModel table shape** (`event_log.py` lines 1-28):
```python
from __future__ import annotations
from typing import Optional
from sqlmodel import Field, SQLModel


class EventLog(SQLModel, table=True):
    """Dedupe + audit trail for inbound webhook + poll events.

    UNIQUE(dedupe_key) is the source of truth for dedupe — `INSERT OR IGNORE` pattern
    in `app/services/event_handlers._insert_event_log_sync`.
    [doc references decisions and pitfalls]
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    event_type: str = Field(index=True)
    plex_rating_key: Optional[str] = Field(default=None, index=True)
    dedupe_key: str = Field(unique=True, index=True)
    received_at: str = Field(index=True)
    processed_at: Optional[str] = Field(default=None)
    handler_error: Optional[str] = Field(default=None)
    raw_payload: Optional[str] = Field(default=None)
```

**Single-row table shape** (`taste_profile.py` lines 1-27 — analog for `SetupState` per ARCHITECTURE.md):
```python
class TasteProfile(SQLModel, table=True):
    """Single-row cache (id=1). Upserted by taste_profile_service on >=10% rated-set delta."""
    id: Optional[int] = Field(default=1, primary_key=True)
    rated_track_count: int = 0
    centroid_energy: Optional[float] = None
    ...
    summary_text: str = ""
    computed_at: Optional[str] = None  # ISO 8601 UTC
```

`SetupState` adopts the id=1 pattern. Its `draft_proposals_json: str = ""` is the JSON-as-TEXT column (Pattern from `taste_profile.top_artists_json`).

**JSON-as-text column** (`taste_profile.py` line 23-24):
```python
top_artists_json: str = ""        # JSON array of {"artist": str, "count": int}
top_genres_json: str = ""         # JSON array of {"genre": str, "count": int}
```

`SetupState.draft_proposals_json: str = ""` — full LLM proposal set + refinement turn history. Per D-08, ~50-100KB; SQLite holds it once vs cookies round-tripping every request.

**Foreign-key timestamp column on LLMUsage** (`llm_usage.py` lines 16-24 — analog for `SetupState.last_llm_call_id`):
```python
class LLMUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    called_at: str = Field(index=True)
    purpose: str = Field(index=True)
    ...
```

`SetupState.last_llm_call_id: Optional[int] = None` is a soft FK (no FK constraint enforced in SQLite by default; matches existing convention of integer references without explicit FK columns).

**Composite PK pattern** for `TrackVibe(track_id, vibe_id)` — SQLModel doesn't natively express composite PKs as cleanly as single-column. Use `__table_args__ = (PrimaryKeyConstraint('track_id', 'vibe_id'),)` or two `Field(foreign_key=...)` columns with `index=True`:
```python
class TrackVibe(SQLModel, table=True):
    track_id: int = Field(foreign_key="track.id", primary_key=True)
    vibe_id: int = Field(foreign_key="vibe.id", primary_key=True)
    distance: float
    assigned_at: str
    assigned_by: str  # "cluster" | "auto-slot" | "manual"
```
Matches the implicit pattern (no existing model has composite PK; this is new but minimal).

**Registration in `init_db()`** (`app/database.py` lines 116-130):
```python
def init_db() -> None:
    """Create all database tables and migrate schema if needed."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import Track, SyncState  # noqa: F401
    from app.models.playlist import Playlist, PlaylistTrack  # noqa: F401
    # Phase 5 (D-19) — register new tables before create_all
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    try:
        _migrate_add_columns(engine)
    except Exception:
        pass
```

**Phase 6 addition:** insert an extra import block before `create_all`:
```python
    # Phase 6 (D-28) — register vibe tables before create_all
    from app.models.vibe import Vibe, TrackVibe, ManagedPlaylist, SetupState  # noqa: F401
```

---

### `app/routers/api_setup.py` (wizard step routes)

**Analog:** `app/routers/api_settings.py` (HTMX partial responses, `hx-post → service_card.html` pattern) + `app/routers/api_webhooks.py` (Form parsing per D-05) + `app/routers/api_rating_sync.py` (concurrency guard + status partial).

**Router header + `get_templates()` lazy import** (`api_rating_sync.py` lines 13-35):
```python
"""Manual rating-sync endpoints (EVT-05).
[docstring with route purposes]
"""
from __future__ import annotations
import asyncio
import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services.backfill_service import (
    BackfillStateEnum,
    get_backfill_status,
    run_backfill,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rating-sync", tags=["rating-sync"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main (Pattern E)."""
    from app.main import templates
    return templates
```

`api_setup.py` uses `prefix="/api/setup"`, `tags=["setup"]`, same `get_templates()` lazy import.

**Form parsing pattern** (`api_webhooks.py` lines 55-82, D-05):
```python
@router.post("/plex")
async def plex_webhook(
    payload: Annotated[str, Form()],
    thumb: Annotated[Optional[UploadFile], File()] = None,
):
    """Push-and-return webhook handler.

    NEVER use pydantic.Json[Model] inside Form() (FastAPI bug #10997).
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Webhook payload not valid JSON; returning 200 anyway")
        return Response(status_code=200)
```

The wizard's `POST /api/setup/refine` accepts a textarea string (no JSON wrapping needed for Phase 6 — the textarea body IS the user message). When the wizard later commits the proposal back to the server, use `Annotated[str, Form()] + json.loads()` for the JSON-as-form-field pattern. NEVER `pydantic.Json[Model]`.

**HTMX partial response pattern** (`api_rating_sync.py` lines 49-62):
```python
@router.post("/start", response_class=HTMLResponse)
async def start_resync(request: Request):
    """EVT-05: 'Resync now' button. Starts (or returns current) backfill state."""
    templates = get_templates()
    status = get_backfill_status()
    if status.state != BackfillStateEnum.RUNNING:
        asyncio.create_task(run_backfill())
        status = get_backfill_status()
    return templates.TemplateResponse(
        request,
        "partials/backfill_banner.html",
        {"backfill_status": status, "state": _state_str(status)},
    )
```

`api_setup.py::POST /refine` and `POST /finalize` follow this shape — return HTML partials (the proposal-card list rerender; the push-to-plex banner) that HTMX swaps into `#proposal-cards` via `hx-target` + `hx-swap="morph:innerHTML"`. The morph extension is required (D-09 / Pitfall 15) because each card has Alpine state.

**Cross-redirect via HX-Redirect** (UI-SPEC Interaction Specs §"Wizard navigation"):
```python
from fastapi import Response
return Response(status_code=204, headers={"HX-Redirect": "/setup/confirm"})
```
Used for "Looks good" → /setup/confirm. New pattern in repo (no existing analog) but standard HTMX idiom.

---

### `app/routers/api_vibes.py` (re-cluster start/commit)

**Analog:** `app/routers/api_rating_sync.py` (entire file — it's the closest mirror: a thin router that fires off a task and returns a status banner).

`POST /api/vibes/recluster/start` mirrors `POST /api/rating-sync/start` line 49-62 above. Returns `Response(status_code=204, headers={"HX-Redirect": "/setup/propose"})` per UI-SPEC modal interaction.

`POST /api/vibes/recluster/commit` is called from `/setup/confirm` after re-cluster path; runs D-21 reconciliation. Returns `partials/push_to_plex_banner.html` (HTMX-polled).

---

### `app/routers/pages.py` extensions (`/setup/*`, `/debug/vibes`)

**Analog:** `pages.py::debug_events` (line 150) — same pattern: query DB rows, package into context dict, render plain HTML page.

**Existing `/debug/events` route** (`pages.py` lines 150-195):
```python
@router.get("/debug/events", response_class=HTMLResponse)
async def debug_events(request: Request, session: Session = Depends(get_session)):
    """DEBUG-01 / D-21: list last 50 EventLog rows + queue depth + poll info..."""
    templates = get_templates()
    rows = session.exec(
        select(EventLog).order_by(col(EventLog.received_at).desc()).limit(50)
    ).all()
    queue = get_event_bus()
    queue_depth = queue.qsize()
    ...
    return templates.TemplateResponse(
        request,
        "pages/debug_events.html",
        {
            "active_page": "debug_events",
            "events": rows,
            "queue_depth": queue_depth,
            ...
        },
    )
```

`/debug/vibes` follows this exactly — query Vibe + TrackVibe + ManagedPlaylist + last-20 SlotInLog rows, package, render.

**Wizard auto-redirect at `/`** (`pages.py::home` lines 30-52, D-07 extension):
```python
@router.get("/", response_class=HTMLResponse)
async def home(request: Request, session: Session = Depends(get_session)):
    templates = get_templates()
    plex_configured = is_service_configured(session, "plex")

    if not plex_configured:
        return templates.TemplateResponse(request, "pages/welcome.html")
    ...
```

**Phase 6 adds** a check before chat.html render: `if Vibe.count() == 0 and Track.count(user_rating > 0) >= 1: return RedirectResponse("/setup")`. Place after `plex_configured` check, before `chat.html` render.

---

### `app/templates/pages/setup_step3.html` (no exact analog — flagship UX)

**Build from primitives:**
- `app/templates/partials/sync_banner.html` (HTMX swap pattern with state machine, lines 1-21 for the `running` state with `hx-trigger="every 2s"`)
- `app/templates/partials/webhook_url_radio.html` (form + HTMX swap pattern, line 1-5 for the form attributes)
- `app/templates/pages/chat.html` (Alpine `x-data` with `started`/state pattern, lines 6-47 for the in-page state-driven Alpine pattern)

**HTMX morph swap (NEW pattern for Phase 6 — Pitfall 15)** is the critical convention:
```html
<form hx-post="/api/setup/refine"
      hx-target="#proposal-cards"
      hx-swap="morph:innerHTML"
      class="...">
  <textarea name="message" ...></textarea>
  <button type="submit">Refine</button>
</form>

<div id="proposal-cards">
  {% for proposal in proposals %}
    {% include "partials/vibe_proposal_card.html" %}
  {% endfor %}
</div>
```

The `morph:innerHTML` swap mode requires `hx-ext="alpine-morph"` on `<body>` (set in `base.html` per D-09). Without alpine-morph, HTMX swaps clobber Alpine state on each card (the click-to-edit name input loses focus and value).

---

### `app/templates/pages/debug_vibes.html`

**Analog:** `app/templates/pages/debug_events.html` — same layout primitives, same screenshot-readable density.

**Exact lines to mirror** (`debug_events.html` lines 13-89):
- Header section (lines 13-19): `<header><h1 class="text-[28px] font-semibold...">Vibe Diagnostics</h1><p class="text-text-secondary text-[13px]...">For diagnostics only. Visible to anyone reachable on this URL...</p></header>`
- Configured/state grid (lines 21-54): `<dl class="grid grid-cols-[160px_1fr] gap-y-1 text-[13px]">` — copy verbatim shape; substitute Vibe-specific fields (vibe count, last clustered, drift).
- Table section (lines 56-89): `<table class="w-full text-[12px] font-mono">` with `<thead class="text-text-secondary">` and row-error coloring `bg-error/5` — copy verbatim for the slot-in log table.

UI-SPEC copy (lead paragraph, "For diagnostics only. Visible to anyone reachable on this URL — Composer assumes Tailscale-only access.") matches Phase 5 word-for-word — the existing `debug_events.html` line 16-17 IS the canonical source.

---

### `app/templates/partials/push_to_plex_banner.html`

**Analog:** `app/templates/partials/backfill_banner.html` — exact mirror of state-machine banner with HTMX poll.

**Lines 1-21 (running state):**
```html
{% if state == "running" %}
{# Running state: show progress bar with HTMX polling every 2s #}
<div id="backfill-banner"
     hx-get="/api/rating-sync/status"
     hx-trigger="every 2s"
     hx-target="#backfill-banner"
     hx-swap="outerHTML"
     class="mb-6 p-4 rounded-lg border border-accent/30 bg-accent/5">
  <div class="flex items-center justify-between mb-2">
    <span class="text-text-primary text-[15px] font-medium">
      Backfilling ratings... {{ backfill_status.backfilled_tracks | default(0) }} / {{ backfill_status.total_tracks | default(0) }} tracks
    </span>
    <span class="text-text-secondary text-[13px]">
      {{ ((backfill_status.backfilled_tracks / backfill_status.total_tracks) * 100) | int if backfill_status.total_tracks else 0 }}%
    </span>
  </div>
  <div class="w-full bg-surface-elevated rounded-full h-2">
    <div class="bg-accent h-2 rounded-full transition-all duration-500"
         style="width: {{ ((backfill_status.backfilled_tracks / backfill_status.total_tracks) * 100) if backfill_status.total_tracks else 0 }}%"></div>
  </div>
</div>
```

`push_to_plex_banner.html` substitutes `id="push-to-plex-banner"`, `hx-get="/api/setup/finalize/status"`, copy "Pushing to Plex... {created}/{total} playlists" per UI-SPEC. Same five states (running / failed / completed / idle), same color tokens, same HTMX target convention.

---

### `app/templates/partials/slot_in_log_table.html`

**Analog:** `app/templates/pages/debug_events.html` lines 56-89 (the events table — extract into a partial for Phase 6).

**Exact pattern to copy:**
```html
<section>
  <h2 class="text-[18px] font-semibold mb-2">Last 20 slot-in decisions</h2>
  <table class="w-full text-[12px] font-mono">
    <thead class="text-text-secondary">
      <tr>
        <th class="text-left p-1">time</th>
        <th class="text-left p-1">track</th>
        <th class="text-left p-1">vibe(s)</th>
        <th class="text-left p-1">distance(s)</th>
        <th class="text-left p-1">2nd vibe?</th>
      </tr>
    </thead>
    <tbody>
      {% if slot_in_log %}
        {% for row in slot_in_log %}
        <tr class="border-t border-border">
          <td class="p-1 align-top">{{ row.timestamp }}</td>
          ...
        </tr>
        {% endfor %}
      {% else %}
        <tr><td colspan="5" class="text-text-secondary p-2">No auto-slot decisions yet. Rate a track in Plexamp to see Composer in action.</td></tr>
      {% endif %}
    </tbody>
  </table>
</section>
```

UI-SPEC empty-state copy ("No auto-slot decisions yet. Rate a track in Plexamp...") goes verbatim into the `{% else %}` branch.

---

### `app/templates/partials/recluster_modal.html` (Alpine modal — first in repo)

**Analog (partial):** Combine `webhook_url_radio.html` (form pattern) with new Alpine x-data + x-show pattern. UI-SPEC has the full layout spec.

**Pattern to follow:**
```html
<div x-data="{ open: false }"
     x-show="open"
     @open-recluster.window="open = true"
     @keydown.escape.window="open = false"
     class="fixed inset-0 bg-black/60 z-40"
     style="display: none;">
  <div @click.self="open = false"
       class="fixed inset-0 flex items-start justify-center pt-32">
    <div class="bg-surface-card rounded-lg p-6 max-w-md w-full mx-4 border border-border"
         x-init="$nextTick(() => $refs.keep.focus())">
      <h2 class="text-[20px] font-semibold mb-3">Re-cluster your vibes?</h2>
      <p class="text-text-secondary text-[14px] mb-6">
        The AI will propose a new vibe set. You can refine it before anything changes —
        your current Plex playlists stay intact until you click "Looks good".
      </p>
      <div class="flex gap-4">
        <button type="button" x-ref="keep" @click="open = false"
                class="flex-1 min-h-11 bg-surface-elevated text-text-primary rounded-lg font-medium">
          Keep current vibes
        </button>
        <button type="button"
                hx-post="/api/vibes/recluster/start"
                class="flex-1 min-h-11 bg-accent hover:bg-accent-hover text-surface-primary rounded-lg font-semibold">
          Re-cluster
        </button>
      </div>
    </div>
  </div>
</div>
```

The `$refs.keep` ref name is intentional per UI-SPEC §"Re-cluster confirm modal" — preserves the user's current state when activated.

---

## Shared Patterns

### Authentication
**Status:** none. Composer is a single-user, Tailscale-only deployment. No auth pattern to apply. Every router omits auth deps. The `/debug/vibes` page surfaces a Tailscale-only warning in its header copy (matches `/debug/events`).

---

### Error handling

**Source:** `app/services/backfill_service.py` lines 158-216 (concurrency guard + token-sanitized error message).

**Pattern:**
```python
async def run_backfill() -> None:
    global _backfill_status
    if _backfill_status.state == BackfillStateEnum.RUNNING:
        return  # concurrency guard
    _backfill_status = BackfillStatus(state=BackfillStateEnum.RUNNING, ...)
    token = ""
    try:
        # ... main work
        _backfill_status.state = BackfillStateEnum.COMPLETED
    except Exception as exc:
        _backfill_status.state = BackfillStateEnum.FAILED
        err = str(exc)
        if token:
            err = err.replace(token, "[REDACTED]")
        _backfill_status.error = err
        logger.exception("Backfill failed")
```

**Apply to:**
- `vibe_service.run_recluster_commit` (long-running operation, idempotency-on-failure per D-23)
- `plex_playlist_service.create_playlist` / `update_playlist_items` (token must be sanitized in error path)
- `vibe_clusterer.initial_cluster_proposal` / `refine_proposals` (LLM error path — best-effort persistence per `taste_profile_service.py` lines 240-261, "persisting structured aggregates without LLM text")

---

### LLMUsage logging

**Source:** Automatic via `app/services/anthropic_client.py::AnthropicClient._log_usage` (lines 130-161). No code-side opt-in needed beyond passing a `purpose` string to `call_with_structured_output`.

**Apply to** every LLM call from Phase 6:
- `purpose="vibe_clustering_initial"` — `vibe_clusterer.initial_cluster_proposal`
- `purpose="vibe_clustering_refine"` — `vibe_clusterer.refine_proposals` (one row per refinement turn)
- `purpose="vibe_clustering_recluster"` — same function but called from re-cluster path (lets Phase 7's cost dashboard segment by feature)

The Sonnet 4.6 cache breakpoint check (line 101-108 of `anthropic_client.py`) auto-warns when system prompt < 2048 tokens. Phase 6's `_build_clustering_system_prompt` MUST pad above this threshold by including taste-profile prefix + the rated track list (typical: 5KB+ which is way above 2048 tokens).

---

### Validation

**Source:** No formal Pydantic model layer at the router boundary in this codebase — validation is Form-field-by-Form-field (`webhook_url: str = Form(...)` in `api_settings.py:307`).

**Apply to** Phase 6 routers:
- `api_setup.py::POST /refine`: `message: Annotated[str, Form()]` — direct string, no JSON wrapping (the textarea IS the message).
- `api_setup.py::POST /finalize`: no body — server reads `SetupState.draft_proposals_json` from the DB.
- `api_setup.py::POST /reset`: no body.
- LLM response validation: server-side `VibeProposalSet.model_validate_json(response_text)` per Phase 5's locked `model_validate_json` rule (NOT Instructor — D-03 from Phase 5).
- Index-back-to-ratingKey: server-side range check on `seed_track_indices` (must be `0 <= idx < n_rated_tracks`); reject the LLM response and retry once with an "indices out of range" instruction in the user message before surfacing the error banner. Pitfall 10 mitigation.

---

### `asyncio.to_thread` PlexAPI invariant (D-09 / EVT-06 / Pitfall 4)

**Source:** `app/services/plex_client.py` (every function — see lines 41-60) + `app/services/event_handlers.py` (every async handler).

**Apply to:**
- `app/services/plex_playlist_service.py` — all four public functions wrap PlexAPI calls.
- `app/services/vibe_service.py::slot_track` — calls `plex_playlist_service.update_playlist_items` (which itself wraps via `to_thread`); the only direct DB calls in `vibe_service` go through `_xxx_sync()` + `await asyncio.to_thread(_xxx_sync)`.

**Enforced by AST test** at `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async` (lines 292-338). Phase 6 SHOULD extend this test to scan `app/services/plex_playlist_service.py` and `app/services/vibe_service.py` as well — add the file paths to the test's path-walk loop or duplicate the test in `tests/test_plex_playlist_service.py`.

---

### HTMX swap convention (Phase 6 establishes this for Phase 7+)

**Source:** N/A — Phase 6 introduces `hx-ext="alpine-morph"` (D-09 + Pitfall 15) as the project-wide convention.

**Apply to:**
- `app/templates/base.html` — add `hx-ext="alpine-morph"` to `<body>` element (line 12).
- Every Phase 6 partial that contains Alpine state AND is HTMX-swapped MUST use `hx-swap="morph:innerHTML"` or `hx-swap="morph:outerHTML"` (the morph swap mode comes from the alpine-morph extension).
- Phase 5 partials (`backfill_banner.html`, `webhook_url_radio.html`) currently use `hx-swap="outerHTML"` because they don't carry Alpine state — leave them as-is.

---

### Test fixture pattern for Phase 6 tables

**Source:** `tests/test_event_handlers.py::db_with_phase5` (lines 22-34) and `tests/test_taste_profile_service.py::db_with_phase5` (lines 22-33).

**Pattern:**
```python
@pytest.fixture
def db_with_phase6(test_engine) -> Generator[Session, None, None]:
    """Create all Phase 5 + Phase 6 tables and yield a session."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    # Phase 6
    from app.models.vibe import Vibe, TrackVibe, ManagedPlaylist, SetupState  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)
```

Mirror across every new test file: `tests/test_vibe_service.py`, `tests/test_vibe_clusterer.py`, `tests/test_plex_playlist_service.py`, `tests/test_api_setup.py`, `tests/test_api_vibes.py`.

---

## No Analog Found

| File | Role | Data Flow | Reason / Recommendation |
|------|------|-----------|-------------------------|
| `app/templates/partials/wizard_sticky_cta.html` | partial (sticky bottom bar) | n/a | First sticky-bottom UI in the repo. Build from Tailwind primitives per UI-SPEC §"Sticky-bottom CTA bar": `position: fixed; bottom: 0; left: 0; right: 0; z-index: 30;` + `pb-[calc(16px+env(safe-area-inset-bottom))]` + `bg-surface-elevated`. No prior code to copy; UI-SPEC IS the source-of-truth contract. |
| `app/templates/partials/recluster_modal.html` | partial (Alpine modal) | client interaction | First modal in repo. Pattern documented above — combine Alpine x-data + x-show with the form/HTMX patterns from `webhook_url_radio.html`. |
| `app/templates/partials/wizard_step_indicator.html` | partial | server-render | First multi-step indicator. Build from utility primitives: `flex gap-2`, `bg-accent` for current, `bg-surface-elevated` for past/future. UI-SPEC §"Wizard navigation" gives format. |
| `app/templates/partials/feature_chip.html` | partial | server-render | Trivial — single `<span class="text-text-secondary text-[13px]">{{ chip_text }}</span>`. No analog needed. |

---

## Modify-In-Place Patterns (existing files)

### `app/database.py::_migrate_add_columns()` — ADD to existing dict (lines 62-93)

Existing pattern:
```python
new_columns = {
    # Phase 3
    "file_path": "TEXT",
    ...
    # Phase 5 (D-15) — raw user_rating 0-10 per Pitfall 2
    "user_rating": "REAL",
    "last_viewed_at": "TEXT",
    "view_count": "INTEGER DEFAULT 0",
    "rating_changed_at": "TEXT",
}

for col_name, col_type in new_columns.items():
    if col_name not in existing_cols:
        cursor.execute(f"ALTER TABLE track ADD COLUMN {col_name} {col_type}")

# Phase 5 RATE-04: index on user_rating for the rated-set view.
cursor.execute("CREATE INDEX IF NOT EXISTS ix_track_user_rating ON track(user_rating)")
cursor.execute(
    "CREATE INDEX IF NOT EXISTS ix_eventlog_received_at_desc ON eventlog(received_at)"
)
```

**Phase 6 addition (D-29 + D-30):**
```python
new_columns = {
    ...,  # existing
    # Phase 6 (D-29) — pending slot-in flag for retroactive auto-slot after analysis.
    "pending_slot_in": "INTEGER DEFAULT 0",  # SQLite has no BOOL; INTEGER 0/1
}

# Phase 6 (D-30) indexes
cursor.execute("CREATE INDEX IF NOT EXISTS ix_trackvibe_vibe_id ON trackvibe(vibe_id)")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_managedplaylist_kind ON managedplaylist(kind)")
cursor.execute(
    "CREATE INDEX IF NOT EXISTS ix_track_pending_slot_in ON track(pending_slot_in) "
    "WHERE pending_slot_in = 1"  # partial index
)
```

---

### `app/services/event_handlers.py::handle_rating_changed` — APPEND after taste-profile recompute (lines 173-182)

Existing pattern:
```python
async def handle_rating_changed(event: RatingChangedEvent) -> None:
    """RATE-03: update Track.user_rating + rating_changed_at; D-18: maybe recompute taste profile."""
    if event.plex_rating_key is None:
        logger.warning("RatingChangedEvent without ratingKey; skipping")
        return
    await asyncio.to_thread(
        _update_track_rating_sync,
        event.plex_rating_key,
        event.new_rating,
        datetime.now(timezone.utc).isoformat(),
    )
    # Phase 5 D-18: Trigger taste profile recompute if rated set changed by >=10%.
    try:
        from app.services.taste_profile_service import (
            maybe_recompute_after_rating_change,
        )
        await maybe_recompute_after_rating_change()
    except Exception:
        logger.exception(
            "Taste profile recompute hook failed; rating update succeeded"
        )
```

**Phase 6 addition (D-15, D-18):** append a second best-effort hook AFTER the taste-profile recompute:
```python
    # Phase 6 D-15 / D-18: Slot the track into matching vibes (or unslot if rating cleared).
    try:
        from app.services.vibe_service import slot_track, unslot_track
        if event.new_rating is None or event.new_rating == 0:
            await unslot_track(event.plex_rating_key)
        else:
            await slot_track(event.plex_rating_key)
    except Exception:
        logger.exception(
            "Vibe slot-in hook failed; rating update succeeded"
        )
```

Lazy import inside the try block matches the existing convention (line 174 `from app.services.taste_profile_service import...`) — avoids circular dep risk.

---

### `app/services/analysis_service.py::trigger_post_sync_analysis` — EXTEND with reslot-pending hook (D-17)

Existing pattern (lines 285-309):
```python
async def trigger_post_sync_analysis() -> None:
    """Auto-trigger analysis after sync completes (D-01)."""
    global _analysis_status
    if _analysis_status.state in (AnalysisStateEnum.RUNNING, AnalysisStateEnum.PAUSED):
        return

    engine = get_engine()
    def _has_unanalyzed() -> bool:
        with Session(engine) as session:
            statement = select(Track.id).where(...).limit(1)
            return session.exec(statement).first() is not None

    has_tracks = await asyncio.to_thread(_has_unanalyzed)
    if has_tracks:
        asyncio.create_task(run_analysis())
```

**Phase 6 addition (D-17):** the actual extension is inside `run_analysis` itself (lines 232-272), not `trigger_post_sync_analysis`. After each successful track analysis:
```python
if result["success"]:
    _analysis_status.analyzed_tracks += 1
    # Phase 6 D-17: retroactive slot-in. After audio features are written for a
    # rated track that was waiting on analysis, slot it into matching vibes
    # and clear the pending flag.
    try:
        from app.services.vibe_service import maybe_reslot_pending_track
        await maybe_reslot_pending_track(track_id)  # checks pending_slot_in flag
    except Exception:
        logger.exception("Reslot pending track hook failed; analysis succeeded")
```

`maybe_reslot_pending_track` is responsible for the WHERE clause `pending_slot_in = TRUE AND user_rating > 0`, the actual slot-in call, and clearing the flag.

---

### `app/templates/base.html` — three minimal changes (D-09)

Existing (lines 5, 12):
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0">
...
<body class="bg-surface-primary text-text-primary font-sans min-h-screen">
```

**Phase 6 changes:**
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
...
<body class="bg-surface-primary text-text-primary font-sans min-h-dvh"
      hx-ext="alpine-morph">
```

Plus a CSS token addition in `app/static/css/input.css @theme` block: `--touch-target-min: 44px;` (declared but not used as a Tailwind class directly — used via `min-h-11 min-w-11` per UI-SPEC).

---

### `app/templates/pages/settings.html` — APPEND Vibes section after Rating Sync (after line 48)

Existing (lines 42-55):
```jinja
<!-- Phase 5: Resync now button (EVT-05) -->
<div class="mt-6">
  <h2 class="text-[20px] font-semibold text-text-primary mb-3">Rating Sync</h2>
  <div hx-get="/api/rating-sync/status" hx-trigger="load" hx-target="this" hx-swap="innerHTML">
    <p class="text-[13px] text-text-secondary">Loading…</p>
  </div>
</div>

<!-- Phase 5: D-22 diagnostics footer link -->
<footer class="mt-12 pt-6 border-t border-border">
  <a href="/debug/events" class="text-[13px] text-text-secondary hover:text-accent">
    View diagnostics →
  </a>
</footer>
```

**Phase 6 changes** — between the Rating Sync section and the footer:
```jinja
<!-- Phase 6: Vibes section -->
<div class="mt-6">
  <h2 class="text-[20px] font-semibold text-text-primary mb-3">Vibes</h2>
  <button x-data
          @click="$dispatch('open-recluster')"
          class="bg-accent hover:bg-accent-hover text-surface-primary font-semibold px-4 py-2 rounded-lg min-h-11">
    Re-cluster vibes
  </button>
  <p class="text-[13px] text-text-secondary mt-2">
    Run the AI proposer again. Your current Plex playlists stay intact during refinement.
  </p>
  <a href="/setup" hx-post="/api/setup/reset" hx-trigger="click"
     class="block text-[13px] text-text-secondary hover:text-accent mt-4 underline">
    Run setup wizard again →
  </a>
</div>

{% include "partials/recluster_modal.html" %}
```

**Footer update** — change "View diagnostics →" to "View event diagnostics →" and add a sibling link:
```jinja
<footer class="mt-12 pt-6 border-t border-border space-y-1">
  <a href="/debug/events" class="block text-[13px] text-text-secondary hover:text-accent">
    View event diagnostics →
  </a>
  <a href="/debug/vibes" class="block text-[13px] text-text-secondary hover:text-accent">
    View vibes diagnostics →
  </a>
</footer>
```

---

### `app/main.py` — REGISTER routers (after line 72)

Existing (lines 64-73):
```python
app.include_router(api_analysis.router)
app.include_router(api_chat.router)
app.include_router(api_health.router)
app.include_router(api_library.router)
app.include_router(api_settings.router)
app.include_router(api_sync.router)
app.include_router(api_webhooks.router)  # Phase 5
app.include_router(api_rating_sync.router)  # Phase 5
app.include_router(pages.router)
```

**Phase 6 additions** — append before `app.include_router(pages.router)`:
```python
app.include_router(api_setup.router)        # Phase 6
app.include_router(api_vibes.router)        # Phase 6
app.include_router(pages.router)
```

Plus update the import block at the top (lines 15-25) to include `api_setup` and `api_vibes`. No lifespan changes needed (D-26 / `code_context` integration points — slot-in fires from existing event dispatcher path; clustering fires from /setup HTTP routes).

---

### `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async` — EXTEND with sklearn allowlist test (D-33)

Existing (lines 292-338) scans `app/services/event_handlers.py` only.

**Phase 6 addition** — new sibling test in `tests/test_vibe_clusterer.py`:
```python
def test_sklearn_only_in_clusterer_module():
    """D-33: sklearn import is allowed ONLY in vibe_clusterer.py and clustering*.py.

    Walks every file in app/services/ and asserts sklearn imports appear only in
    the allowlisted clustering modules.
    """
    services_dir = Path(__file__).parent.parent / "app" / "services"
    allowlist = {"vibe_clusterer.py"}  # plus regex match clustering*.py
    violations = []
    for py_file in services_dir.glob("*.py"):
        if py_file.name in allowlist or py_file.name.startswith("clustering"):
            continue
        source = py_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "sklearn" in alias.name:
                        violations.append(f"{py_file.name}: import {alias.name}")
            if isinstance(node, ast.ImportFrom):
                if node.module and "sklearn" in node.module:
                    violations.append(f"{py_file.name}: from {node.module}")
    assert violations == [], (
        "sklearn imports outside vibe_clusterer.py allowlist:\n" + "\n".join(violations)
    )
```

The existing `tests/test_taste_profile_service.py::test_no_sklearn_import` (lines 293-307) STAYS — Phase 5 invariant for taste_profile_service.py specifically. The new Phase 6 test is broader and complements it.

---

## Metadata

**Analog search scope:** `app/services/`, `app/routers/`, `app/models/`, `app/templates/pages/`, `app/templates/partials/`, `tests/`, `app/main.py`, `app/database.py`, `app/templates/base.html`.

**Files scanned (read for pattern extraction):** 21
- Services: `sync_service.py`, `backfill_service.py`, `taste_profile_service.py`, `event_handlers.py`, `event_bus.py`, `analysis_service.py`, `anthropic_client.py`, `plex_client.py`, `chat_service.py` (lines 540-608), `rating_helpers.py`, `poll_service.py` (lines 1-60).
- Routers: `api_webhooks.py`, `api_rating_sync.py`, `api_settings.py`, `pages.py`.
- Models: `track.py`, `event_log.py`, `taste_profile.py`, `llm_usage.py`, `events.py`.
- Templates: `base.html`, `settings.html`, `welcome.html`, `chat.html` (lines 1-80), `debug_events.html`, `sync_banner.html`, `backfill_banner.html`, `webhook_url_radio.html`, `webhook_test_indicator.html`, `service_card.html`.
- Tests: `test_event_handlers.py`, `test_taste_profile_service.py`.
- Bootstrap: `app/main.py`, `app/database.py`.

**Pattern extraction date:** 2026-05-09

**Phase 5 conventions explicitly inherited (from CLAUDE.md "Phase 5 Conventions"):**
1. PlexAPI calls in async paths → `asyncio.to_thread`
2. `*Event` Pydantic suffix + Literal type discriminator → applied to any new Phase 6 events (none currently planned, but if added the pattern holds)
3. Module-level singletons via `_state` + `get_<name>()` accessor → `vibe_service.py`
4. Form parsing: `Annotated[str, Form()] + json.loads()` → wizard refine textarea (no JSON wrapping needed) and any future JSON-in-form-field
5. `userRating` raw 0-10 → already enforced; vibe_helpers consumes the converted value at display only
6. EventLog dedupe → automatic for any new event types via `dispatch_event`
7. Event bus lifecycle (queue → dispatcher → scheduler) → no changes; Phase 6 doesn't add background tasks
