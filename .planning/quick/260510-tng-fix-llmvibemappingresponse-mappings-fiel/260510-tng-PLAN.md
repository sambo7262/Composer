---
phase: quick-260510-tng
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/vibe_clusterer.py
  - tests/test_vibe_clusterer.py
autonomous: true
requirements:
  - QUICK-260510-TNG-01
must_haves:
  truths:
    - "User prompt from _build_user_led_clustering_user_prompt instructs LLM to return {\"mappings\": [...]} not {\"proposals\": [...]}"
    - "Override appears at the TOP of the task string so LLM reads it before existing semantics"
    - "All existing vibe_clusterer + anthropic_client tests continue to pass"
    - "System prompt builder (_build_clustering_system_prompt) is unchanged — 1h prompt cache stays valid"
  artifacts:
    - path: "tests/test_vibe_clusterer.py"
      provides: "Regression test asserting mappings-wrapper override in user prompt"
      contains: "test_user_led_user_prompt_overrides_proposals_wrapper_with_mappings"
    - path: "app/services/vibe_clusterer.py"
      provides: "User-prompt override forcing LLMVibeMappingResponse shape"
      contains: "RESPONSE FORMAT OVERRIDE"
  key_links:
    - from: "app/services/vibe_clusterer.py:_build_user_led_clustering_user_prompt"
      to: "LLMVibeMappingResponse schema (lines 194-206)"
      via: "task string explicitly names 'mappings' wrapper + LLMVibeFit fields"
      pattern: "mappings.*user_name.*cluster_index.*description.*fit.*reason"
---

<objective>
Fix the LLM emitting `{"proposals": [...]}` instead of `{"mappings": [...]}` on the user-led
mapping call (D-NEW-01). Root cause: the cached system prompt hard-codes
`Return ONLY {"proposals": [...]}` for the server-led `VibeProposalSetLLMResponse` schema,
and the user-led path shares that system prompt for cache hits. The user-prompt builder
never tells the LLM about the different response wrapper.

Purpose: Stop the `LLMVibeMappingResponse / mappings / Field required` Pydantic validation
errors observed in production (NAS, 2026-05-10 21:19:04) without invalidating the 1h
Anthropic prompt cache.

Output:
- A failing-then-passing TDD regression test in `tests/test_vibe_clusterer.py`.
- A targeted edit to the user-prompt-only `task` string in
  `_build_user_led_clustering_user_prompt` that prepends an explicit RESPONSE FORMAT
  OVERRIDE block. System prompt and `clusters` payload structure are untouched.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/STATE.md

<interfaces>
<!-- Key types and contracts the executor needs. Extracted from app/services/vibe_clusterer.py. -->
<!-- Use these directly — no further codebase exploration needed. -->

From app/services/vibe_clusterer.py (lines 179-206):
```python
class LLMVibeFit(BaseModel):
    """LLM-side response: one user-typed name mapped to one k-means cluster."""
    user_name: str
    cluster_index: int
    description: str
    fit: Literal["strong", "weak", "no_match"]
    reason: Optional[str] = None


class LLMVibeMappingResponse(BaseModel):
    """Slim LLM contract for the user-led mapping call (D-NEW-01)."""
    mappings: List[LLMVibeFit]
```

From app/services/vibe_clusterer.py (lines 531-575) — the function to modify:
```python
def _build_user_led_clustering_user_prompt(
    user_names: List[str],
    cluster_summaries: List[dict],
) -> str:
    payload = {
        "user_typed_vibe_names": user_names,
        "clusters": cluster_summaries,
        "task": (
            "For each user-typed vibe name above, pick the ONE cluster from "
            ...existing text unchanged...
            "genre-driven names (e.g. 'synth heavy', 'metal core')."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

From app/services/vibe_clusterer.py (lines 410-423) — DO NOT MODIFY this system prompt:
```python
parts.append("## Output format")
parts.append(
    'Return JSON matching VibeProposalSetLLMResponse: '
    '{"proposals": [{"name": "...", "description": "...", "action": "...", ...}]}'
)
parts.append(
    "...Return ONLY {\"proposals\": [...]}. ..."
)
```
The system prompt is cached for 1h — touching it invalidates the cache. The override
MUST live in the per-call user prompt only.

Existing test fixture pattern (tests/test_vibe_clusterer.py:953-987) — model the new test
after `test_user_led_user_prompt_includes_positional_ids_top_genres_and_explicit_instruction`.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1 (RED): Add regression test asserting mappings-wrapper override in user prompt</name>
  <files>tests/test_vibe_clusterer.py</files>
  <behavior>
    - Test name: `test_user_led_user_prompt_overrides_proposals_wrapper_with_mappings`
    - Calls `_build_user_led_clustering_user_prompt(["workout", "focus"], [<2 cluster summaries>])`
      using the same fixture shape as the existing positional-IDs test on line 953
      (cluster_index, centroid dict with energy/tempo/danceability/valence, top_artists,
       top_genres, closest_tracks, member_count). Two clusters is sufficient — keep payload small.
    - Asserts the returned prompt string contains:
        1. The literal substring `"mappings"` (the wrapper field name)
        2. The literal substring `"proposals"` (must be mentioned as the thing to AVOID)
        3. A negation phrase — assert `"do not use" in prompt.lower()` AND
           `"NOT" in prompt` (uppercase, to match "NOT \"proposals\"" or similar emphasis)
        4. Each LLMVibeFit field name appears verbatim somewhere in the prompt:
           `"user_name"`, `"cluster_index"`, `"description"`, `"fit"`, `"reason"`
        5. The literal substring `LLMVibeMappingResponse` (so the LLM sees the target schema name)
    - Place the new test immediately AFTER
      `test_user_led_user_prompt_includes_positional_ids_top_genres_and_explicit_instruction`
      (after line 987) so adjacent tests stay grouped by builder.
  </behavior>
  <action>
    Edit `tests/test_vibe_clusterer.py`. Insert a new test function directly after the
    existing `test_user_led_user_prompt_includes_positional_ids_top_genres_and_explicit_instruction`
    test (which ends around line 987). Pattern after that existing test for fixture shape and
    import style — `from app.services.vibe_clusterer import _build_user_led_clustering_user_prompt`
    inside the function body.

    Use 2 clusters (not 3) to keep the fixture minimal. Assertions per <behavior> above.

    Then RUN the test to confirm it FAILS on current code:
        pytest tests/test_vibe_clusterer.py::test_user_led_user_prompt_overrides_proposals_wrapper_with_mappings -xvs
    Expected failure: assertions on `"mappings"` or `"do not use"` or `LLMVibeMappingResponse`
    will not find their substrings because the current prompt has no override block.

    Do NOT modify any other test, fixture, or production file in this task. RED step only.
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer && python -m pytest tests/test_vibe_clusterer.py::test_user_led_user_prompt_overrides_proposals_wrapper_with_mappings -xvs 2>&1 | tail -20</automated>
  </verify>
  <done>
    Test exists and FAILS with an AssertionError pointing to a missing substring
    (`"mappings"` / `"do not use"` / `LLMVibeMappingResponse` — whichever fails first).
    No other tests modified. No production code modified. RED state confirmed.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2 (GREEN): Prepend RESPONSE FORMAT OVERRIDE block to user-led task string</name>
  <files>app/services/vibe_clusterer.py</files>
  <behavior>
    - After the edit, `_build_user_led_clustering_user_prompt(names, summaries)` returns a
      JSON string whose `task` field begins with a "RESPONSE FORMAT OVERRIDE" block that
      tells the LLM: ignore the `{"proposals": [...]}` wrapper from the system prompt;
      for this call, return `{"mappings": [{"user_name": ..., "cluster_index": <int>,
      "description": ..., "fit": "strong|weak|no_match", "reason": "...|null"}, ...]}`;
      the top-level field is `mappings` NOT `proposals`; do not include other top-level fields.
    - Block references `LLMVibeMappingResponse` by name so the LLM sees the target schema.
    - Existing semantics (permutation rule, fit grading, casing, top_genres signal) remain
      unchanged AFTER the override — append them verbatim with `"TASK: " + <existing task text>`.
    - The `clusters` payload key and structure are NOT touched.
    - Test from Task 1 passes; ALL existing vibe_clusterer + anthropic_client tests still pass.
  </behavior>
  <action>
    Edit `app/services/vibe_clusterer.py` lines 531-575 — the body of
    `_build_user_led_clustering_user_prompt`. Modify ONLY the `"task"` string inside the
    `payload` dict. Do NOT change the function signature, the `user_typed_vibe_names` key,
    the `clusters` key, the docstring header, or the `json.dumps(...)` return.

    Replace the current `"task": ( "For each user-typed vibe name..." ... ")` block with
    a two-part task string:

      "task": (
          "RESPONSE FORMAT OVERRIDE (this call only): "
          "Ignore the {\"proposals\": [...]} wrapper described in the system prompt. "
          "For this call, return JSON matching LLMVibeMappingResponse: "
          "{\"mappings\": [{\"user_name\": \"...\", \"cluster_index\": <int>, "
          "\"description\": \"...\", \"fit\": \"strong|weak|no_match\", "
          "\"reason\": \"...|null\"}, ...]}. "
          "The top-level field is \"mappings\", NOT \"proposals\". "
          "One entry per user-typed vibe name, in any order. "
          "Do NOT include any other top-level fields.\n\n"
          "TASK: "
          "For each user-typed vibe name above, pick the ONE cluster from "
          "the `clusters` list that best matches. Each cluster_index MUST "
          "be used exactly once across all mappings (it is a permutation "
          "of [0.." + str(len(user_names) - 1) + "]). Use the integer "
          "`cluster_index` field, NOT the centroid description or any "
          "top-artist name. Echo back the user's name verbatim in the "
          "`user_name` field (server resolves casing via case-insensitive "
          "lookup; you may use any casing). Grade fit as:\n"
          "- 'strong': clear audio/genre match between name and cluster\n"
          "- 'weak': partial match (e.g., one defining genre present but "
          "broader cluster identity is different)\n"
          "- 'no_match': cluster does not match the user's name at all; "
          "include a 1-line 'reason' explaining why. Empty playlist is "
          "still valid — the user can decide whether to keep it.\n"
          "Write a 1-line `description` per cluster that summarizes its "
          "audio/genre identity AS IT MAPS TO THE USER NAME. The "
          "`top_genres` list per cluster is your primary signal for "
          "genre-driven names (e.g. 'synth heavy', 'metal core')."
      ),

    Key design points:
    - Override block is FIRST so the LLM reads it before existing semantics.
    - Explicit negative ("NOT proposals") AND explicit positive ("the top-level field is mappings")
      — both phrasings improve LLM compliance.
    - Per-entry fields enumerated verbatim — matches LLMVibeFit (lines 187-191).
    - Existing task text appended unchanged after "TASK: " — preserves permutation rule, fit
      grading, casing, top_genres guidance.
    - System prompt (`_build_clustering_system_prompt`, lines 369-425) NOT touched —
      1h prompt cache stays valid (D-09 / Phase 6.1 caching invariant).
    - `clusters` payload (line 553) NOT touched.
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer && python -m pytest tests/test_vibe_clusterer.py tests/test_anthropic_client.py -x 2>&1 | tail -15</automated>
  </verify>
  <done>
    - Task 1's new test PASSES.
    - All other tests in `tests/test_vibe_clusterer.py` (36 tests) PASS.
    - All tests in `tests/test_anthropic_client.py` (8 tests) PASS.
    - `_build_clustering_system_prompt` is byte-identical to before this plan
      (grep confirms `'Return ONLY {"proposals": [...]}.'` still present in the
      system-prompt builder).
    - The `clusters` payload key in `_build_user_led_clustering_user_prompt` is unchanged.
  </done>
</task>

</tasks>

<verification>
After both tasks complete:

1. Full targeted test run — both files green:
   ```
   python -m pytest tests/test_vibe_clusterer.py tests/test_anthropic_client.py -v
   ```
   Expected: new test passes, all 36 vibe_clusterer + 8 anthropic_client tests pass.

2. System prompt untouched — cache invariant preserved:
   ```
   grep -c 'Return ONLY {"proposals": \[\.\.\.\]}' app/services/vibe_clusterer.py
   ```
   Expected: `1` (still present in `_build_clustering_system_prompt`).

3. Override present in user-prompt builder:
   ```
   grep -c "RESPONSE FORMAT OVERRIDE" app/services/vibe_clusterer.py
   ```
   Expected: `1` (in `_build_user_led_clustering_user_prompt`).

4. files_modified scope is exactly the two listed files:
   ```
   git diff --name-only
   ```
   Expected output: `app/services/vibe_clusterer.py` and `tests/test_vibe_clusterer.py` only.
</verification>

<success_criteria>
- LLM user-prompt now contains an explicit "RESPONSE FORMAT OVERRIDE" block naming
  `LLMVibeMappingResponse` and the `mappings` wrapper, with explicit negation of
  `proposals`.
- Regression test
  `test_user_led_user_prompt_overrides_proposals_wrapper_with_mappings` exists and passes.
- All 36 existing vibe_clusterer tests + 8 anthropic_client tests continue to pass.
- `_build_clustering_system_prompt` is byte-identical to pre-plan state (1h cache stays valid).
- `clusters` payload structure in `_build_user_led_clustering_user_prompt` is unchanged.
- `git diff --name-only` shows ONLY `app/services/vibe_clusterer.py` and
  `tests/test_vibe_clusterer.py`.
</success_criteria>

<output>
After completion, the quick task is done — no SUMMARY file needed for quick mode.
The fix can be verified in production by triggering the user-led mapping call and confirming
the LLM emits `{"mappings": [...]}` instead of `{"proposals": [...]}`.
</output>
