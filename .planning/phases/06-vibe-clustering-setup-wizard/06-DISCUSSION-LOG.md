# Phase 6: Vibe Clustering + Setup Wizard — Discussion Log

**Date:** 2026-05-09
**Mode:** discuss (default)
**Phase number:** 6

This log captures the user-facing discussion turns that produced `06-CONTEXT.md`. For human reference (audits, retrospectives) only — downstream agents (researcher, planner, executor) read CONTEXT.md, not this file.

---

## Areas Selected for Discussion

The user selected ALL FOUR areas presented:

1. Cluster proposal UX (step 3)
2. Wizard structure & step flow
3. Slot-in semantics on RatingChanged
4. Re-cluster flow (settings button)

Two areas (cold-start UX & degraded mode, /debug/vibes layout) were not surfaced as separate discussion topics — they were folded into Claude's discretion, with hard rules captured in CONTEXT.md (D-10 cold-start, D-36/D-37 debug page).

---

## Area 1 — Cluster Proposal UX

### Q1: How much editing power does the user get on proposed clusters in step 3?

**Options presented:**
- Rename + drop only (Recommended)
- Add: merge two clusters
- Add: merge + split
- Full surgery (incl drag tracks)

**User selected:** Add: merge two clusters.

*(NOTE: This decision was effectively SUPERSEDED later by the user's pivot in Area 4 to a conversational refinement loop that replaces the static rename/merge/drop UI. See Area 4 sub-discussion.)*

### Q2: What info does the user see for each proposed vibe?

**Options presented:**
- Name + desc + 5 seed tracks (Recommended)
- Above + audio-feature chip
- Above + expandable full track list
- Above + top artists/genres

**User selected:** Above + expandable full track list (= name + desc + 5 seed tracks + audio-feature chip + expandable full member list).

→ Captured as **D-11**.

### Q3: What's the escape hatch if proposed clusters look bad?

**Options presented:**
- Re-propose button (same k) (Recommended)
- Re-propose + k picker
- No escape — accept & re-cluster later
- Re-propose with explicit feedback

**User selected:** Re-propose + k picker.

→ Captured as **D-13** (force-k picker available on initial run; conversational refinement supersedes the "Re-propose" button later).

---

## Area 2 — Wizard Structure & Step Flow

### Q1: How should the wizard be hosted and structured?

**Options presented:**
- `/setup` multi-page HTMX (Recommended)
- Single page + Alpine state machine
- Modal over `/`

**User selected:** `/setup` multi-page HTMX.

→ Captured as **D-06**.

### Q2: Should the wizard include a webhook step (reusing Phase 5 EVT-07 partials)?

**Options presented:**
- Yes — dedicated step, reuses partials (Recommended)
- No — wizard skips it, settings owns it
- Conditional — only if not configured

**User selected:** Conditional — only if not configured.

→ Captured as **D-06** (Step 2 is conditional based on `ServiceConfig.webhook_url` presence).

### Q3: How should the wizard be triggered for first-time users?

**Options presented:**
- Auto-redirect from `/` (Recommended)
- Banner CTA on `/`
- Settings-only entry
- Auto + dismissible

**User selected:** Auto-redirect from `/`.

→ Captured as **D-07** (auto-redirect persists until finalize; no dismiss flag).

---

## Area 3 — Slot-in Semantics on RatingChanged

### Q1: What rating value triggers auto-slotting?

**Options presented:**
- Any rating > 0
- ≥3 stars (raw ≥6.0) (Recommended per PITFALLS Pitfall 8)
- ≥4 stars (raw ≥8.0)
- Configurable in settings

**User typed (free-form):** "i have a simple rating system -- its either 5 stars or not rated -- so we are only talking about one 'flavor' of tracks here to simplify"

→ Captured as **D-14**: any `user_rating > 0` is the endorsement trigger (because the user's ratings are binary in practice). PITFALLS' "≥3 stars" rule is a no-op for this user. **Project-level fact**: documented for Phase 7 to use the same simple `user_rating > 0` rule.

### Q2: How should rated-but-not-yet-analyzed tracks be handled?

**Options presented:**
- Queue — slot when analyzed (Recommended)
- Skip silently
- Trigger analysis on demand
- Surface in /debug/vibes

**User selected:** Queue — slot when analyzed.

→ Captured as **D-17**: new `Track.pending_slot_in` column; analysis_service post-analysis hook reslots retroactively after Essentia features are written.

### Q3: How should rating-cleared (10→0) and manual overrides be handled?

**Options presented:**
- Cleared → immediate drop; manual = sticky (Recommended)
- Cleared → immediate drop; manual = until re-cluster
- Cleared → keep; user removes manually
- Cleared → immediate drop; no manual overrides yet

**User selected:** Cleared → immediate drop; manual = sticky.

→ Captured as **D-18** (rating cleared → immediate drop from all vibes + Plex playlists) and **D-19** (manual overrides sticky, preserved across re-cluster via D-22 snapshot logic).

---

## Area 4 — Re-cluster Flow (and the conversational pivot)

### Q1: How should old vibes carry over to new ones on re-cluster?

**Options presented:**
- Wizard-style review (Recommended) — k-means + LLM names → review screen → confirm
- Apply directly, map by name
- Apply directly, all-new identities
- Wizard review, count-locked

**User typed (free-form):** "i wanted to talk about this a bit -- this is the core of the app -- so i think it needs to be first suggested by what is seen by LLM and user should be able to state back what he wants changed -- i think the 'simplest' thing here would be for the user to be able to state/reprompt as they should be able to give explicit direction on what to change rather than 'hoping' the results are what they want"

→ **MAJOR PIVOT.** The user reframed re-cluster (and by extension the entire vibe-shaping interaction) as a **conversational refinement loop**: LLM proposes → user types natural-language feedback → LLM refines → repeat → "Looks good" commits. This supersedes the static rename/merge/drop UI from Area 1.

### Q2: Where should the conversational loop apply?

**Options presented:**
- Both wizard step 3 and re-cluster (Recommended given "core of the app")
- Re-cluster only
- Both, with Area 1 affordances ALSO available

**User selected:** Both wizard step 3 and re-cluster.

→ Captured as **D-01** (loop applies to both initial proposal and re-cluster, same code path). Area 1 Q1 decision (merge button) is now subsumed by the conversational model.

### Q3: How much surgery should the LLM perform from user feedback?

**Options presented:**
- Names + descriptions + boundaries only — rename, merge, drop, split (no individual track moves)
- Above + LLM moves individual tracks (Recommended given "core of the app")
- Free-form: LLM clusters from scratch each turn

**User typed (free-form):** "#1 - as i wont need to move a swath of tracks -- as long as i can edit myself in plexamp i can remove what i dont want eventually -- i just want to be able to state 'dont like this vibe, add another instead' or 'can we ensure we have a pre-workout pklaylist too'?"

→ Captured as **D-02** (cluster-set-level edits only: rename, drop, merge, split, ADD). LLM does NOT move individual tracks; that's a Plexamp operation. The "add a new vibe" use case ("ensure we have a pre-workout playlist") is explicitly supported via LLM `seed_track_indices` in the proposal schema.

### Q4: How should the loop be bounded (cost + iterations + commit)?

**Options presented:**
- Soft cap + 'Looks good' button (Recommended)
- Hard cap at 5 turns
- Unbounded with daily quota
- One-shot + targeted edit

**User selected:** Soft cap + 'Looks good' button.

→ Captured as **D-04** (soft cap of 10 refinement turns; "Looks good" always visible; daily quota from Phase 5 LLMUsage scaffolding is the underlying breaker).

### Q5: How should existing Plex playlists be reconciled on commit?

**Options presented:**
- Diff-based: rename/preserve/create/delete (Recommended)
- Delete-and-recreate all
- Append-only
- Diff-based + preserve dropped as 'archived'

**User selected:** Diff-based + preserve dropped as 'archived'.

→ Captured as **D-21**: rename in place for kept-with-new-name vibes (additive track reconcile per Pitfall 5), archive dropped vibes by renaming to `Composer · {name} (archived)` and removing from `ManagedPlaylist` registry, create fresh playlists for new vibes. Preserves user listening continuity.

---

## Deferred Ideas

Captured in CONTEXT.md `<deferred>` section:
- Track-level moves between vibes during refinement (out of scope per D-02)
- Composer · Suggestions playlist bootstrap on wizard finalize (deferred to Phase 7)
- Re-cluster scheduling / nudge (out of scope; Phase 9 candidate)
- Vibe taste-drift indicator (v2.1)
- Per-vibe cover art (v2.1)
- Wizard skip / dismiss flag (revisit in ship-test)
- Configurable cluster-count range (v2.1)
- Real-time refinement progress streaming (future polish)
- Manual override management UI (Phase 9 if friction)
- /debug/vibes slot-in log compaction (revisit at scale)

---

## Claude's Discretion (within bounds)

The following are not user-facing UX decisions; planning + implementation will pick within the bounds set above:

- Pydantic type names (`VibeProposalSet`, `VibeProposal`, `ClusterProposal`, etc.)
- Wizard textarea placeholder text + example refinement prompts
- `/debug/vibes` table layout, column widths, color coding
- Initial slot-in pass concurrency (default 1; can relax to 2–3 with measurement)
- z-score normalization caching strategy (per-Vibe row vs per-call recompute)
- Wizard navigation copy + step labels
- Re-cluster confirm-modal exact wording
- Whether `/debug/vibes` surfaces "you have N unanalyzed-but-rated tracks waiting"

---

*End of discussion log.*
