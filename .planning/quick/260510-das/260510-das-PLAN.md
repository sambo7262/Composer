---
phase: quick-260510-das
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/vibe_clusterer.py
  - tests/test_vibe_clusterer.py
autonomous: true
requirements: [BUGFIX-VIBE-CLUSTERER-LLM-SCHEMA]

must_haves:
  truths:
    - "POST /api/setup/propose/init succeeds when the LLM returns only {\"proposals\": [...]}"
    - "_call_llm_with_validation returns a fully-assembled VibeProposalSet (server-controlled fields populated inside the function)"
    - "The LLM is constrained to a slim schema (VibeProposalSetLLMResponse) containing only `proposals`"
    - "The system prompt no longer instructs the LLM to return rated_track_count / silhouette_avg / forced_k / degraded_mode / rated_track_index_map"
    - "All existing 140 Phase 6 vibe_clusterer tests still pass"
    - "The sklearn AST allowlist tripwire still passes (sklearn imports stay confined to vibe_clusterer.py)"
  artifacts:
    - path: "app/services/vibe_clusterer.py"
      provides: "VibeProposalSetLLMResponse (slim model) + refactored _call_llm_with_validation that assembles VibeProposalSet server-side"
      contains: "class VibeProposalSetLLMResponse"
    - path: "tests/test_vibe_clusterer.py"
      provides: "Regression test for the production 500 + defensive test that the slim schema cannot fail on rated_track_index_map shape"
      contains: "test_initial_proposal_succeeds_with_slim_llm_response"
  key_links:
    - from: "app/services/vibe_clusterer.py::_call_llm_with_validation"
      to: "app/services/anthropic_client.py::call_with_structured_output"
      via: "response_model=VibeProposalSetLLMResponse (NOT VibeProposalSet)"
      pattern: "response_model=VibeProposalSetLLMResponse"
    - from: "app/services/vibe_clusterer.py::initial_cluster_proposal"
      to: "_call_llm_with_validation"
      via: "passes agg + forced_k + degraded + sil so the helper assembles the full VibeProposalSet itself"
      pattern: "_call_llm_with_validation\\("
---

<objective>
Fix the production 500 on `POST /api/setup/propose/init` caused by Anthropic returning
`rated_track_index_map: {}` (an empty dict for an empty collection — known LLM behavior).
The current `VibeProposalSet` schema declares server-controlled fields the LLM is told to
return; validation fails before the server's "override after LLM call" code can run.

Purpose: Stop asking the LLM for fields the server controls. Make the LLM's contract a slim
`VibeProposalSetLLMResponse` (only `proposals`); assemble the full `VibeProposalSet`
server-side from LLM output + aggregate data. This is a structural fix — eliminates an
entire class of LLM-shape-mismatch bugs at this boundary.

Output: Refactored `vibe_clusterer.py` (slim LLM schema + tightened prompt + assembly moved
inside `_call_llm_with_validation`), plus a regression test that mocks the LLM with the new
slim shape and confirms `initial_cluster_proposal` succeeds end-to-end.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md
@app/services/vibe_clusterer.py
@app/services/anthropic_client.py
@tests/test_vibe_clusterer.py

<bug_diagnosis>
Production stack trace from the user (already triaged — do NOT re-investigate):

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for VibeProposalSet
rated_track_index_map
  Input should be a valid array [type=list_type, input_value={}, input_type=dict]
```

Trigger: 585 rated tracks, k=5, silhouette=0.299. Numerics fine; clustering succeeded. The
LLM returned `rated_track_index_map: {}` because that field is empty per LLM perspective —
known JSON-shape ambiguity (empty list vs empty dict). Validation against the current
`VibeProposalSet` model failed at `anthropic_client.py:128` (`response_model.model_validate_json`)
before the caller's "override server-controlled fields" code at `vibe_clusterer.py:570-574`
could run.
</bug_diagnosis>

<interfaces>
<!-- Slim LLM contract that Task 1 introduces. Task 2's regression test
     mocks call_with_structured_output to return THIS shape, never the
     full VibeProposalSet. -->

```python
# NEW — narrow schema the LLM is constrained to.
class VibeProposalSetLLMResponse(BaseModel):
    """The slim contract for the LLM. Server assembles the full VibeProposalSet."""
    proposals: List[VibeProposal]

# UNCHANGED — still the public type returned by initial_cluster_proposal / refine_proposals.
class VibeProposalSet(BaseModel):
    proposals: List[VibeProposal]
    rated_track_count: int
    silhouette_avg: Optional[float] = None
    forced_k: Optional[int] = None
    degraded_mode: bool = False
    rated_track_index_map: List[dict] = Field(default_factory=list)
```

Refactored helper signature (Task 1):

```python
async def _call_llm_with_validation(
    *,
    system_prompt: str,
    user_prompt: str,
    purpose: str,
    n_rated: int,
    rated_track_index_map: list,
    forced_k: Optional[int],
    degraded: bool,
    silhouette_avg: Optional[float],
) -> VibeProposalSet:
    ...
```

The helper now:
1. Calls `client.call_with_structured_output(..., response_model=VibeProposalSetLLMResponse)`.
2. Validates `seed_track_indices` against `n_rated` (retry-once-then-raise — UNCHANGED logic).
3. Assembles and returns a fully-populated `VibeProposalSet` from the slim response + the
   server-supplied aggregate / clustering parameters.
</interfaces>

<existing_test_pattern>
<!-- All current tests mock the factory like this. Task 2's new tests follow the SAME pattern. -->

```python
fake_client = MagicMock()
fake_client.call_with_structured_output = AsyncMock(return_value=canned)
monkeypatch.setattr(
    "app.services.vibe_clusterer.get_anthropic_client_v2",
    lambda session: fake_client,
)
```

After refactor, `canned` MUST be a `VibeProposalSetLLMResponse(proposals=[...])`,
NOT a `VibeProposalSet`, because the helper now requests the slim model.
NOTE: the existing 7 behavioral tests in this file build `canned` via `_make_proposal_set`
which currently returns `VibeProposalSet`. Task 1 must update `_make_proposal_set` (or add a
sibling helper) so existing tests keep passing under the new schema. The simplest fix:
update `_make_proposal_set` to return `VibeProposalSetLLMResponse` (the LLM-side shape) since
that is what the mocked `call_with_structured_output` is now contracted to return.
</existing_test_pattern>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Slim LLM schema + assembly inside _call_llm_with_validation + tightened prompt</name>
  <files>app/services/vibe_clusterer.py, tests/test_vibe_clusterer.py</files>
  <action>
Edit `app/services/vibe_clusterer.py`:

1. **Add a new Pydantic model** beneath `VibeProposal` (around line 92):

   ```python
   class VibeProposalSetLLMResponse(BaseModel):
       """Slim LLM contract. The server assembles the full VibeProposalSet."""
       proposals: List[VibeProposal]
   ```

   Leave `VibeProposalSet` (line 93-101) UNCHANGED — it remains the public return type
   that callers (wizard, materialize_clusters) rely on.

2. **Refactor `_call_llm_with_validation`** (currently lines 627-671) to:
   - Accept new keyword-only parameters: `rated_track_index_map: list`, `forced_k: Optional[int]`,
     `degraded: bool`, `silhouette_avg: Optional[float]`.
   - Pass `response_model=VibeProposalSetLLMResponse` to `client.call_with_structured_output`
     on BOTH the first attempt AND the retry attempt.
   - Keep the existing seed-indices range check + retry-once-then-raise logic; just operate
     on `llm_response.proposals` (a `List[VibeProposal]`) instead of on a `VibeProposalSet`.
     `_validate_seed_indices` currently takes a `VibeProposalSet`; either (a) inline the same
     check on `llm_response.proposals`, or (b) refactor `_validate_seed_indices` to accept
     `proposals: List[VibeProposal]` directly. Either is fine — choose whichever produces the
     smaller diff. If you change `_validate_seed_indices`, update its only other caller (none
     outside the module — confirm with `grep -rn _validate_seed_indices app tests`).
   - On the FINAL successful response, construct and return:
     ```python
     return VibeProposalSet(
         proposals=llm_response.proposals,
         rated_track_count=n_rated,
         silhouette_avg=silhouette_avg,
         forced_k=forced_k,
         degraded_mode=degraded,
         rated_track_index_map=rated_track_index_map,
     )
     ```

3. **Update `initial_cluster_proposal`** (lines 562-577): pass the new kwargs through
   to `_call_llm_with_validation` and DELETE the now-redundant override block at lines
   569-574 (the five `proposals.<field> = ...` assignments).

4. **Update `refine_proposals`** (lines 608-621): same treatment — pass `rated_track_index_map`,
   `forced_k=prior.forced_k`, `degraded=prior.degraded_mode`, `silhouette_avg=prior.silhouette_avg`,
   and DELETE the override block at lines 615-621.

5. **Tighten `_build_clustering_system_prompt`** (lines 283-296). Replace the
   "## Output format" block (currently telling the LLM to return `rated_track_count`,
   `silhouette_avg`, `forced_k`, `degraded_mode`, `rated_track_index_map`) with:

   ```
   ## Output format
   Return JSON matching VibeProposalSetLLMResponse:
   {"proposals": [{"name": "...", "description": "...", "action": "...",
                   "source_vibe_ids": [...], "seed_track_indices": [...]}, ...]}

   action ∈ {keep, new, merged_from, split_from, renamed_from, dropped}.
   seed_track_indices MUST be integers in [0, rated_track_count).
   NEVER use raw Plex ratingKey strings (Pitfall 10).
   Do NOT set centroid / spread / silhouette — server computes those.
   Return ONLY {"proposals": [...]}. The server populates rated_track_count,
   silhouette_avg, forced_k, degraded_mode, and rated_track_index_map.
   ```

   Keep the rest of the prompt (Composer context block, etc.) UNCHANGED — it's the cache
   padding and must stay byte-identical across calls within the 1h TTL.

6. **Update the test helper `_make_proposal_set`** in `tests/test_vibe_clusterer.py`
   (lines 157-178): change the return statement so the helper returns
   `VibeProposalSetLLMResponse(proposals=proposals)` instead of `VibeProposalSet(...)`.
   This single change keeps every existing behavioral test passing because the mocked
   `call_with_structured_output` is now contracted to return the slim model.
   Also update the import on line 159 to add `VibeProposalSetLLMResponse`.

Constraints:
- DO NOT touch sklearn imports / placement (the AST tripwire test must still pass).
- DO NOT change any PlexAPI call patterns — none in this file, but D-09 invariant is global.
- DO NOT introduce Instructor (D-03 — keep `Pydantic.model_validate_json` path unchanged).
- DO NOT change `app/services/anthropic_client.py` — the slim model is just a different
  `response_model` argument; the client is already generic over `Type[T]`.
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer && python -m pytest tests/test_vibe_clusterer.py -x -q</automated>
  </verify>
  <done>
- `VibeProposalSetLLMResponse` exists in `vibe_clusterer.py` and contains only `proposals`.
- `_call_llm_with_validation` returns a fully-assembled `VibeProposalSet` (search for
  `return VibeProposalSet(` inside the function — must be present).
- `grep -n "proposals\\.rated_track_count = " app/services/vibe_clusterer.py` returns NO MATCHES
  (the override blocks are gone from `initial_cluster_proposal` and `refine_proposals`).
- `grep -n "rated_track_index_map" app/services/vibe_clusterer.py` shows it ONLY in the
  `VibeProposalSet` model definition, the prompt's "server populates ..." sentence, and the
  assembly inside `_call_llm_with_validation` — NOT in the LLM "## Output format" instruction.
- All existing tests in `tests/test_vibe_clusterer.py` pass (including the sklearn allowlist
  tripwire `test_sklearn_only_in_clusterer_module`).
  </done>
</task>

<task type="auto">
  <name>Task 2: Regression test — slim LLM response succeeds end-to-end</name>
  <files>tests/test_vibe_clusterer.py</files>
  <action>
Append two new tests to `tests/test_vibe_clusterer.py` (place them near the other
`initial_cluster_proposal` tests, e.g. after `test_initial_proposal_picks_silhouette_optimal_k`).

**Test 1 — production-shape regression:**

```python
@pytest.mark.asyncio
async def test_initial_proposal_succeeds_with_slim_llm_response(
    db_with_phase6, monkeypatch
):
    """Regression: prod 500 on POST /api/setup/propose/init.

    Before the fix, the LLM was asked to return rated_track_index_map; Claude returned
    `{}` (empty dict for empty collection — known JSON shape ambiguity), failing
    Pydantic validation against `List[dict]` at anthropic_client.py:128.

    After the fix, the LLM is constrained to VibeProposalSetLLMResponse (proposals only),
    and the server assembles VibeProposalSet from the LLM response + aggregate data.
    Validation cannot fail on a server-controlled field because the LLM never sees it.
    """
    _seed_tracks(db_with_phase6, count=60, well_separated=True)

    # Build a slim LLM response — the new contract.
    from app.services.vibe_clusterer import (
        VibeProposal,
        VibeProposalSet,
        VibeProposalSetLLMResponse,
    )
    canned = VibeProposalSetLLMResponse(
        proposals=[
            VibeProposal(
                name=f"Vibe {i+1}",
                description=f"Description {i+1}",
                action="new",
                source_vibe_ids=[],
                seed_track_indices=list(range(i * 20, (i + 1) * 20)),
            )
            for i in range(3)
        ],
    )

    fake_client = MagicMock()
    fake_client.call_with_structured_output = AsyncMock(return_value=canned)
    monkeypatch.setattr(
        "app.services.vibe_clusterer.get_anthropic_client_v2",
        lambda session: fake_client,
    )

    from app.services.vibe_clusterer import initial_cluster_proposal
    result = await initial_cluster_proposal()

    # Server populated the fields the LLM no longer sees.
    assert isinstance(result, VibeProposalSet)
    assert result.rated_track_count == 60
    assert isinstance(result.rated_track_index_map, list)
    assert len(result.rated_track_index_map) == 60
    assert result.degraded_mode is False  # n_rated >= 30, well-separated
    assert len(result.proposals) == 3

    # Confirm the LLM was asked for the slim model, NOT the full one.
    kwargs = fake_client.call_with_structured_output.await_args.kwargs
    assert kwargs["response_model"] is VibeProposalSetLLMResponse
```

**Test 2 — defensive: slim schema cannot fail on the historical bug shape:**

```python
def test_slim_llm_response_schema_has_no_rated_track_index_map_field():
    """Defensive: the slim LLM contract MUST NOT contain rated_track_index_map.

    If a future refactor accidentally re-adds it, this test fails immediately —
    preventing the production 500 from regressing.
    """
    from app.services.vibe_clusterer import VibeProposalSetLLMResponse

    fields = VibeProposalSetLLMResponse.model_fields
    assert set(fields.keys()) == {"proposals"}, (
        f"VibeProposalSetLLMResponse must contain ONLY `proposals`; got {set(fields.keys())}. "
        "Adding server-controlled fields here re-opens the prod bug where Claude returns "
        "`rated_track_index_map: {}` and Pydantic validation fails."
    )
```

Use the existing imports at the top of the file (`AsyncMock`, `MagicMock`, `pytest`, etc.) —
no new top-level imports needed.
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer && python -m pytest tests/test_vibe_clusterer.py::test_initial_proposal_succeeds_with_slim_llm_response tests/test_vibe_clusterer.py::test_slim_llm_response_schema_has_no_rated_track_index_map_field -v</automated>
  </verify>
  <done>
- Both new tests pass.
- Full file run still green: `python -m pytest tests/test_vibe_clusterer.py -q` (no regressions
  in the original 9 tests including the sklearn AST tripwire).
- `test_slim_llm_response_schema_has_no_rated_track_index_map_field` would fail loudly if
  someone re-introduces `rated_track_index_map` (or any other server-controlled field) into
  `VibeProposalSetLLMResponse` — verify this by mentally tracing the assertion.
  </done>
</task>

</tasks>

<verification>
Run the full vibe_clusterer test file plus a broader smoke (Phase 6 wizard route uses this
service):

```bash
python -m pytest tests/test_vibe_clusterer.py -v
python -m pytest tests/ -q -k "vibe or wizard or setup" 2>/dev/null
```

Both should be green. The second command is a smoke check — `initial_cluster_proposal`
is the only entry point Phase 6 wizard calls into for clustering, and the public
`VibeProposalSet` shape is unchanged, so no caller changes are required.

Manual confirmation (optional, no automated check needed): grep proves the prompt no longer
asks for server-controlled fields.

```bash
grep -A5 "## Output format" app/services/vibe_clusterer.py
# Expected: mentions ONLY proposals; explicitly says "server populates ..."
```
</verification>

<success_criteria>
- `VibeProposalSetLLMResponse` exists with exactly one field: `proposals`.
- `_call_llm_with_validation` returns a fully-assembled `VibeProposalSet` constructed inside
  the helper from LLM output + aggregate data.
- The override blocks at the old call sites (lines 569-574 in `initial_cluster_proposal`,
  615-621 in `refine_proposals`) are GONE.
- The system prompt's "## Output format" block instructs the LLM to return ONLY
  `{"proposals": [...]}` and explicitly says the server populates the rest.
- The new regression test passes; the defensive schema test passes.
- All pre-existing 9 tests in `test_vibe_clusterer.py` still pass (including the sklearn
  AST tripwire).
</success_criteria>

<output>
After completion, create `.planning/quick/260510-das/260510-das-01-SUMMARY.md` recording:
- The slim/full schema split (`VibeProposalSetLLMResponse` vs `VibeProposalSet`).
- The assembly-inside-helper pattern as the new convention for LLM calls where the response
  mixes LLM-generated and server-controlled fields (note for future Phase 7 ranking call).
- Files modified, tests added, atomic commit hashes.
</output>
