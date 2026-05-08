# Phase 5: Plex Event Foundation + Rating Sync - Context

**Gathered:** 2026-05-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 5 lays the event-driven foundation for v2.0. Composer reliably ingests Plex events from two sources (webhook receiver + APScheduler polling fallback), dedupes them via an `EventLog` table, and propagates typed events (`RatingChanged`, `TrackPlayed`, `LibraryAdded`) onto a single `asyncio.Queue` event bus that downstream phases consume. The `Track` model gains `user_rating` (raw 0-10), `last_viewed_at`, `view_count`, and `rating_changed_at`. A first-run auto-backfill job populates these fields for the existing ~10k tracks. A new `anthropic_client.py` (using the SDK with prompt caching) is built alongside the legacy v1 LLM client; a full taste profile (centroid + top artists/genres + LLM-generated summary text) is computed and cached, with recompute triggered on RatingChanged when the rated set delta ≥ 10%. A `/debug/events` HTML page surfaces the event pipeline state for diagnostics.

This is the trunk every later v2 phase depends on. If event ingestion is wrong, vibes (Phase 6) and Suggestions (Phase 7) break.

</domain>

<decisions>
## Implementation Decisions

### Anthropic SDK migration scope
- **D-01:** Build a NEW `app/services/anthropic_client.py` using the official `anthropic>=0.100,<1.0` SDK. v1 `app/services/llm_client.py` (direct-httpx) and `app/services/chat_service.py` (mood-chat consumer) remain UNTOUCHED. They keep working as-is until Phase 7 deletes both atomically when the chat UI retires.
- **D-02:** The legacy `app/services/ollama_client.py` (~82 lines) has no remaining consumers (Anthropic took over mid-v1). Confirm and DELETE in Phase 5 — small win, removes dead code from the surface area.
- **D-03:** New client must support: `cache_control: {"type": "ephemeral", "ttl": "1h"}` on the system message (explicit TTL — don't rely on the silently-changed 5-min default), structured output via `model_validate_json()` on a Pydantic class (no Instructor — fights prompt caching), per-call usage logging (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `cost_estimate_usd`).
- **D-04:** Cost circuit breaker scaffolding ships in Phase 5 alongside the client, even though the actual ranking calls don't fire until Phase 7. Counters (`daily_calls`, `burst_window`, `last_call_at`) live in a `LLMUsage` table. This way Phase 7's first ranking call already has the breaker in place — no retrofitting under fire.

### Event ingestion (webhook + polling)
- **D-05:** Webhook endpoint at `POST /api/webhooks/plex` returns 200 within 50ms. Parses Plex's multipart/form-data with the `payload` JSON field via `Annotated[str, Form()]` + `json.loads()` — explicitly NOT `pydantic.Json[Model]` (FastAPI bug #10997).
- **D-06:** All inbound events become typed Pydantic events (`RatingChanged`, `TrackPlayed`, `LibraryAdded`, `WebhookTest`) and get pushed onto a single `asyncio.Queue` event bus. A single dispatcher task consumes the queue and serializes downstream handlers — prevents SQLite write contention.
- **D-07:** `EventLog` table with `UNIQUE(dedupe_key)` constraint where `dedupe_key = sha256(event_type|ratingKey|user_rating|5s_timestamp_bucket)`. `INSERT OR IGNORE` pattern — duplicates drop silently. Webhook + polling overlap resolves naturally through this UNIQUE.
- **D-08:** Polling job runs via the existing `app/services/sync_scheduler.py` APScheduler. Default 5-min interval. Polls bounded queries: `userRating` diffs, `lastViewedAt` diffs, `addedAt>>` for new tracks. NEVER full library scan. Same event bus / dedupe path as webhooks.
- **D-09:** PlexAPI is sync; ALL calls from event handlers wrap in `asyncio.to_thread(...)`. Pattern already in `app/services/plex_client.py` (line 45) — extend it. Document the convention in CLAUDE.md before any handler ships.

### Backfill on first deploy
- **D-10:** On first Phase 5 startup (detected by `Track.user_rating IS NULL` for all rows after migration), schedule a one-time auto-backfill background job. Pages through Plex via existing `get_library_tracks()` pagination, fills in `user_rating`, `last_viewed_at`, `view_count` for every existing track. Status visible as a banner like sync/analysis (reuse `sync_service.py` singleton pattern).
- **D-11:** Manual "Resync now" button (EVT-05) stays in settings. Auto handles initial deploy; manual handles future drift (e.g., after webhook outage).

### Webhook URL display in wizard
- **D-12:** Wizard auto-detects three URL candidates and presents them via radio buttons:
  1. Docker hostname (`http://composer:8085/api/webhooks/plex`) — derived from container's own hostname; works when Plex shares `synobridge` network
  2. NAS LAN IP (`http://<host-ip>:<port>/api/webhooks/plex`) — derived from the first browser request's Host header; works when Plex uses host networking
  3. Tailscale IP (`http://100.x.x.x:<port>/api/webhooks/plex`) — detected by checking if any 100.x address is bound; fallback for remote access setups
- **D-13:** Each candidate has a "Test webhook" action button. Wizard shows a live "✓ webhook test received" indicator (HTMX poll on `/api/webhooks/plex/last-test`) when Plex's test event arrives at that path. User picks whichever turns ✓ first.
- **D-14:** Whichever URL the user picks gets persisted to `ServiceConfig` so settings page can display "Composer is currently configured for webhooks at: <url>" and the user can swap it later without re-running the wizard.

### Rating sync + taste profile scope
- **D-15:** `Track` model gets four new columns via `_migrate_add_columns()`:
  - `user_rating` (REAL, raw 0-10 from Plex; conversion to 0-5 stars happens ONLY at display boundaries via a single helper — write a unit test asserting `7.0 → "3.5 stars"`)
  - `last_viewed_at` (TEXT, ISO string)
  - `view_count` (INTEGER, default 0)
  - `rating_changed_at` (TEXT, ISO string — when user_rating last changed)
- **D-16:** New `RatedTrackView` (just a SQL query, not materialized): `SELECT * FROM track WHERE user_rating > 0`. Index on `user_rating`. Used by the taste profile builder and downstream phases.
- **D-17:** Phase 5 builds the FULL taste profile (RATE-05 in its entirety, no split):
  - Structured part: 4-D centroid (energy, tempo, danceability, valence), top 10 artists by rated count, top 10 genres, count + distribution stats, last_computed_at
  - LLM-generated summary text: ~200-word "your taste tends toward X / leans Y / has a wide range in Z" via the new `anthropic_client` with prompt caching enabled — this verifies the prompt-cache infrastructure works end-to-end before Phase 6/7 depend on it
  - Storage: new `TasteProfile` table (single row, upserted on recompute)
- **D-18:** Recompute trigger: when `RatingChanged` events accumulate to a delta ≥10% of the prior rated set size since `last_computed_at`. Implemented as a counter on the dispatcher, not a scheduled job.

### Schema migrations + dependencies
- **D-19:** Extend the existing `_migrate_add_columns()` shim in `app/database.py` (line 48) for the four new `Track` columns. New tables (`EventLog`, `LLMUsage`, `TasteProfile`) via `SQLModel.metadata.create_all()`. NO Alembic — locked at milestone level.
- **D-20:** `requirements.txt` updates ALL happen in Phase 5: add `anthropic>=0.100,<1.0`, add `scikit-learn>=1.8,<2.0` (used in Phase 6 but added now to consolidate dep churn), bump `pyarr>=5.2,<6.0` → `pyarr>=6.6,<7.0`. Single dep-bump commit. `python-multipart>=0.0.24` is already present (used by webhook receiver).

### Debug surface (DEBUG-01)
- **D-21:** `/debug/events` page renders plain HTML (no JS-only content — copy-pasteable). Shows:
  - Last 50 EventLog entries: timestamp, source (webhook/poll), event_type, ratingKey, dedupe_key, processed_at, handler_error
  - Current poll interval + last poll completed_at + last poll result (rows changed)
  - Last "test webhook" received: timestamp + payload preview (or "no test received yet")
  - Current event bus depth (asyncio queue size — useful when investigating "is the dispatcher stuck?")
  - Composer's selected webhook URL from D-14
- **D-22:** Page is reachable from settings page footer ("View diagnostics →") and as a direct URL. No auth (Tailscale-only access at the network layer).

### Claude's Discretion
- Internal naming for Pydantic event classes (`RatingChangedEvent` vs `PlexRatingChange` etc.) — pick a consistent convention, document in CLAUDE.md if it differs from existing patterns
- Exact polling SQL queries / filter syntax against PlexAPI — researcher will surface options
- Specific layout/styling of `/debug/events` (table, color coding for errors, pagination if needed)
- Whether to add a `/api/webhooks/plex/last-test` endpoint or surface the last test as part of the page itself with HTMX polling
- The 50ms response-time target — exact mechanism (push-to-queue and return; vs background task; vs response-then-process)
- Backfill banner UX — copy the sync banner pattern; minor copy/style decisions are Claude's
- Cost circuit breaker UI surface in Phase 5 (deferred to Phase 7 when ranking starts) — Phase 5 just stores counters, no UI

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project & milestone scope
- `.planning/PROJECT.md` — v2.0 Music Companion milestone, locked decisions
- `.planning/REQUIREMENTS.md` — EVT-01..07, RATE-01..05, OPS-01..04, DEBUG-01 (Phase 5 reqs)
- `.planning/ROADMAP.md` §"Phase 5" — goal, success criteria, key concerns

### v2.0 research (THIS milestone — most recent, highest authority)
- `.planning/research/SUMMARY.md` — synthesis of all v2 research with locked decisions
- `.planning/research/STACK.md` — `anthropic>=0.100`, `scikit-learn>=1.8`, `pyarr>=6.6` rationale; FastAPI multipart parsing pattern; Tailwind 4 mobile primitives
- `.planning/research/ARCHITECTURE.md` — single asyncio.Queue dispatcher pattern, EventLog dedupe key construction, polling fallback design, webhook→event flow
- `.planning/research/PITFALLS.md` — webhook idempotency (Pitfall 1), userRating 0-10 (Pitfall 2), Anthropic cache TTL regression (Pitfall 9), PlexAPI sync→async (Pitfall 4), schema migration without Alembic (Pitfall 19)

### v1.0 research (foundation context)
- `.planning/research/v1.0/STACK.md` — original v1 stack rationale
- `.planning/research/v1.0/ARCHITECTURE.md` — sync_service / analysis_service singleton pattern that new services follow

### User notes (vision and constraints)
- `.planning/notes/connection-test-bugs.md` — Lidarr connection-test bug (Phase 8 — context only here)
- `.planning/notes/hardware-profile.md` — Synology DS423+, synobridge Docker network, Tailscale; informs webhook URL detection candidates
- `.planning/notes/mobile-first.md` — wizard must be mobile-first (settings page is the wizard host in Phase 5)

### Existing code (reusable / extension points)
- `app/database.py` §`_migrate_add_columns()` (line 48) — extend for `user_rating`, `last_viewed_at`, `view_count`, `rating_changed_at`
- `app/main.py` §`lifespan()` — startup hook to register polling job + dispatcher task; ensure asyncio.Queue is created before sync_scheduler starts
- `app/services/plex_client.py` §`_map_track()` (line 8) — add `userRating`, `lastViewedAt`, `viewCount` extraction
- `app/services/sync_service.py` — pattern for new `rating_sync_service.py` singleton (module-level state dataclass)
- `app/services/sync_scheduler.py` — APScheduler integration to extend with the polling job
- `app/services/analysis_service.py` — state-machine pattern for the auto-backfill job
- `app/services/llm_client.py` — DO NOT TOUCH (v1, retiring in Phase 7)
- `app/services/chat_service.py` — DO NOT TOUCH (v1, retiring in Phase 7)
- `app/services/ollama_client.py` — DELETE in Phase 5 (no consumers, dead code)
- `app/templates/base.html` — base template; settings footer adds "View diagnostics →" link

### Prior phase decisions (do not re-decide)
- `.planning/phases/01-foundation-configuration-deployment/01-CONTEXT.md` — settings page patterns, encryption helpers, lazy-engine singleton
- `.planning/phases/02-library-sync/02-CONTEXT.md` — sync banner UX, APScheduler job patterns
- `.planning/phases/03-audio-feature-extraction/03-CONTEXT.md` — analysis state machine, pause/resume pattern
- `.planning/phases/04-playlist-generation/04-CONTEXT.md` — current chat / LLM client surface (will retire in Phase 7)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`asyncio.to_thread` pattern** (plex_client.py:45): every PlexAPI call already wraps. Extend to all new event-handler paths — convention is in place, not a new invention.
- **Singleton service module pattern** (sync_service.py, analysis_service.py): module-level state dataclass + state enum + `start/stop/status` functions. New `rating_sync_service.py`, `event_dispatcher.py`, `backfill_service.py` follow this.
- **APScheduler integration** (sync_scheduler.py): existing AsyncIOScheduler started in lifespan. Adding the polling job is a one-liner `scheduler.add_job(...)`.
- **`_migrate_add_columns()` shim** (database.py:48): proven schema migration path. Add `user_rating`, `last_viewed_at`, `view_count`, `rating_changed_at` to the new_columns dict.
- **Sync banner HTMX pattern** (templates/partials, sync_service status endpoint): copy-paste-able for backfill banner with minor copy changes.
- **Encryption helpers** (encryption.py, settings_service.py): for secret config (no new secrets in Phase 5 but webhook URL config is non-sensitive).

### Established Patterns
- **HTMX swap convention** (Phase 4 burned issue): use `alpine-morph` extension when partials contain Alpine state. Document in CLAUDE.md if not already there.
- **JSON in form fields**: `Annotated[str, Form()]` + `json.loads()` — never `pydantic.Json[Model]` (FastAPI bug #10997).
- **Service-card test-and-configure flow** (Phase 1): pattern for the wizard's "test webhook" + "save selected URL" flow.
- **Module-level state singletons** (sync_service.SyncStatus): preferred over class-based services in this codebase. Match the convention.

### Integration Points
- **Lifespan startup** (main.py): register asyncio.Queue creation, dispatcher task, polling job. Order matters — Queue first, dispatcher second, polling third.
- **Settings page** (api_settings.py + templates/pages/settings.html): add a "Diagnostics" footer link to `/debug/events`.
- **Sync service** (sync_service.py): backfill service is a peer; backfill should NOT run while sync is running (mutex via existing lock or by status check).
- **Track model** (models/track.py): four new columns extend it; ensure migrations in `_migrate_add_columns` handle existing rows with NULL defaults.

</code_context>

<specifics>
## Specific Ideas

- **Debug-page-as-truth**: user has been burned by deploying to NAS without easy debug observability. The `/debug/events` page is not optional — it's the primary surface for "user sends Claude a screenshot when something looks wrong." Layout should optimize for screenshotting (visible vertical density; copy-button on key fields like dedupe_key and full payload).
- **Webhook test event payload visibility**: when Plex's "test webhook" event arrives, the wizard should show the parsed payload structure inline so the user can verify it's what we expect. Plex docs may not be authoritative for current payload format.
- **No silent failures**: every dropped duplicate, every handler error, every poll-vs-webhook conflict logs a row in EventLog with a non-empty `handler_error` field if anything went wrong. Phase 5 ships with this discipline; Phase 6+ inherits it.

</specifics>

<deferred>
## Deferred Ideas

- **Adaptive polling backoff** — could reduce polling frequency when recent webhooks have arrived (proves webhooks are healthy) and increase when they haven't (proves the webhook URL is wrong). Not needed for Phase 5; revisit in Phase 8 polish or v2.1 if the user reports unnecessary polling load on the NAS.
- **Plex webhook event ordering** — when 100 events arrive in 5 seconds, FIFO global vs FIFO per-ratingKey. Single dispatcher = global FIFO by default; if per-track ordering matters for Phase 6 slot-in, revisit there.
- **Webhook payload export-as-JSON button** on `/debug/events` — useful for long-form debugging but not necessary for Phase 5 ship.
- **Manual replay button** on `/debug/events` (re-run a stored event through the dispatcher) — high diagnostic value but adds complexity. Defer to a later phase if Phase 5 surfaces a real need for it.
- **Settings UI to change polling interval** — config is configurable but exposed only via DB / config file in Phase 5. Add a settings widget when (or if) the user wants to tune it.

</deferred>

---

*Phase: 5-Plex Event Foundation + Rating Sync*
*Context gathered: 2026-05-08*
