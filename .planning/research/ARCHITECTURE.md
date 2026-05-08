# Architecture Research — v2.0 Music Companion

**Domain:** Continuous companion-loop on top of an existing v1.0 FastAPI/SQLite/HTMX music app
**Researched:** 2026-05-08
**Confidence:** HIGH (v1 patterns are observable; v2 surfaces are well-understood event-driven additions)

## Scope

This document covers ONLY the v2.0 surface area. The v1 architecture (FastAPI app factory, SQLite + SQLModel `Track` table, sync_service singleton with module-level `SyncStatus`, analysis_service singleton with 5-state machine + pause/resume, APScheduler-driven periodic sync, PlexAPI/pyarr clients, HTMX/Jinja2 UI) is taken as given. Every recommendation here either **extends an existing service**, **adds a new singleton service following the v1 pattern**, **adds a new model**, or **adds a new router**.

The over-arching design rule is: **single container, single process, single SQLite file, in-process asyncio**. Anything that wants Celery/Redis/RabbitMQ/separate workers is out of scope.

## System Overview

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         HTTP Surface (FastAPI routers)                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │ pages    │ │ api_sync │ │ api_     │ │ api_     │ │ api_webhooks   │  │
│  │ (HTMX)   │ │ (v1)     │ │ analysis │ │ settings │ │ /api/webhooks/ │  │
│  │          │ │          │ │ (v1)     │ │ (v1)     │ │ plex     [NEW] │  │
│  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤ ├────────────────┤  │
│  │ api_     │ │ api_     │ │ api_     │ │ api_     │                    │
│  │ vibes    │ │ suggest- │ │ setup    │ │ discover │                    │
│  │ [NEW]    │ │ ions[NEW]│ │ [NEW]    │ │ [NEW]    │                    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘                    │
├───────────────────────────────────────────────────────────────────────────┤
│                         In-Process Event Bus                              │
│         (asyncio.Queue + dispatcher task; module-level singleton)         │
│   produced by: webhook receiver, polling service, manual /api/* triggers  │
│   consumed by: rating handler, play handler, analyze-on-import handler    │
├───────────────────────────────────────────────────────────────────────────┤
│                            Service Layer                                  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐ │
│  │ sync_      │ │ analysis_  │ │ rating_    │ │ vibe_      │ │ suggest │ │
│  │ service    │ │ service    │ │ sync_      │ │ service    │ │ service │ │
│  │ (v1)       │ │ (v1)       │ │ service    │ │ [NEW]      │ │ [NEW]   │ │
│  │            │ │            │ │ [NEW]      │ │            │ │         │ │
│  ├────────────┤ ├────────────┤ ├────────────┤ ├────────────┤ ├─────────┤ │
│  │ event_bus  │ │ poll_      │ │ vibe_      │ │ taste_     │ │ discover│ │
│  │ [NEW]      │ │ service    │ │ clusterer  │ │ profile_   │ │ y_      │ │
│  │            │ │ [NEW]      │ │ [NEW]      │ │ service    │ │ service │ │
│  │            │ │            │ │            │ │ [NEW]      │ │ [NEW]   │ │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └─────────┘ │
├───────────────────────────────────────────────────────────────────────────┤
│                       Integration Clients (extended)                      │
│  ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐   │
│  │ plex_client     │ │ lidarr_client    │ │ llm_client (Anthropic +  │   │
│  │ (v1) — extend   │ │ (v1) — extend    │ │   Instructor) (v1)       │   │
│  │ with rating R/W │ │ with add-artist, │ │   re-purposed for vibe   │   │
│  │ + playlist CRUD │ │ recent-imports   │ │   clustering + ranking   │   │
│  │ + viewCount     │ │                  │ │                          │   │
│  └─────────────────┘ └──────────────────┘ └──────────────────────────┘   │
├───────────────────────────────────────────────────────────────────────────┤
│                              Data Layer                                   │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ SQLite (single file, WAL mode — already configured in v1)          │  │
│  │  Track            (v1, +userRating, +rating_changed_at,            │  │
│  │                   +viewCount, +last_viewed_at)                     │  │
│  │  Vibe             [NEW]  user-named vibe + cached centroid         │  │
│  │  TrackVibe        [NEW]  many-to-many membership w/ distance       │  │
│  │  ManagedPlaylist  [NEW]  links Vibe.id ↔ Plex playlist ratingKey   │  │
│  │  EventLog         [NEW]  webhook + poll dedupe + audit trail       │  │
│  │  TasteProfile     [NEW]  cached LLM-friendly taste summary (1 row) │  │
│  │  SetupState       [NEW]  wizard step + draft cluster persistence   │  │
│  │  SyncState        (v1)                                             │  │
│  │  ServiceConfig    (v1)                                             │  │
│  │  Playlist/PlaylistTrack (v1) — RETIRED with mood-chat              │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘
        │                       │                          │
   ┌────┴─────┐           ┌─────┴─────┐              ┌─────┴────┐
   │  Plex    │ ─webhook─▶│ Composer  │              │  Lidarr  │
   │ Server   │           │ Container │              │  Server  │
   │          │ ◀─poll/─  │           │              │          │
   │          │   write   │           │              │          │
   └──────────┘           └───────────┘              └──────────┘
```

## Component Responsibilities

| Component | Status | Responsibility |
|-----------|--------|----------------|
| `event_bus` (NEW) | new singleton | `asyncio.Queue` + single dispatcher task; routes typed events to handlers; persists raw event to `EventLog` for dedupe + audit |
| `api_webhooks` router (NEW) | new router | Receives `POST /api/webhooks/plex`; parses multipart payload; pushes typed event to `event_bus`; returns 200 OK in <50 ms |
| `rating_sync_service` (NEW) | new singleton, mirrors `sync_service` shape | Owns reading `userRating` + `viewCount` + `lastViewedAt` from Plex (full pull, batch on demand, single-track on-demand); writes to `Track`; emits `rating_changed` / `track_played` events when local state diverges |
| `poll_service` (NEW) | new singleton, APScheduler-triggered | Webhook-fallback only. Diffs Plex `viewCount`/`lastViewedAt`/`userRating` for rated tracks against local DB; emits the same events `api_webhooks` would |
| `vibe_service` (NEW) | new singleton | CRUD for `Vibe` + `TrackVibe` + `ManagedPlaylist`; on rating-changed event, slots track into matching vibes (audio-feature distance to cached centroid); pushes to Plex playlist |
| `vibe_clusterer` (NEW) | function module (not a long-lived singleton) | One-shot LLM call (Instructor + Anthropic) over rated tracks → returns 3–7 candidate clusters with names, centroids, member track IDs. Used by setup wizard and on user-requested re-cluster only |
| `taste_profile_service` (NEW) | new singleton | Recomputes the cached taste profile after re-cluster or significant rating-set change; provides text snapshot for LLM prompts (cached, prompt-cacheable on Anthropic) |
| `suggestions_service` (NEW) | new singleton + state machine | Owns the Composer Suggestions queue (Plex playlist + SQLite mirror). On `track_played` event for a Suggestions track: removes track, requests refill (audio-feature shortlist → LLM rank → top N) |
| `discovery_service` (NEW) | new singleton | Periodic + on-demand: asks LLM for "artists like your taste profile, NOT in library" → cross-checks against Track table → exposes via UI; one-click → calls `lidarr_client.add_artist` |
| `setup_wizard` (NEW) | router + small service | Multi-step flow (rating-source → propose vibes → name/edit → finalize); state persisted in `SetupState` table (one row, key/value-ish) — NOT in cookies |
| `plex_client` (extend) | v1 client | Add: `get_track_metadata(rating_key)` (single-track refresh), `get_rated_tracks(library_id)`, `create_playlist`/`update_playlist_items`/`delete_playlist`, `get_playlist_items` |
| `lidarr_client` (extend) | v1 client | Add: `add_artist(name, profile_id, root_folder)`, `get_recent_imports(since)` for analyze-on-import; **fix the connection-test bug from v1 (carried forward)** |

## Data Model Extensions

### Extending `Track` (additive only — keep v1 migration pattern)

```python
class Track(SQLModel, table=True):
    # ... v1 fields unchanged ...

    # v2.0 additions (all nullable for backwards-compat)
    user_rating: Optional[float] = Field(default=None, index=True)  # 0.0–10.0 (Plex stars × 2)
    rating_changed_at: Optional[str] = Field(default=None)
    last_viewed_at: Optional[str] = Field(default=None, index=True)
    view_count: int = Field(default=0)
```

Add columns via the existing `_migrate_add_columns()` shim in `app/database.py` — same approach as the Phase 3 audio-feature additions. **Don't introduce Alembic** for this; the lightweight ALTER-on-startup pattern is already working.

**Cross-cutting answer: where does the "rated set" live?** It's a **derived view** — `Track.user_rating > 0` (or `IS NOT NULL` if you treat 0-star as "explicitly cleared"). Reasoning:

- Plex is the source of truth for ratings; we mirror it. A separate `Rated` table would be a second cache to keep coherent.
- `user_rating` is already indexed → `WHERE user_rating IS NOT NULL` is fast at 10k tracks.
- Keeps the existing v1 query patterns (single Track table, rich filtering) intact.

### New tables

```python
class Vibe(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)                       # user-edited
    description: Optional[str] = None                   # LLM-suggested, user-editable
    # Cached centroid (audio-feature averages over members)
    centroid_energy: Optional[float] = None
    centroid_tempo: Optional[float] = None
    centroid_danceability: Optional[float] = None
    centroid_valence: Optional[float] = None
    # Optional spread (std dev) for soft-match thresholds
    spread_energy: Optional[float] = None
    spread_tempo: Optional[float] = None
    spread_danceability: Optional[float] = None
    spread_valence: Optional[float] = None
    created_at: str
    centroid_recomputed_at: Optional[str] = None
    is_active: bool = Field(default=True)

class TrackVibe(SQLModel, table=True):
    """Many-to-many membership with cached distance.
    Composite PK on (track_id, vibe_id)."""
    track_id: int = Field(foreign_key="track.id", primary_key=True)
    vibe_id: int = Field(foreign_key="vibe.id", primary_key=True)
    distance: float = Field(index=True)
    assigned_at: str
    assigned_by: str  # "cluster" | "auto-slot" | "manual"

class ManagedPlaylist(SQLModel, table=True):
    """Links a Composer-managed entity to its Plex playlist ratingKey."""
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)            # "vibe" | "suggestions"
    vibe_id: Optional[int] = Field(default=None, foreign_key="vibe.id", index=True)
    plex_rating_key: str = Field(unique=True, index=True)
    last_pushed_at: Optional[str] = None
    track_count: int = Field(default=0)

class EventLog(SQLModel, table=True):
    """Dedupe + audit trail for inbound events (webhook + poll)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)          # "webhook" | "poll" | "manual"
    event_type: str = Field(index=True)      # "media.rate" | "media.scrobble" | ...
    plex_rating_key: Optional[str] = Field(default=None, index=True)
    dedupe_key: str = Field(unique=True, index=True)  # hash of (event_type, ratingKey, timestamp_bucket)
    received_at: str
    processed_at: Optional[str] = None
    raw_payload: Optional[str] = None        # JSON; truncate large fields

class TasteProfile(SQLModel, table=True):
    """Single-row cache (id=1). Recomputed on re-cluster or major rating-set change."""
    id: Optional[int] = Field(default=1, primary_key=True)
    rated_track_count: int = 0
    summary_text: str = ""                   # LLM-readable taste summary, prompt-cacheable
    feature_centroid_json: str = ""          # JSON of overall feature averages
    top_artists_json: str = ""               # JSON list of (artist, count) pairs
    top_genres_json: str = ""                # JSON list of (genre, count) pairs
    computed_at: Optional[str] = None
    vibe_signature: Optional[str] = None     # hash of vibe IDs to invalidate when vibes change

class SetupState(SQLModel, table=True):
    """Persistent multi-step wizard state. Single row (id=1)."""
    id: Optional[int] = Field(default=1, primary_key=True)
    step: str = Field(default="rating_source")   # rating_source | proposing | confirming | done
    draft_clusters_json: Optional[str] = None    # JSON of LLM-proposed Vibes pre-finalize
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
```

### Decision: TrackVibe link table vs computed-on-read

**Recommendation: link table (`TrackVibe`).** Here's the trade matrix:

| Concern | Computed on read | TrackVibe link table (recommended) |
|---------|------------------|-----------------------------------|
| Storage | 0 bytes | ~30 bytes/row × ~3 vibes/track × 460 rated = ~40 KB. Negligible. |
| Slot-on-rating-change | Compute distance to all vibes, sort, write — same effort either way | Same — but result is persisted |
| "Show me a vibe" | Scan all tracks, compute distance, filter → O(N·V) per page load | `WHERE vibe_id = ?` → instant |
| Drift when centroid updates | Always fresh (re-compute every time) | Stale until re-run; need invalidation hook |
| Manual override / "exclude this track from this vibe" | Impossible without a separate exception table | One row, `assigned_by="manual"`, easy |
| Multi-vibe membership | Trivial both ways | Trivial |

The recompute-on-centroid-change cost is bounded (only on re-cluster, which is user-triggered and rare per `PROJECT.md`'s "Re-clustering vibes on a schedule" being explicitly out of scope). On rating-changed events, you're computing distance for ONE track against 3–7 vibes — cheap regardless. The link table wins on read paths and on supporting manual overrides cleanly.

**Storage of distance:** keep it on `TrackVibe`. Lets the UI sort vibe members by fit, lets the slot logic skip tracks that are clearly outside any vibe's spread.

## Architectural Patterns

### Pattern 1: New services follow the v1 singleton + module-level dataclass status pattern

**What:** Module-level `_status` dataclass + `get_*_status()` accessor + `run_*()` async entry point + `stop_*()` for state-machine services. Status held in memory only; durable state goes to SQLite. Concurrent invocations guarded by checking the state enum at the top of `run_*()`.

**Why this is the right call:** It's already the v1 idiom (see `sync_service.py` lines 37–43, `analysis_service.py` lines 67–72). Consistency reduces cognitive load. Single-user single-container means in-memory status is fine — restarts re-derive from DB.

**Apply to:** `rating_sync_service`, `poll_service`, `suggestions_service`, `discovery_service`, `vibe_service`.

```python
# Template — apply to each new service
class RatingSyncStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class RatingSyncStatus:
    state: RatingSyncStateEnum = RatingSyncStateEnum.IDLE
    total: int = 0
    processed: int = 0
    error: Optional[str] = None

_status = RatingSyncStatus()

def get_rating_sync_status() -> RatingSyncStatus:
    return _status

async def run_rating_sync() -> None:
    global _status
    if _status.state == RatingSyncStateEnum.RUNNING:
        return  # idempotent guard, same as sync_service
    # ... orchestration ...
```

### Pattern 2: Webhook receiver = thin endpoint + asyncio.Queue + single dispatcher task

**What:** `POST /api/webhooks/plex` does three things, fast: (1) parse payload, (2) compute `dedupe_key`, (3) push to `asyncio.Queue`. Returns 200 immediately. A single long-running dispatcher task (started in `lifespan`) consumes the queue, looks up the dedupe key in `EventLog`, and dispatches to the right handler.

**Why a queue + dispatcher and not inline `BackgroundTasks`:**

- Plex retries webhooks on non-2xx; the receiver MUST return 200 in <1s (Plex Pro Week '25 guidance is "respond fast or you'll get duplicates").
- `BackgroundTasks` runs after response is sent — fine — but it spawns one task per request. With Plexamp scrubbing (potentially many `media.play`/`media.pause`/`media.resume` events), unbounded task spawning is messy. A queue gives you natural back-pressure and a single ordered consumer.
- A single dispatcher task means SQLite writes are serialized — no contention with `sync_service` or `analysis_service` (both already write to SQLite from background tasks).

**Why not Celery/Redis:** Single container constraint. Single user. Zero compelling reason. The whole event volume for one user is maybe 100 events/day on a busy listening day.

**Idempotency strategy:**

- `dedupe_key = sha256(f"{event_type}|{rating_key}|{timestamp_bucket_5s}")`. Plex's retry window is short; a 5-second bucket on the event timestamp prevents legitimate replays from hitting twice while letting genuinely separate plays of the same track 30 seconds apart through.
- Insert `EventLog` with `dedupe_key UNIQUE`. On `IntegrityError`, skip dispatch.
- For `media.rate` specifically: also dedupe on `(rating_key, new_rating)` if the rating value didn't change (Plex sometimes emits `media.rate` for re-saves of the same value).

```python
# api_webhooks.py
@router.post("/plex")
async def plex_webhook(request: Request):
    # Plex sends multipart/form-data with 'payload' field as JSON string
    form = await request.form()
    payload = json.loads(form["payload"])
    event_type = payload.get("event", "")
    rating_key = payload.get("Metadata", {}).get("ratingKey")
    timestamp = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()

    if not event_type.startswith("media."):
        return Response(status_code=200)  # ignore library.* etc.

    bucket = int(time.time() / 5)
    dedupe_key = hashlib.sha256(
        f"{event_type}|{rating_key}|{bucket}".encode()
    ).hexdigest()

    event = InboundEvent(
        source="webhook",
        event_type=event_type,
        rating_key=rating_key,
        dedupe_key=dedupe_key,
        payload=payload,
    )
    await event_bus.put(event)
    return Response(status_code=200)
```

### Pattern 3: Polling fallback — APScheduler job that emits the same event types

**What:** A `poll_service` job runs every N minutes (default: 5 min for active users; configurable). It queries Plex for tracks where `lastViewedAt > local.last_viewed_at` OR `userRating != local.user_rating`, and emits `track_played` / `rating_changed` events through the SAME `event_bus.put()`. Handlers don't know or care whether the event came from a webhook or a poll.

**Conflict resolution when both webhook and poll fire:** the dedupe key handles it. If the webhook arrived first, `EventLog` has the row, the poll's emit is rejected. If polling fires first (rare — would only happen if Plex Pass is unconfigured but you happen to poll right after a play), the webhook's emit is rejected.

**Polling frequency recommendation:**
- Default: every **5 minutes** (low cost — only fetches changed tracks via Plex `updatedAt` filtering — same trick already used in `sync_service.get_tracks_since`).
- After 30 minutes of zero events: back off to 15 min. After 2 hours of nothing: back off to 60 min.
- On webhook-received: reset the back-off timer (you know there's activity).
- If `media.scrobble` webhook fires: skip the next poll (saves a Plex round-trip).

**Cost:** 5-min poll × 460 rated tracks × cached `lastViewedAt` filter = one cheap Plex query every 5 min. At idle, near-zero NAS load.

### Pattern 4: Event types are explicit Pydantic models, not dicts

**What:** Define `InboundEvent` as a tagged union of `RatingChanged`, `TrackPlayed`, `LidarrImport`. Dispatcher does `match event:` (Python 3.12+).

**Why:** v1 already uses Pydantic everywhere (FastAPI, SQLModel, Instructor). Typed events = mypy catches dispatcher gaps; new event types force you to add a handler branch.

```python
class RatingChanged(BaseModel):
    type: Literal["rating_changed"] = "rating_changed"
    plex_rating_key: str
    new_rating: float | None
    source: Literal["webhook", "poll", "manual"]

class TrackPlayed(BaseModel):
    type: Literal["track_played"] = "track_played"
    plex_rating_key: str
    played_at: str
    source: Literal["webhook", "poll"]

class LidarrImport(BaseModel):
    type: Literal["lidarr_import"] = "lidarr_import"
    artist_name: str
    detected_at: str

InboundEvent = Annotated[
    RatingChanged | TrackPlayed | LidarrImport,
    Field(discriminator="type")
]
```

### Pattern 5: Suggestions queue — Plex is the user-visible truth, SQLite is the fast-react mirror

**What:** Mirror Suggestions playlist contents in `ManagedPlaylist` (the row) + a small `SuggestionsQueue` materialized view (or just query `TrackVibe` for tracks tagged `assigned_by="suggestions"`). On `track_played`:

1. Look up the track in the local mirror. If it's in the Suggestions queue, **act locally first**: remove from mirror, decide refill candidates, call LLM ranker, pick top N to add.
2. THEN reconcile to Plex: `update_playlist_items(playlist_rating_key, new_track_ids)` — single Plex call, replace contents.

**Why act locally first:** the LLM call and shortlist computation take 2–5 seconds. If you wait for Plex to acknowledge the play AND then re-fetch playlist contents, you've added a round trip and a race window where the user could play another track before refill finishes. Local-first means: the user keeps listening, you reconcile when ready, Plex eventually shows the new state.

**Why not let Plex be the only source:** because reading "current Suggestions queue" via PlexAPI requires fetching the playlist, and you want to react to events in <500 ms. SQLite read is sub-millisecond.

**Conflict case:** user manually removes a track from Suggestions in Plexamp. Composer doesn't notice until next sync of that playlist. Acceptable — Suggestions is meant to be auto-managed; manual edits aren't a primary use case. The next refill will reconcile (mirror gets rebuilt from Plex on every refill cycle's last step).

### Pattern 6: Setup wizard — server-side state in SQLite, NOT cookies/session

**What:** `SetupState` table, single row. Each wizard step posts to `/api/setup/<step>` which updates the row and returns the next step's HTMX partial. URL drives navigation (`/setup`, `/setup/propose`, `/setup/confirm`).

**Why not cookies:** the wizard's heaviest step is "LLM proposes 3–7 vibes from your 460 rated tracks" — that's a sizeable JSON blob (cluster names, descriptions, member track lists). Cookies would have to round-trip on every request; SQLite holds it once and the partial just reads.

**Why not pure URL-driven (stateless):** because step 2 ("propose vibes") takes 10–30s LLM time and you don't want to re-run it if the user refreshes. SQLite acts as the cache.

**HTMX integration:** each step is a Jinja2 partial. Server returns the next partial; HTMX swaps it in. Standard v1 idiom (see `partials/sync_banner.html`).

### Pattern 7: Taste profile — text summary cached as a string, with structured data alongside

**What:** Store both:
- `summary_text` — a few-sentence LLM-friendly description ("You like high-energy electronic, mid-tempo indie folk, and a sprinkle of jazz piano. Top artists: X, Y, Z. Avoiding: heavy metal."). This goes into the LLM system prompt for ranking and discovery.
- `feature_centroid_json`, `top_artists_json`, `top_genres_json` — structured raw data for any caller that needs to compute (e.g., a UI panel showing the user their own taste).

**Why both:** the text summary is what Anthropic's prompt-caching can amortize most efficiently (same prefix across many ranking calls = $0 marginal cost after first hit). The structured data is what UI surfaces and any deterministic logic want.

**Recompute trigger:** on `re-cluster` (rare, user-initiated) and on `rating-set delta exceeds 10% since last computation` (cheap check on each `rating_changed` event — increment a counter, recompute when threshold crossed). Don't recompute on every event — Anthropic call is the cost driver.

**Don't store as a vector list of every rated track.** That's what the underlying `Track` table is. A profile is a *summary*, not a copy.

## Data Flow

### Flow 1: Rating change (webhook path — Plex Pass)

```
User taps 4★ in Plexamp
        │
        ▼
Plex Server fires webhook (media.rate)
        │  POST /api/webhooks/plex (multipart, 'payload' field is JSON)
        ▼
api_webhooks.plex_webhook()
        │  1. Parse payload, extract ratingKey + new rating
        │  2. Compute dedupe_key (sha256 of event_type|key|timestamp_bucket)
        │  3. event_bus.put(RatingChanged(...))
        │  4. return 200 OK   (<50 ms total)
        ▼
event_bus dispatcher (single long-running asyncio task)
        │  1. INSERT INTO EventLog(dedupe_key, ...)  → IntegrityError? skip
        │  2. match event: case RatingChanged: → handle_rating_changed()
        ▼
handle_rating_changed()
        │  1. plex_client.get_track_metadata(rating_key) → confirm rating
        │     (defensive: webhook payload sometimes has stale rating)
        │  2. UPDATE Track SET user_rating, rating_changed_at WHERE plex_rating_key=...
        │  3. If new_rating > 0:
        │       vibe_service.slot_track(track_id)
        │         → compute distance to each Vibe.centroid
        │         → INSERT into TrackVibe for matches under threshold
        │         → for each matched vibe: plex_client.update_playlist_items(...)
        │  4. Increment "ratings since last profile recompute" counter
        │     If > 10% of rated set: taste_profile_service.recompute()  (async)
        │  5. UPDATE EventLog SET processed_at = now WHERE dedupe_key=...
```

### Flow 2: Track played (drives Suggestions refill)

```
User listens to a Suggestions track to completion
        │
        ▼
Plex Server fires media.scrobble webhook
        │  (Plex fires scrobble at ~90% playback)
        ▼
api_webhooks.plex_webhook() → event_bus.put(TrackPlayed(...))
        │
        ▼
dispatcher → handle_track_played()
        │  1. Dedupe via EventLog
        │  2. UPDATE Track SET last_viewed_at, view_count = view_count + 1
        │  3. Lookup: is this track in ManagedPlaylist(kind='suggestions')?
        │     Query: SELECT vibe_id FROM TrackVibe WHERE track_id=? AND assigned_by='suggestions'
        │  4. If yes:
        │       suggestions_service.handle_consumed(track_id)
        │         a. Remove from local mirror (DELETE TrackVibe row)
        │         b. shortlist = pick_audio_feature_candidates(taste_profile, exclude=played_set)
        │         c. ranked = llm_client.rank(shortlist, taste_profile)  ← Anthropic, prompt-cached
        │         d. Pick top-K to bring queue back to target size (default 30)
        │         e. INSERT new TrackVibe rows (assigned_by='suggestions')
        │         f. plex_client.update_playlist_items(suggestions_playlist_key, new_ids)
        │  5. UPDATE EventLog SET processed_at = now
```

### Flow 3: Lidarr import detected → analyze + score

```
APScheduler tick (every 30 min)
        │
        ▼
poll_service.check_lidarr_imports()
        │  GET /api/v1/history?since=last_check  (or recent imports endpoint)
        │  → list of newly-imported albums
        ▼
For each new album:
        │  Wait for next normal Plex sync to pick up tracks (or trigger run_sync())
        │  After sync, find new Track rows by added_at > last_check
        ▼
event_bus.put(LidarrImport(artist_name, ...)) per album
        │
        ▼
dispatcher → handle_lidarr_import()
        │  1. Find Track rows for the artist with analyzed_at IS NULL
        │  2. analysis_service.run_analysis() will already pick them up via existing
        │     trigger_post_sync_analysis() — no new code needed
        │  3. After analysis completes, for each newly-analyzed track:
        │       — pre-score against all Vibes (no playlist push; track is unrated)
        │       — eligible for Suggestions if distance to taste centroid < threshold
        │  4. UI surfaces "X new tracks from imported album are now in your library"
```

### Flow 4: Polling fallback (no Plex Pass)

```
APScheduler every 5 min (back-off when idle)
        │
        ▼
poll_service.poll_plex()
        │  1. plex_client.get_tracks_updated_since(library_id, last_poll_time)
        │     uses existing 'updatedAt>>' filter pattern from get_tracks_since()
        │  2. For each returned track:
        │     — Compare local Track.user_rating vs Plex userRating
        │       → diff: emit RatingChanged
        │     — Compare local Track.last_viewed_at vs Plex lastViewedAt
        │       → diff: emit TrackPlayed
        │  3. dedupe_key handles concurrent webhook+poll arrivals
        ▼
[Same handlers as webhook path — handlers are source-agnostic]
```

### Flow 5: First-run setup wizard

```
User opens Composer for first time
        │  (no Plex configured? → settings page first; v1 flow)
        ▼
GET /setup
        │  Reads SetupState row. Step = "rating_source".
        │  Renders partial: "Sync your rated tracks from Plex"
        ▼
POST /setup/sync-ratings (HTMX trigger)
        │  rating_sync_service.run_full_rating_sync()
        │    — paginate Plex library, pull userRating for every track
        │    — UPDATE Track.user_rating WHERE plex_rating_key=...
        │  SetupState.step = "proposing"
        ▼
Returns partial with "Analyzing your taste..." spinner + HTMX poll on /setup/proposal-status
        │
        ▼
Background task: vibe_clusterer.propose(rated_tracks, target_count=5)
        │  Anthropic call (Instructor with Pydantic schema for List[ClusterProposal])
        │  Output: 3–7 named clusters with centroids + member track IDs
        │  SetupState.draft_clusters_json = json.dumps(proposals)
        │  SetupState.step = "confirming"
        ▼
GET /setup/confirm
        │  Renders editable list of proposed vibes (name, description, sample tracks)
        │  User edits names, optionally drops a cluster
        ▼
POST /setup/finalize
        │  For each confirmed cluster:
        │    — INSERT Vibe(name, description, centroid_*)
        │    — INSERT TrackVibe(track_id, vibe_id, distance, assigned_by='cluster')
        │    — plex_client.create_playlist(name, track_ids)
        │    — INSERT ManagedPlaylist(kind='vibe', vibe_id, plex_rating_key)
        │  taste_profile_service.recompute()
        │  suggestions_service.bootstrap()  → creates initial Suggestions playlist
        │  SetupState.step = "done"
        ▼
Redirect to /  (vibes home — new v2 landing page)
```

### State Management

```
SQLite (durable) ← v1 idiom unchanged
   ▲
   │ writes from background tasks (event dispatcher, sync, analysis, vibe_service, ...)
   │
   ├── module-level dataclasses (in-memory status: _sync_status, _analysis_status,
   │       _rating_sync_status, _suggestions_status) — read by HTMX poll endpoints
   │
   ▼
Jinja2 partials → HTMX swaps → DOM updates
   │
   ▲ user actions (buttons, drag-drop, rate, etc.) → POST → service call → DB write
```

No new state-management framework. v1's "SQLite truth + in-memory status dataclasses + HTMX polling" extends cleanly.

## Build Order (Dependency Chain)

Recommended phase decomposition for v2.0 — each phase is independently shippable. Order is dictated by data dependencies, not feature priority.

```
Phase 5: Rating-source foundation
  ├── DB migration: Track.user_rating, last_viewed_at, view_count, rating_changed_at
  ├── plex_client extensions: get_track_metadata, get_rated_tracks, viewCount/lastViewedAt mapping
  ├── rating_sync_service (full + on-demand)
  ├── EventLog table + event_bus skeleton (dispatcher with no handlers yet)
  ├── api_webhooks router (handles media.rate, media.scrobble — writes EventLog only)
  ├── poll_service (also writes EventLog only — proves dedupe works end-to-end)
  └── settings UI: webhook URL display + polling-interval setting + Plex Pass detection
       Why this order: nothing else can be built without ratings flowing in.
       Ship gate: rated-track count visible on home page; manual refresh works;
                  webhook + poll both populate EventLog with no duplicates.

Phase 6: Vibe clustering + setup wizard
  ├── Vibe, TrackVibe, ManagedPlaylist, SetupState, TasteProfile tables
  ├── vibe_clusterer (one-shot Instructor + Anthropic call)
  ├── taste_profile_service (compute + cache)
  ├── vibe_service (CRUD + slot_track for new ratings)
  ├── plex_client extensions: create_playlist, update_playlist_items, delete_playlist
  ├── api_setup router + /setup/* templates (HTMX wizard)
  ├── api_vibes router (list/edit/rename/delete vibes)
  └── Wire RatingChanged handler in event_bus dispatcher: slot newly-rated track
       Ship gate: user completes wizard, has 3–7 named vibes pushed to Plex;
                  rating a new track in Plexamp lands it in matching vibe within 10s.

Phase 7: Suggestions queue
  ├── suggestions_service (state machine + queue logic)
  ├── llm_client.rank() method (Anthropic ranking call w/ prompt cache hint)
  ├── Wire TrackPlayed handler: refill Suggestions on consumption
  ├── Bootstrap flow: create Suggestions playlist on demand
  └── Mobile-first vibes home page (also retires v1 mood-chat UI)
       Ship gate: Suggestions playlist exists in Plexamp, drains as user listens,
                  refills with taste-matched tracks within 30s of a play.

Phase 8: Lidarr discovery + polish
  ├── discovery_service (LLM artist suggestions vs library set)
  ├── lidarr_client extensions: add_artist, get_recent_imports
  ├── FIX: Lidarr connection-test bug from .planning/notes/connection-test-bugs.md
  ├── api_discovery router + UI
  ├── LidarrImport handler in event_bus (auto-analyze on import → already mostly works
  │   via existing trigger_post_sync_analysis)
  └── Responsive pass on legacy library/settings pages
       Ship gate: discover page shows ≥10 artist suggestions; one-click adds to Lidarr;
                  imported tracks auto-analyze and become eligible for Suggestions.
```

**Why this order:**
- Phase 5 isolates the riskiest new piece (webhook reception, dedupe correctness) and ships value-of-zero — but you can't safely build Phase 6 without it because vibe clustering needs the rating set.
- Phase 6 establishes the clustering loop and the playlist-write integration with Plex. Suggestions in Phase 7 reuses the playlist-write code.
- Phase 7 brings the headline feature ("continuous queue") online. By the time you get here, all the plumbing (events, vibes, Plex playlist CRUD) is battle-tested.
- Phase 8 is leafy — depends on rating set + library state but on nothing in 5–7. Could swap with 7 if priorities change. Lidarr connection-test fix lives here as a small carry-over.

## Anti-Patterns (What NOT to Add)

### Anti-Pattern 1: Spinning up Celery / RQ / Redis for "background processing"

**What people do:** "We've got webhooks and event handlers, this needs a real queue."
**Why it's wrong:** v1 already runs background work via `asyncio.to_thread` + `asyncio.create_task` without a broker. The Synology DS423+ has 32 GB RAM and a Celeron — it does not need separate worker processes. SQLite in WAL mode handles the concurrent reads. The whole event volume is bounded by one user's listening behavior.
**Do this instead:** Single `asyncio.Queue` + single dispatcher task started in FastAPI `lifespan`. SQLite for durable state. That's it.

### Anti-Pattern 2: Separate microservice for the "vibe engine"

**What people do:** Carve the LLM clustering work into its own container so it can scale independently.
**Why it's wrong:** Clustering happens once per setup + once per user-requested re-cluster. Maybe 3 times per year. Latency is fine in-process. Adding a second container breaks the "single Docker container" constraint from `PROJECT.md`.
**Do this instead:** Plain function in `vibe_clusterer.py`, called as `await asyncio.to_thread(...)` if needed (Anthropic SDK is sync; wrap it).

### Anti-Pattern 3: Cookie/session-backed setup wizard

**What people do:** Stash wizard state in a signed cookie or in-memory session.
**Why it's wrong:** Step 2 produces ~50 KB of cluster proposals. Cookies can't hold that. Sessions vanish on container restart, mid-wizard.
**Do this instead:** `SetupState` table, single row, persists across restarts. URL-driven step navigation.

### Anti-Pattern 4: Re-cluster on every rating change

**What people do:** "User rated something; let's re-derive vibes for accuracy."
**Why it's wrong:** Anthropic costs money. Vibes are user-named — re-clustering changes membership but you can't auto-rename. Confusing UX.
**Do this instead:** Slot the new track into existing vibes via cheap audio-feature distance. Re-cluster only on user request (per `PROJECT.md`'s explicit out-of-scope on scheduled re-clustering).

### Anti-Pattern 5: Treating the Plex playlist as the canonical Suggestions queue source

**What people do:** "Plex is the truth. Re-fetch the playlist on every event."
**Why it's wrong:** Adds 200–500 ms latency per event handler. Race window if user plays two tracks quickly. PlexAPI fetches are not free.
**Do this instead:** SQLite mirror is the read path; Plex playlist update is the eventual-consistent write path. Reconcile mirror from Plex on every refill cycle as a sanity check (cheap — just a list of rating keys).

### Anti-Pattern 6: Using `BackgroundTasks` per webhook request

**What people do:** `def webhook(payload, bg: BackgroundTasks): bg.add_task(handle, payload)`.
**Why it's wrong:** Plexamp scrubbing emits many `media.play`/`media.pause`/`media.resume` events. Each spawns a task. Hundreds of concurrent tasks racing on SQLite writes is a recipe for `database is locked` even in WAL mode.
**Do this instead:** Single dispatcher consuming a queue. Serialized DB writes. Predictable.

### Anti-Pattern 7: Storing taste profile as a vector of every rated track

**What people do:** "Centroid is lossy; let's keep all 460 feature vectors and recompute on every LLM call."
**Why it's wrong:** That's already what the `Track` table holds. A taste *profile* is a summary by definition. Sending 460 vectors into an LLM prompt is wasteful and breaks Anthropic prompt caching (cache key changes when input changes).
**Do this instead:** Cache a short text summary + structured aggregates. Recompute on threshold or user request.

### Anti-Pattern 8: Adding Alembic migrations now

**What people do:** "We're adding tables and columns; we should adopt Alembic."
**Why it's wrong:** v1 uses a lightweight `_migrate_add_columns()` ALTER-on-startup pattern that already works. Alembic adds boilerplate and a new tool to learn. v2 schema changes are still small enough to extend the same shim.
**Do this instead:** Extend `_migrate_add_columns()` for new Track columns. Use `SQLModel.metadata.create_all()` for new tables (it's idempotent and creates only missing tables).

## Integration Points

### External Services

| Service | Integration Pattern | Key Endpoints / Mechanisms | v2-specific Gotchas |
|---------|---------------------|---------------------------|---------------------|
| **Plex Server (webhook)** | HTTP POST receiver (multipart form, JSON in `payload` field) | `media.rate`, `media.play`, `media.pause`, `media.resume`, `media.stop`, `media.scrobble` | Requires Plex Pass on the server. Per-user webhook = fires per user; server-owner webhook = fires for all users. Use `account.id` in payload to filter to the configured user. Webhook payload's `Metadata.userRating` may be stale — re-fetch via API on rate events. |
| **Plex Server (REST)** | PlexAPI as today | Existing `searchTracks`, plus `playlists`, `createPlaylist`, `playlist.addItems`, `playlist.removeItems`. Read `userRating`, `viewCount`, `lastViewedAt` from track metadata. | `userRating` writes are NOT in scope (Composer reads ratings only). When polling, prefer `updatedAt>>` filter (already used in v1) — limits the diff scan. |
| **Lidarr** | pyarr (existing) | `lidarr.add_artist(...)`, `lidarr.get_history()` for recent imports, `lidarr.lookup_artist(...)` for discovery validation | Connection-test bug from v1: likely Docker network resolution. Verify URL matches `synobridge` network hostname (e.g. `http://lidarr:8686`), not `localhost`. |
| **Anthropic Claude** | Existing `llm_client` + Instructor | `messages.create` with prompt caching for the taste profile system message | Three call sites total: vibe_clusterer (rare), suggestions ranking (per-consumption refill), discovery (periodic). Stay under $5/year by caching the taste profile prefix (Anthropic prompt caching: 1-hour TTL, 90% cost reduction on cache hits). |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `api_webhooks` ↔ `event_bus` | `await event_bus.put(InboundEvent)` | Webhook returns 200 the moment the event hits the queue. Total receive→ack ~30 ms. |
| `event_bus` dispatcher ↔ handlers | direct async function call (`await handle_rating_changed(event)`) | One dispatcher, sequential handler execution. If a handler is slow (LLM call), it blocks the queue — acceptable at expected event rates. If this becomes a bottleneck, add a parallel handler pool keyed by event type. |
| handlers ↔ services | direct async function call | No new abstraction. `handle_track_played` calls `suggestions_service.handle_consumed(...)` directly. |
| services ↔ `plex_client`/`lidarr_client` | existing pattern (async wrapper around sync SDK call) | No change. Extend clients with new methods as listed. |
| services ↔ DB | `Session(get_engine())` (existing pattern) | Continue to use `asyncio.to_thread` for any sync-only DB operations on the hot path. |
| HTMX ↔ services (via routers) | existing pattern: router queries service status, returns Jinja partial | New routers: `api_vibes`, `api_suggestions`, `api_setup`, `api_discovery`. Each follows the exact shape of `api_sync` / `api_analysis`. |

## Scaling Considerations

This is still a single-user app. The relevant axes are **rated-set size**, **library size**, and **event rate**.

| Concern | At 460 rated / 10 k library (current) | At 2 k rated / 50 k library | At 10 k rated / 200 k library |
|---------|---------------------------------------|------------------------------|--------------------------------|
| Vibe slotting on rate event | <50 ms (3–7 distance comps) | <50 ms | <50 ms |
| Suggestions refill | LLM ~3 s + shortlist ~50 ms = ~3 s | ~3 s (shortlist scales linearly but stays <500 ms) | ~3 s (consider pre-computed feature index) |
| Re-cluster (rare, user-triggered) | One LLM call, ~30 s | ~60 s (more tokens) | Need to chunk into multiple LLM calls; pass partial summaries |
| Polling cost | 1 query / 5 min, returns small diff | Same (filter is on `updatedAt`) | Same |
| Webhook receive latency | <50 ms | <50 ms | <50 ms |
| EventLog table size | ~100 rows/day → ~36 k/yr | ~1 k/day → ~360 k/yr | Need TTL purge (drop processed rows >30 days) |
| Taste profile recompute | <1 s aggregation | ~3 s | ~10 s — schedule overnight |

### Scaling priorities

1. **First bottleneck (predicted):** EventLog table growth at multi-year scale. Mitigation: add a periodic APScheduler job that purges `EventLog` rows where `processed_at < now - 30 days`. Cheap. Defer until needed.
2. **Second bottleneck (predicted):** LLM ranking call latency dominates Suggestions refill. Mitigation: pre-compute next 5 candidates eagerly so the next refill is instant. Defer until measured.
3. **Non-bottleneck:** SQLite performance. WAL mode + indexed columns handle 200 k rows trivially.

## Sources

- [Plex Webhooks documentation](https://support.plex.tv/articles/115002267687-webhooks/) — payload format, event types, multipart structure
- [plexwebhooks Go package docs](https://pkg.go.dev/github.com/hekmon/plexwebhooks) — fully documented payload schema (account, Server, Metadata, ratingKey)
- [Plex Pro Week '25: Webhooks 101](https://www.plex.tv/blog/plex-pro-week-25-webhooks-101/) — webhook delivery semantics, retry behavior
- [Python PlexAPI documentation](https://python-plexapi.readthedocs.io/en/latest/modules/audio.html) — `lastViewedAt`, `viewCount`, `userRating` fields on Track
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) — `BackgroundTasks` semantics; why a queue+dispatcher beats per-request tasks for sustained event flow
- [Practical Guide to Webhook Receivers in FastAPI](https://blog.greeden.me/en/2026/04/07/a-practical-guide-to-safely-implementing-webhook-receiver-apis-in-fastapi-from-signature-verification-and-retry-handling-to-idempotency-and-asynchronous-processing/) — idempotency + ack-fast pattern
- [Webhook Idempotency Guide (fast.io)](https://fast.io/resources/fastio-webhook-idempotency-guide/) — dedupe strategy with unique event IDs
- v1 Composer source: `app/services/sync_service.py`, `app/services/analysis_service.py`, `app/services/sync_scheduler.py`, `app/services/plex_client.py`, `app/database.py` — singleton-status pattern, SQLite migration shim, APScheduler integration
- `.planning/research/v1.0/ARCHITECTURE.md` — v1 system layout and rationale
- `.planning/PROJECT.md` — v2.0 scope, key decisions, constraints
- `.planning/notes/connection-test-bugs.md` — Lidarr connection bug to fix in Phase 8

---
*Architecture research for: Composer v2.0 Music Companion — extending v1 patterns*
*Researched: 2026-05-08*
