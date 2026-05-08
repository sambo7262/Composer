# Phase 5: Plex Event Foundation + Rating Sync — Research

**Researched:** 2026-05-08
**Domain:** Event-driven Plex webhook + polling ingestion, dedupe via SQLite UNIQUE, asyncio dispatcher, Anthropic SDK migration with prompt caching, Track schema extension
**Confidence:** HIGH (Plex payload schema, Anthropic SDK syntax, PlexAPI attribute behavior, FastAPI lifespan pattern all verified against official sources or live code; LOW only where noted)

## Summary

Phase 5 is "no-product" foundation work — visible user value is a rated-track count on home, a `/debug/events` page, and a webhook setup wizard. Beneath that are three durable primitives every later phase depends on: (1) `EventLog` with `UNIQUE(dedupe_key)` for at-least-once webhook + polling ingestion, (2) a single `asyncio.Queue` + dispatcher task started in FastAPI `lifespan` that serializes downstream handlers, (3) a NEW `app/services/anthropic_client.py` using the official `anthropic>=0.100` SDK with explicit `cache_control={"type": "ephemeral", "ttl": "1h"}` on the system message (the silent March-2026 5-min default would otherwise blow the budget). The legacy `llm_client.py` and `chat_service.py` stay untouched. The Track model gains four columns via the existing `_migrate_add_columns()` shim — no Alembic.

The risk concentration is tight and frontloaded. Three things must land in the very first commit of the phase or rework cost compounds: (a) the dedupe key construction (sha256 over `event_type|ratingKey|user_rating|5s_bucket` with `INSERT OR IGNORE`), (b) `userRating` stored raw 0-10 with a single display helper, (c) every PlexAPI call wrapped in `asyncio.to_thread(...)`. All three are documented as locked decisions in CONTEXT.md and verified against PITFALLS 1, 2, 4. Beyond those, every implementation detail is either a direct extension of an existing v1 pattern (singleton service module, APScheduler job, `_migrate_add_columns` shim) or a one-shot integration of a well-documented library.

**Primary recommendation:** Build in this order — (1) Track schema migration + new tables → (2) `event_bus` module + `EventLog` dedupe + lifespan dispatcher wiring → (3) `api_webhooks` router (returns 200 in <50ms) → (4) `poll_service` + APScheduler job → (5) `rating_sync_service` + auto-backfill → (6) `anthropic_client.py` + `taste_profile_service` → (7) wizard URL detection + `/debug/events` page. Each step is independently testable. The dedupe + UNIQUE constraint is the single most important invariant — every later phase trusts it.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Anthropic SDK migration scope:**
- **D-01:** Build a NEW `app/services/anthropic_client.py` using the official `anthropic>=0.100,<1.0` SDK. v1 `app/services/llm_client.py` (direct-httpx) and `app/services/chat_service.py` (mood-chat consumer) remain UNTOUCHED. They keep working as-is until Phase 7 deletes both atomically when the chat UI retires.
- **D-02:** The legacy `app/services/ollama_client.py` (~82 lines) has no remaining consumers (Anthropic took over mid-v1). Confirm and DELETE in Phase 5 — small win, removes dead code from the surface area.
- **D-03:** New client must support: `cache_control: {"type": "ephemeral", "ttl": "1h"}` on the system message (explicit TTL — don't rely on the silently-changed 5-min default), structured output via `model_validate_json()` on a Pydantic class (no Instructor — fights prompt caching), per-call usage logging (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `cost_estimate_usd`).
- **D-04:** Cost circuit breaker scaffolding ships in Phase 5 alongside the client, even though the actual ranking calls don't fire until Phase 7. Counters (`daily_calls`, `burst_window`, `last_call_at`) live in a `LLMUsage` table. This way Phase 7's first ranking call already has the breaker in place — no retrofitting under fire.

**Event ingestion (webhook + polling):**
- **D-05:** Webhook endpoint at `POST /api/webhooks/plex` returns 200 within 50ms. Parses Plex's multipart/form-data with the `payload` JSON field via `Annotated[str, Form()]` + `json.loads()` — explicitly NOT `pydantic.Json[Model]` (FastAPI bug #10997).
- **D-06:** All inbound events become typed Pydantic events (`RatingChanged`, `TrackPlayed`, `LibraryAdded`, `WebhookTest`) and get pushed onto a single `asyncio.Queue` event bus. A single dispatcher task consumes the queue and serializes downstream handlers — prevents SQLite write contention.
- **D-07:** `EventLog` table with `UNIQUE(dedupe_key)` constraint where `dedupe_key = sha256(event_type|ratingKey|user_rating|5s_timestamp_bucket)`. `INSERT OR IGNORE` pattern — duplicates drop silently. Webhook + polling overlap resolves naturally through this UNIQUE.
- **D-08:** Polling job runs via the existing `app/services/sync_scheduler.py` APScheduler. Default 5-min interval. Polls bounded queries: `userRating` diffs, `lastViewedAt` diffs, `addedAt>>` for new tracks. NEVER full library scan. Same event bus / dedupe path as webhooks.
- **D-09:** PlexAPI is sync; ALL calls from event handlers wrap in `asyncio.to_thread(...)`. Pattern already in `app/services/plex_client.py` (line 45) — extend it. Document the convention in CLAUDE.md before any handler ships.

**Backfill on first deploy:**
- **D-10:** On first Phase 5 startup (detected by `Track.user_rating IS NULL` for all rows after migration), schedule a one-time auto-backfill background job. Pages through Plex via existing `get_library_tracks()` pagination, fills in `user_rating`, `last_viewed_at`, `view_count` for every existing track. Status visible as a banner like sync/analysis (reuse `sync_service.py` singleton pattern).
- **D-11:** Manual "Resync now" button (EVT-05) stays in settings. Auto handles initial deploy; manual handles future drift.

**Webhook URL display in wizard:**
- **D-12:** Wizard auto-detects three URL candidates and presents them via radio buttons: Docker hostname, NAS LAN IP (from request Host header), Tailscale 100.x address.
- **D-13:** Each candidate has a "Test webhook" action button. Wizard shows live ✓ via HTMX poll on `/api/webhooks/plex/last-test`.
- **D-14:** Selected URL persisted to `ServiceConfig` so settings page can display "currently configured" and user can swap later.

**Rating sync + taste profile scope:**
- **D-15:** `Track` gets four new columns via `_migrate_add_columns()`: `user_rating` (REAL, raw 0-10), `last_viewed_at` (TEXT ISO), `view_count` (INTEGER default 0), `rating_changed_at` (TEXT ISO).
- **D-16:** `RatedTrackView` is a SQL query, not a materialized view: `SELECT * FROM track WHERE user_rating > 0`. Index on `user_rating`.
- **D-17:** Phase 5 builds the FULL taste profile (RATE-05 in entirety): 4-D centroid (energy, tempo, danceability, valence), top 10 artists by rated count, top 10 genres, count + distribution stats, last_computed_at, AND ~200-word LLM-generated summary text via the new `anthropic_client` with prompt caching enabled. Storage: new `TasteProfile` table (single row, upserted).
- **D-18:** Recompute trigger: `RatingChanged` events accumulate to ≥10% delta of prior rated set size since `last_computed_at`. Counter on the dispatcher, not a scheduled job.

**Schema migrations + dependencies:**
- **D-19:** Extend existing `_migrate_add_columns()` shim (database.py:48) for the four new Track columns. New tables (`EventLog`, `LLMUsage`, `TasteProfile`) via `SQLModel.metadata.create_all()`. NO Alembic.
- **D-20:** `requirements.txt` updates ALL happen in Phase 5: add `anthropic>=0.100,<1.0`, add `scikit-learn>=1.8,<2.0` (used Phase 6 but added now to consolidate dep churn), bump `pyarr>=5.2,<6.0` → `pyarr>=6.6,<7.0`. Single dep-bump commit. `python-multipart>=0.0.24` already present.

**Debug surface (DEBUG-01):**
- **D-21:** `/debug/events` page renders plain HTML (no JS-only content). Shows last 50 EventLog entries, current poll interval + last poll info, last test webhook, current event bus queue depth, configured webhook URL.
- **D-22:** Reachable from settings page footer ("View diagnostics →") and as direct URL. No auth (Tailscale-only access at the network layer).

### Claude's Discretion

- Internal naming for Pydantic event classes (`RatingChangedEvent` vs `PlexRatingChange` etc.) — pick a consistent convention, document in CLAUDE.md if it differs from existing patterns
- Exact polling SQL queries / filter syntax against PlexAPI — researcher will surface options
- Specific layout/styling of `/debug/events` (table, color coding for errors, pagination if needed)
- Whether to add a `/api/webhooks/plex/last-test` endpoint or surface the last test as part of the page itself with HTMX polling
- The 50ms response-time target — exact mechanism (push-to-queue and return; vs background task; vs response-then-process)
- Backfill banner UX — copy the sync banner pattern; minor copy/style decisions are Claude's
- Cost circuit breaker UI surface in Phase 5 (deferred to Phase 7 when ranking starts) — Phase 5 just stores counters, no UI

### Deferred Ideas (OUT OF SCOPE)

- Adaptive polling backoff (vary interval based on recent webhook activity) — defer to Phase 8 polish or v2.1
- Plex webhook event ordering guarantees per-ratingKey — single dispatcher = global FIFO is sufficient until Phase 6 proves otherwise
- Webhook payload export-as-JSON button on `/debug/events`
- Manual replay button on `/debug/events` (re-run a stored event)
- Settings UI to change polling interval — config is configurable but exposed only via DB / config file in Phase 5
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EVT-01 | `POST /api/webhooks/plex` accepts multipart, returns 200 in <50ms, pushes typed event to bus | Code Example "Webhook receiver"; Pattern 2 (queue + dispatcher); Pitfall 4 (sync→async wrap) |
| EVT-02 | Webhook handler dedupes via `EventLog UNIQUE(dedupe_key)`; duplicates drop silently | Code Example "Dedupe key"; Pitfall 1 (no Plex idempotency token) |
| EVT-03 | APScheduler polling job, 5-min default, emits same typed events through same bus | Code Example "Polling job"; Pattern "Bounded queries" (Pitfall 21) |
| EVT-04 | Single asyncio dispatcher consumes bus and serializes downstream handlers | Code Example "Lifespan dispatcher"; Anti-Pattern 6 (per-request BackgroundTasks) |
| EVT-05 | Manual "Resync now" full event scan from settings UI | Reuse existing api_settings + sync_service idempotency guard pattern |
| EVT-06 | All PlexAPI calls inside event handlers run via `asyncio.to_thread()` | Pitfall 4; existing convention at plex_client.py:45 |
| EVT-07 | Wizard auto-detects accessible webhook URL with copy + test flow | Code Example "URL candidate detection"; Pattern "Three candidates" |
| RATE-01 | `Track.user_rating` stored raw 0-10; conversion at display only | Pitfall 2; Code Example "stars_from_user_rating helper" |
| RATE-02 | Initial sync (and full Resync) populates `user_rating` for every track | Auto-backfill service; reuse `get_library_tracks()` pagination |
| RATE-03 | `media.rate` webhook + polling rating-diffs update `user_rating` and emit `RatingChanged` | Code Example "Webhook flow"; PlexAPI userRating attr |
| RATE-04 | "Rated set" exposed as derived view `Track.user_rating > 0` with index | SQL CREATE INDEX in `_migrate_add_columns`; helper at services/rated_set.py |
| RATE-05 | Taste profile (centroid + top artists/genres + LLM summary) computed and cached; recomputes on ≥10% delta | Code Example "TasteProfile build"; Code Example "Anthropic SDK call" |
| OPS-01 | Schema extends existing `_migrate_add_columns()` shim; new tables via `create_all()` — no Alembic | Code Example "Migration shim"; Anti-Pattern 8 |
| OPS-02 | Anthropic SDK migration: `anthropic>=0.100,<1.0` replaces direct httpx wrapper (NEW client only — v1 untouched) | Code Example "Anthropic SDK"; D-01 |
| OPS-03 | `pyarr` pin bumped to `>=6.6,<7.0` | requirements.txt diff in Stack table |
| OPS-04 | `scikit-learn>=1.8,<2.0` added (Phase 6 will use; Phase 5 only consolidates dep churn) | requirements.txt diff |
| DEBUG-01 | `/debug/events` page lists last 50 events + poll info + last test + queue depth + configured URL | Code Example "Debug page Jinja2"; Pattern "Plain HTML, copy-friendly" |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Webhook reception (HTTP receive + ack) | API / Backend (FastAPI router) | — | HTTP endpoint; must ack <50ms regardless of downstream work [VERIFIED: existing app/routers/ pattern] |
| Event deduplication (UNIQUE key check) | Database / Storage (SQLite) | API / Backend | SQL UNIQUE constraint is the single source of dedupe truth [VERIFIED: SQLModel patterns at app/models/track.py] |
| Event dispatch (queue → handlers) | API / Backend (asyncio task in lifespan) | — | In-process asyncio.Queue inside the FastAPI process [VERIFIED: existing AsyncIOScheduler pattern at sync_scheduler.py:24] |
| Polling job (APScheduler) | API / Backend (scheduler in lifespan) | — | Same scheduler instance as v1 sync; one process, one event loop [VERIFIED: sync_scheduler.py:24 singleton] |
| PlexAPI calls (HTTP to Plex server) | API / Backend (via asyncio.to_thread) | External Service (Plex Server) | Sync library; threadpool keeps event loop free [VERIFIED: plex_client.py:45 wrapper pattern] |
| Schema migration | Database / Storage (lifespan startup) | — | `_migrate_add_columns()` runs in `init_db()` before app accepts requests [VERIFIED: app/main.py lifespan; database.py:48] |
| Track rating storage | Database / Storage (SQLite) | — | Persistent column on existing Track table [VERIFIED: existing models/track.py] |
| Anthropic LLM call (taste profile summary) | API / Backend (anthropic SDK) | External Service (Anthropic API) | New service module mirrors v1 llm_client pattern [VERIFIED: existing app/services/llm_client.py] |
| Backfill orchestration | API / Backend (singleton service) | — | Mirrors `analysis_service` pattern (state machine, status dataclass) [VERIFIED: app/services/analysis_service.py] |
| Webhook URL auto-detection | API / Backend (settings router + helpers) | Browser (selects via radio button) | Server detects candidates; user picks via HTMX form [VERIFIED: existing service-card test-and-configure pattern, Phase 1] |
| `/debug/events` rendering | API / Backend (Jinja2 template) | — | Server-side render, no JS dependency (per D-21) [VERIFIED: existing pages.py + templates/pages/ pattern] |
| Wizard "test webhook" indicator | API / Backend (HTMX poll endpoint) | Browser (polls every 2s) | Mirrors sync-banner HTMX polling [VERIFIED: app/templates/partials/sync_banner.html] |

**Key insight:** Every capability lives in the API / Backend tier in the same process. There are no client-side state stores, no separate worker containers, no external queues. The only crossings are HTTP-to-Plex (sync library wrapped in `to_thread`) and HTTP-to-Anthropic (async SDK). This matches the v2.0 milestone architecture rule: "single container, single process, single SQLite file, in-process asyncio."

## Standard Stack

### Core (additions for Phase 5)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `anthropic` | `>=0.100,<1.0` | Anthropic Claude SDK with native prompt caching | [VERIFIED: pypi.org/project/anthropic — v0.100.0 released 2026-05-06] Native `cache_control` since 0.40; typed `usage.cache_creation_input_tokens` / `cache_read_input_tokens`; `ttl: "1h"` GA without beta header. Replaces v1's hand-rolled httpx wrapper for the NEW client. |
| `scikit-learn` | `>=1.8,<2.0` | k-means + silhouette (Phase 6 actual use; added Phase 5 to consolidate dep churn) | [VERIFIED: pypi.org/project/scikit-learn — v1.8.0 released 2025-12-10, requires Python ≥3.11] Pulls numpy/scipy/joblib/threadpoolctl transitively (~50MB image delta). Phase 5 imports it nowhere; Phase 6 builds vibe clusterer on top. |
| `pyarr` | `>=6.6,<7.0` (bumped from `>=5.2,<6.0`) | Lidarr API client | [CITED: pypi.org/project/pyarr — v6.6.0 March 2026, requires Python ≥3.12] Pin bump only — Phase 5 does not call Lidarr. Bump consolidated here so Phase 8 doesn't surprise with a dep change. |

### Already present (no version change)

| Library | Version (current) | Why It Matters in Phase 5 |
|---------|-------------------|----------------------------|
| `fastapi` | `>=0.135,<0.136` | Webhook router; `Annotated[str, Form()]` for multipart; lifespan context manager |
| `python-multipart` | `>=0.0.24,<1.0` | Required to parse Plex's multipart/form-data webhook body. Already in v1 requirements.txt — no install needed. |
| `plexapi` | `>=4.18,<5.0` | `Track.userRating`, `lastViewedAt`, `viewCount`; `library.search(filters={...}, sort='lastViewedAt:desc')`; sync library — wrap every call in `asyncio.to_thread` |
| `apscheduler` | `>=3.11,<4.0` | Existing `AsyncIOScheduler` singleton at sync_scheduler.py:24; add `polling_job` to it |
| `sqlmodel` | `>=0.0.38,<0.1` | New tables (`EventLog`, `LLMUsage`, `TasteProfile`) via `SQLModel.metadata.create_all()` |
| `httpx` | `>=0.28,<1.0` | v1 LLM client uses this directly — leave alone (D-01); new anthropic_client uses anthropic SDK instead |

### Native to Python 3.12

| Module | Used For |
|--------|----------|
| `asyncio.Queue` | Event bus — created once in `lifespan` |
| `asyncio.create_task` / `asyncio.CancelledError` | Long-running dispatcher task; cancelled cleanly in `lifespan` shutdown |
| `asyncio.to_thread` | Wrapping every PlexAPI call (D-09) |
| `hashlib.sha256` | Dedupe key construction |
| `json.loads` | Parsing Plex `payload` field from multipart form |
| `socket.gethostname` | Docker hostname auto-detection candidate (Pattern in URL detection) |
| `ipaddress.IPv4Network('100.64.0.0/10')` | Tailscale CGNAT range check [CITED: tailscale.com/docs/concepts/tailscale-ip-addresses] |
| `psutil.net_if_addrs()` | Enumerate interfaces for Tailscale IP detection — already pulled transitively |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `anthropic` SDK | Continue with `httpx` direct | Verbose JSON crafting for `cache_control`; manual usage parsing; can't read typed `cache_*_tokens`. Already locked by D-01. |
| `Annotated[str, Form()] + json.loads` | `pydantic.Json[Model]` in `Form()` | Open FastAPI bug #10997 — fails to deserialize on multipart; breaks OpenAPI. Locked by D-05. |
| Single asyncio.Queue + dispatcher | Per-request `BackgroundTasks` | Spawns one task per webhook; SQLite write contention under listening-session burst; unbounded. Anti-Pattern 6 in v2 ARCHITECTURE.md. |
| `_migrate_add_columns` shim extension | Adopt Alembic now | New tool to learn; v2 schema changes are still small. PITFALLS Pitfall 19 disagrees but ARCHITECTURE.md and milestone-locked OPS-01 take precedence. |
| `asyncio.Queue` | Celery / RQ / Redis | Single-user single-container; one user's listening behavior maxes ~100 events/day. ARCHITECTURE Anti-Pattern 1. |
| Instructor for structured output | Direct Pydantic `model_validate_json` | Instructor reformats system messages → fights prompt caching. Removed mid-v1 already; do not reintroduce. Locked by D-03. |

**Version verification (verified against PyPI 2026-05-08):**

```
anthropic       0.100.0  released 2026-05-06
scikit-learn    1.8.0    released 2025-12-10
pyarr           6.6.0    released March 2026 (per STACK.md cross-ref)
plexapi         4.18.1   released 2026-03-22
fastapi         0.135.x  current pin range
sqlmodel        0.0.38   current pin
apscheduler     3.11.x   current pin range
```

Final `requirements.txt` diff:

```diff
  fastapi>=0.135,<0.136
  uvicorn>=0.44,<1.0
  sqlmodel>=0.0.38,<0.1
  httpx>=0.28,<1.0
  python-multipart>=0.0.24,<1.0
  plexapi>=4.18,<5.0
- pyarr>=5.2,<6.0
+ pyarr>=6.6,<7.0
- # openai and instructor removed — using Anthropic API via httpx directly
+ # v1 chat uses httpx directly via app/services/llm_client.py (retiring Phase 7)
+ # v2 services use the official anthropic SDK with native prompt caching
+ anthropic>=0.100,<1.0
+ scikit-learn>=1.8,<2.0
  cryptography>=46.0,<47.0
  jinja2>=3.1,<4.0
  apscheduler>=3.11,<4.0
```

## Architecture Patterns

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         External Services                               │
│  ┌──────────────────┐                          ┌──────────────────┐    │
│  │ Plex Server      │──── webhook POST ───▶    │  Composer        │    │
│  │ (Plex Pass)      │                          │  /api/webhooks/  │    │
│  │                  │ ◀── PlexAPI HTTP poll ── │  plex            │    │
│  └──────────────────┘                          └────────┬─────────┘    │
│                                                          │              │
│  ┌──────────────────┐                                    │              │
│  │ Anthropic API    │ ◀── HTTPS via SDK ──── (taste profile summary)   │
│  └──────────────────┘                                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                                          │
┌─────────────────────────────────────────────────────────┼───────────────┐
│                  Composer (single FastAPI process)      ▼               │
│                                                                         │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐ │
│  │ api_webhooks    │  │ poll_service     │  │ rating_sync_service    │ │
│  │ POST /plex      │  │ (APScheduler 5m) │  │ (full backfill +       │ │
│  │ • parse mp/form │  │ • bounded query  │  │  on-demand "Resync")   │ │
│  │ • build event   │  │ • diff DB↔Plex   │  │ • mirrors sync_service │ │
│  │ • bus.put_nowait│  │ • bus.put_nowait │  │   singleton pattern    │ │
│  │ • return 200    │  │                  │  │                        │ │
│  └─────────┬───────┘  └────────┬─────────┘  └───────────┬────────────┘ │
│            └────────────┬──────┴────────────────────────┘              │
│                         ▼                                              │
│                ┌────────────────────────────┐                          │
│                │    asyncio.Queue           │ ── single instance,      │
│                │    (event_bus singleton)   │    created in lifespan   │
│                └────────────┬───────────────┘                          │
│                             ▼                                          │
│                ┌────────────────────────────┐                          │
│                │  dispatcher task           │ ── single create_task()  │
│                │  (single asyncio.create_   │    in lifespan; consumes │
│                │   task started in lifespan)│    queue serially        │
│                │                            │                          │
│                │  for event in queue:       │                          │
│                │    1. INSERT OR IGNORE     │                          │
│                │       INTO EventLog        │ ◀── dedupe via UNIQUE    │
│                │    2. match event:         │                          │
│                │         RatingChanged →    │                          │
│                │         TrackPlayed   →    │                          │
│                │         LibraryAdded  →    │                          │
│                │         WebhookTest   →    │                          │
│                └────┬────────┬────────┬─────┘                          │
│                     │        │        │                                │
│           ┌─────────┘        │        └─────────┐                      │
│           ▼                  ▼                  ▼                      │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐           │
│  │ handle_      │  │ handle_      │  │ handle_webhook_    │           │
│  │ rating_      │  │ track_       │  │ test               │           │
│  │ changed      │  │ played       │  │ (stash payload     │           │
│  │ • UPDATE     │  │ • UPDATE     │  │  for wizard ✓)     │           │
│  │   Track      │  │   Track      │  │                    │           │
│  │ • inc 10%    │  │   view_count │  │                    │           │
│  │   counter    │  │   last_      │  │                    │           │
│  │ • maybe      │  │   viewed_at  │  │                    │           │
│  │   recompute  │  │              │  │                    │           │
│  │   taste prof.│  │              │  │                    │           │
│  └──────┬───────┘  └──────────────┘  └────────────────────┘           │
│         │                                                              │
│         ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │ taste_profile_service    │ ── recompute on ≥10% delta              │
│  │ • aggregate centroid     │     (counter on dispatcher)             │
│  │ • top artists / genres   │                                          │
│  │ • call anthropic_client  │                                          │
│  │   (NEW SDK, ttl="1h")    │                                          │
│  │ • upsert TasteProfile    │                                          │
│  └────────────┬─────────────┘                                          │
│               ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ SQLite (single file, WAL — already configured in v1)            │  │
│  │   Track          (v1 + 4 new cols: user_rating, last_viewed_at, │  │
│  │                  view_count, rating_changed_at)                 │  │
│  │   EventLog       [NEW] dedupe + audit (UNIQUE dedupe_key)       │  │
│  │   LLMUsage       [NEW] cost circuit breaker counters            │  │
│  │   TasteProfile   [NEW] single row id=1 — centroid + summary text│  │
│  │   ServiceConfig  (v1) — webhook_url stored here under key       │  │
│  │                  service_name='webhook'                          │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
app/
├── main.py                              # extend lifespan: queue + dispatcher
├── database.py                          # extend _migrate_add_columns
├── models/
│   ├── track.py                         # extend Track with 4 new fields
│   ├── event_log.py                     # NEW: EventLog model
│   ├── llm_usage.py                     # NEW: LLMUsage model
│   ├── taste_profile.py                 # NEW: TasteProfile model
│   └── events.py                        # NEW: Pydantic event types (RatingChanged, etc.)
├── routers/
│   ├── api_webhooks.py                  # NEW: POST /api/webhooks/plex + /last-test
│   ├── api_rating_sync.py               # NEW: manual "Resync now" trigger
│   ├── api_settings.py                  # extend: webhook URL display + test
│   └── pages.py                         # extend: /debug/events page
├── services/
│   ├── event_bus.py                     # NEW: asyncio.Queue singleton + dispatcher
│   ├── event_handlers.py                # NEW: handle_rating_changed, handle_track_played
│   ├── rating_sync_service.py           # NEW: full + on-demand resync
│   ├── poll_service.py                  # NEW: APScheduler job for diffs
│   ├── backfill_service.py              # NEW: first-run auto-backfill state machine
│   ├── anthropic_client.py              # NEW: anthropic SDK + cache_control + usage logging
│   ├── taste_profile_service.py         # NEW: compute centroid + LLM summary, upsert
│   ├── webhook_url_detection.py         # NEW: docker/LAN/Tailscale candidates
│   ├── sync_scheduler.py                # extend: add poll_job alongside sync_job
│   ├── plex_client.py                   # extend _map_track with 3 new fields
│   ├── llm_client.py                    # DO NOT TOUCH (v1)
│   ├── chat_service.py                  # DO NOT TOUCH (v1)
│   └── ollama_client.py                 # DELETE (with its 3 tests in test_service_clients.py)
└── templates/
    ├── pages/
    │   ├── debug_events.html            # NEW: /debug/events
    │   └── settings.html                # extend: footer link + webhook URL section
    └── partials/
        ├── webhook_url_radio.html       # NEW: 3-candidate radio + test button
        ├── webhook_test_indicator.html  # NEW: HTMX-polled ✓/⏳ partial
        └── backfill_banner.html         # NEW: copy of sync_banner with backfill copy
```

### Pattern 1: Lifespan-managed asyncio.Queue + dispatcher

**What:** Create the queue once in `lifespan` startup; spawn a single long-running dispatcher task with `asyncio.create_task(...)`; on shutdown, cancel the task and await its `CancelledError`. Inject the queue into webhook routes via a module-level singleton (matches existing `sync_service._sync_status` pattern), NOT via `Depends()`.

**When to use:** Whenever a long-running consumer must outlive any single request and serialize work that downstream handlers do (DB writes, LLM calls). Plex webhook ingestion is the canonical case.

**Example:**
```python
# app/services/event_bus.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level singletons — match the existing sync_service / analysis_service idiom.
# DO NOT use FastAPI Depends() for the queue; the dispatcher needs to outlive any request.
_queue: Optional[asyncio.Queue] = None
_dispatcher_task: Optional[asyncio.Task] = None


def get_event_bus() -> asyncio.Queue:
    """Return the global event bus. Created lazily on first call from lifespan."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def start_dispatcher() -> None:
    """Start the single long-running dispatcher task. Called from lifespan startup."""
    global _dispatcher_task
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return  # already running
    _dispatcher_task = asyncio.create_task(_dispatch_loop(), name="event_dispatcher")


async def stop_dispatcher() -> None:
    """Cancel the dispatcher task. Called from lifespan shutdown."""
    global _dispatcher_task
    if _dispatcher_task is None:
        return
    _dispatcher_task.cancel()
    try:
        await _dispatcher_task
    except asyncio.CancelledError:
        pass
    _dispatcher_task = None


async def _dispatch_loop() -> None:
    """Main consumer. Serializes downstream handlers; one event at a time."""
    queue = get_event_bus()
    from app.services.event_handlers import dispatch_event
    while True:
        try:
            event = await queue.get()
            try:
                await dispatch_event(event)  # writes EventLog, calls handler
            except Exception:
                logger.exception("Handler error for event %r", event)
                # Never re-raise — keep the dispatcher alive
            finally:
                queue.task_done()
        except asyncio.CancelledError:
            logger.info("Dispatcher cancelled, draining queue")
            raise
```

```python
# app/main.py — extended lifespan
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

[CITED: github.com/anthropics/anthropic-sdk-python; FastAPI lifespan + asyncio.create_task pattern verified at https://fastapi.tiangolo.com/advanced/events/]

### Pattern 2: Webhook receiver — push-and-return, no work in handler

**What:** Webhook endpoint does three things: (1) parse multipart, (2) build typed event, (3) `queue.put_nowait(event)` then `return Response(status_code=200)`. Total request lifecycle <50ms. The dispatcher does the actual work asynchronously.

**When to use:** Always for webhooks. Anything that needs to ack quickly to a remote sender that retries on slow responses.

**Why `put_nowait` not `await put`:** `asyncio.Queue` is unbounded by default — `put_nowait` never blocks. Using `await put` would only block if you set `maxsize`, which we don't. `put_nowait` makes it explicit that the webhook handler returns immediately even under heavy load.

**Example:** see Code Examples below.

### Pattern 3: Polling fallback emits the same event types

**What:** APScheduler job runs every 5 min. Queries Plex for: (a) tracks with userRating diff vs local, (b) tracks with lastViewedAt advanced, (c) new tracks via `addedAt>>` filter. Each diff produces the same Pydantic event a webhook would produce. Pushes onto the same queue. The dedupe UNIQUE on `EventLog` handles webhook+poll overlap automatically.

**When to use:** Every event-driven system that needs reliability — webhooks alone are at-least-once but also have failure modes (server restart, network partition, Plex Pass not configured).

**Bounded query patterns** [CITED: python-plexapi.readthedocs.io filters reference]:

```python
# In poll_service.py (sync; called via to_thread)
from plexapi.server import PlexServer

def _poll_recently_rated(plex: PlexServer, library_id: int, since_iso: str):
    """Query tracks rated since the last poll. Bounded — never full library."""
    section = plex.library.sectionByID(library_id)
    # Filter syntax: "track.userRating>>": value, sorted by lastRatedAt desc
    tracks = section.searchTracks(
        filters={"track.userRating>>": 0},   # rated only
        sort="lastRatedAt:desc",
        limit=200,                            # cap result set
    )
    # Stop iteration when track.lastRatedAt < since_iso (already-seen)
    return [t for t in tracks if t.lastRatedAt and t.lastRatedAt.isoformat() > since_iso]


def _poll_recently_played(plex: PlexServer, library_id: int, since_iso: str):
    """Query tracks played since the last poll. Bounded."""
    section = plex.library.sectionByID(library_id)
    tracks = section.searchTracks(
        filters={"track.lastViewedAt>>": since_iso[:10]},  # date-only granularity
        sort="lastViewedAt:desc",
        limit=200,
    )
    return tracks


def _poll_recently_added(plex: PlexServer, library_id: int, since_iso: str):
    """Reuse existing get_tracks_since pattern (plex_client.py:56)."""
    section = plex.library.sectionByID(library_id)
    return section.searchTracks(filters={"addedAt>>": since_iso[:10]})
```

[ASSUMED] The `track.userRating>>` filter syntax follows the pattern documented for video filters (`library.search(filters={"track.userRating>>": 8})`) — I verified the pattern is documented for tracks in PlexAPI 4.18 [CITED: python-plexapi.readthedocs.io/en/latest/modules/library.html] but the exact string syntax (`track.userRating>>` vs `userRating>>`) needs confirmation against PlexAPI source. **Recommended:** during planning, write a small spike that runs each filter against the user's actual library and logs the count of matches; promote whichever syntax works to the codebase.

### Pattern 4: Pydantic event type = discriminated union

**What:** Define each event as a Pydantic class with a literal `type` discriminator. Dispatcher uses Python 3.12 `match` statement on `event.type`.

**Why:** Typed events let mypy catch missing handlers; the discriminator forces every new event type to be added to the dispatch switch.

**Example:**
```python
# app/models/events.py
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel


class BaseEvent(BaseModel):
    """Base for all event-bus events."""
    plex_rating_key: Optional[str] = None
    source: Literal["webhook", "poll", "manual"] = "webhook"
    received_at: str  # ISO 8601 UTC
    raw_payload: Optional[str] = None  # JSON; truncated; for EventLog audit


class RatingChangedEvent(BaseEvent):
    type: Literal["rating_changed"] = "rating_changed"
    new_rating: Optional[float]  # raw 0-10; None means rating cleared


class TrackPlayedEvent(BaseEvent):
    type: Literal["track_played"] = "track_played"
    last_viewed_at: str  # ISO 8601 UTC; from webhook payload, not re-fetched


class LibraryAddedEvent(BaseEvent):
    type: Literal["library_added"] = "library_added"
    library_section_id: Optional[int] = None


class WebhookTestEvent(BaseEvent):
    type: Literal["webhook_test"] = "webhook_test"
    # Plex's "test webhook" arrives as media.play with no rating context;
    # we discriminate it by the absence of useful Metadata fields. See "Pitfall: Plex test event detection."
```

### Pattern 5: Single-row table for cached state (TasteProfile, LLMUsage daily counter)

**What:** Use a SQLModel table with `id: int = Field(default=1, primary_key=True)` for state that is logically singleton. Upsert on every recompute.

**Why:** Avoids the complexity of a key-value blob; lets you query specific columns; SQLite handles a 1-row table fine. v2 ARCHITECTURE.md uses this exact pattern for `TasteProfile` and `SetupState`.

**Example:**
```python
# app/models/taste_profile.py
from typing import Optional
from sqlmodel import Field, SQLModel

class TasteProfile(SQLModel, table=True):
    """Single-row cache (id=1). Upserted by taste_profile_service."""
    id: Optional[int] = Field(default=1, primary_key=True)
    rated_track_count: int = 0
    centroid_energy: Optional[float] = None
    centroid_tempo: Optional[float] = None
    centroid_danceability: Optional[float] = None
    centroid_valence: Optional[float] = None
    top_artists_json: str = ""        # JSON list of (artist, count) pairs
    top_genres_json: str = ""         # JSON list of (genre, count) pairs
    summary_text: str = ""            # ~200-word LLM-generated summary
    computed_at: Optional[str] = None # ISO 8601 UTC
```

### Pattern 6: SQLite UNIQUE + INSERT OR IGNORE for dedupe

**What:** `EventLog.dedupe_key` has `UNIQUE` constraint. The dispatcher wraps each event in `INSERT OR IGNORE`; on no-op (zero rows affected), skip dispatch. The same pattern handles webhook retries, polling overlap, and same-second double-rate-correction.

**Why:** Atomic at the DB level — no race window between "check exists" and "insert." SQLite WAL mode handles this trivially.

**Example:**
```python
# Inside dispatcher, before calling the handler:
import hashlib, time, json
from sqlmodel import Session
from app.database import get_engine
from app.models.event_log import EventLog

def _compute_dedupe_key(event_type: str, rating_key: Optional[str], user_rating: Optional[float], received_at_epoch: float) -> str:
    """5-second bucket means same logical event within 5s dedupes. Different ratings always distinct."""
    bucket = int(received_at_epoch // 5)
    seed = f"{event_type}|{rating_key or '_'}|{user_rating if user_rating is not None else '_'}|{bucket}"
    return hashlib.sha256(seed.encode()).hexdigest()


async def dispatch_event(event) -> None:
    dedupe_key = _compute_dedupe_key(event.type, event.plex_rating_key, getattr(event, "new_rating", None), time.time())
    # INSERT OR IGNORE: returns rowcount==0 on duplicate, ==1 on insert.
    def _insert_sync():
        with Session(get_engine()) as session:
            from sqlalchemy import text
            result = session.execute(
                text("""
                    INSERT OR IGNORE INTO eventlog
                        (source, event_type, plex_rating_key, dedupe_key, received_at, raw_payload)
                    VALUES (:s, :t, :rk, :dk, :ra, :rp)
                """),
                {"s": event.source, "t": event.type, "rk": event.plex_rating_key,
                 "dk": dedupe_key, "ra": event.received_at, "rp": event.raw_payload},
            )
            session.commit()
            return result.rowcount  # 1 = inserted, 0 = duplicate
    rowcount = await asyncio.to_thread(_insert_sync)
    if rowcount == 0:
        return  # silent dedupe
    # Otherwise dispatch:
    match event.type:
        case "rating_changed": await handle_rating_changed(event)
        case "track_played":   await handle_track_played(event)
        case "library_added":  await handle_library_added(event)
        case "webhook_test":   await handle_webhook_test(event)
```

### Pattern 7: Three webhook URL candidates, user picks live

**What:** On wizard render, server computes three candidate webhook URLs simultaneously (Docker hostname, NAS LAN IP from request `Host` header, Tailscale 100.x). Each is shown as a radio option with a "Test" button that opens Plex docs URL or a `mailto`-style copy + a poll partial that watches for `WebhookTestEvent`.

**Why:** No single network topology works for every user (Docker network vs host network vs remote-Tailscale-only). Detection is cheap; let the user pick whichever turns ✓ first.

**Example detection helpers:**
```python
# app/services/webhook_url_detection.py
from __future__ import annotations
import ipaddress
import socket
from typing import Optional, Iterable
from fastapi import Request

TAILSCALE_RANGE = ipaddress.IPv4Network("100.64.0.0/10")  # CGNAT, per RFC 6598


def docker_hostname_candidate(port: int) -> Optional[str]:
    """E.g. http://composer:8085/api/webhooks/plex when Plex shares synobridge network."""
    try:
        # socket.gethostname() returns Docker container's name on synobridge-attached containers.
        host = socket.gethostname()
        if not host or host in ("localhost", "127.0.0.1"):
            return None
        return f"http://{host}:{port}/api/webhooks/plex"
    except OSError:
        return None


def lan_ip_candidate(request: Request, port: int) -> Optional[str]:
    """Use the Host header from the user's first browser request — that's the IP/hostname they reach Composer at."""
    host_header = request.headers.get("host", "")
    if not host_header:
        return None
    # Strip the port; we replace with the configured webhook port.
    host_only = host_header.split(":")[0]
    if not host_only:
        return None
    return f"http://{host_only}:{port}/api/webhooks/plex"


def tailscale_candidate(port: int) -> Optional[str]:
    """Find any 100.64.0.0/10 IPv4 bound on this container."""
    try:
        import psutil  # transitively present
    except ImportError:
        return None
    for iface_name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.IPv4Address(addr.address)
            except ipaddress.AddressValueError:
                continue
            if ip in TAILSCALE_RANGE:
                return f"http://{ip}:{port}/api/webhooks/plex"
    return None
```

[CITED: tailscale.com/docs/concepts/tailscale-ip-addresses for CGNAT range; psutil already pulled transitively]

**Note on `psutil`:** If `psutil` is NOT actually transitively pulled in the current install, planner should add it to requirements.txt explicitly. Quick verification: `pip show psutil` against the v1 install; if missing, add `psutil>=5.9,<7.0`.

### Anti-Patterns to Avoid

- **Per-request `BackgroundTasks` for webhooks** — spawns one task per request; under listening-session burst (Plexamp scrubbing fires many `media.play`/`pause`/`resume`) this races on SQLite writes. Use the queue + dispatcher [from ARCHITECTURE.md Anti-Pattern 6].
- **`Depends()` for the asyncio.Queue** — dependencies are scoped to a request. The queue must outlive any single request. Use a module-level singleton (matches sync_service.py pattern).
- **`pydantic.Json[Model]` inside `Form()`** — open FastAPI bug #10997. Always parse with `Annotated[str, Form()]` + manual `json.loads()`.
- **Re-fetching Plex track inside the webhook handler before returning 200** — ARCHITECTURE recommends defensively re-fetching in the *handler* (not the receiver) for `media.rate` because webhook payload's userRating can be stale; but the *receiver* must not block on a Plex call. Keep the order: ack → enqueue → dispatcher does the (optional) re-fetch.
- **Trusting webhook payload `lastViewedAt` for things other than "this play happened"** — Pitfall 7. Use the webhook timestamp itself, not re-fetched state.
- **`async def` webhook handler that calls PlexAPI inline** — blocks the event loop. Pitfall 4. Either declare `def` (FastAPI threadpool) or wrap every PlexAPI call.
- **Calling `track.reload()` defensively for every Plex read** — PlexAPI auto-reloads when an optional attribute (e.g., `userRating`) is accessed and was not present in the partial response. Either accept the auto-reload cost or use an explicit `library.search(filters={...})` that returns full track objects.
- **Initiating clustering or k-means in Phase 5** — that's Phase 6. Phase 5's centroid is a single `numpy.mean(features, axis=0)` over rated tracks, not multi-centroid clustering. scikit-learn is added now to consolidate dep churn but should NOT be imported anywhere in Phase 5 code.
- **Touching `app/services/llm_client.py` or `app/services/chat_service.py`** — locked OFF-LIMITS until Phase 7 retirement.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Multipart/form-data parsing | Custom multipart parser | `Annotated[str, Form()]` + `json.loads()` | python-multipart already pulled by FastAPI; parser handles all the boundary edge cases |
| Anthropic prompt cache JSON | Hand-rolled httpx + cache_control dict | `anthropic.AsyncAnthropic().messages.create(system=[{"type":"text", "text":..., "cache_control":{"type":"ephemeral","ttl":"1h"}}])` | SDK provides typed `usage.cache_creation_input_tokens` etc.; manual httpx loses this and breaks if Anthropic changes the schema |
| Asyncio task lifecycle in FastAPI | `asyncio.create_task` without retaining a reference | Store task on module-level; `task.cancel()` + `await task` (with CancelledError catch) in lifespan shutdown | Without retention, GC can collect the running task; without cancel, the task survives shutdown and may write to a closed DB |
| SQLite dedupe | Application-level "have I seen this?" check | `INSERT OR IGNORE` on UNIQUE column | Atomic at DB level; no TOCTOU race between two concurrent webhook handlers |
| Tailscale IP detection | Parse `tailscale status` shell output | `psutil.net_if_addrs()` + `100.64.0.0/10` range check | `tailscale` CLI is not in the Docker image; psutil is portable and already a transitive dep |
| Plex webhook payload schema | Define our own dataclass and trust user-supplied fields | Pydantic `WebhookPayload` model with optional fields; tolerate missing keys | Plex schema differs slightly across PMS versions; 4.x payloads may add fields; missing fields crash the handler unless tolerant |
| Pydantic event type dispatch | If/elif chain on event_type string | `match event.type:` with Literal-typed Pydantic discriminator | Type checker catches missing handlers; new event type = compile-time error |
| Schema migration | Alembic or hand-rolled ALTER scripts | Extend `_migrate_add_columns()` shim | Already proven in Phases 2–4; locked by OPS-01 |
| Singleton state | Class with `__new__` override or DI container | Module-level dataclass + `get_*_status()` accessor | Existing v1 idiom; consistency reduces cognitive load |

**Key insight:** Phase 5 introduces zero "novel" infrastructure. Every primitive is either a Python stdlib feature (asyncio.Queue, hashlib, ipaddress) or an existing v1 pattern (singleton service, APScheduler job, _migrate_add_columns shim, sync_banner HTMX poll). The complexity is integration, not invention.

## Runtime State Inventory

This phase introduces persistent state and a long-running asyncio task. The "after every file is updated" question matters here in a different way than rename phases — but it still has answers:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `Track.user_rating` columns will be NULL for existing 10k tracks immediately after migration runs. The auto-backfill job (D-10) is the action that fills them. | Code edit: `_migrate_add_columns` adds columns NULL by default. Data migration: `backfill_service` populates on first startup. Both required. |
| Live service config | Plex Web → Account → Webhooks list — webhook URL must be registered there by the user. Composer never modifies this remotely. | Manual user action via wizard copy-paste. Wizard provides the URL string and the test indicator. |
| OS-registered state | None — Composer runs entirely in its container. No launchd / systemd / Windows Task Scheduler / pm2. APScheduler is in-process. | None — verified by grep: no calls to subprocess for OS-level registration in the codebase. |
| Secrets/env vars | Anthropic API key (already exists in v1 ServiceConfig as `service_name='anthropic'`). NEW client uses the same key. No new secret introduced. | None — the existing `get_decrypted_credential(session, "anthropic")` works for the new client. |
| Build artifacts | Python image will need `anthropic`, `scikit-learn` installed during Docker build. The `pyarr` upgrade is in-place. | Code edit: requirements.txt diff (D-20). Build action: rebuild image; existing GHA pipeline handles this. |

**The canonical question for Phase 5:** *After all files are updated, what runtime systems still have stale state?* Answer: None for the immediate Phase 5 deploy beyond the auto-backfill (which fills `Track.user_rating` columns from Plex on first startup, addressed by D-10).

**Watch for next-phase risk:** Phase 7's chat retirement will need a parallel inventory at that time — the v1 `chat_service.py` writes to v1 `Playlist`/`PlaylistTrack` tables; those tables will need an "exists but unused" decision then.

## Common Pitfalls

### Pitfall 1: Webhook duplicate events crash dedupe assumptions

**What goes wrong:** Plex retries on slow handler responses; same event arrives twice. Without UNIQUE on `EventLog.dedupe_key`, downstream handler runs twice — slot tracks twice into vibes (Phase 6+), double-charge LLM calls (Phase 7+).

**Why it happens:** No Plex-supplied `event_id`; at-least-once delivery; webhook handler that takes >1s gets retried.

**How to avoid:**
1. Receiver returns 200 in <50ms (D-05 enforces).
2. `dedupe_key = sha256(f"{event_type}|{ratingKey}|{user_rating}|{int(time.time()/5)}")` — 5-second bucket prevents Plex retries from looking distinct.
3. `INSERT OR IGNORE` into `EventLog` BEFORE dispatching the handler. Skip dispatch on rowcount==0.
4. For `media.rate` specifically, include the new rating value in the key — a quick rate-then-unrate produces two distinct events.

**Warning signs:**
- Same `ratingKey` slotted into a vibe twice within a minute (Phase 6).
- Anthropic call counter shows >1 per `media.scrobble` (Phase 7+).
- EventLog has no rows but UI says webhook fired (means dedupe is over-eager — increase bucket size or include more fields).

### Pitfall 2: `userRating` 0-10 vs 0-5 — display bugs

**What goes wrong:** Plex stores `userRating` as float on **0-10 scale** (1 star = 2.0, 5 stars = 10.0). UI accidentally shows "10 stars."

**Prevention (locked in D-15):**
- Single `stars_from_user_rating(raw: float | None) -> str` helper used everywhere on the display path.
- Persist raw 0-10 in SQLite; never round-trip through normalized 0-5.
- Filter rated set as `WHERE user_rating IS NOT NULL AND user_rating > 0`.
- **Required test:** assert `userRating=7.0 → "3.5 stars"` and DB stores `7.0`.

### Pitfall 3: PlexAPI sync calls inside async handlers stall the event loop

**What goes wrong:** `async def webhook_handler(...)` calls `plex.fetchItem(...)` (blocking via `requests`). Whole asyncio loop freezes for 100ms-5s; health endpoint stalls; Plex retries the slow handler.

**Prevention (locked in D-09):**
- Wrap **every** PlexAPI call in `asyncio.to_thread(plex.method, ...)`. Existing pattern at `plex_client.py:45`.
- Add a project convention to CLAUDE.md before any handler ships.
- Optional: a load test in CI — 30 webhooks/sec for 2 minutes; assert p95 health-check latency <200ms.

### Pitfall 4: Anthropic prompt cache TTL silent regression (March 2026)

**What goes wrong:** Default ephemeral TTL silently dropped from 1h to 5min. Event-driven calls fire less often than 5min, so every call writes the cache (paying 1.25x input cost) and never reads it (paying 0% discount). Net: paying *more* than no caching while believing caching is saving money.

**Prevention:**
- ALWAYS specify `cache_control: {"type": "ephemeral", "ttl": "1h"}` explicitly. Verified syntax against `platform.claude.com/docs/en/build-with-claude/prompt-caching` (HIGH confidence).
- Log `usage.cache_creation_input_tokens` and `usage.cache_read_input_tokens` per call into the new `LLMUsage` table.
- **Required test:** call the client twice within 30 seconds; assert second response's `cache_read_input_tokens > 0`.
- 2,048 token minimum on Sonnet 4.6 — system message must be padded to that or caching silently does nothing. Per `platform.claude.com/docs/en/build-with-claude/prompt-caching`: "Shorter prompts cannot be cached, even if marked with `cache_control`. Any requests to cache fewer than this number of tokens will be processed without caching, and no error is returned."

### Pitfall 5: PlexAPI optional attributes auto-reload silently

**What goes wrong:** Accessing `track.userRating` on a partially-fetched Track instance triggers an automatic full reload (extra HTTP call) when the attribute wasn't present in the partial XML. In a polling loop over 200 tracks this is 200 extra HTTP calls that look like "PlexAPI is slow" but are actually our access pattern.

**Why it happens:** PlexAPI's `__getattribute__` does a lazy reload for optional attrs. [CITED: github.com/pkkid/python-plexapi/issues/713]

**How to avoid:**
- Use `library.search(filters={...}, sort=...)` which returns fully-populated Track objects.
- For polling, use `searchTracks(filters={"track.userRating>>": 0})` not `library.all()` followed by per-track attribute access.
- For webhook handler defensive re-fetch (Phase 6+), accept the cost or query via `fetchItems([rating_key1, rating_key2, ...])` batch call.
- For unrated tracks, `userRating` may be the literal Python attribute `None` OR raise `AttributeError` depending on version. Use `getattr(track, "userRating", None) or 0`. Treat `None` and `0` the same for "unrated."

**Warning signs:**
- Poll service takes >30s for 200 tracks (each one lazy-loads).
- Plex server logs show 200+ requests per polling cycle.

### Pitfall 6: `webhook_test` event detection has no canonical signal

**What goes wrong:** When the user adds a webhook URL in Plex Web → Account → Webhooks, Plex sends a test event. The exact payload format is **not documented**. The community Go library `plexwebhooks` (hekmon) defines only 6 event types: `media.pause`, `media.play`, `media.rate`, `media.resume`, `media.scrobble`, `media.stop`. There is **no** `webhook.test` or similar discriminator. [VERIFIED: pkg.go.dev/github.com/hekmon/plexwebhooks]

**Why it happens:** Plex sends a real `media.play` event from a "Plex Web" player as the test, with whatever happens to be on top of the user's library at that moment.

**How to handle:**
- Treat the FIRST event received within 60 seconds of the wizard's "Test webhook" button click as the test event. Stash its full payload in a module-level variable + an entry in `EventLog` with `event_type='webhook_test'` (synthesized), and have the wizard's HTMX poll target read from there.
- Don't try to discriminate by payload content — just match on "received within 60s of arming the wizard test."
- Display the parsed payload structure inline in the wizard (per CONTEXT.md Specifics #2) so the user can confirm it looks right.

**[ASSUMED]** Behavior may vary across Plex Media Server versions. The user is on a Synology NAS running an unspecified PMS version; if the test event behaves differently, we discover this only on first real test. Recommend: surface the raw payload in the wizard's debug area regardless, so the user can screenshot and share if anything looks off.

### Pitfall 7: First-startup race — backfill runs before sync has populated tracks

**What goes wrong:** Backfill (D-10) checks `Track.user_rating IS NULL for all rows`. On a true first deploy, the Track table is empty (sync hasn't run yet). Backfill thinks "no tracks need rating sync" and exits. User never gets ratings populated.

**How to avoid:**
- Backfill check is `(Track row exists with user_rating IS NULL AND no Track row has user_rating IS NOT NULL)` rather than just "any NULL rows." This is true after a first sync of v2 (rows exist, all NULL); false on truly empty DB.
- OR: backfill subscribes to a "sync completed" hook (extend `trigger_post_sync_analysis` pattern at `analysis_service.py:285`).
- Recommend the second: backfill triggered from the same hook that triggers analysis, gated on "is this the first run with the new schema?" check.

**Warning signs:**
- Backfill banner never appears after first deploy.
- All Track rows have `user_rating IS NULL` after several days of operation.

### Pitfall 8: `library.new` webhook for music — fires per-track or per-album?

**What goes wrong:** Plex docs and community packages describe `library.new` as "new item added." For music libraries, Plex may fire it per-track (one webhook per song imported), per-album (one per album), per-artist (one per artist) — **the documentation does not crisply say.** [LOW confidence — community search result didn't surface authoritative answer].

**How to handle:**
- Build the `LibraryAddedEvent` handler tolerant: if `Metadata.type == 'track'`, treat as a single track add; if `'album'`, fan out to `searchTracks(filters={"album.id": parent_rating_key})` to get the constituent tracks.
- Phase 5 only logs `LibraryAddedEvent`; doesn't act on it. The actual auto-analyze pipeline is Phase 8 (per CONTEXT.md, library.new is in scope here only as an event type, not a downstream behavior).

**Action for planner:** during the wizard flow, log the actual `library.new` payload to `EventLog.raw_payload` for the user's environment so we can confirm during Phase 5 what shape Plex is sending. The first time the user adds music after Phase 5 ships, we'll know.

### Pitfall 9: `ollama_client.py` deletion breaks tests

**What goes wrong:** D-02 says delete `app/services/ollama_client.py`. But [VERIFIED: grep on the codebase] `tests/test_service_clients.py:83-122` has 3 tests that import `ollama_client.test_ollama_connection`. Deleting the module without removing those tests = test suite fails on the very first commit of Phase 5.

**How to avoid:**
- Delete BOTH the source module AND the corresponding tests in the same commit.
- Lines to remove from `tests/test_service_clients.py`: roughly lines 80-130 (the 3 `@patch("app.services.ollama_client.OpenAI")` test methods).

**Warning signs:**
- `pytest tests/test_service_clients.py` fails on `ImportError` after Phase 5 deps land.

### Pitfall 10: Settings page footer link to /debug/events without route protection

**What goes wrong:** D-22 says "no auth (Tailscale-only access at network layer)." If user accidentally exposes the container on public internet, `/debug/events` shows EventLog rows including `raw_payload` which may contain Plex usernames, IPs, ratingKeys.

**How to handle:**
- This is acceptable per locked decision but worth surfacing in the page itself: "Diagnostics — visible to anyone who can reach this URL. Composer assumes Tailscale-only access."
- Truncate `raw_payload` displayed on the page to first 200 chars; full payload only stored in DB.
- Strip Plex tokens if they ever appear in payloads (defensive — they shouldn't).

## Code Examples

### Webhook receiver — push-and-return
```python
# app/routers/api_webhooks.py — verified against FastAPI 0.135 multipart pattern
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from typing import Annotated, Optional
from fastapi import APIRouter, Form, Response, UploadFile, File
import logging

from app.services.event_bus import get_event_bus
from app.models.events import (
    RatingChangedEvent, TrackPlayedEvent, LibraryAddedEvent, WebhookTestEvent,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Module-level cache for the wizard's "last test event" indicator (D-13).
# Wizard polls /api/webhooks/plex/last-test which reads this.
_last_test_received_at: Optional[str] = None
_last_test_payload: Optional[dict] = None


@router.post("/plex")
async def plex_webhook(
    payload: Annotated[str, Form()],            # JSON string in multipart 'payload' field
    thumb: Annotated[Optional[UploadFile], File()] = None,  # Plex sends a thumbnail file part
):
    """Receive Plex webhook. Push to bus and return 200 in <50ms.

    Per D-05: NEVER use pydantic.Json[Model] in Form() — FastAPI bug #10997.
    Per D-09: NO PlexAPI calls in this handler. Defer to dispatcher.
    """
    global _last_test_received_at, _last_test_payload

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Webhook payload not valid JSON, skipping")
        return Response(status_code=200)  # Always 200 — Plex retries on non-2xx

    event_type = data.get("event", "")
    metadata = data.get("Metadata", {}) or {}
    rating_key = metadata.get("ratingKey")
    raw_payload_str = json.dumps(data)[:4096]  # truncate for EventLog
    received_at = datetime.now(timezone.utc).isoformat()

    # If wizard test mode is "armed" (settings flag set in last 60s), treat next event as test.
    # Pitfall 6: there's no canonical Plex test discriminator — use timestamp gating.
    from app.services.webhook_test_state import is_test_armed, disarm_test
    if is_test_armed():
        _last_test_received_at = received_at
        _last_test_payload = data
        disarm_test()
        # Still process the actual event — but also push a synthetic test event for the wizard.
        bus = get_event_bus()
        bus.put_nowait(WebhookTestEvent(
            plex_rating_key=rating_key,
            source="webhook",
            received_at=received_at,
            raw_payload=raw_payload_str,
        ))

    bus = get_event_bus()
    if event_type == "media.rate":
        # Plex sends "Rating" at the top level for media.rate events.
        # NB: Plex's Rating field is on the 0-10 scale already (per Pitfall 2).
        new_rating = data.get("Rating") if "Rating" in data else metadata.get("userRating")
        bus.put_nowait(RatingChangedEvent(
            plex_rating_key=rating_key,
            new_rating=float(new_rating) if new_rating is not None else None,
            source="webhook",
            received_at=received_at,
            raw_payload=raw_payload_str,
        ))
    elif event_type == "media.scrobble":
        last_viewed = metadata.get("lastViewedAt") or received_at
        bus.put_nowait(TrackPlayedEvent(
            plex_rating_key=rating_key,
            last_viewed_at=str(last_viewed),
            source="webhook",
            received_at=received_at,
            raw_payload=raw_payload_str,
        ))
    elif event_type == "library.new":
        bus.put_nowait(LibraryAddedEvent(
            plex_rating_key=rating_key,
            library_section_id=metadata.get("librarySectionID"),
            source="webhook",
            received_at=received_at,
            raw_payload=raw_payload_str,
        ))
    # All other event types ignored silently. Always 200.
    return Response(status_code=200)


@router.get("/plex/last-test")
async def last_test_event():
    """HTMX-polled by the wizard. Returns ✓ partial when a test event has arrived."""
    from app.main import templates
    from fastapi import Request  # if needed
    # In practice this endpoint returns a Jinja2 partial; sketch only here.
    return {"received_at": _last_test_received_at, "payload": _last_test_payload}
```

[VERIFIED: FastAPI 0.135 supports `Annotated[str, Form()]` per fastapi/fastapi#10997 — workaround for pydantic.Json bug]

### Anthropic SDK call with prompt caching
```python
# app/services/anthropic_client.py — NEW module per D-01
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional, Type, TypeVar
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.database import get_engine
from app.models.llm_usage import LLMUsage
from sqlmodel import Session

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Sonnet 4.6 cache breakpoint minimum (per platform.claude.com/docs).
SONNET_4_6_CACHE_MIN_TOKENS = 2048


class AnthropicClient:
    """v2 Anthropic client — SEPARATE from v1 llm_client.py (D-01).

    Built on the official anthropic>=0.100 SDK with explicit ttl=1h cache_control.
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def call_with_structured_output(
        self,
        system_prompt: str,                  # the cacheable prefix; must be >2048 tokens to actually cache on Sonnet 4.6
        user_prompt: str,
        response_model: Type[T],
        max_tokens: int = 1024,
        purpose: str = "unspecified",        # for cost telemetry
    ) -> T:
        """Call Anthropic with cacheable system prompt; parse JSON response into Pydantic."""
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},  # explicit per D-03
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Verify caching is engaged. Per Pitfall 4: if both are 0, prompt is below
        # the 2048 token threshold and silently not cached.
        cache_creation = response.usage.cache_creation_input_tokens or 0
        cache_read = response.usage.cache_read_input_tokens or 0
        input_tokens = response.usage.input_tokens or 0
        output_tokens = response.usage.output_tokens or 0

        if cache_creation == 0 and cache_read == 0:
            logger.warning(
                "Anthropic caching not engaged for purpose=%s — system prompt likely <2048 tokens "
                "(Sonnet 4.6 minimum). Total input %d.", purpose, input_tokens
            )

        # Per D-04: log every call into LLMUsage for circuit breaker accounting.
        await self._log_usage(
            purpose=purpose,
            model=self._model,
            input_tokens=input_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            output_tokens=output_tokens,
        )

        # Parse the JSON response — ARCHITECTURE D-03 says no Instructor. Manual validation:
        text = response.content[0].text
        # Trim possible markdown fences.
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return response_model.model_validate_json(text)

    async def _log_usage(
        self,
        purpose: str,
        model: str,
        input_tokens: int,
        cache_creation_tokens: int,
        cache_read_tokens: int,
        output_tokens: int,
    ) -> None:
        """Insert one row per call. The circuit breaker reads this table."""
        from sqlmodel import Session
        import asyncio
        def _insert():
            with Session(get_engine()) as session:
                row = LLMUsage(
                    called_at=datetime.now(timezone.utc).isoformat(),
                    purpose=purpose,
                    model=model,
                    input_tokens=input_tokens,
                    cache_creation_input_tokens=cache_creation_tokens,
                    cache_read_input_tokens=cache_read_tokens,
                    output_tokens=output_tokens,
                    # Cost estimate: Sonnet 4.6 pricing as of May 2026 — verify before billing alerts.
                    # input $3 / MTok; cache_write_1h ~$6 / MTok; cache_read $0.30 / MTok; output $15 / MTok.
                    cost_estimate_usd=(
                        input_tokens * 3.0 / 1_000_000
                        + cache_creation_tokens * 6.0 / 1_000_000
                        + cache_read_tokens * 0.30 / 1_000_000
                        + output_tokens * 15.0 / 1_000_000
                    ),
                )
                session.add(row)
                session.commit()
        await asyncio.to_thread(_insert)


def get_anthropic_client_v2(session) -> AnthropicClient:
    """Factory using v1's existing settings table (no new credential)."""
    from app.services.settings_service import get_setting, get_decrypted_credential
    setting = get_setting(session, "anthropic")
    if not setting or not setting.is_configured:
        raise ValueError("Anthropic is not configured. Set up your API key in Settings first.")
    api_key = get_decrypted_credential(session, "anthropic")
    if not api_key:
        raise ValueError("Anthropic API key not found.")
    model = (setting.extra_config or {}).get("model_name", "claude-sonnet-4-6")
    return AnthropicClient(api_key, model)
```

[VERIFIED against pypi.org/project/anthropic v0.100.0 (released 2026-05-06); platform.claude.com/docs/en/build-with-claude/prompt-caching]
[CITED: startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/]

### EventLog model + migration shim extension
```python
# app/models/event_log.py — new model
from typing import Optional
from sqlmodel import Field, SQLModel

class EventLog(SQLModel, table=True):
    """Dedupe + audit trail for inbound webhook + poll events.

    UNIQUE(dedupe_key) is the source of truth for dedupe — `INSERT OR IGNORE` pattern.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)              # "webhook" | "poll" | "manual"
    event_type: str = Field(index=True)          # "rating_changed" | "track_played" | "library_added" | "webhook_test"
    plex_rating_key: Optional[str] = Field(default=None, index=True)
    dedupe_key: str = Field(unique=True, index=True)
    received_at: str = Field(index=True)         # ISO 8601 UTC; index for /debug/events ORDER BY DESC
    processed_at: Optional[str] = Field(default=None)
    handler_error: Optional[str] = Field(default=None)
    raw_payload: Optional[str] = Field(default=None)  # JSON; truncated to 4KB
```

```python
# app/database.py — extending the existing _migrate_add_columns()
def _migrate_add_columns(engine) -> None:
    import logging, sqlite3
    url = str(engine.url)
    db_path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(track)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # Phase 3 columns — keep existing
    new_columns = {
        "file_path": "TEXT",
        "energy": "REAL",
        # ... existing entries ...
        # Phase 5 additions (D-15):
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

    # Phase 5 DEBUG-01: index on EventLog.received_at for /debug/events ORDER BY DESC LIMIT 50.
    # Created via SQLModel; this is a defensive belt-and-suspenders.
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_eventlog_received_at_desc ON eventlog(received_at DESC)")

    # Existing energy recompute SQL block — leave alone.
    # ...

    conn.commit()
    conn.close()
```

### Polling job integrated into existing scheduler
```python
# app/services/sync_scheduler.py — extended (additive only)
from apscheduler.triggers.interval import IntervalTrigger

# ... existing code ...

def schedule_polling(interval_minutes: int = 5) -> None:
    """Register the Plex polling job alongside the existing library_sync job.

    Per D-08: same scheduler instance; bounded queries; same event bus / dedupe path as webhooks.
    """
    scheduler = get_scheduler()
    if scheduler.get_job("plex_polling"):
        scheduler.remove_job("plex_polling")
    scheduler.add_job(
        _trigger_polling,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="plex_polling",
        replace_existing=True,
        name=f"Plex polling every {interval_minutes}m",
    )
    logger.info("Scheduled Plex polling every %d minutes", interval_minutes)


async def _trigger_polling() -> None:
    """Job function called by APScheduler. Launches the poll service."""
    from app.services.poll_service import run_poll
    asyncio.create_task(run_poll())  # fire-and-forget; poll_service itself owns idempotency


# In start_scheduler() — add after schedule_sync():
async def start_scheduler() -> None:
    # ... existing code ...
    schedule_polling(interval_minutes=5)  # Phase 5 default per D-08
```

### Auto-backfill state machine (mirrors analysis_service singleton)
```python
# app/services/backfill_service.py — NEW
from __future__ import annotations
import asyncio, logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import Session, select
from app.database import get_engine
from app.models.track import Track

logger = logging.getLogger(__name__)


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
    error: Optional[str] = None


_backfill_status = BackfillStatus()


def get_backfill_status() -> BackfillStatus:
    return _backfill_status


async def maybe_trigger_first_run_backfill() -> None:
    """Called from lifespan. Runs ONCE if rating columns are all NULL but tracks exist (Pitfall 7)."""
    global _backfill_status
    if _backfill_status.state == BackfillStateEnum.RUNNING:
        return

    def _check():
        with Session(get_engine()) as session:
            # Has any track at all?
            any_track = session.exec(select(Track.id).limit(1)).first()
            if not any_track:
                return False  # truly empty DB — sync hasn't run yet
            # Any track WITH a rating?
            any_rated = session.exec(
                select(Track.id).where(Track.user_rating.is_not(None)).limit(1)  # type: ignore
            ).first()
            return any_rated is None  # tracks exist but none rated → run backfill
    needs_backfill = await asyncio.to_thread(_check)
    if needs_backfill:
        asyncio.create_task(run_backfill())


async def run_backfill() -> None:
    """Page through Plex via existing get_library_tracks pagination, populate user_rating
    + last_viewed_at + view_count for every existing Track row.
    """
    global _backfill_status
    if _backfill_status.state == BackfillStateEnum.RUNNING:
        return
    _backfill_status = BackfillStatus(state=BackfillStateEnum.RUNNING)
    try:
        # Mirror sync_service.run_sync() but write rating fields instead.
        # Implementation detail: extend plex_client._map_track to include the 3 new fields,
        # then iterate get_library_tracks() pages and upsert via a new
        # _upsert_track_ratings_sync() helper.
        # ... see plan.md ...
        _backfill_status.state = BackfillStateEnum.COMPLETED
    except Exception as exc:
        _backfill_status.state = BackfillStateEnum.FAILED
        _backfill_status.error = str(exc)
        logger.exception("Backfill failed")
```

### plex_client._map_track extension
```python
# app/services/plex_client.py — extend existing _map_track at line 8
def _map_track(t) -> dict:
    """Map a PlexAPI Track object to a dict with standard field names.

    Phase 5 additions: user_rating (raw 0-10), last_viewed_at, view_count.
    """
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
        # Phase 5 additions — defensive getattr because attributes are optional in partial responses
        "user_rating": getattr(t, "userRating", None),  # raw 0-10; None if unrated (Pitfall 5)
        "last_viewed_at": (lv.isoformat() if (lv := getattr(t, "lastViewedAt", None)) else None),
        "view_count": getattr(t, "viewCount", 0) or 0,
    }
```

### /debug/events — plain HTML, no JS-only content
```python
# app/routers/pages.py — add route
@router.get("/debug/events", response_class=HTMLResponse)
async def debug_events(request: Request, session: Session = Depends(get_session)):
    """DEBUG-01 + D-21: list last 50 events; copy-friendly; no JS-only content."""
    rows = session.exec(
        select(EventLog).order_by(EventLog.received_at.desc()).limit(50)
    ).all()
    from app.services.event_bus import get_event_bus
    queue = get_event_bus()
    queue_depth = queue.qsize()
    # Last poll info from sync_scheduler.get_scheduler().get_job("plex_polling").next_run_time
    scheduler = get_scheduler()
    poll_job = scheduler.get_job("plex_polling")
    poll_info = {
        "interval_minutes": 5,  # configured
        "next_run": poll_job.next_run_time.isoformat() if poll_job else None,
    }
    # Last test event (from api_webhooks._last_test_*)
    from app.routers.api_webhooks import _last_test_received_at, _last_test_payload
    # Configured webhook URL from ServiceConfig
    webhook_url = (get_setting(session, "webhook") or {}).get("url", None)
    return templates.TemplateResponse(
        request, "pages/debug_events.html",
        {
            "events": rows,
            "queue_depth": queue_depth,
            "poll_info": poll_info,
            "last_test_received_at": _last_test_received_at,
            "last_test_payload": _last_test_payload,
            "webhook_url": webhook_url,
        },
    )
```

```jinja
{# app/templates/pages/debug_events.html — DEBUG-01 #}
{% extends "base.html" %}
{% block title %}Diagnostics — Events{% endblock %}
{% block content %}
<div class="space-y-6">
  <header>
    <h1 class="text-2xl font-bold">Event Pipeline Diagnostics</h1>
    <p class="text-text-secondary text-sm">For diagnostics only. Visible to anyone reachable on this URL.</p>
  </header>

  <section>
    <h2 class="text-lg font-semibold mb-2">Configured</h2>
    <dl class="grid grid-cols-[140px_1fr] gap-y-1 text-sm">
      <dt class="text-text-secondary">Webhook URL</dt><dd class="font-mono break-all">{{ webhook_url or "not configured" }}</dd>
      <dt class="text-text-secondary">Poll interval</dt><dd>{{ poll_info.interval_minutes }}m (next run: {{ poll_info.next_run or "—" }})</dd>
      <dt class="text-text-secondary">Queue depth</dt><dd>{{ queue_depth }} events pending</dd>
      <dt class="text-text-secondary">Last test</dt>
      <dd>
        {% if last_test_received_at %}{{ last_test_received_at }}<br>
        <details><summary class="cursor-pointer text-text-secondary text-xs">payload preview</summary>
        <pre class="text-xs overflow-x-auto bg-surface-elevated p-2 mt-1">{{ last_test_payload | tojson(indent=2) }}</pre></details>
        {% else %}no test received yet{% endif %}
      </dd>
    </dl>
  </section>

  <section>
    <h2 class="text-lg font-semibold mb-2">Last 50 events</h2>
    <table class="w-full text-xs font-mono">
      <thead class="text-text-secondary"><tr>
        <th class="text-left p-1">received_at</th><th>src</th><th>type</th><th>ratingKey</th>
        <th>processed</th><th class="text-left">error / dedupe_key</th>
      </tr></thead>
      <tbody>
        {% for e in events %}
        <tr class="{% if e.handler_error %}bg-error/5{% endif %}">
          <td class="p-1 align-top">{{ e.received_at }}</td>
          <td class="align-top">{{ e.source }}</td>
          <td class="align-top">{{ e.event_type }}</td>
          <td class="align-top">{{ e.plex_rating_key or "—" }}</td>
          <td class="align-top">{{ e.processed_at or "—" }}</td>
          <td class="align-top">
            {% if e.handler_error %}<span class="text-error">{{ e.handler_error }}</span>{% else %}
            <span class="text-text-secondary text-[10px]">{{ e.dedupe_key[:16] }}…</span>{% endif %}
          </td>
        </tr>
        {% else %}
        <tr><td colspan="6" class="text-text-secondary p-2">No events yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </section>
</div>
{% endblock %}
```

### Wizard "test webhook" indicator (HTMX poll)
```jinja
{# app/templates/partials/webhook_test_indicator.html #}
{# HTMX-polled by the wizard form when a candidate URL is being tested. #}
{# Renders one of: ⏳ waiting / ✓ received / ✗ timeout (after 60s). #}
<div id="webhook-test-indicator"
     hx-get="/api/webhooks/plex/last-test?since={{ test_armed_at }}"
     hx-trigger="every 2s"
     hx-target="#webhook-test-indicator"
     hx-swap="outerHTML"
     class="inline-flex items-center gap-2 px-3 py-1 rounded-full text-sm
            {% if last_test_received_at and last_test_received_at > test_armed_at %}
              bg-success/10 text-success border border-success/30
            {% else %}
              bg-surface-elevated text-text-secondary
            {% endif %}">
  {% if last_test_received_at and last_test_received_at > test_armed_at %}
    ✓ Webhook test received at {{ last_test_received_at }}
  {% else %}
    ⏳ Waiting for test event… (paste URL into Plex Web → Account → Webhooks, then send test)
  {% endif %}
</div>
```

## State of the Art

| Old Approach (v1 / pre-v2) | Current Approach (Phase 5) | When Changed | Impact |
|---------------------------|----------------------------|--------------|--------|
| `httpx` direct calls to Anthropic | `anthropic.AsyncAnthropic` SDK | This phase (D-01, OPS-02) | Native typed `usage.cache_*_tokens`; explicit `cache_control={"ttl":"1h"}`; structured response parsing. v1 client stays for chat retirement in Phase 7. |
| 5-minute default ephemeral TTL | Explicit `ttl: "1h"` | Anthropic regressed default March 2026 | Without explicit TTL, every event-driven LLM call is a cache write with no read amortization (PITFALL 9, $5/year budget at risk) |
| Default Anthropic prompt cache without minimum-token check | Verify `cache_creation_input_tokens > 0` after first call | Sonnet 4.6 minimum bumped to 2048 (was 1024 on Sonnet 3.7/4.5) | If system message <2048 tokens, caching silently disabled — pad with project description / vibe context |
| `pydantic.Json[Model]` in `Form()` | `Annotated[str, Form()]` + `json.loads()` | FastAPI bug #10997 still open | Avoids the documented multipart parse failure |
| `pyarr>=5.2,<6.0` (stale pin from v1) | `pyarr>=6.6,<7.0` | This phase (OPS-03) | Phase 8's Lidarr discovery needs 6.6 endpoints; bump now to consolidate dep churn |
| sklearn not in deps | `scikit-learn>=1.8,<2.0` | This phase (OPS-04) | Phase 6 actually uses it; added Phase 5 to consolidate dep churn (image rebuild = 1, not 2) |
| `BackgroundTasks` per webhook | Single asyncio.Queue + dispatcher | This phase (D-06) | Avoids per-request task spawning under listening-session burst |

**Deprecated/outdated:**
- `app/services/ollama_client.py` — Confirm no consumers (only test mocks at `tests/test_service_clients.py:83-122`). Delete in Phase 5 along with those tests (D-02).
- The 5-min default Anthropic prompt cache TTL — never rely on default; explicit `ttl: "1h"` everywhere.
- "Heredoc-style" `cat > file.py << 'EOF'` in shell for content creation — use Write tool. (Documented in CLAUDE.md? If not, add.)

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | PlexAPI's `track.userRating>>` filter syntax follows the documented pattern; exact string may need adjustment | Pattern 3 (Polling delta queries) | Polling job returns 0 results until syntax fixed. Mitigation: spike during planning; one round-trip to fix. |
| A2 | Plex sends a synthetic event (likely `media.play` from "Plex Web" player) when a webhook URL is added — there is no canonical "test" event_type | Pitfall 6 | If Plex behavior differs, wizard's ✓ indicator never lights up. Mitigation: timestamp-based gating (first event within 60s of arming); display raw payload in wizard regardless. |
| A3 | `library.new` event for music libraries fires per-track (community packages suggest this but docs are not crisp) | Pitfall 8 | Phase 5 only logs; Phase 8 will be the first phase that depends on this. Logging the actual payload now de-risks Phase 8. |
| A4 | `psutil` is transitively pulled in the v1 install (used by FastAPI's uvicorn dev tooling or similar) | Pattern 7 (URL detection) | If not present, `import psutil` fails — Tailscale candidate detection silently skipped. Mitigation: planner verifies via `pip show psutil`; if missing, add to requirements.txt explicitly (`psutil>=5.9,<7.0`). |
| A5 | Sonnet 4.6 pricing: input $3 / cache_write_1h $6 / cache_read $0.30 / output $15 per million tokens | anthropic_client cost estimate | If pricing is off, the LLM cost circuit breaker miscalibrated. Mitigation: pricing is "estimate" not "billing"; planner should add a "verify before next phase" comment in the cost calc. |
| A6 | The Plex `media.rate` payload's top-level `Rating` field stores the raw 0-10 value, not 0-5 | Code Example "Webhook receiver" | If 0-5, ratings written to Track will be off by 2× until display path is corrected. Mitigation: log the first `media.rate` payload during dev; assert the value matches what the user sees in Plexamp. |
| A7 | Plex's `media.scrobble` for music fires at ~50% playback (confirmed for video at 90%; music threshold less crisp in docs) | Implicit in TrackPlayedEvent semantics | If threshold is different, `view_count` doesn't behave as expected. Phase 5 only counts; Phase 7 acts on it — risk concentrated in Phase 7. |
| A8 | A `WebhookTestEvent` is best modeled as a synthetic event injected by the receiver based on the "wizard test mode armed in last 60s" flag, not by parsing the payload | Code Example "Webhook receiver"; Pitfall 6 | If Plex changes behavior (adds an actual test discriminator field), our gating becomes redundant but not broken. Low risk. |
| A9 | The auto-backfill check distinguishes "first deploy + sync done" from "first deploy + sync not yet done" via `(any track exists) AND (no track has user_rating)` | Pitfall 7 | If backfill triggers on a partially-synced DB, it'll page through Plex for tracks not yet in our DB. Probably fine — get_library_tracks pages from Plex regardless. Worst case: extra Plex API calls. |

**If this table contains items:** the planner must confirm each [ASSUMED] item before locking the relevant decision in PLAN.md. Items A1, A2, A3, A4 are highest-leverage to verify during the first commit of the phase (a 30-minute spike).

## Open Questions

1. **Plex webhook `media.scrobble` exact threshold for music**
   - What we know: Video is ~90%; music is "around 50%" per ARCHITECTURE.md sources but the cutoff isn't published.
   - What's unclear: 50% or 80% or first-90s? Affects whether `view_count` is "user really played this" or "Plexamp briefly opened it."
   - Recommendation: log first 10 `media.scrobble` events with the player position metadata if available; sanity-check against the user's actual listening behavior. Defer real action to Phase 7 where it matters.

2. **Will `library.new` fire for the user's auto-imported Lidarr arrivals before the user has rated them?**
   - What we know: yes, Plex fires `library.new` on file scan — confirmed by community packages.
   - What's unclear: when Lidarr-imports complete, how quickly Plex picks them up (depends on user's Plex library scan interval).
   - Recommendation: Phase 5 logs only. Phase 8 (Lidarr discovery + auto-ingest) is where we act on this.

3. **Webhook URL accessibility from Plex's network position**
   - What we know: user runs Plex on the same Synology NAS; same `synobridge` Docker network.
   - What's unclear: whether Plex (running as native NAS app or Docker?) can reach Composer at `http://composer:8085/...` (Docker DNS), or only via NAS LAN IP.
   - Recommendation: present all 3 candidates, let the user pick whichever the test event lands on. This is exactly what D-12/D-13 implement.

4. **Polling interval impact on user's NAS at 10k tracks**
   - What we know: bounded queries (filter on `userRating`, `lastViewedAt`, `addedAt`) return tens of tracks in normal operation.
   - What's unclear: peak case (user does 100 ratings in a Plexamp session) — could the 5-min poll fetch 100 tracks and cause UI lag?
   - Recommendation: 5-min default is conservative; user can tune via DB if it matters. Real measurement will only come from production deploy.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Plex Server (Plex Pass) | Webhook reception | ✓ (per CONTEXT) | unspecified PMS | Polling fallback (D-08) covers no-Plex-Pass and webhook outages |
| Lidarr | Phase 8 only — NOT Phase 5 | ✓ (per v1) | per v1 install | — |
| Anthropic API access | Taste profile summary call (D-17) | ✓ (per v1 already configured) | account-level | If unreachable: log + fail-soft on the LLM portion of taste profile; structured aggregates still computed |
| Synology DS423+ Docker `synobridge` network | Webhook URL Docker hostname candidate | ✓ (per hardware-profile.md) | — | NAS LAN IP candidate is the fallback when Docker hostname resolution fails |
| Tailscale | Tailscale URL candidate | ✓ (per hardware-profile.md) | — | Skipped if no 100.x interface present; user picks one of the other two candidates |
| Python 3.12 + amd64 manylinux wheels | scikit-learn install | ✓ (per Phase 3 Essentia pattern) | 3.12 | None — Phase 5 deps are all amd64 manylinux |
| `psutil` | Tailscale IP detection | [ASSUMED present transitively — VERIFY] | unknown | If missing: add `psutil>=5.9,<7.0` to requirements.txt |
| `httpx` | v1 chat (untouched), anthropic SDK transitively | ✓ | `>=0.28,<1.0` | — |

**Missing dependencies with no fallback:** None blocking.

**Missing dependencies with fallback:** `psutil` may need to be added explicitly — flagged for planner.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio (already configured) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (asyncio_mode="auto", testpaths=["tests"]) |
| Quick run command | `pytest tests/test_event_bus.py tests/test_api_webhooks.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| EVT-01 | `POST /api/webhooks/plex` returns 200 in <50ms with valid multipart | unit + perf | `pytest tests/test_api_webhooks.py::test_returns_200_under_50ms -x` | ❌ Wave 0 |
| EVT-02 | Same dedupe_key inserted twice → only one EventLog row | unit | `pytest tests/test_event_log.py::test_dedupe_unique_constraint -x` | ❌ Wave 0 |
| EVT-03 | Polling APScheduler job emits typed events with `source='poll'` | unit | `pytest tests/test_poll_service.py::test_emits_rating_changed -x` | ❌ Wave 0 |
| EVT-04 | Single dispatcher consumes queue serially | unit | `pytest tests/test_event_bus.py::test_dispatcher_serializes -x` | ❌ Wave 0 |
| EVT-05 | "Resync now" button triggers backfill_service | integration | `pytest tests/test_api_rating_sync.py::test_resync_button -x` | ❌ Wave 0 |
| EVT-06 | All `_*_sync` helpers run via `asyncio.to_thread` (no direct PlexServer calls in async path) | static (grep test) | `pytest tests/test_event_handlers.py::test_no_blocking_plexapi_in_async -x` | ❌ Wave 0 |
| EVT-07 | webhook_url_detection returns 3 plausible candidates given a request | unit | `pytest tests/test_webhook_url_detection.py -x` | ❌ Wave 0 |
| RATE-01 | `userRating=7.0` → display "3.5 stars" AND DB stores 7.0 | unit | `pytest tests/test_rating_helpers.py::test_stars_from_user_rating -x` | ❌ Wave 0 |
| RATE-02 | `backfill_service.run_backfill` fills user_rating for all tracks | integration | `pytest tests/test_backfill_service.py::test_full_run -x` | ❌ Wave 0 |
| RATE-03 | `media.rate` webhook → Track.user_rating updated, Track.rating_changed_at set | integration | `pytest tests/test_event_handlers.py::test_handle_rating_changed -x` | ❌ Wave 0 |
| RATE-04 | `WHERE user_rating > 0` returns rated set efficiently (uses index) | unit (EXPLAIN QUERY PLAN) | `pytest tests/test_track_model.py::test_user_rating_indexed -x` | ❌ Wave 0 |
| RATE-05 | `taste_profile_service.recompute()` upserts TasteProfile with centroid + summary text | integration with mocked anthropic | `pytest tests/test_taste_profile_service.py::test_recompute -x` | ❌ Wave 0 |
| OPS-01 | `_migrate_add_columns()` adds 4 new Track cols + creates EventLog/LLMUsage/TasteProfile tables | integration | `pytest tests/test_database.py::test_phase5_migration -x` | ❌ Wave 0 (extend existing) |
| OPS-02 | New AnthropicClient sends `cache_control={"ttl":"1h"}` on system message | unit (mock SDK) | `pytest tests/test_anthropic_client.py::test_explicit_ttl_1h -x` | ❌ Wave 0 |
| OPS-03 | `pyarr>=6.6,<7.0` in requirements.txt | static (grep) | `grep "pyarr>=6.6" requirements.txt` | n/a (manual) |
| OPS-04 | `scikit-learn>=1.8,<2.0` in requirements.txt | static | `grep "scikit-learn>=1.8" requirements.txt` | n/a (manual) |
| DEBUG-01 | `/debug/events` returns 200 and renders last 50 EventLog rows | integration (TestClient) | `pytest tests/test_pages.py::test_debug_events_page -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_<changed_module>.py -x` (target <5s)
- **Per wave merge:** `pytest tests/ -x` (target <30s — current v1 suite is ~20s on this machine)
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/test_event_bus.py` — covers EVT-04, EVT-01 partial
- [ ] `tests/test_api_webhooks.py` — covers EVT-01, EVT-02, dedupe behavior
- [ ] `tests/test_event_log.py` — covers EVT-02 UNIQUE constraint
- [ ] `tests/test_poll_service.py` — covers EVT-03
- [ ] `tests/test_event_handlers.py` — covers RATE-03, EVT-06
- [ ] `tests/test_webhook_url_detection.py` — covers EVT-07
- [ ] `tests/test_rating_helpers.py` — covers RATE-01 (the off-by-two display test)
- [ ] `tests/test_backfill_service.py` — covers RATE-02
- [ ] `tests/test_taste_profile_service.py` — covers RATE-05 (mock Anthropic)
- [ ] `tests/test_anthropic_client.py` — covers OPS-02 (verify cache_control kwarg)
- [ ] `tests/test_pages.py::test_debug_events_page` — covers DEBUG-01
- [ ] `tests/test_api_rating_sync.py::test_resync_button` — covers EVT-05
- [ ] `tests/test_track_model.py::test_user_rating_indexed` — extend existing file
- [ ] `tests/test_database.py::test_phase5_migration` — extend existing file
- [ ] **Removal:** delete `tests/test_service_clients.py` lines 80-130 (the 3 `ollama_client` tests, per D-02)

Framework install: nothing new — pytest + pytest-asyncio + httpx (TestClient) already present.

## Security Domain

`security_enforcement` is not explicitly configured. Default = enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Tailscale-only access at network layer (per project notes); single-user app; no in-app auth |
| V3 Session Management | no | No sessions; HTMX + cookies-free wizard (D-22 server-side state) |
| V4 Access Control | no | Single-user; no role distinctions; debug page surfaced uniformly |
| V5 Input Validation | yes | Pydantic models on webhook payload; `json.loads` wrapped in try/except returning 200; truncate raw_payload to 4KB before persisting |
| V6 Cryptography | partial | Anthropic API key already encrypted via existing `CredentialEncryptor` (Fernet) — no change in Phase 5; no new secrets introduced |
| V7 Error Handling | yes | Webhook always returns 200 (don't leak internal state); EventLog handler_error captures the message but not the full stacktrace; `/debug/events` truncates raw_payload to 200 chars on display |
| V8 Data Protection | yes | Track.user_rating is non-sensitive; raw_payload may contain Plex usernames + player IPs — strip Plex tokens defensively (none expected in payload but verify on first deploy) |
| V13 API & Web Service | yes | Webhook endpoint accepts unauth POSTs (intentional — Plex sends them) |

### Known Threat Patterns for Phase 5

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Webhook URL scraped from public site → spam fake events | Spoofing | Tailscale-only access at network; document this. Optional: per-deployment shared secret in URL path (e.g. `/api/webhooks/plex/<random>`) — defer unless a real spam signal appears. |
| Webhook with malformed JSON → handler crash | Tampering / DoS | `json.loads` wrapped in try/except → return 200; never raise from receiver |
| Plex token leak via raw_payload in `/debug/events` | Info Disclosure | Plex doesn't include token in webhook payload — verify on first deploy; log warning + redact if found |
| LLM cost runaway from buggy refresh trigger | Repudiation (cost surprise) | LLMUsage table + circuit breaker scaffolding ships in Phase 5 (D-04); UI surface deferred to Phase 7 (per Claude's Discretion in CONTEXT.md) |
| SQLite injection via webhook payload field interpolated into SQL | Tampering | All EventLog inserts go through SQLModel parameterized queries — no string interpolation. Code examples above use `:bind` parameters. |
| Race between concurrent webhook + poll for same event | Tampering | UNIQUE(dedupe_key) at DB level handles atomically (Pattern 6) |

## Sources

### Primary (HIGH confidence)
- [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — verified `cache_control={"type":"ephemeral","ttl":"1h"}` syntax, 2048-token Sonnet 4.6 minimum, silent disablement on too-short prompts
- [anthropic 0.100.0 on PyPI](https://pypi.org/project/anthropic/) — released 2026-05-06, native `cache_control` support
- [github.com/anthropics/anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python) — verified `AsyncAnthropic` import path, `usage.cache_creation_input_tokens` typed attribute
- [hekmon/plexwebhooks Go package docs](https://pkg.go.dev/github.com/hekmon/plexwebhooks) — full Plex webhook payload schema (Account, Server, Player, Metadata fields)
- [PlexAPI library filters](https://python-plexapi.readthedocs.io/en/latest/modules/library.html) — `library.search(filters={"track.userRating>>": 8})`, sort syntax
- [PlexAPI audio.py](https://python-plexapi.readthedocs.io/en/latest/modules/audio.html) — Track inherits userRating, lastViewedAt, viewCount from Audio
- [FastAPI lifespan events docs](https://fastapi.tiangolo.com/advanced/events/) — `asynccontextmanager` pattern for `asyncio.create_task` background loops
- [Tailscale CGNAT range](https://tailscale.com/docs/concepts/tailscale-ip-addresses) — confirmed 100.64.0.0/10
- v2.0 milestone research: `.planning/research/SUMMARY.md`, `STACK.md`, `ARCHITECTURE.md`, `PITFALLS.md` (all internal, HIGH authority for v2 scope)
- v1 source: `app/services/sync_service.py`, `analysis_service.py`, `sync_scheduler.py`, `plex_client.py`, `database.py`, `main.py` — patterns verified in-place

### Secondary (MEDIUM confidence)
- [startdebugging.net April 2026 prompt cache walkthrough](https://startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/) — verified hit-rate measurement pattern
- [DEV.to "Anthropic Silently Dropped Prompt Cache TTL"](https://dev.to/whoffagents/anthropic-silently-dropped-prompt-cache-ttl-from-1-hour-to-5-minutes-16ao) — confirms March 2026 default regression
- [FastAPI Discussion #5666](https://github.com/fastapi/fastapi/discussions/5666) — JSON-in-multipart-Form pattern
- [FastAPI Issue #10997](https://github.com/fastapi/fastapi/issues/10997) — open `pydantic.Json` Form bug
- [github.com/pkkid/python-plexapi/issues/713](https://github.com/pkkid/python-plexapi/issues/713) — auto-reload on optional attribute access

### Tertiary (LOW confidence — flagged for verification)
- [A1] PlexAPI `track.userRating>>` exact filter string — pattern documented but specific track-prefix syntax needs spike confirmation
- [A2] Plex webhook test event behavior — community packages don't list a `webhook.test` event_type; behavior assumed but not authoritatively documented
- [A3] `library.new` granularity for music libraries — community sources unclear
- [A5] Sonnet 4.6 token pricing — moves; needs annual verification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all 3 new/bumped deps verified against PyPI 2026-05-08; v1 stack untouched
- Architecture: HIGH — every pattern grounded in observable v1 code (sync_service, analysis_service, _migrate_add_columns) or canonical FastAPI/asyncio docs
- Pitfalls: HIGH for #1-5, 9, 10; MEDIUM for #6, 7 (depend on Plex behavior verifiable only on first deploy); LOW for #8 (library.new granularity uncertain — Phase 8 risk, not Phase 5)
- Webhook payload structure: HIGH (verified against `plexwebhooks` Go package which has full schema)
- Anthropic SDK syntax: HIGH (verified against official docs + SDK source)
- PlexAPI filter syntax: MEDIUM (pattern documented; exact `track.userRating>>` string is [ASSUMED])
- Tailscale IP detection: HIGH (CGNAT range RFC 6598 + psutil pattern verified)

**Research date:** 2026-05-08
**Valid until:** 2026-06-08 (30 days; Anthropic SDK and PlexAPI both move slowly; Plex webhook schema even slower)
