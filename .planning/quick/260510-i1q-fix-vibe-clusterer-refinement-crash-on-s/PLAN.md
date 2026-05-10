---
type: quick
id: 260510-i1q
title: fix vibe_clusterer refinement crash on string source_vibe_ids
status: in-progress
created: 2026-05-10
---

# 260510-i1q — Fix vibe_clusterer refinement crash on string source_vibe_ids

## Production bug

A live wizard refinement call on a 585-track library raised
`pydantic_core._pydantic_core.ValidationError: 7 validation errors for
VibeProposalSetLLMResponse — proposals.N.source_vibe_ids.M / Input should be a
valid integer, unable to parse string as an integer` at
`app/services/anthropic_client.py:128` (`response_model.model_validate_json`).

Concrete failing values from the stack trace:

```
input_value='Neon Nights'
input_value='Hyperdrive'
input_value='Disco Motion'
input_value='Euphoria'
input_value='Deep Daze'
input_value='Indie Energy'
input_value='Chilled Vibes'
```

`POST /api/setup/refine` returned 200 (FastAPI webhook-style on-failure) but the
swap body was the error partial — the wizard is stuck.

## Root cause

Pre-finalize, prior proposals have no DB IDs. `_build_clustering_user_prompt`
(vibe_clusterer.py:374-389) dumps the prior `VibeProposalSet` as JSON via
`model_dump_json` without surfacing what integer ID each prior proposal carries.
With no ID system available, Claude picks the most stable identifier visible —
the vibe **name string** — and emits `source_vibe_ids: ['Neon Nights']`. The
strict `VibeProposal.source_vibe_ids: List[int]` schema rejects.

## Fix shape (atomic — single PR-style commit set)

### app/services/vibe_clusterer.py

1. Add `LLMVibeProposal` BaseModel — permissive `source_vibe_ids: List[Union[int, str]]`.
2. Switch `VibeProposalSetLLMResponse.proposals` to `List[LLMVibeProposal]`.
   Keep canonical `VibeProposal` strict (`List[int]`) — no schema regression.
3. `_build_clustering_user_prompt` injects a stable positional integer `id` field
   into each prior proposal in the JSON dump AND adds an explicit instruction:
   *"When you reference a prior vibe in source_vibe_ids, use the integer 'id'
   field shown above (NOT the name string)."*
4. Add private helper `_resolve_source_vibe_ids(llm_ids, prior_proposals)`:
   case-insensitive (`strip().casefold()`) name → positional-index lookup;
   integers pass through; unmatched strings dropped with `logger.warning`.
5. `_call_llm_with_validation` accepts new optional `prior_proposals` kwarg.
   Walks slim LLM proposals, converts each `LLMVibeProposal` → canonical
   `VibeProposal` via the helper, then assembles `VibeProposalSet`. Retry path
   uses the same conversion.
6. Wire `prior_proposals=None` in `initial_cluster_proposal`, `prior_proposals=prior`
   in `refine_proposals`.

### tests/test_vibe_clusterer.py

Add three regression tests:

- `test_refine_resolves_string_source_vibe_ids_to_indices` — `['Neon Nights', 'Hyperdrive']`
  against a 2-proposal prior set → canonical `[0, 1]`.
- `test_refine_drops_unresolvable_string_source_vibe_id` — `['Nonexistent Vibe', 'Hyperdrive']`
  → canonical `[1]`, warning logged.
- `test_refine_user_prompt_includes_positional_ids` — direct call to
  `_build_clustering_user_prompt`; assert `"id": 0` and `"id": 1` substrings +
  explicit instruction.

## Verification gate

```
.venv/bin/python -m pytest -q --tb=short tests/test_vibe_clusterer.py
```

Expected: 11 prior tests still pass + 3 new tests pass = 14 passing.

## Commit plan

1. `fix(quick-260510-i1q): resolve string source_vibe_ids from LLM via name lookup`
   (app/services/vibe_clusterer.py)
2. `test(quick-260510-i1q): regression tests for string source_vibe_ids resolution`
   (tests/test_vibe_clusterer.py)
3. `docs(quick-260510-i1q): summary` (.planning/quick/260510-i1q-.../PLAN.md + SUMMARY.md)

## Out of scope

- No change to canonical `VibeProposal.source_vibe_ids: List[int]`.
- No change to STATE.md or ROADMAP.md (quick-task convention).
- Does not address the wizard's 200-on-failure pattern returning the error partial —
  that's a separate ergonomic issue; this fix removes the trigger.
