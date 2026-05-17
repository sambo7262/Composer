---
phase: 08-lidarr-discovery-polish
reviewed: 2026-05-17T00:00:00Z
depth: standard
files_reviewed: 35
files_reviewed_list:
  - app/models/discovery.py
  - app/models/track.py
  - app/models/vibe.py
  - app/routers/api_discovery.py
  - app/routers/api_library.py
  - app/routers/api_settings.py
  - app/routers/api_setup.py
  - app/routers/api_vibes.py
  - app/routers/pages.py
  - app/services/analysis_service.py
  - app/services/discovery_service.py
  - app/services/lidarr_client.py
  - app/services/listenbrainz_client.py
  - app/services/musicbrainz_client.py
  - app/services/plex_client.py
  - app/services/suggestions_service.py
  - app/services/sync_scheduler.py
  - app/services/sync_service.py
  - app/services/vibe_clusterer.py
  - app/services/vibe_service.py
  - app/utils/jinja_filters.py
  - app/utils/__init__.py
  - app/templates/pages/debug_discovery.html
  - app/templates/pages/discover.html
  - app/templates/pages/library.html
  - app/templates/pages/setup_step3.html
  - app/templates/pages/vibes_home.html
  - app/templates/partials/connection_status.html
  - app/templates/partials/discover_artist_card.html
  - app/templates/partials/discover_status_row.html
  - app/templates/partials/discover_top_tracks.html
  - app/templates/partials/discover_vibe_section.html
  - app/templates/partials/library_filter_chips.html
  - app/templates/partials/library_results_wrapper.html
  - app/templates/partials/library_sort_sheet.html
  - app/templates/partials/llm_cost_chip.html
  - app/templates/partials/recluster_modal.html
  - app/templates/partials/track_card.html
  - app/templates/partials/vibe_card.html
  - app/templates/partials/vibe_diagnostic_card.html
  - app/templates/partials/vibe_proposal_card.html
findings:
  critical: 1
  warning: 5
  info: 6
  total: 12
status: issues_found
---

# Phase 8: Code Review Report

**Reviewed:** 2026-05-17
**Depth:** standard
**Files Reviewed:** 35
**Status:** issues_found

## Summary

Phase 8 ships a large, coherent discovery surface and the foundational invariants
(Phase 5 D-09 `asyncio.to_thread` wrapping, OPS-06 hands-off, Pitfall 10/12/13/14
gates, raw 0-10 rating, multipart Form parsing) are honored throughout. One
BLOCKER was found: `/discover` reads `DiscoveryCandidate` rows with no week
filter, so as the weekly cron tick keeps writing INSERT-only rows, the page will
accumulate stale weeks indefinitely. Five WARNINGs cluster around correctness
gaps in the manual-tick + discovery state machine, the popularity gate's
mis-labelled label-set extraction, and unbounded-cache hygiene.

## Critical

### CR-01: `/discover` never filters DiscoveryCandidate to the current week — stale candidates accumulate forever

**File:** `app/services/discovery_service.py:1695-1710` (in `_read_active_discover_data_sync`)
**Issue:** `_write_discovery_candidates_sync` (line 790-819) is INSERT-only by
explicit design (Plan 02 decision — comment at lines 793-797). The Plan 02
SUMMARY's "Pointers for Plan 04" prescribed the read path as
`WHERE created_at >= (SELECT last_tick_at FROM weeklycronstate WHERE id=1)`,
but `_read_active_discover_data_sync` issues
`select(DiscoveryCandidate).where(DiscoveryCandidate.seed_vibe_id == vibe.id)`
with no `created_at` floor. After week 2 of operation, the user sees every
candidate the LLM has ever produced for that vibe, not just this week's set.
Worse, the `cand_vibe_lookup` at lines 1683-1686 selects EVERY row from the
table to build the in-flight-add grouping map, scaling linearly with all
weeks of history.

D-B2 explicitly says "/discover reads from this cache. No LLM call on page
open. Seeds rotate Sunday → Sunday." That contract is violated.

**Fix:** Filter by the current week's tick in `_read_active_discover_data_sync`:
```python
weekly = session.get(WeeklyCronState, 1)
cutoff = weekly.last_tick_at if weekly and weekly.last_tick_at else None
...
cands_q = select(DiscoveryCandidate).where(
    DiscoveryCandidate.seed_vibe_id == vibe.id
)
if cutoff is not None:
    cands_q = cands_q.where(DiscoveryCandidate.created_at >= cutoff)
```
Apply the same cutoff to `cand_vibe_lookup` so the in-flight grouping doesn't
read historical rows either. Tests covering this contract should assert that
rows older than `WeeklyCronState.last_tick_at` are excluded.

## Warning

### WR-01: Manual-tick wrapper clobbers `cost_locked`/`error` state from inner artist-discovery call

**File:** `app/services/discovery_service.py:2006-2036` (`run_manual_weekly_tick`)
**Issue:** `run_manual_weekly_tick` sets `_status.state = "running"` before
calling `_weekly_maintenance_tick()`, then unconditionally sets
`_status.state = "idle"` on success. But `artist_discovery_call_weekly`
(called inside the tick) does NOT raise on cost-breaker trip or LLM error —
it catches its own exceptions and stamps `_status.state = "cost_locked"` or
`"error"` then returns normally. Because the catch is inside, the outer
wrapper sees a clean return and overwrites the state back to `"idle"`. The
operator triggering the tick from `/debug/discovery` will see "idle" even
when artist discovery actually failed or was cost-locked, and `last_error`
gets cleared too (line 2022).

**Fix:** Only flip to `"idle"` when the inner state hasn't been set to a
terminal failure state by the inner call:
```python
try:
    from app.services.sync_scheduler import _weekly_maintenance_tick
    await _weekly_maintenance_tick()
    # Only mark idle if inner functions didn't already record a terminal state.
    if _status.state == "running":
        _status.state = "idle"
except Exception as exc:
    ...
```
Also avoid the unconditional `_status.last_error = None` at the top — leave
the previous error string visible until a new one supersedes it.

### WR-02: Popularity-gate "shared-label" arm reads artist-credit names, not labels

**File:** `app/services/discovery_service.py:590-599` (`_passes_popularity_gate`)
**Issue:** The shared-label arm builds `artist_labels: set` by iterating
`rg.get("artist-credit", [])` and pulling `credit["artist"]["name"]` — these
are artist co-credit display names, NOT label names. MusicBrainz labels are
exposed under `label-info-list` on the release / release-group, or via
`label-relation-list` on the artist. Even if `_starred_labels_sync` returned
a real label set today, the comparison would never match because we're
comparing artist names against label names. The latent bug is masked because
`_starred_labels_sync` returns `set()` (line 493), so `shared` is always
empty and the arm is dead — but the moment the v1.1 enrichment populates a
real label set, the gate will let the wrong thing pass.

**Fix:** Either rename the variable + drop the dead code with a TODO referencing
the planned enrichment, or implement it correctly against
`mb_artist.get("label-relation-list", [])`:
```python
artist_labels: set = set()
for rel in mb_artist.get("label-relation-list", []) or []:
    label = (rel.get("label") or {}).get("name")
    if label:
        artist_labels.add(label)
```
Also: `mb_id`-keyed adjacency checks at line 584-587 assume the MB lookup
result uses `artist-relation-list`. The musicbrainzngs lib returns the
`artist-relation-list` key only when `inc=artist-rels` is passed, which it
is (line 45). Confirm the test fixtures exercise both shapes
(`artist-relation-list` with and without nested `artist.id`).

### WR-03: Unbounded module-level cache growth — `_lidarr_status_cache` never evicts

**File:** `app/services/discovery_service.py:1446-1447` and `1773-1832`
**Issue:** `_lidarr_status_cache: dict = {}` is keyed by `mb_id` with no
size cap and no LRU eviction. The 5-min TTL governs RE-fetch, not cache
size — once an `mb_id` enters the cache it never leaves. Over a year of
weekly cron ticks with rotating seeds, this dict grows unbounded.
Acknowledged in Plan 04 SUMMARY ("T-08-22 accepts the unbounded-growth
risk for v1") but the comment on line 1444 says "bounded by the number of
in-flight DiscoveryAdds" — that's wrong. The cache holds an entry for
EVERY `mb_id` ever queried at status-row render time, including artists
whose lifecycle is "analyzed, slotted into vibes" (cached and never
evicted) and artists for which we returned `"unknown"` (no DiscoveryAdd
row — see line 1794). Same applies to `_LIDARR_KNOWN_ARTISTS_CACHE`
(line 329) on a single key, which is OK, but `_lidarr_status_cache`
keyed by mb_id is not.

**Fix:** Either cap with a small bounded LRU (e.g.
`functools.lru_cache` with `maxsize=256` wrapped around an internal
fetcher) or sweep entries whose `mb_id` is no longer in
`DiscoveryAdd WHERE vibe_slotted_at IS NULL` on every read. Don't ship
the "in flight" comment until the code actually enforces it.

### WR-04: Cost-meter chip uses lexicographic ISO string comparison on `LLMUsage.called_at` — works only by accident

**File:** `app/routers/pages.py:113-124` and `1944-1947`
**Issue:** `floor` is computed as `max(weekly.last_tick_at, baseline_iso)`
on Python strings, then passed into `LLMUsage.called_at >= floor` as a
SQL string comparison. This only matches chronological order when EVERY
`LLMUsage.called_at` row uses the same ISO format (UTC offset, identical
fractional-second precision, identical separator). Phase 5 invariants say
DB timestamps are raw UTC ISO 8601, but the comment "Lexicographic ISO
8601 ordering matches chronological ordering" assumes a uniformity the
codebase does not actively enforce (`datetime.now(timezone.utc).isoformat()`
produces variable-length output depending on microseconds).
A row written with `"2026-05-17T03:00:00+00:00"` vs.
`"2026-05-17T03:00:00.123456+00:00"` sorts unexpectedly when compared via
SQLite TEXT collation.

**Fix:** Parse the floor to a datetime and compare against parsed
`called_at` server-side, or normalise on write
(`datetime.now(timezone.utc).replace(microsecond=0).isoformat()`). Add a
test that interleaves rows with and without microseconds and verifies
the chip's `this_week_cost_usd` only sums the post-floor set.

### WR-05: `home_chip_context` baseline floor defaults to epoch — silently swallows missing-baseline misconfiguration

**File:** `app/routers/pages.py:115-119`
**Issue:** When `CostMeterBaseline` is absent (e.g. the bootstrap step at
`discovery_service._bootstrap_baseline_sync` failed but a later step
completed), `baseline_iso` falls back to `"1970-01-01T00:00:00+00:00"`,
which silently includes ALL historical and testing rows in the chip.
This is the exact case D-B4 was designed to prevent ("historical /
testing rows are irrelevant for the runaway-cost check"). The defensive
default hides a state-corruption signal. The bootstrap is gated by
MigrationLog, so a partial-success path is plausible (the bootstrap
catches per-step exceptions; if `_bootstrap_baseline_sync` raises, the
later `try/except` block at 277-296 won't recover because the helper
already called `session.commit()` for baseline. But a forced reset of
MigrationLog without clearing tables could land here).

**Fix:** When the baseline row is missing, treat the chip as
`has_first_tick=False` and surface a "Baseline not initialized — see
/debug/discovery" message OR auto-stamp the baseline at chip read time.
Don't silently fall back to epoch.

## Info

### IN-01: Misleading inline comment about route registration order in `api_discovery.py`

**File:** `app/routers/api_discovery.py:199-202`
**Issue:** Comment says "routes placed BEFORE the `/{mb_id}/...` catch-all
routes so FastAPI matches the literal paths first." But the actual file
order has `/{mb_id}/add` at L80, `/{mb_id}/dismiss` at L111,
`/{mb_id}/status-row` at L127 registered BEFORE `/run-tick-now` at L204
and `/tick-state` at L240. The routes happen to not conflict because
`/{mb_id}/<suffix>` requires two segments while `/run-tick-now` and
`/tick-state` are single-segment paths — but a future contributor reading
the comment will trust the false claim and may introduce a path-conflict
when adding `/{mb_id}` (no suffix).
**Fix:** Either reorder the literal routes to actually be first, or update
the comment to explain why the two-segment requirement prevents a
conflict. Prefer reordering — defense in depth.

### IN-02: `manual_weekly_tick` `<noscript>` form post returns 202 JSON, not a redirect — confusing fallback UX

**File:** `app/templates/pages/debug_discovery.html:75-83`
**Issue:** The no-JS fallback form posts to `/api/discovery/run-tick-now`,
which returns 202 with a JSON body `{"status": "started"}`. A browser
form submit will render the raw JSON in place of the page. Not a security
issue, but the no-JS fallback UX is broken.
**Fix:** Return an HTMX-compatible 202 with a redirect back to
`/debug/discovery` (e.g. via `HX-Redirect`/`Location` header for the
form-post path), or render an HTML acknowledgement when the request was
NOT from htmx.

### IN-03: `lidarr_client.test_lidarr_connection` uses `http_utils.request("metadataprofile")` — bypasses pyarr's typed wrapper

**File:** `app/services/lidarr_client.py:41`
**Issue:** Mid-function comment explains why
(`pyarr 6.x exposes metadata_profile only on Readarr, not Lidarr`), and
this is the documented workaround. But the call shape returns raw API
JSON without the same field-validation pyarr's `quality_profile.get()`
applies. If Lidarr ever changes `/api/v1/metadataprofile`'s response
shape, the test will silently break. Recommend adding a defensive
schema-check (assert `id` and `name` keys exist) and pin the workaround
to a comment with a pyarr issue reference for when (if) Lidarr support
lands upstream.
**Fix:** Add `assert all("id" in p and "name" in p for p in metadata_profiles)`
inside `_fetch_lidarr_test_payload` (raises a clear error rather than
the current KeyError on access at line 75).

### IN-04: `except Exception` in route handlers — Phase 5 convention disallows; explicit override

**File:** `app/routers/api_discovery.py:93, 122, 165, 270`
**Issue:** Phase 5 / CONTEXT-08 established patterns say "Tight per-handler
`except` clauses — no `except Exception` in route handlers." Phase 8 uses
`except Exception:` in `add_to_lidarr`, `dismiss_artist`, `get_status_row`,
and `get_top_tracks`. The CONTEXT explicitly approves the pattern
(`Best-effort error handling: never block the user from dismissing a row…`).
This is INFO because it's documented, but worth flagging in the review
record so future contributors don't take it as license to spread
catch-all elsewhere.
**Fix:** Add a one-line inline rationale at each catch-all site
(`# Best-effort per Phase 8 D-D5 — never block the user`).

### IN-05: `_compute_stale_context` 48h threshold hard-coded inline, no settings hook

**File:** `app/routers/api_discovery.py:38`
**Issue:** `_STALE_WARNING_THRESHOLD_HOURS = 48` is a module constant.
Pitfall 14 specifies "post-add monitoring 24h check" — Plan 04 widened
to 48h without surfacing the choice as a settings field. Future tuning
will require a code change + redeploy. Low priority but worth recording.
**Fix:** Move to a constant alongside `DISCOVERY_ARTIST_*` in
`discovery_service.py` and document the 48h choice with a one-line
comment referencing Pitfall 14.

### IN-06: `_starred_labels_sync` returns empty set — confirmed dead code per Plan 02 SUMMARY but no test coverage

**File:** `app/services/discovery_service.py:482-493`
**Issue:** Helper is documented as a no-op until a future TrackLabel join
table lands. There's no assertion in the test suite that the gate's
shared-label arm is deterministically dead — if `_starred_labels_sync`
is silently replaced with a populated set later, WR-02 (mis-labelled
artist names in `_passes_popularity_gate`) will silently pass the wrong
candidates. A regression test that pins the empty-set contract would
catch the latent coupling.
**Fix:** Add a unit test asserting `_starred_labels_sync()` returns
`set()` AND that any non-empty replacement triggers a test failure
referencing WR-02. (Or fix WR-02 first so the dead code becomes safe.)

---

_Reviewed: 2026-05-17_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
