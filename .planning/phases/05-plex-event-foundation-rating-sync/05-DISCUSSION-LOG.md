# Phase 5: Plex Event Foundation + Rating Sync - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `05-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-05-08
**Phase:** 05-plex-event-foundation-rating-sync
**Areas discussed:** Anthropic SDK migration scope, Backfill strategy on deploy, Webhook URL display in wizard, Taste profile scope split

---

## Anthropic SDK migration scope

| Option | Description | Selected |
|--------|-------------|----------|
| Build new client alongside | New `anthropic_client.py` using SDK with prompt caching support; v1 `llm_client.py` and `chat_service.py` untouched until Phase 7 deletes both | ✓ |
| Migrate `llm_client.py` in-place | Replace direct-httpx with anthropic SDK in existing file; chat_service keeps working through it; mixed-version risk for ~2 weeks | |
| Delete v1 chat now (early retirement) | Pull chat retirement forward into Phase 5; aggressive cleanup but Phase 5 grows ~2 days; leaves no working LLM surface between Phase 5 and Phase 7 | |

**User's choice:** Build new client alongside.
**Notes:** Cleanest path. v1 chat keeps working unmodified through Phase 6. Phase 7 deletes `chat_service.py` + v1 `llm_client.py` atomically when chat UI retires. Bonus: `ollama_client.py` (~82 lines, no consumers since Anthropic took over) gets deleted in Phase 5 as dead-code cleanup.

---

## Backfill strategy on deploy

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-backfill on first startup | One-time background job pages through Plex on first boot, fills userRating/lastViewedAt/viewCount for every track; visible banner | ✓ |
| Trigger from settings 'Resync now' | Phase 5 ships with empty columns; user clicks Resync when ready; vibes home (Phase 6) might launch with empty rated set if user forgets | |
| Auto + manual button both | Both auto on first start AND keep manual button | |

**User's choice:** Auto-backfill on first startup.
**Notes:** Effectively equivalent to "both" because EVT-05 already mandates a manual "Resync now" button regardless. Auto handles initial deploy seamlessly; manual button stays for future drift recovery.

---

## Webhook URL display in wizard

| Option | Description | Selected |
|--------|-------------|----------|
| Detect candidates, you pick | Auto-detect 3 candidates (Docker hostname, NAS LAN IP from Host header, Tailscale 100.x); radio + per-row test webhook | ✓ |
| Show one default + editable text field | Default to `http://composer:8085/api/webhooks/plex` with editable host part | |
| Just show the path, you build the URL | Plain instructions, no auto-detect; lowest implementation cost | |

**User's choice:** Detect candidates, you pick.
**Notes:** User has Plex Pass confirmed. Wizard polls each candidate by fetching `/api/webhooks/plex/last-test` after Plex's test event arrives. User picks whichever turns ✓ first. Selected URL persists to ServiceConfig so settings page can show "Composer is configured for webhooks at: <url>" and allow swapping later.

---

## Taste profile scope split

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 5: structured only — Phase 7 adds LLM text | Phase 5 ships centroid + top artists/genres + stats; Phase 7 adds LLM summary text when ranking consumes it | |
| Phase 5: full taste profile incl LLM text | End-to-end in Phase 5 — verifies prompt caching, cost circuit breaker scaffolding, end-to-end anthropic_client | ✓ |
| Phase 5: schema only — compute deferred | Just adds DB columns; first computation in Phase 6 | |

**User's choice:** Phase 5: full taste profile incl LLM text.
**Notes:** Trade scope-creep for risk-reduction. The LLM scaffolding (prompt caching with explicit `ttl: "1h"`, cost circuit breaker counters, structured-output via `model_validate_json`) is *exercised end-to-end* in Phase 5 — by the time Phase 6/7 need it, it's known-working. Cost is tiny (one ~1k-token call per recompute, recomputes only on ≥10% delta in rated set). Recompute trigger: counter on the dispatcher tracking rated-set deltas.

---

## Claude's Discretion

- Internal naming for Pydantic event classes (`RatingChangedEvent` vs `PlexRatingChange`)
- Exact polling SQL query / filter syntax against PlexAPI
- Specific layout/styling of `/debug/events`
- Whether the "test webhook" indicator polls a dedicated `/api/webhooks/plex/last-test` endpoint or surfaces it in the page itself
- The 50ms response-time mechanism (push-to-queue and return; vs background task; vs response-then-process)
- Backfill banner UX (copy the sync banner pattern; small style decisions)
- Cost circuit breaker UI surface — Phase 5 just stores counters, no UI yet (UI lands in Phase 7)

## Deferred Ideas

- **Adaptive polling backoff** (reduce frequency when webhooks healthy) — revisit in Phase 8 polish or v2.1
- **Plex webhook event ordering** (FIFO global vs per-ratingKey) — single dispatcher = global FIFO by default; revisit if Phase 6 slot-in needs per-track ordering
- **Webhook payload export-as-JSON button** on `/debug/events` — defer; simple HTML preview ships first
- **Manual event replay button** on `/debug/events` — high diagnostic value but adds complexity; defer until a real need surfaces
- **Settings UI to change polling interval** — config-file-only in Phase 5; add widget when (or if) tuning is wanted
