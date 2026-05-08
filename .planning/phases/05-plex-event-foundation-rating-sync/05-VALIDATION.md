---
phase: 5
slug: plex-event-foundation-rating-sync
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-08
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio (already configured in v1) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` — `asyncio_mode = "auto"`, `testpaths = ["tests"]` |
| **Quick run command** | `pytest tests/test_event_bus.py tests/test_api_webhooks.py -x` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | ~30 seconds for full suite (current v1 suite is ~20s; Phase 5 adds ~14 new test files mostly unit-level) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_<changed_module>.py -x` (target <5s)
- **After every plan wave:** Run `pytest tests/ -x` (full suite, target <30s)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds for full suite, 5 seconds for per-module

---

## Per-Task Verification Map

Initial table — task IDs (`{N}-PP-TT`) populated by the planner during PLAN.md generation; this table provides the requirement → behavior → command pairings the planner attaches to each task.

| Requirement | Behavior | Test Type | Automated Command | File Exists |
|-------------|----------|-----------|-------------------|-------------|
| EVT-01 | `POST /api/webhooks/plex` returns 200 in <50ms with valid multipart | unit + perf | `pytest tests/test_api_webhooks.py::test_returns_200_under_50ms -x` | ❌ W0 |
| EVT-02 | Same `dedupe_key` inserted twice → only one EventLog row | unit | `pytest tests/test_event_log.py::test_dedupe_unique_constraint -x` | ❌ W0 |
| EVT-03 | Polling APScheduler job emits typed events with `source='poll'` | unit | `pytest tests/test_poll_service.py::test_emits_rating_changed -x` | ❌ W0 |
| EVT-04 | Single dispatcher consumes queue serially (no concurrent SQLite writes) | unit | `pytest tests/test_event_bus.py::test_dispatcher_serializes -x` | ❌ W0 |
| EVT-05 | "Resync now" button triggers backfill_service.run | integration | `pytest tests/test_api_rating_sync.py::test_resync_button -x` | ❌ W0 |
| EVT-06 | All `_*_sync` helpers run via `asyncio.to_thread` (no blocking PlexAPI in async path) | static (grep+ast) | `pytest tests/test_event_handlers.py::test_no_blocking_plexapi_in_async -x` | ❌ W0 |
| EVT-07 | `webhook_url_detection` returns 3 plausible candidates given a request | unit | `pytest tests/test_webhook_url_detection.py -x` | ❌ W0 |
| RATE-01 | `userRating=7.0` displays "3.5 stars" AND DB stores 7.0 raw | unit | `pytest tests/test_rating_helpers.py::test_stars_from_user_rating -x` | ❌ W0 |
| RATE-02 | `backfill_service.run_backfill` fills `user_rating` for all tracks | integration | `pytest tests/test_backfill_service.py::test_full_run -x` | ❌ W0 |
| RATE-03 | `media.rate` webhook → `Track.user_rating` updated, `rating_changed_at` set, `RatingChanged` event emitted | integration | `pytest tests/test_event_handlers.py::test_handle_rating_changed -x` | ❌ W0 |
| RATE-04 | `WHERE user_rating > 0` query uses index (EXPLAIN QUERY PLAN) | unit | `pytest tests/test_track_model.py::test_user_rating_indexed -x` | ❌ W0 (extend) |
| RATE-05 | `taste_profile_service.recompute()` upserts TasteProfile with centroid + LLM summary text | integration (mocked anthropic) | `pytest tests/test_taste_profile_service.py::test_recompute -x` | ❌ W0 |
| OPS-01 | `_migrate_add_columns()` adds 4 new Track cols + creates EventLog/LLMUsage/TasteProfile tables | integration | `pytest tests/test_database.py::test_phase5_migration -x` | ❌ W0 (extend) |
| OPS-02 | New AnthropicClient sends `cache_control={"type":"ephemeral","ttl":"1h"}` on system message | unit (mock SDK) | `pytest tests/test_anthropic_client.py::test_explicit_ttl_1h -x` | ❌ W0 |
| OPS-03 | `pyarr>=6.6,<7.0` pinned in requirements.txt | static | `grep "pyarr>=6.6" requirements.txt` | n/a |
| OPS-04 | `scikit-learn>=1.8,<2.0` pinned in requirements.txt | static | `grep "scikit-learn>=1.8" requirements.txt` | n/a |
| DEBUG-01 | `/debug/events` returns 200 and renders last 50 EventLog rows | integration (TestClient) | `pytest tests/test_pages.py::test_debug_events_page -x` | ❌ W0 |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Wave 0 must land before any other tasks execute — these are the test scaffolds that all subsequent tasks verify against.

- [ ] `tests/test_event_bus.py` — covers EVT-01 partial, EVT-04
- [ ] `tests/test_api_webhooks.py` — covers EVT-01, EVT-02 (dedupe behavior end-to-end)
- [ ] `tests/test_event_log.py` — covers EVT-02 UNIQUE constraint
- [ ] `tests/test_poll_service.py` — covers EVT-03
- [ ] `tests/test_event_handlers.py` — covers RATE-03, EVT-06 (static analysis test for sync-call leakage)
- [ ] `tests/test_webhook_url_detection.py` — covers EVT-07 (3-candidate detection)
- [ ] `tests/test_rating_helpers.py` — covers RATE-01 (the off-by-two display test, 7.0 → "3.5 stars")
- [ ] `tests/test_backfill_service.py` — covers RATE-02
- [ ] `tests/test_taste_profile_service.py` — covers RATE-05 (mock anthropic SDK)
- [ ] `tests/test_anthropic_client.py` — covers OPS-02 (verify `cache_control={"ttl":"1h"}` kwarg passed to SDK)
- [ ] `tests/test_pages.py::test_debug_events_page` — covers DEBUG-01 (extend existing pages test file)
- [ ] `tests/test_api_rating_sync.py::test_resync_button` — covers EVT-05
- [ ] `tests/test_track_model.py::test_user_rating_indexed` — extend existing file
- [ ] `tests/test_database.py::test_phase5_migration` — extend existing file
- [ ] **Removal:** delete `tests/test_service_clients.py` lines 80-130 (the 3 `ollama_client` tests, per CONTEXT.md D-02 — must ship in same commit as `ollama_client.py` deletion to keep CI green)

Framework installation: nothing new — pytest + pytest-asyncio + httpx (TestClient) already present in v1.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end webhook from real Plex → Composer event log → Track update | EVT-01 + RATE-03 | Requires the user's actual Plex Pass server to send events; cannot mock Plex's webhook delivery in CI | After deploy: rate a track in Plexamp → check `/debug/events` shows the event within 5s → verify Track row in DB has updated `user_rating` and `rating_changed_at` |
| First-startup auto-backfill against ~10k-track library | RATE-02 | Requires the user's actual Plex library; CI only has small fixtures | First container restart after Phase 5 deploy: backfill banner appears → completes within ~5–15 min → settings page shows correct rated-track count (should match Plexamp count) |
| Webhook URL candidate auto-detection on the user's network | EVT-07 | Output depends on the user's actual NAS networking (synobridge container hostname, host IP, Tailscale presence) | Open setup wizard webhook step → verify all 3 candidates render → click "Test webhook" on each → confirm exactly one ✓ matches the URL the user pasted into Plex |
| `/debug/events` page is screenshot-readable | DEBUG-01 | Visual usability assessment | Open `/debug/events` after some events have flowed → confirm columns are visible at 1280px wide and at 375px portrait (mobile-first verification) → test "copy" button on dedupe_key field |
| Anthropic prompt caching actually hits cache on subsequent taste profile recomputes | OPS-02 + RATE-05 | Requires real Anthropic API and timing across multiple calls within 1h TTL | After 2nd recompute within 1h: check `LLMUsage.cache_read_input_tokens > 0` for the second row; if 0, system message is below 2048-token threshold and caching silently disabled |

---

## Validation Sign-Off

- [ ] All 17 phase requirements have `<automated>` test paths or Wave 0 stubs
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING (❌) test files
- [ ] No watch-mode flags (`pytest --watch` etc.) — single-run only
- [ ] Feedback latency < 30s full suite, < 5s per-module
- [ ] `nyquist_compliant: true` set in frontmatter once Wave 0 is verified by gsd-plan-checker

**Approval:** pending
