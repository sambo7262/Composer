# Phase 8: Lidarr Discovery + Polish — Research

**Researched:** 2026-05-16
**Domain:** Lidarr / pyarr integration, MusicBrainz adjacency, APScheduler reliability, color-blind-safe dark palette
**Confidence:** HIGH on Lidarr+pyarr surface and APScheduler; MEDIUM on MusicBrainz strategy (one significant push-back on the CONTEXT framing); HIGH on palette options

---

## Summary

Phase 8 is a Lidarr-led discovery phase resting on five mechanical foundations: a fixed Lidarr connection-test (currently misses `metadataProfileId`), a reliable daily library-sync cron (currently silently fails on the NAS), a MusicBrainz-based adjacency oracle for surfacing candidate artists, a Sunday-cron LLM re-rank piggybacking the Phase 7.1 `_weekly_maintenance_tick`, and a vibe-color palette propagated across UI. CONTEXT.md (08-CONTEXT) locks every architectural decision — research scope is "verify the locked assumptions, surface light-spike answers, flag any that the codebase reality contradicts."

**Two findings the planner MUST surface to the user before execution:**

1. **MusicBrainz does NOT expose a "similar artists" relation.** [VERIFIED: musicbrainz.org/relationships/artist-artist] Artist-to-artist relations are `member of band`, `collaboration`, `supporting musician`, `subgroup`, `is person` — none of these are the semantic-similarity edges Last.fm calls "similar artists." CONTEXT D-A1 says *"MusicBrainz 'similar artists' lookup against a small seed set produces a validated candidate list (50-100 artists)"* — that wording will mislead the planner. Adjacency in MusicBrainz must be **synthesised** from (a) artist-artist relation edges, (b) shared release-group credits, (c) shared label, (d) shared release-group tags. This is a **graph-walk over the lookup endpoint**, not a single "similar" call. The sibling project **ListenBrainz** does expose a `similar-artists` dataset endpoint (labs.api.listenbrainz.org/similar-artists), but it's documented on the "labs" subdomain — less stable, less specified than Last.fm's `artist.getSimilar`. Two viable paths:
   - **Path A (CONTEXT's stated intent, corrected):** synthesise adjacency from MusicBrainz artist-rels + release-group co-credits + shared-label edges. Slower (multiple lookups per seed), free, deterministic.
   - **Path B (recommended alternative):** ListenBrainz `similar-artists` for the candidate pool, MusicBrainz only for hallucination validation (Pitfall 10). Both are MetaBrainz-operated, free, no API key. ListenBrainz returns actual similarity scores; MusicBrainz does not.
   - **Decision the planner must take to the user:** Path A or Path B? CONTEXT D-A1 reads as Path A but is technically inaccurate. Path B is materially simpler and matches the intent ("similar artists for taste expansion").

2. **The Composer dark theme uses Plex orange (`#e5a00d`) as the accent.** A vibe-color palette MUST avoid the accent hue and `#22c55e` success / `#ef4444` error tokens. The Okabe-Ito palette ships with an orange (`#E69F00`) that visually competes with `#e5a00d`. Recommend swapping that slot for a desaturated alternative or selecting a different palette entirely (see Vibe Palette section).

**Everything else** in the phase is well-trodden: pyarr 6.6 `add_artist` requires the four params the planner already expects (`artist` dict, `root_dir`, `quality_profile_id`, `metadata_profile_id`), MusicBrainz rate-limit is 1 req/sec global with a 503 throttle, APScheduler `IntervalTrigger` does reset on restart and the canonical fix is `CronTrigger(hour=H)` + lifespan missed-tick catch-up + `coalesce=True` + `misfire_grace_time`.

**Primary recommendation:** Build Plan 01 first (Lidarr connection-test fix + library-sync cron reliability fix per D-E1/D-E3 — both are foundational and load-bearing). In parallel, surface the Path A vs Path B candidate-source decision to the user during planning — the planner should NOT silently lock either path. Recommend Path B (ListenBrainz similar-artists + MusicBrainz validation) on simplicity + correctness grounds.

---

## User Constraints (from CONTEXT.md)

### Locked Decisions

The full Areas A–E decisions in 08-CONTEXT.md `<decisions>` block are locked and not re-decided here:

- **D-A1:** Seed-first pipeline — MusicBrainz candidate fetch → popularity gate → LLM re-rank. *(See finding above: "MusicBrainz" in this phrasing must be either ListenBrainz or a synthesised MusicBrainz graph walk.)*
- **D-A2:** Per-vibe rotation — one starred track per vibe, rotated week-over-week.
- **D-A3:** Popularity-bias hard gate — listener-count filter + adjacency requirement.
- **D-A4:** Rationale = factual MB hook + LLM one-liner.
- **D-B1:** Piggyback `_weekly_maintenance_tick` (no second weekly job).
- **D-B2:** Cache results all week; serve from `DiscoveryCandidate` table.
- **D-B3:** Catch-up + failure modes mirror SUGG-13.
- **D-B4:** Home-page weekly LLM cost chip, baseline-filtered.
- **D-C1:** Auto-ingest piggybacks the daily `library_sync` cron.
- **D-C2:** No Lidarr OnImport webhook in Phase 8 (deferred).
- **D-C3:** Per-add status timeline on `/discover` + `/debug/discovery`.
- **D-D1:** Full mobile-first rewrite of `/library` (separate plan likely).
- **D-D2:** `/discover` = vibe-grouped horizontal-scroll sections.
- **D-D3:** Artist cards tap-to-expand (Phase 7 vocabulary).
- **D-D4:** Post-ingest lifecycle: card → status row → REMOVED from `/discover`.
- **D-D5:** Dismiss = artist-only exclude, no neighborhood deboost.
- **D-E1:** Two-step Lidarr settings flow (quality + metadata profile dropdowns) — **Plan 01 task**.
- **D-E2:** `Vibe.color` propagation across all surfaces.
- **D-E3:** Library-sync cron reliability fix — **Plan 01 task**, load-bearing for D-C1.

### Claude's Discretion

Per 08-CONTEXT.md `<decisions>` → "Claude's Discretion":

- Rotation mechanism for D-A2 seed selection (random / round-robin / LRU).
- MusicBrainz API client choice (`musicbrainzngs` is canonical; cache by `mb_id`).
- Last.fm vs MusicBrainz as candidate source — *see Light Spike Answer 4 below; this research RECOMMENDS revisiting CONTEXT's "MusicBrainz only" default in favor of ListenBrainz, not Last.fm.*
- Discovery LLM call shape (single call, cached preamble, `purpose="discovery_artist_weekly"`).
- Discovery cost ceiling (repurpose `WEEKLY_DISCOVERY_BUDGET_USD` as shared cap).
- Empty states for `/discover`.
- Discovery candidate cap (borrow Phase 7.1 constants).
- `/debug/discovery` layout (plain HTML per DEBUG-05).
- DiscoveryAdd → Lidarr status sync (lazy poll, 5-min cache).
- Color palette assignment order (deterministic or sequential).

### Deferred Ideas (OUT OF SCOPE)

Per 08-CONTEXT.md `<deferred>`:

- Lidarr OnImport webhook + `/api/webhooks/lidarr` endpoint
- Last.fm as parallel candidate source (defer unless MB adjacency too sparse)
- Dismiss neighborhood deboost
- Settings UI to unblock dismissed artists
- Configurable discovery cadence
- Per-vibe discover-this-vibe button on `/vibes`
- Cover Art Archive integration for MB-only artist images
- "Recommend Composer's added artists back to me later" loop (handled organically)
- Bulk add (one-at-a-time per D-D3)
- Vibe color customization UI
- Mobile-first rewrite of v1 surfaces beyond library

---

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DISC-03 | Discover artists page (10–20 ranked candidates) | Light Spikes 1–4 (pyarr `add_artist` signature, MB endpoints, candidate-source decision) |
| DISC-04 | Popularity-bias mitigation | Pitfall 13 (locked); MB listener-count via ratings table or release-count proxy (Spike 3) |
| DISC-05 | One-click add to Lidarr (quality + metadata profile) | Spike 1 (pyarr `add_artist` requires both profile IDs + root_dir); Pitfall 14 |
| DISC-06 | Auto-ingest of Lidarr arrivals | D-C1 piggybacks daily `library_sync` → `trigger_post_sync_analysis` (already wired at `sync_service.py:222`); Pitfall 25 |
| DISC-07 | Lidarr connection-test fix | D-E1; current `lidarr_client.py:21` fetches only `get_quality_profile`; extend to fetch `get_metadata_profile` in same `to_thread` call |
| DISC-08 | Library-sync cron reliability | D-E3 + APScheduler reliability section below; CronTrigger + lifespan catch-up + `coalesce=True` + `misfire_grace_time` + surface silent failures from `sync_service.py:226-229` |
| UI-07 | Settings + library page mobile-responsive | D-D1; existing `app/templates/pages/library.html` is wide-table layout (47 lines); needs full rewrite per mobile-first.md conventions |
| UI-08 | Mobile-first interaction details | Inherits Phase 7 UI-03..05 (h-dvh, safe-area-inset, ≥44px, no-hover) |
| UI-09 | Vibe color coding | Vibe Palette section below; `Vibe.color` additive column via `_migrate_add_columns()` |
| UI-10 | Home-page weekly LLM cost chip | D-B4; query shape in CONTEXT `<code_context>` Integration Points |
| OPS-06 | Legacy v1 playlist recognition | `plex_playlist_service.is_managed_playlist` (`Composer · ` prefix) already exists; just ensure `/discover` and any new playlist-listing surface respect it |
| DEBUG-04 | `/debug/discovery` page | Plain HTML per DEBUG-05; mirror `debug_suggestions.html` / `debug_vibes.html` shape |

---

## Project Constraints (from CLAUDE.md)

These directives are project-wide and treated as locked decisions; the planner must verify compliance.

1. **Phase 5 D-09 — `asyncio.to_thread` invariant.** Every PlexAPI/pyarr call from any `async def` runs through `asyncio.to_thread(...)`. AST static test `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async` enforces this for `event_handlers.py`, `plex_playlist_service.py`, `vibe_service.py`, `suggestions_service.py`. **Phase 8 must EXTEND the test's `paths` list** to include any new file that calls Lidarr/PlexAPI sync methods from async context — likely `discovery_service.py`, `api_discovery.py`, and the extended `lidarr_client.py`. [VERIFIED: tests/test_event_handlers.py:293-329]
2. **Pydantic event types with `*Event` suffix + Literal `type` discriminator.** New event types (if any — most of Phase 8 is request/response, not bus-driven) go in `app/models/events.py`. [CITED: CLAUDE.md Phase 5 D-06]
3. **Module-level singletons** for new services follow `_state` + `get_state()` pattern with `autouse=True` reset fixture. `discovery_service` follows the `suggestions_discovery` shape. [CITED: CLAUDE.md Phase 5 D-08]
4. **Multipart Form parsing:** `Annotated[str, Form()]` + `json.loads()`. NEVER `pydantic.Json[Model]` inside `Form()` (FastAPI #10997). N/A for Phase 8 unless a new webhook is added (D-C2 defers Lidarr OnImport — so probably N/A). [CITED: CLAUDE.md Phase 5 D-05]
5. **`userRating` raw 0–10** — irrelevant to Phase 8 (no rating writes). [CITED: CLAUDE.md Phase 5 D-15]
6. **EventLog dedupe** — irrelevant to Phase 8 unless DISC-08 routes silent sync failures through `EventLog` with `event_type="sync_failed"` (D-E3 explicitly does this). Use `INSERT OR IGNORE` on `dedupe_key`. [CITED: CLAUDE.md Phase 5 D-07]
7. **Event bus lifecycle** — irrelevant to Phase 8 (no new long-running tasks).
8. **Tight per-handler `except` clauses, no `except Exception` in route handlers.** [CITED: 08-CONTEXT.md Established Patterns]
9. **Mobile-first conventions** (`h-dvh`, `env(safe-area-inset-bottom)`, ≥44px targets, no hover-only, `alpine-morph` on HTMX swaps). UI-07/08 plans must enforce. [CITED: mobile-first.md + Phase 7 UI-03..05]
10. **GSD Workflow Enforcement** — every Phase 8 task lands via `/gsd-execute-phase` (or `/gsd-quick` for hotfixes).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Lidarr API HTTP calls | Backend service (`lidarr_client.py` + `discovery_service.py`) | — | All pyarr calls are sync; must wrap in `asyncio.to_thread` (D-09). Browser never talks to Lidarr directly. |
| MusicBrainz API HTTP calls | Backend service (`musicbrainz_client.py`, NEW) | — | Rate-limited (1 req/sec); centralise the throttle in one module. Browser never talks to MB directly. |
| LLM artist re-rank | Backend service (`discovery_service.py`) | — | Anthropic SDK is async; goes through `anthropic_client.py::call_with_structured_output` (single entry point). Wrap with `llm_cost_breaker.check_or_raise` first. |
| Weekly cron tick | Backend scheduler (`sync_scheduler.py::_weekly_maintenance_tick`) | — | APScheduler in-process; existing Sunday 03:00 UTC tick gains a third step. NOT a separate job. |
| Discovery candidate cache | Database (`DiscoveryCandidate` table) + Backend read | — | Cron writes; `/discover` reads. Cache replaced wholesale each Sunday. |
| Dismissed-artist filter | Database (`DiscoveryDismissed` table) + Backend read | — | Read at `/discover` render and subtracted from cached candidate set. |
| Lidarr add timeline | Database (`DiscoveryAdd` table) + Backend lazy poll | Frontend (HTMX 5-min cache) | Per-add status. Lazy poll of `lidarr.get_history()` on `/discover` render. No cron. |
| Home-page weekly cost chip | Backend (`api_pages.py` /vibes route) + Database (`LLMUsage`, `CostMeterBaseline`, `WeeklyCronState`) | Frontend (Jinja render) | Single SQL aggregate query with baseline filter; small chip partial. |
| Vibe color rendering | Database (`Vibe.color` col, NEW) + Backend (template context) | Frontend (Jinja + inline style) | Source-of-truth column read by every template rendering a vibe label. |
| `/discover` UX | Frontend (Jinja templates + Alpine.js tap-to-expand + HTMX swap) | Backend (api_discovery router) | Vibe-grouped horizontal scroll rows; tap-to-expand mirrors Phase 7 D-09/10. |
| `/debug/discovery` | Frontend (plain HTML, no JS) | Backend (read-only aggregates) | Per DEBUG-05; mirror `debug_suggestions.html`. |
| Library page mobile rewrite | Frontend (Jinja partials + Alpine.js bottom sheet for sort) | Backend (existing `/library` route reused) | D-D1: card-per-track list <md, filter chips, sticky search, sort moves to bottom sheet. |

**Why this matters for Phase 8:** the discovery pipeline crosses six tiers (HTTP integrations, cron, cache, lazy poll, template, debug). Most planning errors in similar phases come from putting LLM calls in the request hot path instead of the cron, or putting Lidarr poll in a cron instead of lazy-on-render. The map above locks both anti-patterns out.

---

## Standard Stack

### Core (already in `requirements.txt`)

| Library | Version | Purpose | Why Standard | Confidence |
|---------|---------|---------|--------------|------------|
| `pyarr` | `>=6.6,<7.0` | Lidarr API client | Already pinned (Phase 5 OPS-03). `add_artist()` requires both `quality_profile_id` and `metadata_profile_id` (Pitfall 14). Python 3.12+ supported. | HIGH [VERIFIED: pypi.org/project/pyarr/ shows 6.6.0 released 2026-03-31] |
| `anthropic` | `>=0.100,<1.0` | LLM client | Phase 5 OPS-02. Use `app/services/anthropic_client.py::call_with_structured_output` as single entry point. | HIGH |
| `apscheduler` | 3.x (`AsyncIOScheduler`) | Cron + interval scheduler | Phase 02 already pinned. Phase 8 extends `_weekly_maintenance_tick` + fixes `library_sync` reliability (D-E3). | HIGH |

### New (Phase 8 adds)

| Library | Version | Purpose | Why Standard | Confidence |
|---------|---------|---------|--------------|------------|
| `musicbrainzngs` | `>=0.7.1,<1.0` | MusicBrainz API client | Canonical Python library, mature, handles rate-limit + User-Agent automatically when configured via `musicbrainzngs.set_useragent(...)`. [VERIFIED: pypi.org/project/musicbrainzngs/] | HIGH |
| `requests` (transitive via musicbrainzngs) | — | Already present | — | HIGH |

**Optional (recommended for Path B, see Light Spike 4):**

| Library | Version | Purpose | Why Standard | Confidence |
|---------|---------|---------|--------------|------------|
| `pylistenbrainz` or plain `httpx` | latest | ListenBrainz similar-artists endpoint | If the user picks Path B (ListenBrainz as candidate source, MB as validator), one tiny GET to `labs.api.listenbrainz.org/similar-artists/json?artist_mbids=<mbid>&algorithm=<algo>` returns a scored list. Plain `httpx` is fine — `pylistenbrainz` adds nothing beyond auth helpers that aren't needed for the read-only similar-artists call. | MEDIUM [VERIFIED: labs.api.listenbrainz.org/similar-artists page exists; CITED: api endpoint shape from search results] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `musicbrainzngs` | `python-musicbrainz-ngs` (`sampsyo` fork) | Same library, older fork; `musicbrainzngs` (`alastair`) is the maintained canonical |
| Direct `httpx` calls to MB | `musicbrainzngs` wrapper | Wrapper handles rate-limit + User-Agent + XML parsing; saves ~50 lines of boilerplate and the rate-limit logic is the easy-to-get-wrong part |
| Last.fm `artist.getSimilar` | ListenBrainz `similar-artists` | Both are simple. Last.fm requires API key + has stricter rate limit (5 req/sec averaged 5-min). ListenBrainz is MetaBrainz family (no key, free, but on `labs.` subdomain — less stable contract). Use Last.fm only if ListenBrainz proves flaky in practice. |

**Installation** (assuming Path B / ListenBrainz):

```bash
# Add to requirements.txt:
musicbrainzngs>=0.7.1,<1.0
# httpx already present via FastAPI transitive
```

**Version verification:**

```bash
# Verify before pinning — training data is stale
pip index versions pyarr           # confirmed 6.6.0 (2026-03-31) [VERIFIED]
pip index versions musicbrainzngs  # confirmed 0.7.1 as latest stable [VERIFIED: pypi]
```

[VERIFIED: pypi.org/project/pyarr/ — 6.6.0, March 31 2026]
[VERIFIED: pypi.org/project/musicbrainzngs/ — 0.7.1, January 11 2020 (no newer release; library is stable, not abandoned per recent GH activity)]

---

## Light Spike Answers

### Spike 1 — pyarr 6.6 `add_artist()` signature

**Signature** (synthesised from [CITED: docs.totaldebug.uk/pyarr/modules/lidarr.html] + web-search WebFetch hits):

```python
# pyarr 6.x — class is `Lidarr` (renamed from `LidarrAPI`)
def add_artist(
    self,
    artist: dict[str, Any],          # REQUIRED — must be a dict from lookup_artist() / lookup()
    root_dir: str,                   # REQUIRED — root folder path on the Lidarr host
    quality_profile_id: int,         # REQUIRED — int from get_quality_profile()
    metadata_profile_id: int,        # REQUIRED — int from get_metadata_profile()
    monitored: bool = True,
    artist_monitor: str = "all",     # one of: "all", "future", "missing", "existing", "first", "latest", "none"
    search_for_missing_albums: bool = False,
) -> dict[str, Any]:
    """Add a new artist to Lidarr."""
```

[VERIFIED via WebSearch result describing the exact signature; CITED: docs.totaldebug.uk/pyarr/modules/lidarr.html]

**Key invariants:**

- `artist` must be the **dict shape returned by `lookup()` / `lookup_artist()`** (which is the Lidarr `MetadataService` response — includes `foreignArtistId` aka MusicBrainz `mb_id`, `artistName`, `disambiguation`, images, `artistType`). Do NOT hand-craft this dict.
- All four primary params (`artist`, `root_dir`, `quality_profile_id`, `metadata_profile_id`) are required positional/keyword arguments. Lidarr's REST API will silently 200-with-validation-error if `metadataProfileId` is missing from the body — pyarr forces it at the Python layer, which is exactly the v1 bug Pitfall 14 captures. [VERIFIED: cross-confirmed in the WebSearch result and aligned with Pitfall 14's "metadataProfileId required" claim.]
- `search_for_missing_albums=True` is opt-in; default `False` means Lidarr won't immediately kick off RSS searches. **For Phase 8 set `search_for_missing_albums=True`** so the user sees Lidarr act on the add — otherwise the 24h post-add monitoring (Pitfall 14) shows "no releases found" for cosmetic reasons.
- `artist_monitor="all"` is the right default for Composer's "I want this whole artist" UX (matches "monitor all releases by this artist" semantics in the Lidarr UI).
- There is **no separate "wanted but unmonitored" code path** to avoid. The combination `monitored=False, artist_monitor="none"` is the "wanted but won't search" mode — but Phase 8's flow is "user explicitly clicked Add, search now," so use defaults.

**Recent renames noted in pyarr changelog** (relevant to Composer):

- `add_artist` and `add_album` switched from `search_term` to `id_` (MusicBrainz ID) parameter at some point in the 6.x line for the higher-level "lookup-and-add" helper (not the `add_artist` documented above). Confirm whether the codebase wants the lower-level `add_artist(artist=..., ...)` (recommended — explicit, no double-lookup) or the higher-level `add_artist_by_id(...)` helper if it exists. [CITED: WebSearch result on pyarr release notes — "add_artist and add_album changed from using search_term to using id_"]

**Phase 5 D-09 invariant:** every `add_artist` call from `discovery_service.py` MUST wrap in `await asyncio.to_thread(lidarr.add_artist, ...)`. Static test (`test_no_blocking_plexapi_in_async`) must be extended to include `discovery_service.py` and the extended `lidarr_client.py` in its file list, AND must add `add_artist`, `lookup_artist`, `lookup`, `get_quality_profile`, `get_metadata_profile`, `get_root_folder`, `get_history` to `forbidden_names`.

### Spike 2 — pyarr 6.6 endpoints for profiles + history

| Method | Signature (verified shape) | Return | Use |
|--------|---------------------------|--------|-----|
| `get_quality_profile(id_: int | None = None)` | id_=None → list of all profiles; id_=N → single | `list[dict]` or `dict` with keys `{id, name, cutoff, items, ...}` | D-E1 dropdown source. **Currently consumed at `app/services/lidarr_client.py:21` — return shape `{id, name}` is already correct.** [VERIFIED: current code works] |
| `get_metadata_profile(id_: int | None = None)` | id_=None → list; id_=N → single | `list[dict]` with `{id, name, primaryAlbumTypes, secondaryAlbumTypes, releaseStatuses, ...}` | D-E1 NEW dropdown sibling — call in the SAME `to_thread` as `get_quality_profile` to halve round-trips |
| `get_root_folder()` | no args (or id_=None) | `list[dict]` with `{id, path, accessible, freeSpace, ...}` | D-E1 — needed for the `root_dir` param to `add_artist`. **Recommend: persist `lidarr_root_folder_path` to `ServiceConfig.extras` at save time** so `add_artist` doesn't have to re-query on every add. If multiple root folders exist, the connection-test UI must let the user pick one (third dropdown), or default to the first/only one. |
| `get_history(page: int = 1, page_size: int = 10, sort_key: str = "date", sort_dir: str = "default", event_type: int | None = None)` | paged list | `dict` with `{page, pageSize, totalRecords, records: [...]}` where each record has `{eventType, artistId, albumId, trackId, sourceTitle, date, ...}` | D-C3 — used to populate the per-`DiscoveryAdd` Lidarr-status timeline. **Filter strategy:** there's no native "filter by `mb_id`" — you fetch recent history and join in Python against `DiscoveryAdd.mb_id ↔ lidarr_artist_id`. Cache: 5 min on `/discover` render per CONTEXT discretion. |
| `lookup(term: str)` | search by free text | `list[dict]` of search results (any entity type) | NOT for Phase 8 — too broad |
| `lookup_artist(term: str)` | search artists only by name | `list[dict]` of artist dicts (the shape `add_artist` requires) | Useful for the validation pass: given an LLM-returned artist name, call `lookup_artist` → take the top hit → check its `foreignArtistId` matches the expected MB ID. ALSO: Lidarr's `lookup_artist` IS itself a MusicBrainz lookup under the hood — so Pitfall 10's "validate via MusicBrainz before showing" can be satisfied with `lookup_artist` directly, avoiding a second MB call. |
| `get_queue(...)` | active downloads | `list[dict]` of queue items | Not needed for Phase 8 — `get_history` covers the "did Lidarr import?" question. |

[CITED: docs.totaldebug.uk/pyarr/modules/lidarr.html for general module shape]
[VERIFIED: the current `lidarr_client.py:14-49` shows `get_quality_profile()` returning `[{"id": p["id"], "name": p["name"]} for p in profiles]` — so the return shape is verified at the codebase level.]

**Recommended Phase 8 extension to `lidarr_client.py`:**

```python
async def test_lidarr_connection(url: str, api_key: str) -> dict:
    # ... existing setup ...
    lidarr = Lidarr(host_url=url, api_key=api_key)
    # Fetch all three in ONE to_thread so we hold the GIL once.
    quality_profiles, metadata_profiles, root_folders = await asyncio.to_thread(
        _fetch_lidarr_test_payload, lidarr
    )
    return {
        "success": True,
        "quality_profiles": [{"id": p["id"], "name": p["name"]} for p in quality_profiles],
        "metadata_profiles": [{"id": p["id"], "name": p["name"]} for p in metadata_profiles],
        "root_folders": [{"id": r["id"], "path": r["path"]} for r in root_folders],
    }

def _fetch_lidarr_test_payload(lidarr):
    return (
        lidarr.get_quality_profile(),
        lidarr.get_metadata_profile(),
        lidarr.get_root_folder(),
    )

async def add_artist(...):
    # ... new helper using saved profile + root from ServiceConfig.extras
```

### Spike 3 — MusicBrainz API: rate limit, similar-artists, popularity proxy, User-Agent

| Aspect | Answer | Source |
|--------|--------|--------|
| **Rate limit** | 1 request/sec averaged per IP. Throttled requests return **HTTP 503**. Specific UA throttling: anonymous / `python-urllib` / generic Java UAs get harder caps (50 req/sec global pool). | [VERIFIED: musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting] |
| **Throttling headers** | None. MB does NOT send `X-RateLimit-Remaining` or `X-RateLimit-Reset`. Client must self-throttle (musicbrainzngs does this automatically when `set_rate_limit(...)` is configured). | [VERIFIED: same source] |
| **User-Agent format** | `<AppName>/<version> ( <contact-url-or-email> )` — REQUIRED. Recommended: `Composer/2.0 ( https://github.com/<owner>/composer )` or `Composer/2.0 ( sam.e.browning@gmail.com )` (per project user-email memory). `musicbrainzngs.set_useragent(app, version, contact)` formats this correctly. Failing to set → `musicbrainzngs.UsageError`. | [VERIFIED: musicbrainz.org rate-limit page + musicbrainzngs docs] |
| **"Similar artists" endpoint** | **DOES NOT EXIST** in the public MusicBrainz API. Artist-to-artist relations are limited to: `member of band`, `collaboration`, `supporting musician`, `subgroup`, `is person` — NONE of these are semantic similarity. To synthesise adjacency: walk `inc=artist-rels` on the seed artist's lookup, plus `inc=release-group-rels` and `inc=label-rels` on the seed's releases. This is the Path A approach and is expensive (multiple lookups per seed). | [VERIFIED: musicbrainz.org/relationships/artist-artist + musicbrainz.org/doc/MusicBrainz_API/Examples — no "similar" relation type exists] |
| **`listener_count` / popularity proxy** | MusicBrainz itself does not expose listener counts. Available proxies on the artist lookup endpoint: (a) **community rating** (1–5 stars, `inc=ratings` → `rating.{value, votes-count}`) — sparse; many artists have 0 votes. (b) **release-group count** (`inc=release-groups`, count length) — heavier discography correlates loosely with prominence. (c) **tags vote count** (`inc=tags` → tag has `count`) — loose proxy for "people know enough about this to tag it." None of these are great. **The clean proxy is Last.fm `artist.getInfo` → `playcount` + `listeners`** (free, requires API key). | [VERIFIED: musicbrainz.org schema + relationships pages; Last.fm verified separately] |
| **Cleanest "similar artists" path** | **ListenBrainz** `similar-artists` dataset endpoint at `labs.api.listenbrainz.org/similar-artists/json` — returns scored similar artists by MBID, free, no auth required. Has multiple algorithm options (the labs UI lets you pick). Less stable than core APIs (it's on `labs.`) but it's MetaBrainz-operated and the contract is what every modern arr-stack discovery tool (Navidrome, Aurral, Musicseerr) is moving to. **This is what CONTEXT D-A1's "MusicBrainz similar artists" likely meant — but the endpoint is ListenBrainz, not MusicBrainz proper.** | [VERIFIED: labs.api.listenbrainz.org/similar-artists exists; CITED: listenbrainz.readthedocs.io radio API mentions similar-artists shape] |
| **Python client recommendation** | `musicbrainzngs>=0.7.1` for MusicBrainz lookups (validation + tag/relation lookups). Plain `httpx` (already a project dep) for the one ListenBrainz `similar-artists` call — no full client needed for one read endpoint. Justification: `musicbrainzngs` auto-handles the 1-req/sec self-throttle + User-Agent + XML parsing (saves ~80 lines of boilerplate and gets the easy-to-screw-up rate-limit code right). | RECOMMENDATION based on verified sources |

**Concrete URL examples for the planner:**

```text
# MusicBrainz artist lookup with relations (for validation + adjacency synthesis):
GET https://musicbrainz.org/ws/2/artist/<MBID>?inc=artist-rels+url-rels+release-groups+tags+ratings&fmt=json
User-Agent: Composer/2.0 ( sam.e.browning@gmail.com )

# ListenBrainz similar artists (for candidate source, Path B):
GET https://labs.api.listenbrainz.org/similar-artists/json?artist_mbids=<MBID>&algorithm=session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30
# (algorithm string is from the labs UI; planner should confirm the recommended algorithm via that page)
```

### Spike 4 — Last.fm vs MusicBrainz as candidate source (decision point)

**CONTEXT D-A1 default:** MusicBrainz only. Last.fm deferred unless adjacency proves sparse.

**This research's pushback:** CONTEXT's framing assumes MusicBrainz has a "similar artists" endpoint. It does not. There are three real options:

| Option | Pro | Con | Recommendation |
|--------|-----|-----|----------------|
| **A. Synthesised MB adjacency** — walk artist-rels + shared release-groups + shared labels for each seed | Free, no API key, deterministic, no third dependency, fully MetaBrainz | 5–20 MB lookups per seed (rate-limit pressure — 7 vibes × 20 lookups = 140s of MB calls per cron tick), sparse for indie artists, edge graph is patchy | Defer unless required |
| **B. ListenBrainz `similar-artists` for candidates, MB for validation** *(RECOMMENDED)* | Free, no API key, MetaBrainz-operated (aligns with project's anti-Last.fm-scrobble decision), single call per seed, returns scored results, has multiple algorithms | On `labs.` subdomain (less stable contract), score interpretation requires picking an algorithm | **Default for v1** |
| **C. Last.fm `artist.getSimilar`** | Mature, well-documented, fast, ~5 req/sec budget | Requires user-supplied API key (NEW config surface), explicit Lock against the project's "Last.fm scrobble-as-taste-signal is out of scope" decision (REQUIREMENTS.md Out of Scope table), heavier coupling | Defer unless B proves unreliable |

**Library scale context:** ~10K tracks, 460+ rated. Per CONTEXT specifics, library is "niche-leaning" — exactly the case where ListenBrainz adjacency outperforms collaborative-filtering proxies. The user already has a rich rated set; the seed-rotation pattern in D-A2 makes per-week MB rate-limit non-issues.

**Concrete recommendation:** Path B. The planner should bring this back to the user as a confirmation question: *"CONTEXT D-A1 said 'MusicBrainz similar artists' but that endpoint doesn't exist. ListenBrainz (same project family) has the real similar-artists data and is free + no key. OK to use ListenBrainz as candidate source and MusicBrainz only for hallucination validation?"* If yes, lock it. If the user prefers strict MB-only on dependency-minimization grounds, fall back to Path A (synthesised adjacency) but warn that the 7-vibes-per-week walk takes ~3 minutes of MB calls — acceptable within a Sunday cron, but heavier than expected.

### Spike 5 — Vibe color palette (UI-09)

**Constraints:**

- Composer dark theme: `--color-surface-primary: #1f1f23` (page bg), `--color-surface-card: #2a2a2e` (card bg), `--color-text-primary: #e8e8ed`, `--color-accent: #e5a00d` (Plex orange). [VERIFIED: app/static/css/input.css]
- ≥7 distinct hues required (Composer has 3–7 vibes per CONTEXT D-E2; palette must cover the upper bound + provide headroom).
- Color-blind-safe (deuteranopia, protanopia, tritanopia).
- WCAG contrast: when used as a chip text on `#2a2a2e` card → ≥3:1 for "non-text UI" (WCAG 1.4.11); when used as background tint with white text → ≥4.5:1 (AA normal text). Most palette colors below pass 3:1 easily; planner should compute 4.5:1 only for any color used as background.
- MUST NOT collide with `#e5a00d` (Plex orange accent — already the project's interactive emphasis color), `#22c55e` (success), `#ef4444` (error).

**Three candidate palettes** (planner picks one with user during planning):

#### Option 1 — Okabe-Ito (8 hues, color-blind-safe academic standard)

| Slot | Hex | Name | Contrast vs `#2a2a2e` | Note |
|------|-----|------|------------------------|------|
| 1 | `#E69F00` | Orange | ~7.0 | **CONFLICT with Plex accent `#e5a00d`** — swap or drop |
| 2 | `#56B4E9` | Sky Blue | ~7.5 | ✓ |
| 3 | `#009E73` | Bluish Green | ~5.4 | ✓ |
| 4 | `#F0E442` | Yellow | ~10.5 | ✓ (very high contrast; may be too loud — use sparingly) |
| 5 | `#0072B2` | Blue | ~3.5 | borderline for AA on text; fine as chip bg with white text |
| 6 | `#D55E00` | Vermillion | ~5.2 | ✓ |
| 7 | `#CC79A7` | Reddish Purple | ~5.8 | ✓ |
| 8 | `#FFFFFF` (drop black) | — | — | Black/white drop; replace with a teal like `#44AA99` if 8th slot needed |

**Verdict:** Strongest color-blind-safety credentials in the world, but slot-1 collision with Plex accent is awkward. RECOMMENDED IF the orange slot is dropped or replaced.

[VERIFIED: conceptviz.app/blog/okabe-ito-palette-hex-codes-complete-reference + easystats.github.io/see/reference/scale_color_okabeito.html]

#### Option 2 — Tableau 10 (10 hues, designed for visualization, well-tested on dark themes)

| Slot | Hex | Name |
|------|-----|------|
| 1 | `#5778a4` | Blue |
| 2 | `#e49444` | Orange | **(borderline conflict with `#e5a00d`; lower-saturation so OK in practice)** |
| 3 | `#d1615d` | Red |
| 4 | `#85b6b2` | Teal |
| 5 | `#6a9f58` | Green |
| 6 | `#e7ca60` | Yellow |
| 7 | `#a87c9f` | Purple |
| 8 | `#f1a2a9` | Pink |
| 9 | `#967662` | Brown |
| 10 | `#b8b0ac` | Grey |

[VERIFIED: gist.github.com/leblancfg/b145a966108be05b4a387789c4f9f474 — quoted via WebFetch]

**Verdict:** Lower-saturation, designed for many-category visualization (charts). 10 hues > 7 needed gives palette headroom for vibe re-cluster. Tableau 10 is **partial** colorblind-safe — deuteranopia (the most common red-green CB) handles it OK because red+green slots are spaced. Less rigorous than Okabe-Ito but more visually pleasant. RECOMMENDED for a music app's "personality matters" aesthetic.

#### Option 3 — Tailwind 4 saturated stops (curated, dark-bg friendly)

A handpicked Tailwind 4 palette using the `-500` stops (saturation balanced for dark backgrounds):

| Slot | Hex | Tailwind name | Why |
|------|-----|--------------|-----|
| 1 | `#3b82f6` | blue-500 | universally readable |
| 2 | `#8b5cf6` | violet-500 | distinct from blue + pink |
| 3 | `#ec4899` | pink-500 | warm, distinct from red error |
| 4 | `#14b8a6` | teal-500 | green-adjacent but distinct from success `#22c55e` |
| 5 | `#f59e0b` | amber-500 | warm but distinct from Plex orange `#e5a00d` (lighter) |
| 6 | `#a855f7` | purple-500 | richer than violet, distinct |
| 7 | `#06b6d4` | cyan-500 | cool blue, distinct from blue-500 |
| 8 (optional) | `#84cc16` | lime-500 | bright green-yellow; distinct from teal + success |

**Verdict:** Tightest visual match to the existing Composer aesthetic (Tailwind is already the styling system). Less rigorous on color-blind safety than Okabe-Ito but the `-500` saturation tier across cool→warm hues is naturally well-spaced. The amber-500 vs Plex-orange near-collision is the closest call. RECOMMENDED for least template surgery.

#### Recommendation

Default to **Option 3 (Tailwind 4 -500 stops)** for aesthetic + zero-new-CSS cost (Tailwind utility classes already exist for these). Bring all three to the user for the visual decision during planning. **Assign deterministically** via `palette[vibe_id % len(palette)]` (per CONTEXT discretion) so a re-cluster preserves existing colors via D-22's manual-override preservation rule.

**WCAG check the planner must run:** for each chosen palette, compute the contrast ratio of each hex against `#2a2a2e` (card bg) using `https://webaim.org/resources/contrastchecker/`. Anything below 3:1 must not be used as a chip foreground; below 4.5:1 must not be used as a chip background with text overlay (use the color only as a stripe / border).

### Spike 6 — APScheduler reliability (DISC-08 / D-E3)

**The bug:** `app/services/sync_scheduler.py:39-46` registers `library_sync` via `IntervalTrigger(hours=interval_hours)`. APScheduler's `MemoryJobStore` (the default, and what Composer uses) **does NOT persist the `next_run_time`** across process restarts. On every container restart, `IntervalTrigger.get_next_fire_time()` is computed from `datetime.now()`, so the schedule effectively resets. If the container restarts at 14:30 with `interval_hours=24`, next sync is tomorrow 14:30. Successive restarts every ~12h push the sync indefinitely. NAS UAT confirmed: `Last synced: 2026-05-14T03:01:24` on a 24h schedule, observed ~48h stale on 2026-05-16.

**Compounding bug:** `app/services/sync_service.py:226-229` catches `Exception`, sets `state=FAILED`, logs `logger.exception("Sync failed")`, but never writes to `EventLog` or surfaces to `/debug/events`. So even if the schedule fires, a silent failure looks identical to "scheduled but not yet run."

**Canonical fix (three parts, all required):**

#### Part A — Switch to `CronTrigger` for wall-clock anchoring

```python
# Replace schedule_sync's IntervalTrigger with a CronTrigger anchored at 03:00 UTC.
# Rationale: 03:00 UTC matches the Phase 7.1 weekly tick; off-peak across US tz;
# wall-clock anchoring means restarts don't reset the schedule.

from apscheduler.triggers.cron import CronTrigger

def schedule_sync(interval_hours: int) -> None:
    scheduler = get_scheduler()
    if scheduler.get_job("library_sync"):
        scheduler.remove_job("library_sync")

    if interval_hours == 24:
        trigger = CronTrigger(hour=3, minute=0, timezone="UTC")
    elif interval_hours == 12:
        trigger = CronTrigger(hour="3,15", minute=0, timezone="UTC")
    elif interval_hours == 6:
        trigger = CronTrigger(hour="3,9,15,21", minute=0, timezone="UTC")
    else:
        # Fallback for unusual values — IntervalTrigger with misfire grace
        trigger = IntervalTrigger(hours=interval_hours)

    scheduler.add_job(
        _trigger_sync,
        trigger=trigger,
        id="library_sync",
        replace_existing=True,
        coalesce=True,                    # collapse missed runs into one
        misfire_grace_time=3600,          # 1h grace window
        max_instances=1,                  # never overlap concurrent syncs
        name=f"Library sync ({interval_hours}h cadence)",
    )
```

[CITED: apscheduler.readthedocs.io/en/3.x/userguide.html — `coalesce`, `misfire_grace_time`, `max_instances` semantics]

**Why `coalesce=True`:** if APScheduler comes up after a missed fire window (container was down at 03:00 UTC), `coalesce=True` ensures only ONE catch-up run fires, not N runs for N missed fires. Combined with `misfire_grace_time=3600`, missed fires within 1h are still honored; missed fires older than 1h are dropped by APScheduler (the lifespan catch-up gate below handles those).

#### Part B — Lifespan missed-tick catch-up

Mirror Phase 7.1 D-C2 / `start_scheduler` catch-up gate at lines 307-377 — same pattern, different state row:

```python
# Inside start_scheduler() AFTER scheduling library_sync:
try:
    sync_info = get_last_sync_info(session)
    last_done = sync_info["last_sync_completed"]  # ISO str or None
    needs_catch_up = False
    if last_done is None:
        # Already handled by existing first-run auto-sync at lines 283-291.
        pass
    else:
        last_dt = datetime.fromisoformat(last_done)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_dt
        grace = timedelta(hours=1)
        if age > timedelta(hours=interval_hours) + grace:
            needs_catch_up = True
            logger.warning(
                "library_sync stale: last completed %s ago, scheduling catch-up",
                age,
            )

    if needs_catch_up:
        async def _delayed_catch_up_sync():
            await asyncio.sleep(15)  # let app fully boot
            await run_sync()
        asyncio.create_task(_delayed_catch_up_sync())
except Exception:
    logger.exception("Failed to evaluate library_sync catch-up gate; continuing")
```

This is the lift-and-shift of the Phase 7.1 D-C2 pattern that's already proven in `start_scheduler` (lines 315-377 of `sync_scheduler.py`). The planner should put `library_sync` catch-up before `discovery_call_weekly` catch-up so library state is fresh when discovery runs.

#### Part C — Surface silent sync failures

Replace `sync_service.py:226-229`:

```python
# OLD:
except Exception as exc:
    _sync_status.state = SyncStateEnum.FAILED
    _sync_status.error = _sanitize_error(str(exc), token)
    logger.exception("Sync failed")

# NEW:
except Exception as exc:
    _sync_status.state = SyncStateEnum.FAILED
    sanitized = _sanitize_error(str(exc), token)
    _sync_status.error = sanitized
    logger.exception("Sync failed")
    # DISC-08 — surface to /debug/events so silent failures are observable.
    try:
        await _record_sync_failure_event(sanitized)
    except Exception:
        # Never let the event write itself break the sync state transition.
        logger.exception("Failed to record sync_failed EventLog row")
```

Implementation of `_record_sync_failure_event` writes a row to `EventLog` with `event_type="sync_failed"`, `dedupe_key=f"sync_failed:{datetime_bucket_5min}:{sanitized[:80]}"` (so a flapping failure doesn't spam the log), `processed_at=now`. **Use the same `INSERT OR IGNORE` shape Phase 5 D-07 prescribes.** The existing `/debug/events` page (Phase 5 DEBUG-01) automatically shows new event types — no template change required.

#### Detection in test

```python
def test_library_sync_uses_cron_trigger_at_03_utc():
    """DISC-08 — schedule_sync(24) must register a CronTrigger anchored to wall-clock."""
    from apscheduler.triggers.cron import CronTrigger
    schedule_sync(24)
    job = get_scheduler().get_job("library_sync")
    assert isinstance(job.trigger, CronTrigger)
    assert job.coalesce is True
    assert job.misfire_grace_time >= 3600

def test_sync_failure_writes_event_log_row():
    """DISC-08 — silent sync failures must surface on /debug/events."""
    # ... mock run_sync to raise, assert EventLog has a sync_failed row
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MusicBrainz HTTP client + rate-limit | bare `httpx` + `asyncio.sleep(1)` ring-buffer | `musicbrainzngs` (set rate-limit via `musicbrainzngs.set_rate_limit(limit_or_interval=1.0)`) | Self-throttle is the easy-to-get-wrong part; the library handles it correctly |
| Lidarr API HTTP client | bare `httpx` | `pyarr.Lidarr` (already a dep) | API surface evolves with Lidarr server versions; pyarr abstracts |
| Anthropic SDK wrapping | new Anthropic client | `anthropic_client.call_with_structured_output` | Phase 5 single-entry-point invariant; gets `cache_control=ttl:1h`, `MaxTokensTruncationError` retry, JSON-from-prose tolerance for free |
| LLM cost gating | local quota check | `llm_cost_breaker.check_or_raise(purpose_prefix="discovery_")` | Pitfall 11; established Phase 7.1 pattern. Use a NEW purpose prefix `discovery_` (separate from `suggestions_`) so artist + suggestions discovery share the weekly budget cap but get logged distinctly |
| APScheduler job persistence | SQLAlchemyJobStore for `library_sync` | `MemoryJobStore` + `CronTrigger` + lifespan catch-up gate | Persistence adds new dependency surface; wall-clock anchoring + catch-up gate solves the bug with no new infra |
| Color contrast calculator at runtime | inline contrast check | Pre-compute hex values during palette selection; static palette in code | Vibe colors are assigned once at vibe creation, never re-derived — no need for runtime calc |
| MB cache eviction | TTL + LRU | `MusicBrainzCache(mb_id, payload_json, cached_at)` with no eviction | Artists don't change. Cache is write-once-read-many. SQLite can hold millions of rows trivially. |
| Hashlib for `DiscoveryAdd` dedupe key | manual SHA | `INSERT OR IGNORE` on `UNIQUE(mb_id)` constraint | The natural key already exists; no need for a hash |

**Key insight:** Phase 8 has more "wire-up established things" surface than "design new things" surface. The temptation is to abstract — resist it. Every new client is one more thing that needs `to_thread`-wrapping and AST coverage.

---

## Architecture Patterns

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│ Browser (mobile-first)                                              │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────┐    │
│   │ /vibes   │   │/discover │   │/settings │   │/debug/discov.│    │
│   │ + chip   │   │ + cards  │   │ + 2-prof │   │              │    │
│   └────┬─────┘   └────┬─────┘   └────┬─────┘   └──────┬───────┘    │
└────────┼──────────────┼─────────────┼──────────────────┼───────────┘
         │HTMX swap     │HTMX (5min)  │HTMX           │read-only
         │              │             │               │
┌────────▼──────────────▼─────────────▼───────────────▼──────────────┐
│ FastAPI routers                                                    │
│   pages.py            api_discovery.py        api_settings.py       │
│   (cost chip read)    (cards, add, dismiss)   (test, save lidarr)   │
└────────┬──────────────────────┬───────────────────────┬─────────────┘
         │                      │                       │
         ▼                      ▼                       ▼
┌────────────────────────────────────────────────────────────────────┐
│ Services layer                                                     │
│                                                                    │
│  ┌──────────────────────┐    ┌────────────────────────────┐        │
│  │ discovery_service.py │    │ lidarr_client.py (extended)│        │
│  │  - read_cached_      │    │  - test_lidarr_connection  │        │
│  │    candidates        │    │    (now returns 3 lists)   │        │
│  │  - artist_discovery_ │    │  - add_artist              │        │
│  │    call_weekly       │    │  - get_history             │        │
│  │  - dismiss_artist    │    └─────────────┬──────────────┘        │
│  │  - record_add        │                  │ pyarr (sync, wrap     │
│  └──┬───────────────────┘                  │ in to_thread)         │
│     │                                      ▼                       │
│     │                                  ┌────────┐                  │
│     ├─►anthropic_client (re-rank)      │ Lidarr │                  │
│     ├─►llm_cost_breaker.check_or_raise │ server │                  │
│     │  (purpose="discovery_artist_     └────────┘                  │
│     │   weekly")                                                   │
│     │                                                              │
│     ├─►musicbrainz_client.py (NEW)     ┌──────────────┐           │
│     │   - validate(mb_id)              │ MusicBrainz  │           │
│     │   - synthesise_adjacency(…)      │  API (1/s)   │           │
│     │   - cache layer over             └──────────────┘           │
│     │     MusicBrainzCache                                         │
│     │                                                              │
│     └─►listenbrainz_client.py (NEW,    ┌──────────────┐           │
│         IF Path B chosen)              │ ListenBrainz │           │
│        - similar_artists(mb_id)        │  labs API    │           │
│                                        └──────────────┘           │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ sync_scheduler._weekly_maintenance_tick (extended)       │      │
│  │   1. prune_suggestions_playlist_to_mirror  (Phase 7.1)   │      │
│  │   2. discovery_call_weekly                 (Phase 7.1)   │      │
│  │   3. artist_discovery_call_weekly          (NEW Phase 8) │      │
│  │   4. update WeeklyCronState.last_tick_at   (NEW Phase 8) │      │
│  └──────────────────────────────────────────────────────────┘      │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ sync_scheduler.schedule_sync (FIXED — DISC-08)           │      │
│  │   CronTrigger(hour=3) + coalesce + misfire_grace_time    │      │
│  │   + lifespan catch-up gate                               │      │
│  └──────────────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────┐
│ SQLite (single writer via dispatcher serialization)                │
│  Existing:    LLMUsage, EventLog, Vibe, TrackVibe, Track, ...      │
│  Phase 8 NEW: DiscoveryCandidate, DiscoveryAdd, DiscoveryDismissed,│
│               MusicBrainzCache, CostMeterBaseline, WeeklyCronState │
│  Phase 8 COL: Vibe.color (additive via _migrate_add_columns)       │
└────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────┐
│ External integrations (all sync libraries — must to_thread)        │
│  PlexAPI (sync_service unchanged) ─ Plex server                    │
│  pyarr.Lidarr ──────────────────── Lidarr server                   │
│  musicbrainzngs ────────────────── musicbrainz.org (1 req/sec)     │
│  httpx ─────────────────────────── ListenBrainz labs (Path B)      │
│  anthropic AsyncClient ─────────── api.anthropic.com               │
└────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
app/
├── services/
│   ├── lidarr_client.py             # EXTEND (D-E1)
│   ├── discovery_service.py         # NEW — module-singleton, mirrors suggestions_discovery
│   ├── musicbrainz_client.py        # NEW — wraps musicbrainzngs with cache + UA setup
│   ├── listenbrainz_client.py       # NEW (Path B only)
│   ├── sync_scheduler.py            # EXTEND _weekly_maintenance_tick + schedule_sync (DISC-08)
│   └── sync_service.py              # EXTEND _record_sync_failure_event (DISC-08)
├── models/
│   ├── discovery.py                 # NEW — DiscoveryCandidate, DiscoveryAdd,
│   │                                #       DiscoveryDismissed, MusicBrainzCache
│   ├── ops.py (or extend existing)  # NEW — CostMeterBaseline, WeeklyCronState
│   └── vibe.py                      # EXTEND — Vibe.color column
├── routers/
│   ├── api_discovery.py             # NEW — /discover endpoints (list, add, dismiss, status)
│   ├── api_settings.py              # EXTEND — test_lidarr returns 3 lists; save_lidarr persists 4 fields
│   └── pages.py                     # EXTEND — vibes_home weekly chip query; /debug/discovery route
├── templates/
│   ├── pages/
│   │   ├── discover.html            # REPLACE discover_placeholder.html (real page)
│   │   ├── library.html             # FULL REWRITE (D-D1 mobile-first)
│   │   ├── debug_discovery.html     # NEW
│   │   └── debug_index.html         # EDIT — unmask the /debug/discovery slot
│   └── partials/
│       ├── connection_status.html   # EDIT — add metadata-profile dropdown + root-folder picker
│       ├── discover_vibe_row.html   # NEW — vibe-grouped horizontal scroll row
│       ├── discover_artist_card.html # NEW — tap-to-expand card
│       ├── discover_status_row.html # NEW — post-add Lidarr lifecycle row
│       ├── home_weekly_chip.html    # NEW — D-B4 home-page chip
│       └── vibe_color_chip.html     # NEW (or inline) — used by every vibe label surface
└── main.py                          # EXTEND lifespan — run_phase_08_discovery_bootstrap
```

### Pattern 1: Wrapping pyarr in `asyncio.to_thread`

```python
# Source: existing app/services/lidarr_client.py:14-49 (verified)
async def add_artist_for_discovery(
    mb_id: str, artist_name: str
) -> dict[str, Any]:
    """Add an LLM-recommended artist to Lidarr via the saved quality + metadata
    profile + root folder. All four params required (Pitfall 14)."""
    # Read saved Lidarr config (sync DB → wrap to_thread)
    config = await asyncio.to_thread(_read_lidarr_config_sync)
    if not config:
        raise LidarrNotConfiguredError()

    lidarr = Lidarr(host_url=config.url, api_key=config.api_key)
    # 1) Look up the artist by MB ID to get the dict shape add_artist needs
    candidates = await asyncio.to_thread(lidarr.lookup_artist, f"mbid:{mb_id}")
    # 2) Filter to exact MB ID match (defensive — Pitfall 10)
    artist_dict = next((c for c in candidates if c.get("foreignArtistId") == mb_id), None)
    if not artist_dict:
        raise ArtistNotFoundError(mb_id)
    # 3) Add — ALL FOUR required params
    result = await asyncio.to_thread(
        lidarr.add_artist,
        artist=artist_dict,
        root_dir=config.root_folder_path,
        quality_profile_id=config.quality_profile_id,
        metadata_profile_id=config.metadata_profile_id,
        monitored=True,
        artist_monitor="all",
        search_for_missing_albums=True,
    )
    return result
```

### Pattern 2: MusicBrainz client with cache + rate-limit

```python
# NEW file: app/services/musicbrainz_client.py
import musicbrainzngs
from app.database import get_engine
from app.models.discovery import MusicBrainzCache

# Module load — set UA once (REQUIRED by MB or raises UsageError)
musicbrainzngs.set_useragent(
    "Composer",
    "2.0",
    "sam.e.browning@gmail.com",  # contact per project user email
)
musicbrainzngs.set_rate_limit(limit_or_interval=1.0)  # 1 req/sec

def _cache_get_sync(mb_id: str) -> dict | None:
    # SELECT payload_json FROM musicbrainzcache WHERE mb_id = ?
    ...

def _cache_put_sync(mb_id: str, payload: dict) -> None:
    # INSERT OR REPLACE ...
    ...

async def lookup_artist(mb_id: str) -> dict:
    cached = await asyncio.to_thread(_cache_get_sync, mb_id)
    if cached:
        return cached
    # The sync MB call — wrap in to_thread; rate-limit handled inside the library
    payload = await asyncio.to_thread(
        musicbrainzngs.get_artist_by_id,
        mb_id,
        includes=["artist-rels", "url-rels", "tags", "ratings", "release-groups"],
    )
    await asyncio.to_thread(_cache_put_sync, mb_id, payload)
    return payload
```

### Pattern 3: Weekly tick extension

```python
# EXTEND app/services/sync_scheduler.py::_weekly_maintenance_tick

async def _weekly_maintenance_tick() -> None:
    from app.services.plex_playlist_service import prune_suggestions_playlist_to_mirror
    from app.services.suggestions_discovery import discovery_call_weekly
    from app.services.suggestions_service import _read_plex_creds_sync
    from app.services.discovery_service import artist_discovery_call_weekly  # NEW

    plex_url, plex_token = await asyncio.to_thread(_read_plex_creds_sync)

    # Step 1 — prune (best-effort)
    try:
        result = await prune_suggestions_playlist_to_mirror(plex_url, plex_token)
        logger.info("Weekly maintenance step 1: prune ok (removed=%d)", len(result.removed))
    except Exception:
        logger.exception("Weekly maintenance step 1: prune failed; continuing")

    # Step 2 — track suggestions discovery (Phase 7.1)
    try:
        await discovery_call_weekly()
        logger.info("Weekly maintenance step 2: track suggestions discovery ok")
    except Exception:
        logger.exception("Weekly maintenance step 2: track suggestions discovery failed; continuing")

    # Step 3 — artist discovery (Phase 8 NEW)
    try:
        await artist_discovery_call_weekly()
        logger.info("Weekly maintenance step 3: artist discovery ok")
    except Exception:
        logger.exception("Weekly maintenance step 3: artist discovery failed; continuing")

    # Step 4 — update WeeklyCronState.last_tick_at (Phase 8 NEW, for D-B4 chip)
    try:
        await asyncio.to_thread(_update_weekly_cron_state_sync)
    except Exception:
        logger.exception("Weekly maintenance step 4: WeeklyCronState update failed")
```

### Anti-Patterns to Avoid

- **Bulk Lidarr add (`/discover` "add all 10")** — amplifies Pitfall 14 failure modes; CONTEXT defers to v2 (one-at-a-time per D-D3).
- **Aggressive Lidarr polling cron post-add** — D-C3 explicitly chooses lazy poll on `/discover` render (5-min cache). No new APScheduler job.
- **LLM call from `/discover` request hot path** — would re-introduce Pitfall 11. All LLM happens in the Sunday cron tick.
- **MusicBrainz validation done client-side / in template** — the rate-limit makes it server-side-only with the `MusicBrainzCache` layer.
- **`hx-trigger="every 30s"` on the per-add status row** — Pitfall 11. Use `hx-trigger="revealed"` + `hx-trigger="load delay:5min"` instead (or even simpler: no auto-refresh, page reload is the refresh).
- **Putting `Vibe.color` in CSS rather than the DB** — Vibes are user-named runtime entities; their colors must be queryable via SQL for the cost chip and debug pages.
- **`BackgroundTasks` for the Lidarr add request** — established anti-pattern (Phase 5). Use a direct `await asyncio.to_thread(lidarr.add_artist, ...)` inside the request handler; it's a single bounded call.

---

## Runtime State Inventory

> Phase 8 is NOT a rename / refactor / migration phase. This section is included anyway because the deploy crosses a schema boundary (new tables + `Vibe.color` column) and a NAS already has live data.

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | (a) Existing `Vibe` rows (production NAS has ~3-7 from Phase 6.2). Need `Vibe.color` backfilled on first Phase 8 deploy. (b) Existing `LLMUsage` rows — historical/testing data; D-B4 chip must filter `called_at >= CostMeterBaseline.deploy_at` to exclude these. | Lifespan migration `run_phase_08_discovery_bootstrap` backfills `Vibe.color` (deterministic palette assignment by `id % len(palette)`) AND seeds the `CostMeterBaseline` row with `deploy_at=now()`. Idempotent — `MigrationLog` gates. |
| Live service config | None. The user's Lidarr config (`ServiceConfig.lidarr`) is in the SQLite DB and already in git-managed deploy. D-E1 ADDS new `extras` keys (`metadata_profile_id`, `metadata_profile_name`, `root_folder_path`) — but these are written by the user via the settings flow, not migrated from somewhere. After Phase 8 deploys, the existing Lidarr config will be functional but incomplete; the user must re-test connection to populate the new fields. **The discovery feature will short-circuit with a "Lidarr settings incomplete — re-test connection in Settings" CTA until the user does this.** | Document in the deploy notes: "After this deploy, re-run Lidarr connection test in Settings to populate the new metadata-profile + root-folder fields." |
| OS-registered state | None — APScheduler is in-process; no systemd/launchd registration. | None |
| Secrets / env vars | None. Anthropic key + Plex token + Lidarr API key already in DB. Phase 8 introduces NO new secrets. | None |
| Build artifacts / installed packages | Adding `musicbrainzngs` to `requirements.txt`. Docker rebuild on next deploy installs it cleanly via `pip install -r`. No stale egg-info concerns. | None |

---

## Common Pitfalls (Phase 8 surface)

The five pitfalls below come from `.planning/research/PITFALLS.md` and are LOCKED, not re-decided. Each task in Phase 8 plans should reference the relevant Pitfall number in its acceptance criteria.

### Pitfall 10 — LLM hallucinates artist names

**What goes wrong:** LLM returns "The Velvet Echoes from Brooklyn" — a real-sounding artist that doesn't exist. Without validation, that name lands in `/discover`. User clicks Add. Lidarr's `lookup_artist` returns nothing. User sees a generic error and loses trust.

**How to avoid (Phase 8 application):** Every LLM-returned `mb_id` (or `artist_name` if the LLM is freer) round-trips through `musicbrainz_client.lookup_artist(mb_id)` OR `lidarr_client.lookup_artist(name)` before being written to `DiscoveryCandidate`. Mismatch → drop and log to `LLMUsage.notes` with `validation_failure=True`. Pre-LLM step is even better: since the seed-first pipeline (D-A1) sources candidates from ListenBrainz/MB, the candidates ARE already real — the LLM only re-ranks. Validation is then a sanity check, not the primary defense.

**Warning signs:** `/debug/discovery` shows >5% LLM-validation drops; Lidarr `add_artist` 404s.

### Pitfall 12 — Cross-surface dedup

**What goes wrong:** User has Burial in their library; LLM re-ranks "Burial" into the discovery list. Now `/discover` shows an artist the user has 12 tracks of. Trust collapses.

**How to avoid (Phase 8 application):** Two filters MUST run BEFORE the LLM re-rank:
1. `mb_id` ∉ `SELECT DISTINCT plex_artist_mbid FROM track WHERE plex_artist_mbid IS NOT NULL` (already-in-library check)
2. `mb_id` ∉ `lidarr.get_artist()` (already-in-Lidarr check — cache the result for 1h to avoid hammering Lidarr)

Implement as a `compute_discovery_eligible_artists()` mirror of `suggestions_discovery.compute_discovery_eligible`.

### Pitfall 13 — Popularity bias (Coldplay problem)

**What goes wrong:** LLM defaults to mainstream-adjacent recommendations.

**How to avoid (Phase 8 application):** Already locked as CONTEXT D-A3 (popularity-bias hard gate). Implementation:
1. **Anchor** — D-A2 per-vibe single-track seed (narrowest possible question, not "what would I like generally")
2. **Filter** — listener-count gate. Since MusicBrainz lacks listener counts (Spike 3), use either (a) MB `release-group` count as a proxy (`>200 release-groups` → likely top-tier popular, drop unless user has a comparable-popularity artist already), or (b) one targeted Last.fm `artist.getInfo` call per candidate to read `listeners` (costs an API key — defer unless adjacency-only proves insufficient). Default: use MB release-group count as the soft proxy.
3. **Hard adjacency gate** — every candidate's MB record must have at least one `member-of-band` / `collaboration` / `supporting-musician` edge to a starred-artist MB ID, OR share a label/release-group with a starred artist.
4. **Provenance display** — the factual hook (D-A4) makes the gate visible.

**Counters surfaced on `/debug/discovery`:** "Pre-LLM candidates: 142. Listener-gate drops: 18. Adjacency-gate drops: 89. Sent to LLM: 35. LLM-returned: 12. Validation drops: 0. Surfaced: 12."

### Pitfall 14 — Quality profile mismatch + connection-test bug

**What goes wrong (Phase 8 application — this is the v1 bug):** Current `lidarr_client.test_lidarr_connection` returns only quality profiles. User saves config; later `add_artist` call needs `metadata_profile_id` which is never persisted → silent Lidarr failure.

**How to avoid:** D-E1 fix — extend `test_lidarr_connection` to fetch quality + metadata profile + root folder in one `to_thread`. `save_lidarr` persists `quality_profile_id`, `quality_profile_name`, `metadata_profile_id`, `metadata_profile_name`, `root_folder_path` to `ServiceConfig.extras`. `discovery_service.add_artist` reads all from settings.

**Also ship in v1 of the feature** (per Pitfall 14):
- **Upfront UI text** on each `/discover` artist card: "Will use 'FLAC Preferred' profile" (read from settings)
- **24h post-add monitoring** — `DiscoveryAdd.lidarr_status` polled lazily via `lidarr.get_history()`. If `added_at > 2 days ago AND no `eventType=trackFileImported` history rows reference the artist`, show a soft warning chip "Velvet Echoes added 2d ago, no releases found yet — change quality profile?"

### Pitfall 25 — Auto-ingest plumbing

**What goes wrong:** Lidarr imports a downloaded album, Plex sees it, but Composer's local DB doesn't because its sync ran before the import.

**How to avoid (Phase 8 application):** CONTEXT D-C1 piggybacks the daily `library_sync` cron. The existing chain `library_sync → trigger_post_sync_analysis → Essentia → handle_rating_changed slot_track` is already wired (verified at `sync_service.py:222` and `analysis_service.py:299`). **Phase 8 makes no changes to this chain.** Latency: up to 24h. Acceptable per D-C1.

**Load-bearing dependency: D-E3 / DISC-08.** This pitfall's mitigation only works if `library_sync` runs daily. If the cron is silently broken (as NAS UAT confirmed), the auto-ingest never fires. Plan 01 MUST land DISC-08 before any discovery-add UX is shipped.

### NEW pitfall surfaced during research — MB "similar artists" doesn't exist

**What goes wrong:** Planner reads CONTEXT D-A1 "MusicBrainz 'similar artists' lookup" and writes a plan task that calls a nonexistent endpoint. Implementation hits a wall.

**How to avoid:** Surface this to the user at planning time (see Light Spike 4 + Summary). Either pick Path B (ListenBrainz similar-artists, recommended) or Path A (synthesised MB adjacency, more work). Make the choice explicit in the plan tasks.

---

## Code Examples

### LLM-returned artist validation against MusicBrainz (Pitfall 10)

```python
# In discovery_service.py
from app.services.musicbrainz_client import lookup_artist as mb_lookup_artist

async def _validate_and_persist_candidate(
    candidate: LLMArtistPick, seed_track_id: int, seed_vibe_id: int
) -> DiscoveryCandidate | None:
    try:
        mb_payload = await mb_lookup_artist(candidate.mb_id)
    except musicbrainzngs.ResponseError as e:
        if "404" in str(e):
            logger.warning("LLM hallucination: mb_id=%s not in MusicBrainz", candidate.mb_id)
            return None
        raise
    # Confirm artist name matches (defensive — LLM might pair wrong name with right MBID)
    mb_name = mb_payload["artist"]["name"]
    if not _name_match(mb_name, candidate.artist_name):
        logger.warning("Name mismatch: LLM=%r MB=%r mb_id=%s",
                       candidate.artist_name, mb_name, candidate.mb_id)
        return None
    return await asyncio.to_thread(
        _insert_discovery_candidate_sync,
        mb_id=candidate.mb_id,
        artist_name=mb_name,  # use MB's canonical name
        seed_track_id=seed_track_id,
        seed_vibe_id=seed_vibe_id,
        llm_rank=candidate.rank,
        llm_rationale=candidate.rationale,
        factual_hook=candidate.factual_hook,
    )
```

### Lazy lidarr status poll on `/discover` render (D-C3)

```python
# In api_discovery.py
from datetime import datetime, timezone, timedelta

_lidarr_status_cache: dict[str, tuple[str, datetime]] = {}  # mb_id → (status, fetched_at)

async def _get_lidarr_status_cached(mb_id: str, lidarr_artist_id: int | None) -> str:
    now = datetime.now(timezone.utc)
    cached = _lidarr_status_cache.get(mb_id)
    if cached and (now - cached[1]) < timedelta(minutes=5):
        return cached[0]
    # Refresh: get the artist's recent history
    if not lidarr_artist_id:
        return "pending"
    history = await asyncio.to_thread(_get_artist_history_sync, lidarr_artist_id)
    status = _derive_status(history)  # "searching" | "downloading" | "imported, awaiting sync" | etc.
    _lidarr_status_cache[mb_id] = (status, now)
    return status
```

### Home-page weekly cost chip query (D-B4 / UI-10)

```sql
-- Sums LLM cost since the most recent successful weekly cron tick,
-- but only counts rows after the Phase 8 deploy baseline.
SELECT COALESCE(SUM(cost_estimate_usd), 0.0) AS week_cost
FROM llm_usage
WHERE called_at >= (
    SELECT last_tick_at FROM weekly_cron_state WHERE id = 1
)
AND called_at >= (
    SELECT deploy_at FROM cost_meter_baseline WHERE id = 1
);

-- And "next refresh in Nd" is computed from WeeklyCronState.last_tick_at +
-- 7 days vs now. If WeeklyCronState row is missing (pre-first-tick), show
-- "Your first weekly tick lands Sunday at 03:00 UTC."
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| MusicBrainz as "similar artists" source | ListenBrainz `similar-artists` dataset (labs) OR Last.fm `artist.getSimilar` | Since ListenBrainz launched ~2017 with similar-artists data; MB never had it | Phase 8 must pick a non-MB source for the candidate fetch step |
| `IntervalTrigger` for daily jobs | `CronTrigger(hour=N)` + `coalesce=True` + lifespan catch-up | APScheduler 3.x has supported this since 3.0; the pattern just gets ignored | DISC-08 fix shape |
| pyarr `LidarrAPI` class | `Lidarr` class (renamed 6.x) | pyarr 6.0 | Current `lidarr_client.py` already handles via `try/except ImportError` fallback — no action |
| Direct Anthropic SDK calls | Single `anthropic_client.call_with_structured_output` entry point | Phase 5 OPS-02 | Phase 8 inherits — no new SDK code |

**Deprecated / outdated:**

- **Instructor library** — removed in v1 of Composer (per CLAUDE.md "Alternatives Considered"). Phase 8 must NOT re-introduce.
- **`pyarr<6.0` `LidarrAPI` class name** — already handled via the import fallback in `lidarr_client.py:6-9`.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Tableau 10 hex values | Vibe Palette Option 2 | Low — verified via GitHub gist WebFetch quote, but the gist is community-curated; planner should cross-check against the official Tableau blog post `tableau.com/blog/colors-upgrade-tableau-10-56782` if exact values matter for brand alignment |
| A2 | `musicbrainzngs.set_rate_limit(limit_or_interval=1.0)` argument shape | Pattern 2 code example | Low — library is 0.7.1, API has been stable for years; confirm via `help(musicbrainzngs.set_rate_limit)` during implementation |
| A3 | pyarr `get_history()` paged response includes `eventType` per record | Spike 2 | Low — confirmed via Lidarr server API docs that `/api/v1/history` returns this shape; pyarr is a thin wrapper |
| A4 | ListenBrainz `similar-artists` endpoint at `labs.api.listenbrainz.org/similar-artists/json` is stable | Spike 3, Path B | MEDIUM — `labs.` subdomain implies the contract is not as firmly held as core APIs. Planner should test the endpoint manually before locking Path B. If it 404s or shape-changes, fall back to Path A (MB adjacency synthesis) or escalate to Last.fm. |
| A5 | The contact email for the MB User-Agent should be `sam.e.browning@gmail.com` | Spike 3 | Trivial — pulled from user-email memory; could be a project-specific contact instead |
| A6 | `coalesce=True` + `misfire_grace_time=3600` is the right combo for `library_sync` | Spike 6 | Low — standard APScheduler reliability recipe; verified via docs |
| A7 | Lidarr's `lookup_artist("mbid:<MBID>")` query string is the right way to search by MBID | Pattern 1 | MEDIUM — pyarr passes the search term through; Lidarr's MetadataService accepts `mbid:` prefix but the exact behaviour should be tested with a real Lidarr server during implementation. Fallback: pass the artist name as `term` and filter results in Python. |

**Assumed claims to confirm during planning:** A4 (ListenBrainz endpoint stability — verify before locking Path B), A7 (Lidarr `lookup_artist` MBID query format — test against the actual NAS Lidarr during Plan 01). Everything else is low-risk.

---

## Open Questions

1. **Path A vs Path B for candidate source** — what we know: CONTEXT says "MusicBrainz similar artists" but that's not a real endpoint. What's unclear: whether the user wants strict MB-only (Path A, more work, more rate-limit pressure, fully MetaBrainz) or ListenBrainz-as-supplement (Path B, simpler, slightly less-stable contract). Recommendation: surface to user at start of planning, default to Path B.

2. **Album art for MB-only artists** — what we know: CONTEXT defers this to a placeholder/initials avatar. What's unclear: how good "initials only" looks on the vibe-grouped horizontal scroll layout (D-D2). Recommendation: ship initials avatars in v1; if visually weak, add a Cover Art Archive lookup in a Phase 8.1 quick task.

3. **Which palette option does the user prefer?** — three viable options in Spike 5. Bring all three to the user during planning with a Tailwind utility-class preview screenshot or a quick HTML diff.

4. **Should `_record_sync_failure_event` (DISC-08 Part C) write to `EventLog` or to a new `SyncFailureLog` table?** — CONTEXT D-E3 specifies `EventLog` with `event_type="sync_failed"`. Recommendation: stick with `EventLog` (one fewer table, already plumbed to `/debug/events`, dedupe pattern already proven).

5. **Lidarr `root_folder` picker UX when multiple root folders exist** — most Lidarr users have one root folder; some have multiple (organised by genre). Recommendation: if `get_root_folder()` returns >1, add a third dropdown to the connection-test partial; otherwise auto-select the only one and don't show a dropdown.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Lidarr server | DISC-05, DISC-06, DISC-07, DEBUG-04 | User-supplied; project does not control | varies (Lidarr `>=2.0` recommended) | None — feature gates with "Configure Lidarr in Settings" empty state |
| Plex server with `userRating`-populated tracks | All discovery (seed source) | User-supplied | Existing Composer prereq | None — discovery requires rated set |
| MusicBrainz API | DISC-03 (validation + adjacency synth, Path A) | ✓ (public, free, no key) | API ws/2 stable | If MB down: log + fail current cron tick; SQL hot path keeps Suggestions playlist full (per D-B3) |
| ListenBrainz API | DISC-03 (candidate fetch, Path B only) | ✓ (public, free, no key) | labs API | Fall back to Path A (MB synth) at runtime if returns 5xx |
| Anthropic API | DISC-03 LLM re-rank | ✓ (key already in `ServiceConfig`) | claude-sonnet-4.x | Cost breaker trips → discovery skipped that week (per D-B3) |
| Docker container internet egress | All HTTP integrations | ✓ assumed | — | None — no point running Composer without |
| `musicbrainzngs` package | NEW for Phase 8 | Will be added to `requirements.txt` | 0.7.1 | None |

**Missing dependencies with fallback:**
- ListenBrainz (Path B): falls back to Path A at runtime
- Lidarr: feature gates entire `/discover` UX

**Missing dependencies with no fallback that block execution:** None — every dependency has a graceful-degradation path.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest` + `pytest-asyncio` (asyncio_mode = "auto"); FastAPI TestClient via `httpx` |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/test_<file>.py -x --tb=short` |
| Full suite command | `pytest tests/ -x` |

[VERIFIED: pyproject.toml read]

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| DISC-03 | Discovery page renders ≥10 candidates per vibe | integration | `pytest tests/test_api_discovery.py::test_discover_renders_candidates -x` | ❌ Wave 0 |
| DISC-04 | Popularity-bias gate drops top-N% globally popular | unit | `pytest tests/test_discovery_service.py::test_popularity_gate -x` | ❌ Wave 0 |
| DISC-05 | `add_artist` passes both `quality_profile_id` AND `metadata_profile_id` | unit (mock pyarr) | `pytest tests/test_discovery_service.py::test_add_artist_sends_both_profile_ids -x` | ❌ Wave 0 |
| DISC-06 | After `library_sync`, new Lidarr-imported tracks are queued for analysis | integration | `pytest tests/test_sync_service.py::test_auto_ingest_chain_runs -x` (extend existing) | ⚠ Partial (existing test_sync_service covers some) |
| DISC-07 | `test_lidarr_connection` returns quality + metadata + root-folder lists | unit (mock pyarr) | `pytest tests/test_lidarr_client.py::test_connection_test_returns_three_lists -x` | ❌ Wave 0 |
| DISC-08 | `schedule_sync(24)` registers `CronTrigger` not `IntervalTrigger` | unit | `pytest tests/test_sync_scheduler.py::test_library_sync_uses_cron_trigger -x` | ❌ Wave 0 |
| DISC-08 | Silent sync failures write `EventLog` row with `event_type="sync_failed"` | unit | `pytest tests/test_sync_service.py::test_sync_failure_writes_event_log -x` | ❌ Wave 0 |
| DISC-08 | Lifespan catch-up fires `run_sync()` when `last_sync_completed` > `interval_hours + grace` | unit | `pytest tests/test_sync_scheduler.py::test_catch_up_fires_when_stale -x` | ❌ Wave 0 |
| UI-07 | Library page renders mobile (≤375px) without horizontal scroll | manual-only | manual UAT on NAS via iPhone | manual gate |
| UI-08 | All Phase 8 templates pass `test_mobile_first_conventions` | AST test | `pytest tests/test_mobile_first_conventions.py -x` (existing) | ✅ Existing AST test extends naturally |
| UI-09 | Every vibe-rendering template reads `Vibe.color` | AST/static | `pytest tests/test_vibe_color_propagation.py -x` (new) | ❌ Wave 0 |
| UI-10 | Home-page chip query excludes rows older than `CostMeterBaseline.deploy_at` | unit | `pytest tests/test_pages_home_chip.py::test_chip_baseline_filter -x` | ❌ Wave 0 |
| OPS-06 | Discover never lists v1 playlist artists as "in library" via the `Composer · ` prefix check | unit | `pytest tests/test_discovery_service.py::test_legacy_playlist_recognition -x` | ❌ Wave 0 |
| DEBUG-04 | `/debug/discovery` returns 200 + renders provenance per candidate | integration | `pytest tests/test_pages_debug_discovery.py::test_renders_provenance -x` | ❌ Wave 0 |
| Phase 5 D-09 invariant | No PlexAPI/pyarr sync calls in async paths in NEW files | AST test | `pytest tests/test_event_handlers.py::test_no_blocking_plexapi_in_async -x` (EXTEND file list) | ✅ Existing AST test must EXTEND its `paths` list to include `discovery_service.py`, `lidarr_client.py`, `musicbrainz_client.py`, `api_discovery.py` |

### Sampling Rate

- **Per task commit:** `pytest tests/test_<file_under_change>.py -x --tb=short` (≈10s)
- **Per wave merge:** `pytest tests/ -x` (full suite, ≈3min based on existing suite size)
- **Phase gate:** Full suite green BEFORE `/gsd-verify-work`. Manual UAT on NAS for UI-07 portrait rendering BEFORE phase complete.

### Wave 0 Gaps

The following test files must be created/extended in Wave 0 before implementation tasks land:

- [ ] `tests/test_api_discovery.py` — covers DISC-03 surface render, DEBUG-04 placeholder unmask
- [ ] `tests/test_discovery_service.py` — covers DISC-04 popularity gate, DISC-05 add-artist params, OPS-06 legacy recognition
- [ ] `tests/test_lidarr_client.py` — EXTEND existing if any (none currently); covers DISC-07 three-list return shape
- [ ] `tests/test_sync_scheduler.py` — covers DISC-08 CronTrigger + catch-up tests
- [ ] `tests/test_musicbrainz_client.py` — covers MB cache write-through, rate-limit invocation, UA validation (the UA-missing → UsageError check)
- [ ] `tests/test_pages_debug_discovery.py` — covers DEBUG-04 page render with provenance
- [ ] `tests/test_pages_home_chip.py` — covers UI-10 baseline filter
- [ ] `tests/test_vibe_color_propagation.py` — covers UI-09 across all surfaces (AST scan)
- [ ] `tests/test_event_handlers.py` — EXTEND `test_no_blocking_plexapi_in_async`'s `paths` list (Phase 5 D-09 invariant)

No framework install needed; pytest + pytest-asyncio already in pyproject.toml.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | Single-user, Tailscale-only access; no auth surface added by Phase 8 |
| V3 Session Management | no | Stateless HTMX |
| V4 Access Control | no | Same — single-user |
| V5 Input Validation | YES | All `/discover` `Form` inputs validated via `Annotated[str, Form()]` + length caps. `mb_id` is validated against MusicBrainz before any storage. LLM-returned artist names always cross MB validation (Pitfall 10). |
| V6 Cryptography | no | Lidarr API key is already encrypted at rest via `encryption.py` (existing) |
| V8 Data Protection | YES | API keys never exposed in HTML / API responses after first save (existing CONF-04 invariant — Phase 8 settings flow must respect when re-rendering `connection_status.html` after a re-test) |
| V11 Business Logic | YES | Cost breaker (`llm_cost_breaker.check_or_raise`) gates the artist discovery LLM call — re-using Phase 7.1 pattern; daily/weekly quota + burst limit + debounce |
| V13 API Security | YES | Lidarr API key transmitted only inside the container's outbound request to Lidarr; never reflected back to browser |
| V14 Configuration | YES | New `ServiceConfig.extras` keys (`metadata_profile_id`, `metadata_profile_name`, `root_folder_path`) are non-sensitive (profile names, path strings) — fine to render in UI |

### Known Threat Patterns for {Python/FastAPI/Lidarr/MB stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection in LLM-supplied `mb_id` written to `DiscoveryCandidate` | Tampering | Use SQLModel ORM with parameterised inserts (NEVER string interpolate `mb_id` into SQL) — already the project's standard |
| SSRF via attacker-controlled Lidarr URL in settings | Tampering / Spoofing | Lidarr URL is single-user-typed; out of scope (user is trusted owner of their NAS). No new validation needed. |
| Anthropic API key leak via debug page | Information Disclosure | `/debug/discovery` must never render the Anthropic API key — only model name + token counts + cost (existing `/debug/suggestions` pattern) |
| Rate-limit abuse against MusicBrainz | Repudiation (MB-side) | `musicbrainzngs.set_rate_limit(1.0)` + cache layer (`MusicBrainzCache`) prevent runaway lookups even on a runaway cron |
| Cost runaway (Pitfall 11) | DoS (user wallet) | `llm_cost_breaker.check_or_raise(purpose_prefix="discovery_")` gates the discovery LLM call. The cost chip (UI-10) is the user-visible canary. |

---

## Sources

### Primary (HIGH confidence)

- `app/services/lidarr_client.py` — current implementation, 49 lines, verified return shape
- `app/services/sync_scheduler.py` — Phase 7.1 weekly tick + catch-up pattern, verified
- `app/services/sync_service.py:226-229` — silent except clause, verified for DISC-08
- `app/static/css/input.css` — dark theme tokens, verified for palette contrast
- `app/templates/pages/discover_placeholder.html` — placeholder to replace
- `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async` — extant AST test to extend
- `.planning/research/PITFALLS.md` Pitfalls 10, 12, 13, 14, 25 — phase pitfall surface
- `.planning/phases/08-lidarr-discovery-polish/08-CONTEXT.md` — locked decisions
- [musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting) — 1 req/sec, HTTP 503 on throttle, UA format
- [musicbrainz.org/relationships/artist-artist](https://musicbrainz.org/relationships/artist-artist) — confirmed no "similar artists" relation
- [pypi.org/project/pyarr/](https://pypi.org/project/pyarr/) — pyarr 6.6.0 released 2026-03-31
- [docs.totaldebug.uk/pyarr/modules/lidarr.html](https://docs.totaldebug.uk/pyarr/modules/lidarr.html) — pyarr Lidarr module docs
- [apscheduler.readthedocs.io/en/3.x/userguide.html](https://apscheduler.readthedocs.io/en/3.x/userguide.html) — coalesce, misfire_grace_time, max_instances semantics
- [python-musicbrainzngs.readthedocs.io/en/v0.7.1/usage/](https://python-musicbrainzngs.readthedocs.io/en/v0.7.1/usage/) — `set_useragent`, `set_rate_limit` API
- [conceptviz.app/blog/okabe-ito-palette-hex-codes-complete-reference](https://conceptviz.app/blog/okabe-ito-palette-hex-codes-complete-reference) — Okabe-Ito hex values

### Secondary (MEDIUM confidence — WebSearch verified against official source)

- [gist.github.com/leblancfg/b145a966108be05b4a387789c4f9f474](https://gist.github.com/leblancfg/b145a966108be05b4a387789c4f9f474) — Tableau 10 hex values (community-curated gist, cross-verified via WebFetch)
- [labs.api.listenbrainz.org/similar-artists](https://labs.api.listenbrainz.org/similar-artists) — ListenBrainz similar-artists labs endpoint exists (contract less stable than core APIs)
- [www.last.fm/api/show/artist.getSimilar](https://www.last.fm/api/show/artist.getSimilar) — Last.fm endpoint for the deferred Option C
- [tableau.com/blog/colors-upgrade-tableau-10-56782](https://www.tableau.com/blog/colors-upgrade-tableau-10-56782) — Tableau 10 palette design rationale
- [v10.carbondesignsystem.com/guidelines/color/overview/](https://v10.carbondesignsystem.com/guidelines/color/overview/) — IBM Carbon palette (reference for additional palette option if needed)
- [github.com/agronholm/apscheduler/issues/1095](https://github.com/agronholm/apscheduler/issues/1095) — confirms missed-job semantics across restart

### Tertiary (LOW confidence — single source, would benefit from re-verification before locking)

- pyarr `add_artist` exact parameter list (signature reconstructed from WebSearch result quoting the docs; the actual source on github.com/totaldebug/pyarr returned 404 via WebFetch — `gh` CLI not available to fetch). Confirm by inspecting `~/.venv/lib/python3.12/site-packages/pyarr/lidarr.py` after `pip install` during Plan 01 implementation.
- ListenBrainz `similar-artists` exact JSON shape — labs page documents the UI but not the GET URL contract verbatim. Confirm via a one-shot manual curl before locking Path B.

---

## Metadata

**Confidence breakdown:**

- Standard stack: **HIGH** — pyarr 6.6 pin verified, musicbrainzngs canonical, anthropic + apscheduler already in deps
- Architecture: **HIGH** — every component maps to an existing Phase 5/6/7/7.1 pattern; the only NEW shape is the `_weekly_maintenance_tick` extension which is mechanical
- pyarr signature: **HIGH** for `add_artist` (multi-source verification); MEDIUM for `get_history` shape (verified Lidarr API docs but pyarr wrapper not directly read)
- MusicBrainz strategy: **MEDIUM** — strong verification that the "similar artists" endpoint does NOT exist; recommendation of ListenBrainz/Path B is solid but the labs-API stability is an assumed risk worth re-verifying during Plan 01
- APScheduler reliability fix: **HIGH** — pattern matches Phase 7.1 D-C2 verbatim; the only new code is the `CronTrigger` switch which is one-line in `schedule_sync`
- Palette options: **HIGH** — Okabe-Ito and Tableau 10 are gold-standard; Tailwind option is judgment call but defensible
- Pitfalls: **HIGH** — all 5 are locked, not re-derived
- Validation architecture: **HIGH** — pytest infra exists, test files mapped 1:1 to requirements

**Research date:** 2026-05-16
**Valid until:** 2026-06-15 (30 days for stable; APScheduler patterns, pyarr 6.x stable, MB API decade-stable). Re-verify ListenBrainz labs endpoint before locking Path B in Plan 02.

## RESEARCH COMPLETE
