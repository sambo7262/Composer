---
phase: 260510-sht-fix-map-user-vibes-to-clusters-attribute
plan: 01
type: tdd
wave: 1
depends_on: []
files_modified:
  - app/services/vibe_clusterer.py
  - tests/test_vibe_clusterer.py
autonomous: true
requirements:
  - QUICK-260510-SHT
gap_closure: false

must_haves:
  truths:
    - "POST /api/setup/propose/init no longer raises AttributeError when reaching the LLM call inside map_user_vibes_to_clusters"
    - "map_user_vibes_to_clusters obtains an Anthropic client by passing a real SQLModel Session (not None) into get_anthropic_client_v2"
    - "A regression test exercises the production code path WITHOUT monkeypatching vibe_clusterer.get_anthropic_client_v2, so future None-session regressions are caught"
    - "All 35 existing vibe_clusterer tests still pass (no behavioral regression)"
  artifacts:
    - path: "app/services/vibe_clusterer.py"
      provides: "Fixed map_user_vibes_to_clusters that opens a Session before calling get_anthropic_client_v2"
      contains: "with Session(get_engine()) as session:"
    - path: "tests/test_vibe_clusterer.py"
      provides: "Regression test test_map_user_vibes_passes_real_session_to_anthropic_factory"
      contains: "test_map_user_vibes_passes_real_session_to_anthropic_factory"
  key_links:
    - from: "app/services/vibe_clusterer.py::map_user_vibes_to_clusters (line ~1145)"
      to: "app/services/anthropic_client.py::get_anthropic_client_v2 (line 164)"
      via: "session argument (must be Session, never None)"
      pattern: "with Session\\(get_engine\\(\\)\\) as session:\\s+client = get_anthropic_client_v2\\(session\\)"
    - from: "tests/test_vibe_clusterer.py regression test"
      to: "app.services.anthropic_client.get_anthropic_client_v2"
      via: "monkeypatch on the UPSTREAM factory (not the vibe_clusterer re-export shim) — receives the real session"
      pattern: "monkeypatch.setattr\\(\\s*[\"']app\\.services\\.anthropic_client\\.get_anthropic_client_v2[\"']"
---

<objective>
Fix a production-only AttributeError in `map_user_vibes_to_clusters` that crashes
POST /api/setup/propose/init on the NAS. The bug: line 1145 passes `None` to
`get_anthropic_client_v2`, which then calls `session.exec(...)` on None.

The fix is a one-liner — replace the bare `get_anthropic_client_v2(None)` with
the working `with Session(get_engine()) as session: client = get_anthropic_client_v2(session)`
pattern that already exists at line 1259 of the same file.

The hard part is preventing the regression from happening again. Every existing
`map_user_vibes_to_clusters` test monkeypatches the module-local re-export shim
`vibe_clusterer.get_anthropic_client_v2`, so the real factory at
`anthropic_client.py:164` is never exercised. We add a regression test that
monkeypatches the UPSTREAM factory and asserts it receives a real `Session`,
not `None`.

Purpose: unblock the user-led clustering flow on the NAS without breaking the
existing 35-test suite, and lock in a guard so the next refactor can't silently
pass None through this path again.

Output: One regression test (RED then GREEN) and one 2-line code fix.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/STATE.md

<interfaces>
<!-- Key types and contracts extracted from the codebase. -->
<!-- Use these directly — no exploration needed. -->

From app/services/vibe_clusterer.py (imports already present, lines 52-54):
```python
from sqlmodel import Session, select
from app.database import get_engine
```

From app/services/vibe_clusterer.py (the buggy site, lines 1144-1145):
```python
# Lazy session — module-local re-export shim so tests can monkeypatch.
client = get_anthropic_client_v2(None)
```

From app/services/vibe_clusterer.py (the working pattern to copy, lines 1259-1260):
```python
with Session(get_engine()) as session:
    client = get_anthropic_client_v2(session)
```

From app/services/vibe_clusterer.py (the module-local shim, lines 1327-1330):
```python
def get_anthropic_client_v2(session):
    """Module-local re-export so tests can monkeypatch this name without import cycles."""
    from app.services.anthropic_client import get_anthropic_client_v2 as _factory
    return _factory(session)
```

From app/services/anthropic_client.py (the REAL factory, lines 164-179):
```python
def get_anthropic_client_v2(session) -> AnthropicClient:
    from app.services.settings_service import get_setting, get_decrypted_credential
    setting = get_setting(session, "anthropic")       # ← crashes if session is None
    if not setting or not setting.is_configured:
        raise ValueError("Anthropic is not configured. Set up your API key in Settings first.")
    api_key = get_decrypted_credential(session, "anthropic")
    if not api_key:
        raise ValueError("Anthropic API key not found. Please reconfigure in Settings.")
    model = (setting.extra_config or {}).get("model_name", "claude-sonnet-4-6")
    return AnthropicClient(api_key, model)
```

From tests/test_vibe_clusterer.py (existing fixture used by every map_user_vibes test, lines 64-87):
```python
@pytest.fixture
def db_with_phase6(test_engine) -> Generator[Session, None, None]:
    """Create all Phase 5 + Phase 6 tables and yield a session."""
    # ... imports Track, ServiceConfig, Vibe, TrackVibe, SetupState, etc. ...
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)
```

From tests/test_vibe_clusterer.py (existing `_seed_tracks` helper, lines 90-155):
```python
def _seed_tracks(session: Session, count: int, *, well_separated: bool = False) -> None:
    # seeds `count` Tracks with energy/tempo/danceability/valence + user_rating=8.0;
    # well_separated=True builds 3 Gaussian clusters suitable for KMeans.
```

From tests/test_vibe_clusterer.py (existing patch pattern for engine-redirection, line 852):
```python
monkeypatch.setattr(vc, "get_engine", lambda: test_engine)
```

From tests/test_vibe_clusterer.py (existing pattern that monkeypatches the WRONG name and thus hides this bug, used in 11 tests — examples at lines 233-236, 265-268, 448-451):
```python
monkeypatch.setattr(
    "app.services.vibe_clusterer.get_anthropic_client_v2",   # ← the shim, NOT the real factory
    lambda s: fake_client,                                    # ← `s` is discarded — None goes unnoticed
)
```
</interfaces>

# Phase 5 Conventions That Apply

From CLAUDE.md "Phase 5 Conventions":
- **PlexAPI is sync, wrap in asyncio.to_thread** — N/A here (no Plex calls).
- **Sync DB read inside async** — The working pattern at line 1259 uses a sync
  `with Session(get_engine())` block inside `async def`. This is acceptable
  for the short credential read (one row from `ServiceConfig`) and is the
  existing house pattern. Do NOT introduce `asyncio.to_thread` for this DB
  read — it would diverge from line 1259 without benefit.

# Why Tests Didn't Catch This

Every existing test for `map_user_vibes_to_clusters` and friends monkeypatches
`app.services.vibe_clusterer.get_anthropic_client_v2` (the module-local
re-export shim defined at line 1327). The mock's signature is `lambda s:
fake_client`, which silently swallows whatever `s` is — including `None`. The
real factory at `anthropic_client.py:164` is never exercised, so the
`session.exec(...)` crash only surfaces in production.

The fix-side test must monkeypatch the UPSTREAM name
(`app.services.anthropic_client.get_anthropic_client_v2`), which the shim
imports lazily inside its function body. That way the shim still runs, still
receives the real session argument, and the test can assert on what it got.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add failing regression test that asserts the upstream factory receives a real Session</name>
  <files>tests/test_vibe_clusterer.py</files>
  <behavior>
    - Test name: `test_map_user_vibes_passes_real_session_to_anthropic_factory`
    - Location: append to the "Phase 6.1 Plan 01 Task 2 — map_user_vibes_to_clusters + helpers" section (after the existing `test_validate_mapping_permutation_passes_valid_response` test near line 949).
    - Marker: `@pytest.mark.asyncio`
    - Fixtures: `db_with_phase6`, `monkeypatch`, plus `test_engine` redirection via `monkeypatch.setattr(vc, "get_engine", lambda: test_engine)` (mirrors line 852).
    - Setup:
      1. Seed 60 well-separated tracks via `_seed_tracks(db_with_phase6, 60, well_separated=True)`.
      2. Insert a configured `ServiceConfig` row for `service="anthropic"` so the real factory wouldn't bail on `not setting.is_configured`. Use `app.models.settings.ServiceConfig` (already imported by the fixture at line 70). Minimum viable row: `ServiceConfig(service="anthropic", is_configured=True, encrypted_credential=b"placeholder", extra_config={"model_name": "claude-sonnet-4-6"})`. If you cannot insert a real encrypted credential without invoking the encryption helper, instead monkeypatch `app.services.anthropic_client.get_decrypted_credential` to return `"fake-key"` and `app.services.anthropic_client.get_setting` to return a `MagicMock(is_configured=True, extra_config={"model_name": "claude-sonnet-4-6"})`. The point is to let the upstream factory RUN against a real Session without exploding on credential decryption — the assertion is about the Session, not the credential.
      3. Monkeypatch `AnthropicClient.__init__` (or wrap it) so the constructor at `anthropic_client.py:179` doesn't try to create a real Anthropic SDK client. Alternative cleaner approach: monkeypatch `app.services.anthropic_client.AnthropicClient` itself to a MagicMock class whose instances expose `call_with_structured_output = AsyncMock(return_value=<a valid LLMVibeMappingResponse>)`.
      4. Build a captured-session container: `seen_sessions: list = []`.
      5. Wrap `app.services.anthropic_client.get_anthropic_client_v2` so that it appends `session` to `seen_sessions` BEFORE delegating to the real factory (or returning a stub client). Easiest form:
         ```python
         from app.services import anthropic_client as ac_module
         from sqlmodel import Session as _SessionCls

         seen_sessions = []
         original_factory = ac_module.get_anthropic_client_v2

         def spy(session):
             seen_sessions.append(session)
             return fake_client  # the MagicMock built in step 3

         monkeypatch.setattr(ac_module, "get_anthropic_client_v2", spy)
         ```
         CRITICAL: monkeypatch the attribute on the `anthropic_client` module object (or by string `"app.services.anthropic_client.get_anthropic_client_v2"`). Do NOT monkeypatch `app.services.vibe_clusterer.get_anthropic_client_v2` — that's the shim, and patching it makes the test useless (same blind spot as the existing 11 tests).
       6. Build a valid `LLMVibeMappingResponse` to return from `fake_client.call_with_structured_output`. Use 3 LLMVibeFit entries with `cluster_index=0,1,2`, `fit="strong"` (no `reason` field required when fit != "no_match"), and `user_name="a","b","c"`, `description="d"`. This shape passes `_validate_mapping_permutation` for `n=3` and lets the function reach a clean return.
    - Action: `await map_user_vibes_to_clusters(["alpha", "beta", "gamma"])`.
    - Assertions:
      1. `len(seen_sessions) == 1` — the factory was called exactly once.
      2. `seen_sessions[0] is not None` — THE BUG ASSERTION. This line fails on current code (the shim is bypassed by patching the upstream name, but `vibe_clusterer.py:1145` still passes `None` down through the shim → the spy still records None).
      3. `isinstance(seen_sessions[0], _SessionCls)` — stronger assertion that we passed a real `sqlmodel.Session` instance, not just any truthy object.
    - Why this works as RED-before-fix:
      The shim at line 1327 is `def get_anthropic_client_v2(session): from app.services.anthropic_client import get_anthropic_client_v2 as _factory; return _factory(session)`. Patching `anthropic_client.get_anthropic_client_v2` redirects `_factory` (lazy import inside the shim) but the shim still passes through whatever the CALLER passed in. So with the current bug at line 1145 (`get_anthropic_client_v2(None)`), the spy sees `None`. After the fix at line 1145 (`get_anthropic_client_v2(session)`), the spy sees the real Session.
  </behavior>
  <action>
    Implement the test above. RED phase: add the test, run it, confirm it FAILS with assertion `seen_sessions[0] is not None`. Do NOT yet apply the fix in Task 2 — the failing test is what makes this TDD.

    Commit the RED state:
    ```
    test(quick-260510-sht): add regression for map_user_vibes None session bug
    ```
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest tests/test_vibe_clusterer.py::test_map_user_vibes_passes_real_session_to_anthropic_factory -xvs 2>&amp;1 | tail -40</automated>
  </verify>
  <done>
    - New test exists in tests/test_vibe_clusterer.py.
    - Running it FAILS on the current `app/services/vibe_clusterer.py` (line 1145 still passes None).
    - The failure message is the `seen_sessions[0] is not None` assertion (or `isinstance(..., Session)`) — NOT a setup error, NOT an unrelated AttributeError, NOT a fixture mismatch. If the test fails for any other reason, fix the setup until the failure is purely the bug-detection assertion.
    - RED commit landed.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Apply the 2-line fix in vibe_clusterer.py — open a real Session before calling the factory</name>
  <files>app/services/vibe_clusterer.py</files>
  <behavior>
    - The regression test from Task 1 must transition from RED to GREEN.
    - All 35 pre-existing vibe_clusterer tests must remain GREEN.
    - No other behavior changes: the same `LLMVibeMappingResponse` is produced, the same retry-on-validation-failure flow runs, the same `_one_call` closure binds `client`.
  </behavior>
  <action>
    In `app/services/vibe_clusterer.py`, locate lines 1144-1145:
    ```python
        # Lazy session — module-local re-export shim so tests can monkeypatch.
        client = get_anthropic_client_v2(None)
    ```

    Replace with (mirroring the working pattern at line 1259-1260):
    ```python
        # Open a sync Session for the credential read (mirrors _call_llm_with_validation
        # at line ~1259). The AnthropicClient returned does NOT hold a Session reference
        # — it only captures the decrypted api_key + model_name during construction —
        # so the closure-captured `client` reference remains valid after the `with`
        # block exits and `_one_call` can safely use it. Test-side monkeypatching
        # still works via the module-local shim defined below.
        with Session(get_engine()) as session:
            client = get_anthropic_client_v2(session)
    ```

    Notes:
    - `Session` is already imported at line 52, `get_engine` at line 54 — no new imports needed.
    - Indentation: this block lives inside `async def map_user_vibes_to_clusters`, at the same indent level as the prior `user_prompt = _build_user_led_clustering_user_prompt(...)` block (4 spaces of function body indent inside the `async def`).
    - The `client` name remains visible to the nested `async def _one_call` below (Python closures bind by name, not by `with` block — names defined inside `with` persist after the block).
    - Do NOT change the module-local shim at line 1327 — existing tests rely on monkeypatching `vibe_clusterer.get_anthropic_client_v2`.
    - Do NOT change anything else in the function. No scope creep.

    After editing, run the regression test from Task 1 (must now PASS), then the full vibe_clusterer test file (all 35 + 1 new = 36 tests must PASS).

    Commit the GREEN state:
    ```
    fix(quick-260510-sht): open Session before get_anthropic_client_v2 in map_user_vibes_to_clusters

    The user-led clustering path passed None into the factory, which then called
    session.exec(...) and crashed on the NAS. Mirror the working pattern from
    _call_llm_with_validation (line 1259) which opens a sync Session via
    `with Session(get_engine())`.

    Regression test asserts the upstream factory receives a real Session,
    monkeypatching anthropic_client.get_anthropic_client_v2 (not the
    vibe_clusterer shim) so future None-session regressions cannot slip past
    the test suite the way this one did.
    ```
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest tests/test_vibe_clusterer.py -x 2>&amp;1 | tail -20</automated>
  </verify>
  <done>
    - `pytest tests/test_vibe_clusterer.py` reports all tests passing (36 = 35 existing + 1 new).
    - `pytest tests/test_vibe_clusterer.py::test_map_user_vibes_passes_real_session_to_anthropic_factory` passes specifically.
    - `git diff app/services/vibe_clusterer.py` shows ONLY the 2-line replacement at lines 1144-1145 (plus a few comment lines). No other diffs in this file.
    - `grep -n "get_anthropic_client_v2(None)" app/services/vibe_clusterer.py` returns nothing (the None-passing call is gone).
    - GREEN commit landed.
  </done>
</task>

</tasks>

<verification>

Final, end-to-end verification after both tasks:

```bash
cd /Users/Oreo/Projects/Composer

# 1. The regression test exists and is wired correctly.
grep -n "test_map_user_vibes_passes_real_session_to_anthropic_factory" tests/test_vibe_clusterer.py

# 2. The bug line is gone.
grep -n "get_anthropic_client_v2(None)" app/services/vibe_clusterer.py
# Expect: no output.

# 3. The fix line is present.
grep -n "with Session(get_engine()) as session:" app/services/vibe_clusterer.py
# Expect: 2 hits — line ~1145 (new) and line ~1259 (pre-existing).

# 4. The regression test patches the UPSTREAM name (not the shim).
grep -A2 "test_map_user_vibes_passes_real_session_to_anthropic_factory" tests/test_vibe_clusterer.py | grep "anthropic_client.get_anthropic_client_v2"
# Expect: at least one hit referencing app.services.anthropic_client (NOT vibe_clusterer).

# 5. Full vibe_clusterer suite green.
pytest tests/test_vibe_clusterer.py -q

# 6. Sanity — no breakage elsewhere in the services layer.
pytest tests/test_vibe_clusterer.py tests/test_anthropic_client.py 2>&1 | tail -5
```

</verification>

<success_criteria>

- [ ] `tests/test_vibe_clusterer.py` contains `test_map_user_vibes_passes_real_session_to_anthropic_factory`.
- [ ] The test monkeypatches `app.services.anthropic_client.get_anthropic_client_v2` (NOT the vibe_clusterer shim).
- [ ] The test asserts `isinstance(seen_session, sqlmodel.Session)` — fails on None.
- [ ] Test was verified to FAIL on un-fixed code (RED), then PASS on fixed code (GREEN).
- [ ] `app/services/vibe_clusterer.py:1145` no longer passes `None` to `get_anthropic_client_v2`.
- [ ] The new code uses `with Session(get_engine()) as session:` mirroring line 1259's working pattern.
- [ ] All 35 existing vibe_clusterer tests still pass.
- [ ] Two atomic commits landed: one RED test, one GREEN fix.
- [ ] No changes outside `app/services/vibe_clusterer.py` and `tests/test_vibe_clusterer.py` (verified by `git diff --name-only HEAD~2`).
- [ ] No scope creep — the other code-review BLOCKERs noted by the user are NOT touched in this plan.

</success_criteria>

<output>
After completion, create `.planning/quick/260510-sht-fix-map-user-vibes-to-clusters-attribute/260510-sht-01-SUMMARY.md` summarizing:
- The bug (None session → AttributeError on NAS).
- The fix (2-line change mirroring line 1259's pattern).
- The regression guard (test patches upstream factory, not the shim).
- Why the existing 35 tests didn't catch it (all patched the shim with `lambda s: ...` discarding the session arg).
- Test count: 35 → 36 (one new regression test).
</output>
