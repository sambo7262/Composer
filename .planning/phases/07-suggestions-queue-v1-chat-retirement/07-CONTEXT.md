# Phase 7: Suggestions Queue + v1 Chat Retirement - Context

**Gathered:** 2026-05-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 7 delivers a continuous `Composer · Suggestions` Plex playlist that drains as the user listens and refills with taste-aware picks within 30 seconds. Each suggestion carries a one-line "Why this track?" rationale. The v1 mood-chat UI retires, the v2 vibes home becomes the landing page, and mobile-first conventions land project-wide (bottom tab bar, `h-dvh`, `safe-area-inset-bottom`, ≥44px targets, no hover-only states). The LLM cost circuit breaker, prompt-cache observability, `/debug/suggestions` page, and `/debug` index ship as part of this phase — none of them defer.

Phase 7 does NOT touch:

- Vibe centroids / vibe membership (Phase 6.2 owns those; this phase consumes them)
- The auto-slot pipeline on new rating (`event_handlers.handle_rating_changed` → `vibe_service.slot_track`) — that's Phase 6 SC2, shipped
- `userRating` write-back to Plex (PROJECT.md key decision: Composer never writes ratings)
- Legacy v1-generated Plex playlists (no `Composer ·` prefix) — recognized as legacy, never touched
- Existing user-created Plex playlists (PROJECT.md key decision: hands-off)

</domain>

<decisions>
## Implementation Decisions

### Refill cadence + bootstrap

- **D-01: Bootstrap on wizard finalize (fresh setup).** Phase 6 wizard finalize triggers the first ranking call so a brand-new user lands on `/suggestions` with a populated queue. No cold-start moment.
- **D-02: Bootstrap on Phase 7 lifespan migration (existing setup).** Phase 7 first-deploy startup checks "vibes exist AND no `Composer · Suggestions` ManagedPlaylist row" → fires one ranking call to bootstrap the queue. Mirrors the Phase 6.1 first-deploy migration pattern (gated by a MigrationLog row for idempotency). Wizard finalize and lifespan migration BOTH call the same `bootstrap_suggestions_queue()` function — one bootstrap path, two callers.
- **D-03: Refill trigger = threshold-only.** No refill on every scrobble. Refill fires when queue size drops below the target (default 30 — configurable per SUGG-01). User reasoning: "if I'm listening it will refill; if I'm not it won't." Composes naturally with SUGG-11's per-event debounce.
- **D-04: Refill size = back to target (variable batch).** Queue always tops up to the target. Simplest invariant: `queue.size == target` after every refill. Variable batch size depending on how many were consumed since the last refill.

### Shortlist composition

- **D-05: Balanced across all vibes.** For 50-track shortlist and 6 vibes, ~8 candidates per vibe. Every vibe is represented; user dismisses what doesn't fit. Rejected: "weighted by listen activity" (needs listen-time-per-vibe tracking not yet built) and "focused on top 2-3 vibes" (sleepy vibes never get fed and stagnate).
- **D-06: Source pool = broad + distance pre-filter.** Eligible candidates = all unrated tracks with audio features computed, MINUS the 14-day `SuggestionHistory` exclusion window, MINUS hard-negative artists. THEN pre-filter to tracks within 2σ of at least one vibe centroid before LLM ranking. Big enough pool to avoid getting stuck on small library areas; quality bar filters obvious mismatches before they burn LLM tokens.
- **D-07: Shared longer LLM preamble for cache engagement.** Suggestions-ranking cached system prompt = vibe definitions (from 6.2's `vibe_definitions_preamble` call output) + taste profile summary + Composer context. Target >2048 tokens to clear the Sonnet 4.6 caching threshold that bit Phase 6.2 (`Anthropic caching not engaged` warnings). One cache namespace serves both vibe assignment and suggestions ranking. The per-call payload is just "~50 tracks, rank these."

### Queue UI + dismiss interaction

- **D-08: Compact list layout.** Vertical list, each row = 48px album art + title + artist + tiny vibe chip. Scrolls fast (~6 rows visible on iPhone portrait). Feels like a streaming queue. Rejected: cards (lower density) and hybrid next-up emphasis (more template complexity for marginal benefit at this scale).
- **D-09: Tap-to-expand inline rationale.** Default row state is compact (D-08). Tap a row → Alpine `x-show` toggle expands an inline panel below the row with "Why this track?" rationale + actions. Reuses the existing `alpine-morph` HTMX swap pattern. Keeps the list scannable; rationale is one tap away.
- **D-10: Dismiss inside the expanded view.** Dismiss button lives alongside "Play in Plexamp" in the expanded panel. Two-tap dismiss (expand → tap dismiss) = no accidents from mis-taps while scrolling. Reuses the tap-to-expand pattern instead of teaching the user a new gesture. Rejected: swipe-left (gesture infrastructure not built) and always-visible X (mis-tap risk on 44px touch targets).

### Skip-tracking calibration

- **D-11: Soft-negative window = 14 days.** Matches the SuggestionHistory dedup window from SUGG-07. A track played from Suggestions but not rated within 14 days is marked as soft negative.
- **D-12: Soft-negative effect = inject context into LLM prompt.** The per-call ranking prompt receives an addendum: "user heard but didn't rate these similar tracks: [list]; deprioritize." Uses the LLM's intelligence to disambiguate "didn't rate because didn't like" vs "forgot to rate." Aligns with PROJECT.md "tiered similarity: LLM for ranking, distance for slotting." No pre-LLM math penalty layered on top — the LLM is the deboost mechanism.
- **D-13: Hard-negative artist deboost = until user rates 3+ stars.** Track-level exclusion is forever (SUGG-09 requirement). Artist-level deboost stays in effect until the user gives that artist a positive signal (rates one of their tracks at 3+ stars). Natural recovery path; models "I dismissed once, but if I starred something I'm back open to them." Rejected: rolling 60-day window (signal evaporates without user action) and track-only no-artist-deboost (same artist's other tracks keep appearing).

### Claude's Discretion

- **Empty-state UX for `/suggestions` during initial bootstrap.** Show a "Building your queue…" loader with the existing LLM progress card pattern from hotfix 260512-k3n. Hide when first refill completes.
- **Vibe coverage indicator UX placement.** SUGG-10's "Find candidates" CTA — place on each vibe card on the vibes home (`/vibes` or `/`), threshold default 25 (per the requirement). LLM call only fires on explicit user tap of the CTA; never automatically.
- **`/debug` index page layout.** Simple plain-HTML list of links: `/debug/events`, `/debug/vibes`, `/debug/suggestions`, `/debug/discovery` (placeholder until Phase 8). Linked from settings page footer. No fancy navigation.
- **"Play in Plexamp" deeplink behavior.** Use `plexamp://` URI scheme. If Plexamp isn't installed, browser falls back to Plex Web. No detection logic — let the OS handle the protocol.
- **Prompt-cache TTL math.** SUGG-05's explicit `ttl: "1h"` is the requirement. Refresh cadence (threshold-only refill, D-03) means most refill bursts complete within a single 1h cache window. Cross-session: cold start re-creates the cache prefix; first call costs more, batches 2..N hit the cache.
- **Queue auto-scroll on scrobble.** When a track is consumed and removed, the queue subtly shifts (HTMX `outerHTML` swap with `alpine-morph` per UI-05). No explicit auto-scroll to top — user's scroll position is preserved.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope + requirements
- `.planning/ROADMAP.md` §"Phase 7: Suggestions Queue + v1 Chat Retirement" (lines 208–231) — phase goal, dependencies, 5 success criteria, key pitfalls baked in
- `.planning/REQUIREMENTS.md` SUGG-01..11, UI-01..06, OPS-05, DEBUG-03, DEBUG-05 (20 requirements total)
- `.planning/PROJECT.md` §"Key Decisions" — locked v2.0 decisions: Anthropic over Ollama, event-driven (no scheduled refreshes), hands-off existing Plex playlists, mobile-first, retire v1 mood-chat

### Phase 5/6 carry-forward (NOT re-decided here)
- `.planning/phases/05-plex-event-foundation-rating-sync/05-CONTEXT.md` — event bus + RatingChanged/TrackPlayed dispatcher; Phase 7 ranking refill is triggered by `TrackPlayed` events
- `.planning/phases/06.2-llm-direct-vibe-assignment/06.2-CONTEXT.md` §"Pass 1 — batching & assign" — Anthropic prompt cache pattern (D-02 cache, D-04 vibe_definitions_preamble) that Phase 7 ranking reuses
- `app/services/anthropic_client.py` — `call_with_structured_output` with `thinking="off"|"adaptive"` translation + 400-fallback retry (hotfix 260512-k3n). Pass 7 ranking calls use `thinking="off"` (Pass 1 pattern) since shortlist ranking is bulk classification, not boundary review
- `app/services/taste_profile_service.py` — existing cached-preamble pattern that's the closest analog for Phase 7 ranking; system_prompt structure to mimic

### Anti-patterns / pitfalls (cite at planning time)
- `.planning/notes/pitfalls.md` Pitfall 8 — Scrobble ≠ endorsement; skip-bomb detector (>10 tracks consumed in <10min disables drain)
- `.planning/notes/pitfalls.md` Pitfall 9 — Anthropic prompt cache `ttl: "1h"` explicit (silently-changed default is 5min)
- `.planning/notes/pitfalls.md` Pitfall 10 — Pydantic validation on every LLM-ranked track ID before insertion (v1-burned)
- `.planning/notes/pitfalls.md` Pitfall 11 — LLM cost circuit breaker ships in FIRST commit, not last
- `.planning/notes/pitfalls.md` Pitfall 12 — Cross-surface dedup via SuggestionHistory 14-day window
- `.planning/notes/pitfalls.md` Pitfalls 15–18 — Alpine-morph + iOS dvh + 44px targets + tap-not-hover (mobile-first conventions)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`app/services/anthropic_client.py::call_with_structured_output`** — single entry point for all Anthropic calls. Already handles `thinking="off"|"adaptive"` translation, 400-fallback retry on extended-thinking unsupported, JSON-from-prose tolerance (260510-t5n). Phase 7 ranking adds a new `purpose="suggestions_rank"` here. NO new SDK client.
- **`app/services/taste_profile_service.py`** — closest analog for Phase 7's ranking call shape. Generates cached preamble (~200-word summary of user taste) using the same prompt-cache pattern (system message with explicit `cache_control` + `ttl: "1h"`). Phase 7's ranking system prompt extends this pattern by appending vibe definitions and ~600 more tokens of Composer context.
- **`app/services/event_handlers.py::handle_rating_changed`** — pattern for typed-event handlers consuming the event bus. Phase 7 adds `handle_track_played(event: TrackPlayedEvent)` alongside it. The bus dispatch loop (Phase 5 D-06) already supports new event types via the Literal discriminator.
- **`app/services/vibe_service.py::slot_track`** — pattern for "consume an event, update SQLite, push to Plex" hot path with module-level lock + LLMUsage logging. Phase 7's `refill_suggestions_queue()` mirrors this shape but for the queue mirror instead of TrackVibe.
- **`app/templates/partials/llm_progress_card.html`** (hotfix 260512-k3n) — Alpine.js polling card for "calling LLM…" UX. Phase 7 reuses this for initial bootstrap and refill progress; the `/api/vibes/last-llm-call/progress` endpoint already covers `purpose=suggestions_*` queries since it's purpose-agnostic.
- **`app/models/vibe.py::ManagedPlaylist`** — Phase 7 adds a `Composer · Suggestions` ManagedPlaylist row using the same pattern. The "Composer ·" namespace prefix is mandatory (Phase 6 D-25).
- **`app/routers/pages.py::read_debug_vibes`** — pattern for `/debug/*` plain-HTML diagnostic pages with `SlotInLog`-style "last N events" tables. Phase 7's `/debug/suggestions` follows this shape with `last 20 refill triggers` instead of slot-ins.

### Established Patterns

- **PlexAPI sync calls wrapped in `asyncio.to_thread`** (Phase 5 D-09). Any `update_playlist_items` call from `async def` MUST be wrapped. Static AST test enforces this for `event_handlers.py`; Phase 7 must extend the same test coverage to `suggestions_service.py` (new module).
- **Module-level singleton + `get_state()`** pattern for services (Phase 5 D-08). Phase 7's `suggestions_service` follows the same shape. `_state` + `get_state()` + autouse-reset fixture in tests.
- **EventLog dedupe via SHA256** (Phase 5 D-07). TrackPlayed events get dedupe key `sha256(event_type|ratingKey|5s_bucket)`. Phase 7's refill scheduler reads from EventLog; dedupe is already enforced upstream.
- **LLMUsage logging on every Anthropic call** (Phase 5 D-04). Phase 7's `purpose="suggestions_rank"` and `purpose="suggestions_bootstrap"` flow through the same path. `/debug/suggestions` aggregates by purpose for the cost panel.
- **HTMX swap + `alpine-morph`** (Phase 6 UI-05). Phase 7's queue rendering uses `outerHTML` swaps with morph extension to preserve Alpine state on partial replacement.
- **Tight per-handler `except` clauses** (Phase 5 / 6 / 6.2 conventions). No `except Exception` in route handlers. Phase 7 follows the same enforcement.

### Integration Points

- **`Composer · Suggestions` Plex playlist created in bootstrap path.** Use `plex_playlist_service.create_playlist(name="Composer · Suggestions", ...)`. Register `ManagedPlaylist` row with `kind="suggestions"` (new kind alongside `kind="vibe"`).
- **TrackPlayed event source.** Plex webhook `media.scrobble` (and `lastViewedAt` polling fallback per Phase 5) publishes `TrackPlayedEvent` on the event bus. `handle_track_played` filters to events where the track is currently in the Suggestions mirror, removes locally, schedules refill via the threshold check.
- **Wizard finalize hook.** `api_setup.py::finalize` (Phase 6) calls `bootstrap_suggestions_queue()` after the vibe playlists are pushed but before redirecting to `/suggestions`.
- **Lifespan migration.** `app/main.py` lifespan reads `MigrationLog` and runs `bootstrap_suggestions_queue()` once if no Suggestions ManagedPlaylist row exists. Same gate pattern as `run_phase_61_migration`.
- **Cost meter on settings page.** `app/routers/pages.py::read_settings` queries `LLMUsage` GROUP BY DATE(called_at) for today's totals and aggregates `cache_read_input_tokens` to show cache hit %. New section on settings template alongside the existing service-config cards.

</code_context>

<specifics>
## Specific Ideas

- **Mirror Phase 6.1's `run_phase_61_migration` pattern for bootstrap.** Use a `MigrationLog` row with key like `phase_07_suggestions_bootstrap` to ensure idempotency. Don't reinvent the gate.
- **System-prompt-caching fix is part of Phase 7, NOT deferred.** The "caching not engaged" warning seen during Phase 6.2 (system prompt <2048 tokens) becomes load-bearing for Phase 7 SC4 ("`cache_read_input_tokens` accumulating across calls"). Plan accordingly: the shared longer preamble (D-07) is the fix.
- **Reuse the LLM progress card pattern from hotfix 260512-k3n.** During initial bootstrap and during refill calls, the existing `/api/vibes/last-llm-call/progress` endpoint can be reused since it's purpose-agnostic (matches `LLMUsage.purpose LIKE 'vibe_%' OR 'suggestions_%'`).

</specifics>

<deferred>
## Deferred Ideas

- **Listen-time-per-vibe tracking for shortlist weighting.** D-05 locked balanced shortlist; "weighted by listen activity" was rejected because the tracking infra doesn't exist yet. If Phase 9 (Feed the Engine) ships, that's the right place to add it.
- **Skip-bomb detector tuning.** Pitfall 8 mentions ">10 tracks consumed in <10min disables Suggestions drain." Default threshold not yet calibrated against real usage. Plan to ship default + make configurable; revisit numbers post-UAT.
- **Vibe coverage indicator threshold N tunable from settings.** SUGG-10 default is 25. UI to change it lives in Phase 7 settings page if simple enough; otherwise defer to Phase 8 polish.
- **Cross-session prompt cache.** Locked at `ttl: "1h"` per SUGG-05. If usage patterns reveal sessions often span >1h with the same preamble, consider explicit cache priming on app open in a future phase. Not Phase 7 scope.
- **v1 chat data archival policy.** UI-06 archives templates and removes nav; the `composer.chats` and `composer.playlists` tables stay in DB untouched (no migration). If the user wants a future export tool, that's a separate quick task.

</deferred>

---

*Phase: 07-suggestions-queue-v1-chat-retirement*
*Context gathered: 2026-05-13*
