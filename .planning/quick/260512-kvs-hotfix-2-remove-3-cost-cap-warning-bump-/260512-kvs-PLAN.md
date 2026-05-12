---
phase: quick-260512-kvs-hotfix
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/vibe_clusterer.py
  - app/routers/pages.py
  - app/templates/pages/debug_vibes.html
autonomous: true
requirements:
  - HOTFIX-260512-KVS
must_haves:
  truths:
    - "Pass 1 LLM calls run with max_tokens=6000 (was 3000)."
    - "Pass 2 LLM calls run with max_tokens=8000 (was 4000); enough headroom for thinking.budget_tokens=2000 + ~4000 JSON output."
    - "PREAMBLE_MAX_TOKENS stays at 600."
    - "/debug/vibes no longer renders a $3.00 cost-ceiling warning state or red border, regardless of vibe_cost_total."
    - "/debug/vibes still surfaces the per-purpose breakdown and total cost."
    - "vibe_cost_warning is not computed in app/routers/pages.py and not passed in the template context."
    - "The 'Cost ceiling (D-32): … must be ≤ $3.00' sentence is removed from the assign_tracks_to_user_vibes docstring; the '/debug/vibes surfaces the cost breakdown by purpose (D-32)' visibility note remains."
    - "Full test suite (excluding the documented pre-existing-failure files) is green."
    - "Changes are pushed to origin/main."
  artifacts:
    - path: "app/services/vibe_clusterer.py"
      provides: "Bumped max_tokens constants + cleaned docstring."
      contains: "PASS1_MAX_TOKENS = 6000"
    - path: "app/services/vibe_clusterer.py"
      provides: "Bumped max_tokens constants + cleaned docstring."
      contains: "PASS2_MAX_TOKENS = 8000"
    - path: "app/routers/pages.py"
      provides: "debug_vibes route with no cost-warning flag."
      exports: ["debug_vibes"]
    - path: "app/templates/pages/debug_vibes.html"
      provides: "Cost panel without warning state."
  key_links:
    - from: "app/services/vibe_clusterer.py::_pass2_one_batch"
      to: "anthropic client.call_with_structured_output"
      via: "max_tokens=PASS2_MAX_TOKENS"
      pattern: "max_tokens=PASS2_MAX_TOKENS"
    - from: "app/routers/pages.py::debug_vibes"
      to: "app/templates/pages/debug_vibes.html"
      via: "TemplateResponse context dict"
      pattern: "vibe_cost_by_purpose"
---

<objective>
Quick hotfix bundling two tightly-coupled changes:

A) Bump Pass 1 / Pass 2 `max_tokens` to stop NAS UAT crashes where
   `stop_reason=max_tokens` truncated Pass 2 responses (only a `thinking`
   block returned, no `text`).
B) Remove the soft `$3.00` cost-cap warning on `/debug/vibes` per explicit
   user request. Keep the per-purpose cost breakdown and total; drop only
   the warning flag, conditional red border, and the warning paragraph.
   Update docstring + template doc comment so stale "$3 ceiling" language
   does not survive.

Purpose: Restore vibe-assignment reliability on the NAS and remove a
display-only warning the user no longer wants surfaced.

Output: Updated `vibe_clusterer.py`, `pages.py`, `debug_vibes.html`. No
ROADMAP / STATE / REQUIREMENTS edits. Two atomic commits, pushed to
`origin/main`.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@app/services/vibe_clusterer.py
@app/routers/pages.py
@app/templates/pages/debug_vibes.html

<interfaces>
<!-- Extracted from the codebase so the executor has no scavenger hunt. -->

Current constants in `app/services/vibe_clusterer.py` (lines 966-976):
```python
# Phase 6.2 D-01 / D-16 — batch sizes locked per CONTEXT.md.
PASS1_BATCH_SIZE = 25
PASS2_BATCH_SIZE = 15
# Phase 6.2 D-07 — Pass 1 output token cap (25 tracks × ~80 = ~2000; 3000 gives margin).
PASS1_MAX_TOKENS = 3000
# Phase 6.2 RESEARCH §4.3 — Pass 2 output cap; thinking tokens roll into output.
PASS2_MAX_TOKENS = 4000
# Phase 6.2 D-04 — preamble output cap (one definition per vibe; short).
PREAMBLE_MAX_TOKENS = 600
# Phase 6.2 D-14 — peer count per candidate vibe in Pass 2 system prompt.
PASS2_PEER_COUNT = 10
```

Current docstring tail in `assign_tracks_to_user_vibes` (lines 1462-1465):
```
    Cost ceiling (D-32): SELECT SUM(cost_estimate_usd) FROM llmusage WHERE
    purpose LIKE 'vibe_%' and called_at >= <run_start> must be ≤ $3.00
    (target ≤ $1.50). /debug/vibes surfaces the breakdown by purpose.
```

Current Pass 2 caller (lines 1885-1893) — unchanged in this hotfix; the
constant bump propagates automatically because the call site already reads
`max_tokens=PASS2_MAX_TOKENS`:
```python
async def _one(prompt: str) -> LLMVibeBoundaryResponse:
    return await client.call_with_structured_output(
        system_prompt=pass2_system_prompt,
        user_prompt=prompt,
        response_model=LLMVibeBoundaryResponse,
        max_tokens=PASS2_MAX_TOKENS,
        purpose="vibe_assign_pass2",
        thinking="adaptive",
    )
```

Current route block in `app/routers/pages.py` (lines 491-515):
```python
vibe_cost_by_purpose: dict = {}
for row in cost_by_purpose_rows:
    if isinstance(row, tuple):
        purpose, total = row
    else:
        purpose, total = row[0], row[1]
    vibe_cost_by_purpose[purpose] = float(total or 0.0)
vibe_cost_total = sum(vibe_cost_by_purpose.values())
# D-32: $3.00 acceptance ceiling (the user-approved cap; $1.50 target).
vibe_cost_warning = vibe_cost_total > 3.0

return templates.TemplateResponse(
    request,
    "pages/debug_vibes.html",
    {
        "active_page": "debug_vibes",
        "vibes": vibes,
        "vibe_member_counts": vibe_member_counts,
        "slot_in_log": slot_in_log,
        "drift": drift,
        "vibe_cost_by_purpose": vibe_cost_by_purpose,
        "vibe_cost_total": vibe_cost_total,
        "vibe_cost_warning": vibe_cost_warning,
    },
)
```

Current template block in `app/templates/pages/debug_vibes.html` (lines 26-64):
- Lines 26-30: Jinja doc comment ends with "and a warning state when the
  total exceeds $3.00 (acceptance ceiling per D-32)."
- Line 32: `class="… {% if vibe_cost_warning %}border-error{% else %}border-border{% endif %}"`
- Lines 59-64: `{% if vibe_cost_warning %}<p…>⚠ Vibe LLM cost exceeds…</p>{% endif %}`

Test scan results (`grep` already run during planning):
- `grep -n 'PASS1_MAX_TOKENS\|PASS2_MAX_TOKENS\|PREAMBLE_MAX_TOKENS' tests/test_vibe_clusterer.py` → no hits.
- `grep -rn 'vibe_cost_warning' tests/` → no hits.
No test updates required.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Bump max_tokens constants + clean docstring in vibe_clusterer.py</name>
  <files>app/services/vibe_clusterer.py</files>
  <action>
Make exactly two edits in `app/services/vibe_clusterer.py`:

1) Around lines 969-972 (the constants block), replace:

```
# Phase 6.2 D-07 — Pass 1 output token cap (25 tracks × ~80 = ~2000; 3000 gives margin).
PASS1_MAX_TOKENS = 3000
# Phase 6.2 RESEARCH §4.3 — Pass 2 output cap; thinking tokens roll into output.
PASS2_MAX_TOKENS = 4000
```

with:

```
# Phase 6.2 D-07 + hotfix 260512-kvs — Pass 1 output token cap.
# NAS UAT (May 2026) observed stop_reason=max_tokens for purpose=vibe_assign_pass1
# at 3000 with 25-track batches; 6000 gives ~2× headroom.
PASS1_MAX_TOKENS = 6000
# Phase 6.2 RESEARCH §4.3 + hotfix 260512-kvs — Pass 2 output cap; thinking
# tokens roll into output. NAS UAT (May 2026) observed stop_reason=max_tokens
# for purpose=vibe_assign_pass2 with only a `thinking` block returned (no
# `text`). On Sonnet 4.6, thinking.budget_tokens=2000 + ~4000 JSON output
# needs >6000; 8000 gives headroom.
PASS2_MAX_TOKENS = 8000
```

Leave `PREAMBLE_MAX_TOKENS = 600` and every other constant in this block
unchanged. Do not modify any call site — both `_one` for Pass 1 and
`_pass2_one_batch` already read the constants by name.

2) Around lines 1462-1464 inside the `assign_tracks_to_user_vibes`
docstring, replace:

```
    Cost ceiling (D-32): SELECT SUM(cost_estimate_usd) FROM llmusage WHERE
    purpose LIKE 'vibe_%' and called_at >= <run_start> must be ≤ $3.00
    (target ≤ $1.50). /debug/vibes surfaces the breakdown by purpose.
```

with:

```
    /debug/vibes surfaces the cost breakdown by purpose (D-32).
```

Keep the indentation consistent with the surrounding docstring. Do not
touch any other line of the docstring.

After both edits, sanity-check from the repo root:
```
grep -n 'PASS1_MAX_TOKENS\s*=' app/services/vibe_clusterer.py
grep -n 'PASS2_MAX_TOKENS\s*=' app/services/vibe_clusterer.py
grep -n 'PREAMBLE_MAX_TOKENS\s*=' app/services/vibe_clusterer.py
grep -n '\$3.00\|acceptance ceiling' app/services/vibe_clusterer.py
```
Expected: PASS1=6000, PASS2=8000, PREAMBLE=600, the `$3.00` /
`acceptance ceiling` grep returns nothing.

Then commit (DO NOT push yet — push happens in Task 3):
```
git add app/services/vibe_clusterer.py
git commit -m "fix(vibes): bump Pass1/Pass2 max_tokens; drop stale \$3 ceiling from docstring

Pass 2 NAS UAT crashed with stop_reason=max_tokens at 4000 (only a
thinking block returned, no text). Sonnet 4.6 with
thinking.budget_tokens=2000 + ~4000 JSON output needs >6000; 8000 gives
headroom. Pass 1 bumped 3000->6000 for symmetric margin after similar
truncation on 25-track batches. Preamble unchanged.

Also strips the obsolete '\$3.00 cost ceiling' sentence from the
assign_tracks_to_user_vibes docstring; /debug/vibes still surfaces the
cost breakdown by purpose."
```
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; grep -q 'PASS1_MAX_TOKENS = 6000' app/services/vibe_clusterer.py &amp;&amp; grep -q 'PASS2_MAX_TOKENS = 8000' app/services/vibe_clusterer.py &amp;&amp; grep -q 'PREAMBLE_MAX_TOKENS = 600' app/services/vibe_clusterer.py &amp;&amp; ! grep -qE '\$3\.00|acceptance ceiling' app/services/vibe_clusterer.py &amp;&amp; source .venv/bin/activate &amp;&amp; python -m pytest tests/test_anthropic_client.py tests/test_vibe_clusterer.py -v</automated>
  </verify>
  <done>
Constants are 6000 / 8000 / 600. No `$3.00` or `acceptance ceiling`
string remains in `vibe_clusterer.py`. `test_anthropic_client.py` and
`test_vibe_clusterer.py` are fully green. Commit recorded on the local
branch.
  </done>
</task>

<task type="auto">
  <name>Task 2: Remove vibe_cost_warning from route + template (cost panel stays)</name>
  <files>app/routers/pages.py, app/templates/pages/debug_vibes.html</files>
  <action>
Two coordinated edits — route first, template second.

1) `app/routers/pages.py` around lines 500-501 and line 514:

Delete these two lines (501 and the surrounding comment on 500):

```
    # D-32: $3.00 acceptance ceiling (the user-approved cap; $1.50 target).
    vibe_cost_warning = vibe_cost_total > 3.0
```

Delete this dict entry from the `TemplateResponse` context dict:

```
            "vibe_cost_warning": vibe_cost_warning,
```

Keep every other line in the function intact. After the edit the
`TemplateResponse` should still pass `vibe_cost_by_purpose` and
`vibe_cost_total` and the trailing 3 entries (`active_page`, `vibes`,
`vibe_member_counts`, `slot_in_log`, `drift`) unchanged.

2) `app/templates/pages/debug_vibes.html` — three edits in this file:

  a) Lines 26-30 (Jinja doc comment): replace the existing comment text
     so the "warning state when the total exceeds $3.00 (acceptance
     ceiling per D-32)" clause is gone. The new comment must read:

     ```
     {# Phase 6.2 D-32 / VIBE-13 — LLM cost panel segmented by purpose.
        Renders the three Phase 6.2 purposes (preamble / Pass 1 / Pass 2)
        plus any legacy vibe_clustering_* rows still in the table, with a
        running total. #}
     ```

  b) Line 31-32 (conditional border): replace

     ```
       <section class="space-y-2 bg-surface-elevated rounded-lg p-4 border
                       {% if vibe_cost_warning %}border-error{% else %}border-border{% endif %}">
     ```

     with

     ```
       <section class="space-y-2 bg-surface-elevated rounded-lg p-4 border border-border">
     ```

  c) Lines 59-64 (warning paragraph): delete the entire block

     ```
           {% if vibe_cost_warning %}
             <p class="text-[13px] text-error mt-1">
               ⚠ Vibe LLM cost exceeds the $3.00 acceptance ceiling. Investigate
               retry storms or unexpected Pass 2 traffic on /debug/vibes.
             </p>
           {% endif %}
     ```

     so the `</tbody>` / closing `</table>` are immediately followed by
     the `{% else %}` of the outer `{% if vibe_cost_by_purpose %}`. Do
     not change indentation of surrounding lines.

After both files are saved, sanity-check from the repo root:
```
grep -n 'vibe_cost_warning' app/routers/pages.py app/templates/pages/debug_vibes.html
grep -n 'acceptance ceiling\|\$3\.00\|border-error' app/templates/pages/debug_vibes.html
```
Both greps must return nothing. Then quickly verify the file still
parses as valid Jinja by importing the route module:
```
source .venv/bin/activate
python -c "from app.routers import pages; print('ok')"
```

Commit (DO NOT push yet — push happens in Task 3):
```
git add app/routers/pages.py app/templates/pages/debug_vibes.html
git commit -m "fix(debug): remove \$3 vibe-cost warning from /debug/vibes

Per user request, the \$3.00 ceiling on /debug/vibes was a soft display
warning, not a circuit breaker. Drops vibe_cost_warning from the route
context and removes the conditional red border + warning paragraph from
the template. Per-purpose breakdown and total cost still render."
```
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; ! grep -q 'vibe_cost_warning' app/routers/pages.py &amp;&amp; ! grep -q 'vibe_cost_warning' app/templates/pages/debug_vibes.html &amp;&amp; ! grep -qE 'acceptance ceiling|\$3\.00|border-error' app/templates/pages/debug_vibes.html &amp;&amp; grep -q 'vibe_cost_by_purpose' app/routers/pages.py &amp;&amp; grep -q 'vibe_cost_total' app/routers/pages.py &amp;&amp; source .venv/bin/activate &amp;&amp; python -c "from app.routers import pages; print('ok')"</automated>
  </verify>
  <done>
`vibe_cost_warning` appears nowhere in `app/routers/pages.py` or
`app/templates/pages/debug_vibes.html`. `vibe_cost_by_purpose` and
`vibe_cost_total` still appear in the route. Template no longer contains
`acceptance ceiling`, `$3.00`, or `border-error`. `pages` module imports
cleanly. Commit recorded on the local branch.
  </done>
</task>

<task type="auto">
  <name>Task 3: Run full test suite (minus deferred files) and push to origin/main</name>
  <files>(no file edits)</files>
  <action>
Run the broader regression suite, then push.

From the repo root:

```
source .venv/bin/activate
python -m pytest -x \
  --ignore=tests/test_audio_analyzer.py \
  --ignore=tests/test_chat_service.py \
  --ignore=tests/test_sync_api.py \
  --ignore=tests/test_sync_scheduler.py \
  --ignore=tests/test_sync_service.py
```

Pre-existing failures in the ignored files are documented in
`.planning/phases/06.2-llm-direct-vibe-assignment/deferred-items.md` and
are explicitly out of scope for this hotfix.

If pytest passes, push:

```
git push origin main
```

If pytest fails, do NOT push. Read the failure, fix the offending file
(stay in scope — only the three files this plan touched), amend or
create a follow-up commit on the same branch, re-run the suite, then
push.

After push, confirm the remote head matches local head:
```
git log -3 --oneline
git rev-parse HEAD
git rev-parse origin/main
```
The last two must match.
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; source .venv/bin/activate &amp;&amp; python -m pytest -x --ignore=tests/test_audio_analyzer.py --ignore=tests/test_chat_service.py --ignore=tests/test_sync_api.py --ignore=tests/test_sync_scheduler.py --ignore=tests/test_sync_service.py &amp;&amp; [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]</automated>
  </verify>
  <done>
The non-deferred test suite is green. `origin/main` HEAD equals local
HEAD; both hotfix commits are visible in `git log -3 --oneline`.
  </done>
</task>

</tasks>

<verification>
End-to-end check after all three tasks:

```
cd /Users/Oreo/Projects/Composer
grep -n 'PASS1_MAX_TOKENS = 6000\|PASS2_MAX_TOKENS = 8000\|PREAMBLE_MAX_TOKENS = 600' app/services/vibe_clusterer.py
grep -n 'vibe_cost_warning' app/routers/pages.py app/templates/pages/debug_vibes.html || echo "warning fully removed"
git log -3 --oneline
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && echo "pushed"
```

Expected: three constants line up; "warning fully removed" prints; two
hotfix commits visible; "pushed" prints.
</verification>

<success_criteria>
- `PASS1_MAX_TOKENS == 6000`, `PASS2_MAX_TOKENS == 8000`,
  `PREAMBLE_MAX_TOKENS == 600` in `app/services/vibe_clusterer.py`.
- `assign_tracks_to_user_vibes` docstring no longer asserts a `$3.00`
  cost ceiling; visibility note (`/debug/vibes surfaces the cost
  breakdown by purpose (D-32)`) is preserved.
- `app/routers/pages.py::debug_vibes` no longer computes or passes
  `vibe_cost_warning`. `vibe_cost_by_purpose` and `vibe_cost_total` are
  still in the context dict.
- `app/templates/pages/debug_vibes.html` no longer references
  `vibe_cost_warning`, `border-error`, `$3.00`, or "acceptance ceiling".
  Cost table + total still render.
- `tests/test_anthropic_client.py` and `tests/test_vibe_clusterer.py`
  are green (Task 1).
- Full suite (minus the five documented pre-existing-failure files) is
  green (Task 3).
- `origin/main` advanced by two commits that together implement the
  hotfix.
- ROADMAP.md, STATE.md, REQUIREMENTS.md are untouched.
</success_criteria>

<output>
After completion, no SUMMARY file is required for a `/gsd-quick` task.
Confirmation is the pushed commits on `origin/main`.
</output>
