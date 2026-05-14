---
quick_id: 260514-e6w
type: hotfix
phase: 7
status: complete
completed: 2026-05-14
commit: 482d52e
requirements: [HOTFIX-260514-e6w]
files_modified:
  - app/services/suggestions_service.py
  - tests/test_suggestions_service.py
tags:
  - hotfix
  - phase-7
  - suggestions
  - max-tokens
  - llm-cost
follows_up_with: phase-7.1-cost-architecture
references:
  - .planning/notes/phase-07-followup-cost-architecture.md
  - hotfix-260512-kvs (Phase 6.2 same root-cause class)
---

# Quick Task 260514-e6w: suggestions_rank max_tokens 2000 -> 8000 Hotfix Summary

One-liner: Bumped `max_tokens` budget on every `purpose=suggestions_rank` Anthropic call from `2000` to `8000` via a named module-level constant `SUGGESTIONS_RANK_MAX_TOKENS = 8000`, replacing all four hardcoded literals in `app/services/suggestions_service.py`, plus two regression tests preventing future drift.

## Background

NAS UAT 2026-05-14 16:51 UTC, immediately after the `260514-cdl` bootstrap deadlock fix landed, observed:

```
WARNING: Anthropic response stop_reason=max_tokens for purpose=suggestions_rank
ERROR: refill_suggestions_queue raised during maybe_schedule_refill (deficit=30)
pydantic_core._pydantic_core.ValidationError: 1 validation error for SuggestionRankingResponse
  Invalid JSON: EOF while parsing a string at line 112 column 7
```

`deficit=30` picks plus the shortlist context exceeds the prior `2000`-token output budget. The model hits `stop_reason=max_tokens` mid-response, returns truncated JSON, pydantic validation fails, refill aborts, the suggestions queue cannot bootstrap. Same root-cause class as Phase 6.2 hotfix `260512-kvs` (vibe-mapping bumped 3000 -> 6000 / 4000 -> 8000).

## Changes (single atomic commit `482d52e`)

### 1. New module-level constant in `app/services/suggestions_service.py`

```python
# Phase 7 hotfix 260514-e6w: structured SuggestionRankingResponse JSON for
# deficit=30 picks + shortlist context exceeds the prior 2000 budget and
# triggers stop_reason=max_tokens -> truncated JSON -> pydantic validation
# failure -> refill aborts. Bumped to 8000 (same class as hotfix 260512-kvs
# Phase 6.2 PASS2). Proper architectural fix lives in
# .planning/notes/phase-07-followup-cost-architecture.md (Phase 7.1).
SUGGESTIONS_RANK_MAX_TOKENS = 8000
```

Adjacent to the existing `SUGGESTIONS_TARGET_SIZE` / `SUGGESTIONS_PLAYLIST_NAME` / `PHASE_07_MIGRATION_ID` / `DEFERRED_PLEX_RATING_KEY_SENTINEL` block.

### 2. Four call sites replaced — `max_tokens=2000` -> `max_tokens=SUGGESTIONS_RANK_MAX_TOKENS`

| Plan-stated line (pre-edit) | Post-edit line | Function context | Role |
|---|---|---|---|
| 1301 | **1308** | `refill_suggestions_queue` | initial refill call (the one that bit on UAT) |
| 1337 | **1344** | `refill_suggestions_queue` | retry on invalid `candidate_index` |
| 1502 | **1509** | second suggestions_rank call site | initial call |
| 1520 | **1527** | second suggestions_rank call site | retry on invalid `candidate_index` |

Each line is a `keyword` arg to `client.call_with_structured_output(...)` with `purpose="suggestions_rank"`. Constant insertion at line 58 shifted all four sites down by 7 lines, exactly as the plan's `<done>` predicted.

### 3. Two regression tests in `tests/test_suggestions_service.py`

New class `TestSuggestionsRankMaxTokensHotfix260514E6w` appended after the existing `TestSuggestionsServiceAstShape` class:

| Test name | Mechanism | Catches |
|---|---|---|
| `test_suggestions_rank_max_tokens_constant_pinned` | imports `SUGGESTIONS_RANK_MAX_TOKENS`, asserts `== 8000` and `isinstance(int)` | silent "let me trim it back" regressions on the constant value |
| `test_no_hardcoded_max_tokens_2000_in_suggestions_service` | `ast.parse()` source of `app/services/suggestions_service.py`, walks every `ast.Call` node, asserts no `keyword` with `arg == "max_tokens"` has `ast.Constant(value=2000)` | future drift where someone adds a new `max_tokens=2000` literal anywhere in the module |

The AST approach (mirroring `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async`, lines 292-340) means comments and docstrings mentioning `max_tokens=2000` historically — including the constant's own contextual comment and the test's own docstring — do NOT trip the assertion. Only an actual literal int keyword argument is forbidden.

The required `import ast` and `from pathlib import Path` were already present at the top of the file from the existing `TestSuggestionsServiceAstShape` class — no new module-level imports added.

## Verification

| Check | Result |
|---|---|
| `pytest tests/test_suggestions_service.py -v` | **14 passed** (12 baseline + 2 new), 1 unrelated urllib3/LibreSSL warning. No regressions. |
| `pytest tests/ -k suggestions -v` | **85 passed, 553 deselected** — no Phase 7 regressions across `test_suggestions_service.py`, `test_suggestions_service_v2.py`, `test_suggestions_migration.py`, `test_pages_suggestions.py`. |
| `grep -nE 'max_tokens=2000\b' app/services/suggestions_service.py` | **0 matches** (exit 1) |
| `grep -cE 'max_tokens=SUGGESTIONS_RANK_MAX_TOKENS\b' app/services/suggestions_service.py` | **4** |
| `python -c "from app.services.suggestions_service import SUGGESTIONS_RANK_MAX_TOKENS; assert SUGGESTIONS_RANK_MAX_TOKENS == 8000; print('OK')"` | **OK** |
| `git diff app/services/suggestions_service.py | grep -E "^[+-].*max_tokens"` | 4 removed `max_tokens=2000`, 4 added `max_tokens=SUGGESTIONS_RANK_MAX_TOKENS`, plus the new comment line — exactly as predicted |

## Deviations from Plan

None. Plan executed exactly as written:

- Constant value, name, and comment block added at the documented location.
- All four call sites replaced.
- Two regression tests with the documented names and mechanisms.
- Single atomic commit (per task constraints).
- Strict scope: did NOT touch refill cadence, deficit gate, `maybe_schedule_refill`, `stop_reason=max_tokens` retry-on-truncation, longer-preamble caching engagement, helper extraction, or any other constant. All deferred to Phase 7.1 per `.planning/notes/phase-07-followup-cost-architecture.md`.

## Important Caveat (carried forward from the architecture note)

This hotfix makes the calls **succeed** instead of fail — it does NOT solve the cost problem. Each successful call still costs ~$0.05. With the current deficit-driven refill (`maybe_schedule_refill` fires `refill_suggestions_queue` any time `deficit > 0`), every play of a mirror-member track triggers one LLM call. The daily cost breaker (`DAILY_COST_BUDGET_USD = $0.42`) trips after ~8 plays.

UAT guidance: confirm the `Composer · Suggestions` playlist materializes in Plex after one or two plays, then **stop testing** until the proper architectural fix ships. The proper fix — SQL-refill hot path against pre-computed `TrackVibe.distance` + weekly LLM discovery (Option C) — is captured in `.planning/notes/phase-07-followup-cost-architecture.md` and will land as a separate Phase 7.1 effort (`/gsd-spec-phase 7.1` -> `/gsd-plan-phase 7.1` -> `/gsd-execute-phase 7.1`).

## Self-Check: PASSED

- [x] `app/services/suggestions_service.py` exists and contains `SUGGESTIONS_RANK_MAX_TOKENS = 8000`
- [x] `tests/test_suggestions_service.py` exists and contains class `TestSuggestionsRankMaxTokensHotfix260514E6w` with both new test methods
- [x] Commit `482d52e` exists in `git log --oneline --all`
- [x] All four call sites use `max_tokens=SUGGESTIONS_RANK_MAX_TOKENS` (verified via grep, count = 4)
- [x] Zero remaining `max_tokens=2000` literals (verified via grep, exit 1)
- [x] Full `pytest tests/test_suggestions_service.py -v` exits 0 (14 passed)
- [x] Smoke check `pytest tests/ -k suggestions -v` exits 0 (85 passed)
