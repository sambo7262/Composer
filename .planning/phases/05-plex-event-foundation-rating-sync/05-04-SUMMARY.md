---
phase: 05-plex-event-foundation-rating-sync
plan: 04
subsystem: ui-surface
tags: [phase5, evt-07, debug-01, sc-5, d-02, d-12, d-14, d-21, d-22, htmx, ollama-deletion]
status: complete
requires:
  - 05-01 (event_bus, webhook_test_state, EventLog model, last-test stub, /api/webhooks/plex)
  - 05-02 (poll_service.get_poll_status, sync_scheduler.get_scheduler with plex_polling job)
  - 05-03 (taste_profile_service — referenced for future debug-page extensions)
provides:
  - "GET /api/library/stats — library-at-a-glance partial endpoint (Concern 1 / SC-5)"
  - "GET /debug/events — DEBUG-01 page (last 50 EventLog rows + queue depth + poll info + last test + webhook URL)"
  - "POST /api/webhooks/plex/test-arm — wizard test-arming endpoint"
  - "GET /api/settings/webhook/section + POST /api/settings/webhook/save — wizard radio form + persist (D-14)"
  - "app/services/webhook_url_detection.py — 3-candidate detector (Docker hostname, LAN IP, Tailscale 100.x)"
  - "ServiceConfig with service_name='webhook' — D-14 persistence target"
affects:
  - "tests/test_service_clients.py (TestOllamaClient deleted, 3 tests)"
  - "app/services/ollama_client.py (DELETED — D-02)"
  - "app/templates/pages/settings.html (extended with library-at-a-glance card, webhook section, Resync now, diagnostics footer)"
  - "app/routers/api_webhooks.py (test-arm endpoint added; /plex/last-test now uses partial template)"
  - "app/routers/api_settings.py (webhook section + save endpoints added)"
  - "app/routers/api_library.py (/stats endpoint + EventLog import)"
  - "app/routers/pages.py (/debug/events route)"
tech-stack:
  added:
    - "psutil (already in requirements; first use in app code)"
  patterns:
    - "ipaddress.IPv4Network('100.64.0.0/10') containment check for Tailscale CGNAT discovery"
    - "module-level _last_test_received_at + _last_test_payload accessed across routers"
    - "HTMX hx-trigger='load, every 10s' for polled stat partials"
    - "Pitfall 6: arm_test() + 60s TTL gate for indistinguishable Plex test events"
key-files:
  created:
    - app/services/webhook_url_detection.py
    - app/templates/pages/debug_events.html
    - app/templates/partials/webhook_url_radio.html
    - app/templates/partials/webhook_test_indicator.html
    - app/templates/partials/library_stats.html
    - tests/test_webhook_url_detection.py
    - tests/test_pages.py
  modified:
    - app/routers/pages.py
    - app/routers/api_webhooks.py
    - app/routers/api_settings.py
    - app/routers/api_library.py
    - app/templates/pages/settings.html
    - tests/test_library_api.py
    - tests/test_service_clients.py
  deleted:
    - app/services/ollama_client.py
decisions:
  - "Webhook URL ServiceConfig stores empty credential string (no token to encrypt; D-14)"
  - "Empty-state branch in debug_events.html uses {% if events %}/{% else %} (not {% for%}/{% else %}) to give the test a stable 'No events yet.' string anchor"
  - "library_stats partial uses hx-trigger='load, every 10s' so the rated count refreshes within 10s of a media.rate webhook arriving (ROADMAP SC-5)"
  - "webhook_save defensively prefixes 'http://' if scheme missing (display-only field; T-05-22 accept disposition)"
metrics:
  duration_seconds: TBD
  completed: TBD
  tasks_completed: 2 (autonomous) + 1 (checkpoint pending)
  files_created: 7
  files_modified: 7
  files_deleted: 1
  tests_added: 21 (14 webhook_url_detection + 4 pages + 3 stats endpoint)
  tests_green_relevant: 60/60
  full_suite_pass_rate: 241/255 (14 pre-existing failures unrelated to Plan 05-04)
checkpoint:
  status: complete
  task: "Task 3 — visual verification on real Plex deployment"
  url_visited: "http://192.168.86.37:8085/settings and /debug/events"
  outcome: "User validated webhook flow + SC-5 real-time rated count on NAS (2026-05-09). Two follow-up issues surfaced (PlexAPI 4.18 polling filter incompat + section.totalSize music-library quirk inverting backfill display), fixed in 3e56b3a — polling now opt-in, backfill denominator uses local DB count."
  follow_up_commits:
    - "dca617c (lidarr_client: pyarr 6.x rename compat)"
    - "3e56b3a (polling opt-in + backfill denominator fix)"
---

# Phase 5 Plan 04: Plex Event Foundation -- UI Surface (EVT-07 + DEBUG-01 + SC-5 + D-02)

> **Status:** Complete. Implementation landed 2026-05-08; user validated against real Plex/Plexamp on 2026-05-09. Two surface issues uncovered during testing were fixed in follow-up commits.

One-liner: Surface the Phase 5 backend to the user via /debug/events diagnostic page, /settings webhook configuration wizard with 3-candidate URL detection, and a Library-at-a-glance widget polling /api/library/stats every 10s — plus atomic Ollama deletion (D-02 / Pitfall 9).

## What was built

Four deliverables, all autonomous-task complete:

1. **Webhook URL auto-detection (EVT-07 / D-12)** — `app/services/webhook_url_detection.py` returns up to 3 candidates: Docker hostname (`socket.gethostname()`), LAN IP (parsed from request `Host` header), Tailscale 100.64.0.0/10 (via `psutil.net_if_addrs()`). Wizard radio form on /settings shows each candidate with a "Test webhook" button + per-candidate ✓/⏳ indicator that polls `/api/webhooks/plex/last-test?since=<arm_ts>` every 2s. Selected URL persists to `ServiceConfig` with `service_name='webhook'` (D-14).

2. **/debug/events diagnostic page (DEBUG-01 / D-21)** — Plain HTML, screenshot-readable. Renders last 50 `EventLog` rows + queue depth (from `event_bus.get_event_bus().qsize()`) + poll info (next_run from APScheduler, last_completed from `poll_service.get_poll_status()`) + last test event timestamp + payload preview (truncated to 200 chars per T-05-21) + configured webhook URL. Header explicitly warns Tailscale-only access assumption.

3. **Library-at-a-glance widget (Concern 1 / ROADMAP Phase 5 SC-5)** — `GET /api/library/stats` returns `{total_tracks, rated_tracks, last_rating_event_at}` as the `partials/library_stats.html` partial. Embedded on BOTH `/settings` (above service cards) AND `/debug/events` (header). Both surfaces poll the same endpoint every 10s via HTMX, so the rated count visibly increments within 10s of a `media.rate` webhook arriving — directly satisfying ROADMAP SC-5.

4. **Atomic Ollama deletion (D-02 / Pitfall 9)** — `app/services/ollama_client.py` deleted in same commit as the `TestOllamaClient` class (3 test methods) from `tests/test_service_clients.py`. Surviving Plex (4 tests) and Lidarr (4 tests) classes remain green. No remaining consumers in `app/` (verified via grep).

## Files

### Created (7)

- `app/services/webhook_url_detection.py` (91 lines) — 4 functions: `docker_hostname_candidate`, `lan_ip_candidate`, `tailscale_candidate`, `get_webhook_url_candidates`
- `app/templates/pages/debug_events.html` (75 lines) — DEBUG-01 page with library-stats header
- `app/templates/partials/webhook_url_radio.html` (45 lines) — 3-radio form with per-candidate Test buttons
- `app/templates/partials/webhook_test_indicator.html` (15 lines) — HTMX-polled ✓/⏳ pill
- `app/templates/partials/library_stats.html` (24 lines) — Total / Rated / Last rating event card
- `tests/test_webhook_url_detection.py` (147 lines, 14 tests)
- `tests/test_pages.py` (118 lines, 4 tests)

### Modified (7)

- `app/routers/pages.py` — `/debug/events` route + new imports for EventLog, event_bus, poll_service, sync_scheduler, api_webhooks
- `app/routers/api_webhooks.py` — `POST /plex/test-arm` endpoint added; `GET /plex/last-test` now returns `partials/webhook_test_indicator.html` (was inline `HTMLResponse`)
- `app/routers/api_settings.py` — `GET /api/settings/webhook/section` + `POST /api/settings/webhook/save` endpoints; import of `get_webhook_url_candidates`
- `app/routers/api_library.py` — `GET /api/library/stats` endpoint + import of `EventLog`
- `app/templates/pages/settings.html` — library-at-a-glance card (above service cards), Plex Webhooks section, Rating Sync section, "View diagnostics →" footer link
- `tests/test_library_api.py` — `TestStatsEndpoint` class with 3 tests
- `tests/test_service_clients.py` — `TestOllamaClient` class removed (3 tests deleted)

### Deleted (1)

- `app/services/ollama_client.py` (83 lines, atomic with TestOllamaClient deletion)

## Tests added (21)

- `tests/test_webhook_url_detection.py` — 14 unit tests covering all 4 functions including: hostname loopback skip, OSError fallback, host header port stripping, missing host header, psutil unavailable, IPv6 family skip, all-3-present aggregator, all-None aggregator
- `tests/test_pages.py::TestDebugEventsPage` — 2 integration tests (with-events + empty-state, exact "No events yet" copy assertion, bg-error/5 highlight on handler_error rows)
- `tests/test_pages.py::TestLibraryAtAGlanceRender` — 2 integration tests (settings + debug both reference `/api/library/stats`)
- `tests/test_library_api.py::TestStatsEndpoint` — 3 integration tests (empty library shows em-dash, mixed-rating count, most-recent rating_changed event filter)

All 21 new tests + 39 adjacent (apply: webhook_url_detection, pages, service_clients, api_webhooks, library_api, settings_api) pass green = **60/60 plan-relevant**.

## Test results

- Plan 04 relevant: **60 passed / 0 failed** (pytest tests/test_webhook_url_detection.py tests/test_pages.py tests/test_service_clients.py tests/test_api_webhooks.py tests/test_library_api.py tests/test_settings_api.py)
- Phase 5 cumulative (Plans 01-03 + new): **46 passed / 0 failed** (pytest tests/test_event_bus.py tests/test_event_handlers.py tests/test_event_log.py tests/test_poll_service.py tests/test_taste_profile_service.py tests/test_backfill_service.py tests/test_api_rating_sync.py tests/test_rating_helpers.py)
- Full suite: **241 passed / 14 failed** — the 14 failures are pre-existing baseline failures unrelated to Plan 04 (analysis_service mock issue, audio_analyzer Essentia stub issues, chat_service ollama-removed test references, sync_service async loop issues). Verified: failure list is identical to pre-plan baseline minus the 2 TestOllamaClient tests we intentionally deleted.

## Commits (6)

| # | Hash | Message |
| - | ---- | ------- |
| 1 | 304ab8a | test(05-04): add failing tests for webhook URL detection, /debug/events, and library stats |
| 2 | 122d8a3 | chore(05-04): atomic deletion of ollama_client.py and TestOllamaClient (D-02) |
| 3 | ab6734a | feat(05-04): webhook URL auto-detection service (EVT-07 / D-12) |
| 4 | 711972f | feat(05-04): add /api/library/stats endpoint + library_stats partial (SC-5) |
| 5 | a578f84 | feat(05-04): wizard webhook config (EVT-07) -- radio form + test-arm + indicator |
| 6 | 1a8c268 | feat(05-04): /debug/events diagnostic page + settings.html Phase 5 extensions |

## Visual checkpoint — pending

**Task 3 has NOT been executed.** Per Plan 04 frontmatter `autonomous: false`, the final task requires user verification on the real Plex deployment. The 11 verification steps from PLAN.md Task 3 are surfaced to the orchestrator below.

### What the user should verify

1. `docker compose up --build` — container starts cleanly; logs show "Phase 5 migration" or "Scheduled Plex polling".
2. Visit `http://<your-host>:8085/settings`. Confirm:
   - Plex / Anthropic / Lidarr existing service cards still render correctly (no v1 regressions).
   - **NEW** "Plex Webhooks" section shows up to 3 radio candidates (LAN IP should match how you reached the page).
   - Each candidate has a "Test webhook" button + empty indicator placeholder.
   - **NEW** "Rating Sync" section shows the Resync now button (idle state).
   - **NEW** Footer has "View diagnostics →" link.
3. Click "Test webhook" on the LAN IP candidate — indicator changes to "⏳ Waiting for test event…".
4. In Plex Web → Account → Webhooks: paste the LAN IP URL, click "Send test".
5. Within 2s the indicator turns green: "✓ Webhook test received at <timestamp>".
6. If a candidate doesn't work within 60s, try the next one.
7. Once one works, select its radio button and click "Save selected URL". Form swaps to saved state.
8. Visit `http://<your-host>:8085/debug/events`. Confirm:
   - Page renders cleanly (no template errors).
   - Configured Webhook URL shows the URL just saved.
   - Queue depth is 0 (or small number — drains fast).
   - Poll interval is 5m + a next_run time.
   - Last test shows the timestamp of the recent test webhook.
   - Last 50 events table shows the WebhookTest event + any media.play / scrobble / rate from your Plex listening.
9. Rate a track in Plexamp. Within 5s, refresh /debug/events — confirm a new `rating_changed` row with `processed_at` populated, no `handler_error`, correct dedupe_key prefix and ratingKey.
9a. **Concern 1 / ROADMAP SC-5 — Library-at-a-glance verification:**
   - On /settings: "Library at a glance" card appears ABOVE service cards. Three values: Total tracks, Rated tracks, Last rating event.
   - On /debug/events: same three values appear at the TOP of the page.
   - **Real-time test:** Open /settings, note current "Rated tracks" number. In Plexamp, rate a previously-unrated track. Within ~10s the "Rated tracks" number on /settings should bump by 1 WITHOUT a page reload. Repeat on /debug/events.
10. Mobile portrait check (375px wide via DevTools): /debug/events table doesn't horizontally scroll past viewport.

### Resume signal

User: type "approved" if all 11 steps pass. If a step fails, describe which one and what you saw.

## Carry-forward TODOs for Phase 6

- **Setup wizard takes over webhook configuration entry point.** Phase 5's settings-page surface remains as the post-wizard tuning surface; the wizard will likely embed `webhook_url_radio.html` directly in its flow.
- **webhook_test_state TTL configurability.** 60s may prove too short on slow networks. Phase 6 may expose this as `webhook_test_arm_ttl_seconds` in ServiceConfig extra_config.
- **Pre-existing failing tests** (14 unrelated to Plan 04): the chat_service tests reference a removed Ollama path; analysis_service mocks have an Essentia type mismatch; sync_scheduler / sync_service async-loop tests need refactoring. None affect Phase 5 functionality. Track in Phase 6 cleanup if scope allows.

## Threat Flags

None. The threat surface introduced by Plan 04 is fully covered by the existing `<threat_model>` register (T-05-21 through T-05-26 in PLAN.md). Specifically:

- T-05-21 (info disclosure on /debug/events): mitigated via header warning + 200-char payload truncation
- T-05-22 (user-submitted webhook_url): accepted (single-user app; URL is display-only, no outbound request)
- T-05-23 (Plex token leak in raw_payload): defensive — Plex doesn't include token in webhook payload; truncation provides additional defense in depth
- T-05-26 (out-of-order ollama_client.py + tests deletion): mitigated via atomic commit (122d8a3)

## Self-Check

To be appended after visual checkpoint approval.
