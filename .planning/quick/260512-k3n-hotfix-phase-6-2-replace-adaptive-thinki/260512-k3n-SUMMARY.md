---
quick_id: 260512-k3n
phase: 06.2
type: hotfix
started: 2026-05-12
completed: 2026-05-12
tests_added: 3
tests_total_passing: 445
commits: 3
files_changed:
  modified:
    - app/services/anthropic_client.py
    - tests/test_anthropic_client.py
    - app/routers/api_vibes.py
    - app/templates/pages/setup_step3.html
  created:
    - app/templates/partials/llm_progress_card.html
---

# 260512-k3n — Phase 6.2 NAS UAT Hotfix Summary

**One-liner:** Replaced the `thinking="adaptive"` literal with the documented
Anthropic API dict shape, added a one-shot BadRequestError-retry fallback, and
wired a live `Calling Anthropic…` progress card into the propose page.

## Bugs Fixed

1. **Critical — propose flow blocked.** `AnthropicClient` was sending
   `thinking={"type": "adaptive"}` (a literal that the API rejects with
   `BadRequestError: 400 — 'adaptive thinking is not supported on this model'`).
   The traceback fired at `vibe_clusterer.py:1895` (Pass 2), blocking every
   /setup/propose call on the NAS. The wrapper now translates
   `thinking="adaptive"` → `{"type": "enabled", "budget_tokens": 2000}` and
   `thinking="off"` → `{"type": "disabled"}`. On `BadRequestError` mentioning
   `"thinking"`, it retries ONCE with disabled and logs a `WARNING`, so the
   propose flow still completes when the operator points Composer at a model
   that rejects extended thinking entirely.

2. **UX — 30–60s silent wait on /setup/propose.** Propose page now includes
   `partials/llm_progress_card.html`, an Alpine.js card that listens for
   `htmx:beforeRequest` / `htmx:afterRequest` events on `document.body` and,
   while a `/api/setup/propose/init` or `/api/setup/propose/refine` request is
   in-flight, polls `/api/vibes/last-llm-call/progress` every 2 seconds. The
   card surfaces the current pass label (preamble / Pass 1 / Pass 2 /
   refine / recluster) and elapsed seconds. It hides on `htmx:afterRequest`.

## Commits

| Commit  | Task | Subject |
|---------|------|---------|
| cd78f72 | 1    | `fix(06.2): translate adaptive-thinking literal to API dict + one-shot retry` |
| 09f0f7e | 2    | `feat(06.2): add JSON progress endpoint for live LLM-call surface` |
| 80ca7c8 | 3    | `feat(06.2): live LLM-call progress card on propose page` |

## Tests

**3 new regression tests** in `tests/test_anthropic_client.py::TestAnthropicThinkingAndRobustExtraction`:

- `test_adaptive_translates_to_enabled_dict` — asserts the kwarg shape and
  explicitly confirms the bug literal `"adaptive"` is gone.
- `test_retries_once_when_model_rejects_thinking` — asserts two
  `messages.create` calls, second with `thinking={"type":"disabled"}`, result
  parses cleanly, and `WARNING` log mentions `extended thinking`.
- `test_off_sends_disabled_dict` — asserts explicit `thinking="off"` sends
  `{"type":"disabled"}` in the request body.

**3 existing tests updated** to match the new "always send a thinking kwarg"
behavior:
- `test_anthropic_client_thinking_default_sends_disabled` (was: assert
  `"thinking" not in kwargs`)
- `test_anthropic_client_thinking_adaptive_param_shape` (now asserts the
  full `{"type":"enabled","budget_tokens":2000}` shape)
- `test_anthropic_client_thinking_off_param_shape` (now asserts the
  disabled dict)

**Full unit suite:** 445 passed, 1 deselected (see Deferred Issues below).
Pre-existing deferred failures (test_audio_analyzer, test_chat_service,
test_sync_*) were skipped via `--ignore` per project convention.

## Files Touched

| File | Change |
|------|--------|
| `app/services/anthropic_client.py` | Added `BadRequestError` import; replaced single-mode kwarg construction with explicit translation for both modes; wrapped `messages.create` in tight `try/except BadRequestError` with one-shot retry; updated docstring. |
| `tests/test_anthropic_client.py` | Updated 3 existing thinking-mode tests; added 3 new hotfix regression tests. |
| `app/routers/api_vibes.py` | Added `JSONResponse` to fastapi.responses import; new endpoint `GET /api/vibes/last-llm-call/progress` returning `{purpose, called_at, elapsed_seconds}` from the latest `LIKE 'vibe_%'` `LLMUsage` row. |
| `app/templates/partials/llm_progress_card.html` (NEW) | Alpine.js progress card with inline `[x-cloak]` style; polls every 2s while in-flight; hides on `htmx:afterRequest`. |
| `app/templates/pages/setup_step3.html` | Included the partial at the top of `{% block wizard_content %}` before the `<h1>`. |

## Deviations from Plan

**1. [Rule 1 — Bug] Existing tests asserted `"thinking" not in kwargs`.**
The plan's task 8 anticipated this and instructed me to "soften" those asserts.
I updated three existing tests
(`test_anthropic_client_thinking_default_sends_disabled`,
`test_anthropic_client_thinking_adaptive_param_shape`,
`test_anthropic_client_thinking_off_param_shape`) to match the new "always send
a thinking kwarg" behavior. The first test was also renamed from
`test_anthropic_client_thinking_default_is_off_no_param_in_request` to reflect
the new contract. Test files committed alongside production code in Task 1.

**2. [Rule 1 — Bug] BadRequestError instance construction.**
`anthropic.BadRequestError` requires `response` + `body` to construct via the
public constructor, which would couple the test to internal SDK shape. Per the
plan's fallback note, I used
`BadRequestError.__new__(BadRequestError)` + `Exception.__init__(...)` so
`str(exc)` returns the expected message without touching SDK internals. The
production code only reads `str(exc).lower()`, so this is fully exercised.

## NAS UAT Verification Steps

After the orchestrator pushes the squashed commit and GitHub Actions builds
the next Docker image:

1. SSH to the NAS, `docker compose pull composer && docker compose up -d composer`.
2. Open `/setup` in the wizard, walk through to Step 3 (`/setup/propose`).
3. Type 3 vibe names and click **Cluster my library**.
4. Within ~2s the card should appear above the form showing
   `Calling Anthropic… (defining your vibes)`. As Pass 1 starts, the label
   should change to `(Pass 1 — assigning tracks)`, and finally
   `(Pass 2 — boundary review)`. The card hides when proposals render.
5. Confirm the propose flow completes without `BadRequestError: 400 —
   'adaptive thinking is not supported on this model'` in the Composer logs.
6. Spot-check `/debug/vibes` shows the new `vibe_assign_pass2` LLMUsage row
   with non-zero `output_tokens` (extended thinking tokens roll into
   `output_tokens`).

## Deferred Issues

While running the constraints' full-suite command I observed one additional
**pre-existing** failure that was not enumerated in the constraints' ignore
list. I verified it reproduces against the EXPECTED_BASE
(`f4290ac9994b7ff6d155713541666d66ba6c4d33`) with my hotfix changes reverted,
so it is unrelated to Phase 6.2 / 260512-k3n:

- `tests/test_analysis_service.py::TestRunAnalysis::test_skips_oversized_files`
  — `sqlalchemy.exc.PendingRollbackError` triggered by a `MagicMock`-typed
  value flowing into a `Track.musical_key` UPDATE binding. Same root cause
  pattern as the other pre-existing test_audio_analyzer / Essentia mocks
  documented in `.planning/phases/06.2-llm-direct-vibe-assignment/deferred-items.md`.

This was deselected with `--deselect` for the final-suite verification (445
passed, 1 deselected). It should be triaged in a follow-up `/gsd-quick`
session alongside the other Essentia/audio-feature mock failures already on
the deferred list.

## Self-Check: PASSED

- File created: `app/templates/partials/llm_progress_card.html` — FOUND.
- Commit cd78f72 — FOUND (`git log --oneline --all | grep cd78f72`).
- Commit 09f0f7e — FOUND.
- Commit 80ca7c8 — FOUND.
- Endpoint `/api/vibes/last-llm-call/progress` registered — confirmed via
  `from app.routers.api_vibes import router; ...` smoke import.
- Templates parse — confirmed via `Jinja2Templates(directory='app/templates').get_template(...)`.
- 19/19 tests in `tests/test_anthropic_client.py` pass (16 prior + 3 new).
- 445/445 selected tests in full unit suite pass.
- Caller surface (`vibe_clusterer.py` still passes `thinking="off"` and
  `thinking="adaptive"`) untouched.
- ROADMAP.md / STATE.md milestone status untouched (per constraints — that
  is the orchestrator's job).
