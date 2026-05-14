# Phase 7 Follow-up: Suggestions Cost Architecture (Option C)

**Captured:** 2026-05-14
**Trigger:** NAS UAT at 16:51 UTC after CDL hotfix (commit `8594589`) revealed
that the bootstrap deadlock fix is correct, but the underlying refill model
makes every play of a mirror-member track cost ~$0.05 in LLM fees. Current
daily cost breaker ($0.42) trips after ~8 plays. Cost-effective for a
hobbyist personal-music app: NO.

## Problem (current state)

`maybe_schedule_refill` (`app/services/suggestions_service.py:349-377`) fires
`refill_suggestions_queue` (which makes a `purpose=suggestions_rank` Anthropic
call) any time `deficit > 0`. Because each play of a mirror-member track
drains the mirror by 1 → `deficit = 1` → refill fires → 1 LLM call per play.

This contradicts Phase 7 design intent in `.planning/phases/07-suggestions-queue-v1-chat-retirement/07-CONTEXT.md`:

- **D-03:** "Refill trigger = threshold-only. **No refill on every scrobble.**
  Composes naturally with SUGG-11's per-event debounce."
- **D-04:** "Refill size = back to target (variable batch). **Variable batch
  size depending on how many were consumed since the last refill.**"

The "per-event debounce" referenced in D-03 was never implemented — only
appears as a string inside an LLM prompt template (`suggestions_service.py:1029`),
not as actual cooldown logic.

## Decision: Option C — SQL hot path + weekly LLM discovery

The refill *hot path* does NOT need the LLM. Phase 6 already pre-computed
`TrackVibe.distance` (z-score Euclidean to vibe centroid) for every track in
the library. Picking 30 fresh tracks for the current vibe is a SQL query, not
a recommendation problem.

```
SELECT track FROM TrackVibe
WHERE vibe_id = ? AND view_count < ?
ORDER BY distance LIMIT 30
```

**The LLM's unique value** is creative variety — finding tracks the user owns
but rarely plays that fit the current vibe context. That's a *discovery*
problem, not a *ranking* problem, and it doesn't need to fire on every play.

### Architecture

| Layer | Trigger | What runs | Cost |
|---|---|---|---|
| **Hot path (refill)** | `TrackPlayed` event drains mirror | SQL query against `TrackVibe` ordered by distance + recency filter | $0 |
| **Discovery layer** | Weekly cron (e.g., Sunday 03:00 UTC) | One LLM call: "given my library + last week's plays + recent ratings, suggest ~5 discovery tracks I haven't played in 90+ days that fit my taste" | ~$0.05/week, ~$0.20/month |
| **Vibe re-scoring** | Existing Phase 6 cadence (already done) | Pre-computed `TrackVibe.distance` | already paid |

Total ongoing cost: **~$0.05–0.20/month** vs current **~$150/month at full use**.

### Why not the alternatives

- **Option A (weekly LLM batch for all 30 picks):** Slightly *over*-uses the
  LLM. We'd be paying ~$0.05/week for picks that pure SQL ranking against
  pre-computed `TrackVibe.distance` would do for free. Still 100x cheaper than
  current, but leaves money on the table.
- **Option B (SQL-only, no LLM in suggestions at all):** Cheapest possible
  (~$0/month), but loses the "AI-curated" property of Phase 7. Risk that picks
  feel deterministic/repetitive over time. C buys back the AI variety at
  trivial cost.

## Prerequisite fixes (must land before C planning)

### BLOCKER: max_tokens=2000 truncation in suggestions_rank

Four sites in `suggestions_service.py` (lines 1301, 1337, 1502, 1520)
hardcode `max_tokens=2000`. With `deficit=30` and structured JSON output, the
model hits `stop_reason=max_tokens` mid-response, returns truncated JSON,
pydantic validation fails, refill aborts. NAS log evidence (16:51:00):

```
WARNING: Anthropic response stop_reason=max_tokens for purpose=suggestions_rank
ERROR: refill_suggestions_queue raised during maybe_schedule_refill (deficit=30)
pydantic_core._pydantic_core.ValidationError: 1 validation error for SuggestionRankingResponse
  Invalid JSON: EOF while parsing a string at line 112 column 7
```

Same class as hotfix `260512-kvs` (Phase 6.2 vibe-mapping bumped 3000→6000 / 4000→8000).

**Hotfix scope:** bump 2000 → 8000 at all four sites. ~5-min `/gsd-quick`. The
purpose is to *validate the CDL bootstrap fix end-to-end* (mirror populates,
playlist materializes in Plex) BEFORE committing to the C redesign. Without
this, we can't confirm the foundation works.

**Important:** the hotfix does NOT solve the cost problem — it just makes
calls succeed instead of fail. Each successful call still costs ~$0.05.
Don't leave Plex playing for an hour after the hotfix lands or you'll burn
through the daily $0.42 breaker. Just enough to confirm the playlist
materializes, then stop testing until C ships.

### Lesson to fold into C design

The truncation bug existed because `max_tokens` was an *implicit assumption*
that 2000 was enough. C's weekly discovery call should:

1. Size `max_tokens` generously (~4000-6000 even for ~5 picks).
2. Add an explicit `stop_reason=max_tokens` guard that retries with a larger
   budget instead of returning truncated JSON to pydantic.
3. Log the chosen `max_tokens` and observed output token count to LLMUsage
   so future sizing decisions are data-driven, not guess-driven.

### Caching engagement is MOOT for C

Current NAS log shows `cache_creation_input_tokens=0` (system prompt below
Sonnet 4.6's 2048-token caching threshold). Phase 7 D-07 was supposed to fix
this with a longer shared preamble — never landed.

**For C: ignore this entirely.** Caching only helps when you make multiple
calls within a 1-hour window. A weekly LLM call always cold-starts the cache.
We get the cost win from *call frequency*, not from *cache hits*. D-07 can be
deleted from the Phase 7 follow-up scope.

## Out-of-scope for C (separate quick fixes later)

- **`/api/library/stats` polling at ~10 req/sec** — UI bug from a debug page.
  Not a server bug per se, not a cost issue, not blocking C. Worth a quick
  task later (probably HTMX or Alpine polling misconfiguration).

## Recommended sequence

1. **`/gsd-quick`** — `max_tokens` 2000→8000 hotfix at 4 sites in
   `suggestions_service.py`. Test on NAS: play one track, confirm mirror
   populates and `Composer · Suggestions` playlist appears in Plex. Then stop
   testing.
2. **`/gsd-spec-phase 7.1`** (or `/gsd-discuss-phase` if scope is clear
   enough) — spec the SQL-refill + weekly-discovery architecture. Real
   architecture change, deserves a proper plan, not a quick task. Captures C's
   design decisions (including the max_tokens lesson and caching is moot).
3. **`/gsd-plan-phase 7.1`** → **`/gsd-execute-phase 7.1`** as normal.

## References

- Originating conversation: 2026-05-14 chat after CDL hotfix UAT
- Phase 7 CONTEXT.md: `.planning/phases/07-suggestions-queue-v1-chat-retirement/07-CONTEXT.md`
- CDL bootstrap fix: commit `8594589`
- Daily cost breaker: `DAILY_COST_BUDGET_USD = $0.42` (per quick task `260511-bpf`/`260512-kvs` lineage, recently constantified per `260514-cdl` predecessor)
- Same truncation class as Phase 6.2: hotfix `260512-kvs`
