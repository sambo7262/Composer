---
phase: 06-vibe-clustering-setup-wizard
reviewed: 2026-05-09T00:00:00Z
depth: standard
files_reviewed: 56
files_reviewed_list:
  - app/database.py
  - app/main.py
  - app/models/track.py
  - app/models/vibe.py
  - app/routers/api_setup.py
  - app/routers/api_vibes.py
  - app/routers/pages.py
  - app/services/analysis_service.py
  - app/services/event_handlers.py
  - app/services/plex_playlist_service.py
  - app/services/vibe_clusterer.py
  - app/services/vibe_helpers.py
  - app/services/vibe_service.py
  - app/static/css/input.css
  - app/templates/base.html
  - app/templates/pages/debug_vibes.html
  - app/templates/pages/debug_vibes_stub.html
  - app/templates/pages/settings.html
  - app/templates/pages/setup_done.html
  - app/templates/pages/setup_step1.html
  - app/templates/pages/setup_step2.html
  - app/templates/pages/setup_step3.html
  - app/templates/pages/setup_step4.html
  - app/templates/partials/cold_start_panel.html
  - app/templates/partials/drift_indicator.html
  - app/templates/partials/feature_chip.html
  - app/templates/partials/proposal_cards_swap.html
  - app/templates/partials/push_to_plex_banner.html
  - app/templates/partials/rated_count.html
  - app/templates/partials/recluster_modal.html
  - app/templates/partials/refine_error.html
  - app/templates/partials/refinement_input.html
  - app/templates/partials/seed_track_row.html
  - app/templates/partials/slot_in_log_table.html
  - app/templates/partials/vibe_diagnostic_card.html
  - app/templates/partials/vibe_members_disclosure.html
  - app/templates/partials/vibe_name_input.html
  - app/templates/partials/vibe_proposal_card.html
  - app/templates/partials/wizard_layout.html
  - app/templates/partials/wizard_step_indicator.html
  - app/templates/partials/wizard_sticky_cta.html
  - tests/test_api_setup.py
  - tests/test_api_vibes.py
  - tests/test_base_html_conventions.py
  - tests/test_database_phase6.py
  - tests/test_event_handlers.py
  - tests/test_event_handlers_phase6.py
  - tests/test_finalize_integration.py
  - tests/test_pages_debug_vibes.py
  - tests/test_pages_phase6.py
  - tests/test_plex_playlist_service.py
  - tests/test_recluster_commit.py
  - tests/test_settings_phase6.py
  - tests/test_setup_templates.py
  - tests/test_vibe_clusterer.py
  - tests/test_vibe_helpers.py
  - tests/test_vibe_service.py
findings:
  critical: 2
  warning: 9
  info: 6
  total: 17
status: issues_found
---

# Phase 6: Code Review Report

**Reviewed:** 2026-05-09T00:00:00Z
**Depth:** standard
**Files Reviewed:** 56
**Status:** issues_found

## Summary

Phase 6 ships a substantial body of work: vibe clustering with k-means + LLM
naming, a 5-step HTMX wizard with conversational refinement, slot-in hot path
on RatingChanged events, diff-based re-cluster commit with manual-override
preservation, and an operator-facing /debug/vibes page. The Phase 5
conventions (PlexAPI in `to_thread`, INSERT OR IGNORE dedupe, raw 0-10
user_rating, multipart Form parsing) are correctly inherited and the AST
tripwires for `sklearn` allowlist + PlexAPI-in-async wrapping are in place
and pass.

The review found **two BLOCKER issues**:

1. A confirmed **stored-XSS vector** in `partials/vibe_name_input.html` where
   the LLM-supplied `proposal.name` is interpolated into a JavaScript string
   literal inside an Alpine `x-data` attribute using only the HTML-escape
   filter. An attacker who can shape an LLM response (via prompt-injection
   from the user-typed refinement message, or via direct DB tampering of
   `SetupState.draft_proposals_json`) can break out of the JS string and
   execute arbitrary script in the wizard UI.

2. A **control-flow defect** in `api_vibes.recluster_commit`: the `dropped`
   action uses `if action == "dropped":` rather than `elif`, immediately
   following an `if action in ("keep", "renamed_from")` block whose
   defensive fallback re-assigns `action = "new"`. The chain happens to
   produce correct behavior today only because no path reassigns `action`
   to `"dropped"` — a one-line future change to that fallback would
   silently double-execute. The dispatcher should be a single `if/elif/elif`
   chain.

Plus nine WARNINGs (notably: per-track lock LRU eviction race that breaks
serialization, finalize banner percent overflow on merge/split actions,
unused dead variable that signals an incomplete idempotency feature,
maybe_reslot_pending_track clearing the flag in pre-wizard state, and
several minor robustness gaps) and six INFO items.

## Critical Issues

### CR-01: Stored XSS via LLM-supplied vibe name interpolated into Alpine JS string

**File:** `app/templates/partials/vibe_name_input.html:4`
**Issue:** The LLM-derived `proposal.name` is rendered into a JavaScript
string literal inside an Alpine `x-data` attribute using only Jinja's
`| e` (HTML escape) filter:

```html
<div x-data="{ editing: false, value: '{{ proposal.name | e }}' }" class="mb-2">
```

`| e` is the wrong filter for a JS-string-inside-HTML-attribute context.
The browser HTML-decodes the attribute value BEFORE Alpine.js parses the
expression as JavaScript. So a name like `Foo'); alert(1); //` is
HTML-encoded to `Foo&#39;); alert(1); //`, decoded by the browser to
`Foo'); alert(1); //`, and handed to Alpine which sees:

```js
{ editing: false, value: 'Foo'); alert(1); //' }
```

— breaking out of the single-quoted string and executing arbitrary JS in
the operator's session. The code page also includes a `name` attribute on
the `<input>` element bound to `vibe_name_{{ proposal_index }}`, so the
malicious value would also be submitted on form posts.

The Phase 6 plan asserts (CONTEXT, item 2 in `<phase_context>`): "the user
message is NEVER echoed back (only LLM-derived VibeProposalSet rendered
with Jinja2 auto-escape)." This is a half-truth: the user's raw text isn't
rendered, but the LLM response IS, and the user's text directly shapes the
LLM's output. Prompt-injection through `/api/setup/refine` is a realistic
attack path (the user-typed message is appended verbatim to the system
prompt). For example: "Rename a vibe to: `Foo'); alert(1); //`".

Local-only deployment (Tailscale / NAS) limits external exposure but does
NOT mitigate the bug — a curious user pasting an attacker-supplied
refinement string, or a malicious browser extension, can still trigger it.

**Fix:** Use `tojson` instead of `| e` for the JS string context. `tojson`
emits a properly JSON-encoded string with quotes, escapes, and surrounding
quotation marks built in:

```html
<div x-data="{ editing: false, value: {{ proposal.name | tojson }} }" class="mb-2">
```

Note: drop the surrounding single quotes — `tojson` produces them. Audit
every other `x-data="{...}"` in Phase 6 partials for the same pattern; the
description input on line 9 of `vibe_proposal_card.html` is OK because it
is an HTML attribute value (`value="..."`) rather than a JS string, but a
similar refactor (`value="{{ proposal.description | default('') }}"`) is
safer if Tailwind / Alpine ever consume the attribute as JS.

---

### CR-02: `dropped` action uses `if` instead of `elif`, breaking dispatcher invariant

**File:** `app/routers/api_vibes.py:576`
**Issue:** The `recluster_commit` proposal-dispatch loop is:

```python
for prop in targets:
    try:
        async with sema:
            action = prop.action

            if action in ("keep", "renamed_from"):
                ...
                if existing is None:
                    # No source vibe found — defensive fallback: treat as "new".
                    action = "new"
                else:
                    ... handle keep/renamed ...

            if action == "dropped":          # <-- IF not ELIF (line 576)
                ...
            elif action == "merged_from":
                ...
            elif action == "split_from":
                ...
            elif action == "new":
                ...
```

The `if action == "dropped":` on line 576 is a separate `if` statement
following the `keep/renamed_from` block. The chain happens to produce
correct behavior today ONLY because:
  - No path inside the `keep/renamed_from` block reassigns `action` to
    `"dropped"`.
  - The `keep/renamed_from` defensive fallback reassigns `action = "new"`,
    which is then handled by the `elif action == "new"` branch downstream.

Any future edit that adds a fallback like `action = "dropped"` (e.g., "if
the source vibe was already archived externally, drop instead of merge")
will silently execute BOTH the keep handler AND the drop handler in the
same iteration — double-archiving, double-incrementing counters, or
duplicate Plex API calls.

This is also a maintainability landmine: the structure reads as a
case-statement pattern, and reviewers may assume mutual exclusion.

**Fix:** Convert the dispatch chain to a single `if/elif/elif/.../else`
chain:

```python
if action in ("keep", "renamed_from"):
    ...
    if existing is None:
        action = "new"
    else:
        ... handle keep/renamed ...
        continue  # don't fall through to "new" handler
elif action == "dropped":
    ...
elif action == "merged_from":
    ...
elif action == "split_from":
    ...
elif action == "new":
    ...
else:
    logger.warning("Unknown action %r in recluster commit", action)
```

If the fallback-to-new behavior must be preserved, lift the `"new"`
handling out into a helper function and call it explicitly from the
fallback branch (don't rely on fall-through into a separate elif chain).

## Warnings

### WR-01: Per-track Lock LRU eviction breaks serialization invariant

**File:** `app/services/vibe_service.py:91-105`
**Issue:** `_get_lock` evicts the LRU entry once the dict exceeds 100
entries:

```python
def _get_lock(rating_key: str) -> asyncio.Lock:
    if rating_key in _slot_in_locks:
        _slot_in_locks.move_to_end(rating_key)
        return _slot_in_locks[rating_key]
    lock = asyncio.Lock()
    _slot_in_locks[rating_key] = lock
    if len(_slot_in_locks) > _LOCK_DICT_MAX:
        _slot_in_locks.popitem(last=False)  # may evict an in-use lock
    return lock
```

If the evicted lock is currently held by another coroutine (still inside
`async with lock:`) and a SECOND request for the SAME ratingKey arrives,
`_get_lock` will create a NEW Lock instance and insert it. The new
coroutine acquires the new lock immediately (it's not held), so two
concurrent slot_track invocations for the same ratingKey now race —
violating the per-track serialization invariant the lock dict is supposed
to provide (D-16 / Pitfall 23).

For the user's profile (~1-2 concurrent webhook+poll events), this is
unlikely to surface in practice. But under burst conditions or future
fan-out (Phase 7+ Suggestions consumption), it WILL fail. The bound is
also defended only by the cap, not by usage pattern: `reslot_all_rated_tracks`
iterates ~460 rated tracks, populating ~460 distinct lock entries.

**Fix:** Don't evict locks that are currently held. The simplest robust
form is to track a per-lock reference count and only evict once the count
drops to zero. Alternative: use a `WeakValueDictionary` so locks held
elsewhere (i.e., by the `async with` block) keep themselves alive
automatically.

A minimal patch:

```python
import weakref
_slot_in_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)

def _get_lock(rating_key: str) -> asyncio.Lock:
    lock = _slot_in_locks.get(rating_key)
    if lock is None:
        lock = asyncio.Lock()
        _slot_in_locks[rating_key] = lock
    return lock
```

The `async with lock:` block holds a strong reference, preventing GC.
Once no holder remains, GC reclaims the entry. No bound needed.

---

### WR-02: Recluster banner can display percentages > 100% on merge/split

**File:** `app/routers/api_vibes.py:143-158` (and `partials/push_to_plex_banner.html:17-22`)
**Issue:** `_state_to_banner_payload` computes:

```python
done = (_recluster_status.created
        + _recluster_status.archived
        + _recluster_status.renamed)
```

It assigns this to the banner's `created` field. But `total = len(targets)`
where `targets` is the proposal list. For a `merged_from` proposal that
merges 2 sources → 1 new vibe, that single proposal increments
`archived` twice + `created` once = 3, while `total` for that proposal is
1. Net result: `((3 / 1) * 100) | int = 300%` rendered as "300%" in the
banner percentage display, and "3 / 1 playlists" in the headline.

For a re-cluster session with multiple merges, the displayed numbers can
be wildly misleading.

**Fix:** Either (a) use only `_recluster_status.created` as the headline
count and rename the banner template's `created` semantics to be specifically
the "new playlists count", OR (b) compute `total` to reflect the number of
operations, not proposals (e.g., total = sum of expected created+archived+renamed
ahead of time). Option (a) is simpler:

```python
return {
    "state": _recluster_status.state,
    "created": _recluster_status.created,
    "total": _recluster_status.total,  # number of proposals
    "error": _recluster_status.last_error,
}
```

Or pass through separate fields and update the template to render
"Created N · Archived M · Renamed K".

---

### WR-03: Dead/unused `existing_managed_vibe_ids` signals incomplete idempotency

**File:** `app/routers/api_setup.py:351-355`
**Issue:** `finalize` computes:

```python
# Idempotent Retry support: skip vibes whose ManagedPlaylist already links.
existing_managed_vibe_ids = set(
    row.vibe_id
    for row in session.exec(select(ManagedPlaylist)).all()
    if row.vibe_id is not None
)
```

…but `existing_managed_vibe_ids` is never read. The comment claims this
supports idempotent retry; the implementation does not. On a partial
failure, the user clicks "Retry" → `finalize` is re-invoked → it iterates
over EVERY proposal again, INSERTing new Vibe rows even for proposals
whose ManagedPlaylist already exists. Tests like
`test_finalize_integration_handles_partial_failure` only verify that
`len(vibes) >= 2` (loose ≥ assertion), so the duplicate-insert bug is
masked.

This produces orphan Vibe rows on every retry attempt and inflates the
vibe count over time. Compare to `recluster_commit` which has a real
idempotency check via `_read_existing_vibe_by_name_sync`.

**Fix:** Either implement the idempotency by name-match (mirroring the
recluster path) before the per-proposal try block, or delete the dead
variable + comment. The recluster pattern:

```python
# Skip proposals whose vibe already exists by name (D-23 idempotency).
existing_id = await asyncio.to_thread(
    _read_existing_vibe_by_name_sync, prop.name
)
if existing_id is not None:
    created_count += 1
    continue
```

Add a tighter test that asserts `len(vibes) == expected_count` (not `>=`)
across retry runs.

---

### WR-04: `maybe_reslot_pending_track` clears flag even when no slotting happened

**File:** `app/services/vibe_service.py:608-628`
**Issue:**

```python
async def maybe_reslot_pending_track(track_id: int) -> None:
    ...
    result = await slot_track(track.plex_rating_key)
    if result.pending is False:
        # Slot succeeded (or vibes don't exist yet). Clear the flag either
        # way — slot_track already handled the no-vibes-yet case.
        await asyncio.to_thread(_set_pending_slot_in_sync, track_id, False)
```

`slot_track` returns `pending=False` in the "no vibes yet — pre-wizard
state" branch (line 386-394) without actually slotting the track. The
caller then clears the `pending_slot_in` flag. Subsequent flag-driven
recovery paths (D-17 retroactive slot-in) will skip this track even
after vibes are created.

The mitigation in the comment ("slot_track already handled the
no-vibes-yet case") is incorrect: slot_track returns successfully but
does NOT enqueue any future work. Once the wizard creates vibes, this
track will only be slotted by (a) the next RatingChanged event, or (b)
the user manually clicking "Reslot all rated tracks" on /debug/vibes.

**Fix:** Distinguish the "successfully slotted" case from the "skipped
because no vibes yet" case in the SlotInResult:

```python
@dataclass
class SlotInResult:
    track_id: int
    rating_key: str
    primary_vibe_id: Optional[int]
    ...
    pending: bool
    skipped_no_vibes: bool = False  # NEW
    skipped_manual: bool = False
```

Set `skipped_no_vibes=True` in the no-vibes branch of `_slot_track_inner`.
In `maybe_reslot_pending_track`, only clear the flag when
`result.primary_vibe_id is not None OR result.pending is False AND not
result.skipped_no_vibes`.

---

### WR-05: Refine endpoint accepts empty `message` and burns an LLM call

**File:** `app/routers/api_setup.py:238-299`
**Issue:** The refine endpoint declares `message: Annotated[str, Form()]`
which requires the field to be present but allows empty strings. A user
submitting an empty textarea triggers a full LLM call with `user_message=""`
and increments `state.refinement_turn_count`. With the daily LLM-call cap
(50/day from Phase 5 LLMUsage scaffolding) being the underlying cost
circuit breaker, a stuck-button accident could waste turns toward the cap.

There's no client-side guard either (the textarea has no `required`
attribute and the submit button doesn't disable on empty input).

**Fix:** Reject empty messages at the server before calling the LLM:

```python
if not message.strip():
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "partials/refine_error.html",
        {
            "reason": "Please enter what to change",
            "refinement_turn_count": state.refinement_turn_count,
            "draft_proposals": prior,
        },
    )
```

Add `required` to the `<textarea>` in `partials/refinement_input.html` as
a defense-in-depth client-side guard.

---

### WR-06: `recluster_modal` auto-focus runs at hidden-state mount, not on open

**File:** `app/templates/partials/recluster_modal.html:18-19`
**Issue:** The modal uses Alpine's `x-init` to focus the dismiss button:

```html
<div class="bg-surface-card rounded-lg p-6 max-w-md w-full border border-border"
     x-init="$nextTick(() => $refs.keep && $refs.keep.focus())">
```

`x-init` runs once at component mount. At that point `open=false` and
`x-show` has applied `display: none`, so the modal AND its children are
hidden. `focus()` on a hidden element is a no-op in all modern browsers.
When the modal is later opened via `@open-recluster.window`, no focus
shift happens — the user must reach for the touch target with no keyboard
hint, and screen-reader users won't know the modal opened.

**Fix:** Move the focus into a watcher on `open`:

```html
<div x-data="{ open: false }"
     x-show="open"
     @open-recluster.window="open = true; $nextTick(() => $refs.keep && $refs.keep.focus())"
     ...>
```

Or use Alpine's `x-effect` to react to `open` changes. The
`test_recluster_modal_alpine_x_show_dismiss_paths` test only asserts that
the `$nextTick` and `$refs.keep` strings exist; it doesn't verify the
focus actually fires on open, so the bug passes the test.

---

### WR-07: `propose/init` idempotency claim is untested

**File:** `app/routers/api_setup.py:198-235`
**Issue:** The endpoint claims:

> "Idempotent: if SetupState.draft_proposals_json is already populated AND
> forced_k is None, returns the existing cards WITHOUT calling the LLM
> (cost guard — T-06-03-07 mitigation)."

This guard does work (line 214-217). But there's no test covering it. A
regression that removes the short-circuit would burn LLM calls on every
HTMX page reload of /setup/propose without any test failing. Given the
explicit cost-guard framing, this is a critical-path regression risk.

**Fix:** Add a test:

```python
def test_propose_init_is_idempotent_when_draft_exists(client_with_phase6, test_engine, monkeypatch):
    call_count = {"n": 0}
    async def fake_initial(forced_k=None):
        call_count["n"] += 1
        return _canned_proposal_set()
    monkeypatch.setattr("app.routers.api_setup.initial_cluster_proposal", fake_initial)

    with Session(test_engine) as session:
        _seed_setup_state(session, draft_proposals_json=_make_proposal_set_json(3))

    # First call: serves from cache (no LLM)
    r = client_with_phase6.post("/api/setup/propose/init")
    assert r.status_code == 200
    assert call_count["n"] == 0

    # Forced k bypasses the cache (calls LLM)
    r = client_with_phase6.post("/api/setup/propose/init", data={"forced_k": "5"})
    assert r.status_code == 200
    assert call_count["n"] == 1
```

---

### WR-08: `_migrate_add_columns` opens raw sqlite3 connection, bypasses WAL pragma

**File:** `app/database.py:48-162`
**Issue:** The migration helper opens its own `sqlite3.connect(db_path)`
connection rather than using the SQLAlchemy engine. The engine's `connect`
event-listener that sets `journal_mode=WAL` and `foreign_keys=ON` does
NOT fire on this raw connection. The migration commits in default
"delete" journal mode, which can briefly conflict with WAL-mode readers
from the engine pool (though SQLite's mode-switching is usually
forgiving).

A more concrete concern: `foreign_keys=OFF` (default) on the migration
connection means the `UPDATE track SET energy = ...` recalculation runs
without FK enforcement. There are no FK columns on `track` itself, so
this is benign today, but the next schema additive (e.g., a new FK to
Vibe.id from Track) would silently bypass FK validation during migration.

**Fix:** Either run migrations through the engine connection (which
triggers the pragma listener):

```python
def _migrate_add_columns(engine) -> None:
    with engine.connect() as conn:
        # Use raw cursor on the engine's connection so WAL + FK pragmas
        # are already applied.
        ...
```

Or apply the same pragmas to the raw connection at the top of the
function:

```python
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("PRAGMA journal_mode=WAL")
cursor.execute("PRAGMA foreign_keys=ON")
```

---

### WR-09: `_finalize_status` and `_recluster_status` have no concurrency guard

**File:** `app/routers/api_setup.py:79` + `app/routers/api_vibes.py:121`
**Issue:** Both endpoints mutate module-level singletons without locking:

```python
_finalize_status: FinalizeStatus = FinalizeStatus()
...
_finalize_status = FinalizeStatus(state="running", ...)
```

Two simultaneous POSTs to `/api/setup/finalize` (or `/api/vibes/recluster/commit`)
will both proceed and clobber each other's status. There's no guard like
`if _finalize_status.state == "running": return 409`. For a single-user
deployment behind Tailscale this is unlikely in practice, but the wizard's
"Retry" button on the failed banner can be double-clicked, and HTMX
doesn't debounce.

The double-finalize would also create duplicate Vibe rows (the dead
variable WR-03 was supposed to prevent this).

**Fix:** Reject re-entrant calls when status is `"running"`:

```python
if _finalize_status.state == "running":
    return templates.TemplateResponse(
        request,
        "partials/push_to_plex_banner.html",
        {
            "state": "running",
            "created": _finalize_status.created,
            "total": _finalize_status.total,
            "error": None,
        },
    )
```

Apply the same guard to `recluster_commit`. Optionally use an asyncio.Lock
at module scope to serialize properly.

## Info

### IN-01: Unused `import json` in both Phase 6 routers

**File:** `app/routers/api_setup.py:18` and `app/routers/api_vibes.py:29`
**Issue:** `import json` is at module top in both routers but never used
directly — the only `json.loads` / `json.dumps` calls are inside lazy
imports elsewhere (e.g., in vibe_service via `import json as _json`).
Module-top `import json` is dead.

**Fix:** Remove the unused imports.

---

### IN-02: Duplicated `(None, 0, 0.0)` membership check is redundant

**File:** `app/services/vibe_service.py:355` and `:620`
**Issue:** `track.user_rating in (None, 0, 0.0)` — `0 == 0.0` in Python,
so the tuple `(None, 0, 0.0)` matches the same set as `(None, 0)`. Minor
clutter.

**Fix:** Either `track.user_rating in (None, 0)` (concise), or
`track.user_rating is None or track.user_rating == 0` (more explicit).

---

### IN-03: Stale stub template file remains after Plan 04 replaces it

**File:** `app/templates/pages/debug_vibes_stub.html`
**Issue:** Plan 03 introduced `debug_vibes_stub.html` as a placeholder.
Plan 04 replaced it with `debug_vibes.html` and the route now renders the
real page. The stub file is no longer referenced anywhere but remains in
the repo.

**Fix:** Delete `app/templates/pages/debug_vibes_stub.html`.

---

### IN-04: `total == 0` edge case in finalize silently completes wizard

**File:** `app/routers/api_setup.py:436-477`
**Issue:** If every proposal has `action == "dropped"`, then
`targets = []` and `total = 0`. The for-loop runs zero times,
`created_count = 0`, and `created_count == total` is `True (0==0)`.
The wizard transitions to step="done" with no vibes created and no
Plex playlists. The user sees the "completed" banner ("Your 0 vibe
playlists are live in Plex") with no recoverable state.

**Fix:** Guard the success branch on `total > 0`:

```python
if total > 0 and created_count == total:
    state.step = "done"
    ...
elif total == 0:
    # All proposals were dropped — refuse to finalize, stay in confirming.
    ...
```

---

### IN-05: Banner templates compute percentage with Jinja arithmetic on potentially zero values

**File:** `app/templates/partials/push_to_plex_banner.html:17,21`
**Issue:** `{{ ((created / total) * 100) | int if total else 0 }}%` is
defensive. But when WR-02's bug fires (`done > total`), the int conversion
clamps display but the percentage still reads >100%. Already covered by
WR-02; flagging here for the template author too.

---

### IN-06: `pages.debug_vibes` performs N+1 queries for slot-in log hydration

**File:** `app/routers/pages.py:386-412`
**Issue:** For each of the last 20 SlotInLog rows, the route queries the
Track table (1 query) and queries Vibe (1-2 queries). Per page load: up
to 20 × 3 = 60 queries plus the vibe-member-count loop. Performance is
out of scope per review rules, but mentioned for future optimization.

**Fix:** Pre-fetch with a JOIN or a single `WHERE id IN (...)` query.
Defer if the page load is sub-100ms in practice (likely fine for a
debug surface).

---

_Reviewed: 2026-05-09T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
