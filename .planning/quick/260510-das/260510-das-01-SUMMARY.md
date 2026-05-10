---
phase: quick-260510-das
plan: 01
subsystem: vibe-clusterer / LLM-boundary
tags: [bugfix, production-500, llm-schema, structured-output, refactor, regression-test]
requires:
  - app/services/vibe_clusterer.py
  - app/services/anthropic_client.py
provides:
  - VibeProposalSetLLMResponse (slim LLM contract)
  - assembly-inside-helper pattern for LLM calls with mixed LLM-generated + server-controlled fields
affects:
  - app/services/vibe_clusterer.py::initial_cluster_proposal
  - app/services/vibe_clusterer.py::refine_proposals
  - app/services/vibe_clusterer.py::_call_llm_with_validation
  - app/services/vibe_clusterer.py::_validate_seed_indices
  - app/services/vibe_clusterer.py::_build_clustering_system_prompt
tech-stack:
  added: []
  patterns:
    - "Slim/full schema split at LLM boundary: narrow LLM-facing Pydantic model contains ONLY LLM-generated fields; server assembles full DTO from slim response + aggregate / server-controlled data."
    - "Schema-lock defensive test: assert ``model_fields == {expected}`` on the LLM-facing model so any future field addition fails loudly."
key-files:
  created: []
  modified:
    - app/services/vibe_clusterer.py
    - tests/test_vibe_clusterer.py
decisions:
  - "Refactored ``_validate_seed_indices`` to take ``List[VibeProposal]`` instead of ``VibeProposalSet`` — only callers were inside ``_call_llm_with_validation``; produces smaller diff than inlining."
  - "Added ``_make_full_proposal_set`` test helper for the ``prior`` argument to ``refine_proposals`` (still a full ``VibeProposalSet``); kept ``_make_proposal_set`` returning the slim model since it now models the ``call_with_structured_output`` mock-return contract."
metrics:
  duration: ~25min
  completed: 2026-05-10
  tasks_committed: 2
  files_modified: 2
  tests_added: 2
  tests_passing: 21 (11 vibe_clusterer + 10 event_handlers)
---

# Quick 260510-das Plan 01: Fix VibeProposalSet LLM-shape mismatch (production 500) Summary

Structural fix for a production 500 on `POST /api/setup/propose/init` caused by Claude returning `rated_track_index_map: {}` (empty dict for an empty collection) where Pydantic expected `List[dict]`. The fix splits the LLM-facing schema (`VibeProposalSetLLMResponse`, only `proposals`) from the server-controlled `VibeProposalSet`, moves field assembly inside `_call_llm_with_validation`, and tightens the system prompt so the LLM is never asked for fields the server controls.

## What changed

### `app/services/vibe_clusterer.py`

- **NEW** `VibeProposalSetLLMResponse(BaseModel)` — slim LLM contract with a single field, `proposals: List[VibeProposal]`.
- **MODIFIED** `_call_llm_with_validation`:
  - New keyword-only signature: `*, system_prompt, user_prompt, purpose, n_rated, rated_track_index_map, forced_k, degraded, silhouette_avg`.
  - Calls `client.call_with_structured_output(..., response_model=VibeProposalSetLLMResponse)` on both first attempt and retry.
  - Validates `seed_track_indices` against `n_rated` (range check unchanged) on `llm_response.proposals`.
  - On success, **assembles** and returns a fully-populated `VibeProposalSet` from the slim LLM response + the supplied aggregate / clustering parameters. The LLM never sees `rated_track_count`, `silhouette_avg`, `forced_k`, `degraded_mode`, or `rated_track_index_map`.
- **MODIFIED** `_validate_seed_indices(proposals: List[VibeProposal], n_rated: int)` — was `VibeProposalSet`. Only callers were inside `_call_llm_with_validation`.
- **MODIFIED** `initial_cluster_proposal` — passes new kwargs through; deleted the post-call override block (5 `proposals.<field> = ...` assignments).
- **MODIFIED** `refine_proposals` — same: passes `forced_k=prior.forced_k`, `degraded=prior.degraded_mode`, `silhouette_avg=prior.silhouette_avg`, `rated_track_index_map=agg["rated_track_index_map"]`; deleted the override block.
- **MODIFIED** `_build_clustering_system_prompt`'s `## Output format` section: now instructs the LLM to return ONLY `{"proposals": [...]}` and explicitly states the server populates the rest. Composer-context block (the cacheable padding inside the 1h TTL) is **byte-identical** so the prompt cache still hits.

### `tests/test_vibe_clusterer.py`

- **MODIFIED** `_make_proposal_set` — returns `VibeProposalSetLLMResponse(proposals=...)`. This is the new mock-return contract for `call_with_structured_output`.
- **NEW** `_make_full_proposal_set` — returns a full `VibeProposalSet`. Used as the `prior` argument to `refine_proposals` (which still expects the full model).
- **MODIFIED** Two existing tests (`test_refine_proposals_validates_seed_indices_in_range`, `test_refine_proposals_uses_recluster_purpose_when_flag_set`) updated to use `_make_full_proposal_set` for the `prior` arg, and the inline bad-shape mock to use `VibeProposalSetLLMResponse`.
- **NEW** `test_initial_proposal_succeeds_with_slim_llm_response` — production-shape regression. Asserts the LLM is asked for the slim model (`response_model is VibeProposalSetLLMResponse`) and the server-assembled result has the correct `rated_track_count`, `rated_track_index_map`, and `degraded_mode`.
- **NEW** `test_slim_llm_response_schema_has_no_rated_track_index_map_field` — schema-lock defensive test. Asserts `VibeProposalSetLLMResponse.model_fields == {"proposals"}`. If a future refactor accidentally re-adds any server-controlled field, this fails loudly with a regression-explaining message.

## Pattern: assembly inside helper

This refactor introduces a pattern that applies to any future LLM call where the response mixes LLM-generated fields and server-controlled fields:

> Define two Pydantic models — a slim `*LLMResponse` containing only the fields the LLM generates, and a full DTO that the rest of the system consumes. The helper that wraps the LLM call validates against the slim model, then assembles the full DTO from the slim response + server-supplied parameters before returning.

Why: Pydantic validates the LLM response **before** any post-call override code can run (this is the entire point of structured-output libraries). If the LLM-facing model declares fields the server intends to overwrite, you have a validation race the server cannot win — Claude's perfectly valid `{}` for an empty collection sees a `List[dict]` annotation and rejects.

**Apply this to Phase 7's ranking call** (`get_song_recommendations` / similar). Anywhere the LLM is asked to emit a structure where some fields are generated and others are server-supplied, split the schema.

## Why this fix is structural, not patchwork

The previous architecture had:

```
LLM returns full VibeProposalSet → Pydantic validates against List[dict] → server overrides fields
                                            ^
                                            FAILS HERE before override runs
```

A non-structural fix would set `rated_track_index_map: List[dict] = Field(default_factory=list)` and pre-populate it in the prompt. That treats the symptom but leaves the root cause (LLM contract includes server-controlled fields) live for any future field that gets added — and the cache-padding prompt is locked at 1h TTL, so per-call substitutions inside the cache block defeat caching.

The structural fix removes the entire class of "LLM returned the wrong shape for a server-controlled field" bugs by ensuring the LLM is never told about server-controlled fields at all.

## Atomic commits

| Task | Description                                                                | Commit    |
| ---- | -------------------------------------------------------------------------- | --------- |
| 1    | Slim LLM schema + assembly inside `_call_llm_with_validation` + prompt     | `976495b` |
| 2    | Regression test (slim LLM response succeeds) + defensive schema-lock test  | `c402e53` |

## Verification

| Gate                                                            | Result                                          |
| --------------------------------------------------------------- | ----------------------------------------------- |
| `pytest tests/test_vibe_clusterer.py`                           | 11/11 passed (9 existing + 2 new)               |
| `pytest tests/test_event_handlers.py` (Phase 5 AST tripwire)    | 10/10 passed                                    |
| `pytest tests/ -k "vibe or wizard or setup"` (broader smoke)    | 90 passed, 0 failures                           |
| sklearn AST allowlist (`test_sklearn_only_in_clusterer_module`) | passed                                          |
| `grep -c 'proposals\.rated_track_count = '`                     | 0 (override blocks deleted)                     |
| `VibeProposalSetLLMResponse` defined                            | line 93 of `app/services/vibe_clusterer.py`     |
| `response_model=VibeProposalSetLLMResponse` in helper           | lines 675 + 695 (first attempt + retry)         |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `_make_proposal_set` return-type change broke `prior` arg path**

- **Found during:** Task 1 — after updating `_make_proposal_set` per the plan to return `VibeProposalSetLLMResponse`, two existing tests (`test_refine_proposals_validates_seed_indices_in_range`, `test_refine_proposals_uses_recluster_purpose_when_flag_set`) were also using the helper as the `prior` argument to `refine_proposals`. The slim model lacks `forced_k` / `degraded_mode` / `silhouette_avg`, which `refine_proposals` reads off `prior`.
- **Issue:** Plan said the single helper change "keeps every existing behavioral test passing". That was true for the mock-return path but not the `prior`-argument path.
- **Fix:** Added a sibling helper `_make_full_proposal_set` that returns a full `VibeProposalSet` (wraps `_make_proposal_set`'s slim output + sets `rated_track_count`); switched the two `prior=` call sites to use it.
- **Files modified:** `tests/test_vibe_clusterer.py`
- **Commit:** `976495b` (Task 1, same commit)

### Architectural changes (Rule 4)

None — fix is internal to the `vibe_clusterer.py` module + its test file. Public type `VibeProposalSet` is unchanged; downstream callers (wizard, `materialize_clusters`) are untouched.

## Self-Check: PASSED

- File `app/services/vibe_clusterer.py` modified — verified.
- File `tests/test_vibe_clusterer.py` modified — verified.
- Commit `976495b` exists in `git log` — verified.
- Commit `c402e53` exists in `git log` — verified.
- `VibeProposalSetLLMResponse` class present at line 93 — verified.
- 0 occurrences of `proposals\.rated_track_count = ` (override blocks gone) — verified.
- 2 occurrences of `return VibeProposalSet(` (cold-start path + `_call_llm_with_validation` assembly) — verified.
- 2 occurrences of `response_model=VibeProposalSetLLMResponse` (first attempt + retry inside helper) — verified.
- All 11 vibe_clusterer tests + 10 event_handlers tests pass — verified.
- 90 vibe/wizard/setup tests pass (broader smoke) — verified.
