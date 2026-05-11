---
phase: quick-260510-t5n
plan: 01
subsystem: llm-client
tags: [hotfix, llm, anthropic, json-parsing, tdd]
requires:
  - app/services/anthropic_client.py (existing AsyncAnthropic wrapper)
provides:
  - Trailing/leading-prose-tolerant JSON parsing in AnthropicClient.call_with_structured_output
affects:
  - app/services/vibe_clusterer.py::map_user_vibes_to_clusters (downstream consumer — now resilient to trailing prose)
tech-stack:
  added: []
  patterns:
    - "Use json.JSONDecoder.raw_decode for tolerant JSON parsing when the source may contain trailing junk"
    - "Locate object start with str.find('{') before raw_decode to also handle leading prose"
    - "Fall back to model_validate_json on JSONDecodeError so callers still see canonical Pydantic ValidationError"
key-files:
  created: []
  modified:
    - app/services/anthropic_client.py
    - tests/test_anthropic_client.py
decisions:
  - "Use stdlib json.JSONDecoder.raw_decode rather than regex extraction — zero new deps and reads exactly one well-formed JSON value, ignoring everything after it"
  - "Preserve the Pydantic ValidationError error-handling contract on truly malformed input (fall back to model_validate_json on JSONDecodeError) so the existing 'WARNING: ... rejected input' log path in map_user_vibes_to_clusters still functions"
  - "Keep the existing markdown-fence stripping block first and unchanged — orthogonal to the prose-tolerance fix"
metrics:
  duration_minutes: 5
  completed: 2026-05-10
  tasks_completed: 2
  files_modified: 2
---

# Quick 260510-t5n: Fix AnthropicClient JSON Parsing for Trailing/Leading Prose Summary

Tolerate trailing/leading prose around the JSON object returned by Anthropic Sonnet by parsing with `json.JSONDecoder.raw_decode` after `str.find("{")`, eliminating the production `Invalid JSON: trailing characters` failure in `map_user_vibes_to_clusters`.

## Production Traceback That Motivated the Fix

On 2026-05-10 20:57:33 the NAS deployment logged a Pydantic `ValidationError: Invalid JSON: trailing characters at line 47 column 1` from `AnthropicClient.call_with_structured_output`. The Anthropic Sonnet model had returned a well-formed JSON object describing cluster→vibe proposals, then continued with a short paragraph of commentary (something like `"...feel the name implies."`). `Pydantic.model_validate_json` is strict — it rejects ANY trailing characters after the value — so the entire response was dropped, and `map_user_vibes_to_clusters` logged `WARNING: ... rejected input` and produced zero mappings for that user vibe request.

## Mitigation Strategy (Two-Line stdlib Fix)

The fix is purely additive on the parser tail in `app/services/anthropic_client.py`:

1. Add `import json` to the stdlib block (alphabetical between `asyncio` and `logging`).
2. After the existing markdown-fence stripping block, locate the first `{` and feed `text[first_brace:]` into `json.JSONDecoder().raw_decode(...)`. `raw_decode` returns `(parsed_obj, end_index)` after reading exactly one well-formed JSON value, silently discarding everything after it. Pass `parsed_obj` to `response_model.model_validate(...)`.
3. On `JSONDecodeError`, fall back to `response_model.model_validate_json(text[first_brace:])` so callers still receive the canonical Pydantic `ValidationError` for truly malformed output (preserves the existing error-handling contract).

The markdown-fence stripping logic still runs first and is untouched — the two behaviors are orthogonal.

## Regression Sweep — All Tests Pass

| Test File                          | Tests | Result |
| ---------------------------------- | ----- | ------ |
| `tests/test_anthropic_client.py`   | 8     | PASS   |
| `tests/test_vibe_clusterer.py`     | 36    | PASS   |
| **Total**                          | 44    | PASS   |

- The 6 prior `anthropic_client` tests (`test_explicit_ttl_1h`, `test_logs_usage_to_llmusage_table`, `test_warns_when_caching_not_engaged`, `test_parses_pydantic_response`, `test_strips_markdown_fences`, `test_factory_raises_when_not_configured`) continue to pass.
- 2 new tests added inside `class TestAnthropicClient`:
  - `test_tolerates_trailing_prose_after_json` — mocks a `{"proposals": [...]}` response followed by `"Note: cluster 0 is a stretch."`, asserts `len(result.proposals) == 1` and the first proposal's name. Reproduces the production traceback shape.
  - `test_tolerates_prose_before_and_after_json` — mocks `'Here is the JSON:\n{"proposals": []}\nLet me know if you need changes.'`, asserts `result.proposals == []`.
- Both new tests demonstrably FAILED against the pre-fix parser with the expected `pydantic_core._pydantic_core.ValidationError` ("trailing characters" / "Invalid JSON: expected value"), then PASS post-fix — proper RED→GREEN cycle.
- The 36 `vibe_clusterer` tests pass unchanged — the parser's calling contract is preserved: still returns a `response_model` instance, still raises `ValidationError` on unsalvageable input.

## ValidationError Contract Preserved

`map_user_vibes_to_clusters` (and any other caller of `call_with_structured_output`) catches `pydantic.ValidationError` and logs `WARNING: ... rejected input`. The fix preserves this contract:

- When `text.find("{") == -1` (no JSON object at all) → falls through to `response_model.model_validate_json(text)` → callers see `ValidationError`, same as before.
- When `raw_decode` itself fails (e.g., the model returned `{broken json}`) → falls through to `response_model.model_validate_json(text[first_brace:])` → callers see `ValidationError`, same as before.
- When `raw_decode` succeeds but the parsed object fails Pydantic schema validation (e.g., missing required field) → `response_model.model_validate(obj)` raises `ValidationError` directly — same exception type, same caller handling.

No new exception types introduced; no caller changes required.

## Deviations from Plan

None — plan executed exactly as written. Both tasks committed atomically, RED phase verified the tests fail with the expected `ValidationError`, GREEN phase made all 8 anthropic_client + 36 vibe_clusterer tests pass.

## Commits

| Phase | Commit    | Message                                                                         |
| ----- | --------- | ------------------------------------------------------------------------------- |
| RED   | `82f6455` | test(quick-260510-t5n): add failing tests for trailing/leading prose in JSON responses |
| GREEN | `ca691bd` | fix(quick-260510-t5n): tolerate trailing/leading prose around JSON in AnthropicClient   |

## Self-Check: PASSED

- File `app/services/anthropic_client.py` — FOUND, contains `json.JSONDecoder().raw_decode`.
- File `tests/test_anthropic_client.py` — FOUND, contains `test_tolerates_trailing_prose_after_json`.
- Commit `82f6455` — FOUND in git log.
- Commit `ca691bd` — FOUND in git log.
- 8 anthropic_client tests pass; 36 vibe_clusterer tests pass; total 44/44 green.
