# Phase 2: Library Sync - Research

**Researched:** 2026-04-09
**Domain:** Plex library synchronization, background job scheduling, HTMX polling, SQLModel data modeling
**Confidence:** HIGH

## Summary

Phase 2 syncs the user's full Plex music library (10k+ tracks) to local SQLite, with paginated fetching, delta sync, background job execution with progress UI, a full library browse page, and a bug fix for the Plex library selection form.

The PlexAPI library already handles pagination internally via `container_start`/`container_size` parameters on `MusicSection.searchTracks()`. Delta sync can be achieved by filtering on `addedAt` (tracks added after last sync timestamp). The background job should use `asyncio.create_task` with in-memory state tracking (no need for APScheduler for the sync job itself -- APScheduler is for the recurring schedule). Progress display uses the existing HTMX polling pattern with a new progress banner partial.

**Primary recommendation:** Use PlexAPI's built-in `searchTracks()` with `container_start`/`container_size` for paginated fetching, `addedAt` filtering for delta sync, `asyncio.create_task` for background execution, APScheduler 3.x `AsyncIOScheduler` for recurring schedule, and HTMX `hx-get` polling on a 2-second interval for the progress banner.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Sync can be triggered manually via a "Sync Now" button on the library page
- **D-02:** Sync also runs on a configurable schedule (user sets interval in settings, e.g., every 6/12/24 hours)
- **D-03:** Auto-sync on app startup if Plex is configured and no sync has run yet
- **D-04:** Schedule interval is configurable in the settings page (add to existing Plex config section or a new "Sync" section)
- **D-05:** Sync progress shown as an inline banner at the top of the library page: "Syncing... 4,521 / 10,234 tracks" with a progress bar
- **D-06:** Banner disappears when sync completes, replaced by "Last synced: [timestamp]" and track count
- **D-07:** Progress updates via HTMX polling every 2-3 seconds (matches existing HTMX patterns from Phase 1)
- **D-08:** Full library browse page at `/library` with a table showing: title, artist, album, genre, year
- **D-09:** Table supports pagination, search (across title/artist/album), and column sorting
- **D-10:** "Library" link added to the top nav bar (separate from home page)
- **D-11:** Sync Now button and last sync status displayed above the table
- **D-12:** Fix Plex library selection bug -- user selects "Music" library but a different library is shown in settings config. Investigate and fix the library ID/name mapping in the Plex settings flow.

### Claude's Discretion
- Exact pagination size (25/50/100 per page)
- Search debounce timing
- Track model field types and indexes
- Background job implementation (APScheduler vs asyncio task)
- Delta sync detection strategy (Plex updatedAt field vs full comparison)

### Deferred Ideas (OUT OF SCOPE)
- Plex library selection bug (D-12) is noted as a Phase 1 fix that should be addressed early in Phase 2 execution

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SYNC-01 | App syncs user's full Plex music library to a local SQLite database with paginated fetching | PlexAPI `searchTracks()` with `container_start`/`container_size` handles pagination; Track SQLModel stores data locally |
| SYNC-02 | After initial sync, app performs delta sync -- only importing new or changed tracks | Filter on `addedAt>>` (PlexAPI filter syntax) using stored last-sync timestamp; also compare `updatedAt` for changed tracks |
| SYNC-03 | Library sync runs as a background job with progress indicator visible in the UI | `asyncio.create_task` for background execution; in-memory `SyncStatus` object; HTMX polling endpoint returns progress partial |
| SYNC-04 | Sync captures track metadata: title, artist, album, genre, year, duration, Plex ratingKey | All fields available on PlexAPI `Track` object: `title`, `grandparentTitle` (artist), `parentTitle` (album), `genres`, `year`, `duration`, `ratingKey` |

</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Deployment**: Single Docker container with compose YAML
- **Security**: API keys never exposed in UI or API responses after initial configuration
- **Stack**: Python 3.12+ / FastAPI / SQLModel / SQLite / Jinja2 / HTMX / Alpine.js / Tailwind CSS
- **Existing patterns**: `asyncio.to_thread()` wrapping for sync PlexAPI calls, FastAPI router organization (`api_*.py` for API, `pages.py` for HTML), HTMX partial swap pattern, lazy engine singleton

## Standard Stack

### Core (already in requirements.txt)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PlexAPI | >=4.18,<5.0 | Plex server integration | Already used in Phase 1 for connection testing; `MusicSection.searchTracks()` handles paginated track fetching with built-in `container_start`/`container_size` [VERIFIED: requirements.txt] |
| SQLModel | >=0.0.38,<0.1 | Track model ORM | Already used for `ServiceConfig`; same pattern for new `Track` model [VERIFIED: requirements.txt] |
| FastAPI | >=0.135,<0.136 | API endpoints | Already the framework; new sync API router and library page route [VERIFIED: requirements.txt] |

### New Dependencies

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| APScheduler | 3.11.x (stable) | Recurring sync schedule | For the configurable interval sync (D-02). Use `AsyncIOScheduler` with `IntervalTrigger`. Do NOT use 4.x -- still alpha (4.0.0a6). [VERIFIED: PyPI shows 3.11.2 as latest stable, released Dec 2025] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| APScheduler for recurring | `asyncio` sleep loop | Simpler but no persistence, no cron syntax, harder to make configurable |
| APScheduler for recurring | FastAPI BackgroundTasks | Only per-request, not recurring -- wrong tool |
| In-memory sync status | Database sync status table | Persistence across restarts, but adds complexity for a status that's transient |

**Installation:**
```bash
pip install apscheduler>=3.11,<4.0
```

## Architecture Patterns

### Recommended Project Structure (new files for Phase 2)

```
app/
  models/
    track.py              # Track SQLModel + TrackResponse
    sync_status.py        # SyncStatus dataclass (in-memory state)
  services/
    plex_client.py        # EXTEND: add get_all_tracks(), get_tracks_since()
    sync_service.py       # NEW: orchestrates sync logic, delta detection
    sync_scheduler.py     # NEW: APScheduler setup, start/stop/reconfigure
  routers/
    api_sync.py           # NEW: POST /api/sync/start, GET /api/sync/status
    api_library.py        # NEW: GET /api/library/tracks (paginated, searchable)
    pages.py              # EXTEND: add /library route
  templates/
    pages/
      library.html        # NEW: full library browse page
    partials/
      sync_banner.html    # NEW: progress bar + status partial (HTMX target)
      track_table.html    # NEW: paginated track table partial (HTMX target)
      nav.html            # MODIFY: add Library link
```

### Pattern 1: Background Sync with In-Memory Progress

**What:** Sync runs as an `asyncio.Task`, updates an in-memory `SyncStatus` singleton. HTMX polls a status endpoint every 2 seconds.
**When to use:** For the sync job (D-05, D-06, D-07, SYNC-03).

```python
# Source: Established pattern from app/services/plex_client.py (asyncio.to_thread)
# [VERIFIED: codebase inspection]
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class SyncState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class SyncStatus:
    state: SyncState = SyncState.IDLE
    total_tracks: int = 0
    synced_tracks: int = 0
    last_synced: datetime | None = None
    error: str | None = None

# Singleton -- only one sync can run at a time
_sync_status = SyncStatus()

async def run_sync(plex_url: str, plex_token: str, library_id: str):
    """Run library sync as background task. Updates _sync_status in-place."""
    _sync_status.state = SyncState.RUNNING
    _sync_status.synced_tracks = 0
    try:
        # PlexAPI calls wrapped in asyncio.to_thread (sync library)
        plex = await asyncio.to_thread(PlexServer, plex_url, plex_token)
        section = await asyncio.to_thread(lambda: plex.library.sectionByID(int(library_id)))
        
        # Get total count first
        _sync_status.total_tracks = await asyncio.to_thread(lambda: section.totalSize)
        
        # Paginated fetch
        offset = 0
        batch_size = 200
        while offset < _sync_status.total_tracks:
            tracks = await asyncio.to_thread(
                section.searchTracks,
                container_start=offset,
                container_size=batch_size,
            )
            # Upsert tracks to database
            await _upsert_tracks(tracks)
            offset += len(tracks)
            _sync_status.synced_tracks = offset
            if len(tracks) < batch_size:
                break
        
        _sync_status.state = SyncState.COMPLETED
        _sync_status.last_synced = datetime.now(timezone.utc)
    except Exception as e:
        _sync_status.state = SyncState.FAILED
        _sync_status.error = str(e)
```

### Pattern 2: HTMX Polling for Progress

**What:** The sync banner partial uses `hx-get` with `hx-trigger="load, every 2s"` to poll the status endpoint. When sync completes, the response includes no further polling trigger.
**When to use:** For D-05, D-06, D-07.

```html
<!-- sync_banner.html partial -->
{% if sync_status.state == "running" %}
<div id="sync-banner" 
     hx-get="/api/sync/status" 
     hx-trigger="every 2s" 
     hx-target="#sync-banner" 
     hx-swap="outerHTML"
     class="...progress bar styles...">
  <div class="...">Syncing... {{ sync_status.synced_tracks }} / {{ sync_status.total_tracks }} tracks</div>
  <div class="w-full bg-gray-200 rounded-full h-2">
    <div class="bg-accent h-2 rounded-full" 
         style="width: {{ (sync_status.synced_tracks / sync_status.total_tracks * 100) if sync_status.total_tracks else 0 }}%"></div>
  </div>
</div>
{% elif sync_status.state == "completed" %}
<div id="sync-banner">
  Last synced: {{ sync_status.last_synced }} | {{ track_count }} tracks
</div>
{% elif sync_status.state == "idle" %}
<div id="sync-banner">
  <!-- No active sync, no polling -->
</div>
{% endif %}
```

### Pattern 3: Server-Side Pagination with HTMX

**What:** The track table uses query parameters for page, search, sort. HTMX replaces the table body on navigation/search/sort.
**When to use:** For D-08, D-09.

```python
# api_library.py
@router.get("/api/library/tracks", response_class=HTMLResponse)
async def get_tracks(
    request: Request,
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    sort: str = "title",
    order: str = "asc",
    session: Session = Depends(get_session),
):
    query = select(Track)
    if search:
        search_filter = f"%{search}%"
        query = query.where(
            or_(
                Track.title.ilike(search_filter),
                Track.artist.ilike(search_filter),
                Track.album.ilike(search_filter),
            )
        )
    # Dynamic sort
    sort_col = getattr(Track, sort, Track.title)
    query = query.order_by(sort_col.asc() if order == "asc" else sort_col.desc())
    # Pagination
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    tracks = session.exec(query.offset((page - 1) * per_page).limit(per_page)).all()
    # Return partial or full page based on HTMX header
    ...
```

### Pattern 4: Delta Sync via addedAt

**What:** Store the timestamp of the last successful sync. On subsequent syncs, use PlexAPI's `addedAt>>` filter to fetch only tracks added after that timestamp. Also do a full scan periodically to catch metadata updates.
**When to use:** For SYNC-02.

```python
# Delta sync approach
async def delta_sync(section, last_sync_time: datetime):
    """Fetch only tracks added since last sync."""
    # PlexAPI supports addedAt>> filter (greater than)
    new_tracks = await asyncio.to_thread(
        section.searchTracks,
        filters={"addedAt>>": last_sync_time.strftime("%Y-%m-%d")},
    )
    return new_tracks
```

### Anti-Patterns to Avoid

- **Fetching all 10k tracks in one call without pagination:** PlexAPI handles pagination internally but the default container_size may be too large. Explicitly set `container_size=200` to control memory and enable progress tracking.
- **Blocking the HTTP request on sync:** Never run sync synchronously in a route handler. Always use `asyncio.create_task()`.
- **Using APScheduler for the one-off sync trigger:** APScheduler is for the recurring schedule only. Manual sync uses `asyncio.create_task()` directly.
- **Storing sync progress in the database:** Sync status is transient and updates every few hundred milliseconds. In-memory dataclass is correct; database writes would thrash SQLite.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Plex API pagination | Custom HTTP pagination with X-Plex-Container-Start/Size headers | PlexAPI `searchTracks(container_start=N, container_size=M)` | Library handles XML parsing, auth headers, error handling [CITED: python-plexapi docs] |
| Recurring job scheduling | Custom asyncio sleep loop with interval tracking | APScheduler `AsyncIOScheduler` with `IntervalTrigger` | Handles missed runs, dynamic interval changes, graceful shutdown [CITED: APScheduler docs] |
| Server-side table pagination | Custom offset/limit logic with manual page calculation | SQLModel `query.offset().limit()` with `func.count()` for total | Standard SQLAlchemy pattern, well-tested |
| Search across columns | Custom string parsing and WHERE clause building | SQLModel `or_()` with `ilike()` on multiple columns | Standard ORM pattern |

**Key insight:** PlexAPI already abstracts the Plex XML API's pagination quirks. Do not make raw HTTP calls to the Plex server -- use the library's `MusicSection.searchTracks()` method which handles container pagination internally.

## Common Pitfalls

### Pitfall 1: Plex Library Selection Bug (D-12)

**What goes wrong:** User selects "Music" from the library dropdown but a different library name is saved to config. The `library_name` hidden `<select>` in `connection_status.html` always submits the first option (due to `{% if loop.first %}selected{% endif %}`) regardless of which `library_id` the user selects in the visible dropdown.
**Why it happens:** The visible `library_id` `<select>` and hidden `library_name` `<select>` are independent elements with no JavaScript sync logic. When the user changes the visible dropdown, the hidden one stays on its default (first option).
**How to avoid:** Replace the dual-select pattern. Either: (a) use a single `<select>` with `value=key` and `data-name=title`, using Alpine.js to populate a hidden input on change, or (b) look up the library name server-side from the library_id when saving.
**Warning signs:** Saved Plex config shows wrong library name in settings card.
[VERIFIED: codebase inspection of `connection_status.html` lines 29-39]

### Pitfall 2: PlexAPI Calls Are Synchronous

**What goes wrong:** Calling PlexAPI methods directly in async route handlers blocks the event loop, freezing the entire FastAPI application during multi-second API calls.
**Why it happens:** PlexAPI is a synchronous library using `requests` under the hood. It does not support `async/await`.
**How to avoid:** Always wrap PlexAPI calls in `asyncio.to_thread()`, following the existing pattern in `plex_client.py`. The sync service must use `asyncio.to_thread()` for every Plex interaction.
**Warning signs:** Application becomes unresponsive during sync. Other API calls time out.
[VERIFIED: codebase inspection of `plex_client.py` -- all calls wrapped in `asyncio.to_thread`]

### Pitfall 3: SQLite Write Contention During Sync

**What goes wrong:** Background sync task writes thousands of rows while the user is also querying the library table. Without WAL mode, writes block reads and vice versa.
**Why it happens:** SQLite default journal mode allows only one writer at a time, and writers block all readers.
**How to avoid:** WAL mode is already enabled in `database.py` (verified). WAL allows concurrent reads during writes. Use batch inserts (not row-by-row) to minimize write transaction duration. Commit in batches of 200 tracks.
**Warning signs:** Library page shows "database is locked" errors during sync.
[VERIFIED: `database.py` lines 24-26 set WAL mode on every connection]

### Pitfall 4: APScheduler Creates Multiple Scheduler Instances

**What goes wrong:** If the scheduler is initialized per-request or per-module-import, multiple scheduler instances run simultaneously, each triggering syncs.
**Why it happens:** FastAPI can create multiple workers (though single-container Uvicorn typically uses one). Module-level initialization can be triggered multiple times.
**How to avoid:** Initialize the `AsyncIOScheduler` once in the FastAPI `lifespan` context manager. Store as app state. Shut down on app shutdown.
**Warning signs:** Multiple concurrent syncs running, duplicate track entries.
[ASSUMED]

### Pitfall 5: Genre Field Is a List of Objects, Not a String

**What goes wrong:** Storing `track.genres` directly produces `[<Genre:Rock>, <Genre:Alternative>]` instead of actual genre names.
**Why it happens:** PlexAPI returns genre as a list of `Genre` media objects, not strings.
**How to avoid:** Extract genre tags: `", ".join([g.tag for g in track.genres])` and store as a comma-separated string or JSON array.
**Warning signs:** Genre column shows object representations instead of genre names.
[CITED: python-plexapi docs -- genres is List of Genre objects]

## Code Examples

### Track Model (SQLModel)

```python
# app/models/track.py
# [VERIFIED: follows existing ServiceConfig pattern from app/models/settings.py]
from __future__ import annotations
from typing import Optional
from sqlmodel import Field, SQLModel

class Track(SQLModel, table=True):
    """A synced track from the user's Plex music library."""
    id: Optional[int] = Field(default=None, primary_key=True)
    plex_rating_key: str = Field(unique=True, index=True)  # Plex unique ID
    title: str = Field(index=True)
    artist: str = Field(index=True)           # grandparentTitle from PlexAPI
    album: str = Field(default="", index=True) # parentTitle from PlexAPI
    genre: str = Field(default="")             # Comma-separated genre tags
    year: Optional[int] = Field(default=None)
    duration_ms: int = Field(default=0)        # Duration in milliseconds
    added_at: Optional[str] = Field(default=None)  # ISO timestamp from Plex
    updated_at: Optional[str] = Field(default=None) # ISO timestamp from Plex
    synced_at: Optional[str] = Field(default=None)  # When we last synced this track

class SyncState(SQLModel, table=True):
    """Tracks the last sync timestamp for delta sync."""
    id: Optional[int] = Field(default=None, primary_key=True)
    last_sync_started: Optional[str] = None
    last_sync_completed: Optional[str] = None
    total_tracks: int = 0
```

### Extending PlexClient for Track Fetching

```python
# Extend app/services/plex_client.py
# [VERIFIED: follows existing asyncio.to_thread pattern]
async def get_library_tracks(
    url: str, token: str, library_id: str,
    container_start: int = 0, container_size: int = 200,
) -> tuple[list[dict], int]:
    """Fetch a batch of tracks from Plex. Returns (tracks, total_size)."""
    plex = await asyncio.to_thread(PlexServer, url, token, timeout=30)
    section = await asyncio.to_thread(lambda: plex.library.sectionByID(int(library_id)))
    total = section.totalSize  # Total tracks in library
    
    tracks_raw = await asyncio.to_thread(
        section.searchTracks,
        container_start=container_start,
        container_size=container_size,
    )
    
    tracks = [
        {
            "plex_rating_key": str(t.ratingKey),
            "title": t.title or "",
            "artist": t.grandparentTitle or "",
            "album": t.parentTitle or "",
            "genre": ", ".join(g.tag for g in (t.genres or [])),
            "year": t.year,
            "duration_ms": t.duration or 0,
            "added_at": t.addedAt.isoformat() if t.addedAt else None,
            "updated_at": t.updatedAt.isoformat() if t.updatedAt else None,
        }
        for t in tracks_raw
    ]
    return tracks, total
```

### APScheduler Integration with FastAPI Lifespan

```python
# app/services/sync_scheduler.py
# [ASSUMED: based on APScheduler 3.x AsyncIOScheduler docs]
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

_scheduler: AsyncIOScheduler | None = None

def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler

def schedule_sync(interval_hours: int):
    """Add or update the recurring sync job."""
    scheduler = get_scheduler()
    # Remove existing job if any
    if scheduler.get_job("library_sync"):
        scheduler.remove_job("library_sync")
    scheduler.add_job(
        trigger_sync,
        trigger=IntervalTrigger(hours=interval_hours),
        id="library_sync",
        replace_existing=True,
    )

# In app/main.py lifespan:
# scheduler = get_scheduler()
# scheduler.start()
# ... yield ...
# scheduler.shutdown()
```

### Library Browse Page with HTMX Search/Sort/Pagination

```html
<!-- Simplified pattern for library.html -->
<!-- [VERIFIED: follows existing HTMX patterns from Phase 1 partials] -->
<div>
  <!-- Sync banner (polled) -->
  <div id="sync-banner" hx-get="/api/sync/status" hx-trigger="load" hx-swap="outerHTML">
    <!-- Initial load fetches current status -->
  </div>
  
  <!-- Search input with debounce -->
  <input type="search" name="search" 
         hx-get="/api/library/tracks" 
         hx-trigger="input changed delay:300ms"
         hx-target="#track-table" 
         hx-swap="outerHTML"
         placeholder="Search tracks, artists, albums...">
  
  <!-- Track table (HTMX target) -->
  <div id="track-table" hx-get="/api/library/tracks" hx-trigger="load" hx-swap="outerHTML">
    <!-- Loaded via HTMX -->
  </div>
</div>
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| APScheduler 4.x | APScheduler 3.11.x (stable) | 4.x still alpha as of Apr 2025 | Use 3.x; 4.x has breaking API changes and is not production-ready [VERIFIED: PyPI] |
| Plex XML API direct | PlexAPI 4.18+ Python library | Ongoing | Library handles pagination, auth, XML parsing |
| Full re-fetch on every sync | Delta sync via `addedAt` filter | PlexAPI has supported filter kwargs for years | Reduces 10k track sync to only-new-tracks on subsequent runs |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | APScheduler `AsyncIOScheduler` works correctly with FastAPI's lifespan in a single-worker Uvicorn setup | Architecture Patterns / Pitfall 4 | LOW -- single worker is the standard Docker deployment, well-documented pattern |
| A2 | PlexAPI `searchTracks()` with `container_start`/`container_size` returns correct batches for large libraries | Pattern 1 code example | MEDIUM -- if pagination is buggy at 10k+ scale, may need to use raw API calls |
| A3 | PlexAPI `addedAt` filter with `>>` operator works for ISO date strings for delta sync | Pattern 4 code example | MEDIUM -- if filter syntax is different, delta sync approach needs adjustment |
| A4 | `section.totalSize` property returns the total track count without fetching all tracks | Pattern 1 code example | LOW -- standard PlexAPI property, but untested at scale |
| A5 | 50 tracks per page is a good default for library browse | Claude's Discretion | LOW -- easily configurable, 50 is standard for data tables |

## Open Questions

1. **Exact PlexAPI filter syntax for delta sync**
   - What we know: PlexAPI supports kwargs filters on `addedAt` using operators like `>>` (greater than)
   - What's unclear: Whether the filter accepts ISO datetime strings or only date strings, and whether `updatedAt` is also filterable for detecting metadata changes
   - Recommendation: Test against the actual Plex server during implementation. Fall back to full sync with in-memory comparison if filter doesn't work as expected.

2. **Sync schedule persistence across restarts**
   - What we know: APScheduler 3.x `AsyncIOScheduler` is in-memory by default
   - What's unclear: Whether we need to persist the schedule interval in the database and re-create the job on startup
   - Recommendation: Store the interval in `ServiceConfig.extra_config` for Plex (or a new sync config entry). Re-create the scheduled job from stored config on startup in the lifespan handler.

3. **How to handle tracks deleted from Plex**
   - What we know: Delta sync adds new tracks. No mention of handling deletions.
   - What's unclear: Whether we should detect and remove tracks that no longer exist in Plex
   - Recommendation: For v1, keep orphaned tracks. They don't harm anything and deletion detection requires a full library scan comparison. Can add a "full re-sync" option later.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.4.2 + pytest-asyncio |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `python -m pytest tests/ -x -q` |
| Full suite command | `python -m pytest tests/ -v` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-01 | Full library sync with paginated fetching | unit (mock PlexAPI) | `python -m pytest tests/test_sync_service.py::test_full_sync -x` | Wave 0 |
| SYNC-01 | Track model CRUD operations | unit | `python -m pytest tests/test_track_model.py -x` | Wave 0 |
| SYNC-02 | Delta sync only fetches new tracks | unit (mock PlexAPI) | `python -m pytest tests/test_sync_service.py::test_delta_sync -x` | Wave 0 |
| SYNC-03 | Sync status tracking (running/completed/failed) | unit | `python -m pytest tests/test_sync_service.py::test_sync_status -x` | Wave 0 |
| SYNC-03 | Sync API endpoints (start, status) | integration | `python -m pytest tests/test_sync_api.py -x` | Wave 0 |
| SYNC-04 | Track metadata fields correctly extracted from PlexAPI | unit (mock PlexAPI) | `python -m pytest tests/test_sync_service.py::test_track_extraction -x` | Wave 0 |
| D-09 | Library API pagination, search, sort | integration | `python -m pytest tests/test_library_api.py -x` | Wave 0 |
| D-12 | Plex library selection saves correct library | integration | `python -m pytest tests/test_settings_api.py::test_plex_library_selection -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `python -m pytest tests/ -x -q`
- **Per wave merge:** `python -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/test_track_model.py` -- covers SYNC-01, SYNC-04 (Track model CRUD)
- [ ] `tests/test_sync_service.py` -- covers SYNC-01, SYNC-02, SYNC-03, SYNC-04 (sync orchestration with mocked PlexAPI)
- [ ] `tests/test_sync_api.py` -- covers SYNC-03 (API endpoint integration tests)
- [ ] `tests/test_library_api.py` -- covers D-08, D-09 (library browse endpoints)

### Plex Library Selection Bug Analysis

**Root cause identified:** In `connection_status.html` (lines 29-39), the Plex save form has two `<select>` elements:
1. Visible: `<select name="library_id">` -- user interacts with this
2. Hidden: `<select name="library_name" class="hidden">` -- always submits first option due to `{% if loop.first %}selected{% endif %}`

These are not synchronized. When the user picks a different library from the visible dropdown, the hidden `library_name` select still submits the first library's title.

**Fix approach:** Remove the hidden `<select>`. Instead, add JavaScript (Alpine.js) to sync the selected library name to a hidden `<input>`, or resolve the library name server-side from the `library_id` during the save endpoint. The server-side approach is simpler and more reliable.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | N/A (single-user app) |
| V3 Session Management | no | N/A |
| V4 Access Control | no | N/A (single-user) |
| V5 Input Validation | yes | Pydantic/SQLModel validation on query params (page, search, sort); sanitize search input for SQL injection via ORM parameterized queries |
| V6 Cryptography | no | Plex token already encrypted in ServiceConfig (Phase 1) |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via search param | Tampering | SQLModel `ilike()` uses parameterized queries -- safe by default |
| Plex token exposure in sync logs | Information Disclosure | Never log the decrypted token; mask in error messages |
| Sync endpoint abuse (trigger many syncs) | Denial of Service | Check `SyncStatus.state` -- reject if already running |

## Sources

### Primary (HIGH confidence)
- [PlexAPI Library docs](https://python-plexapi.readthedocs.io/en/latest/modules/library.html) - MusicSection.searchTracks(), pagination params, filter kwargs
- [PlexAPI Audio docs](https://python-plexapi.readthedocs.io/en/latest/modules/audio.html) - Track class attributes (ratingKey, title, grandparentTitle, genres, duration, year, addedAt, updatedAt)
- [PlexAPI source (audio.py)](https://github.com/pkkid/python-plexapi/blob/master/plexapi/audio.py) - Track._loadData attributes verified
- [PlexAPI source (library.py)](https://github.com/pkkid/python-plexapi/blob/master/plexapi/library.py) - MusicSection.all(), search(), container_start/size
- [APScheduler PyPI](https://pypi.org/project/APScheduler/) - v3.11.2 latest stable (Dec 2025), v4.0.0a6 still alpha
- Codebase inspection: `app/services/plex_client.py`, `app/models/settings.py`, `app/database.py`, `app/routers/api_settings.py`, `app/templates/partials/connection_status.html`

### Secondary (MEDIUM confidence)
- [APScheduler + FastAPI patterns](https://rajansahu713.medium.com/implementing-background-job-scheduling-in-fastapi-with-apscheduler-6f5fdabf3186) - AsyncIOScheduler integration approach
- [APScheduler GitHub releases](https://github.com/agronholm/apscheduler/releases) - Version history confirms 4.x is alpha

### Tertiary (LOW confidence)
- PlexAPI `addedAt>>` filter syntax -- confirmed by docs but untested at scale with datetime values

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - PlexAPI and SQLModel already in use, APScheduler 3.x is well-established
- Architecture: HIGH - Extends existing patterns (asyncio.to_thread, HTMX partials, SQLModel models)
- Pitfalls: HIGH - Bug root cause verified via codebase inspection; sync patterns well-documented
- Delta sync: MEDIUM - PlexAPI filter syntax confirmed in docs but not tested against real 10k library

**Research date:** 2026-04-09
**Valid until:** 2026-05-09 (stable libraries, unlikely to change)
