# Phase 5: Plex Event Foundation + Rating Sync — Pattern Map

**Mapped:** 2026-05-08
**Files analyzed:** 35 (9 services NEW, 2 routers NEW, 3 models NEW, 2 templates NEW, 12 test files NEW; 8 modifications; 1 deletion)
**Analogs found:** 33 / 35 (the 2 with no exact analog use stdlib only — `webhook_url_detection.py` and `app/models/events.py`)

---

## File Classification

| New / Modified File                                    | Role            | Data Flow             | Closest Analog                          | Match Quality |
|--------------------------------------------------------|-----------------|-----------------------|-----------------------------------------|---------------|
| `app/services/event_bus.py`                            | service         | event-driven (queue)  | `app/services/sync_service.py`          | role-match (singleton) |
| `app/services/event_dispatcher.py` (or in `event_bus.py`) | service       | event-driven          | `app/services/sync_service.py`          | role-match (singleton) |
| `app/services/event_handlers.py`                       | service         | event-driven          | `app/services/sync_service.py`          | role-match (DB upsert helpers) |
| `app/services/rating_sync_service.py`                  | service         | CRUD (Plex → DB)      | `app/services/sync_service.py`          | exact (singleton state machine) |
| `app/services/poll_service.py`                         | service         | scheduled polling     | `app/services/sync_service.py` + `sync_scheduler.py` | role-match |
| `app/services/backfill_service.py`                     | service         | batch (one-shot)      | `app/services/analysis_service.py`      | exact (state machine + paginated batch) |
| `app/services/anthropic_client.py`                     | service (client)| request-response      | `app/services/llm_client.py`            | role-match (don't touch v1) |
| `app/services/taste_profile_service.py`                | service         | computation + LLM     | `app/services/analysis_service.py`      | role-match (compute + persist) |
| `app/services/webhook_url_detection.py`                | utility         | request-response (helpers) | n/a — stdlib only                  | no analog |
| `app/services/webhook_test_state.py`                   | utility (state) | event-driven          | module-level singleton in `event_bus.py` (sibling pattern) | role-match |
| `app/routers/api_webhooks.py`                          | router          | request-response (multipart) | `app/routers/api_sync.py`            | role-match (HTMX-aware) |
| `app/routers/api_rating_sync.py`                       | router          | request-response      | `app/routers/api_sync.py`               | exact |
| `app/routers/api_debug.py` OR extend `pages.py`        | router (pages)  | read-only HTML        | `app/routers/pages.py` `library_page`   | exact |
| `app/models/event_log.py`                              | model           | persistent            | `app/models/track.py` (Track + SyncState) | exact (SQLModel table) |
| `app/models/llm_usage.py`                              | model           | persistent (audit)    | `app/models/track.py`                   | exact |
| `app/models/taste_profile.py`                          | model           | persistent (singleton row) | `app/models/track.py`              | exact (single-row pattern uses default=1) |
| `app/models/events.py`                                 | model           | in-memory pydantic    | `app/models/schemas.py` (FeatureCriteria) | partial (no SQLModel — `pydantic.BaseModel`) |
| `app/templates/pages/debug_events.html`                | template (page) | server-render         | `app/templates/pages/library.html`      | role-match (table + banners) |
| `app/templates/partials/backfill_banner.html`          | template (partial) | HTMX partial      | `app/templates/partials/sync_banner.html` + `analysis_banner.html` | exact |
| `app/templates/partials/webhook_test_indicator.html`   | template (partial) | HTMX poll         | `app/templates/partials/sync_banner.html` (running state) | role-match |
| `app/templates/partials/webhook_url_radio.html`        | template (partial) | form              | `app/templates/partials/connection_status.html` | partial |
| `app/database.py`                                      | infra (extend)  | schema migration      | self (extend `_migrate_add_columns()` line 48) | self-extend |
| `app/main.py`                                          | infra (extend)  | lifespan              | self (`lifespan()` line 19)             | self-extend |
| `app/models/track.py`                                  | model (extend)  | persistent            | self                                    | self-extend |
| `app/services/plex_client.py`                          | service (extend)| HTTP→PlexAPI          | self (`_map_track()` line 8)            | self-extend |
| `app/services/sync_scheduler.py`                       | service (extend)| APScheduler           | self (`schedule_sync()` line 28)        | self-extend |
| `app/templates/pages/settings.html`                    | template (extend)| server-render        | self                                    | self-extend |
| `requirements.txt`                                     | config          | -                     | self                                    | self-extend |
| `tests/test_event_bus.py`                              | test            | unit (asyncio)        | `tests/test_sync_scheduler.py`          | role-match (asyncio singleton) |
| `tests/test_api_webhooks.py`                           | test            | integration (TestClient) | `tests/test_sync_api.py`             | exact |
| `tests/test_event_log.py`                              | test            | unit (DB)             | `tests/test_track_model.py`             | exact (SQLModel + IntegrityError) |
| `tests/test_poll_service.py`                           | test            | unit (mocks)          | `tests/test_sync_service.py`            | exact |
| `tests/test_event_handlers.py`                         | test            | unit (mocks)          | `tests/test_sync_service.py`            | role-match |
| `tests/test_webhook_url_detection.py`                  | test            | unit                  | `tests/test_service_clients.py::TestPlexClient` | partial |
| `tests/test_rating_helpers.py`                         | test            | unit (pure)           | `tests/test_track_model.py` (assertion style) | partial |
| `tests/test_backfill_service.py`                       | test            | unit (mocks)          | `tests/test_sync_service.py::TestRunSync` | exact |
| `tests/test_taste_profile_service.py`                  | test            | unit (mock SDK)       | `tests/test_service_clients.py` (mock client class) | role-match |
| `tests/test_anthropic_client.py`                       | test            | unit (mock SDK)       | `tests/test_service_clients.py::TestOllamaClient` | exact |
| `tests/test_pages.py::test_debug_events_page`          | test            | integration           | `tests/test_sync_api.py`                | exact |
| `tests/test_api_rating_sync.py`                        | test            | integration           | `tests/test_sync_api.py`                | exact |
| `tests/test_database.py::test_phase5_migration`        | test (extend)   | integration           | self (`tests/test_database.py`)         | self-extend |
| `tests/test_track_model.py::test_user_rating_indexed`  | test (extend)   | unit                  | self (line 122 `test_audio_feature_columns_indexed`) | self-extend |
| `tests/test_service_clients.py` (lines 80-130)         | test (DELETE)   | -                     | -                                       | removal |
| `app/services/ollama_client.py` (DELETE)               | service (DELETE)| -                     | -                                       | removal |

---

## Pattern Assignments

### `app/services/event_bus.py` (NEW — service, event-driven)

**Analog:** `app/services/sync_service.py` (singleton state) + `app/services/sync_scheduler.py` (long-running asyncio task lifecycle).

**Imports pattern** (from `sync_service.py:1-18`):
```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)
```

**Module-level singleton pattern** (from `sync_service.py:37-42`, also `sync_scheduler.py:17-25`):
```python
# Module-level state — match the existing sync_service / analysis_service / sync_scheduler idiom.
# DO NOT use FastAPI Depends() for the queue; the dispatcher needs to outlive any request.
_queue: Optional[asyncio.Queue] = None
_dispatcher_task: Optional[asyncio.Task] = None


def get_event_bus() -> asyncio.Queue:
    """Return the global event bus. Created lazily on first call from lifespan."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue
```

**Long-running task lifecycle** (mirrors `sync_scheduler.start_scheduler()` lines 52-89 + `stop_scheduler()` lines 92-97):
```python
async def start_dispatcher() -> None:
    """Start the single long-running dispatcher task. Called from lifespan startup."""
    global _dispatcher_task
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return  # already running
    _dispatcher_task = asyncio.create_task(_dispatch_loop(), name="event_dispatcher")


async def stop_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task is None:
        return
    _dispatcher_task.cancel()
    try:
        await _dispatcher_task
    except asyncio.CancelledError:
        pass
    _dispatcher_task = None
```

**Key conventions to copy:**
- `from __future__ import annotations` at top of every service file (used everywhere — `sync_service.py:1`, `analysis_service.py:7`).
- `logger = logging.getLogger(__name__)` (used everywhere; never `logging.basicConfig`).
- Module-level singletons use a leading underscore (`_queue`, `_dispatcher_task`, mirroring `_sync_status`, `_analysis_status`, `_scheduler`).
- Public accessor pattern: `get_<name>()` (mirrors `get_sync_status`, `get_scheduler`, `get_analysis_status`).

---

### `app/services/event_handlers.py` (NEW — service, event-driven)

**Analog:** `app/services/sync_service.py` (specifically the `_*_sync` thread helpers + `to_thread` wrappers, lines 45-110).

**Sync-helper + async-wrapper pattern** (from `sync_service.py:45-88`):
```python
def _upsert_tracks_sync(track_dicts: list[dict]) -> None:
    """Upsert a batch of tracks into the database (synchronous, called via to_thread)."""
    engine = get_engine()
    with Session(engine) as session:
        now = datetime.now(timezone.utc).isoformat()
        for td in track_dicts:
            statement = select(Track).where(
                Track.plex_rating_key == td["plex_rating_key"]
            )
            existing = session.exec(statement).first()
            if existing:
                existing.title = td["title"]
                # ... update fields ...
                session.add(existing)
            else:
                track = Track(...)
                session.add(track)
        session.commit()


async def _upsert_tracks(track_dicts: list[dict]) -> None:
    """Upsert tracks in a background thread to avoid blocking the event loop."""
    await asyncio.to_thread(_upsert_tracks_sync, track_dicts)
```

**Apply to handlers as:**
- `handle_rating_changed(event)` → calls `_update_track_rating_sync(rating_key, new_rating)` via `asyncio.to_thread` (Pitfall 4 / D-09).
- `handle_track_played(event)` → updates `view_count` and `last_viewed_at` via thread helper.
- `handle_library_added(event)` → logs only in Phase 5 (per RESEARCH §"Pitfall 8").
- `handle_webhook_test(event)` → stash payload to module-level state (mirrors `_sync_status` pattern).

**Match-statement dispatch** (RESEARCH §"Pattern 6"):
```python
match event.type:
    case "rating_changed": await handle_rating_changed(event)
    case "track_played":   await handle_track_played(event)
    case "library_added":  await handle_library_added(event)
    case "webhook_test":   await handle_webhook_test(event)
```

---

### `app/services/rating_sync_service.py` (NEW — service, CRUD)

**Analog:** `app/services/sync_service.py` (entire structure mirrors).

**State enum + dataclass + singleton** (from `sync_service.py:21-42`):
```python
class SyncStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SyncStatus:
    state: SyncStateEnum = SyncStateEnum.IDLE
    total_tracks: int = 0
    synced_tracks: int = 0
    last_synced: Optional[str] = None
    error: Optional[str] = None


_sync_status = SyncStatus()


def get_sync_status() -> SyncStatus:
    """Return the current in-memory sync status."""
    return _sync_status
```

**Concurrency guard at top of `run_*` entry point** (from `sync_service.py:152-159`):
```python
async def run_sync() -> None:
    global _sync_status

    # T-02-03: Prevent concurrent sync execution
    if _sync_status.state == SyncStateEnum.RUNNING:
        return

    _sync_status = SyncStatus(state=SyncStateEnum.RUNNING)
    token = ""
```

**Paginated batched fetch + yield** (from `sync_service.py:188-212`):
```python
batch_size = 200
offset = 0
total = None
while True:
    batch, batch_total = await get_library_tracks(
        url, token, library_id,
        container_start=offset,
        container_size=batch_size,
    )
    if total is None:
        total = batch_total
        _sync_status.total_tracks = total

    await _upsert_tracks(batch)
    _sync_status.synced_tracks += len(batch)

    # Yield to event loop so status endpoint can respond
    await asyncio.sleep(0)

    if len(batch) < batch_size or offset + len(batch) >= total:
        break
    offset += batch_size
```

**Token-sanitizing error path** (from `sync_service.py:139-143`, `226-229`) — applies whenever Plex token might appear in errors:
```python
def _sanitize_error(error_msg: str, token: str) -> str:
    """Remove Plex token from error messages (T-02-02 mitigation)."""
    if token:
        error_msg = error_msg.replace(token, "[REDACTED]")
    return error_msg

# ... at exception handler ...
except Exception as exc:
    _sync_status.state = SyncStateEnum.FAILED
    _sync_status.error = _sanitize_error(str(exc), token)
    logger.exception("Sync failed")
```

---

### `app/services/poll_service.py` (NEW — service, scheduled polling)

**Analog:** `app/services/sync_service.py` for the run loop body; `app/services/sync_scheduler.py` for the registration call site.

**APScheduler job registration pattern** (from `sync_scheduler.py:28-44`):
```python
def schedule_sync(interval_hours: int) -> None:
    """Schedule (or reschedule) the recurring library sync job."""
    scheduler = get_scheduler()
    if scheduler.get_job("library_sync"):
        scheduler.remove_job("library_sync")
    scheduler.add_job(
        _trigger_sync,
        trigger=IntervalTrigger(hours=interval_hours),
        id="library_sync",
        replace_existing=True,
        name=f"Library sync every {interval_hours}h",
    )
    logger.info("Scheduled library sync every %d hours", interval_hours)


async def _trigger_sync() -> None:
    """Job function called by APScheduler. Launches run_sync as a task."""
    asyncio.create_task(run_sync())
```

**Apply to poll_service / scheduler extension:**
- Add `schedule_polling(interval_minutes: int = 5)` to `sync_scheduler.py` (additive, not in a new file).
- Job id: `"plex_polling"` (mirrors `"library_sync"` naming).
- `_trigger_polling` calls `asyncio.create_task(run_poll())` — fire-and-forget, like `_trigger_sync`.
- Inside `run_poll()`: ALL PlexAPI calls wrapped in `asyncio.to_thread` (D-09; mirror `plex_client.py:45-52`).

**PlexAPI bounded query pattern** (from `plex_client.py:45-53`):
```python
plex = await asyncio.to_thread(PlexServer, url, token, timeout=30)
section = await asyncio.to_thread(lambda: plex.library.sectionByID(int(library_id)))
total = section.totalSize
tracks = await asyncio.to_thread(
    section.searchTracks,
    container_start=container_start,
    container_size=container_size,
)
```

For poll_service, change `searchTracks` kwargs to filter forms (`filters={"track.userRating>>": 0}` etc., per RESEARCH §"Pattern 3"). Spike syntax during planning (RESEARCH A1).

---

### `app/services/backfill_service.py` (NEW — service, batch one-shot)

**Analog:** `app/services/analysis_service.py` (state machine with idle→running→completed/failed; auto-trigger from lifespan).

**State machine + status dataclass** (from `analysis_service.py:30-72`):
```python
class AnalysisStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AnalysisStatus:
    state: AnalysisStateEnum = AnalysisStateEnum.IDLE
    total_tracks: int = 0
    analyzed_tracks: int = 0
    failed_tracks: int = 0
    current_track: str = ""
    avg_seconds_per_track: float = 0.0
    errors: list = field(default_factory=list)


_analysis_status = AnalysisStatus()


def get_analysis_status() -> AnalysisStatus:
    """Return the current in-memory analysis status."""
    return _analysis_status
```

**Auto-trigger pattern (gated on DB state)** (from `analysis_service.py:285-309`):
```python
async def trigger_post_sync_analysis() -> None:
    """Auto-trigger analysis after sync completes (D-01)."""
    global _analysis_status

    if _analysis_status.state in (AnalysisStateEnum.RUNNING, AnalysisStateEnum.PAUSED):
        return

    engine = get_engine()

    def _has_unanalyzed() -> bool:
        with Session(engine) as session:
            statement = select(Track.id).where(
                Track.analyzed_at.is_(None),
                Track.file_path.isnot(None),
            ).limit(1)
            result = session.exec(statement).first()
            return result is not None

    has_tracks = await asyncio.to_thread(_has_unanalyzed)
    if has_tracks:
        asyncio.create_task(run_analysis())
```

**Apply to backfill:**
- `maybe_trigger_first_run_backfill()` mirrors `trigger_post_sync_analysis` exactly (RESEARCH §"Pitfall 7" check: `(any track exists) AND (no track has user_rating)`).
- Called from `app/main.py` `lifespan()` after `start_scheduler()`, NOT from sync_service post-hook (gating already handles ordering).

---

### `app/services/anthropic_client.py` (NEW — service, request-response)

**Analog:** `app/services/llm_client.py` for the *factory pattern* (lines 53-74) — but the new client uses the `anthropic` SDK, not `httpx`. **Do NOT modify llm_client.py** (D-01).

**Factory pattern to mirror** (from `llm_client.py:53-74`):
```python
def get_anthropic_client(db_session) -> tuple:
    """Get Anthropic API key and model name from settings."""
    setting = get_setting(db_session, "anthropic")
    if setting is None or not setting.is_configured:
        raise ValueError("Anthropic is not configured. Set up your API key in Settings first.")

    api_key = get_decrypted_credential(db_session, "anthropic")
    if not api_key:
        raise ValueError("Anthropic API key not found. Please reconfigure in Settings.")

    model_name = "claude-3-5-haiku-latest"
    if setting.extra_config and setting.extra_config.get("model_name"):
        model_name = setting.extra_config["model_name"]

    return api_key, model_name
```

**Naming to differentiate from v1:** `get_anthropic_client_v2()` (per RESEARCH code example line 1049). v1's `get_anthropic_client()` stays.

**SDK usage shape** (from RESEARCH lines 956-1011 — already vetted):
- `from anthropic import AsyncAnthropic`
- `await client.messages.create(model=..., system=[{"type": "text", "text": ..., "cache_control": {"type": "ephemeral", "ttl": "1h"}}], messages=[...])`
- Parse output via `response_model.model_validate_json(text)` — NO Instructor (D-03).
- Log `usage.input_tokens`, `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, `usage.output_tokens` to `LLMUsage` table via `asyncio.to_thread` (mirrors `_upsert_tracks_sync` pattern).

**Anti-patterns from llm_client.py to NOT copy:**
- v1 hand-rolls Anthropic message conversion (`llm_client.py:89-96`); v2 uses SDK objects directly.
- v1 uses raw `httpx.AsyncClient` per call; v2 stores `AsyncAnthropic` instance on the class for connection reuse.

---

### `app/services/taste_profile_service.py` (NEW — service, computation + LLM)

**Analog:** `app/services/analysis_service.py` (overall service shape — query rated tracks, compute, persist).

**Aggregation pattern** (mirrors how `analysis_service.py:204-217` queries un-analyzed tracks):
```python
def _get_rated_tracks_sync() -> list[Track]:
    with Session(get_engine()) as session:
        statement = select(Track).where(Track.user_rating > 0)
        return session.exec(statement).all()
```

**Upsert single-row pattern** (from `sync_service.py:91-109` — adapt for TasteProfile id=1):
```python
def _update_sync_state_sync(total_tracks: int) -> None:
    """Update the SyncState record in the database (synchronous)."""
    engine = get_engine()
    with Session(engine) as session:
        now = datetime.now(timezone.utc).isoformat()
        statement = select(SyncState)
        state = session.exec(statement).first()
        if state:
            state.last_sync_completed = now
            state.total_tracks = total_tracks
            session.add(state)
        else:
            state = SyncState(
                last_sync_started=now,
                last_sync_completed=now,
                total_tracks=total_tracks,
            )
            session.add(state)
        session.commit()
```

Apply with `select(TasteProfile).where(TasteProfile.id == 1)` upsert.

**Computation core (4-D centroid):** numpy mean over rated tracks' (energy, tempo, danceability, valence). NO scikit-learn import in Phase 5 (RESEARCH §"Anti-Patterns to Avoid": clustering is Phase 6).

---

### `app/services/webhook_url_detection.py` (NEW — utility, no analog)

**Analog:** None in repo — pure stdlib helpers. **Recommend planner copy verbatim from RESEARCH lines 596-647.**

Key conventions still apply:
- `from __future__ import annotations` at top.
- Functions return `Optional[str]` (graceful when interface not present).
- `try/except (ImportError, OSError, ipaddress.AddressValueError)` — mirrors v1's defensive `try/except (IndexError, AttributeError)` in `plex_client.py:11-18`.

**`psutil` dependency check** (RESEARCH A4): planner must run `pip show psutil` against current env; if missing, add `psutil>=5.9,<7.0` to requirements.txt.

---

### `app/routers/api_webhooks.py` (NEW — router, request-response)

**Analog:** `app/routers/api_sync.py`.

**Router declaration + lazy templates pattern** (from `api_sync.py:18-24`):
```python
router = APIRouter(prefix="/api/sync", tags=["sync"])


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates
```

**For api_webhooks.py:**
- `router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])`
- Same `get_templates()` lazy-import pattern (needed for the `/last-test` HTMX partial).

**Multipart form parsing pattern** (NEW — locked by D-05; not in v1):
```python
from typing import Annotated, Optional
from fastapi import Form, UploadFile, File

@router.post("/plex")
async def plex_webhook(
    payload: Annotated[str, Form()],            # JSON string in multipart 'payload' field
    thumb: Annotated[Optional[UploadFile], File()] = None,
):
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return Response(status_code=200)  # Always 200 — Plex retries on non-2xx
```

**Module-level state for last-test (mirrors `_sync_status` pattern):**
```python
_last_test_received_at: Optional[str] = None
_last_test_payload: Optional[dict] = None
```

**Always return 200** even on parse failure (RESEARCH §"Pitfall 1" + D-05; never 400/500 — Plex retries on non-2xx).

---

### `app/routers/api_rating_sync.py` (NEW — router, request-response)

**Analog:** `app/routers/api_sync.py` exactly — copy with renamed handlers.

**HTMX banner pattern** (from `api_sync.py:27-83`):
```python
@router.post("/start", response_class=HTMLResponse)
async def start_sync(
    request: Request,
    session: Session = Depends(get_session),
):
    """Trigger a library sync. Returns the sync banner partial for HTMX swap."""
    templates = get_templates()
    status = get_sync_status()

    # T-02-06: If already running, just return current progress
    if status.state == SyncStateEnum.RUNNING:
        # ... return existing-progress banner
        return templates.TemplateResponse(
            request,
            "partials/sync_banner.html",
            {"sync_status": status, "state": status.state.value, ...},
        )

    # Check service is configured before attempting
    if not is_service_configured(session, "plex"):
        # ... return error banner

    # Launch sync as background task
    asyncio.create_task(run_sync())

    return templates.TemplateResponse(...)
```

**For Resync (EVT-05):** the `/api/rating-sync/start` endpoint mirrors this exactly with `run_backfill()` (or a `run_resync()` alias) and returns `partials/backfill_banner.html`.

---

### `app/routers/pages.py` extension — `/debug/events` (modify)

**Analog:** existing `library_page` (lines 83-142) for the structure; existing `home`/`settings_page` for context-fetching style.

**Pattern to copy** (from `pages.py:83-142`):
```python
@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request, session: Session = Depends(get_session)):
    templates = get_templates()
    sync_status = get_sync_status()
    sync_info = get_last_sync_info(session)
    # ... gather all context ...
    return templates.TemplateResponse(
        request,
        "pages/library.html",
        {
            "active_page": "library",
            # ... all context vars ...
        },
    )
```

For `/debug/events` route, use `select(EventLog).order_by(EventLog.received_at.desc()).limit(50)` and pull `queue.qsize()` from `get_event_bus()`. RESEARCH lines 1283-1314 has the verbatim pattern.

---

### `app/models/event_log.py`, `app/models/llm_usage.py`, `app/models/taste_profile.py` (NEW — model)

**Analog:** `app/models/track.py` (lines 1-38) for both Track and SyncState.

**SQLModel table pattern** (from `models/track.py:8-38`):
```python
from __future__ import annotations
from typing import Optional
from sqlmodel import Field, SQLModel


class Track(SQLModel, table=True):
    """Database model for a music track synced from Plex."""

    id: Optional[int] = Field(default=None, primary_key=True)
    plex_rating_key: str = Field(unique=True, index=True)
    title: str = Field(index=True)
    # ...
```

**`unique=True, index=True` for dedupe** (mirrors `Track.plex_rating_key` line 12) — apply to `EventLog.dedupe_key`.

**Single-row id=1 pattern for TasteProfile** (RESEARCH §"Pattern 5" lines 521-538): use `id: Optional[int] = Field(default=1, primary_key=True)` with upsert.

**Naming convention:** Composer uses single-word lowercase table names that match the class name lowercased — `track`, `serviceconfig`, `syncstate`. New tables: `eventlog`, `llmusage`, `tasteprofile`. See `models/settings.py:8` and `models/track.py:8, 41`.

---

### `app/models/events.py` (NEW — pydantic, in-memory)

**Analog:** `app/models/schemas.py` (FeatureCriteria) — partial. Composer's existing pydantic models for non-table data live in `schemas.py`.

**Decision for planner:** events are pydantic-only (NOT SQLModel `table=True`). Either:
- Place in new `app/models/events.py` (cleaner separation; matches RESEARCH layout line 296), or
- Append classes to `app/models/schemas.py`.

**Pattern (RESEARCH lines 480-512 — copy verbatim):** discriminated union via `type: Literal["..."]` + Python 3.12 `match` dispatch.

---

### `app/templates/partials/backfill_banner.html` (NEW — partial)

**Analog:** `app/templates/partials/sync_banner.html` (lines 1-97) is the closest match — almost a verbatim copy with copy changes.

**HTMX polling pattern** (from `sync_banner.html:1-21`):
```jinja
{% if state == "running" %}
<div id="sync-banner"
     hx-get="/api/sync/status"
     hx-trigger="every 2s"
     hx-target="#sync-banner"
     hx-swap="outerHTML"
     class="mb-6 p-4 rounded-lg border border-accent/30 bg-accent/5">
  <div class="flex items-center justify-between mb-2">
    <span class="text-text-primary text-[15px] font-medium">
      Syncing... {{ sync_status.synced_tracks | default(0) }} / {{ sync_status.total_tracks | default(0) }} tracks
    </span>
    <span class="text-text-secondary text-[13px]">
      {{ ((sync_status.synced_tracks / sync_status.total_tracks) * 100) | int if sync_status.total_tracks else 0 }}%
    </span>
  </div>
  <div class="w-full bg-surface-elevated rounded-full h-2">
    <div class="bg-accent h-2 rounded-full transition-all duration-500"
         style="width: {{ ((sync_status.synced_tracks / sync_status.total_tracks) * 100) if sync_status.total_tracks else 0 }}%"></div>
  </div>
</div>
{% elif state == "error" %} ... {% elif state == "completed" %} ... {% else %} ... {% endif %}
```

**Copy with changes:**
- Banner id `backfill-banner`, polling endpoint `/api/rating-sync/status`.
- Field names `backfilled_tracks` / `total_tracks` (mirrors `BackfillStatus` from RESEARCH §"Auto-backfill" lines 1187-1195).
- Five state branches: running / error / failed / completed / idle (same shape as sync_banner).

**Color tokens used in the design system** (observed across both banners):
- Running: `border-accent/30 bg-accent/5`
- Error/failed: `border-error/30 bg-error/5`
- Completed: `border-border bg-surface-primary`
- Buttons: `bg-accent hover:bg-accent-hover text-surface-primary font-semibold px-4 py-2 rounded-lg`

---

### `app/templates/pages/debug_events.html` (NEW — page)

**Analog:** `app/templates/pages/library.html` (lines 1-31) for the page-with-banners shape.

**Page skeleton** (from `library.html:1-31`):
```jinja
{% extends "base.html" %}

{% block title %}Library - Composer{% endblock %}

{% block content %}
<div class="pb-12">
  <h1 class="text-[28px] font-semibold text-text-primary mb-6">Library</h1>

  {# Sync banner: shows Sync Now button, progress, or last synced status #}
  {% include "partials/sync_banner.html" %}
  ...
</div>
{% endblock %}
```

**Copy with changes:** debug_events.html follows the same `extends "base.html"` + `{% block content %}` shape. RESEARCH lines 1316-1370 has the full body draft (table of last-50 events + diagnostics dl).

**Tailwind table-rendering observed** (RESEARCH lines 1345-1367): `text-xs font-mono` for diagnostic data, `bg-error/5` highlight on error rows.

---

### `app/database.py` `_migrate_add_columns()` extension (modify line 48)

**Self-extend pattern** (current code at lines 48-99):
```python
def _migrate_add_columns(engine) -> None:
    """Add any missing columns to existing tables (lightweight schema migration)."""
    import logging
    import sqlite3
    url = str(engine.url)
    db_path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get existing columns for the track table
    cursor.execute("PRAGMA table_info(track)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # Columns added in Phase 3 (audio feature extraction)
    new_columns = {
        "file_path": "TEXT",
        "energy": "REAL",
        # ... existing entries ...
    }

    for col_name, col_type in new_columns.items():
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE track ADD COLUMN {col_name} {col_type}")
```

**Apply (Phase 5 additions to the new_columns dict):**
```python
new_columns = {
    # ... existing Phase 3 entries kept ...
    # Phase 5 additions (D-15):
    "user_rating": "REAL",
    "last_viewed_at": "TEXT",
    "view_count": "INTEGER DEFAULT 0",
    "rating_changed_at": "TEXT",
}
```

**Add `init_db()` registrations** (current `init_db()` lines 102-116 — extend the import block at line 105-107):
```python
# Import models to register them with SQLModel metadata
from app.models.settings import ServiceConfig  # noqa: F401
from app.models.track import Track, SyncState  # noqa: F401
from app.models.playlist import Playlist, PlaylistTrack  # noqa: F401
# Phase 5 additions:
from app.models.event_log import EventLog  # noqa: F401
from app.models.llm_usage import LLMUsage  # noqa: F401
from app.models.taste_profile import TasteProfile  # noqa: F401
```

**Add new index for RATE-04** (after the existing ALTER loop):
```python
cursor.execute("CREATE INDEX IF NOT EXISTS ix_track_user_rating ON track(user_rating)")
```

---

### `app/main.py` `lifespan()` extension (modify line 19)

**Current pattern** (lines 19-26):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database, encryption key, and sync scheduler. Shutdown: cleanup."""
    init_db()
    get_encryptor()  # Generate encryption key on first startup
    await start_scheduler()
    yield
    await stop_scheduler()
```

**Extended pattern (RESEARCH lines 397-413 — copy with minor adjustments):**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    get_encryptor()
    # Order matters: queue must exist BEFORE scheduler starts (poll job uses it).
    from app.services.event_bus import get_event_bus, start_dispatcher, stop_dispatcher
    get_event_bus()         # 1. ensure queue exists
    await start_dispatcher() # 2. start consumer
    await start_scheduler()  # 3. now the poll_job can publish into the queue
    # 4. trigger one-time auto-backfill if Track.user_rating is NULL for all rows
    from app.services.backfill_service import maybe_trigger_first_run_backfill
    asyncio.create_task(maybe_trigger_first_run_backfill())
    yield
    await stop_scheduler()
    await stop_dispatcher()
```

**Router includes (line 33-40):** add `app.include_router(api_webhooks.router)` and `app.include_router(api_rating_sync.router)` (or `api_debug.router` if separate from pages).

---

### `app/services/plex_client.py` `_map_track()` extension (modify line 8)

**Current pattern** (lines 8-31):
```python
def _map_track(t) -> dict:
    """Map a PlexAPI Track object to a dict with standard field names."""
    file_path = None
    try:
        if hasattr(t, "media") and t.media:
            parts = t.media[0].parts
            if parts:
                file_path = parts[0].file
    except (IndexError, AttributeError):
        pass

    return {
        "plex_rating_key": str(t.ratingKey),
        "title": t.title or "",
        "artist": t.grandparentTitle or "",
        "album": t.parentTitle or "",
        "genre": ", ".join(g.tag for g in (t.genres or [])),
        "year": t.year,
        "duration_ms": t.duration or 0,
        "added_at": t.addedAt.isoformat() if t.addedAt else None,
        "updated_at": t.updatedAt.isoformat() if t.updatedAt else None,
        "file_path": file_path,
    }
```

**Apply Phase 5 additions** (RESEARCH lines 1248-1278, mirrors `getattr(t, ...)` defensive style):
```python
return {
    # ... existing fields preserved ...
    "user_rating": getattr(t, "userRating", None),  # raw 0-10; None if unrated (Pitfall 5)
    "last_viewed_at": (lv.isoformat() if (lv := getattr(t, "lastViewedAt", None)) else None),
    "view_count": getattr(t, "viewCount", 0) or 0,
}
```

**Convention point:** existing code uses `t.title or ""` for required fields and `getattr(t, "...", default)` for optional ones. The 3 new fields are all optional → use `getattr` with default.

---

### `tests/test_event_bus.py` etc. (NEW tests)

**Analogs:** `tests/test_sync_scheduler.py` (asyncio singleton + `pytest.fixture(autouse=True)` to reset module-level state), `tests/test_sync_service.py` (async run-loop tests with mocks), `tests/test_track_model.py` (SQLModel + IntegrityError patterns), `tests/test_sync_api.py` (TestClient + lifespan fixture).

**Reset-singleton fixture pattern** (from `test_sync_scheduler.py:11-23`):
```python
@pytest.fixture(autouse=True)
def reset_scheduler_singleton():
    """Reset the module-level scheduler singleton between tests."""
    sync_scheduler._scheduler = None
    yield
    if sync_scheduler._scheduler is not None:
        try:
            if sync_scheduler._scheduler.running:
                sync_scheduler._scheduler.shutdown(wait=False)
        except Exception:
            pass
    sync_scheduler._scheduler = None
```

Apply to `test_event_bus.py`: `_queue = None; _dispatcher_task = None` reset.

**Async run-loop test pattern** (from `test_sync_service.py:47-101`):
```python
def _run_async(coro):
    """Helper to run async coroutines in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _reset_sync_status():
    """Reset the module-level sync status to IDLE."""
    import app.services.sync_service as svc
    svc._sync_status = svc.SyncStatus()


class TestRunSync:
    def setup_method(self):
        _reset_sync_status()

    @patch("app.services.sync_service.get_library_tracks")
    @patch("app.services.sync_service.get_decrypted_credential")
    @patch("app.services.sync_service.get_setting")
    def test_full_sync_fetches_in_batches(self, ...):
        mock_setting.return_value = _make_mock_setting()
        # ...
        _run_async(run_sync())
        from app.services.sync_service import get_sync_status, SyncStateEnum
        status = get_sync_status()
        assert status.state == SyncStateEnum.COMPLETED
```

**TestClient + DB fixture pattern** (from `test_sync_api.py:13-46`):
```python
@pytest.fixture
def client(test_engine):
    """Create a test client with fresh database."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)
```

For Phase 5 router tests, extend the `client` fixture to also create the new tables (`EventLog`, `LLMUsage`, `TasteProfile`).

**SQLModel UNIQUE constraint test** (from `test_track_model.py:55-68`):
```python
def test_plex_rating_key_is_unique(self, db_with_tracks):
    """Inserting duplicate plex_rating_key raises IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    track1 = Track(plex_rating_key="99999", title="Song A", artist="Artist A")
    db_with_tracks.add(track1)
    db_with_tracks.commit()

    track2 = Track(plex_rating_key="99999", title="Song B", artist="Artist B")
    db_with_tracks.add(track2)
    with pytest.raises(IntegrityError):
        db_with_tracks.commit()
```

**Apply to `test_event_log.py`:** the test for EventLog dedupe uses this exact pattern with `dedupe_key`. Equivalent test must verify `INSERT OR IGNORE` no-ops in the dispatcher path.

**Mock-SDK pattern for `test_anthropic_client.py`** (from `test_service_clients.py:11-39` — TestPlexClient style):
```python
@pytest.mark.asyncio
class TestAnthropicClient:
    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_explicit_ttl_1h(self, mock_anthropic_cls):
        mock_client = MagicMock()
        # ... configure response with usage.cache_creation_input_tokens etc ...
        mock_anthropic_cls.return_value = mock_client
        # ... assert mock_client.messages.create.call_args matches cache_control={"type": "ephemeral", "ttl": "1h"}
```

**Pure helper test** (from `test_track_model.py:122-135` style — no fixtures):
```python
def test_audio_feature_columns_indexed(self, db_with_tracks):
    """energy, danceability, valence columns have database indexes."""
    inspector = inspect(db_with_tracks.get_bind())
    indexes = inspector.get_indexes("track")
    indexed_columns = set()
    for idx in indexes:
        for col in idx["column_names"]:
            indexed_columns.add(col)

    assert "energy" in indexed_columns
```

Apply to `test_track_model.py::test_user_rating_indexed` extension.

---

### Test removal in `tests/test_service_clients.py` (DELETE lines 80-130)

**Lines to remove** (verified at `tests/test_service_clients.py:80-130`): the `class TestOllamaClient:` block — three `@pytest.mark.asyncio` test methods with `@patch("app.services.ollama_client.OpenAI")`.

This must ship in the same commit as deleting `app/services/ollama_client.py` (RESEARCH §"Pitfall 9").

---

## Shared Patterns

### Pattern A — Module-level singleton state (used everywhere)

**Source:** `app/services/sync_service.py:37-42`, `app/services/analysis_service.py:67-72`, `app/services/sync_scheduler.py:17-25`

**Apply to:** every new service module (`event_bus.py`, `event_dispatcher.py`, `rating_sync_service.py`, `backfill_service.py`).

```python
# Module-level singleton — match the existing sync_service / analysis_service / sync_scheduler idiom.
_state: Optional[StateClass] = None  # leading-underscore for private


def get_state() -> StateClass:
    global _state
    if _state is None:
        _state = StateClass()
    return _state
```

**Convention:** test files reset this via a `setup_method()` or `pytest.fixture(autouse=True)` (see `test_sync_scheduler.py:11-23`).

---

### Pattern B — Async wrapper around sync DB operation (`asyncio.to_thread`)

**Source:** `app/services/sync_service.py:45-88`, `app/services/analysis_service.py:122-183`, `app/services/plex_client.py:45-52`

**Apply to:** every event handler that touches DB or PlexAPI (D-09 hard rule).

```python
def _operation_sync(arg) -> result:
    """Synchronous DB operation. Called via to_thread."""
    with Session(get_engine()) as session:
        # ... synchronous SQLModel calls ...
        session.commit()


async def _operation(arg) -> result:
    """Async wrapper. Always exists when there's a `_*_sync` helper."""
    return await asyncio.to_thread(_operation_sync, arg)
```

**Style:** the `_*_sync` suffix is a Composer convention. Mirror it for: `_insert_event_log_sync`, `_update_track_rating_sync`, `_upsert_taste_profile_sync`, `_log_llm_usage_sync`.

---

### Pattern C — Pre-flight concurrency guard

**Source:** `app/services/sync_service.py:152-159`, `app/services/analysis_service.py:194-198`

**Apply to:** every `run_*` entry point that should not run concurrently (`run_backfill`, `run_resync`, `run_poll`, `recompute_taste_profile`).

```python
async def run_backfill() -> None:
    global _backfill_status
    if _backfill_status.state == BackfillStateEnum.RUNNING:
        return
    _backfill_status = BackfillStatus(state=BackfillStateEnum.RUNNING)
    try:
        # ... main work ...
        _backfill_status.state = BackfillStateEnum.COMPLETED
    except Exception as exc:
        _backfill_status.state = BackfillStateEnum.FAILED
        _backfill_status.error = str(exc)
        logger.exception("Backfill failed")
```

---

### Pattern D — HTMX banner partial (running / error / completed / idle states)

**Source:** `app/templates/partials/sync_banner.html`, `app/templates/partials/analysis_banner.html`

**Apply to:** `backfill_banner.html`, `webhook_test_indicator.html`.

**Five-branch structure:**
```jinja
{% if state == "running" %}
  <div id="<banner-id>" hx-get="/api/.../status" hx-trigger="every 2s" hx-target="#<banner-id>" hx-swap="outerHTML"
       class="mb-6 p-4 rounded-lg border border-accent/30 bg-accent/5"> ... </div>
{% elif state == "error" %}
  <div id="<banner-id>" class="mb-6 p-4 rounded-lg border border-error/30 bg-error/5"> ... </div>
{% elif state == "failed" %}
  ... (similar to error; may include retry button) ...
{% elif state == "completed" or (state == "idle" and last_synced) %}
  <div id="<banner-id>" class="mb-6 flex items-center justify-between"> ... </div>
{% else %}
  ... idle initial state ...
{% endif %}
```

**Color tokens (used everywhere):**
- Accent (running): `accent/30` border, `accent/5` background, `bg-accent` button.
- Error: `error/30` border, `error/5` background, `text-error`.
- Warning (paused): `warning/30` border, `warning/5` background.
- Surface: `surface-primary`, `surface-elevated`.
- Text: `text-primary`, `text-secondary`.

**HTMX swap convention** (CONTEXT §"Established Patterns"): use `hx-swap="outerHTML"` for banners; `morph` extension only when partials contain Alpine state.

---

### Pattern E — Lazy template import in router

**Source:** `app/routers/api_sync.py:21-24`, `app/routers/pages.py:19-22`, `app/routers/api_settings.py:22-25`

**Apply to:** every new router file (`api_webhooks.py`, `api_rating_sync.py`, debug events route).

```python
def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates
```

---

### Pattern F — `from __future__ import annotations` everywhere

**Source:** every Composer file (`sync_service.py:1`, `plex_client.py:1`, `models/track.py:1`, `routers/api_sync.py:1`, `tests/test_*.py:1`).

**Apply to:** every new `.py` file Phase 5 creates.

---

### Pattern G — Settings access via factory + decryption

**Source:** `app/services/llm_client.py:53-74`, `app/services/sync_service.py:163-179`

**Apply to:** `anthropic_client.get_anthropic_client_v2`, `taste_profile_service` (when calling Anthropic), any client touching encrypted credentials.

```python
def get_anthropic_client(db_session) -> tuple:
    setting = get_setting(db_session, "anthropic")
    if setting is None or not setting.is_configured:
        raise ValueError("Anthropic is not configured. Set up your API key in Settings first.")
    api_key = get_decrypted_credential(db_session, "anthropic")
    if not api_key:
        raise ValueError("Anthropic API key not found. Please reconfigure in Settings.")
    return api_key, model_name
```

**Convention:** raise `ValueError` (not custom exception) with a user-readable message. v1 exclusively does this.

---

### Pattern H — Test client + `SQLModel.metadata.create_all` + `TestClient` lifespan

**Source:** `tests/test_sync_api.py:13-46`, `tests/conftest.py:13-67`

**Apply to:** `test_api_webhooks.py`, `test_api_rating_sync.py`, `test_pages.py::test_debug_events_page`.

```python
@pytest.fixture
def client(test_engine):
    """Create a test client with fresh database."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    # Phase 5 additions:
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)
```

---

## No Analog Found

| File | Role | Data Flow | Reason | Recommendation |
|------|------|-----------|--------|----------------|
| `app/services/webhook_url_detection.py` | utility | helpers (stdlib) | No prior code uses `socket`/`psutil`/`ipaddress` for network introspection. | Copy verbatim from RESEARCH lines 596-647. Apply Pattern F (`from __future__ import annotations`). Keep functions returning `Optional[str]` for graceful failure (mirrors `plex_client.py` defensive style). |
| `app/models/events.py` | pydantic-only | discriminated union | No prior pydantic-only event types. `models/schemas.py` has `FeatureCriteria` (similar shape) but no Literal-discriminated union. | Copy verbatim from RESEARCH lines 480-512. Use Python 3.12 `match` statement on `event.type` in dispatcher (Pattern 6 in research, Pattern A in this doc for module placement). |

Both files have small surface area (≤80 lines), so the absence of a direct analog is low-risk. Planner should follow project-wide conventions (Patterns F, G, file naming).

---

## Metadata

**Analog search scope:** `app/services/`, `app/routers/`, `app/models/`, `app/templates/pages/`, `app/templates/partials/`, `tests/`
**Files scanned (read in full or targeted sections):** `sync_service.py`, `analysis_service.py`, `sync_scheduler.py`, `llm_client.py`, `ollama_client.py`, `plex_client.py`, `database.py`, `main.py`, `playlist_engine.py` (head), `settings_service.py`, `models/track.py`, `models/settings.py`, `routers/api_sync.py`, `routers/api_health.py`, `routers/api_settings.py` (head), `routers/pages.py`, `templates/partials/sync_banner.html`, `templates/partials/analysis_banner.html`, `templates/pages/library.html`, `templates/pages/settings.html`, `tests/conftest.py`, `tests/test_sync_service.py`, `tests/test_sync_scheduler.py`, `tests/test_sync_api.py` (head), `tests/test_service_clients.py`, `tests/test_track_model.py`, `tests/test_database.py`, `requirements.txt`.
**Pattern extraction date:** 2026-05-08

**Verified canonical references the planner can rely on:**
- `app/services/sync_service.py:37-42` — singleton state dataclass
- `app/services/sync_service.py:45-88` — `_*_sync` helper + async wrapper convention
- `app/services/sync_service.py:152-159` — concurrency guard
- `app/services/analysis_service.py:285-309` — auto-trigger gated on DB state
- `app/services/sync_scheduler.py:28-44` — APScheduler `add_job` + `replace_existing` pattern
- `app/services/sync_scheduler.py:52-89` — `start_scheduler` with auto-sync on first deploy
- `app/services/plex_client.py:8-31` — `_map_track` defensive `getattr` style
- `app/services/plex_client.py:45-52` — `asyncio.to_thread` wrapping every PlexAPI call
- `app/services/llm_client.py:53-74` — credential factory pattern (do NOT modify file; use as reference only)
- `app/database.py:48-99` — schema migration shim to extend
- `app/database.py:102-116` — `init_db()` model registration
- `app/main.py:19-26` — `lifespan()` to extend
- `app/models/track.py:1-38` — SQLModel table definition style
- `app/routers/api_sync.py:21-83` — HTMX-aware router with banner partials
- `app/routers/pages.py:19-22, 83-142` — page-route pattern with context dict
- `app/templates/partials/sync_banner.html:1-97` — five-state HTMX banner (running/error/failed/completed/idle)
- `tests/conftest.py:13-67` — `tmp_data_dir` + `test_engine` + `test_db` fixtures (already covers Phase 5 needs)
- `tests/test_sync_scheduler.py:11-23` — `autouse=True` singleton-reset fixture
- `tests/test_sync_service.py:47-101` — async run-loop test with `_run_async` helper
- `tests/test_sync_api.py:13-46` — TestClient + DB-creation fixture
- `tests/test_track_model.py:55-68` — SQLModel UNIQUE-constraint test (apply to `EventLog.dedupe_key`)
- `tests/test_track_model.py:122-135` — column-index test (apply to `user_rating` index assertion)
- `tests/test_service_clients.py:80-130` — DELETE this block in same commit as removing `app/services/ollama_client.py`
