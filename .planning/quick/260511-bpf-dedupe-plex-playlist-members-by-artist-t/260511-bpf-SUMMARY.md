---
phase: quick-260511-bpf
plan: 01
subsystem: vibe-playlist-push
tags: [bugfix, dedupe, vibe-service, plex-playlist, phase-6-followup]
type: tdd
dependency_graph:
  requires:
    - app/services/vibe_service.py::_read_vibe_member_rating_keys_sync
    - app/models/track.py::Track  # plex_rating_key, artist, title columns
  provides:
    - app/services/vibe_service.py::_canonical_rating_keys_sync
  affects:
    - app/services/vibe_service.py::_read_vibe_member_rating_keys_sync (now pipes through helper)
tech-stack:
  added: []  # zero new deps — pure stdlib + existing sqlmodel
  patterns:
    - "Read-side dedupe at the playlist-push boundary; slot-in / TrackVibe rows untouched"
    - "Lexicographically smallest plex_rating_key per (artist, title) group — deterministic, idempotent"
key-files:
  created: []
  modified:
    - app/services/vibe_service.py    # +_canonical_rating_keys_sync helper, +1-line wire-in
    - tests/test_vibe_service.py      # _add_track extended with artist=/title=, +4 regression tests
decisions:
  - "Canonical pick = min(plex_rating_key) per (artist, title) group — proxies for Plex import order (original album imported before Best Of compilation)"
  - "Normalize via casefold().strip() on BOTH artist and title — handles 'Air ' vs 'air' transparently"
  - "Dedupe only at the playlist-push read boundary (_read_vibe_member_rating_keys_sync). TrackVibe rows for BOTH album versions still exist; slot-in pipeline untouched"
  - "Quality-aware dedupe (bitrate/format) DEFERRED to Phase 6.2 per ROADMAP — not a regression, a documented sequencing choice"
metrics:
  duration: ~6min
  completed: 2026-05-11
  tasks_executed: 2
  commits: 2
  tests_added: 4
  tests_passing: 150  # full quality-gate suite (10 test files)
requirements:
  - BUGFIX-260511-BPF-01
---

# Quick Task 260511-bpf: Dedupe Plex Playlist Members by (artist, title) Summary

**One-liner:** Cross-album duplicate-song dedupe at the vibe-playlist push boundary — `min(plex_rating_key)` per `(artist.casefold().strip(), title.casefold().strip())` group, deferring quality-aware dedupe to Phase 6.2.

## The Bug

User UAT (2026-05-11) surfaced a regression: when the user rated the **same song on two different albums** (e.g., "La Femme d'argent" on both `Moon Safari` and `Air — The Best Of`), the vibe's Plex playlist contained **both** copies. Each Plex track has its own `plex_rating_key`, so the slot-in pipeline correctly created **two** `TrackVibe` rows — one per ratingKey — and the playlist-push read returned both. End result: the same song appeared twice in the vibe playlist.

## The Fix

Dedupe at the **read boundary**, not the write boundary. `_read_vibe_member_rating_keys_sync` (the function that feeds `update_playlist_items`) now pipes its result through a new `_canonical_rating_keys_sync` helper:

1. Bulk-select `(plex_rating_key, artist, title)` for all ratingKeys in the input list.
2. Group by `(artist.casefold().strip(), title.casefold().strip())`.
3. Keep the **lexicographically smallest** `plex_rating_key` per group.
4. Return `sorted(canonical.values())` — deterministic, idempotent for repeated playlist pushes.

**TrackVibe rows are untouched.** Both album versions still have a slot-in row; only the read filters them. This means re-clustering, manual overrides, and pending-slot retroactive paths all see the full membership — only the Plex playlist itself dedupes.

## Heuristic Choice Rationale

**Why `min(plex_rating_key)` per group?** Plex assigns ratingKeys in **import order**. The user's original album was almost always imported before a "Best Of" compilation (compilations tend to land in the library later, often via a separate add). So the smallest ratingKey is a reliable proxy for "the original album version" — which is the one users intuitively expect to see in a vibe playlist.

This heuristic is:

- **Deterministic** across runs — same input → same output, idempotent for repeated pushes.
- **Cheap** — single `SELECT … WHERE plex_rating_key IN (…)` plus an in-memory dict reduction.
- **Conservative** — never collapses two genuinely-different songs (Test 2 covers this).
- **Tolerant** — case + whitespace normalization handles the messy real-world track metadata that Plex returns (Tests 3 and 4).

## Explicit Deferral: Quality-Aware Dedupe → Phase 6.2

**Quality-aware dedupe is intentionally NOT in scope for this bugfix.** The natural "better" heuristic would be to keep the **highest-quality** copy (e.g., FLAC over MP3, 320 kbps over 128 kbps). But:

- The `Track` schema today (Phase 6.1 baseline) has no `bitrate` or `file_format` columns.
- Phase 6.2 (per ROADMAP) explicitly adds those columns as part of the broader library-quality work.
- Trying to add quality awareness here would (a) require a schema migration outside the quick-task scope, (b) preempt Phase 6.2 work, and (c) leave the UAT bug live for the entire intervening period.

**This is a documented sequencing choice, not a regression.** When Phase 6.2 lands the schema columns, `_canonical_rating_keys_sync` can be upgraded in-place: replace `min(rating_key)` with `max(quality_score)`, where `quality_score` is derived from `(file_format, bitrate)`. The wire-in point (`_read_vibe_member_rating_keys_sync`) and the public contract (`List[str] -> List[str]`) stay identical, so no caller churn.

Until then, `min(plex_rating_key)` is a **better-than-nothing** heuristic that resolves the visible UAT bug and ships immediately.

## Files Changed

| File | Change | Lines |
|------|--------|-------|
| `app/services/vibe_service.py` | Added `_canonical_rating_keys_sync` helper immediately before `_read_vibe_member_rating_keys_sync`; piped the existing read through it (one-line wire-in) | +41 / −3 |
| `tests/test_vibe_service.py` | Extended `_add_track(...)` helper with optional `artist=` / `title=` kwargs (back-compat defaults); appended 4 regression tests at the file tail | +92 / −3 |

Untouched (verified via `git diff` grep for protected function names — 0 deletions):

- `slot_track`, `unslot_track` — write-side slot-in pipeline.
- `reslot_all_rated_tracks`, `maybe_reslot_pending_track` — retroactive pending paths.
- `_read_all_rated_track_keys_sync` — full-library reader (used by suggestions, not vibes).
- `app/models/track.py` — Track schema is byte-identical; no migration.

## Test Counts

| Test file | Before | After | Delta |
|-----------|--------|-------|-------|
| `tests/test_vibe_service.py` | 15 | 19 | +4 (canonical-key suite) |
| 9 other quality-gate files | 131 | 131 | 0 |
| **Total quality-gate suite** | 146 | 150 | +4 |

Full suite (`tests/test_vibe_service.py tests/test_vibe_clusterer.py tests/test_anthropic_client.py tests/test_api_setup.py tests/test_database_phase6.py tests/test_phase61_migration.py tests/test_finalize_integration.py tests/test_recluster_commit.py tests/test_event_handlers.py tests/test_setup_templates.py`): **150 passed**, no regressions.

## TDD Gate Compliance

- **RED gate** `67f1c20` — `test(260511-bpf): add failing canonical-key dedupe tests`. 4 tests fail with `ImportError: cannot import name '_canonical_rating_keys_sync'`. Existing 15 vibe-service tests still pass against the extended `_add_track` signature.
- **GREEN gate** `1371b53` — `fix(260511-bpf): dedupe vibe playlist members by (artist, title) canonical key`. 4 new tests pass; full 150-test quality-gate suite passes.
- **REFACTOR gate** — Skipped (no cleanup needed; helper is already minimal at 16 lines).

## Deviations from Plan

None. Plan executed exactly as written:

- 2 atomic commits (RED + GREEN), each with its single intended file scope.
- Helper placed immediately before `_read_vibe_member_rating_keys_sync` (line 253 site, now shifted by the insertion).
- `_add_track` signature extended additively — all 14 existing call sites continue to work unchanged with the default kwarg values.
- Scope-discipline static check passed (0 lines deleted from `slot_track`, `unslot_track`, `reslot_all_rated_tracks`, `maybe_reslot_pending_track`, `_read_all_rated_track_keys_sync`).

One tiny adjustment for Python 3.9 venv compat: type annotation `dict[tuple[str, str], str]` in the helper is **lazy-evaluated** because `app/services/vibe_service.py` has `from __future__ import annotations` at line 36 — confirmed safe before commit. Not a deviation, just noting the why.

## Known Stubs

None. Helper is fully wired; the deferral note on quality-aware dedupe is a **future enhancement**, not a stub — the current implementation is fully correct against the documented contract (collapse cross-album dupes, pick deterministic representative).

## Self-Check: PASSED

- File exists: `app/services/vibe_service.py` — FOUND.
- File exists: `tests/test_vibe_service.py` — FOUND.
- Symbol exists: `_canonical_rating_keys_sync` in `app/services/vibe_service.py` — FOUND (grep confirms helper definition).
- Commit exists: `67f1c20` (RED) — FOUND in `git log`.
- Commit exists: `1371b53` (GREEN) — FOUND in `git log`.
- All 150 quality-gate tests pass — VERIFIED via final `pytest` run.
- TrackVibe rows untouched — verified via plan's scope-discipline diff grep (0 deletions in protected functions).
