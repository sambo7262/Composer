---
phase: 07-suggestions-queue-v1-chat-retirement
plan: 02
subsystem: suggestions-refill-llm
tags: [anthropic, prompt-cache, cost-breaker, refill, llm-ranking, skip-tracking, apscheduler-cron, ops-observability, tdd]

# Dependency graph
requires:
  - phase: 05-plex-event-foundation-rating-sync
    provides: AnthropicClient with cache_control ttl=1h (D-04 / OPS-02), LLMUsage telemetry, asyncio.to_thread D-09 convention, AsyncIOScheduler singleton (D-08 process-wide one-scheduler invariant)
  - phase: 06-vibe-clustering-llm-pipeline
    provides: Vibe.centroid_*/spread_* fields (used as 2σ pre-filter gate), ManagedPlaylist(kind='vibe') pattern, plex_playlist_service.update_playlist_items
  - phase: 06.2-llm-direct-vibe-assignment
    provides: shared cached preamble pattern (D-02/D-04), thinking="off" for bulk classification, llm_progress_card.html partial + /api/vibes/last-llm-call/progress endpoint
  - phase: 07-suggestions-queue-v1-chat-retirement-plan-01
    provides: SuggestionsMirror table, suggestions_service module-level singleton, ManagedPlaylist(kind='suggestions') sentinel row, handle_track_played drain branch, EventLog "suggestions_refill_pending" marker (this plan retires)
provides:
  - app/services/llm_cost_breaker.py with check_or_raise (daily 50, burst 5/60s, debounce 30s thresholds; SUGG-11 / Pitfall 11)
  - SuggestionHistory + NegativeSignal + RefillTriggerLog SQLModel tables
  - refill_suggestions_queue: shortlist → LLM rank → Pydantic validate → INSERT OR IGNORE → Plex push → RefillTriggerLog (SUGG-04..07)
  - refill_suggestions_for_vibe: targeted single-vibe refill (SUGG-10)
  - handle_dismiss_track + handle_artist_rating_recovery + handle_soft_negative_sweep (SUGG-08 / SUGG-09 / D-11/D-12/D-13)
  - schedule_soft_negative_sweep daily UTC 04:00 cron job on AsyncIOScheduler singleton (B1 — actually delivers SUGG-08)
  - POST /api/vibes/{vibe_id}/find-candidates SUGG-10 CTA endpoint
  - LLM cost meter card on /settings (OPS-05): today_calls / today_cost_usd / cache_hit_pct / breaker-paused state
  - /api/vibes/last-llm-call/progress extended to also match purpose LIKE 'suggestions_%'
  - Plan 01 W4: maybe_schedule_refill upgraded from EventLog marker to in-line refill_suggestions_queue() await; zero "suggestions_refill_pending" references remain in tests/
  - W1 runtime warning "suggestions ranking cache creation not engaged" on first-observed-call cache miss (mirrors AnthropicClient's existing "Anthropic caching not engaged" warning)
affects: [07-03-suggestions-page-ui, 07-04-debug-suggestions]

# Tech tracking
tech-stack:
  added: []  # all reuse: anthropic SDK, sqlmodel, sqlalchemy.text, apscheduler.cron
  patterns:
    - "Cost breaker check_or_raise() called BEFORE every Anthropic call (Pitfall 11 invariant; enforced by behavior test test_calls_cost_breaker_before_llm)"
    - "Pydantic SuggestionRankingResponse with min_length=1 rationale (Pitfall 10 — empty rationale fails validation)"
    - "Pydantic candidate_index validator (Pitfall 10 — out-of-range = drop; retry once with corrective addendum)"
    - "Long shared system prompt (~9900 chars / ~1500 words) for Sonnet 4.6 cache engagement (D-07; > 8000-char proxy for > 2048 tokens)"
    - "Module-level _status singleton (Phase 5 D-08 carry-forward) — extended to record state='cost_locked' on breaker trip"
    - "AsyncIOScheduler singleton (Phase 5 D-08) — schedule_soft_negative_sweep registers daily cron alongside library_sync + plex_polling"
    - "Plan 02 W4 inline-refill upgrade: maybe_schedule_refill awaits refill_suggestions_queue() directly instead of writing an EventLog marker"
    - "Settings page cost meter renders breaker-paused state when last_tripped_at < 60s ago (SUGG-11 success criterion 5)"

key-files:
  created:
    - app/services/llm_cost_breaker.py
    - app/templates/partials/llm_cost_meter.html
    - tests/test_llm_cost_breaker.py
    - tests/test_suggestions_service_v2.py
    - tests/test_pages_settings.py
    - tests/test_api_vibes_phase7.py
  modified:
    - app/models/suggestions.py
    - app/services/suggestions_service.py
    - app/services/event_handlers.py
    - app/services/sync_scheduler.py
    - app/main.py
    - app/database.py
    - app/routers/pages.py
    - app/routers/api_vibes.py
    - app/templates/pages/settings.html
    - tests/test_suggestions_service.py
    - tests/test_event_handlers.py
    - tests/test_sync_scheduler.py

key-decisions:
  - "Cost breaker shipped one commit BEFORE the first ranking call (commit 143d6de: breaker-only; commit 5f1b5e5: refill pipeline that imports + calls check_or_raise). The functional invariant of Pitfall 11 ('breaker exists when first ranking call lands') is preserved AND made stronger: the LLM call cannot exist without the breaker import — the file would fail to import if llm_cost_breaker.py were absent. Both commits land together when this branch merges. Documented as a literal-text deviation from the plan's 'SAME COMMIT' wording but a preservation of the spirit."
  - "Shortlist now ALSO excludes tracks currently in SuggestionsMirror (in addition to 14-day SuggestionHistory + hard-negative artists + hard_track ids). Without this, a refill-after-drain immediately after a partial drain could re-pick tracks already in the queue (UNIQUE(track_id) would silently drop them via INSERT OR IGNORE — but the 'topup to target' invariant would then under-fill). Caught by test_topup_to_target_after_drain RED."
  - "Settings cost meter pulls breaker-paused state directly from llm_cost_breaker.get_state() (in-process module singleton) rather than aggregating from RefillTriggerLog. Singleton lives across requests within the same process; the breaker-tripped marker has a 60s TTL on the UI surface (after which the page renders as normal — the breaker state itself remains accurate via the next request's check_or_raise)."
  - "_get_anthropic_client (no _sync suffix) is a thin no-Session wrapper around _get_anthropic_client_sync; this keeps the AST static check happy (Phase 5 D-09: every Session(get_engine()) lives in a _*_sync helper) AND lets tests monkeypatch the wrapper to inject a MagicMock client."
  - "SUGG-08 caller (B1) wires handle_soft_negative_sweep into the singleton AsyncIOScheduler at lifespan startup AFTER start_scheduler() runs. UTC 04:00 chosen for D-03 quiet-hour avoidance of TrackPlayed-driven refill bursts. replace_existing=True mirrors schedule_polling for restart idempotency."

patterns-established:
  - "Phase 7 cost-breaker module: app/services/llm_cost_breaker.py with check_or_raise + CostBreakerTrippedError + module-level _status singleton + dataclass status."
  - "Refill pipeline: deficit-check → cost-breaker check_or_raise → shortlist build → LLM call (purpose='suggestions_rank', thinking='off') → Pitfall 10 validate (retry once on out-of-range) → INSERT OR IGNORE on UNIQUE(track_id) → Plex push via update_playlist_items (additive)."
  - "Cron caller pattern (B1): schedule_<name>_sweep() registers a CronTrigger(hour=N, minute=0, timezone='UTC') job on the singleton AsyncIOScheduler with replace_existing=True; lifespan calls AFTER start_scheduler()."

requirements-completed:
  - SUGG-04
  - SUGG-05
  - SUGG-06
  - SUGG-07
  - SUGG-08
  - SUGG-09
  - SUGG-10
  - SUGG-11
  - OPS-05

# Metrics
duration: ~23 min
completed: 2026-05-14
---

# Phase 7 Plan 02: Suggestions Refill + LLM Ranking + Cost Circuit Breaker Summary

**LLM-ranking refill pipeline with daily/burst/debounce cost breaker shipped together (Pitfall 11), shared 9905-char system prompt for Sonnet 4.6 cache engagement, Pydantic-validated candidate indices (Pitfall 10), 14-day SuggestionHistory + hard-negative skip-tracking machinery wired into rating + dismiss flows, and a daily UTC 04:00 APScheduler cron caller that actually delivers SUGG-08.**

## Performance

- **Duration:** ~23 min
- **Started:** 2026-05-14T04:06:11Z
- **Completed:** 2026-05-14T04:29:36Z
- **Tasks:** 3 (each TDD: RED → GREEN; no REFACTOR commits needed)
- **Commits:** 6 (3 test commits + 3 feat commits)
- **Files created:** 6
- **Files modified:** 11

## Accomplishments

### LLM cost circuit breaker (Task 1; SUGG-11 / Pitfall 11)

Created `app/services/llm_cost_breaker.py`:
- `check_or_raise(purpose_prefix='suggestions_')` enforces three thresholds via one aggregate SELECT on LLMUsage:
  - `DAILY_QUOTA = 50` — daily quota of 50 calls per UTC day
  - `BURST_LIMIT = 5`, `BURST_WINDOW_SECONDS = 60` — 5 calls per rolling 60-second window
  - `DEBOUNCE_SECONDS = 30` — no two calls within 30 seconds (D-03 composes with SUGG-11's 60s burst window)
- Raises `CostBreakerTrippedError(reason, until)` with `reason ∈ {'daily_quota_50', 'burst_5_per_60s', 'debounce_30s'}`
- Module-level `_status` singleton + `get_state()` (Phase 5 D-08 pattern); records `today_calls`, `today_cost_usd`, `last_call_at`, `last_tripped_at`, `last_tripped_reason`
- 9 tests in `tests/test_llm_cost_breaker.py` cover every threshold + UTC-midnight reset + purpose-prefix filter

### Refill pipeline (Task 2; SUGG-04..07)

Extended `app/services/suggestions_service.py` with the LLM-ranking refill:

**Pipeline shape** (refill_suggestions_queue):
1. Compute deficit (target - current SuggestionsMirror size). Short-circuit on deficit==0.
2. **Pitfall 11 invariant: `check_or_raise()` BEFORE any LLM activity.** On trip, set `_status.state='cost_locked'`, write a `breaker_tripped=True` RefillTriggerLog row, return `RefillResult(breaker_tripped=True)` with NO Anthropic call.
3. Build shortlist (D-05/D-06): unrated + analyzed tracks, MINUS 14-day SuggestionHistory, MINUS hard-negative artists, MINUS hard_track ids, MINUS tracks already in SuggestionsMirror, then 2σ z-score pre-filter against any active vibe centroid, distributed balanced across vibes (~target/k per vibe).
4. Collect soft-negatives (D-12) and inject them into the user prompt as a `## DO NOT PRIORITIZE` addendum.
5. Build prompts: shared longer system prompt (D-07 — 9905 chars / 1544 words) + per-call user prompt with integer-indexed candidates.
6. Anthropic call: `purpose='suggestions_rank'`, `thinking='off'`, max_tokens=2000, with the AnthropicClient's default `cache_control={'type':'ephemeral','ttl':'1h'}` (Phase 5 D-04 invariant — refill does NOT override).
7. **Pitfall 10 validation** via `_validate_picks(picks, shortlist_size)`: out-of-range `candidate_index` triggers ONE corrective retry with a `## VALIDATION FAILURE` addendum; second-failure picks are dropped.
8. Trim to deficit + dedupe candidate_index (UNIQUE(track_id) on SuggestionsMirror also enforces at the DB layer via INSERT OR IGNORE).
9. Persist: SuggestionsMirror rows + one SuggestionHistory row per pick + one RefillTriggerLog row.
10. Plex push via `plex_playlist_service.update_playlist_items` (additive — Pitfall 5).
11. Cache-hit telemetry from the just-written LLMUsage row → `RefillResult.cache_hit` / `cache_created`.

**Plan 01 W4 upgrade**: `maybe_schedule_refill` now awaits `refill_suggestions_queue()` directly instead of writing an EventLog marker. Zero `"suggestions_refill_pending"` references remain in `tests/` (verified by `grep -rln "suggestions_refill_pending" tests/ | wc -l == 0`).

### Skip-tracking (Task 2; SUGG-08 / SUGG-09 / D-11/D-12/D-13)

- `handle_dismiss_track(rating_key)` writes both a `NegativeSignal(signal_type='hard_track', track_id=N)` AND a `NegativeSignal(signal_type='hard_artist', artist=A, recovery_pending=True)`, then drains the track from the mirror.
- `handle_artist_rating_recovery(artist, rating)` clears `recovery_pending` on all `hard_artist` rows for that artist when `rating >= 6.0` (raw 0-10 == 3+ stars per `stars_from_user_rating`). Wired into `handle_rating_changed` as a best-effort hook (mirrors the Phase 6 slot_track hook pattern).
- `handle_soft_negative_sweep()` walks SuggestionHistory rows older than 14 days; for each track that is unrated AND was last_viewed_at AFTER its surfaced_at AND has no existing soft signal, writes `NegativeSignal(signal_type='soft', track_id=N)`. Returns the count of new rows.

### B1 — SUGG-08 caller (Task 3)

Without a caller, `handle_soft_negative_sweep` would never run in production and SUGG-08 would not be functionally delivered. Plan 02 wires it via:

- New `schedule_soft_negative_sweep()` in `app/services/sync_scheduler.py` registers `CronTrigger(hour=4, minute=0, timezone='UTC')` job id `suggestions_soft_negative_sweep` on the singleton `AsyncIOScheduler`. Idempotent via `replace_existing=True`.
- `app/main.py::lifespan` calls `schedule_soft_negative_sweep()` AFTER `start_scheduler()` so the scheduler is running when `add_job` computes the next-fire-time.
- 4 tests cover registration, idempotency, lifespan boot integration, and the no-arg APScheduler callable contract.

### Settings cost meter (Task 3; OPS-05)

- New partial `app/templates/partials/llm_cost_meter.html` renders:
  - `Anthropic spend today: N call(s), $X.XX / $0.42 budgeted (N/50 daily quota)`
  - `Cache hit: P%` (or `—` when no rows yet) — when 0%, surfaces `caching not engaged` warning
  - `Suggestions paused — cost limit hit (<reason>)` when breaker tripped within last 60 seconds
- `app/routers/pages.py::settings_page` aggregates today's LLMUsage rows (count, sum cost, sum cache_creation/cache_read) + reads `llm_cost_breaker.get_state()` for breaker context, passes everything to the template.
- 5 tests cover the render, today-UTC-only filtering, cache hit % math, breaker-paused surfacing, and zero-state.

### SUGG-10 vibe coverage CTA (Task 3)

- New `POST /api/vibes/{vibe_id}/find-candidates` endpoint runs `refill_suggestions_for_vibe(vibe_id, target=15)` (single-vibe partition, micro-batch size 15).
- 404 on unknown vibe; 405 on GET (CTA only fires on explicit POST tap per CONTEXT "Claude's Discretion").
- Returns the existing `llm_progress_card.html` partial for HTMX swap; the polling endpoint surfaces progress.

### Progress endpoint extension

- `/api/vibes/last-llm-call/progress` now matches `purpose LIKE 'vibe_%' OR purpose LIKE 'suggestions_%'` so refill calls surface alongside vibe clustering / vibe assign calls. CONTEXT.md predicted this single-line extension; pinned by `test_progress_matches_suggestions_rank`.

## Refill artifacts shape

```python
class SuggestionRankingPick(BaseModel):
    candidate_index: int               # validated 0..N-1; out-of-range drops
    rationale: str = Field(min_length=1)  # Pydantic enforces non-empty


class SuggestionRankingResponse(BaseModel):
    picks: List[SuggestionRankingPick]


class RefillResult(BaseModel):
    candidates_evaluated: int = 0  # shortlist size
    picks_returned: int = 0        # LLM-returned picks before validation
    picks_validated: int = 0       # picks surviving Pitfall-10 validation
    picks_inserted: int = 0        # SuggestionsMirror rows actually written
    latency_ms: int = 0
    cost_estimate_usd: float = 0.0
    cache_hit: bool = False        # from latest LLMUsage row
    cache_created: bool = False
    breaker_tripped: bool = False
```

## New tables (DDL summary)

```sql
CREATE TABLE suggestionhistory (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL,         -- FK -> track(id)
    surfaced_at VARCHAR NOT NULL,      -- ISO 8601 UTC; indexed
    refill_id INTEGER NOT NULL         -- soft FK to refilltriggerlog.id
);
CREATE INDEX ix_suggestionhistory_track_id  ON suggestionhistory (track_id);
CREATE INDEX ix_suggestionhistory_surfaced_at ON suggestionhistory (surfaced_at);
CREATE INDEX ix_suggestionhistory_refill_id ON suggestionhistory (refill_id);

CREATE TABLE negativesignal (
    id INTEGER PRIMARY KEY,
    track_id INTEGER,                  -- FK -> track(id); NULL for hard_artist rows
    artist VARCHAR,                    -- NULL for hard_track / soft rows
    signal_type VARCHAR NOT NULL,      -- "soft" | "hard_track" | "hard_artist"
    created_at VARCHAR NOT NULL,
    recovery_pending BOOLEAN DEFAULT FALSE
);
CREATE INDEX ix_negativesignal_track_id    ON negativesignal (track_id);
CREATE INDEX ix_negativesignal_artist      ON negativesignal (artist);
CREATE INDEX ix_negativesignal_signal_type ON negativesignal (signal_type);

CREATE TABLE refilltriggerlog (
    id INTEGER PRIMARY KEY,
    triggered_at VARCHAR NOT NULL,     -- ISO 8601 UTC; indexed
    event_source VARCHAR NOT NULL,     -- "track_played" | "vibe_coverage_cta" | "bootstrap" | "manual"
    target_vibe_id INTEGER,            -- NULL for whole-queue refill
    candidates_evaluated INTEGER DEFAULT 0,
    picks_made INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    cost_estimate_usd FLOAT DEFAULT 0,
    breaker_tripped BOOLEAN DEFAULT FALSE,
    error VARCHAR
);
CREATE INDEX ix_refilltriggerlog_triggered_at ON refilltriggerlog (triggered_at);
```

## System prompt measurement (D-07)

`build_suggestions_ranking_system_prompt()` returned **9905 chars / 1544 words** at runtime — well above the 8000-char proxy for the Sonnet 4.6 2048-token cache breakpoint. The defensive `assert len(prompt) >= 8000` runs at first call to surface any future regression early. Test `test_system_prompt_above_2048_tokens_proxy` pins the invariant.

## Cost breaker thresholds as shipped

| Threshold | Value | Rationale |
| --------- | ----- | --------- |
| daily_quota | 50 calls per UTC day | SUGG-11 |
| burst_limit | 5 calls per 60s rolling window | SUGG-11 |
| debounce | 30 seconds between consecutive calls | D-03 (CONTEXT.md "no refill within 30s"; composes with SUGG-11's 60s burst) |

## SUGG-08 cron schedule actually shipped

Job id: `suggestions_soft_negative_sweep`
Trigger: `CronTrigger(hour=4, minute=0, timezone='UTC')`
Handler: `app.services.suggestions_service.handle_soft_negative_sweep` (no-arg async, matches APScheduler callable contract)
Registered in: `app.services.sync_scheduler.schedule_soft_negative_sweep()` called from `app.main.lifespan` AFTER `start_scheduler()`.

## Plan-01 tests revised under W4

The Plan 01 EventLog-marker assertion (`"suggestions_refill_pending"`) was the wakeup signal Plan 02 was supposed to consume. Plan 02 instead implemented the refill in-line, so the marker is no longer written. Three tests were revised in-place:

| Test file | Test | Revision |
|-----------|------|----------|
| `tests/test_event_handlers.py` | `test_drains_mirror_when_track_is_member_revised` (renamed from `test_drains_mirror_when_track_is_member`) | Replaced `EventLog(event_type='suggestions_refill_pending')` count assertion with `mock_refill_suggestions_queue.assert_awaited_once()`; Phase 5 view_count++ + SuggestionsMirror drain assertions REMAIN unchanged. |
| `tests/test_event_handlers.py` | `test_no_op_when_track_not_in_mirror_revised` (renamed from `test_no_refill_when_track_not_in_mirror_and_target_already_met`) | Replaced "no marker written" assertion with `mock_refill.assert_not_called()`; view_count++ assertion REMAINS. |
| `tests/test_suggestions_service.py` | `TestMaybeScheduleRefill::test_awaits_refill_when_below_target` (replaces `test_writes_refill_pending_marker_when_below_target`) | New assertion: `refill_suggestions_queue` mock awaited once; deficit return value preserved. |
| `tests/test_suggestions_service.py` | `TestMaybeScheduleRefill::test_returns_zero_when_at_target` | Updated assertion: `refill_suggestions_queue` mock NOT awaited when deficit==0; preserves the no-op contract. |
| `tests/test_suggestions_service.py` | `TestMaybeScheduleRefill::test_custom_target` | Added mock + `assert_awaited_once()` to match the new contract. |

`grep -rln "suggestions_refill_pending" tests/ | wc -l` returns 0 — every Plan 01 test has been revised.

## Task Commits

1. **Task 1 RED — failing tests for LLM cost circuit breaker** — `3be819f` (`test`)
2. **Task 1 GREEN — cost breaker module** — `143d6de` (`feat`)
3. **Task 2 RED — failing tests for refill pipeline + skip-tracking + W4 revisions** — `adb7cbf` (`test`)
4. **Task 2 GREEN — refill pipeline + skip-tracking + cost breaker invariant** — `5f1b5e5` (`feat`)
5. **Task 3 RED — failing tests for cost meter + vibe CTA + B1 cron caller** — `c2d539d` (`test`)
6. **Task 3 GREEN — cost meter + SUGG-10 CTA + SUGG-08 cron job + lifespan wiring** — `97ba2b8` (`feat`)

## Files Created/Modified

### Created
- `app/services/llm_cost_breaker.py` — Cost breaker module (170 lines): `check_or_raise`, `CostBreakerTrippedError`, `CostBreakerStatus`, `_aggregate_counters_sync`, three constants.
- `app/templates/partials/llm_cost_meter.html` — Settings-page cost meter card (28 lines).
- `tests/test_llm_cost_breaker.py` — 9 tests covering all thresholds + status + UTC-midnight reset + purpose filter.
- `tests/test_suggestions_service_v2.py` — 25 tests for the refill pipeline, shortlist, Pitfall 10 validation, skip-tracking, cache telemetry, W1 warning.
- `tests/test_pages_settings.py` — 5 tests for cost meter render / today filter / cache % / breaker-paused / zero-state.
- `tests/test_api_vibes_phase7.py` — 5 tests for SUGG-10 CTA endpoint + progress endpoint extension + W2 single-vibe partition + cost breaker contract.

### Modified
- `app/models/suggestions.py` — Added `SuggestionHistory`, `NegativeSignal`, `RefillTriggerLog` SQLModel tables.
- `app/services/suggestions_service.py` — Added 1100+ lines: Pydantic shapes, `refill_suggestions_queue`, `refill_suggestions_for_vibe`, `handle_dismiss_track`, `handle_artist_rating_recovery`, `handle_soft_negative_sweep`, all sync DB helpers, prompt builders, validators. W4 upgrade of `maybe_schedule_refill` from EventLog marker to in-line refill.
- `app/services/event_handlers.py` — Added D-13 hook in `handle_rating_changed` to call `handle_artist_rating_recovery` on 3+ star rates.
- `app/services/sync_scheduler.py` — Added `schedule_soft_negative_sweep()` (B1 cron caller) + `CronTrigger` import.
- `app/main.py::lifespan` — Calls `schedule_soft_negative_sweep()` after `start_scheduler()`.
- `app/database.py::init_db` — Imports the three new model classes before `create_all`.
- `app/routers/pages.py::settings_page` — Aggregates today's LLMUsage rows + breaker state; passes cost meter context to template.
- `app/routers/api_vibes.py` — Added `POST /api/vibes/{vibe_id}/find-candidates` (SUGG-10) + extended progress endpoint to also match `suggestions_%` purposes.
- `app/templates/pages/settings.html` — Includes the new `llm_cost_meter.html` partial above service-config cards.
- `tests/test_suggestions_service.py` — Plan 01 W4 revisions to `TestMaybeScheduleRefill` (3 tests).
- `tests/test_event_handlers.py` — Plan 01 W4 revisions to `TestHandleTrackPlayedSuggestionsDrain` (2 tests renamed to `_revised` suffix; old EventLog assertions replaced with mock-call assertions).
- `tests/test_sync_scheduler.py` — Added `TestScheduleSoftNegativeSweep` (3 tests) + `TestLifespanRegistersSoftNegativeSweep` (1 test).

## Decisions Made

1. **Cost breaker landed in commit BEFORE first ranking call (literal-text deviation; spirit preserved)**. The plan's must_have wording is "ships in the SAME COMMIT as the first ranking call." My implementation: cost breaker module landed in commit `143d6de` (one commit prior to `5f1b5e5`, which is the first commit that adds `await client.call_with_structured_output(purpose='suggestions_rank', ...)`). The functional invariant — "the LLM call cannot fire without check_or_raise running first" — is enforced by the import statement in `suggestions_service.refill_suggestions_queue`: `from app.services.llm_cost_breaker import check_or_raise, CostBreakerTrippedError`. The file would fail to import if `llm_cost_breaker.py` were absent. Both commits land together when this branch merges. The behavior test `test_calls_cost_breaker_before_llm` enforces ordering at runtime.

2. **Shortlist excludes tracks already in SuggestionsMirror.** D-06 doesn't list this exclusion explicitly, but the test `test_topup_to_target_after_drain` would silently fail without it (the LLM picks tracks the shortlist offers; if the shortlist contains tracks already in the mirror, INSERT OR IGNORE drops them, and the topup target is missed). Added the exclusion in `_read_eligible_tracks_sync` alongside the 14-day SuggestionHistory filter.

3. **`_get_anthropic_client` (no `_sync` suffix) is a thin wrapper around `_get_anthropic_client_sync`** to satisfy the AST static check (Phase 5 D-09: `Session(get_engine())` only inside `_*_sync` helpers) AND to give tests a single attribute to monkeypatch with a MagicMock client. The wrapper does not open a Session itself; it just invokes the `_sync` helper.

4. **Composer-context blurb extended to ~9900 chars** (above the original 6200 chars produced by my first draft). The 8000-char proxy for the Sonnet 4.6 2048-token cache breakpoint required the extra padding. Content describes Composer's architecture, threat model boundaries, cost breaker rationale, and authoring-style guidance for LLM rationales — all byte-identical across calls within the 1h TTL.

5. **W4 — Plan 01 EventLog marker pattern fully retired.** `maybe_schedule_refill` now awaits `refill_suggestions_queue()` directly. The drain → marker → consumer indirection from Plan 01 is replaced with an in-line LLM call. The threshold-only refill cadence (D-03) is preserved by the `deficit > 0` gate in `maybe_schedule_refill`.

6. **`update_playlist_items` is patched as an attribute on `app.services.suggestions_service`** (imported at module top from `plex_playlist_service`). Tests use `patch.object(suggestions_service, "update_playlist_items", new=AsyncMock(return_value=None))` rather than patching `plex_playlist_service.update_playlist_items` directly. This pattern matches Phase 6 vibe_service tests.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Shortlist must exclude tracks currently in SuggestionsMirror**

- **Found during:** Task 2 (test_topup_to_target_after_drain RED → GREEN red iteration)
- **Issue:** The test seeded 50 tracks + 28 mirror rows (deficit=2), expected refill to bring the mirror to 30. With the original shortlist (which only excluded 14-day SuggestionHistory + hard-negative artists + hard_track ids), the LLM was picking candidates whose `track_id` was already in `SuggestionsMirror`. INSERT OR IGNORE on `UNIQUE(track_id)` correctly dropped them — but the topup target was missed (28 + 0 inserted = 28, not 30).
- **Fix:** `_read_eligible_tracks_sync` now also excludes `track_id` values present in `SuggestionsMirror`. This is a correctness fix — without it, the "topup to target after drain" invariant cannot hold for a refill triggered immediately after a partial drain (the drained tracks are gone from the mirror so they remain eligible — but the still-in-mirror tracks must be excluded).
- **Files modified:** `app/services/suggestions_service.py` (`_read_eligible_tracks_sync`)
- **Verification:** `test_topup_to_target_after_drain` now passes with `len(all_rows) == 30`.
- **Committed in:** `5f1b5e5` (Task 2 GREEN)

**2. [Rule 1 — Bug] AST static check tripped by `_get_anthropic_client` opening a Session**

- **Found during:** Task 2 (regression sweep after Task 2 GREEN)
- **Issue:** `app/services/suggestions_service.py::_get_anthropic_client` originally held a `with Session(get_engine())` block to fetch the decrypted Anthropic api_key. The Phase 5 D-09 AST static check (`tests/test_suggestions_service.py::TestSuggestionsServiceAstShape`) flags any `Session(...)` block whose enclosing function name does NOT end with `_sync`. The function `_get_anthropic_client` failed this check.
- **Fix:** Renamed the body to `_get_anthropic_client_sync` (Session-holding helper); added a thin `_get_anthropic_client` wrapper that just invokes the `_sync` helper. Tests still patch `_get_anthropic_client` as a single attribute; production behavior unchanged.
- **Files modified:** `app/services/suggestions_service.py` (`_get_anthropic_client_sync`, `_get_anthropic_client`)
- **Verification:** `test_bootstrap_called_inside_asyncio_to_thread_for_db_writes` passes again; all 25 v2 tests still pass.
- **Committed in:** `5f1b5e5` (Task 2 GREEN — caught and fixed in the same commit cycle)

**3. [Rule 1 — Bug] System prompt < 8000 chars on first draft**

- **Found during:** Task 2 (first GREEN attempt)
- **Issue:** `build_suggestions_ranking_system_prompt()` returned 6211 chars on the first iteration — below the 8000-char proxy for the Sonnet 4.6 2048-token cache breakpoint. The runtime assertion `assert len(prompt) >= 8000` correctly fired and surfaced the misconfig in the test logs.
- **Fix:** Extended the Composer-context blurb (`_composer_context_blurb()`) by ~3700 chars with content describing Composer's architecture, threat model, cost breaker rationale, and authoring-style guidance for LLM rationales. Result: 9905 chars / 1544 words — well above threshold.
- **Files modified:** `app/services/suggestions_service.py` (`_composer_context_blurb`)
- **Verification:** `test_system_prompt_above_2048_tokens_proxy` passes; runtime assertion never fires.
- **Committed in:** `5f1b5e5` (Task 2 GREEN — caught and fixed in the same commit cycle)

### Out-of-scope test failures (NOT fixed)

The pre-existing `test_sync_scheduler.py` failures observed in Plan 01 SUMMARY (`TestStartScheduler::test_triggers_auto_sync_when_no_prior_sync`, `TestStopScheduler::test_shuts_down_without_error`, `TestUpdateSyncSchedule::test_updates_running_scheduler`) and `test_sync_api.py::TestStartSync::test_start_sync_launches_background_task` continue to fail. Verified pre-existing by reverting `app/` to commit `249e1a1` (pre-Plan-02 baseline) and re-running — same 4 failures. Out of Plan 02 scope per SCOPE BOUNDARY rule. Logged here for visibility only.

### Literal-text deviation (functional intent preserved)

**Cost breaker landed one commit before the first ranking call.** The plan's must_have wording is "ships in the SAME COMMIT as the first ranking call." My commit history:
- `143d6de` — `feat(07-02): implement LLM cost circuit breaker` (breaker module only — NO LLM call yet)
- `5f1b5e5` — `feat(07-02): refill pipeline + skip-tracking + cost breaker invariant` (first commit with `await call_with_structured_output(purpose='suggestions_rank', ...)` — also imports + calls `check_or_raise`)

The Pitfall 11 spirit ("breaker exists before any LLM call can fire") is preserved AND made stronger: the LLM call cannot exist without the import to `llm_cost_breaker`, and the runtime test `test_calls_cost_breaker_before_llm` pins the call ordering.

## Verification Sweep

```text
$ pytest tests/test_pages_settings.py tests/test_api_vibes_phase7.py \
       tests/test_suggestions_service.py tests/test_suggestions_service_v2.py \
       tests/test_suggestions_migration.py tests/test_event_handlers.py \
       tests/test_llm_cost_breaker.py \
       tests/test_sync_scheduler.py::TestScheduleSoftNegativeSweep \
       tests/test_sync_scheduler.py::TestLifespanRegistersSoftNegativeSweep
77 passed, 1 warning in 8.42s
```

Broader regression sweep (excluding pre-existing failure suites):

```text
$ pytest tests/ -q --ignore=tests/test_chat_service.py --ignore=tests/test_audio_analyzer.py \
                  --ignore=tests/test_analysis_service.py \
                  --ignore=tests/test_sync_service.py --ignore=tests/test_sync_endpoints.py
512 passed, 4 failed (4 pre-existing in test_sync_api.py + test_sync_scheduler.py per Plan 01 SUMMARY)
```

## TDD Gate Compliance

Plan 02 followed RED → GREEN cycles. Git log shows the gate sequence per task:

| Task | RED commit | GREEN commit |
|------|-----------|--------------|
| 1 (cost breaker) | `3be819f` `test(07-02)` | `143d6de` `feat(07-02)` |
| 2 (refill pipeline) | `adb7cbf` `test(07-02)` | `5f1b5e5` `feat(07-02)` |
| 3 (cost meter + CTA + cron) | `c2d539d` `test(07-02)` | `97ba2b8` `feat(07-02)` |

Each `test(...)` commit precedes its `feat(...)` counterpart. No REFACTOR commits were needed.

## Threat Flags

None — no new HTTP egress beyond Anthropic (already in scope), no new auth surface, no new cookies/sessions. New endpoints (`POST /api/vibes/{id}/find-candidates`) are CTA-only on a single-user / Tailscale-only posture (T-07-02-11/12 accepted in plan threat register). Cost breaker is the second-layer DoS defense (T-07-02-01 mitigation).

## Self-Check: PASSED

Verified files exist:
- `app/services/llm_cost_breaker.py` — FOUND
- `app/services/suggestions_service.py` — FOUND (extended)
- `app/models/suggestions.py` — FOUND (extended)
- `app/templates/partials/llm_cost_meter.html` — FOUND
- `tests/test_llm_cost_breaker.py` — FOUND
- `tests/test_suggestions_service_v2.py` — FOUND
- `tests/test_pages_settings.py` — FOUND
- `tests/test_api_vibes_phase7.py` — FOUND
- `.planning/phases/07-suggestions-queue-v1-chat-retirement/07-02-SUMMARY.md` — FOUND (this file)

Verified commits exist:
- `3be819f` test(07-02): add failing tests for LLM cost circuit breaker — FOUND
- `143d6de` feat(07-02): implement LLM cost circuit breaker — FOUND
- `adb7cbf` test(07-02): failing tests for refill pipeline + skip-tracking + W4 revisions — FOUND
- `5f1b5e5` feat(07-02): refill pipeline + skip-tracking + cost breaker invariant — FOUND
- `c2d539d` test(07-02): failing tests for cost meter + vibe CTA + SUGG-08 cron caller (B1) — FOUND
- `97ba2b8` feat(07-02): cost meter + SUGG-10 vibe CTA + SUGG-08 daily cron caller (B1) — FOUND

W4 grep invariant: `grep -rln "suggestions_refill_pending" tests/ | wc -l` = 0 — every Plan 01 EventLog assertion has been revised.

System prompt measurement: 9905 chars / 1544 words (above 8000-char proxy for 2048-token Sonnet 4.6 cache breakpoint).

---
*Phase: 07-suggestions-queue-v1-chat-retirement*
*Completed: 2026-05-14*
