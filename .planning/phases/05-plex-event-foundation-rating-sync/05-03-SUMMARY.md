---
phase: 05-plex-event-foundation-rating-sync
plan: 03
subsystem: anthropic-client-and-taste-profile
tags:
  - anthropic-sdk
  - prompt-caching
  - taste-profile
  - llm-usage-logging
  - circuit-breaker-scaffolding
  - tdd
dependency-graph:
  requires:
    - 05-01  # LLMUsage + TasteProfile tables; event_handlers + handle_rating_changed
    - 05-02  # event_handlers extended (handle_track_played); shared file
  provides:
    - app.services.anthropic_client.{AnthropicClient, get_anthropic_client_v2, SONNET_4_6_CACHE_MIN_TOKENS}
    - app.services.taste_profile_service.{recompute, maybe_recompute_after_rating_change, get_current_profile, TasteProfileSummary}
    - app.services.event_handlers.handle_rating_changed (extended with recompute hook)
  affects:
    - LLMUsage table (writes one row per call; previously empty after Plan 01)
    - TasteProfile single-row id=1 (populates summary_text + structured aggregates; previously default-empty after Plan 01)
tech-stack:
  added: []  # anthropic>=0.100,<1.0 already in requirements.txt from Plan 01 (D-20)
  patterns:
    - AsyncAnthropic SDK + explicit cache_control={"type":"ephemeral","ttl":"1h"} on system message
    - Pydantic.model_validate_json structured-output parsing (D-03; NO Instructor)
    - Markdown-fence stripping for Sonnet's tendency to wrap JSON in ```json blocks
    - Per-call LLMUsage row via asyncio.to_thread (D-09 invariant maintained)
    - >=2048-token padded system prompt to engage Sonnet 4.6's prompt cache (Pitfall 4)
    - 4-D centroid via numpy.mean — NO scikit-learn (clustering = Phase 6; AST test enforces)
    - Single-row id=1 upsert pattern for TasteProfile (Pattern 5)
    - >=10% accumulated rated-set delta trigger from event handler (D-18)
key-files:
  created:
    - app/services/anthropic_client.py (179 lines)
    - app/services/taste_profile_service.py (315 lines)
    - tests/test_anthropic_client.py (223 lines)
    - tests/test_taste_profile_service.py (307 lines)
  modified:
    - app/services/event_handlers.py (+15 lines — handle_rating_changed recompute hook)
decisions:
  - cache_control TTL is "1h" EXPLICITLY (Pitfall 4 / OPS-02) — never rely on the silently regressed 5-min default
  - Structured output via Pydantic.model_validate_json (D-03); Instructor REMOVED — fights prompt caching
  - LLMUsage table is the audit log; circuit-breaker counters computed via aggregate SELECT on demand
  - Phase 5 ships NO dedicated counter columns; Phase 7 may refactor to O(1) if profiling demands
  - 4-D centroid is a single numpy.mean — NOT k-means (clustering = Phase 6)
  - Recompute trigger uses a true accumulation: prior.rated_track_count snapshot vs current count, not a per-event delta
  - Recompute is best-effort: LLM failure persists structured aggregates with empty summary_text rather than crashing
  - Phase 5 default model is claude-sonnet-4-6; pricing constants documented at top of anthropic_client.py
metrics:
  duration: ~1h
  tasks-completed: 3 / 3
  tests-added: 11 (all green — 6 anthropic_client + 5 taste_profile)
  tests-pre-existing-fail: 17 (unchanged baseline — no regressions)
  files-changed: 5
  lines-added: 1024
  lines-removed: 1
completed: 2026-05-09
---

# Phase 5 Plan 03: AnthropicClient + Full TasteProfile Summary

End-to-end LLM infrastructure for v2.0: a NEW Anthropic SDK client (`anthropic_client.py` — separate from v1 `llm_client.py` per D-01) with explicit `cache_control={"type":"ephemeral","ttl":"1h"}` on the system message + per-call LLMUsage logging, plus the full taste profile service that computes a 4-D centroid + top artists/genres + ~200-word LLM-generated summary text and upserts the single-row TasteProfile (RATE-05 in entirety per D-17). The dispatcher fires `maybe_recompute_after_rating_change` after each Track rating update; the hook accumulates rated-set delta and triggers `recompute` when the change crosses 10% of the prior baseline (D-18). After this plan the prompt-caching infrastructure is verified end-to-end via mocked SDK assertions; Phase 6 (vibe naming) and Phase 7 (suggestions ranking) inherit it known-working.

## Tasks Completed

### Task 1: Wave 0 — failing test scaffolds
**Commit:** `76583c2`

Created 2 new test files with 11 collected tests; all initially RED (services don't exist).

- `tests/test_anthropic_client.py` NEW — 6 mock-SDK tests:
  - `test_explicit_ttl_1h` (the OPS-02 / Pitfall 4 invariant assertion).
  - `test_logs_usage_to_llmusage_table` (per-call row insertion with all 4 token counts + cost > 0).
  - `test_warns_when_caching_not_engaged` (logger.warning fires when cache_creation==0 AND cache_read==0).
  - `test_parses_pydantic_response` (model_validate_json round-trip; no Instructor).
  - `test_strips_markdown_fences` (Sonnet's `\`\`\`json ... \`\`\`` wrap pattern).
  - `test_factory_raises_when_not_configured` (ValueError on missing settings).
- `tests/test_taste_profile_service.py` NEW — 5 tests:
  - `test_recompute` (4 rated tracks → centroid + top artists + top genres + summary_text + computed_at).
  - `test_recompute_no_rated_tracks` (early return; no Anthropic call).
  - `test_maybe_recompute_after_rating_change_below_threshold` (9 insertions over 100 baseline = 9% < 10% → no recompute; off-by-one boundary).
  - `test_maybe_recompute_after_rating_change_at_threshold` (10th insertion crosses threshold → recompute called exactly once).
  - `test_no_sklearn_import` (static AST scan — clustering is Phase 6 only).

Verified RED: `python -m pytest tests/test_anthropic_client.py tests/test_taste_profile_service.py --collect-only` → 11 tests collected, no errors. Initial run: 11 failed with `ModuleNotFoundError: No module named 'app.services.anthropic_client'` etc.

### Task 2: AnthropicClient — SDK wrapper with prompt caching + LLMUsage logging
**Commit:** `6849cf2`

Made all 6 anthropic_client tests GREEN.

- **`app/services/anthropic_client.py` NEW** (179 lines) — single `AnthropicClient` class wrapping `AsyncAnthropic` with:
  - `call_with_structured_output(system_prompt, user_prompt, response_model, max_tokens=1024, purpose="unspecified")` builds a `messages.create` call with the system message wrapped in `[{"type":"text", "text":..., "cache_control":{"type":"ephemeral", "ttl":"1h"}}]`. The explicit `ttl="1h"` defeats the silent regression to 5-min default (March 2026 / Pitfall 4 / OPS-02).
  - Pydantic structured output via `response_model.model_validate_json(text)` (D-03 — no Instructor; Instructor reformats system messages and fights prompt caching).
  - Markdown-fence stripping handles Sonnet's tendency to wrap JSON in `\`\`\`json ... \`\`\`` blocks.
  - `_log_usage` inserts one LLMUsage row per call via `asyncio.to_thread` with all 4 token counts + cost_estimate_usd computed from Sonnet 4.6 May-2026 pricing constants.
  - Logger.warning when `cache_creation_input_tokens == 0 AND cache_read_input_tokens == 0` — catches prompts below the 2048-token Sonnet 4.6 minimum.
- **`get_anthropic_client_v2(session)` factory** — mirrors v1's `get_anthropic_client` settings access pattern but returns an `AnthropicClient` instance with the new SDK. Defaults to `claude-sonnet-4-6`; raises `ValueError` if Anthropic isn't configured.
- **DESIGN NOTE** in module docstring documents Phase 7 forward-compat: circuit-breaker counters (daily_calls, burst_window_calls, last_call_at) are computed via aggregate SELECT against LLMUsage on demand. Phase 5 schema is sufficient for Phase 7's correctness; refactor to O(1) counter access only if profiling demands.
- Constants: `SONNET_4_6_CACHE_MIN_TOKENS = 2048`, plus `PRICING_*` per-million-token rates.

**Verification:** `pytest tests/test_anthropic_client.py -x` → 6/6 GREEN. Phase 5 cumulative tests (Plan 01 + 02 + this) all still green.

### Task 3: TasteProfileService + recompute hook in handle_rating_changed
**Commit:** `ff5d951`

Made all 5 taste_profile tests GREEN; extended event_handlers.handle_rating_changed.

- **`app/services/taste_profile_service.py` NEW** (315 lines):
  - `_aggregate_rated_set_sync()` queries `Track.user_rating > 0`; computes the 4-D centroid via `numpy.mean` over (energy, tempo, danceability, valence) — skipping rows with any None feature; top 10 artists via `collections.Counter`; top 10 genres (split comma-separated genre strings); a top-50 track list for the LLM prompt.
  - `_upsert_taste_profile_sync()` performs the single-row id=1 upsert (Pattern 5).
  - `_build_system_prompt()` builds a >2048-token system prompt padded with real Composer context (architecture, conventions, audio-feature definitions, event-bus design) — the padding is identical across calls within 1h TTL, which is what enables the cache to work (Pitfall 4).
  - `recompute(triggered_by="manual")` — full pipeline: aggregate, call `AnthropicClient.call_with_structured_output(purpose="taste_profile_summary")` with `TasteProfileSummary(BaseModel)` as the response shape, upsert. Best-effort: `try/except Exception` around the LLM call persists structured aggregates with empty `summary_text` rather than crashing if Anthropic fails (T-05-16 mitigation).
  - `maybe_recompute_after_rating_change()` — D-18 trigger: queries the current rated count vs `prior.rated_track_count` snapshot; fires `recompute` when `abs(current - prior) / max(prior, 1) >= 0.10`. Accumulates naturally across events because each `RatingChanged` updates a Track row before this hook runs.
  - `get_current_profile()` — sync read accessor for the single-row id=1 profile.
  - `TasteProfileSummary(BaseModel)` — the response shape passed to `AnthropicClient`.
  - **NO scikit-learn import** — clustering is Phase 6 (RESEARCH Anti-Patterns); static AST test enforces.
- **`app/services/event_handlers.py`** — `handle_rating_changed` extended (existing function — Plan 02 already touched the file with `handle_track_played`; this plan touches a different function, no conflict). After `_update_track_rating_sync`, `maybe_recompute_after_rating_change` is called via lazy import + `try/except`. Lazy import avoids a circular-dep risk via `anthropic_client → settings_service → ...`. Best-effort: never let recompute failure break the rating-update path.

**Verification:** `pytest tests/test_taste_profile_service.py -x` → 5/5 GREEN. `pytest tests/test_event_handlers.py -x` → 10/10 GREEN (existing tests unaffected by the hook addition).

## Verification Commands Run

| Command | Result |
|---------|--------|
| `pytest tests/test_anthropic_client.py tests/test_taste_profile_service.py --collect-only` | 11 tests collected, no errors |
| `pytest tests/test_anthropic_client.py tests/test_taste_profile_service.py --tb=line` (Task 1 RED check) | 11 failed (no service modules — TDD ready) |
| `pytest tests/test_anthropic_client.py -x` (Task 2) | 6 passed |
| `pytest tests/test_taste_profile_service.py -x` (Task 3) | 5 passed |
| `pytest tests/test_event_handlers.py -x` (regression check after extending handle_rating_changed) | 10 passed |
| `python -c "from app.services.anthropic_client import AnthropicClient, get_anthropic_client_v2, SONNET_4_6_CACHE_MIN_TOKENS; print(SONNET_4_6_CACHE_MIN_TOKENS)"` | "2048" (smoke import) |
| `pytest tests/ --tb=line -q` (full suite, regression sanity) | **220 PASS, 17 FAIL** — same 17 pre-existing failures as the Plan 01 / 02 baseline (no regressions; +11 vs Plan 02's 209) |
| `grep -c '"ttl": "1h"' app/services/anthropic_client.py` | 1 (the OPS-02 invariant) |
| `grep -v '^[[:space:]]*#' app/services/taste_profile_service.py \| grep -v '"""' \| grep -cE "import sklearn\|from sklearn"` | 0 (Phase 5 invariant — clustering is Phase 6) |
| `grep -c "asyncio.to_thread" app/services/anthropic_client.py app/services/taste_profile_service.py` | 4 (D-09 invariant maintained) |
| `grep -c "model_validate_json" app/services/anthropic_client.py` | 3 (D-03 invariant — Pydantic, no Instructor) |
| AST scan (verified at runtime): `instructor` module imports in anthropic_client.py | 0 (D-03 invariant) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `getattr` on Track audio-feature fields was unnecessary defensive code**
- **Found during:** Task 3 first run.
- **Issue:** The plan's example code used `getattr(t, "tempo", None)` etc. on Track instances when computing the centroid. Track is a SQLModel — those columns ARE attributes (verified via `app/models/track.py` lines 28-30); accessing them directly returns None when unset, no AttributeError. The defensive `getattr` was safe but added noise.
- **Fix:** Used direct attribute access `t.energy, t.tempo, t.danceability, t.valence` for the feature tuple. Cleaner, same behavior; matches the rest of the file's style (which already used `t.user_rating`, `t.artist`, `t.genre` directly).
- **Files modified:** `app/services/taste_profile_service.py`
- **Commit:** `ff5d951` (rolled into the same feature commit)

**2. [Rule 1 - Bug] `get_anthropic_client_v2` patch path mismatch with monkeypatched name**
- **Found during:** Task 3 first run of `test_recompute`.
- **Issue:** The test patches `app.services.taste_profile_service.get_anthropic_client_v2`. If `recompute()` did `from app.services.anthropic_client import get_anthropic_client_v2` at call time, the patch wouldn't apply (patching the source module, not the consumer). The plan's example used a function-local import.
- **Fix:** Added a module-level `get_anthropic_client_v2(session)` shim in `taste_profile_service.py` that lazy-imports the real factory inside its body. `recompute()` now calls the shim — which IS patchable at the consumer-module path. This avoids both the circular-import risk AND keeps the test contract clean. Functionally identical for production callers.
- **Files modified:** `app/services/taste_profile_service.py`
- **Commit:** `ff5d951`

### Authentication Gates
None encountered. All Anthropic calls are mocked in tests; no real API key needed.

### Pre-existing Issues (Out of Scope)
- 17 pre-existing test failures unchanged from Plan 01 / Plan 02 baseline — same set of v1 chat / Ollama / sync flakes. None touch files this plan modifies.
- `docker-compose.yml` is in `M` state from a prior session; flagged for the user but not touched by this plan.

## Phase 5 Cumulative Test Pass Rate

- After Plan 01: 50 new Phase 5 tests passing (193 total).
- After Plan 02: 50 + 16 = 66 new Phase 5 tests passing (209 total).
- After Plan 03: 66 + 11 = **77 new Phase 5 tests passing** (220 total).
- Pre-existing v1 baseline (17 fails) unchanged across all three waves.
- Full suite: **220 PASS, 17 FAIL** (no regressions).

## Phase 5 Carry-Forward Notes (for Plan 04 / Phase 6 / Phase 7)

### LLMUsage schema sufficiency for Phase 7 circuit breaker (CONFIRMED)
LLMUsage now writes one row per Anthropic call with all 4 token counts + cost_estimate_usd. Phase 7's cost circuit breaker can compute counters via aggregate SELECT against this table on demand:
```sql
-- daily_calls
SELECT COUNT(*) FROM llmusage WHERE called_at >= ? -- last 24h ISO
-- burst_window_calls (e.g., last hour)
SELECT COUNT(*) FROM llmusage WHERE called_at >= ? -- last 1h ISO
-- daily_cost
SELECT SUM(cost_estimate_usd) FROM llmusage WHERE called_at >= ?
```
**Phase 5 schema is sufficient for Phase 7 CORRECTNESS without any schema changes.** The DESIGN NOTE in `app/services/anthropic_client.py` (top of file) documents this contract.

### Phase 7 may need to refactor counter access to O(1) — performance only, not correctness
The aggregate-SELECT pattern is fine for Phase 5's actual call volume (~1 recompute/week realistically — gated by D-18's >=10% threshold + a single-user listening cadence). Phase 7's suggestions ranking will exercise the breaker on a much hotter path (potentially per-event). If profiling shows the per-event aggregate SELECT is too slow:
- **Option A:** Add a dedicated `CircuitBreakerState` table with counter columns (`daily_calls`, `burst_window_calls`, `last_reset_at`); rebuild from LLMUsage at startup. Single-row pattern (Pattern 5).
- **Option B:** In-memory counter cache rebuilt from LLMUsage at app startup; protected by an asyncio.Lock for concurrent updates. No new DB schema.
- **Option C:** Compound index on `LLMUsage.called_at` (already indexed) might be enough — measure first.

The decision is purely performance-driven; Phase 5's schema doesn't lock anything in.

### TasteProfile.summary_text is now populated end-to-end
Plan 01 shipped the table with `summary_text=""` placeholder; this plan's `recompute()` populates it via the LLM call. Plan 04 may surface the current TasteProfile in the `/debug/events` page footer (or settings page) — `get_current_profile()` is the read accessor. Re-renders cheap (single-row read).

### Recompute trigger fires from rating events ONLY (Phase 5)
`maybe_recompute_after_rating_change` is the only entry point in Phase 5. Future scheduled recomputes (e.g., post-sync, on-demand) can call `recompute()` directly with a different `triggered_by` label for cost attribution in LLMUsage. Phase 5 doesn't need them.

### `app/services/llm_client.py` UNTOUCHED
v1 chat code remains. D-01 retirement happens in Phase 7 atomically with `app/services/chat_service.py` and the chat UI removal. Plan 04 (final Phase 5 plan) does NOT touch llm_client.py either.

### Plan 04 carry-forwards from Plan 02 still pending
- Settings page "Resync now" button surface — Plan 04 should embed `partials/backfill_banner.html` into the settings page.
- `/debug/events` page surface for poll info + EventLog last-50 (DEBUG-01).
- `app/services/ollama_client.py` deletion (D-02) + corresponding test removal in `tests/test_service_clients.py::TestOllamaClient`.
- TasteProfile read on `/debug/events` footer (this plan provides `get_current_profile`).
- Manual verification on real Plex deployment (Plan 02 + Plan 04 checklist).

## Self-Check: PASSED

**Files (all created/modified files exist on disk):**
- `app/services/anthropic_client.py` — FOUND (179 lines)
- `app/services/taste_profile_service.py` — FOUND (315 lines)
- `tests/test_anthropic_client.py` — FOUND (223 lines)
- `tests/test_taste_profile_service.py` — FOUND (307 lines)
- `app/services/event_handlers.py` (modified) — FOUND (handle_rating_changed extended)

**Commits (all 3 task commits exist):**
- `76583c2` — `test(05-03): add Wave 0 test scaffolds for anthropic_client + taste_profile_service`
- `6849cf2` — `feat(05-03): AnthropicClient v2 with explicit ttl=1h cache + LLMUsage logging`
- `ff5d951` — `feat(05-03): TasteProfileService — centroid + LLM summary + recompute hook`

**Behavior (final test pass):**
- 11/11 new Phase 5 Plan 03 tests GREEN (6 anthropic_client + 5 taste_profile)
- 0 regressions vs pre-existing baseline (17 failures unchanged)
- D-09 invariant maintained: 4 `asyncio.to_thread` calls across `anthropic_client.py + taste_profile_service.py`
- D-03 invariant maintained: 0 `instructor` module imports anywhere (verified via AST scan)
- OPS-02 / Pitfall 4 invariant maintained: explicit `cache_control={"type":"ephemeral","ttl":"1h"}` on every system message
- D-17 invariant maintained: full taste profile (centroid + top artists/genres + summary_text + computed_at) populates from a populated rated set
- D-18 invariant maintained: recompute fires exactly once when accumulated delta crosses 10% (boundary off-by-one robust per the threshold tests)
- RESEARCH Anti-Pattern invariant maintained: 0 sklearn imports in `taste_profile_service.py` (static AST test enforces)
- D-01 invariant maintained: `app/services/llm_client.py` and `app/services/chat_service.py` UNTOUCHED (`git diff` confirms)
