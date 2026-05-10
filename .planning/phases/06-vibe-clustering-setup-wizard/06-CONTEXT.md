# Phase 6: Vibe Clustering + Setup Wizard - Context

**Gathered:** 2026-05-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 6 turns the populated rated set (Phase 5 foundation) into 3–7 user-named, persistent "vibe" playlists in Plex, and wires `RatingChanged` events to auto-slot newly-rated tracks into matching vibes within ~10 seconds. The first-run setup wizard (`/setup` multi-page HTMX) walks the user through (optional) webhook configuration, runs scikit k-means + LLM naming on the rated set, and then enters a **conversational refinement loop** where the user iterates with the LLM ("merge late-night chill with late-night drives", "add a pre-workout playlist", "split workout into cardio and lifting") until the proposed vibe set feels right. On "Looks good", Composer creates `Composer · {name}` Plex playlists and seeds them with rated tracks via audio-feature distance to centroid (soft membership cap of 2 vibes per track, 1-std-dev margin).

Post-wizard, every `RatingChanged` event for `rating > 0` slots the track into matching vibes; rating cleared (10→0) drops it from all vibes; manual overrides (`assigned_by='manual'`) are sticky and survive re-cluster. The "Re-cluster vibes" button in settings re-runs the same conversational refinement loop and commits via a diff (rename in place, archive dropped vibes as `Composer · {name} (archived)`, create new vibes fresh).

This phase establishes three project-wide conventions that Phases 7 and 8 inherit: `hx-ext="alpine-morph"` for HTMX swaps inside Alpine components, mobile-first portrait layout (`h-dvh`, `safe-area-inset-bottom`, 44px touch targets), and the LLM-cluster-proposes-then-user-refines interaction model that Phase 7's Suggestions ranking and Phase 8's Discovery will mirror.

</domain>

<decisions>
## Implementation Decisions

### Conversational refinement loop (the heart of the wizard — user's framing)

- **D-01:** Cluster proposal is a multi-turn LLM refinement loop, NOT a static "rename/merge/drop" UI. Initial proposal: scikit k-means (auto-k by silhouette ≥ 0.25 within `k_max = min(7, n_rated // 15)`) → AnthropicClient call (`purpose="vibe_clustering_initial"`) names + describes each cluster. Then the user types natural-language feedback ("combine X and Y", "rename Z", "add a pre-workout playlist", "split workout into cardio vs lifting") and each turn calls AnthropicClient again with the previous proposal + user message + cached taste-profile prefix. Loop applies to BOTH initial wizard step 3 AND the "Re-cluster vibes" button in settings (same code path).
- **D-02:** LLM operates at the **cluster-set level only**: it can rename a vibe, edit a description, drop a vibe, merge two vibes, split a vibe, or add a brand-new vibe (with seed-track suggestions). It DOES NOT move individual tracks between clusters — track-level edits are the user's job in Plexamp. Removes a class of hallucinated-track-id risk (Pitfall 10) and keeps the data model clean.
- **D-03:** LLM response is structured as a Pydantic `VibeProposalSet` (a list of `VibeProposal` records: `{name, description, action: "keep"|"new"|"merged_from"|"split_from"|"renamed_from"|"dropped", source_vibe_ids: list[int], seed_track_indices: list[int]}`). `seed_track_indices` are integer indices into the candidate track list that was passed in the prompt — never raw `ratingKey` strings (Pitfall 10 / D-03 from Phase 5). Server-side maps indices back to ratingKey before any DB or Plex write.
- **D-04:** Loop bounding: soft cap of 10 refinement turns per session (counter shown in UI: "Refinement 4 of 10"). After 10, surface a "save what you have or start over" choice. "Looks good" button is always visible during the loop and commits the current proposal. Daily LLM-call limit (50/day from Phase 5 LLMUsage scaffolding) is the underlying cost circuit breaker — refinement loop counts against it like any other AnthropicClient call.
- **D-05:** Centroid recomputation: each accepted refinement turn re-runs scikit k-means CONSTRAINED by the LLM's proposed cluster set (e.g., "split workout into cardio and lifting" → k-means with k+1 within Workout's track set; "merge X and Y" → union the two clusters' tracks and recompute single centroid). For LLM-added new vibes (`action: "new"`), the LLM returns `seed_track_indices` (5–15 tracks it thinks belong); those seeds initialize a new k-means cluster (k+1 with a single fixed seed centroid) that pulls in additional tracks by distance. 4-D feature vector is z-score normalized (mean/std from rated-set computed once at clustering start) before any distance math — tempo's 60–180 range can't co-exist with energy/dance/valence's 0–1 in raw Euclidean.

### Wizard structure & step flow

- **D-06:** Wizard hosted at `/setup` as multi-page HTMX (each step is its own URL — refresh-safe, back/forward works, SetupState persists step). Step routes:
  - `/setup` (Step 1: rating-source confirm — show rated-track count from Plex, gate at <30 rated)
  - `/setup/webhook` (Step 2: conditional — only present if `ServiceConfig` has no persisted webhook URL; reuses Phase 5 EVT-07 partials `webhook_url_radio.html` + `webhook_test_indicator.html`)
  - `/setup/propose` (Step 3: initial cluster proposal + conversational refinement loop)
  - `/setup/confirm` (Step 4: final review of vibes — names, descriptions, member counts; "Push to Plex" button)
  - `/setup/done` (Step 5: success page with link to vibes home — placeholder for Phase 7)
- **D-07:** Wizard auto-launches by `GET /` redirect when WIZ-01 conditions are met (`Vibe.count() == 0` AND `Track.count(user_rating > 0) >= 1`). Once any Vibe row exists, `/` no longer redirects. Settings page surfaces a "Run setup wizard again" link that resets `SetupState` and redirects to `/setup`. No "dismiss" flag — auto-redirect persists until the wizard finalizes.
- **D-08:** SetupState (single-row table id=1 per ARCHITECTURE.md): `step` (rating_source / webhook / proposing / confirming / done), `draft_proposals_json` (full LLM proposal set + refinement-turn history; ~50–100KB; SQLite holds it once vs cookies round-tripping every request), `started_at`, `completed_at`, `refinement_turn_count`, `last_llm_call_id` (FK to LLMUsage for "Re-show last cluster proposal" debug page).
- **D-09:** Wizard pages are mobile-first portrait — `h-dvh` (not `h-screen`), `env(safe-area-inset-bottom)` on any sticky-bottom buttons, 44px touch targets enforced by a CSS token `--touch-target-min: 44px`. Adopt `hx-ext="alpine-morph"` on `<body>` in `app/templates/base.html` as the FIRST templates change — Phase 6 is the first phase shipping HTMX swaps inside Alpine components per Pitfall 15 / UI-05.
- **D-10:** `<30 rated` cold-start: Step 1 shows "Composer needs ~30 rated tracks to find your vibes (you have N). Keep rating in Plexamp; the wizard will be ready when you are." with a "Refresh count" button (re-queries DB) but no path forward. `30–49 rated` single-vibe degraded mode: Step 3 shows ONE proposed vibe named "Your Taste" (user-editable) with all rated tracks as members; refinement loop still works (user can rename, can't split-with-fewer-than-30-per-cluster); the wizard finishes normally with one `Composer · {name}` Plex playlist. User has 460+ rated tracks → almost certainly hits the full multi-vibe path; the cold-start UX is for completeness.

### Per-cluster information density (refinement loop UI)

- **D-11:** Each proposed vibe in the refinement-loop UI displays:
  - Editable name + 1-line description (LLM-generated; user can edit inline before commit)
  - 5 closest-to-centroid seed tracks shown as "Title — Artist"
  - Audio-feature chip mapping centroid to human words ("High energy · Fast tempo · Low danceability"); single helper `app/services/vibe_helpers.py::feature_chip_text(centroid)` maps centroid→string
  - "Show all N tracks" disclosure that expands inline to the full member list (scrollable; Alpine `x-collapse`)
  - Member count + silhouette-score-for-this-cluster (small, secondary text)
- **D-12:** Refinement input is a textarea (mobile-friendly, multi-line) with a "Refine" button beneath the cluster cards. Below the input: small explainer text "Try: 'merge X and Y', 'add a pre-workout playlist', 'rename Z to W', 'drop the Workout vibe'" — sets expectations on what the LLM can do.
- **D-13:** Force-k picker available on the INITIAL k-means run (before any LLM call) — small dropdown "Find {3..7} vibes" with default = silhouette-picked auto-k. After initial proposal, k changes happen via natural-language refinement only (e.g., "give me 6 vibes" prompts the LLM to add or split). No separate "Re-propose with same prompt" button — refinement loop is the only escape hatch.

### Slot-in semantics on RatingChanged

- **D-14:** Endorsement threshold is **any `user_rating > 0`** — the user's rating system is binary in practice (5★ or unrated; raw value is always either 10.0 or null/0). This simplifies the locked "≥3 stars endorsement" rule from PITFALLS Pitfall 8 to a no-op for this user. Phase 7 Suggestions endorsement uses the same simple `user_rating > 0` rule.
- **D-15:** Slot-in fires from `app/services/event_handlers.py::handle_rating_changed` AFTER the existing taste-profile recompute hook (D-18 from Phase 5). New module `app/services/vibe_service.py` exposes `slot_track(rating_key) -> SlotInResult` which: looks up the Track row, checks `user_rating > 0`, computes z-score-normalized 4-D distance from each Vibe's centroid, applies soft-membership rule (closest vibe + any second vibe whose distance ≤ closest_distance + 1·std-dev, capped at 2), upserts `TrackVibe(track_id, vibe_id, distance, assigned_at, assigned_by="auto-slot")`, then pushes the additive delta to each affected Plex playlist via the new `app/services/plex_client.update_playlist_items()` helper. All PlexAPI calls inside `slot_track` go through `asyncio.to_thread` (Pitfall 4 / EVT-06 — enforced by the existing AST static test).
- **D-16:** Per-track `asyncio.Lock` keyed on `plex_rating_key` serializes rapid rate-correct sequences (3★→4★ within 200ms — irrelevant for this user's binary rating but required by Pitfall 23 / VIBE convention). The lock dict lives at module scope in `vibe_service.py`; entries are cleaned up after each slot-in by `lru_cache`-like eviction (max 100 active locks; rare cases fall back to a short-lived lock instance).
- **D-17:** **Unanalyzed-but-rated track handling**: the slot-in path checks `Track.energy IS NULL OR Track.tempo IS NULL OR Track.danceability IS NULL OR Track.valence IS NULL`. If any feature missing, set `Track.pending_slot_in = TRUE` (new column added via `_migrate_add_columns`) and return without slotting. The existing analysis_service post-analysis hook (`trigger_post_sync_analysis` from v1 Phase 3) is extended with a new step: after each track's audio features are written, query `WHERE pending_slot_in = TRUE AND user_rating > 0`, slot each one via `vibe_service.slot_track()`, then clear the flag. Retroactive slot-in; user never sees a rated track miss its vibe.
- **D-18:** **Rating cleared (10→0)**: when `RatingChangedEvent.new_rating` is `None` or `0`, `vibe_service.unslot_track(rating_key)` removes ALL `TrackVibe` rows for the track and pushes a delete to each affected Plex playlist (one `update_playlist_items` per affected vibe — additive removal). This is the ONE place where Composer removes tracks from a Plex playlist as a side effect of a Plex event; document the convention so future phases don't accidentally use it as a precedent for other auto-removal.
- **D-19:** **Manual overrides are sticky**: `TrackVibe.assigned_by = "manual"` (set when the user moves a track between vibes — Phase 6 doesn't add a UI for this, but the column exists for future Phase 9 features and for user direct-DB tweaks). The slot-in path skips any `(track_id, vibe_id)` row already marked manual. Manual overrides survive re-cluster — D-26 below explicitly preserves them.

### Re-cluster flow (settings button)

- **D-20:** Settings page adds a "Re-cluster vibes" button (behind a confirm modal: "This will run the AI cluster proposer again and let you refine the new set. Your current Plex playlists stay intact during refinement; nothing changes until you click 'Looks good'."). Click → resets `SetupState` to `step="proposing"` with `draft_proposals_json` cleared, redirects to `/setup/propose`. Wizard pages handle the case of "post-wizard re-cluster" by hiding Step 1 and 2 nav (those don't apply) and routing "Looks good" to a dedicated re-cluster commit endpoint that runs D-21 instead of the initial-bootstrap path.
- **D-21:** **Re-cluster commit reconciliation (diff-based)**:
  - **Renamed vibe**: existing Vibe row's `name` + `description` updated; existing `Composer · {old_name}` Plex playlist renamed in place via `playlist.editTitle()`; tracks reconciled additively (Pitfall 5 — never remove user-edited tracks; just add new auto-slot members).
  - **Kept vibe (same name)**: no Plex change; tracks reconciled additively if membership shifted.
  - **Dropped vibe**: existing `Composer · {name}` Plex playlist renamed to `Composer · {name} (archived)`; corresponding `ManagedPlaylist` row deleted (so the dual-marker rule of D-27 stops Composer from managing the archived playlist). Vibe row marked `is_active=False` (preserved for audit but no longer slotting). User can manually delete the archived playlist in Plexamp.
  - **New vibe**: fresh Vibe row created; new Plex playlist `Composer · {name}` created; populated from LLM-suggested seed tracks + initial slot-in pass over the rest of the rated set.
  - **Split vibe** (one becomes two): treated as one drop + two news. Old playlist → archived; two new playlists created.
  - **Merged vibe** (two become one): treated as two drops + one new. Both old playlists → archived; one new playlist created with the union of tracks (de-duped via `TrackVibe` upsert).
- **D-22:** **Manual overrides preserved**: before re-cluster commit, snapshot all `TrackVibe` rows with `assigned_by="manual"` keyed by `(track_id, source_vibe_name)`. After commit, for each manual override: try to find the new vibe by name (LLM may have renamed, in which case match the kept-from-which-old-vibe via the `VibeProposal.source_vibe_ids` field — see D-03); if found, re-create the manual TrackVibe row pointing at the new vibe. If old vibe was dropped (no successor), the override is lost (logged to `/debug/vibes`).
- **D-23:** **Idempotency on re-cluster commit**: each operation in D-21 is wrapped in a try/except so a partial Plex API failure doesn't leave the DB and Plex out of sync. Failures roll forward (log + continue); after commit, run a reconciliation pass that re-fetches every `Composer · {name}` playlist from Plex, compares to `ManagedPlaylist` registry + Vibe state, and surfaces drift on `/debug/vibes`. Pitfall 6 (silent track drops on Plex push) is already addressed by VIBE-12's post-push verify; same path used here.

### Plex playlist management (creation, push, ownership)

- **D-24:** New module `app/services/plex_playlist_service.py` (separate from `plex_client.py` to keep the lower-level client lean). Exposes:
  - `async def create_playlist(name, rating_keys) -> str` — creates `Composer · {name}` in Plex, returns the new playlist's `ratingKey`. Reuses the batch-fetchItems pattern from `chat_service.push_playlist_to_plex` (line 562).
  - `async def update_playlist_items(playlist_rating_key, rating_keys) -> ReconcileResult` — replaces playlist contents. Internally: fetch current contents, compute additive delta (only adds missing tracks; never removes — Pitfall 5), apply add, then re-fetch and verify (VIBE-12). Returns `ReconcileResult(added=[], unchanged=[], silently_dropped=[], retried=[])`.
  - `async def archive_playlist(playlist_rating_key, current_name)` — renames to `Composer · {name} (archived)` via `playlist.editTitle()`. Called only by re-cluster path on dropped vibes.
  - `async def rename_playlist(playlist_rating_key, new_name)` — single rename. Validates `new_name.startswith("Composer · ")`.
- **D-25:** Initial wizard finalize ("Push to Plex" in Step 4): one create_playlist call per Vibe; each populated with the rated tracks whose closest-centroid is that vibe (initial slot-in is a single in-memory pass — no LLM, just z-score-normalized 4-D Euclidean distance). All creates run sequentially under a single semaphore (max 1 concurrent Plex playlist mutation — keeps Plex API quiet on the NAS). On any create failure, surface "Created N of M playlists. Retry?" inline; partial state is recoverable (already-created playlists are persisted in `ManagedPlaylist`).
- **D-26:** No "Composer · Suggestions" playlist creation in Phase 6. WIZ-06 implies it ("creates the Composer · Suggestions playlist"), but Phase 7 owns Suggestions logic + the queue itself; Phase 6 finalize creates ONLY vibe playlists. Phase 7 Plan 01 will add the Suggestions bootstrap step. (If Phase 7 planning surfaces a need to bootstrap an empty playlist earlier, this can be revisited; deferring keeps Phase 6 scope tight.)
- **D-27:** **Hands-off rule reinforced** (OPS-06 / Pitfall 20): every read or write against a Plex playlist must check both markers — `playlist.title.startswith("Composer · ")` AND `ManagedPlaylist.exists(plex_rating_key=...)`. Either marker missing → playlist is legacy / user-owned / archived; do not touch. v1-generated playlists that happen to use `Composer · ` prefix without a `ManagedPlaylist` row also fail this check and are correctly left alone. Helper `is_managed_playlist(plex_rating_key) -> bool` lives in `plex_playlist_service.py` and is used by every read/write.

### Data model + schema migration

- **D-28:** New tables (added via `SQLModel.metadata.create_all()` in `app/database.py::init_db()`):
  - `Vibe` — per ARCHITECTURE.md schema; add `is_active: bool = True` for re-cluster archival audit.
  - `TrackVibe` — composite PK on `(track_id, vibe_id)`; `assigned_by` enum: "cluster" | "auto-slot" | "manual".
  - `ManagedPlaylist` — per ARCHITECTURE.md; `kind` is currently always `"vibe"` in Phase 6 (Phase 7 will add `"suggestions"`).
  - `SetupState` — per ARCHITECTURE.md; extended with `refinement_turn_count: int` and `last_llm_call_id: Optional[int]` (FK to LLMUsage.id for the "re-show last cluster proposal" diagnostic surface).
- **D-29:** New `Track` columns added via the existing `_migrate_add_columns()` shim in `app/database.py` (line 48):
  - `pending_slot_in: bool` (default FALSE) — D-17 retroactive slot-in flag.
  - No other Track changes; centroid/spread live on Vibe, not Track.
- **D-30:** Indexes added in `_migrate_add_columns`:
  - `ix_trackvibe_vibe_id` on `(vibe_id)` — for "show me a vibe's members" queries (`/debug/vibes`, future vibe detail page).
  - `ix_managedplaylist_kind` on `(kind)` — for filtering vibe vs suggestions playlists.
  - `ix_track_pending_slot_in` on `(pending_slot_in)` partial index — for the analysis_service post-analysis hook to find waiting tracks fast.
- **D-31:** No Alembic — extend the existing shim per OPS-01 (locked at milestone level). New tables via `create_all()` (idempotent). Schema migration is silent on existing DBs (additive only).

### LLM clustering call architecture

- **D-32:** New module `app/services/vibe_clusterer.py` (function module, not a singleton — clustering is one-shot per refinement turn). Exposes:
  - `async def initial_cluster_proposal(forced_k: Optional[int] = None) -> VibeProposalSet` — runs k-means + AnthropicClient initial naming. Forced k overrides silhouette pick.
  - `async def refine_proposals(prior_proposals: VibeProposalSet, user_message: str) -> VibeProposalSet` — single conversational turn. AnthropicClient call with cached system prompt (taste-profile prefix from `taste_profile_service`, Pitfall 9 `ttl="1h"` enforced by the client).
  - `def materialize_clusters(proposals: VibeProposalSet) -> dict[int, list[int]]` — runs the constrained k-means in D-05 to map proposals back to track-level membership.
- **D-33:** scikit import is allowed in `vibe_clusterer.py` ONLY. The static AST test in Phase 5 forbade sklearn in `taste_profile_service.py`; extend the test with an allowlist: `vibe_clusterer.py` and any future `app/services/clustering*.py` files may import sklearn. All other `app/services/*` files remain banned from sklearn import. Test name: `tests/test_vibe_clusterer.py::test_sklearn_only_in_clusterer_module`.
- **D-34:** AnthropicClient `purpose` field per call: `"vibe_clustering_initial"`, `"vibe_clustering_refine"`, `"vibe_clustering_recluster"`. Lets the Phase 7 settings cost dashboard segment cost by feature later.
- **D-35:** Prompt structure for the conversational turns:
  - System prompt (cached, `ttl="1h"`, ≥2048 tokens enforced by AnthropicClient SONNET_4_6_CACHE_MIN_TOKENS check): taste profile summary text + top artists/genres + generic "you are Composer's vibe clusterer" instructions + the rated track list as numbered indices (0..N-1) with `"{i}: {title} — {artist} (energy={e:.2f}, tempo={t:.0f}, dance={d:.2f}, valence={v:.2f})"`. Cached prefix means refinement turns 2..10 cost ~10% of turn 1 in input tokens.
  - User prompt (uncached, varies per turn): the prior proposal as JSON + the user's natural-language feedback message + "respond with the revised VibeProposalSet as JSON matching the schema below" + Pydantic schema dump.
  - Response: parsed via `model_validate_json` per D-03 from Phase 5 (no Instructor — locked).

### /debug/vibes page (DEBUG-02)

- **D-36:** `/debug/vibes` HTML page (rendered server-side, no JS-only content per DEBUG-05) shows:
  - **Per-vibe section** (one card per active Vibe): name, description, centroid features (raw 4-D values + the human-friendly chip from D-11), member count, silhouette score (computed at last cluster pass), `last_clustered_at`, `is_active`. Sortable column for centroid distance not needed — bulk view is fine.
  - **Last 20 slot-in decisions** (table): timestamp, track title — artist, source vibe(s) chosen, computed distance(s), whether soft-membership 2nd vibe applied. Reads from a new `SlotInLog` table (lightweight; rotates rows >30 days old via existing EventLog purge pattern from Phase 5 if/when added).
  - **"Re-show last cluster proposal" button**: surfaces the LLM naming response from the last `LLMUsage` row with `purpose IN ("vibe_clustering_initial", "vibe_clustering_refine", "vibe_clustering_recluster")`. Renders the raw user/system/response text in `<pre>` blocks. Critical when slot-in produces a surprising assignment and the user wants to debug the LLM's reasoning.
  - **Drift indicator**: count of `Composer · ` Plex playlists not in `ManagedPlaylist` (legacy / archived) and count of `ManagedPlaylist` rows whose `last_pushed_at` differs from the Plex playlist's actual mtime — diagnostic for OPS-06 violations or Plex-side edits.
  - **"Reslot all rated tracks" button** (manual recovery): re-runs `vibe_service.slot_track()` over every `user_rating > 0` track. Linked from `/debug/vibes`, never auto-fired. Useful after a re-cluster or bug recovery.
- **D-37:** Page reachable from settings page footer ("View vibes diagnostics →") AND from the `/debug` index page (Phase 7 will add the index per DEBUG-05; Phase 6 just adds a direct link from settings).

### Claude's Discretion

- Internal naming for Pydantic types (`VibeProposalSet` vs `ClusterProposal`, etc.) — pick a consistent convention; document if it differs from existing patterns.
- Exact textarea placeholder text + the example refinement prompts in D-12.
- `/debug/vibes` table layout details — Tailwind classes, color coding for distance ranges, pagination if needed (probably not at 20 rows).
- Initial slot-in pass concurrency (D-25): 1 concurrent Plex mutation is the conservative default; can be relaxed to 2–3 if measurements show it's safe.
- z-score normalization caching: store rated-set mean/std in `Vibe` row at clustering time (so subsequent slot-ins use the SAME normalization basis), or recompute on every slot-in (consistent but slightly more CPU). Pick whichever is simpler.
- Wizard navigation copy + step labels — UI-SPEC level decisions.
- Confirm-modal copy for re-cluster button (D-20) — exact wording.
- Whether to surface "you have N unanalyzed-but-rated tracks waiting" on `/debug/vibes` — diagnostic value but not critical.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project & milestone scope (most authoritative)
- `.planning/PROJECT.md` — v2.0 Music Companion locked decisions; "tiered similarity" (LLM clusters/ranking, distance for slotting); hands-off existing playlists; mobile-first; Anthropic locked
- `.planning/REQUIREMENTS.md` §"Vibe Curation" (VIBE-01..12), §"Setup Wizard" (WIZ-01..07), §"Debug" (DEBUG-02), §"Operations" (OPS-06)
- `.planning/ROADMAP.md` §"Phase 6: Vibe Clustering + Setup Wizard" — goal, success criteria, Key Concerns table

### v2.0 research (THIS milestone — most recent, highest authority)
- `.planning/research/SUMMARY.md` §"Phase 6" — locked decisions on k-means + LLM naming, anthropic + scikit-learn deps, ship gate
- `.planning/research/STACK.md` — `anthropic>=0.100`, `scikit-learn>=1.8`, Tailwind 4 mobile primitives (h-dvh, dvw, safe-area-inset)
- `.planning/research/ARCHITECTURE.md` §"Vibe", §"TrackVibe", §"ManagedPlaylist", §"SetupState" schemas — pre-specified data model. §"Pattern 6" (server-side wizard state). §"Flow 5" (first-run setup wizard sequence).
- `.planning/research/PITFALLS.md` — pitfalls baked into Phase 6:
  - **Pitfall 3** (k-means cold start, n=30 floor, silhouette ≥ 0.25)
  - **Pitfall 5** (Plex playlist edit conflicts, additive-only push)
  - **Pitfall 6** (stale ratingKey silent drops, post-push verify)
  - **Pitfall 9** (Anthropic cache TTL `ttl: "1h"` explicit — already handled by Phase 5's AnthropicClient)
  - **Pitfall 10** (LLM hallucinated track IDs — use integer indices, validate)
  - **Pitfall 15** (HTMX swaps clobber Alpine state — `alpine-morph` extension required)
  - **Pitfall 16** (iOS Safari `100vh` — use `h-dvh` + `safe-area-inset-bottom`)
  - **Pitfall 17** (44px touch targets)
  - **Pitfall 18** (no hover-only states)
  - **Pitfall 20** (v1-generated playlists hands-off + dual-marker rule)
  - **Pitfall 23** (per-track lock for rate-correct races)
  - **Pitfall 24** (multi-vibe membership cap=2, 1-std-dev margin)

### v1.0 research (foundation context)
- `.planning/research/v1.0/STACK.md`, `.planning/research/v1.0/ARCHITECTURE.md` — singleton service pattern, sync_service idiom

### User notes (vision and constraints)
- `.planning/notes/hardware-profile.md` — Synology DS423+, synobridge Docker network; clustering CPU budget is generous (4-core 2.0GHz Celeron, 32GB RAM)
- `.planning/notes/mobile-first.md` — touch targets ≥44px, no hover-only UX, portrait-first design

### Existing code (reusable / extension points)
- `app/services/anthropic_client.py` — REUSE for vibe clusterer + refinement loop. Already has explicit `ttl="1h"`, structured output via `model_validate_json`, LLMUsage logging, SONNET_4_6_CACHE_MIN_TOKENS check. Do not reimplement.
- `app/services/taste_profile_service.py` — REUSE its taste-profile-summary text + structured aggregates as the cached system-prompt prefix for clustering calls. The `_aggregate_rated_set_sync()` helper produces the rated-track list in a clusterer-ready shape.
- `app/services/event_handlers.py::handle_rating_changed` — EXTEND. After existing taste-profile recompute, add `vibe_service.slot_track(event.plex_rating_key)`. Same `asyncio.to_thread` discipline.
- `app/services/event_handlers.py::handle_rating_changed` already routes through dispatch_event with INSERT OR IGNORE on EventLog — slot-in benefits from the dedupe automatically.
- `app/services/plex_client.py::_map_track()` — REUSE as-is for any new Plex track fetches inside the clustering / playlist push paths. No changes needed here.
- `app/services/plex_client.py` (whole file) — pattern for `asyncio.to_thread` PlexAPI wrapping. New `plex_playlist_service.py` follows the same convention.
- `app/services/sync_service.py`, `app/services/analysis_service.py`, `app/services/backfill_service.py` — singleton service pattern (module-level `_status` dataclass). New `vibe_service.py` follows the same shape (though most operations are stateless functions, the slot-in lock dict is module-level state).
- `app/services/analysis_service.py::trigger_post_sync_analysis` (or equivalent) — EXTEND with the post-analysis "reslot pending tracks" hook (D-17). Verify exact function name during planning; pattern may have moved during Phase 5 work.
- `app/services/chat_service.py::push_playlist_to_plex` (line 562) — pattern for Plex playlist creation via `plex.fetchItems` + `plex.createPlaylist`. New `plex_playlist_service.create_playlist` follows the exact shape.
- `app/database.py::_migrate_add_columns()` (line 48) — EXTEND for `Track.pending_slot_in` column + new indexes.
- `app/database.py::init_db()` — register new SQLModel tables (Vibe, TrackVibe, ManagedPlaylist, SetupState) via the existing model imports + `create_all()` flow.
- `app/main.py::lifespan()` — no new lifespan hooks required (slot-in fires from existing event dispatcher path; no new background services).
- `app/templates/base.html` — ADD `hx-ext="alpine-morph"` to `<body>` element. CSS reset additions: `--touch-target-min: 44px` token, `min-h-dvh` body class, `viewport-fit=cover` in the existing viewport meta tag.
- `app/templates/partials/webhook_url_radio.html`, `app/templates/partials/webhook_test_indicator.html` — REUSE in wizard Step 2 (D-06).
- `app/templates/pages/settings.html` — ADD "Re-cluster vibes" button + "Run setup wizard again" link + "View vibes diagnostics →" footer link.
- `app/routers/api_webhooks.py` — already returns 200 in <50ms; webhook test routing already in place.
- `app/services/event_bus.py`, `app/services/event_handlers.py` — single dispatcher, INSERT OR IGNORE dedupe; vibe slot-in inherits this.

### Prior phase decisions (do not re-decide)
- `.planning/phases/05-plex-event-foundation-rating-sync/05-CONTEXT.md` — Phase 5 anchored conventions (D-09 PlexAPI sync→async wrap, D-15 raw 0-10 user_rating, D-06 typed events, D-07 EventLog dedupe, D-19 schema migration shim, D-22 debug page conventions)
- `.planning/phases/05-plex-event-foundation-rating-sync/05-01-PLAN.md` through `05-04-PLAN.md` — implementation patterns to mirror (singleton state, AST static tests, lifespan ordering, multipart Form parsing)
- `.planning/phases/04-playlist-generation/04-CONTEXT.md` — chat service playlist push pattern (`createPlaylist` via fetchItems batch); to be retired in Phase 7 but the push primitive is reused
- `.planning/phases/01-foundation-configuration-deployment/01-CONTEXT.md` — settings page patterns, encryption helpers, `service_card` HTMX flow

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **AnthropicClient** (anthropic_client.py): handles `cache_control={"type":"ephemeral","ttl":"1h"}`, structured output via `model_validate_json`, LLMUsage logging, cost circuit breaker scaffolding. ZERO new LLM-client work in Phase 6 — just add `purpose` strings ("vibe_clustering_initial", "vibe_clustering_refine", "vibe_clustering_recluster").
- **Taste profile service** (taste_profile_service.py): produces the cacheable system-prompt prefix the clusterer needs. The `_aggregate_rated_set_sync()` helper returns rated tracks in a clusterer-ready shape. The 4-D centroid + top artists/genres are exactly what the LLM gets told.
- **`asyncio.to_thread` PlexAPI pattern** (plex_client.py:8 onwards, all functions): every PlexAPI call already wraps. New `plex_playlist_service.py` follows the same convention — enforced by the existing AST static test `tests/test_event_handlers.py::test_no_blocking_plexapi_in_async`.
- **Singleton service modules** (sync_service.py, analysis_service.py, backfill_service.py): module-level `_status` dataclass + state-machine + `start/stop/get_status` functions. `vibe_service.py` follows for the slot-in lock dictionary; clustering is stateless function-level (`vibe_clusterer.py`).
- **`_migrate_add_columns()` shim** (database.py:48): proven schema migration path. Add `pending_slot_in` + new indexes to the existing dict.
- **HTMX partial pattern** (api_webhooks.py + partials/webhook_*.html): test-and-respond + indicator update via HTMX poll. Wizard Step 2 reuses verbatim.
- **Event dispatcher** (event_bus.py + event_handlers.py): already routes RatingChanged through `handle_rating_changed`. Adding slot-in is a single line in the existing handler — no new dispatcher infrastructure.
- **Encryption helpers** (encryption.py): not needed in Phase 6 (no new secrets).
- **Plex playlist creation primitive** (chat_service.py:562 `push_playlist_to_plex`): batch `fetchItems` + `createPlaylist`. New `plex_playlist_service.create_playlist` follows the exact shape.

### Established Patterns
- **HTMX swap convention**: introduce `hx-ext="alpine-morph"` on `<body>` in `base.html` as the first templates change. Document in CLAUDE.md per Pitfall 15. Phase 7 inherits.
- **JSON in form fields**: `Annotated[str, Form()]` + `json.loads()` (NEVER `pydantic.Json[Model]` — FastAPI bug #10997). The wizard's textarea input doesn't need this (it's just a string), but any future form posts with JSON payloads do.
- **Module-level state singletons** (sync_service.SyncStatus, analysis_service.AnalysisStatus, backfill_service.BackfillStatus): lock dict in `vibe_service.py` follows.
- **Service-card test-and-configure flow** (Phase 1): pattern for the wizard's webhook step (already proven in Phase 5 settings page).
- **AST static tests** (tests/test_event_handlers.py::test_no_blocking_plexapi_in_async): extend with sklearn-allowlist test (`vibe_clusterer.py` only).
- **TasteProfile cache invalidation**: D-18 from Phase 5 — recompute on ≥10% rated-set delta. Re-cluster path explicitly forces a recompute regardless of delta (the LLM needs the latest summary as the cached prefix).

### Integration Points
- **Lifespan startup** (main.py): NO new tasks. Slot-in fires from the existing event dispatcher; clustering fires from /setup HTTP routes. No background loops.
- **Settings page** (api_settings.py + templates/pages/settings.html): adds "Re-cluster vibes" button (POST `/api/vibes/recluster/start`), "Run setup wizard again" link (POST `/api/setup/reset` then redirect to `/setup`), "View vibes diagnostics →" footer link.
- **Track model** (models/track.py): one new column `pending_slot_in` via migration shim. No model class changes other than adding the field.
- **Sync / analysis / backfill services**: backfill is the path that populates user_rating in bulk; the lifespan-launched `maybe_trigger_first_run_backfill()` (Phase 5) populates ratings BEFORE Phase 6 wizard ever runs. No coordination needed beyond the existing post-analysis hook for D-17.
- **Event handlers** (event_handlers.py): `handle_rating_changed` extended with one line to call `vibe_service.slot_track`. `handle_track_played` is unchanged in Phase 6 (Phase 7 owns Suggestions consumption).
- **Pages router** (pages.py): adds `/setup`, `/setup/webhook`, `/setup/propose`, `/setup/confirm`, `/setup/done`, `/debug/vibes`. May factor wizard routes into a new `routers/api_setup.py` for hygiene.

</code_context>

<specifics>
## Specific Ideas

- **Conversational refinement is the defining UX of v2.0.** The user explicitly framed it as "the core of the app." Phase 6 establishes the pattern; Phase 7 Suggestions ranking and Phase 8 Discovery are expected to mirror the loop (LLM proposes → user gives natural-language feedback → LLM refines → commit). Plan and document accordingly so the LLM-prompt structure, refinement-turn cap, and "Looks good" UI affordance can be reused.
- **User's rating system is binary in practice (5★ or unrated).** This simplifies endorsement-threshold logic across v2 — every rating > 0 IS an endorsement. PITFALLS Pitfall 8's "≥3 stars" rule is a no-op for this user; Phase 7 should also use `user_rating > 0` rather than checking ≥6.0.
- **Re-cluster is the showcase for the conversational loop.** It runs the same `/setup/propose` UI but commits via diff (rename / archive-dropped / create-new) rather than fresh-create. Phase 7's Suggestions refresh button can mirror this.
- **Diff-based + archived approach** (D-21) preserves user trust: user listening continuity isn't broken by a re-cluster (renamed playlists keep their members; manual edits in Plexamp survive the rename via Pitfall 5's additive-only push). Dropped vibes are renamed not deleted, so the user can recover a dropped vibe by un-archiving in Plexamp.
- **Dual ownership marker is non-negotiable.** Every Plex playlist read or write must check both `name.startswith("Composer · ")` AND `ManagedPlaylist.exists(plex_rating_key=...)`. Helper enforces this; reviewers should reject any new code touching playlists that bypasses the helper. v1 playlists, archived playlists, and user-renamed-our-playlists all fail one or the other marker and stay untouched.
- **Wizard state visibility for debugging.** `SetupState.last_llm_call_id` lets `/debug/vibes` "Re-show last cluster proposal" surface the exact LLM round-trip from the most recent refinement turn. This is high diagnostic value when the user says "why did Composer slot Daft Punk into Late Night?" — the answer is in the cluster that won, the centroid distance, and the LLM's naming response.
- **Mobile-first is the hard constraint.** The wizard refinement-loop UI runs on a phone in portrait. Every cluster card must be readable without horizontal scroll at 375px wide; the textarea input must sit above the keyboard; the "Looks good" button should be sticky-bottom with `safe-area-inset`.

</specifics>

<deferred>
## Deferred Ideas

- **Track-level moves between vibes during refinement** — explicitly out of scope (D-02). User can do this in Plexamp post-commit. If the conversational loop turns out to need this in real use (Phase 6 ship-test feedback), revisit; else Phase 9 or v2.1.
- **"Composer · Suggestions" playlist bootstrap on wizard finalize** — WIZ-06 implies it; Phase 6 defers to Phase 7 Plan 01 (D-26). Revisit during Phase 7 planning if Suggestions ranking has a hard need to fire on wizard exit.
- **Re-cluster scheduling / nudge** — out of scope per PROJECT.md. If usage shows the user forgets to re-cluster after the rated set grows significantly, surface a soft prompt "Your rated set has grown 30% — re-cluster?" but do not auto-run. Phase 9 candidate.
- **Vibe taste-drift indicator** — "Your Late Night vibe has drifted X% from its original centroid" — interesting metric but not actionable in v2.0. v2.1.
- **Per-vibe cover art** — Plex shows a generic cover for Composer-created playlists; could LLM-generate art or pick a representative track's album art. Cute, not critical. v2.1.
- **Wizard skip / dismiss flag** — D-07 chooses "auto-redirect persists until finalize" (no dismiss). If user feedback says this is too aggressive, add a "set up later" link that flags the wizard as dismissed for 7 days; revisit in Phase 6 ship-test.
- **Configurable cluster-count range** — k_max formula `min(7, n_rated // 15)` is hardcoded per VIBE-06. If the user grows to 5000 rated tracks the formula gives k_max = 7 still (capped); seems fine. Configurable settings widget = v2.1 candidate.
- **Real-time refinement progress indicator** — each refinement-loop turn takes ~5s of LLM time. A "thinking..." spinner is plenty. Streaming the LLM response is a future polish.
- **Manual override management UI** — Phase 6 stores `assigned_by="manual"` rows but doesn't expose a UI for the user to set them. Tracks moved between vibes in Plexamp will have to be re-synced to vibes manually (or wait for re-cluster). Phase 9 if it becomes friction.
- **/debug/vibes slot-in log compaction** — first version logs every slot-in. If it grows unbounded, add a 30-day rotation. Revisit when the table size hits 100k rows.

</deferred>

---

*Phase: 6-Vibe Clustering + Setup Wizard*
*Context gathered: 2026-05-09*
