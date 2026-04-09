# Phase 2: Library Sync - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Sync user's full Plex music library to local SQLite database with paginated fetching, delta sync, configurable scheduled sync, background job with progress UI, and a full library browse page. This phase delivers the track data foundation that all downstream features (audio analysis, playlist generation) depend on.

</domain>

<decisions>
## Implementation Decisions

### Sync trigger & scheduling
- **D-01:** Sync can be triggered manually via a "Sync Now" button on the library page
- **D-02:** Sync also runs on a configurable schedule (user sets interval in settings, e.g., every 6/12/24 hours)
- **D-03:** Auto-sync on app startup if Plex is configured and no sync has run yet
- **D-04:** Schedule interval is configurable in the settings page (add to existing Plex config section or a new "Sync" section)

### Progress display
- **D-05:** Sync progress shown as an inline banner at the top of the library page: "Syncing... 4,521 / 10,234 tracks" with a progress bar
- **D-06:** Banner disappears when sync completes, replaced by "Last synced: [timestamp]" and track count
- **D-07:** Progress updates via HTMX polling every 2-3 seconds (matches existing HTMX patterns from Phase 1)

### Library browse view
- **D-08:** Full library browse page at `/library` with a table showing: title, artist, album, genre, year
- **D-09:** Table supports pagination, search (across title/artist/album), and column sorting
- **D-10:** "Library" link added to the top nav bar (separate from home page)
- **D-11:** Sync Now button and last sync status displayed above the table

### Bug fix (carry from deployment)
- **D-12:** Fix Plex library selection bug — user selects "Music" library but a different library is shown in settings config. Investigate and fix the library ID/name mapping in the Plex settings flow.

### Claude's Discretion
- Exact pagination size (25/50/100 per page)
- Search debounce timing
- Track model field types and indexes
- Background job implementation (APScheduler vs asyncio task)
- Delta sync detection strategy (Plex updatedAt field vs full comparison)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above and in:
- `.planning/REQUIREMENTS.md` — SYNC-01 through SYNC-04
- `.planning/research/STACK.md` — PlexAPI library, APScheduler recommendation
- `.planning/research/ARCHITECTURE.md` — Plex Client component, pagination patterns
- `.planning/research/PITFALLS.md` — Plex sync timeout at scale, delta sync via updatedAt

### Existing code (from Phase 1)
- `app/services/plex_client.py` — Existing Plex connection test client (asyncio.to_thread wrapper)
- `app/models/settings.py` — ServiceConfig model and encryption patterns
- `app/services/settings_service.py` — Settings CRUD (decrypt Plex token for API calls)
- `app/database.py` — SQLite engine with WAL mode, session management
- `app/templates/base.html` — Base template with nav bar (add Library link)
- `app/templates/partials/` — HTMX partial patterns established

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `plex_client.py`: Has `test_plex_connection()` — extend with library fetch and track sync methods
- `settings_service.py`: `get_setting()` / `decrypt_setting()` — use to retrieve Plex token for sync
- `database.py`: Engine singleton with WAL mode — add Track model to same database
- HTMX partial patterns: `connection_status.html`, `service_card.html` — reuse pattern for progress banner

### Established Patterns
- `asyncio.to_thread()` wrapping for sync PlexAPI calls (must continue for all Plex operations)
- FastAPI router organization: `api_*.py` for API routes, `pages.py` for HTML pages
- Jinja2 template inheritance from `base.html`
- HTMX `hx-post`/`hx-get` with `hx-target` for partial swaps

### Integration Points
- Nav bar in `base.html` — add Library link
- Settings page — add sync schedule configuration
- `pages.py` — add `/library` route
- New `api_sync.py` router — sync trigger and progress endpoints

</code_context>

<specifics>
## Specific Ideas

- Progress banner should feel like a download progress bar — track count updating smoothly
- The library table is the first "data-heavy" view in the app — it sets the pattern for how Composer displays information going forward
- User has 10k+ tracks, so pagination and efficient queries matter from the start

</specifics>

<deferred>
## Deferred Ideas

- Plex library selection bug (D-12) is a Phase 1 fix that should be addressed early in Phase 2 execution to ensure sync works correctly

</deferred>

---

*Phase: 02-library-sync*
*Context gathered: 2026-04-09*
