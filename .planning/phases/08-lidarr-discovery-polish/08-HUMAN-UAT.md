---
status: partial
phase: 08-lidarr-discovery-polish
source: [08-VERIFICATION.md]
started: 2026-05-17
updated: 2026-05-17
---

## Current Test

[awaiting human testing of items not pre-approved]

## Tests

### 1. Mobile portrait `/discover` round-trip
expected: vibe-grouped vertical-scroll sections with `Vibe.color` left-border accents, tap-to-expand artist card surfaces factual hook + LLM rationale + top tracks (lazy-loaded from ListenBrainz), `[Add to Lidarr]` swaps to status row, `[Dismiss]` removes the card immediately
result: pre-approved (Plan 04 UAT 2026-05-17 PT)

### 2. Mobile portrait `/library` rewrite
expected: card list at sub-md viewports, sticky search bar, filter chips (All / Analyzed / Unanalyzed / Rated), bottom-sheet sort modal respects `safe-area-inset-bottom`, all tap targets ≥44px, no horizontal page scroll at 375px
result: pre-approved (Plan 04 UAT 2026-05-17 PT)

### 3. Home-page cost chip
expected: chip shows `$X.XXXX` with 4-decimal precision under $0.01, "next refresh in Nd" once first tick has fired, "first refresh {LA datetime}" pre-tick, tap navigates to `/debug/suggestions`
result: pre-approved (UAT iter push `98a6f0b`)

### 4. CR-01 weekly rotation (multi-tick UAT)
expected: after a second Sunday cron tick (or two manual ticks ≥1 week apart simulated via SQL), `/discover` displays only the most recent week's `DiscoveryCandidate` rows; older rows remain in the DB but are filtered out
result: pending (needs ≥2 cron tick cycles to validate end-to-end)

### 5. Manual-tick button round-trip
expected: clicking `[Run weekly tick now]` on `/debug/discovery` flips state to `running`, the 5s poll shows `running → idle/error`, candidates land on `/discover`, cost chip transitions to post-first-tick state with `next refresh in 7d`
result: pending (needs real Lidarr + MB + Anthropic to round-trip on NAS)

## Summary

total: 5
passed: 3
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps

(none — items 4 and 5 are time/topology-bound, not gaps)
