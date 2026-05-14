# Phase 7: Suggestions Queue + v1 Chat Retirement - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-13
**Phase:** 07-suggestions-queue-v1-chat-retirement
**Areas discussed:** Refill cadence + bootstrap, Shortlist composition, Queue UI + dismiss interaction, Skip-tracking calibration

---

## Refill cadence + bootstrap

### Q1: When does the queue first populate?

| Option | Description | Selected |
|--------|-------------|----------|
| Auto on wizard finalize (Recommended) | Phase 6 finalize triggers the first ranking call so the user lands on /suggestions with the queue already full | partial ✓ (fresh setup only) |
| Lazy on first /suggestions visit | Empty until user navigates; ranking call fires with a 'building your queue' loader | |
| Manual 'Build queue' button | Explicit user trigger; closest to v1's 'generate playlist' mental model | |

**User's clarifying question:** "Would #1 work if the wizard has already run? At this point, we should expect the wizard to not need to run as we have all of the data clustered."

**Resolution:** Dual-bootstrap design. Auto on wizard finalize covers fresh setup. A separate one-shot lifespan migration covers the "Phase 7 deploys to instance with existing vibes" case.

### Q1b: Bootstrap path for existing-deploy case?

| Option | Description | Selected |
|--------|-------------|----------|
| One-shot lifespan migration (Recommended) | Phase 7 first-deploy startup checks 'vibes exist AND no Suggestions ManagedPlaylist row' → fires bootstrap; gated by MigrationLog row (Phase 6.1 pattern). Wizard finalize calls the same bootstrap function | ✓ |
| Lazy on first /suggestions visit | Page shows 'building your queue' loader; no migration code | |
| Bootstrap on first refill-trigger event | Queue stays empty until first scrobble; first refill doubles as bootstrap | |

**User's choice:** One-shot lifespan migration.
**Notes:** One bootstrap function, two callers (wizard finalize + lifespan migration). Idempotent.

### Q2: Refill trigger after bootstrap?

| Option | Description | Selected |
|--------|-------------|----------|
| 60s-debounced event window (Recommended) | First scrobble schedules refill 60s later; subsequent scrobbles batch into same refill | |
| Strict 1:1 — every scrobble → one refill | Each consumed track triggers exactly one ranking call. Burst risk trips cost breaker | |
| Threshold-only (refill when queue < N of 30) | Wait until queue drops below threshold then refill in one batch | ✓ |

**User's choice:** Threshold-only.
**Notes:** "If I'm listening it will refill; if I'm not it won't." Composes with the SUGG-11 per-event debounce already locked.

### Q3: How many tracks per refill?

| Option | Description | Selected |
|--------|-------------|----------|
| Refill to target (variable batch) (Recommended) | Always tops queue back up to target (default 30). Simplest invariant: queue.size == target | ✓ |
| Fixed batch of 5 | Predictable cost per call; may drift around target | |
| 1 per consumed track (1:1) | Strict drain-replace model; queue oscillates around target | |

**User's choice:** Refill to target.

---

## Shortlist composition

### Q1: How should the ~50-track shortlist be balanced across vibes?

| Option | Description | Selected |
|--------|-------------|----------|
| Balanced across all vibes (Recommended) | Proportional split (~8 per vibe for 6 vibes). Queue feels diverse | ✓ |
| Weighted by listen activity per vibe | Recently-played vibes get more candidates; needs listen-time tracking infra not yet built | |
| Focused on top 2-3 vibes by activity | Deeper match; sleepy vibes stagnate | |

**User's choice:** Balanced across all vibes.

### Q2: Source pool for unrated candidates?

| Option | Description | Selected |
|--------|-------------|----------|
| Broad pool + distance pre-filter (Recommended) | All unrated w/ features minus SuggestionHistory minus hard-negatives; then filter to within 2σ of a vibe centroid | ✓ |
| Broad pool only | All unrated; LLM sees everything | |
| Narrow — only tracks close to a vibe centroid | Highest quality bar, smallest pool; same tracks cycle | |

**User's choice:** Broad pool + distance pre-filter.

### Q3: LLM prompt shape — reuse the Phase 6.2 cached preamble?

| Option | Description | Selected |
|--------|-------------|----------|
| Shared longer preamble (Recommended) | Cached preamble = vibe defs + taste profile + Composer context, >2048 tokens. One cache namespace serves vibe assignment and suggestions ranking | ✓ |
| Separate suggestions preamble | New cached prefix scoped to suggestions only | |
| You decide | Defer to planner | |

**User's choice:** Shared longer preamble.

---

## Queue UI + dismiss interaction

### Q1: Layout style for the Suggestions queue on mobile?

| Option | Description | Selected |
|--------|-------------|----------|
| Compact list (Recommended) | 48px album art + title + artist + tiny vibe chip per row. ~6 rows visible on iPhone portrait | ✓ |
| Cards (richer) | Bigger square album art + rationale subtitle; ~2-3 cards per screen | |
| Hybrid — next-up emphasis | Top of page = prominent cards; rest = compact list | |

**User's choice:** Compact list.

### Q2: How does the "Why this track?" rationale appear?

| Option | Description | Selected |
|--------|-------------|----------|
| Tap row to expand inline (Recommended) | Default state compact; tap → Alpine x-show expansion shows rationale + actions | ✓ |
| Always-visible subtitle | Rationale under artist name, truncated if too long. Visual noise | |
| Long-press → modal | Conserves space; long-press undiscoverable on iOS | |

**User's choice:** Tap row to expand inline.

### Q3: Dismiss action?

| Option | Description | Selected |
|--------|-------------|----------|
| Inside the expanded-row view (Recommended) | Dismiss button lives in expanded view alongside Play in Plexamp. Two-tap = no accidents | ✓ |
| Swipe-left reveals dismiss button | Native-feeling; swipe infrastructure not built | |
| Always-visible X button | One-tap dismiss; mis-tap risk on 44px targets | |

**User's choice:** Inside the expanded-row view.

---

## Skip-tracking calibration

### Q1: Soft-negative window?

| Option | Description | Selected |
|--------|-------------|----------|
| 14 days (Recommended — default) | Matches SuggestionHistory dedup window. Balanced for casual listening | ✓ |
| 7 days | Tighter signal; risk = penalize tracks user might've liked | |
| 30 days | Slower learning | |
| Track-count based | If played ≥3 times without rating; Plex viewCount drift complicates | |

**User's choice:** 14 days.

### Q2: How does soft-negative affect ranking of similar tracks?

| Option | Description | Selected |
|--------|-------------|----------|
| Inject context into LLM prompt (Recommended) | Prompt addendum: "user heard but didn't rate similar tracks: X, Y, Z; deprioritize". LLM disambiguates intent | ✓ |
| Small linear penalty in pre-LLM shortlist score | Cheap deterministic ~10% similarity reduction. Math is blind to nuance | |
| Both — small math penalty AND LLM context | Strongest signal; highest complexity | |

**User's choice:** Inject context into LLM prompt.

### Q3: Hard-negative artist deboost duration?

| Option | Description | Selected |
|--------|-------------|----------|
| Until user rates 3+ stars of that artist (Recommended) | Track-level exclusion forever; artist deboost stays until positive signal. Natural recovery | ✓ |
| Rolling 60-day window | Time-based recovery; signal evaporates without user action | |
| Track-only — no artist deboost | Conservative; same artist's other tracks keep appearing | |

**User's choice:** Until user rates 3+ stars.

---

## Claude's Discretion

The user delegated these to my judgment:

- **Empty-state UX during initial bootstrap** — reuse hotfix 260512-k3n LLM progress card
- **Vibe coverage indicator UX placement** — per-vibe card on home, threshold default 25 per requirement, LLM call only on explicit user CTA tap
- **`/debug` index page layout** — simple plain-HTML list, linked from settings footer
- **"Play in Plexamp" deeplink behavior** — `plexamp://` URI, browser fallback to Plex Web, no detection logic
- **Prompt-cache TTL math** — explicit `ttl: "1h"` per SUGG-05; refill bursts mostly fit within one cache window
- **Queue auto-scroll on scrobble** — preserve scroll position; rely on HTMX `outerHTML` + `alpine-morph` for smooth visual update

## Deferred Ideas

- Listen-time-per-vibe tracking for weighted shortlists (Phase 9 candidate if it ships)
- Skip-bomb detector threshold tuning (default ships; revisit numbers post-UAT)
- Vibe coverage indicator threshold N configurable from settings UI (Phase 7 if simple; else Phase 8 polish)
- Cross-session prompt cache priming (not Phase 7 scope; revisit if usage patterns warrant)
- v1 chat data archival/export tool (separate quick task if ever needed)
