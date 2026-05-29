---
phase: 07-suggestions-queue-v1-chat-retirement
verified: 2026-05-14T14:44:00Z
updated: 2026-05-29
status: verified
score: 26/26 must-haves verified — 3 human-verification items confirmed via NAS UAT 2026-05-29 (see 07-HUMAN-UAT.md)
overrides_applied: 0
re_verification: null
gaps: []
deferred: []
human_verification:
  - test: "SC1 — drain-and-refill within 30 seconds (end-to-end)"
    expected: "User plays a track from `Composer · Suggestions` in Plexamp on the NAS. Within 30 seconds: (a) played track disappears from the SuggestionsMirror queue (visible at /suggestions and /debug/suggestions), (b) a new track is appended (with non-empty `rationale`, `vibe_id`, `score`), (c) the Plex playlist `Composer · Suggestions` reflects the new top-up. The first-ever refill on a fresh deploy must materialize the Plex playlist (CR-01 fix path — sentinel `plex_rating_key=''` is replaced with the real ratingKey)."
    why_human: "Requires a real Plex+Plexamp+Anthropic stack on the NAS. Tests prove the SuggestionsMirror writes, the Plex push branches (first-refill vs. subsequent), and the cost breaker; only a live scrobble can prove the 30-second end-to-end latency including PlexAPI roundtrip."

  - test: "SC4 — Anthropic prompt caching engaged across two refills within 1h"
    expected: "Trigger two refills within an hour (drain a track, wait the 30s debounce, drain another). On /settings the LLM cost meter card shows `Cache hit: P%` where P > 0 (i.e. `cache_read_input_tokens` > 0 on the second call). `cache_creation_input_tokens` > 0 on the first call. `/debug/suggestions` shows the two LLMUsage rows with the cache columns populated. System prompt size invariant: a runtime warning ('suggestions ranking cache creation not engaged') would log on the first call if the prompt fell below the 2048-token threshold — DO NOT see that warning in NAS logs."
    why_human: "Real Anthropic API call needed to populate cache_read_input_tokens; the unit tests mock the AnthropicClient response shape but cannot verify the live cache behaviour."

  - test: "CR-03 — find-candidates progress card visibly streams during a real refill"
    expected: "On the /vibes page, tap a 'Find candidates' CTA on a vibe with <25 tracks (mobile Safari portrait at 375px). The button is replaced with an llm_progress_card that shows 'Calling Anthropic… (ranking candidates)' and an elapsed-seconds counter that ticks during the LLM round-trip. When the call completes (or breaker trips) the card resolves. The endpoint must return in <100ms (fire-and-forget), with the LLM heavy work happening in the background task."
    why_human: "Visual / timing UAT on a real device. Tests confirm `asyncio.create_task` is used and the template renders `llmProgressCard(true)` (autostart=true) — but only a live tap proves the polling loop actually attaches and the card visibly displays during the real ~10-30s Anthropic call."
---

# Phase 7: Suggestions Queue + v1 Chat Retirement — Verification Report

**Phase Goal:** User has a continuous `Composer · Suggestions` Plex playlist that drains as they listen and refills with taste-aware picks within 30 seconds; v1 mood-chat is retired; vibes home is the new landing page.

**Verified:** 2026-05-14T14:44:00Z
**Status:** `human_needed` — every observable truth verifiable from code passes; three behaviors require a NAS / live-Plex / live-Anthropic UAT to fully confirm the user-facing outcome.
**Re-verification:** No — initial verification.

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | SuggestionsMirror SQLite table is the source of truth for queue contents (SUGG-02) | VERIFIED | `app/models/suggestions.py:24-48` defines `SuggestionsMirror(SQLModel, table=True)` with `track_id` UNIQUE+indexed, `position`, `added_at`, `rationale`, `vibe_id`, `score`. Used as the read-truth in `/suggestions`, `/debug/suggestions`, refill insert path. |
| 2  | Single `ManagedPlaylist(kind='suggestions', composer_name='Composer · Suggestions')` row created exactly once per install | VERIFIED | `app/services/suggestions_service.py:280-328` `bootstrap_suggestions_queue` short-circuits via `_find_suggestions_managed_playlist_sync`. Sentinel pattern documented; `MigrationLog(phase_id='7.0-suggestions-bootstrap')` gate. |
| 3  | bootstrap callable from BOTH wizard finalize AND lifespan migration, gated by MigrationLog | VERIFIED | `app/routers/api_setup.py:669` awaits `suggestions_service.bootstrap_suggestions_queue()` after reslot in `finalize`. `app/main.py:250-251` awaits `run_phase_07_suggestions_bootstrap()` in `lifespan` between `run_phase_61_migration` and `get_event_bus`. |
| 4  | handle_track_played drains the mirror and schedules refill via threshold gate (SUGG-03) | VERIFIED | `app/services/event_handlers.py:230-264` — `_update_track_play_sync` first (Phase 5 RATE-04), then best-effort `drain_track_from_mirror` + `maybe_schedule_refill` in `try/except`. |
| 5  | maybe_schedule_refill drives the actual refill in-line (Plan 02 W4 upgrade) | VERIFIED | `app/services/suggestions_service.py:349-377` — when deficit > 0, `await refill_suggestions_queue(target=target)`. Tests in test_suggestions_service.py confirm; `grep -rln 'suggestions_refill_pending' tests/` returns 0. |
| 6  | Every PlexAPI call in async paths dispatched via `asyncio.to_thread` (Phase 5 D-09) | VERIFIED | `tests/test_event_handlers.py::TestStaticAnalysis::test_no_blocking_plexapi_in_async` passes including `app/services/suggestions_service.py`. The new `_materialize_suggestions_plex_playlist` wraps `PlexServer.createPlaylist` in `asyncio.to_thread`. |
| 7  | refill_suggestions_queue brings mirror back to target=30 with cost breaker BEFORE LLM (Pitfall 11) | VERIFIED | `app/services/suggestions_service.py:1213-1416` — deficit check → `check_or_raise` → shortlist → Anthropic call (`purpose='suggestions_rank'`, `thinking='off'`) → Pitfall 10 validate (one retry on out-of-range) → INSERT OR IGNORE → Plex push branch (`first-refill materialize` vs additive `update_playlist_items`). |
| 8  | Anthropic call uses cache_control ttl=1h and system prompt > 2048-token threshold | VERIFIED | `app/services/anthropic_client.py:118` — `"cache_control": {"type": "ephemeral", "ttl": "1h"}` (explicit, never 5min default). `app/services/suggestions_service.py:1148-1150` — runtime assertion `len(prompt) >= 8000` chars. Plan 02 SUMMARY reports 9905 chars / 1544 words. **Live cache_read evidence requires NAS UAT (see human verification 2).** |
| 9  | Pitfall 10 — LLM picks validated against shortlist range, retry once then drop | VERIFIED | `_validate_picks` at `app/services/suggestions_service.py:1189-1212`; `SuggestionRankingPick` with integer `candidate_index` and `min_length=1` rationale; retry path with `## VALIDATION FAILURE` addendum at `1326-1340`. Tests in `test_suggestions_service_v2.py` confirm. |
| 10 | SuggestionHistory tracks every surfaced track; 14-day exclusion in shortlist (SUGG-07 / Pitfall 12) | VERIFIED | `SuggestionHistory` model in `app/models/suggestions.py:51-62`. `_build_shortlist_sync` excludes the 14-day window (verified at `_read_eligible_tracks_sync`). |
| 11 | Hard-negative dismiss (SUGG-09 / D-13): writes hard_track AND hard_artist NegativeSignal, drains row, recovery on 3+ star artist rating | VERIFIED | `handle_dismiss_track` at `suggestions_service.py:1710-1728` writes both signals + drains. `handle_artist_rating_recovery` at `1731-1749` clears recovery_pending on rating >= 6.0 (raw 0-10 == 3 stars). Wired into `handle_rating_changed` at `event_handlers.py:201-227`. |
| 12 | Soft-negative sweep callable AND scheduled (SUGG-08) | VERIFIED | `handle_soft_negative_sweep` at `suggestions_service.py:1752-1759`. `schedule_soft_negative_sweep` at `sync_scheduler.py:80-115` registers CronTrigger(hour=4, minute=0, tz='UTC') with `replace_existing=True`. Called from `app/main.py:262` AFTER `start_scheduler()`. |
| 13 | Cost breaker (SUGG-11): daily quota 50, burst 5/60s, debounce 30s; trips set `_status.state='cost_locked'` | VERIFIED | `app/services/llm_cost_breaker.py` — `DAILY_QUOTA=50`, `BURST_LIMIT=5`, `BURST_WINDOW_SECONDS=60`, `DEBOUNCE_SECONDS=30`. `check_or_raise` aggregated SELECT enforces all three. Refill catches and sets `_status.state='cost_locked'` (verified at `suggestions_service.py:1259-1264`). |
| 14 | OPS-05 LLM cost meter on Settings: spend today / cache hit % / breaker-paused state | VERIFIED | `pages.py::settings_page:313-374` aggregates LLMUsage for today; includes `today_calls`, `today_cost_usd`, `cache_hit_pct`, breaker context (60s TTL on tripped surface). Template `partials/llm_cost_meter.html` renders the card (included in `settings.html:18`). |
| 15 | SUGG-10 vibe coverage CTA: POST /api/vibes/{id}/find-candidates fires only on user tap | VERIFIED | `api_vibes.py:1021-1062` — POST only; 404 on unknown vibe; renders the progress card with `autostart=true`; fire-and-forget via `asyncio.create_task` (CR-03 fix). Vibe card CTA at `partials/vibe_card.html:18-28` shown only when `track_count < 25`. |
| 16 | UI-01: GET / serves v2 vibes home when Plex configured + ≥1 Vibe row exists; legacy chat never reachable | VERIFIED | `pages.py:34-81` — home route renders `read_vibes_home` when `vibe_count > 0`; falls back to welcome on render error (WR-09 fix). |
| 17 | UI-06: GET /chat returns 404; all POST /api/chat/* return 404; chat templates archived under `_archived/` | VERIFIED | `pages.py:162-171` returns 404 for /chat. `api_chat.py:36-58` — every endpoint returns `_RETIRED_BODY` with 404. `app/templates/_archived/chat.html`, `chat_message.html`, `playlist_card.html` present; `app/templates/pages/chat.html` does NOT exist. No live `{% include %}` references _archived (grep confirmed). |
| 18 | UI-06 (data preservation): playlist + chat data tables untouched | VERIFIED | `app/models/playlist.py` exists. `app/services/chat_service.py` exists (tests fail for chat_service — pre-existing). Models and DB schema intact; only UI surface removed. |
| 19 | UI-02: bottom tab bar (Vibes / Suggestions / Discover / Settings) rendered at mobile via md:hidden | VERIFIED | `partials/bottom_tab_bar.html` — fixed bottom inset-x-0 + `md:hidden`; 4 tabs in a Jinja loop; included from `base.html:27`. Top nav `nav.html` is `hidden md:block`. |
| 20 | UI-03: viewport-fit=cover + min-h-dvh + safe-area-inset gutter | VERIFIED | `base.html:5` `viewport-fit=cover`; `base.html:14` `min-h-dvh`; `base.html:22` `pb-[calc(env(safe-area-inset-bottom)+64px)]`; `bottom_tab_bar.html:13` `pb-[env(safe-area-inset-bottom)]`. |
| 21 | UI-04: every tappable element ≥ 44px (min-h-11) | VERIFIED | grep confirms `min-h-11` in: `nav.html` (6 hits), `bottom_tab_bar.html` (rendered 4× via loop), `suggestions_row.html` (1), `suggestion_expanded.html` (2), `vibe_card.html` (2), `debug_index.html` (4), `vibes_home.html` (1). No `:hover`-only state controls information access (verified per Plan 03 SUMMARY + test_mobile_first_conventions.py). |
| 22 | UI-05: alpine-morph extension active; Alpine state survives HTMX swaps | VERIFIED | `base.html:15` `hx-ext="alpine-morph"`; `base.html:9-11` loads alpine-morph + plugin scripts. Tests in `test_mobile_first_conventions.py` pin the convention. |
| 23 | DEBUG-03: /debug/suggestions renders queue + last 20 refill triggers + last 20 LLMUsage (suggestions_%) + last 20 NegativeSignal + breaker state | VERIFIED | `pages.py:204-278` queries all 5 sections; `debug_suggestions.html` renders all 5 with table/pre/code blocks (DEBUG-05 plain-HTML invariant); breaker state from in-process singleton. |
| 24 | DEBUG-05: /debug index links every debug surface; settings footer links /debug | VERIFIED | `pages.py:190-201` route; `debug_index.html` lists `/debug/events`, `/debug/vibes`, `/debug/suggestions`, Phase 8 `/debug/discovery` placeholder. `settings.html:102` `<a href="/debug">View diagnostics →</a>`. |
| 25 | SUGG-04 first-refill materializes Plex playlist (CR-01 fix) | VERIFIED | `app/services/suggestions_service.py:210-277` `_materialize_suggestions_plex_playlist` wraps PlexServer.createPlaylist via `asyncio.to_thread`. Both `refill_suggestions_queue` (1381-1387) and `refill_suggestions_for_vibe` (1563-1569) branch on `not mp.plex_rating_key` to call materialize on first non-empty refill. Tests `test_first_refill_materializes_plex_playlist_when_sentinel` + `test_subsequent_refill_uses_update_playlist_items` confirm. **Live Plex side-effect requires NAS UAT (see human verification 1).** |
| 26 | SC5 cost-paused UI surface: "Suggestions paused — cost limit hit (reason)" | VERIFIED | `partials/llm_cost_meter.html:24-28` renders the banner when `breaker_paused == True`. `pages.py:340-351` flips `breaker_paused=True` when `last_tripped_at < 60s ago`. |

**Score:** 23 verified from code alone; 3 require live UAT (still counted as VERIFIED at the implementation layer — they would PASS in code review but cannot be 100% proven without a real device/server).

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/models/suggestions.py` | SuggestionsMirror + SuggestionHistory + NegativeSignal + RefillTriggerLog SQLModel tables | VERIFIED | 4 tables present (lines 24-110). UNIQUE(track_id), proper indices. |
| `app/services/suggestions_service.py` | Full Plan 01 + Plan 02 module (bootstrap, drain, refill_queue, refill_for_vibe, dismiss, recovery, sweep, ranking) | VERIFIED | 1760 lines. All 14 public functions present (grep listed earlier). |
| `app/services/llm_cost_breaker.py` | check_or_raise + CostBreakerTrippedError + CostBreakerStatus + DAILY_COST_BUDGET_USD constant | VERIFIED | 178 lines; aggregated SELECT; all three thresholds enforced; DAILY_COST_BUDGET_USD=0.42 (WR-11 fix). |
| `app/services/sync_scheduler.py::schedule_soft_negative_sweep` | UTC 04:00 cron registered on AsyncIOScheduler singleton | VERIFIED | Lines 80-115; CronTrigger(hour=4, minute=0, timezone='UTC'); replace_existing=True. |
| `app/routers/api_suggestions.py` | POST /api/suggestions/{rating_key}/dismiss | VERIFIED | 51 lines; calls `handle_dismiss_track`; best-effort try/except → empty 200. |
| `app/routers/api_vibes.py::find_vibe_candidates` | POST /api/vibes/{id}/find-candidates fire-and-forget + autostart progress card | VERIFIED | Lines 1021-1062; `asyncio.create_task`; renders `partials/llm_progress_card.html` with `autostart=true` (CR-03 fix). |
| `app/routers/pages.py` | home / read_vibes_home / read_suggestions / read_chat_retired / read_discover_placeholder / read_debug_index / read_debug_suggestions / settings_page | VERIFIED | All 8 routes present (lines 34-374). Settings aggregates LLMUsage + breaker. |
| `app/routers/api_chat.py` | All endpoints return 404 + retirement notice | VERIFIED | 5 endpoints, all return `_retired()` (404 + body). Router stays registered (intentional). |
| `app/templates/base.html` | min-h-dvh + viewport-fit=cover + hx-ext alpine-morph + bottom tab bar include + safe-area gutter | VERIFIED | All 4 invariants present (lines 5, 14, 15, 22, 27). |
| `app/templates/partials/bottom_tab_bar.html` | 4-tab nav, fixed bottom, md:hidden, env(safe-area-inset-bottom) | VERIFIED | All 4 tabs in Jinja loop; min-h-11 per anchor; class-based active state. |
| `app/templates/partials/nav.html` | Vibes + Suggestions links; Compose label removed; hidden md:block; min-h-11 anchors | VERIFIED | 6× min-h-11; no `\bCompose\b` label (verified by regex test). |
| `app/templates/pages/suggestions.html` | SuggestionsMirror compact list + empty-state loader | VERIFIED | Reuses `partials/suggestions_row.html` per row; empty state shows `llm_progress_card`. |
| `app/templates/partials/suggestions_row.html` | 48px art + title + artist + vibe chip + min-h-11 tap + Alpine x-data toggle | VERIFIED | 47 lines; explicit `| e` on Track/Vibe fields (T-07-03-01..03). |
| `app/templates/partials/suggestion_expanded.html` | Rationale + Plexamp deeplink + Dismiss button | VERIFIED | 30 lines; `plexamp://play/{rk}` href; HTMX outerHTML dismiss target. |
| `app/templates/pages/vibes_home.html` | Per-vibe cards | VERIFIED | Reuses `vibe_card.html` per row. |
| `app/templates/partials/vibe_card.html` | track_count display + Find candidates CTA when count < 25 | VERIFIED | 29 lines; CTA POSTs to `/api/vibes/{id}/find-candidates`; conditional on count. |
| `app/templates/pages/debug_index.html` | Links to /debug/events, /debug/vibes, /debug/suggestions, /debug/discovery placeholder | VERIFIED | 53 lines; min-h-11 per card; Phase 8 placeholder rendered as inert span. |
| `app/templates/pages/debug_suggestions.html` | 5 sections: breaker, queue, refill log, LLMUsage suggestions_%, NegativeSignal | VERIFIED | 153 lines; all 5 sections; explicit `| e` on Track/Vibe/rationale. |
| `app/templates/partials/llm_cost_meter.html` | spend / cache % / breaker-paused; daily_budget_usd via defined guard | VERIFIED | 30 lines; WR-11 hardcoded literal removed in favour of `daily_budget_usd`. |
| `app/templates/_archived/{chat,chat_message,playlist_card}.html` | Templates moved via `git mv`, data layer preserved | VERIFIED | All 3 present in `_archived/`; no copies in `pages/` or `partials/`. |
| `app/static/css/input.css` | `[x-cloak] { display: none !important }` rule (WR-03 fix) | VERIFIED | Lines 23-28; comment block notes the WR-03 fix purpose. |

All 21 artifacts pass Level 1 (exists), Level 2 (substantive — non-stub), Level 3 (wired into the app from a router/lifespan/template entry point), and Level 4 (data flows — SuggestionsMirror rows seed via refill, vibe counts seed from TrackVibe, LLMUsage seeds from AnthropicClient).

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `api_setup.finalize` | `suggestions_service.bootstrap_suggestions_queue` | await after reslot_all_rated_tracks, before _finalize_status='completed' | WIRED | `api_setup.py:669`; wrapped in try/except so wizard never fails on Plex hiccup. |
| `app/main.py::lifespan` | `suggestions_service.run_phase_07_suggestions_bootstrap` | between `run_phase_61_migration` and `get_event_bus` | WIRED | `main.py:250-251`. |
| `event_handlers.handle_track_played` | `suggestions_service.drain_track_from_mirror` + `maybe_schedule_refill` | lazy import + try/except hook | WIRED | `event_handlers.py:252-264`. |
| `event_handlers.handle_rating_changed` | `suggestions_service.handle_artist_rating_recovery` | best-effort hook when `new_rating >= 6.0` | WIRED | `event_handlers.py:201-227`. |
| `suggestions_service.maybe_schedule_refill` | `suggestions_service.refill_suggestions_queue` | direct await when deficit > 0 (Plan 02 W4 upgrade) | WIRED | `suggestions_service.py:369-376`. |
| `suggestions_service.refill_suggestions_queue` | `llm_cost_breaker.check_or_raise` | called BEFORE AnthropicClient.call_with_structured_output | WIRED | `suggestions_service.py:1256-1257`. |
| `suggestions_service.refill_suggestions_queue` | `anthropic_client.call_with_structured_output` | purpose='suggestions_rank', thinking='off' | WIRED | `suggestions_service.py:1296-1304`. |
| `suggestions_service.refill_*` | `_materialize_suggestions_plex_playlist` OR `update_playlist_items` | branch on `not mp.plex_rating_key` (sentinel detection) | WIRED | `suggestions_service.py:1373-1399` (queue) + `1554-1580` (vibe). CR-01 fix verified. |
| `app/main.py::lifespan` | `sync_scheduler.schedule_soft_negative_sweep` | called AFTER `start_scheduler()` | WIRED | `main.py:262`. |
| `sync_scheduler.schedule_soft_negative_sweep` | `suggestions_service.handle_soft_negative_sweep` | CronTrigger UTC 04:00 daily, job id `suggestions_soft_negative_sweep` | WIRED | `sync_scheduler.py:100-115`. |
| `pages.settings_page` | `partials/llm_cost_meter.html` | include + 7 context fields (today_calls, today_cost_usd, daily_quota, daily_budget_usd, cache_hit_pct, breaker_paused, breaker_reason) | WIRED | `pages.py:353-374` + `settings.html:18`. |
| `pages.read_suggestions` | `SuggestionsMirror` (Plan 01) | reads ordered by position; joins to Track/Vibe | WIRED | `pages.py:125-159`. |
| `pages.read_debug_suggestions` | `SuggestionsMirror` + `RefillTriggerLog` + `LLMUsage` + `NegativeSignal` + `llm_cost_breaker.get_state` | reads all 5 surfaces | WIRED | `pages.py:204-278`. |
| `api_suggestions.dismiss_track` | `suggestions_service.handle_dismiss_track` | best-effort await | WIRED | `api_suggestions.py:39-49`. |
| `api_vibes.find_vibe_candidates` | `suggestions_service.refill_suggestions_for_vibe` | `asyncio.create_task` fire-and-forget (CR-03) | WIRED | `api_vibes.py:1048-1052`. |
| `app/main.py` | `api_suggestions.router` | include_router | WIRED | `main.py:288`. |
| `base.html` | `partials/bottom_tab_bar.html` | `{% include %}` at end of body | WIRED | `base.html:27`. |
| `suggestion_expanded.html` | `api_suggestions.dismiss_track` | hx-post + hx-target=`#suggestion-row-{rk}` + hx-swap=outerHTML | WIRED | `suggestion_expanded.html:22-24`. |

All 18 key links verified.

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `pages/suggestions.html` | `suggestions` | `pages.read_suggestions` reads SuggestionsMirror ORDER BY position, joins Track + Vibe | Yes — real DB query joined to real Track / Vibe rows | FLOWING |
| `pages/vibes_home.html` | `vibes` | `pages.read_vibes_home` reads Vibe(is_active=True) + per-vibe TrackVibe count | Yes — real DB queries | FLOWING |
| `pages/debug_suggestions.html` | 5 collections | `pages.read_debug_suggestions` queries 5 tables + breaker singleton | Yes — real queries; breaker reads in-process state | FLOWING |
| `partials/llm_cost_meter.html` | today_calls / cost / cache_hit_pct / breaker_paused | `pages.settings_page` aggregates LLMUsage for today, computes cache hit %, reads breaker state | Yes — real LLMUsage aggregate + breaker singleton | FLOWING |
| `partials/llm_progress_card.html` (after find-candidates) | autostart=True | `api_vibes.find_vibe_candidates` returns the partial; Alpine init starts polling | Yes — fire-and-forget; polling endpoint surfaces in-flight progress | FLOWING |
| `partials/suggestions_row.html` | s.track / s.vibe / s.row.rationale | data flows from `read_suggestions` enrichment loop | Yes | FLOWING |
| `partials/vibe_card.html` | v.id / v.name / v.track_count | data flows from `read_vibes_home` enrichment | Yes | FLOWING |

No HOLLOW or DISCONNECTED artifacts. The CR-01 risk (Plex playlist never created) was the historical concern — now fixed and confirmed wired through the sentinel-detection branch.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase 7 service tests | `pytest tests/test_suggestions_service.py tests/test_suggestions_service_v2.py tests/test_suggestions_migration.py tests/test_llm_cost_breaker.py` | 71 passed (in combined run) | PASS |
| Phase 7 API tests | `pytest tests/test_api_suggestions.py tests/test_api_vibes_phase7.py tests/test_pages_settings.py` | 13 passed | PASS |
| Phase 7 UI tests | `pytest tests/test_pages_suggestions.py tests/test_pages_debug_suggestions.py tests/test_chat_retirement.py tests/test_mobile_first_conventions.py` | 50 passed | PASS |
| Combined Phase 7 sweep | `pytest tests/test_suggestions_service*.py tests/test_llm_cost_breaker.py tests/test_api_suggestions.py tests/test_api_vibes_phase7.py tests/test_pages_*.py tests/test_chat_retirement.py tests/test_mobile_first_conventions.py tests/test_suggestions_migration.py` | 122 passed | PASS |
| Cross-cutting tests (event handlers, scheduler, api_setup, finalize) | `pytest tests/test_event_handlers.py tests/test_sync_scheduler.py::TestScheduleSoftNegativeSweep tests/test_sync_scheduler.py::TestLifespanRegistersSoftNegativeSweep tests/test_api_setup.py tests/test_finalize_integration.py` | 58 passed | PASS |
| AST static check covering suggestions_service.py | `pytest tests/test_event_handlers.py::TestStaticAnalysis -q` | 1 passed | PASS |
| Phase 7 module import | `python -c "import app.services.suggestions_service; import app.services.llm_cost_breaker; import app.routers.api_suggestions; import app.routers.api_chat"` | imports OK | PASS |
| Pre-existing test failures (out of scope) | `pytest tests/test_chat_service.py tests/test_audio_analyzer.py tests/test_sync_scheduler.py tests/test_sync_api.py tests/test_sync_service.py` | 13 failed (4 chat_service, 3 audio_analyzer, 3 sync_scheduler, 1 sync_api, 2 sync_service) — none touch Phase 7 code paths | SKIP (pre-existing per Plan SUMMARYs + context note) |

Total Phase 7 + cross-cutting test suite: **180 passed, 0 failed**. Pre-existing failures confirmed unrelated (Plan 01/02/03 SUMMARYs document them; verified by spot-checking that no Phase 7-introduced module appears in the failure list).

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| SUGG-01 | Plan 01 | Configurable target (default 30) for Composer · Suggestions Plex playlist | SATISFIED | `SUGGESTIONS_TARGET_SIZE = 30` constant; `maybe_schedule_refill(target=...)` accepts override. |
| SUGG-02 | Plan 01 | SQLite is source of truth; Plex is eventual mirror | SATISFIED | SuggestionsMirror UNIQUE(track_id); refill writes SQLite first, then pushes Plex; CR-01 sentinel pattern keeps SQLite consistent even when Plex push fails. |
| SUGG-03 | Plan 01 | media.scrobble → drain local row + schedule refill | SATISFIED | `handle_track_played` drain + threshold gate; both lazy-imported with try/except. |
| SUGG-04 | Plan 02 | Refill pipeline: audio-features → shortlist ~50 → LLM rank → top-N | SATISFIED | `refill_suggestions_queue` orchestrates the 11-step pipeline; CR-01 ensures Plex playlist gets materialized. **Live PlexAPI confirmation needed (human verification 1).** |
| SUGG-05 | Plan 02 | Anthropic prompt cache with explicit `ttl='1h'` | SATISFIED | `anthropic_client.py:118` explicit ttl=1h. **Live `cache_read_input_tokens` > 0 confirmation needed (human verification 2).** |
| SUGG-06 | Plan 02 | One-line "Why this track?" rationale stored + shown on tap | SATISFIED | `SuggestionRankingPick.rationale: str = PydField(min_length=1)`; persisted into `SuggestionsMirror.rationale`; rendered in `suggestion_expanded.html:5-12`. |
| SUGG-07 | Plan 02 | SuggestionHistory table + 14-day dedup window | SATISFIED | `SuggestionHistory` model + `_build_shortlist_sync` excludes 14-day window. |
| SUGG-08 | Plan 02 | Soft-negative skip-tracking + LLM deboost | SATISFIED | `handle_soft_negative_sweep` + daily cron caller (`schedule_soft_negative_sweep`); soft signals injected into ranking user-prompt via `_read_soft_negatives_sync`. |
| SUGG-09 | Plan 02 | Hard-negative dismiss writes track + artist signals; artist recovery on 3+ star | SATISFIED | `handle_dismiss_track` + `handle_artist_rating_recovery`; both wired into endpoint and rating-changed handler. |
| SUGG-10 | Plan 02/03 | Vibe coverage CTA on vibes home for <25-track vibes | SATISFIED | `vibe_card.html` conditional CTA + POST `/api/vibes/{id}/find-candidates` (fire-and-forget per CR-03). |
| SUGG-11 | Plan 02 | Cost circuit breaker (daily 50, burst 5/60s, debounce 30s) + UI surface | SATISFIED | `llm_cost_breaker.py` enforces all three; `_status.state='cost_locked'` on trip; `breaker_paused` flag and "Suggestions paused — cost limit hit" banner in cost meter. |
| UI-01 | Plan 03 | Vibes home is the v2 landing | SATISFIED | `pages.home` rewires to `read_vibes_home` when vibes exist. |
| UI-02 | Plan 03 | Bottom tab bar on mobile, top nav on desktop | SATISFIED | `bottom_tab_bar.html` (md:hidden) + `nav.html` (hidden md:block). |
| UI-03 | Plan 03 | `h-dvh` + safe-area-inset + viewport-fit=cover | SATISFIED | All three present in `base.html`. |
| UI-04 | Plan 03 | 44px tap targets, no hover-only state | SATISFIED | min-h-11 audited across all Phase 7 templates. Active state in bottom tab bar is class-switch (border-accent), not :hover. |
| UI-05 | Plan 03 | HTMX swaps use alpine-morph | SATISFIED | `base.html:15` `hx-ext='alpine-morph'`. |
| UI-06 | Plan 03 | /chat 404, nav references gone, templates archived, DB preserved | SATISFIED | `pages.py:162-171` + `api_chat.py` all 404s; templates moved to `_archived/`; data layer (`chat_service.py`, `playlist.py`) intact. |
| OPS-05 | Plan 02 | LLM usage logged per call + daily aggregate on settings | SATISFIED | Phase 5 `anthropic_client.py` writes LLMUsage with cache fields; settings_page aggregates; `llm_cost_meter.html` renders. |
| DEBUG-03 | Plan 03 | /debug/suggestions diagnostic surface | SATISFIED | 5-section page wired to all 5 data sources. |
| DEBUG-05 | Plan 03 | /debug index + settings footer link | SATISFIED | `pages.py:190-201` route; `debug_index.html` lists every surface; settings footer link present. |

**All 20 declared requirement IDs accounted for.** No orphans against REQUIREMENTS.md cross-reference (REQUIREMENTS.md maps SUGG-01..11 + UI-01..06 + OPS-05 + DEBUG-03 + DEBUG-05 to Phase 7; every one is present in a Plan's `requirements` field — Plan 01: SUGG-01..03; Plan 02: SUGG-04..11 + OPS-05; Plan 03: UI-01..06 + DEBUG-03 + DEBUG-05).

---

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `app/templates/partials/suggestions_row.html:9` | `id="suggestion-row-{{ s.track.plex_rating_key }}"` without sanitization | INFO (WR-07 skipped) | Plex rating keys are integers in practice today. Reviewer recommended deferral; documented decision. |
| `app/routers/api_vibes.py` (`recluster_commit` keep/renamed_from path) | Additive push re-adds user-removed tracks | INFO (WR-08 skipped) | Pre-existing concern in re-cluster path (not Phase 7 introduction). Pitfall 5 invariant is intentional. Documented for backlog. |
| `app/services/suggestions_service.py` `_create_plex_suggestions_playlist` | Returns None — Plan 01 deferred indirection | INFO | Documented in REVIEW IN-06; CR-01 fix path supersedes it via `_materialize_suggestions_plex_playlist`. Function kept for test-shim purposes; not dead code. |
| `app/services/suggestions_service.py:1101-1109` | User-controllable Plex track metadata interpolated into LLM prompt | INFO (IN-02) | Pitfall 10 range validator is the mitigation. Documented in plan threat model. |
| `app/services/suggestions_service.py:680-687` | `_count_llm_usage_by_purpose_sync` materializes all rows before counting | INFO (IN-01) | Acceptable for current LLMUsage volume; flagged for optimization. |

No BLOCKERS (critical findings CR-01..04 are all FIXED and verified by tests). 3 WARNINGS were intentionally skipped per developer decision (WR-07, WR-08, WR-10 reclassified-to-Info) — none of them break the phase goal.

---

### Human Verification Required

3 items detailed in the frontmatter `human_verification:` block above.

1. **SC1 — drain-and-refill end-to-end within 30 seconds.** Code-level evidence verifies that every step of the pipeline exists and is wired (drain → threshold → cost-breaker → shortlist → LLM rank → mirror write → Plex push). The 30-second latency budget can only be observed on a real Plex+Anthropic round-trip. NAS UAT.

2. **SC4 — Anthropic caching actually engages.** Code-level evidence verifies the explicit `cache_control={'type':'ephemeral','ttl':'1h'}` plus the 8000-char prompt size invariant. Live `cache_read_input_tokens > 0` requires two real refill calls within an hour against the Anthropic API.

3. **CR-03 — find-candidates progress card visibly streams.** Tests prove the response body contains `llmProgressCard(true)` and the endpoint is fire-and-forget. Visual confirmation that the card actually appears and ticks during a real ~10-30s Anthropic round-trip requires a mobile Safari tap.

---

### Gaps Summary

No gaps blocking phase completion at the code level. Every must-have truth has implementation evidence in the codebase. The 11 fix commits applied after the executor SUMMARYs landed (CR-01..04 + 7 warnings) closed every critical review finding. 3 skipped warnings (WR-07 id-collision, WR-08 re-cluster additive re-add, WR-10 reclassified-to-Info) are tracked as future-hardening backlog and do NOT block the phase goal.

The phase ships:
- A working SuggestionsMirror → drain → refill → cost-breaker pipeline (Plans 01+02)
- The v2 mobile-first navigational shell + vibes home + /suggestions queue UI + retired /chat (Plan 03)
- The /debug/suggestions diagnostic surface + settings cost meter (Plans 02+03)
- The SUGG-08 daily cron caller actually wired into the singleton AsyncIOScheduler (Plan 02 B1)

Status `human_needed` reflects the gates that require live infrastructure to fully prove (Plex push, Anthropic cache hit, mobile-Safari progress card streaming). At the code-and-test layer the phase is complete.

---

_Verified: 2026-05-14T14:44:00Z_
_Verifier: Claude (gsd-verifier)_
