# Phase 6 — Deferred Items

Issues discovered during execution but out of scope for the current plan.

## From 06-01 (Foundation)

### Pre-existing test failures (confirmed on main; unrelated to Phase 6 schema work)

The following 14 tests fail on the unmodified main branch and are unrelated to
Phase 6 Plan 01 changes. They are tracked here so future plans don't pick them
up as Phase-6-introduced regressions:

- `tests/test_analysis_service.py::TestRunAnalysis::test_skips_oversized_files`
- `tests/test_audio_analyzer.py::TestExtractFeatures::test_returns_all_feature_keys`
- `tests/test_audio_analyzer.py::TestExtractFeatures::test_normalizes_danceability`
- `tests/test_audio_analyzer.py::TestExtractFeatures::test_returns_correct_key_and_scale`
- `tests/test_chat_service.py::TestProcessMessage::test_ollama_not_configured`
- `tests/test_chat_service.py::TestProcessMessage::test_process_message_validates_track_ids`
- `tests/test_chat_service.py::TestProcessMessage::test_process_message_success`
- `tests/test_chat_service.py::TestProcessMessage::test_no_candidates_found`
- `tests/test_sync_api.py::TestStartSync::test_start_sync_launches_background_task`
- `tests/test_sync_scheduler.py::TestStartScheduler::test_triggers_auto_sync_when_no_prior_sync`
- `tests/test_sync_scheduler.py::TestStopScheduler::test_shuts_down_without_error`
- `tests/test_sync_scheduler.py::TestUpdateSyncSchedule::test_updates_running_scheduler`
- `tests/test_sync_service.py::TestRunSync::test_delta_sync_when_last_sync_exists`
- `tests/test_sync_service.py::TestRunSync::test_fallback_to_full_sync_when_delta_returns_zero`

#### Detail: `test_skips_oversized_files`

- **Discovered:** Phase 6 Plan 01, full-suite regression sweep
- **Verified pre-existing:** Yes — fails on the unmodified main branch as well
- **Symptom:** `sqlite3.InterfaceError: Error binding parameter 4 - probably unsupported type` —
  a `MagicMock` for `extract_features().__getitem__()` is being passed to the
  `track.musical_key` UPDATE binding. Test fixture mocks the audio analyzer but
  doesn't bind a string return for `musical_key` / `scale`.
- **Triage:** Test-fixture bug (or a real Phase 3 mock-shape mismatch surfaced
  by an SQLAlchemy 2.x stricter binding). Unrelated to Phase 6 schema work.
- **Suggested owner:** Whoever next touches `tests/test_analysis_service.py` —
  add `mock.extract_features.return_value.__getitem__.side_effect = lambda k: "C"`
  for the string columns, or move to a typed fixture.
