---
quick_task: 260512-kvs
slug: hotfix-2-remove-3-cost-cap-warning-bump-max-tokens
type: quick
phase: quick-260512-kvs-hotfix
plan: 01
requirements: [HOTFIX-260512-KVS]
tags: [phase-6.2, vibe-clustering, llm, anthropic, hotfix, ux]
dependency_graph:
  requires:
    - app/services/vibe_clusterer.py (existing constants + assign_tracks_to_user_vibes)
    - app/routers/pages.py (existing debug_vibes route + cost panel context)
    - app/templates/pages/debug_vibes.html (existing cost panel section)
  provides:
    - Pass 1 max_tokens=6000 / Pass 2 max_tokens=8000 (NAS UAT crash fix)
    - /debug/vibes cost panel with no soft warning state
  affects:
    - tests/test_vibe_clusterer.py (test_pass2_max_tokens_uses_constant now constant-driven)
    - tests/test_pages_debug_vibes.py (warning-threshold test inverted to match hotfix intent)
tech_stack:
  added: []
  patterns:
    - Test assertions pinned to module constants (vc.PASS2_MAX_TOKENS) instead of magic numbers — survives future bumps
    - Display-only diagnostic flags drop cleanly from route context + template without schema change
key_files:
  created: []
  modified:
    - app/services/vibe_clusterer.py (constants 6000/8000; docstring cleanup)
    - app/routers/pages.py (drop vibe_cost_warning from route context)
    - app/templates/pages/debug_vibes.html (drop conditional border + warning paragraph)
    - tests/test_vibe_clusterer.py (pin test to PASS2_MAX_TOKENS constant)
    - tests/test_pages_debug_vibes.py (assert warning NEVER renders, even over $3.00)
    - .planning/phases/06.2-llm-direct-vibe-assignment/deferred-items.md (log new pre-existing analysis_service failure)
decisions:
  - [Hotfix-260512-kvs]: PASS1_MAX_TOKENS bumped 3000→6000 and PASS2_MAX_TOKENS bumped 4000→8000 after NAS UAT observed stop_reason=max_tokens with only a `thinking` block returned (no `text`). Sonnet 4.6 with thinking.budget_tokens=2000 + ~4000 JSON output exceeds 6000; 8000 gives headroom. Preamble cap (600) unchanged.
  - [Hotfix-260512-kvs]: /debug/vibes $3.00 cost-ceiling warning was a soft display flag (not a circuit breaker) and is removed per user request. Cost-by-purpose breakdown and total still render; the conditional red border and warning paragraph were the only deletions.
  - [Hotfix-260512-kvs]: Cost-cap removal also strips the obsolete "$3.00 cost ceiling" sentence from assign_tracks_to_user_vibes docstring; the "/debug/vibes surfaces the cost breakdown by purpose (D-32)" visibility note is preserved.
  - [Hotfix-260512-kvs]: Regression tests pinned to magic numbers (max_tokens=4000) or to the now-removed warning text were rewritten under deviation Rule 1 (auto-fix bug — wrong-by-design after hotfix). The bumped constants and the absent warning text are the intended behavior, and the tests now assert against module constants / inverse conditions.
metrics:
  duration_min: 18
  completed: 2026-05-12T22:24:02Z
  commits:
    - 0bae8b4 (fix(vibes): bump Pass1/Pass2 max_tokens; drop stale $3 ceiling from docstring)
    - 709685a (fix(debug): remove $3 vibe-cost warning from /debug/vibes)
    - 22687fc (test(vibes): align debug_vibes warning test with hotfix 260512-kvs)
---

# Quick Task 260512-kvs: Hotfix #2 — Bump max_tokens + Remove $3 Cost-Cap Warning Summary

Bundles two tightly-coupled Phase 6.2 NAS UAT hotfixes: (a) bump Pass 1 / Pass 2 `max_tokens` so the Anthropic call stops truncating with `stop_reason=max_tokens`, and (b) remove the $3.00 cost-cap soft warning from `/debug/vibes` per explicit user request (display flag only, not a circuit breaker).

## What changed

### Task 1 — `app/services/vibe_clusterer.py`

1. `PASS1_MAX_TOKENS`: `3000` → `6000` (2× headroom for 25-track Pass 1 batches).
2. `PASS2_MAX_TOKENS`: `4000` → `8000` (covers `thinking.budget_tokens=2000` + ~4000 JSON output on Sonnet 4.6).
3. `PREAMBLE_MAX_TOKENS`: unchanged at `600` (one short call per re-cluster).
4. Removed the obsolete "Cost ceiling (D-32): … must be ≤ $3.00 (target ≤ $1.50)" sentence from `assign_tracks_to_user_vibes` docstring. Kept the "/debug/vibes surfaces the cost breakdown by purpose (D-32)" visibility note.
5. Updated `tests/test_vibe_clusterer.py::test_pass2_max_tokens_uses_constant` (renamed from `test_pass2_max_tokens_4000`) to read `vc.PASS2_MAX_TOKENS` instead of the literal `4000` — future-proofs the test against the next bump.

**Commit:** `0bae8b4`

### Task 2 — `app/routers/pages.py` + `app/templates/pages/debug_vibes.html`

1. Route: deleted the `vibe_cost_warning = vibe_cost_total > 3.0` computation and the corresponding `"vibe_cost_warning": vibe_cost_warning` entry in the `TemplateResponse` context. `vibe_cost_by_purpose` and `vibe_cost_total` are still in the context (panel renders unchanged).
2. Template: replaced the conditional `{% if vibe_cost_warning %}border-error{% else %}border-border{% endif %}` with a fixed `border-border` class. Deleted the entire `{% if vibe_cost_warning %} <p>⚠ Vibe LLM cost exceeds the $3.00 acceptance ceiling…</p> {% endif %}` block. Updated the Jinja doc comment to drop "and a warning state when the total exceeds $3.00 (acceptance ceiling per D-32)".

**Commit:** `709685a`

### Task 3 — Regression-test alignment (deviation, see below)

1. `tests/test_pages_debug_vibes.py::test_debug_vibes_cost_total_warning_threshold` renamed to `test_debug_vibes_no_cost_warning_regardless_of_total` and inverted: it now asserts the warning text + `border-error` class are NEVER present, even when the total exceeds $3.00. The panel itself (totals, per-purpose rows) is still verified.
2. `.planning/phases/06.2-llm-direct-vibe-assignment/deferred-items.md` updated to log `tests/test_analysis_service.py::test_skips_oversized_files` as a newly-observed pre-existing failure (MagicMock leak into SQLite bind — confirmed independent of this hotfix).

**Commit:** `22687fc`

## Verification

- **Scoped tests (Task 1 gate):** `tests/test_anthropic_client.py` + `tests/test_vibe_clusterer.py` → 66 passed.
- **Smoke import (Task 2 gate):** `python -c "from app.routers import pages; print('ok')"` → ok.
- **Full regression suite (Task 3 gate):** 428 passed, 0 failures, 2 unrelated warnings. Suite ignores: `test_audio_analyzer`, `test_chat_service`, `test_sync_api`, `test_sync_scheduler`, `test_sync_service`, plus the newly-deferred `test_analysis_service` (logged to `deferred-items.md` with proof of non-causation).
- **End-to-end greps:**
  - `PASS1_MAX_TOKENS = 6000`, `PASS2_MAX_TOKENS = 8000`, `PREAMBLE_MAX_TOKENS = 600` all present in `vibe_clusterer.py`.
  - `vibe_cost_warning` grep count: 0 in `app/routers/pages.py`, 0 in `app/templates/pages/debug_vibes.html`.
  - `acceptance ceiling | $3.00 | border-error` grep count: 0 in `app/templates/pages/debug_vibes.html`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] `test_pass2_max_tokens_4000` pinned the prior magic number**
- **Found during:** Task 1 scoped test run.
- **Issue:** The plan's `<context>` claimed `grep -n 'PASS1_MAX_TOKENS\|PASS2_MAX_TOKENS\|PREAMBLE_MAX_TOKENS' tests/test_vibe_clusterer.py → no hits` — that grep missed `assert all(t == 4000 for t in pass2_tokens)` (a hard-coded literal, not the constant name). When `PASS2_MAX_TOKENS` flipped to `8000`, this regression test failed against the new value, which the hotfix specifically targets.
- **Fix:** Updated the test to read `vc.PASS2_MAX_TOKENS` from the module instead of asserting the literal `4000`. Renamed `test_pass2_max_tokens_4000` → `test_pass2_max_tokens_uses_constant` to reflect intent. Rolled into the Task 1 commit since it's part of the same logical change.
- **Files modified:** `tests/test_vibe_clusterer.py`
- **Commit:** `0bae8b4`

**2. [Rule 1 — Bug] `test_debug_vibes_cost_total_warning_threshold` asserted warning text the hotfix removes**
- **Found during:** Task 3 full suite run.
- **Issue:** The plan's `<context>` claimed `grep -rn 'vibe_cost_warning' tests/ → no hits` — that grep used the **variable** name but the test asserted on the **warning STRING** (`"exceeds the $3.00 acceptance ceiling"`). The test was wrong-by-design after the hotfix: it expects the warning to appear when total > $3.00, which is exactly the behavior the user asked to remove.
- **Fix:** Inverted the assertion to verify the warning text and `border-error` class are NEVER present, even when total > $3.00. Test still validates that the cost breakdown + total appear (panel itself unchanged).
- **Files modified:** `tests/test_pages_debug_vibes.py`
- **Commit:** `22687fc`

**3. [Out-of-scope — logged, NOT fixed] `tests/test_analysis_service.py::test_skips_oversized_files` MagicMock leak**
- **Found during:** Task 3 full suite run.
- **Issue:** A MagicMock for `extract_features()` leaks into a SQLite UPDATE binding for `musical_key`/`scale`, raising `InterfaceError: Error binding parameter 4`. Test expects the oversized-file branch to skip `extract_features` entirely, but the production code calls it anyway.
- **Disposition:** **Out of scope.** Hotfix 260512-kvs touches only `app/services/vibe_clusterer.py`, `app/routers/pages.py`, `app/templates/pages/debug_vibes.html`, plus their tests. `analysis_service.py` and its tests are unrelated. Confirmed pre-existing by diffing files modified (`git diff --name-only 8bff40e..HEAD` shows zero overlap with the analysis service module/tests).
- **Action:** Added to `.planning/phases/06.2-llm-direct-vibe-assignment/deferred-items.md` (commit `22687fc`) with a triage note. Ignored in the regression run.

### No-op deviations
None — Task 2's verifications passed first try.

## Authentication Gates
None — this hotfix is purely local code + test edits with no external API calls.

## Known Stubs
None.

## Threat Flags
None — the changes are display-only (no new endpoints, no auth changes, no schema changes, no trust-boundary moves). The removed $3.00 warning was a soft display flag, not a security or cost-enforcement control.

## TDD Gate Compliance
N/A — this plan is `type: execute`, not `type: tdd`. The two `fix(...)` commits + one `test(...)` commit together follow the spirit of "tests track implementation" — the `test(vibes)` commit retroactively aligns regression tests with the deliberately-changed behavior, not the other way around.

## Self-Check: PASSED

- `app/services/vibe_clusterer.py` — FOUND, contains `PASS1_MAX_TOKENS = 6000`, `PASS2_MAX_TOKENS = 8000`, `PREAMBLE_MAX_TOKENS = 600`, no `$3.00` / `acceptance ceiling` strings.
- `app/routers/pages.py` — FOUND, `vibe_cost_warning` removed; `vibe_cost_by_purpose` and `vibe_cost_total` present.
- `app/templates/pages/debug_vibes.html` — FOUND, no `vibe_cost_warning`, no `acceptance ceiling`, no `$3.00`, no `border-error`.
- `tests/test_vibe_clusterer.py` — FOUND, `test_pass2_max_tokens_uses_constant` reads `vc.PASS2_MAX_TOKENS`.
- `tests/test_pages_debug_vibes.py` — FOUND, `test_debug_vibes_no_cost_warning_regardless_of_total` asserts inverse.
- `.planning/phases/06.2-llm-direct-vibe-assignment/deferred-items.md` — FOUND, `test_skips_oversized_files` entry added.
- Commit `0bae8b4` — FOUND on `worktree-agent-a2874dcd51e5b2426`.
- Commit `709685a` — FOUND on `worktree-agent-a2874dcd51e5b2426`.
- Commit `22687fc` — FOUND on `worktree-agent-a2874dcd51e5b2426`.
