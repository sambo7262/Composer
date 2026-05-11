---
phase: 260510-sht-fix-map-user-vibes-to-clusters-attribute
plan: 01
subsystem: vibe-clustering
type: hotfix
tags: [hotfix, regression-guard, anthropic-client, sqlmodel-session, tdd]

requires:
  - app.services.anthropic_client.get_anthropic_client_v2
  - app.database.get_engine
  - sqlmodel.Session
provides:
  - Crash-free POST /api/setup/propose/init for user-led clustering on the NAS
  - Regression guard against future None-session leaks into the Anthropic factory
affects:
  - app/services/vibe_clusterer.py (function: map_user_vibes_to_clusters, line ~1145)
  - tests/test_vibe_clusterer.py (new test appended after line 1252)

tech-stack:
  added: []
  patterns:
    - "Open a sync Session via `with Session(get_engine())` immediately before any get_anthropic_client_v2(session) call (mirrors _call_llm_with_validation pattern)"
    - "Regression tests for the Anthropic factory boundary must monkeypatch `app.services.anthropic_client.get_anthropic_client_v2`, NOT `app.services.vibe_clusterer.get_anthropic_client_v2` (the re-export shim) — patching the shim with `lambda s: ...` silently discards the session argument and hides None-passing bugs"

key-files:
  created: []
  modified:
    - path: app/services/vibe_clusterer.py
      reason: "Replace `get_anthropic_client_v2(None)` with `with Session(get_engine()) as session: client = get_anthropic_client_v2(session)` at line ~1145 in map_user_vibes_to_clusters. Mirrors the working pattern at line ~1266 in _call_llm_with_validation."
    - path: tests/test_vibe_clusterer.py
      reason: "Append test_map_user_vibes_passes_real_session_to_anthropic_factory which spies on the upstream Anthropic factory (not the vibe_clusterer shim) and asserts the session reaching the real factory is a sqlmodel.Session instance, not None. RED → GREEN regression guard."

decisions:
  - id: D-260510-SHT-01
    title: "Use upstream-name monkeypatch in regression test, not the shim"
    decision: "Monkeypatch app.services.anthropic_client.get_anthropic_client_v2 (the real factory name), not app.services.vibe_clusterer.get_anthropic_client_v2 (the module-local re-export shim)."
    rationale: "The shim at vibe_clusterer.py:1327 does a lazy import inside its function body and re-resolves the upstream name at call time. Patching the upstream module attribute means the shim still runs and still receives the session that the caller passed in — the spy records the value that crossed the factory boundary. Patching the shim instead reproduces the exact blind spot that hid this bug from the existing 35-test suite: 11 tests use `lambda s: fake_client` which silently swallows whatever `s` is, including None."
    alternatives_considered:
      - "Patch vc.get_anthropic_client_v2 (the shim) — REJECTED, would replicate the existing blind spot."
      - "Insert a real ServiceConfig row + decryptable credential — REJECTED, requires invoking the encryption helper or stubbing get_setting/get_decrypted_credential, adds setup surface area unrelated to the actual assertion (the assertion is about the Session, not the credential)."

metrics:
  duration_minutes: 4
  completed_date: "2026-05-11"
  tasks_completed: 2
  files_changed: 2
  commits:
    - hash: 078008b
      type: test
      message: "test(quick-260510-sht): add regression for map_user_vibes None session bug"
    - hash: 03dfb87
      type: fix
      message: "fix(quick-260510-sht): open Session before get_anthropic_client_v2 in map_user_vibes_to_clusters"
  test_count_before: 35
  test_count_after: 36
  test_pass_rate: "36/36 (100%)"
---

# Quick Task 260510-sht: Fix map_user_vibes_to_clusters AttributeError Summary

One-liner: Replaced `get_anthropic_client_v2(None)` with `with Session(get_engine()) as session: client = get_anthropic_client_v2(session)` in `map_user_vibes_to_clusters`, plus a regression test that monkeypatches the upstream factory name so future None-session leaks cannot slip past the suite again.

## Objective Recap

Fix a production-only AttributeError that crashed `POST /api/setup/propose/init` on the NAS. The crash originated at `app/services/vibe_clusterer.py:1145` where `get_anthropic_client_v2(None)` was passed into the real factory at `app/services/anthropic_client.py:164`, which then called `session.exec(...)` on `None`.

The fix is a 2-line replacement that mirrors the working pattern already used at `app/services/vibe_clusterer.py:1266` inside `_call_llm_with_validation`. The accompanying regression test prevents the bug from recurring by patching the upstream factory name (`app.services.anthropic_client.get_anthropic_client_v2`) rather than the module-local re-export shim that the existing 11 `map_user_vibes_*` tests all patch.

## What Was Built / Changed

### Task 1 — RED: regression test (commit `078008b`)

Added `test_map_user_vibes_passes_real_session_to_anthropic_factory` to `tests/test_vibe_clusterer.py` (after line 1252).

- `@pytest.mark.asyncio`, uses `monkeypatch`.
- Mocks `vc._aggregate_rated_set_sync` to return a synthetic 90-track rated set with 3 well-separated 4-D Gaussian clusters (same pattern as `test_map_user_vibes_server_populates_seed_track_indices_seed_tracks_members`).
- Stubs `vc.materialize_clusters` to a no-op (avoids the real DB re-query inside materialize).
- Critically: monkeypatches `app.services.anthropic_client.get_anthropic_client_v2` with a spy `_spy(session)` that appends `session` to `seen_sessions` and returns a `fake_client` (`AsyncMock` returning a valid `LLMVibeMappingResponse` with 3 mappings, cluster_index 0/1/2, all `fit="strong"` so the permutation validator accepts).
- Calls `await map_user_vibes_to_clusters(["alpha", "beta", "gamma"])`.
- Asserts:
  1. `len(seen_sessions) == 1` (shim delegated exactly once).
  2. `seen_sessions[0] is not None` — **the bug assertion**.
  3. `isinstance(seen_sessions[0], sqlmodel.Session)` (stronger assertion).

Verified RED: failure was the `seen_sessions[0] is not None` assertion exactly — not a setup error, not an unrelated AttributeError, not a fixture mismatch.

### Task 2 — GREEN: 2-line fix (commit `03dfb87`)

In `app/services/vibe_clusterer.py:1144-1145`, replaced:
```python
# Lazy session — module-local re-export shim so tests can monkeypatch.
client = get_anthropic_client_v2(None)
```
with:
```python
# Open a sync Session for the credential read (mirrors
# _call_llm_with_validation at line ~1259). The AnthropicClient returned
# does NOT hold a Session reference — it only captures the decrypted
# api_key + model_name during construction — so the closure-captured
# `client` reference remains valid after the `with` block exits and
# `_one_call` can safely use it. Test-side monkeypatching still works
# via the module-local shim defined below at line ~1327.
with Session(get_engine()) as session:
    client = get_anthropic_client_v2(session)
```

`Session` and `get_engine` were already imported at lines 52 and 54 respectively — no new imports needed.

## Tests

| Suite | Before | After | Status |
| --- | --- | --- | --- |
| `tests/test_vibe_clusterer.py` | 35 | 36 | 36/36 PASS |
| `tests/test_anthropic_client.py` (sanity) | 6 | 6 | 6/6 PASS |
| Combined (regression sanity) | 41 | 42 | 42/42 PASS |

Verification commands executed:
- `pytest tests/test_vibe_clusterer.py::test_map_user_vibes_passes_real_session_to_anthropic_factory -xvs` → fail before fix, pass after fix.
- `pytest tests/test_vibe_clusterer.py -v` → 36/36 PASS after fix.
- `pytest tests/test_vibe_clusterer.py tests/test_anthropic_client.py` → 42/42 PASS after fix.

## Why the existing 35 tests didn't catch this

Every `map_user_vibes_*` test in the suite (11 of them) patches the module-local re-export shim:
```python
patch.object(vc, "get_anthropic_client_v2", return_value=fake_client)
```
The mock's signature collapses the session argument — `return_value=fake_client` means the replacement is called as `fake_client(whatever_session_was_passed)` is never even invoked; the patched name is returned-from immediately. So `None` flowing through the shim path was invisible.

The new regression test fixes this by patching the **upstream module attribute** the shim's lazy import resolves to:
```python
monkeypatch.setattr("app.services.anthropic_client.get_anthropic_client_v2", _spy)
```
With this in place, the shim's body still runs, the upstream name still resolves to the spy, and the spy observes precisely what the caller (line 1151 of vibe_clusterer) passed through. A future regression that re-introduces `None` at this site will fail this test on the `seen_sessions[0] is not None` assertion.

## Diff Scope

```
git diff --name-only HEAD~2 HEAD
  app/services/vibe_clusterer.py
  tests/test_vibe_clusterer.py
```

Both files touched; no other files modified. No scope creep — the other code-review BLOCKERs noted by the user during pre-dispatch were intentionally NOT touched.

## Deviations from Plan

None — plan executed exactly as written.

The plan offered a fallback approach for Task 1 setup that involved inserting a real `ServiceConfig` row plus stubbing `get_setting` / `get_decrypted_credential` to let the real upstream factory run. That fallback was not needed because the simpler approach (substituting the spy directly via `monkeypatch.setattr` on the upstream attribute name) keeps the assertion surface tight on the Session value — which is what we actually care about — while sidestepping the entire credential-decryption code path. This was the plan's preferred path, not a deviation.

## Authentication Gates

None encountered.

## Known Stubs

None introduced. The bug-line was a stub-passing-None pattern; it has been replaced with a real Session.

## Threat Flags

None — this is a pure refactor of a credential-read code path inside a function that was already authenticated by the surrounding `/api/setup/propose/init` route. No new network endpoints, auth paths, file access, or schema changes.

## Decisions Made

1. **D-260510-SHT-01 — Patch the upstream factory name in the regression test, not the shim.** Rationale and alternatives documented in frontmatter `decisions`. This is the single design choice that makes the regression guard durable; without it the new test would replicate the existing 35-test blind spot and the bug could recur.

## Self-Check: PASSED

- `app/services/vibe_clusterer.py` exists: FOUND
- `tests/test_vibe_clusterer.py` exists: FOUND
- Commit `078008b` (RED test) exists: FOUND
- Commit `03dfb87` (GREEN fix) exists: FOUND
- Bug-line `get_anthropic_client_v2(None)` removed (grep count = 0): VERIFIED
- Fix-line `with Session(get_engine()) as session:` present at line 1151 (new) + line 1266 (pre-existing): VERIFIED (4 hits total in file, 2 inside the LLM-client paths)
- Regression test monkeypatches `app.services.anthropic_client.get_anthropic_client_v2`: VERIFIED
- 36/36 vibe_clusterer tests pass: VERIFIED
- No files outside the planned scope modified: VERIFIED
