---
type: quick
id: 260510-i1q
title: fix vibe_clusterer refinement crash on string source_vibe_ids
status: complete
created: 2026-05-10
completed: 2026-05-10
duration: ~25 minutes
tags: [bug-fix, prod-incident, llm-boundary, pydantic, vibe_clusterer]
key-files:
  modified:
    - app/services/vibe_clusterer.py
    - tests/test_vibe_clusterer.py
  created:
    - .planning/quick/260510-i1q-fix-vibe-clusterer-refinement-crash-on-s/PLAN.md
    - .planning/quick/260510-i1q-fix-vibe-clusterer-refinement-crash-on-s/SUMMARY.md
decisions:
  - LLM-side schema is permissive (LLMVibeProposal — List[Union[int, str]]); canonical schema (VibeProposal — List[int]) stays strict. Resolution happens at one boundary inside _call_llm_with_validation.
  - Unresolvable name strings are dropped with logger.warning (lossy attribution > crashed wizard). The user gets a working refinement; the warning surfaces in container logs for follow-up.
  - Case-insensitive name match via .strip().casefold() to tolerate LLM minor reformatting (e.g. "neon nights" vs "Neon Nights").
  - Prompt-side mitigation (positional 'id' anchor + explicit instruction) ships alongside the schema-side mitigation. Defense in depth — even if Claude ignores the instruction, the server tolerates it.
---

# quick-260510-i1q — Fix vibe_clusterer refinement crash on string source_vibe_ids Summary

Two-layer fix at the LLM ingest boundary in `app/services/vibe_clusterer.py` that eliminates a live prod 200-with-error-partial on the wizard refinement endpoint when the LLM emits vibe names (strings) inside `source_vibe_ids`.

## The bug

Production stack trace on a 585-track library:

```
ERROR: app.routers.api_setup: refine_proposals failed
File "/app/app/services/vibe_clusterer.py", line 672, in _call_llm_with_validation
File "/app/app/services/anthropic_client.py", line 128, in call_with_structured_output
pydantic_core._pydantic_core.ValidationError: 7 validation errors for VibeProposalSetLLMResponse
proposals.0.source_vibe_ids.0 / Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='Neon Nights', input_type=str]
proposals.1.source_vibe_ids.0 / input_value='Hyperdrive'
proposals.2.source_vibe_ids.0 / input_value='Disco Motion'
proposals.3.source_vibe_ids.0 / input_value='Euphoria'
proposals.4.source_vibe_ids.0 / input_value='Deep Daze'
proposals.5.source_vibe_ids.0 / input_value='Indie Energy'
proposals.5.source_vibe_ids.1 / input_value='Chilled Vibes'
```

Root cause: pre-finalize, prior proposals have no DB IDs. The refinement user-prompt builder dumped the prior `VibeProposalSet` as JSON without exposing any integer identifier per proposal — so Claude reasonably picked the most stable identifier visible, the vibe name string, and emitted `source_vibe_ids: ['Neon Nights']`. The strict canonical schema (`VibeProposal.source_vibe_ids: List[int]`) rejected. FastAPI returned 200 with the error partial; wizard stuck.

## The fix

Two-layer mitigation, both in `app/services/vibe_clusterer.py`:

### Prompt side
- `_build_clustering_user_prompt` now injects a stable positional integer `id` per prior proposal in the JSON dump AND adds an explicit instruction telling Claude to use the integer `id` field, NOT the name string, in `source_vibe_ids`. Primary mitigation — Claude now has the identifier it needs.

### Schema side (defense in depth)
- New `LLMVibeProposal` BaseModel — sibling to `VibeProposal` — permissive on `source_vibe_ids: List[Union[int, str]]`. `VibeProposalSetLLMResponse.proposals` is now `List[LLMVibeProposal]`.
- New `_resolve_source_vibe_ids(llm_ids, prior_proposals) -> List[int]`: case-insensitive (`.strip().casefold()`) name → positional-index lookup against `prior_proposals`. Integers pass through. Unmatched strings dropped with `logger.warning`. None-priors path drops strings silently (initial-turn proposals shouldn't use `source_vibe_ids`).
- New `_canonicalize_llm_proposals(llm_proposals, prior_proposals)` walks every `LLMVibeProposal` and rewrites it as a strict canonical `VibeProposal` — called from `_call_llm_with_validation` AFTER slim-schema validation passes.
- `_call_llm_with_validation` accepts a new `prior_proposals` kwarg. `initial_cluster_proposal` passes `None`; `refine_proposals` passes the prior set.

The canonical `VibeProposal.source_vibe_ids: List[int]` is unchanged — the permissiveness lives only at the LLM ingest boundary.

## Verification

```
$ .venv/bin/python -m pytest -q --tb=short tests/test_vibe_clusterer.py
..............                                                           [100%]
14 passed in 2.20s
```

- 11 prior tests pass unchanged.
- 3 new regression tests pass:
  - `test_refine_resolves_string_source_vibe_ids_to_indices` — exact prod-shape input `['Neon Nights', 'Hyperdrive']` against a 2-proposal prior → canonical `[0, 1]`.
  - `test_refine_drops_unresolvable_string_source_vibe_id` — `['Nonexistent Vibe', 'Hyperdrive']` → canonical `[1]` with a warning log captured.
  - `test_refine_user_prompt_includes_positional_ids` — direct unit test on `_build_clustering_user_prompt`; asserts `"id": 0`, `"id": 1`, and the explicit "use the integer 'id' field NOT the name string" instruction.

### Wider sweep

```
$ pytest tests/test_vibe_clusterer.py tests/test_api_setup.py tests/test_api_vibes.py
30 passed in 4.77s
```

### Phase 5 AST tripwires

Both still green:
- `test_sklearn_only_in_clusterer_module` — sklearn import remains confined to `vibe_clusterer.py`.
- `test_no_blocking_plexapi_in_async` — no new PlexAPI calls added.

### Defensive slim-schema test
`test_slim_llm_response_schema_has_no_rated_track_index_map_field` still asserts `VibeProposalSetLLMResponse` has ONLY a `proposals` field — preserved (the field's element TYPE changed, not the field set).

## Commits

| # | Hash    | Message |
| - | ------- | ------- |
| 1 | f4ee057 | fix(quick-260510-i1q): resolve string source_vibe_ids from LLM via name lookup |
| 2 | f928640 | test(quick-260510-i1q): regression tests for string source_vibe_ids resolution |
| 3 | (this)  | docs(quick-260510-i1q): summary |

## Deviations from plan

### Auto-fixed (Rule 3 — blocking issue)

**1. [Rule 3 - Blocking] Updated test helper schemas to construct LLMVibeProposal.**

- **Found during:** First test run after the production fix.
- **Issue:** Pydantic v2 rejects cross-class coercion — `VibeProposalSetLLMResponse(proposals=[VibeProposal(...)])` raises `Input should be a valid dictionary or instance of LLMVibeProposal` because the canonical `VibeProposal` is a different class from the new `LLMVibeProposal`. The existing test helpers `_make_proposal_set` and two inline mock-response builders constructed `VibeProposal` instances and fed them into the slim response, which now expects `LLMVibeProposal`.
- **Fix:** Updated `_make_proposal_set` to construct `LLMVibeProposal`; updated the inline builders in `test_initial_proposal_succeeds_with_slim_llm_response` and `test_refine_proposals_validates_seed_indices_in_range` similarly. Updated `_make_full_proposal_set` to canonicalize slim → strict for the `prior` argument.
- **Files modified:** tests/test_vibe_clusterer.py (shipped in commit 2 alongside the new regression tests).

No architectural changes needed; no Rule 4 escalation.

## Known stubs / threat flags

None. The fix is purely defensive at the LLM ingest boundary; no new network endpoints, auth surfaces, file access, or schema changes.

## Out of scope

- The FastAPI 200-with-error-partial pattern on `/api/setup/refine` still returns 200 on the inner failure (it's the wizard's webhook-style design; the wizard swaps the body partial). This fix removes the trigger.
- No changes to STATE.md or ROADMAP.md (per quick-task convention).

## Self-Check

- [x] `app/services/vibe_clusterer.py` exists and contains `class LLMVibeProposal`, `def _resolve_source_vibe_ids`, `def _canonicalize_llm_proposals`.
- [x] `tests/test_vibe_clusterer.py` exists and contains the 3 new test functions.
- [x] Commit `f4ee057` (production fix) exists in git log.
- [x] Commit `f928640` (regression tests) exists in git log.
- [x] Verification gate: `pytest -q tests/test_vibe_clusterer.py` → 14 passed.

## Self-Check: PASSED
