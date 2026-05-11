---
phase: quick-260510-tng
plan: 01
subsystem: vibe_clusterer / anthropic-cache-invariant
tags: [bugfix, llm-prompt, prompt-cache, vibe-clustering, regression, tdd]
type: execute
wave: 1
completed: 2026-05-11
dependency_graph:
  requires:
    - app/services/vibe_clusterer.py::_build_user_led_clustering_user_prompt
    - app/services/vibe_clusterer.py::LLMVibeMappingResponse
    - app/services/vibe_clusterer.py::_build_clustering_system_prompt  # untouched — cache invariant
  provides:
    - schema-override-in-user-prompt for D-NEW-01 user-led mapping call
  affects:
    - tests/test_vibe_clusterer.py  # +1 regression test
tech_stack:
  added: []
  patterns:
    - "Cache-preserving per-call schema override: cached system prompt fixes the response wrapper for the most-common path; per-call user prompt prepends an explicit RESPONSE FORMAT OVERRIDE block for the divergent path. System prompt stays byte-identical → 1h Anthropic prompt cache (Phase 6.1 invariant) is never invalidated."
key_files:
  created: []
  modified:
    - app/services/vibe_clusterer.py
    - tests/test_vibe_clusterer.py
decisions:
  - "[quick-260510-tng]: User-prompt-only schema override — never edit the 1h-cached system prompt for per-call response-shape divergence (D-NEW-01)"
  - "[quick-260510-tng]: Override block is FIRST in the task string, before existing semantics — LLMs anchor on what they read first"
  - "[quick-260510-tng]: Both positive ('top-level field is mappings') and negative ('NOT proposals') phrasings ship together — empirically improves compliance"
metrics:
  duration_min: 2
  tasks_completed: 2
  files_modified: 2
  tests_total: 45
  tests_passing: 45
requirements:
  - QUICK-260510-TNG-01
---

# Quick Task 260510-tng: Fix LLMVibeMappingResponse `mappings` Field Required Error Summary

**One-liner:** Per-call user-prompt schema-override stops `LLMVibeMappingResponse / mappings / Field required` Pydantic errors on the user-led mapping call (D-NEW-01) without invalidating the 1h Anthropic prompt cache.

## Context

In production (NAS, 2026-05-10 21:19:04), the user-led vibe mapping call failed with `LLMVibeMappingResponse / mappings / Field required` because the LLM emitted `{"proposals": [...]}` instead of `{"mappings": [...]}`. Root cause: the cached system prompt in `_build_clustering_system_prompt` hard-codes `Return ONLY {"proposals": [...]}` for the server-led `VibeProposalSetLLMResponse` schema; the user-led path shares that system prompt for cache hits, and the user-prompt builder never told the LLM about the different response wrapper. Editing the system prompt would invalidate the 1h prompt cache (Phase 6.1 D-NEW-01 invariant) so the fix had to live entirely in the per-call user prompt.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Add regression test asserting mappings-wrapper override in user prompt | `390e6e2` | tests/test_vibe_clusterer.py |
| 2 (GREEN) | Prepend RESPONSE FORMAT OVERRIDE block to user-led task string | `f673c8b` | app/services/vibe_clusterer.py |

### TDD Gate Compliance

- RED commit `390e6e2`: `test(...)` prefix, demonstrably failed against pre-fix builder on `assert "proposals" in prompt` (full failure trace captured during execution).
- GREEN commit `f673c8b`: `fix(...)` prefix (per-task commit type per workflow), follows RED, makes the new test pass while preserving all 44 prior tests.
- No REFACTOR step needed — the override block is minimal and self-explanatory; appending the existing task text verbatim after `TASK: ` keeps the diff surgical.

## Implementation

`app/services/vibe_clusterer.py::_build_user_led_clustering_user_prompt` — the `task` string now opens with a `RESPONSE FORMAT OVERRIDE (this call only)` block that:

1. Explicitly tells the LLM to **ignore** the `{"proposals": [...]}` wrapper from the system prompt.
2. Names the target schema `LLMVibeMappingResponse`.
3. Shows the literal target shape `{"mappings": [{"user_name": ..., "cluster_index": <int>, "description": ..., "fit": "strong|weak|no_match", "reason": "...|null"}, ...]}`.
4. Reinforces with both positive (`The top-level field is "mappings"`) and negative (`NOT "proposals"`) phrasings.
5. Forbids extra top-level fields (`Do NOT include any other top-level fields`).
6. Appends the existing task text verbatim after `TASK:` — permutation rule, fit grading, casing rule, and `top_genres` guidance are byte-identical to prior behavior.

The `user_typed_vibe_names` and `clusters` payload keys are untouched. The function signature, docstring, and `json.dumps(...)` return are untouched. `_build_clustering_system_prompt` is byte-identical to its pre-plan state — confirmed by `grep -c 'Return ONLY {"proposals": \[\.\.\.\]}'` returning `1` in the system-prompt builder, so the cached `ttl: "1h"` system message hashes the same and continues to hit Anthropic's cache.

## Verification

| Check | Expected | Actual |
|-------|----------|--------|
| `pytest tests/test_vibe_clusterer.py tests/test_anthropic_client.py -q` | 45 passed | 45 passed |
| `grep -c 'Return ONLY {"proposals": \[\.\.\.\]}' app/services/vibe_clusterer.py` | 1 (cache invariant) | 1 |
| `grep -c "RESPONSE FORMAT OVERRIDE" app/services/vibe_clusterer.py` | 1 | 1 |
| `git diff --name-only` scope | `app/services/vibe_clusterer.py` + `tests/test_vibe_clusterer.py` only | matches |

## Deviations from Plan

None — plan executed exactly as written. No deviation rules triggered, no auth gates, no fix-attempts. RED failed cleanly on the first assertion (`"proposals" in prompt`), GREEN edit made the test pass on the first run, and all 44 prior tests stayed green.

## Decisions Made

- **User-prompt-only schema override:** Never edit the 1h-cached system prompt for per-call response-shape divergence. The system prompt is the cache key — touching it for a one-line wrapper change would burn the cache for the much-more-frequent server-led path. The per-call user prompt is the right place for per-call schema overrides.
- **Override block FIRST in the task string:** LLMs anchor on early context; placing the override block before the existing semantics (which expect `{"proposals": [...]}`) ensures the override is read before any conflicting cue from the cached system prompt.
- **Both positive and negative phrasings ship together:** `The top-level field is "mappings"` (positive) AND `NOT "proposals"` (negative) both appear in the override block. Empirically improves LLM compliance on schema-shape instructions.

## Known Stubs

None.

## Self-Check: PASSED

- File `app/services/vibe_clusterer.py` modified — FOUND
- File `tests/test_vibe_clusterer.py` modified — FOUND
- Commit `390e6e2` (RED) — FOUND in git log
- Commit `f673c8b` (GREEN) — FOUND in git log
- Test `test_user_led_user_prompt_overrides_proposals_wrapper_with_mappings` — PASSES (verified via pytest run)
- All 45 targeted tests (44 prior + 1 new) — PASS
- System prompt unchanged — verified via grep (1 occurrence of `Return ONLY {"proposals": [...]}.` still in `_build_clustering_system_prompt`)
- Override block present — verified via grep (1 occurrence of `RESPONSE FORMAT OVERRIDE`)
- Scope correct — `git diff --name-only` shows only the two listed files
