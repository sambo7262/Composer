# Phase 07.1 — Deferred Items

Pre-existing test failures discovered during Plan 01 execution. Confirmed
pre-existing by git stash + re-run on the base commit (a2db7a4). NONE are
caused by Plan 01 changes. Logged here for triage in a follow-up quick
task; out of scope for Plan 01 per executor `<scope_boundary>` (only
auto-fix issues DIRECTLY caused by the current task's changes).

## Pre-existing failures (17 total) on base commit a2db7a4

### Sync (6 failures)

- `tests/test_sync_service.py::TestRunSync::test_delta_sync_when_last_sync_exists`
- `tests/test_sync_service.py::TestRunSync::test_fallback_to_full_sync_when_delta_returns_zero`
- `tests/test_sync_api.py::TestStartSync::test_start_sync_launches_background_task`
- `tests/test_sync_scheduler.py::TestStartScheduler::test_triggers_auto_sync_when_no_prior_sync`
- `tests/test_sync_scheduler.py::TestStopScheduler::test_shuts_down_without_error`
- `tests/test_sync_scheduler.py::TestUpdateSyncSchedule::test_updates_running_scheduler`

Root cause: `synced_tracks` assertion fails (0 vs expected); `Post-sync
analysis trigger failed: no such table: track` warning suggests an
init_db / fixture ordering issue with the analysis service hook. Several
of these are also marked `@pytest.mark.asyncio` on sync functions (pytest
warning). Pre-existing on main.

### Audio analyzer (3 failures)

- `tests/test_audio_analyzer.py::TestExtractFeatures::test_returns_all_feature_keys`
- `tests/test_audio_analyzer.py::TestExtractFeatures::test_normalizes_danceability`
- `tests/test_audio_analyzer.py::TestExtractFeatures::test_returns_correct_key_and_scale`

Root cause: `not enough values to unpack (expected 2, got 0)` — Essentia
mock missing return values. Pre-existing on main.

### Chat service (4 failures)

- `tests/test_chat_service.py::TestProcessMessage::test_ollama_not_configured`
- `tests/test_chat_service.py::TestProcessMessage::test_process_message_validates_track_ids`
- `tests/test_chat_service.py::TestProcessMessage::test_process_message_success`
- `tests/test_chat_service.py::TestProcessMessage::test_no_candidates_found`

Phase 7 retired the chat UI; these tests likely became stale. Pre-existing
on main; orthogonal to Phase 7.1.

### Analysis service (1 failure)

- `tests/test_analysis_service.py::TestRunAnalysis::test_skips_oversized_files`

Pre-existing on main.

## Plan 01 final test status

Phase 7.1 Plan 01 directly-modified tests:

- `tests/test_suggestions_discovery.py` — 9/9 passed (NEW file)
- `tests/test_anthropic_client.py` — 23/23 passed (4 new + 1 migrated)
- `tests/test_suggestions_service.py` — 23/23 passed (11 new TestRefillMirrorSql + 3 migrated TestMaybeScheduleRefill + 9 preserved)
- `tests/test_suggestions_service_v2.py` — 8/8 passed (trimmed per D-D1)
- `tests/test_api_vibes_phase7.py` — 8/8 passed (TestRefillSuggestionsForVibeContract rewritten for the new shim)
- `tests/test_event_handlers.py` — 14/14 passed (with the 2 new Task 3 tests pending)

621 passed / 17 pre-existing failures (unchanged since base).
