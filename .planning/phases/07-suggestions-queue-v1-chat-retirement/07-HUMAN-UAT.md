---
status: complete
phase: 07-suggestions-queue-v1-chat-retirement
source: [07-VERIFICATION.md]
started: 2026-05-14T14:50:00Z
updated: 2026-05-29
---

## Current Test

[all items confirmed via NAS UAT 2026-05-29]

## Tests

### 1. SC1 — drain-and-refill within 30 seconds (end-to-end)
expected: User plays a track from `Composer · Suggestions` in Plexamp on the NAS. Within 30 seconds: (a) played track disappears from the SuggestionsMirror queue (visible at /suggestions and /debug/suggestions), (b) a new track is appended (with non-empty `rationale`, `vibe_id`, `score`), (c) the Plex playlist `Composer · Suggestions` reflects the new top-up. The first-ever refill on a fresh deploy must materialize the Plex playlist (CR-01 fix path — sentinel `plex_rating_key=''` is replaced with the real ratingKey).
result: passed (NAS UAT 2026-05-29 — confirmed working; note: per-event LLM refill superseded by 07.1 SQL refill)

### 2. SC4 — Anthropic prompt caching engaged across two refills within 1h
expected: Trigger two refills within an hour (drain a track, wait the 30s debounce, drain another). On /settings the LLM cost meter card shows `Cache hit: P%` where P > 0 (i.e. `cache_read_input_tokens` > 0 on the second call). `cache_creation_input_tokens` > 0 on the first call. `/debug/suggestions` shows the two LLMUsage rows with the cache columns populated. DO NOT see a runtime warning ('suggestions ranking cache creation not engaged') in NAS logs.
result: passed (NAS UAT 2026-05-29 — confirmed working; relevance superseded by 07.1 weekly-only LLM path)

### 3. CR-03 — find-candidates progress card visibly streams during a real refill
expected: On the /vibes page, tap a 'Find candidates' CTA on a vibe with <25 tracks (mobile Safari portrait at 375px). The button is replaced with an llm_progress_card that shows 'Calling Anthropic… (ranking candidates)' and an elapsed-seconds counter that ticks during the LLM round-trip. When the call completes (or breaker trips) the card resolves. The endpoint must return in <100ms (fire-and-forget), with the LLM heavy work happening in the background task.
result: passed (NAS UAT 2026-05-29 — confirmed working)

## Summary

total: 3
passed: 3
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

(none)
