# Phase 8 — Discussion Log

**Date:** 2026-05-16
**Mode:** default (gsd-discuss-phase)
**Areas presented:** 4 + 1 user-initiated polish addition + 1 mid-discussion bug surface = 6 effective areas

This log is a human-reference record of the discuss-phase conversation. The canonical context for downstream agents (researcher, planner) is `08-CONTEXT.md`. This file is NOT consumed by automated tooling.

---

## Areas Presented (multi-select)

All 4 presented, all 4 selected by user:

1. ☑ Discovery pipeline + rationale
2. ☑ Discovery refresh cadence + cost
3. ☑ Auto-ingest trigger mechanism
4. ☑ Mobile-responsive pass + Discover UX

User added a 5th item during the multi-select response:
> "i also want to make sure the home page has a clear 'llm cost this week' that shows me the cost up until the point of the next weekly refresh (the refresh being the 'first' event of the week as it has an llm call. this will serve as a check to confirm to runaway costs - lets initialize as zero as well as previous testing/costs are irrelevant."

→ Folded into Area 2 as decision D-B4.

A 6th item was added at the wrap-up gate:
> "as we start this phase, lets use the lidarr API to call and list the available profiles for metadata and quality so its an easy list to select from (two step -- first test the api, then select the drop downs to finalize the connection) -- additionally, want to add some colors to the ui for each of the different vibes (labels where they are should match colors as seen in home page -- make it pop a bit)"

→ Created Area E (Settings + Cross-Surface Polish) with D-E1 (two-step Lidarr settings) and D-E2 (vibe color coding).

A 7th, unplanned issue surfaced mid-write:
> "in the library page, i see this at the top next to 'last sync': Last synced: 2026-05-14T03:01:24.646182+00:00 · 10587 tracks - is this a visual bug or is the chron not working?"

→ Diagnosed as a real ~48h-stale sync. User chose "Fold into Phase 8 Plan 01" alongside the Lidarr connection-test fix. Captured as D-E3 + load-bearing warning in D-C1.

---

## Area 1 — Discovery pipeline + rationale

### Q1 — Pipeline shape

| Option | Selected |
|--------|---------|
| **Seed-first (MusicBrainz → LLM re-rank)** | ✅ |
| LLM-first (LLM → MusicBrainz validate) | ❌ |
| Hybrid | ❌ |

Locked as D-A1. Reasoning carried forward: cheaper than LLM-first, fewer hallucinations, matches Phase 7.1 cost discipline, anchors on adjacency (Pitfall 13).

### Q2 — Seed selection

| Option | Selected |
|--------|---------|
| **Per-vibe rotation (one seed per vibe)** | ✅ |
| Underrepresented genres only | ❌ |
| Low-listener-count starred artists | ❌ |
| User-picked vibe (on-demand) | ❌ |

User response: "1, but how often is this refreshed?" — triggered an early transition to Area 2 since seed rotation and refresh cadence are coupled. Locked as D-A2.

### Decisions captured
- D-A1: Seed-first pipeline
- D-A2: Per-vibe rotation seeds, rotated week-over-week with the Sunday cron
- D-A3: Popularity-bias hard gate locked from Pitfall 13 (not re-discussed)
- D-A4: "Why this artist?" = `{factual_hook} · {LLM_one_liner}` (Claude's discretion — extracted from pipeline answer)

---

## Area 2 — Discovery refresh cadence + cost

### Q1 — Refresh cadence

| Option | Selected |
|--------|---------|
| **Weekly cron (Sunday 03:00 UTC) — piggyback on SUGG-13** | ✅ |
| On-demand on first /discover open per day | ❌ |
| On-demand with pull-to-refresh button | ❌ |

Locked as D-B1.

### Q2 — Home-page weekly cost widget

| Option | Selected |
|--------|---------|
| **Compact chip, anchored to last cron tick** | ✅ |
| Card with breakdown | ❌ |
| Inline with vibes header | ❌ |

Locked as D-B4. Initialization rule (ignore historical/testing rows) explicit per user instruction.

### Decisions captured
- D-B1: Sunday cron piggyback; third step inside `_weekly_maintenance_tick`
- D-B2: `/discover` serves cached results; cache invalidates at next cron tick
- D-B3: Catch-up + failure handling mirror SUGG-13 D-C2/D-C3 (Claude's discretion — not re-asked)
- D-B4: Compact home-page chip, `CostMeterBaseline` row excludes historical rows

---

## Area 3 — Auto-ingest trigger mechanism

### Q1 — Trigger mechanism (first ask)

User asked for clarification:
> "can you clarify what this question is about? what are we 'ingesting'?"

Re-explained the closing-the-loop sequence (Lidarr download → Plex scan → Composer sync → Essentia → vibe slot → Suggestions eligibility).

### Q2 — Trigger choice (after clarification)

User answer redirected the question:
> "i actually dont think this is an issue for 3 reasons: syncing daily isnt an issue as long as we know for sure the sync of tracks from plex, into composer is happening daily. from there, it could take hours if not days for essentia to go throughte tracks depending on how many there are -- so any sort of 'immediate' trigger wont really matter as we could still be hours behind having the essentia data to use. in a perfect world its quick but the time it takes to run our pipeline i think isnt a huge issue here - its easy to add a webhook from lidarr for debugging but we dont need this quick ingestion"

Effectively rejected all three options as designed and re-scoped the gray area to "the existing daily sync is enough; don't build new trigger infra."

### Decisions captured
- D-C1: Auto-ingest piggybacks on existing daily `library_sync`. No new trigger infrastructure.
- D-C2: Lidarr OnImport webhook explicitly deferred (documented as "easy to add later for debugging").
- D-C3: Per-add status timeline lives on `/discover` (per-card lifecycle) + `/debug/discovery` (full history). Lazy Lidarr status poll on render with 5-min cache, NOT a cron.

---

## Area 4 — Mobile-responsive pass + Discover UX

### Q1 — Mobile-responsive scope

| Option | Selected |
|--------|---------|
| Library track table → card list on mobile | ❌ |
| Light touch — horizontal scroll wrappers + safe-area-inset only | ❌ |
| **Full mobile-first rewrite of library page** | ✅ |

Locked as D-D1. Probably its own plan within Phase 8.

### Q2 — Discover page layout

| Option | Selected |
|--------|---------|
| **Vibe-grouped sections** | ✅ |
| Flat ranked list | ❌ |
| Card grid (2-column mobile) | ❌ |

User answer:
> "#1, but want to also be able to 'dismiss' selection (new ones dont reappear) but perhaps this 'dismiss' action can help with future suggestions?"

Added requirement: dismiss with potential signal-to-future-discovery effect. Triggered Q3.

### Q3 — Dismiss scope

| Option | Selected |
|--------|---------|
| **Artist-only exclude (simplest)** | ✅ |
| Artist + soft-deboost neighbors | ❌ |
| Artist + shared-label deboost | ❌ |

Locked as D-D5.

### Q4 — Add flow + post-add visibility

| Option | Selected |
|--------|---------|
| **Tap-to-expand mirrors Phase 7** | ✅ (with modification) |
| Modal confirmation + bottom 'Recently added' shelf | ❌ |
| Inline add, no confirmation, status replaces card | ❌ |

User modification:
> "i think #1 but once tracks are all added and downloads complete, remove from here as its not longer needed (its a typical artist)"

Lifecycle modified from "14-day status row" to "remove once fully ingested" (D-D4 final form).

### Decisions captured
- D-D1: Full mobile-first rewrite of library page
- D-D2: Vibe-grouped sections on `/discover`
- D-D3: Tap-to-expand artist cards mirror Phase 7
- D-D4: After add, status row replaces card until fully ingested + vibe-slotted, then REMOVED from `/discover`
- D-D5: Dismiss = artist-only exclude into `DiscoveryDismissed`

---

## Area E — Settings + Cross-Surface Polish (user-initiated)

User volunteered at the wrap-up gate. Not separate questions — the user spec'd both directly:

### D-E1 — Two-step Lidarr settings flow
"Test the api, then select the drop downs to finalize the connection." Fetch BOTH `qualityProfile` AND `metadataProfile` from Lidarr in the same connection test; user picks both; both persist.

### D-E2 — Vibe color coding
"Want to add some colors to the ui for each of the different vibes (labels where they are should match colors as seen in home page -- make it pop a bit)." Per-vibe persistent color, surfaced everywhere a vibe label renders.

---

## Area F (unplanned bug surface) — Library sync reliability

Mid-write, user reported:
> "in the library page, i see this at the top next to 'last sync': Last synced: 2026-05-14T03:01:24.646182+00:00 · 10587 tracks - is this a visual bug or is the chron not working?"

Diagnosed as real ~48h stale on 24h interval. Two likely root causes named (container restart resetting `IntervalTrigger`; silent sync failure caught + not surfaced).

### User choice for handling

| Option | Selected |
|--------|---------|
| Investigate now (quick check) | ❌ |
| Quick task before Phase 8 | ❌ |
| **Fold into Phase 8 Plan 01** | ✅ |
| Note + defer | ❌ |

### Decisions captured
- D-E3: Library-sync cron reliability fix lands in Plan 01 alongside D-E1 (Lidarr connection-test). Investigate root cause, add lifespan missed-tick catch-up (mirrors Phase 7.1 D-C2), surface silent failures on `/debug/events`. Plan 01 ship gate updated accordingly.

Cross-reference added to D-C1 as a load-bearing dependency warning.

---

## Notes & Deferred Items

User's responses surfaced a number of deferred ideas (all captured in `<deferred>` section of CONTEXT.md):

- Lidarr OnImport webhook (deferred — D-C2)
- Last.fm as parallel candidate source (deferred unless MusicBrainz proves sparse)
- Dismiss neighborhood deboost (deferred — D-D5 chose artist-only)
- Settings UI to unblock dismissed artists (deferred — out of v1 scope)
- Per-vibe discover-this-vibe button (deferred — cost concerns)
- Bulk add ("add all 10 in this section") (deferred — amplifies Pitfall 14 failure modes)
- Vibe color customization UI (deferred — auto-assign in v1)

---

## Items at Claude's Discretion

These were not raised with the user; planner decides:

- Rotation mechanism for D-A2 seed selection (random / round-robin / LRU)
- MusicBrainz Python client choice (default to `musicbrainzngs`; rate-limit handling)
- Discovery LLM call shape (single call w/ cached preamble + per-call candidate list; `max_tokens` floor; `MaxTokensTruncationError` retry)
- Discovery cost ceiling — repurpose Phase 7.1 D-D3 `WEEKLY_DISCOVERY_BUDGET_USD` as shared
- Empty states for `/discover` when Lidarr unconfigured / no vibes / pre-first-cron-tick
- Discovery candidate cap (`DISCOVERY_CANDIDATE_LIMIT`, `DISCOVERY_PROMPT_TOKEN_CEILING`) — mirror Phase 7.1
- `/debug/discovery` page layout (mirror existing `/debug/*` pattern)
- DiscoveryAdd Lidarr-status polling (lazy on render, 5-min cache)
- Color palette assignment order

Researcher should validate:
- pyarr 6.6 `add_artist()` signature with both profile IDs
- MusicBrainz rate-limit at this volume
- 2–3 candidate color palettes for vibes (dark theme, color-blind-safe)
- Whether daily sync interval needs to move from `IntervalTrigger` to `CronTrigger` for restart-resilience

---

*End of discussion log.*
