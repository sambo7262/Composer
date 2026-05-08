# Pitfalls Research — v2.0 Music Companion

**Domain:** Pivoting an existing v1 self-hosted music app (FastAPI + SQLite + APScheduler + PlexAPI + Lidarr + Anthropic) to a continuous companion model with webhooks, clustering, LLM ranking, and mobile-first UI
**Researched:** 2026-05-08
**Confidence:** HIGH (Plex/webhook/Anthropic specifics verified against official docs; clustering and mobile patterns verified against published research and ecosystem sources; v1 history captured from STATE.md decisions and existing PITFALLS.md)

---

## How To Read This Document

Every pitfall in this document was selected because it is **specific to adding v2 features to the existing v1 system** — not generic music app advice. v1's already-shipped pitfalls (Spotify uncertainty, library sync at scale, credential redaction, etc.) are in `.planning/research/v1.0/PITFALLS.md` and are still in force; this document layers v2-specific failure modes on top.

Each pitfall has: **trigger condition**, **prevention**, **detection signal in production**, and a **phase mapping** so mitigations can be baked into roadmap planning rather than discovered after deploy.

---

## Critical Pitfalls

### Pitfall 1: Plex webhooks deliver duplicate events with no idempotency key

**What goes wrong:**
Plex webhooks fire multipart POST requests for every `media.play`, `media.scrobble`, `media.rate`, `library.new`, etc. They use at-least-once delivery. There is no Plex-supplied `event_id` or idempotency token in the payload. The same `media.rate` event can arrive twice (network blip, retry on slow handler response, server restart in flight). Naive handlers double-process — a rating change triggers two slot-into-vibe operations, two LLM calls for ranking, duplicate suggestion refills, and double counts in any analytics.

**Why it happens:**
v1 had no webhook surface — APScheduler ran on a deterministic interval. v2 introduces an event-driven model where the same logical event (user rated a track) can fire multiple times, but developers default to "I got an event, do the work" without thinking about replay.

**How to avoid:**
1. Synthesize a deduplication key from payload fields that uniquely identify the logical event: `f"{event}:{ratingKey}:{userRating}:{updatedAt or lastViewedAt}"`. For `media.rate`, include the new rating value so a quick rate-then-unrate doesn't dedupe each other.
2. Maintain a `processed_webhook_events` table (event_key TEXT PRIMARY KEY, processed_at TIMESTAMP). On each webhook: `INSERT OR IGNORE` then check if it was a no-op — if yes, return 200 immediately without processing.
3. Prune the table to entries from the last 24 hours on a daily APScheduler job — duplicates beyond that window are vanishingly rare.
4. Always respond 200 within 5 seconds. Defer real work to a background task (use `BackgroundTasks` in FastAPI or queue into APScheduler). Plex retries on slow responses, which **is** the duplicate.
5. Make every downstream operation idempotent by design — "ensure track is in vibe playlist" not "add track to vibe playlist."

**Warning signs:**
- Same track gets logged as slotted into a vibe twice in the same minute
- Anthropic API call counter shows 2× the expected volume per rating event
- Plex playlist sequence numbers/timestamps show duplicate add operations on the server

**Phase to address:**
Webhook ingest phase. The dedupe table and `INSERT OR IGNORE` pattern must exist on day one of webhook handling — retrofitting after duplicate-processing bugs surface means cleaning up corrupted vibe membership and inflated LLM spend.

---

### Pitfall 2: Plex `userRating` is on a 0–10 scale; treating it as 0–5 produces off-by-two display bugs

**What goes wrong:**
Plex stores `userRating` as a float on a **0–10 scale** to support half-stars (1 star = 2.0, 2 stars = 4.0, 3.5 stars = 7.0, 5 stars = 10.0). Plexamp on iOS/Mac and Plex web all support half-star ratings. Developers casually assume `userRating` is a 0–5 integer (because the UI shows up to 5 stars), then either: (a) clip to 5, treating "10" as out-of-range; (b) display "10 stars" instead of "5 stars"; (c) compute clustering on a half-star track and a whole-star track as if they were two stars apart when they're actually one star apart.

**Why it happens:**
The UI shows stars but the API returns the doubled internal value. There is no documentation page from Plex that crisply states "userRating is 0-10, divide by 2 to get stars" — it's only learned by inspection.

**How to avoid:**
1. Wrap `userRating` access through a single helper: `def stars_from_user_rating(plex_user_rating: float | None) -> float: return (plex_user_rating or 0) / 2.0`. Use everywhere that needs to **display** stars.
2. For **logic** that depends on rating (slotting, clustering eligibility), keep the raw 0-10 value internally and only convert at display boundaries.
3. Treat `userRating == None`, `userRating == 0`, and missing keys as "unrated" — they behave the same for v2 purposes (track is a candidate for Suggestions, not a taste signal).
4. When filtering for "rated tracks" use `WHERE user_rating IS NOT NULL AND user_rating > 0` — Plex sometimes writes 0 instead of null for cleared ratings.
5. Persist the raw 0-10 value in SQLite. Never store the normalized 0-5 value as the canonical column — round-tripping through display formatting loses the half-star information.

**Warning signs:**
- UI shows "10 stars" anywhere
- A track rated 4.5 stars in Plexamp shows up as 4 or 5 in Composer instead of 4.5
- Clustering treats half-star differences as full-star jumps (silhouette score is unstable)

**Phase to address:**
The phase that introduces the rating sync model. Add a unit test: a Plex track with `userRating=7.0` must surface as 3.5 stars in the UI and 7.0 in the database.

---

### Pitfall 3: K-means clustering on a small or homogeneous rated set produces nonsense vibes

**What goes wrong:**
The user has 460 rated tracks now, but had ~50 a few months back and a future user might have 20. Naive k-means on small samples (say n=50, k=5) creates clusters of 10 tracks each — too small to be meaningful, and the random seed materially changes the result. Re-running clustering produces three different "vibe" sets. Worse: if the user's taste is tight (e.g., 80% electronic), every cluster ends up looking the same — silhouette scores collapse to near zero across all k values, and the "best k" picker returns 2 simply because there's no real structure to find. The user gets two indistinguishable vibe playlists.

**Why it happens:**
Tutorial k-means assumes balanced, well-separated, large-sample data. Music taste at the personal-library scale is rarely any of those. Silhouette score is also unreliable below ~50 samples per expected cluster, and developers reach for it as a one-line "is this clustering good?" check.

**How to avoid:**
1. Set a hard floor: clustering is disabled below 30 rated tracks. Show a setup-wizard message: "Composer needs about 30 rated tracks to find your vibes — keep rating in Plexamp and check back."
2. Cap k by sample size: `k_max = min(7, n_rated // 15)` — never produce a cluster with fewer than ~15 representatives.
3. Run clustering for k in [3, 4, 5, 6, 7] and pick by silhouette **only when silhouette > 0.25** (the "weak structure" threshold from the literature). Below that threshold, default to k=3 or surface a warning to the user that their taste looks tight and offer "1 vibe" as an option.
4. Use multiple random seeds (sklearn's default `n_init=10` is fine) and accept that runs may differ — disclose in the UI: "These vibes are a starting point. You can rename, merge, or split them."
5. Detect outliers before clustering: any track whose distance from every other rated track is >2σ from the mean nearest-neighbor distance gets flagged as "not yet a vibe-fit" and excluded from centroids. Alternative: use **k-medoids** (uses actual tracks as centers, robust to outliers) or **HDBSCAN** (density-based, handles noise natively, finds variable-density clusters) instead of k-means. For this problem size, k-medoids is the best default — same ergonomics as k-means but won't have a single weird starred track skew an entire centroid.
6. When the user adds 50+ new ratings, **don't auto-recompute centroids**. Composer's locked v2 decision is "re-clustering is user-requested only" (see PROJECT.md Out of Scope). Show a "Recluster vibes?" prompt when drift exceeds some threshold (e.g., 30% of new ratings don't fit any existing centroid within 1.5× median intra-cluster distance).

**Warning signs:**
- Two vibes have nearly identical centroids (intra-vibe distance > inter-vibe distance)
- Silhouette score is below 0.2 for every k tested
- New tracks consistently slot into "Vibe 4" regardless of mood — that vibe has become the catch-all
- A single starred track dominates a small vibe (e.g., 8-track cluster where one track is the obvious centroid)

**Phase to address:**
The vibe-clustering phase. Bake the n=30 floor and the silhouette threshold into the setup wizard. The recluster trigger and outlier detection can be deferred to a later phase but the floor must be in place from day one or the first-run wizard will fail for users with sparse ratings.

---

### Pitfall 4: PlexAPI is synchronous and blocks FastAPI's event loop inside async webhook handlers

**What goes wrong:**
The v2 webhook handler is naturally written `async def`. Inside, it calls `plex.fetchItem(rating_key)` from PlexAPI (which is fully synchronous — issues blocking HTTP requests via `requests`). This blocks the entire asyncio event loop. While the call is in flight (anywhere from 100ms for a fast Plex query to 5s on a slow NAS), no other webhooks, no UI requests, no APScheduler ticks can be serviced. Under a flood of `media.play` events from a listening session, the app appears to freeze. Worse: Plex retries on the now-slow handler, doubling the load on the same blocked event loop.

**Why it happens:**
PlexAPI predates async Python and there's no async equivalent. FastAPI silently runs `def` (sync) endpoints in a threadpool, but `async def` endpoints run on the event loop and any blocking call inside them poisons the whole process. Developers default to `async def` for "modern FastAPI" without noticing they've trapped a sync library on the wrong thread.

**How to avoid:**
1. Either: (a) declare the webhook handler `def webhook(request)` (no `async`), letting FastAPI dispatch it to the threadpool — simple and works; or (b) keep `async def` and wrap every PlexAPI call: `await asyncio.to_thread(plex.fetchItem, rating_key)`. Pick one and apply consistently.
2. Acknowledge the webhook synchronously and offload work to a background task: `background_tasks.add_task(process_rating_event, payload)`. The 200 response goes out in <50ms; PlexAPI calls happen in the threadpool without blocking the request loop.
3. Cap concurrency on the threadpool — Uvicorn's default is 40 threads. PlexAPI calls during a heavy listening session can exhaust this if every webhook spawns its own. Use a `Semaphore` (e.g., max 8 concurrent Plex API calls) inside the background task.
4. For periodic polling fallback (also synchronous), use APScheduler's `AsyncIOScheduler` only with async-wrapped callables. Don't mix `BackgroundScheduler` and `AsyncIOScheduler` — pick one (AsyncIOScheduler is what v1 already uses per STATE.md Phase 02-library-sync decisions).

**Warning signs:**
- Health endpoint becomes slow when listening to music
- Webhook log shows back-to-back identical events (Plex retried because the handler was slow)
- Uvicorn logs "thread pool exhausted" warnings under sustained webhook load
- Server CPU is low but request latency is high — classic event-loop stall signature

**Phase to address:**
The webhook ingest phase. Adopt the threadpool pattern as a project-wide convention before writing any handler that touches PlexAPI. Add a load test: simulate 30 webhooks/sec for 2 minutes and verify p95 health-check latency stays under 200ms.

---

### Pitfall 5: Plex playlist edit conflicts when user edits a Composer-managed vibe in Plexamp

**What goes wrong:**
Composer pushes a "Workout Vibes" playlist to Plex. Later, the user opens Plexamp, removes 3 tracks they're sick of, adds 5 from their queue, and reorders the rest. Composer's next slot-in event fetches the playlist from Plex, sees a different track set than its internal source-of-truth, and either: (a) overwrites the user's edits (data loss), (b) appends without reconciling (duplicate tracks, wrong order), or (c) errors out and stops slotting altogether (the vibe goes stale).

**Why it happens:**
v1 had no continuous-management model — a generated playlist was pushed once and never touched again. v2 makes Composer the owner of vibe playlists, but Plex/Plexamp gives the user full edit access too. There's no "owned by Composer, please don't edit" flag in Plex.

**How to avoid:**
1. Source-of-truth principle: **the Plex playlist is the truth, not Composer's local list**. On every slot-in, fetch the current Plex playlist contents, compute the additive delta (tracks Composer wants to add that aren't there), and apply only adds — never removes. User-removed tracks stay removed even if they still match the centroid.
2. Track a `composer_managed_playlists` table with `(plex_playlist_key, vibe_id, last_sync_signature)`. The signature is `hash(sorted(track_ids))`. If signature changes between Composer-initiated syncs, log a "user edited" event and adopt the new state as the new baseline before doing the next add.
3. Maintain a per-vibe **excluded tracks** list. When the user removes track X from "Workout Vibes" in Plexamp, on next sync detect "Composer last knew this track was in here, now it's not" and add X to the exclusion list — never re-add it via auto-slotting. Surface it in the UI: "You removed Daft Punk - Around the World from Workout Vibes. Don't auto-add again? [Yes / No]."
4. Don't preserve order. Composer's auto-slot adds tracks; user-driven order is preserved by Plex naturally. Don't fight the user by reordering.
5. **Hands-off principle from PROJECT.md** — if a playlist's name no longer matches what Composer expects, or its `summary`/`description` no longer contains the Composer-set marker (e.g., a hidden comment line), treat it as user-adopted and stop managing it. Log a warning and surface in settings: "Workout Vibes was manually renamed and is no longer managed by Composer."

**Warning signs:**
- Vibe playlist track count diverges between Plex and Composer's local DB
- User reports "I removed a track and it came back"
- Same track flickers in/out of a playlist over hours (rate event re-adds it, user removes it, repeat)

**Phase to address:**
The phase that introduces vibe-playlist management. The exclusion list and signature tracking must exist before the first auto-slot fires.

---

### Pitfall 6: Plex silently rejects playlist push when a track is no longer accessible

**What goes wrong:**
Composer holds a list of `ratingKey` values for a vibe playlist. The user moves a music file outside Plex's library, or Lidarr re-imports an album with new ratingKeys, or the Plex library is rebuilt. Composer pushes the playlist with a stale ratingKey. Plex either: (a) returns a 200 with the track silently dropped from the playlist (most common), (b) returns 404 only on the specific item add but the rest succeed, (c) returns 200 but the playlist on disk has the wrong tracks. Composer thinks the push succeeded, but the user opens Plexamp and sees a 28-track playlist where there should be 30, or worse, a "Track unavailable" placeholder.

**Why it happens:**
Plex's API is forgiving by design — it doesn't want a single bad ratingKey to fail an entire batch operation. PlexAPI inherits this behavior. v1's existing PITFALLS.md notes "ratingKeys can change if a library is deleted and re-added" but v1 never managed long-lived playlists, so the issue didn't bite.

**How to avoid:**
1. After every playlist push, **re-fetch the playlist** from Plex and compare track count and ratingKey set. If divergent, log a `PlaylistDriftWarning` with the missing keys.
2. For each missing ratingKey, attempt re-resolution: if the track has an `external_id` or stable `key` field stored locally, search Plex for `(artist, album, title)` and rebind. If found, update the ratingKey in SQLite. If not, mark the track `accessible=False` and exclude it from future slotting.
3. On every Plex library sync, audit ratingKeys: any track in `composer.tracks` not present in the latest Plex sync is flagged stale. Mass re-resolution by `(artist, title, album)` + duration tolerance (±2s).
4. Show the user a single line in the vibe playlist UI: "2 tracks unavailable — fix or remove?" rather than silently dropping them.

**Warning signs:**
- Vibe playlists shrink over time without user edits
- "Track unavailable" placeholders appear in Plexamp
- Logs show successful push but Plex playlist has fewer tracks than expected

**Phase to address:**
The phase that introduces vibe-playlist management. Audit logic can ship in a later polish phase but the post-push verification must be in place from the first push.

---

### Pitfall 7: `lastViewedAt` updates lag behind webhook delivery — race condition

**What goes wrong:**
A `media.scrobble` webhook fires when Plex decides a track was "played enough" (~50% played for music, configurable). Composer's handler immediately calls `plex.fetchItem(ratingKey).lastViewedAt` to confirm and to read the new play count. The returned value is **stale** — Plex hasn't flushed the database write yet. Composer thinks the scrobble is for an older listen, double-counts, or fails an idempotency check. Tested locally with one track and it works; under real listening flow at concurrency >1, race fires randomly.

**Why it happens:**
Plex sends webhooks from one subsystem and updates the metadata DB from another. There's no "wait until visible to API" ordering guarantee. The webhook payload itself contains the play context (account, player, ratingKey, time) — developers ignore it and re-fetch, which is the bug.

**How to avoid:**
1. **Trust the webhook payload.** It contains everything needed for the consumption signal (account ID, player, ratingKey, event type, timestamp from Plex). Don't re-fetch unless you specifically need a field that isn't in the payload.
2. If you must re-fetch (e.g., to read the new `viewCount` for ranking), retry on staleness: poll up to 3× with 500ms backoff, and accept the value only if `lastViewedAt > our_last_seen_value`.
3. Use the webhook's own timestamp as the canonical "when did this play happen" value. Store it in `track_consumption_events(track_id, event_type, plex_timestamp, processed_at)`. This dodges the entire staleness problem.
4. For the polling fallback path, the issue doesn't apply — polling reads `lastViewedAt` directly from the API, no race.

**Warning signs:**
- Logs show consumption events processed twice with the same `plex_timestamp` (correctly deduped) — fine
- Logs show consumption events with `lastViewedAt < plex_timestamp` — the race fired and the wrong value was read
- Suggestions queue refills don't drain in step with actual listening

**Phase to address:**
The phase that introduces consumption detection. The "trust the payload, don't re-fetch" rule should be a documented convention.

---

### Pitfall 8: `lastViewedAt` is a noisy consumption signal — skipped/scrubbed tracks count as plays

**What goes wrong:**
Plex's scrobble threshold for music is approximately 50% of track duration (some Plex sources say up to 80–90% for video, but music is lower). A user skipping through a playlist, listening to 30 seconds of each track, never triggers `media.scrobble` — good. But a user playing a track on Plexamp, walking away, and Plexamp continuing to play through a 4-minute track triggers a real scrobble even though the user wasn't listening. A user scrubbing to 60% to hear the chorus triggers a scrobble for the whole track. Composer treats every scrobble as "user enjoyed this track" and refills the Suggestions queue, drains a track from it, and biases the LLM ranker toward the (accidentally?) consumed track.

**Why it happens:**
Plex doesn't expose a confidence score on scrobbles. There is no "the user was attentive" signal — there can't be. Developers map "scrobble fired" → "user liked it" 1:1.

**How to avoid:**
1. Treat `media.scrobble` as **consumption** (this track left the queue), not endorsement. Drain it from Suggestions, but don't strengthen the taste signal off scrobbles alone.
2. Endorsement signal stays narrow: `userRating ≥ 3 stars` (i.e., raw `userRating ≥ 6`) is the only thing that creates a vibe-membership signal.
3. Detect skip-bombing in polling fallback: if the same player consumes >10 tracks in <10 minutes (avg <60s/track), treat that as "browsing, not listening" and don't drain Suggestions.
4. Combine scrobbles with `viewCount` delta: if `viewCount` increased by 1 but the previous listen was an hour ago, real consumption; if it increased by 1 and the previous listen was 30 seconds ago, suspicious.
5. **Don't include scrobble data in clustering.** v2's clustering input is the rated set only. Consumption affects the Suggestions refill loop; rating affects the vibe model.

**Warning signs:**
- Suggestions queue drains 30 tracks in 5 minutes (impossible if user is listening normally)
- Vibe playlists fill with tracks the user skipped past

**Phase to address:**
The Suggestions queue phase. The skip-bomb detection and the "scrobble ≠ endorsement" boundary must be a written convention before refill logic is implemented.

---

### Pitfall 9: Anthropic prompt cache TTL silently regressed from 1h to 5min in March 2026 — cache misses inflate cost

**What goes wrong:**
PROJECT.md targets ~$5/year of Anthropic spend. Composer's design plan assumes the user's taste profile (rated tracks summary, vibe centroids) is sent as a cached prefix on every LLM ranking call. Sonnet 4.6 prompt caching is the cost lever. In March 2026 Anthropic silently dropped the default ephemeral TTL from **1 hour to 5 minutes**. If Composer's LLM-ranking calls fire less than once every 5 minutes (which they will, in event-driven steady state), every call is a cache **miss** with a write penalty (cache writes cost 25% more than uncached input tokens for 5-min TTL). Net effect: paying *more* than no-caching baseline because of the cache-creation overhead, while still believing caching is saving money.

**Why it happens:**
The Anthropic docs describe `cache_control: {"type": "ephemeral"}` as the cache primitive without prominently noting the TTL change. Developers verify "yes, cache_control is set" but not "is the cache actually hitting".

**How to avoid:**
1. Use `cache_control: {"type": "ephemeral", "ttl": "1h"}` explicitly — pay slightly more on cache writes (1h cache writes cost 2× the base input rate, vs 1.25× for 5min) but the hits last long enough to actually amortize.
2. After every Anthropic API call, log the `usage` block: `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens`. A healthy cache shows `cache_read_input_tokens >> cache_creation_input_tokens` over time.
3. Build a daily cost dashboard: total input tokens, total cached read tokens (10% price), total cache write tokens (125% price for 5min, 200% for 1h), output tokens. Compute effective cost/call. If effective cost ≥ uncached cost, caching isn't working — investigate.
4. Set a circuit breaker on monthly Anthropic spend — when the sum exceeds $1.50 in a calendar month, alert and pause LLM ranking (fall back to pure audio-feature distance for Suggestions). $5/year = $0.42/month average; $1.50 is 3.5× that and a strong "something is broken" signal.
5. Verify cache hits in the test suite: run the LLM ranker twice in succession and assert the second call's `cache_read_input_tokens > 0`.

**Warning signs:**
- `cache_creation_input_tokens` in every response, `cache_read_input_tokens` always 0
- Anthropic billing chart shows steady spend with no caching discount
- Effective $/ranking-call is the same as uncached pricing

**Phase to address:**
Whichever phase introduces the LLM ranking pipeline. Both the `ttl: "1h"` config and the cache-hit logging must ship together — caching that "looks configured" but never hits is the most dangerous failure mode.

---

### Pitfall 10: LLM hallucinates track IDs / artist names — already burned in v1

**What goes wrong:**
v1's STATE.md decision log: *"Phase 04-02: Track ID validation filters LLM-hallucinated IDs before playlist assembly (T-04-03)"*. The LLM ranker, given a candidate list of 200 track IDs and asked to rank/pick, returns an ID that doesn't exist in the candidate list (or worse, an ID that exists in the library but wasn't a candidate). v2 amplifies this risk: with structured output (Instructor) and longer prompts, hallucination on IDs persists, and now those hallucinated tracks would silently land in user-facing vibe playlists.

**Why it happens:**
LLMs treat IDs as text, not foreign keys. With prompt caching, the model may also pull from the cached prefix — recall a track from a previous turn rather than the current candidate set.

**How to avoid:**
1. **Always validate** — every LLM-returned track ID must round-trip against the candidate set you sent: `if returned_id not in candidate_id_set: drop or retry`.
2. Instructor's `max_retries` parameter triggers a re-prompt on validation failure — use a Pydantic validator that asserts membership: `@validator('track_id') def must_be_candidate(cls, v): assert v in candidates_context.get(); return v`.
3. Prefer **integer indices over string IDs** in the prompt: send candidates numbered 0..N, ask the LLM to return indices, then map back to ratingKeys server-side. Smaller token footprint, near-zero hallucination on integers.
4. If the LLM is asked to recommend artists (Lidarr discovery), validate against MusicBrainz before showing. An LLM-invented artist ("The Velvet Echoes from Brooklyn") with a real-sounding name will mislead the user; MusicBrainz lookup via Lidarr's search proves it exists.
5. Log every validation failure with the prompt and response — accumulate a corpus to tune the prompt against.

**Warning signs:**
- Validation drop rate > 5% across LLM calls — prompt is leading the model astray
- User reports "this track isn't in my library" appearing in a vibe playlist
- Lidarr `add artist` returns 404 because the artist doesn't exist in MusicBrainz

**Phase to address:**
Already in place from v1 Phase 4. Continue the convention in v2 phases that introduce LLM ranking and Lidarr discovery. Codify it: any LLM-returned identifier must pass a registry check before reaching user-facing state.

---

### Pitfall 11: Cost runaway from a bug that triggers refresh on every request

**What goes wrong:**
A frontend dev change adds an `hx-trigger="every 5s"` to a Suggestions panel for "live update" feel. Every 5s, an HTMX request hits an endpoint that calls the LLM ranker because of a missed cache check. 12 calls/minute × 60 min × 24 hr = 17,280 LLM calls per day vs. the budget of maybe 5–20. By the time the user notices, monthly spend is 100×–1000× target. There's no enforcement layer — the budget is a hope, not a constraint.

**Why it happens:**
"It's only LLM cost when an event fires" relies on every code path respecting the event-driven contract. One bug or one misplaced refresh defeats the design. v1 didn't enforce cost caps because v1 didn't have refresh-on-event semantics.

**How to avoid:**
1. **Daily quota** in code: `MAX_LLM_CALLS_PER_DAY = 50` (well above expected, well below catastrophic). Increment a counter in SQLite per call; refuse with HTTP 503 when exceeded; reset at UTC midnight.
2. **Burst limit**: max 5 LLM calls in any 60-second window. Implements as a sliding-window counter. Catches the every-5s-refresh bug in <30 seconds.
3. **Per-event de-bounce**: if the same `(track_id, event_type)` triggered an LLM call in the last 60 seconds, return cached result. Prevents Plex's webhook retries from compounding cost.
4. **Cost dashboard in settings**: "Anthropic spend this month: $0.18 / $0.42 budgeted". User sees the runaway before the bill arrives.
5. Instrument the ranker call site with a structured log: `{event:"llm_call", reason:"vibe_slot_in", track_id:..., cost_estimate_usd:0.0023}`. Alerts can be built off this later but day-1 the JSON is enough to grep.

**Warning signs:**
- Daily LLM call count > 100 (10× normal)
- Burst limit triggers more than once per day (something is loop-calling)
- `cost_estimate_usd` daily sum > $0.05

**Phase to address:**
The phase that introduces LLM ranking. All four mechanisms (daily quota, burst limit, per-event debounce, dashboard) must ship together. Adding them after a runaway has occurred means refunding spend with no clean rollback.

---

### Pitfall 12: Suggestions queue and vibes recommend the same tracks — visible duplication across surfaces

**What goes wrong:**
The Suggestions queue refills with 30 unrated tracks ranked by overall taste fit. The vibe playlists also auto-slot newly-rated tracks. A track that lives in both "Suggestions" and "Workout Vibes" is fine — but a track recommended in Suggestions today and recommended again in Suggestions tomorrow (because it wasn't consumed) feels like the system is broken. Worse: if Lidarr discovery surfaces an artist whose tracks Composer is also recommending, the user sees overlap and loses trust.

**Why it happens:**
Each recommendation surface (Suggestions queue, vibe slot-in, Lidarr discovery) implements its own ranking with no cross-surface deduplication.

**How to avoid:**
1. Maintain a `track_recommendation_history` table: `(track_id, surface, recommended_at)`. Every Suggestion drain or vibe slot-in writes a row.
2. Suggestions ranking excludes any track recommended in the last N days (e.g., 14 days) **unless** rating signal changed. Adds variety naturally.
3. Vibe slot-in is fine to always run — it's user-initiated by their rating, not Composer-initiated discovery.
4. Lidarr discovery filters out artists with any track already in `composer.tracks` (in library) or in `lidarr.artists` (already managed). Hit Lidarr's `/api/v1/artist` first to get the managed list, cache for 1 hour.
5. For LLM-driven Suggestions ranking, include `recently_recommended_track_ids` in the prompt context with explicit "don't recommend these again" instruction.

**Warning signs:**
- Same track appears in Suggestions queue refill 3 days in a row
- Lidarr discovery recommends an artist already in the library
- User mentions "you keep showing me the same stuff"

**Phase to address:**
The Suggestions queue phase establishes the dedup table; Lidarr discovery phase consumes it.

---

### Pitfall 13: Lidarr LLM discovery has a mainstream popularity bias — recommends Coldplay

**What goes wrong:**
Asked "given my taste in obscure UK garage, recommend 5 artists I should add", an LLM defaults to popular adjacent artists (Burial *and* Skrillex *and* Disclosure) because mainstream artists dominate its training corpus. Recent research confirms: LLMs as recommenders show moderate but real popularity bias, less severe than collaborative filtering but still present, and **worse in cold-start** (when the model has thin signal from the user). A user with 460 rated tracks is barely past cold-start. The "discovery" feature feels generic — the user's reaction is "I already know about Burial" — eroding trust within the first session.

**Why it happens:**
LLMs memorize their training data. Popular artists appear more often in training, so they're more likely to be returned. Cold-start makes this worse — when the model has less to anchor on, it falls back harder to its priors.

**How to avoid:**
1. **Anchor on adjacency, not similarity.** Prompt with a small, specific seed set: "User likes [3 specific tracks from underrepresented genres in their library]. Suggest 5 artists who collaborated with, were sampled by, or run labels with these artists." The narrower question reduces the model's degrees of freedom.
2. **Filter out mainstream candidates**: query MusicBrainz for the recommended artist, get listener/popularity rank if available, drop top-N% globally popular unless the user's library explicitly contains comparable popularity-tier artists.
3. **Diversity requirement in prompt**: explicit instruction "do not recommend artists with >X million Spotify listeners" or "prefer artists whose top track has <500K plays". Doesn't always work but pushes the distribution.
4. **Use library similarity as a hard gate**: an LLM-recommended artist must have at least one MusicBrainz "similar artist" in the user's library, OR share a label/release-group with a starred artist. Computed via MusicBrainz API (free, no rate-limit issues at this scale).
5. **Show provenance**: "Suggested because you starred Four Tet — same label". Makes the bias visible and lets the user dismiss bad ones.

**Warning signs:**
- Recommendations skew to top-100 most-listened artists globally
- User dismisses >50% of suggestions
- Same handful of "safe" adjacent artists keep appearing across sessions

**Phase to address:**
The Lidarr discovery phase. Diversity guardrails and the popularity filter must be in the first version of the discovery prompt — adding them later means retraining user trust.

---

### Pitfall 14: Lidarr quality profile mismatch — recommended artist available only in low quality

**What goes wrong:**
User has set Lidarr to "FLAC Preferred" quality profile. Composer recommends an artist whose entire discography is only available as 320kbps MP3 from indexers. Lidarr accepts the artist add but never finds an acceptable release, so the artist sits forever as "wanted but not downloaded." User sees "Added to Lidarr!" success in Composer, opens Lidarr later, finds nothing. Trust in the discovery feature collapses.

**Why it happens:**
Composer's add-artist flow doesn't preflight against the configured quality profile or the indexer landscape. Lidarr accepts an add silently and the failure mode is asynchronous and out-of-band.

**How to avoid:**
1. **Surface the quality profile during discovery**: "Adding to Lidarr (FLAC Preferred). Some niche artists may not be available at this quality." Upfront expectation-setting prevents silent failure.
2. **Optional preflight**: query Lidarr's `lookup` endpoint for the artist; if available release count is low, warn the user.
3. **Post-add monitoring**: 24 hours after a Composer-initiated add, check if Lidarr has imported any tracks. If not, surface in the UI: "Velvet Echoes added 2 days ago, no releases found yet — change quality profile?"
4. **Honor the existing v1 pattern from Lidarr connection-test bugs notes**: Lidarr's `metadataProfileId` is required alongside `qualityProfileId` for artist add. Both must be fetched from the user's Lidarr instance dynamically — don't hardcode IDs.
5. **Don't auto-add** — keep the v1 "one-click add, but user-initiated" pattern. Auto-adding amplifies all of these failure modes.

**Warning signs:**
- Lidarr "wanted but no releases" count grows over time
- User reports "I added this artist 2 weeks ago, still nothing"
- Lidarr API returns success on add but the artist doesn't progress to "downloaded"

**Phase to address:**
The Lidarr discovery phase. Preflight check and post-add monitoring can be deferred but the upfront warning must ship with the first version of the feature.

---

### Pitfall 15: HTMX swap targets don't exist on initial page load (Alpine `$dispatch` issue from STATE.md)

**What goes wrong:**
v1 hit this exact bug per STATE.md context (*"Alpine $dispatch issue"*). v2 amplifies it: a mobile-first portrait UI with HTMX-swapped vibe cards, where Alpine components inside swapped fragments lose state, custom events `$dispatch`'d from one card don't reach a sibling that was swapped after, and the bottom-nav state lives in an Alpine root that gets clobbered when a partial replaces it.

**Why it happens:**
HTMX swaps replace the DOM at the swap target, including any Alpine components inside. Alpine reactive bindings reference the old DOM node — `$dispatch` from a swapped component goes to a node that doesn't exist; listeners on the new component aren't wired to the old emitter.

**How to avoid:**
1. **Use the `alpine-morph` HTMX extension** — it preserves Alpine `x-data` state across swaps by morphing rather than replacing the DOM. This is the standard solution and is mature (v1 didn't have it, v2 should adopt it project-wide). Add as `<body hx-ext="alpine-morph">` and per-element `hx-swap="morph"`.
2. **Hoist global Alpine state to the page root**, not inside swap targets. Bottom nav's open/closed state, modal visibility, theme — these all live on `<body x-data="globalState()">`.
3. **Use HTMX events instead of Alpine `$dispatch` for cross-component communication**: emit `htmx:trigger` from server responses (`HX-Trigger` header) and listen with `hx-trigger="event-name from:body"`. HTMX events bubble to body and survive swaps.
4. **Re-init Alpine after swap** if morph isn't viable for a specific case: `htmx.on("htmx:afterSwap", (e) => Alpine.initTree(e.detail.target))`. But morph is cleaner.
5. **Test every swap interaction on a real iPhone** — Alpine + HTMX bugs hide in desktop dev because Chrome forgives much more than mobile Safari does.

**Warning signs:**
- Alpine state resets when a sibling card is updated via HTMX
- Custom events fire but no listener responds
- Bottom-nav flickers or loses its highlighted state on tab change

**Phase to address:**
The first v2 UI phase. Adopt `alpine-morph` from the first template, document the convention in CLAUDE.md, and don't ship `hx-swap="innerHTML"` without explicit reason.

---

### Pitfall 16: iOS Safari `100vh` includes the bottom toolbar — content hides under it

**What goes wrong:**
v2 is mobile-first, which means iOS Safari is the primary target. `100vh` in iOS Safari refers to the **expanded viewport** including the bottom toolbar. A bottom-nav bar styled `position: fixed; bottom: 0` ends up partially hidden behind Safari's tab bar. The Suggestions queue scrollable area set to `height: 100vh` extends below the toolbar, with the last 60-80px of content unreachable. Tapping near the bottom of the screen accidentally summons Safari's UI rather than activating a button. Tested on Chrome desktop: looks perfect.

**Why it happens:**
Apple's deliberate decision: `100vh` returns the largest viewport size to avoid layout reflows during scroll. The toolbar can collapse and expand — `100vh` was set when the toolbar was hidden, so when it's visible, content is occluded.

**How to avoid:**
1. **Use `100dvh` instead of `100vh`** for full-height layouts. `dvh` (dynamic viewport height) updates as the toolbar appears/disappears. Supported in iOS Safari 15.4+ (the user's iPhone almost certainly supports this). Fallback: `min-height: 100vh; min-height: 100dvh;` — older browsers use the first declaration, modern use the second.
2. **Use `safe-area-inset-bottom`** for any fixed-bottom UI: `padding-bottom: env(safe-area-inset-bottom)`. Adds the toolbar/notch height as padding so content doesn't hide under it.
3. **Don't put critical interactive elements within 60px of the viewport bottom**. Bottom nav itself yes (with safe-area-inset), but action buttons should sit further up.
4. **Test on real iPhone** in both Safari (always shows toolbar near bottom) and Plexamp's web view if anyone uses that. Browser-stack and Chrome dev-tools mobile emulation lie about this.
5. **Add `viewport-fit=cover`** to the `<meta viewport>` tag and use safe-area-inset everywhere. Without `cover`, safe-area-inset returns 0 even on notched devices.

**Warning signs:**
- Bottom nav buttons require two taps (first one summons Safari UI)
- Last item in a scrollable list is cut off
- Layout looks correct in landscape but wrong in portrait

**Phase to address:**
The first mobile-first UI phase. Establish `100dvh` and `safe-area-inset-bottom` as project conventions before any layout templates are written. Add to the project's CSS reset.

---

### Pitfall 17: Touch targets <44px cause accidental rate / skip taps

**What goes wrong:**
v1's UI-SPEC notes "minimum 44px touch targets" but didn't enforce it in v1's chat UI. v2's Suggestions queue and vibe playlists have a "remove track" button next to a "play track" button next to a "rate track" thumbs control. On a 6.1" screen at portrait orientation, a 32px button surrounded by other 32px buttons gets misfired regularly — user means to play, hits remove. Discovery: a removed track gets added to the per-vibe exclusion list (Pitfall 5), and now Composer permanently won't slot it. Hard to undo without explanation.

**Why it happens:**
Designers eyeball spacing on desktop monitors with mouse pointers. Apple's HIG mandates 44pt minimum hit area, Android 48dp. Web doesn't enforce this and developers build to whatever looks visually balanced.

**How to avoid:**
1. **Enforce 44px in CSS as a project token**: `--touch-target-min: 44px;` and apply `min-width: var(--touch-target-min); min-height: var(--touch-target-min);` to every button and clickable.
2. **Padding, not visual size**: a 24px icon centered in 44px hit area looks fine and meets the requirement.
3. **Spacing**: minimum 8px between touch targets. Adjacent destructive and primary actions get 16px.
4. **Confirm destructive actions on mobile**: removing a track from a vibe shows a "Remove? [Cancel] [Remove]" sheet, not a one-tap delete. The "are you sure" annoys desktop users but saves mobile users from accidental loss.
5. **Undo for everything destructive**: a 5-second toast "Removed from Workout Vibes [Undo]" recovers from misfires.

**Warning signs:**
- User complaints about "I tapped wrong"
- High remove-then-re-add rate in event logs
- Bug reports of "tracks I want disappeared"

**Phase to address:**
The first mobile-first UI phase. Establish the touch-target token and the destructive-action confirm pattern in the project CSS before writing any v2 templates.

---

### Pitfall 18: Hover-only states don't translate to tap

**What goes wrong:**
A "track details" tooltip appears on hover on desktop. On mobile, there's no hover — the user taps the track to play it, and the details never appear. Useful information (energy score, why Composer chose this track) is invisible on the primary use surface.

**Why it happens:**
Design starts on desktop with hover affordances. Mobile is "tested" as "shrunk desktop" rather than designed-for. v1's mood-chat had this issue per `mobile-first.md`.

**How to avoid:**
1. **Replace hover with persistent disclosure**: an info chip on the card that's always visible on mobile, expandable inline on tap. No hover dependency.
2. **Press-and-hold / long-press** for secondary actions on mobile, same surface as right-click on desktop. iOS supports `touchstart` + timer for this; libraries like `pressed-detector` or Alpine's `@touchstart`/`@touchend` are sufficient.
3. **Persistent state** for selection/active: tapping a track expands its details inline; tap again or tap a different track to collapse. No transient hover-style states.
4. **Test with Chrome dev-tools "touch" emulation early**, then on a real device. Don't ship hover-dependent UX without a tap-equivalent path.

**Warning signs:**
- Information visible in desktop screenshots that doesn't appear on mobile screenshots
- Designer mockups assume tooltips that aren't implementable on mobile
- User asks "how do I see why this was suggested?"

**Phase to address:**
The first mobile-first UI phase. Treat hover as "convenience for desktop" never as "primary information channel."

---

### Pitfall 19: SQLite schema migrations on container restart — silent data loss or startup failure

**What goes wrong:**
v1's SQLite schema is in production on the user's NAS, with 460 ratings, ~10k tracks, audio features for each. v2 needs new tables (vibes, vibe_membership, suggestion_queue, processed_webhooks, recommendation_history) and new columns on existing tables (e.g., `tracks.user_rating`, `tracks.last_viewed_at`). Naive `SQLModel.metadata.create_all()` only creates **missing** tables — it does **not** add new columns to existing tables. The user pulls the v2 image, restarts the container, app boots, but `SELECT user_rating FROM tracks` fails. Or worse: silently uses null defaults for everything, the rating sync runs but writes nothing.

**Why it happens:**
v1 used `create_all()` which is fine for fresh installs (the only context v1 was tested in). v2 is the first time the existing user has a real schema upgrade.

**How to avoid:**
1. **Adopt Alembic** for schema migrations. SQLModel uses SQLAlchemy under the hood, so Alembic integrates directly. Generate migrations with `alembic revision --autogenerate -m "v2 vibe and webhook tables"`, hand-edit to verify.
2. **Use `batch_alter_table`** for SQLite — SQLite has limited `ALTER TABLE` (no drop column pre-3.35, no rename column pre-3.25). Alembic's batch mode creates a new table, copies, drops old, renames. `batch_alter_table` is safe to call always — it no-ops on backends that don't need it.
3. **Run migrations on container startup** before app accepts requests. In `lifespan` context manager: `alembic upgrade head` then start FastAPI. If migration fails, container exits with a clear error rather than booting in a broken state.
4. **Backup before migration**: on detected schema upgrade, copy `data.db` to `data.db.v1backup` before running migrations. User can roll back by stopping the container and restoring the file.
5. **Idempotent migrations**: every migration must be re-runnable. Use `IF NOT EXISTS` patterns where possible.
6. **Test the upgrade path end-to-end** with a v1 database snapshot. Don't ship until "v1 db → v2 container starts → all data intact" passes in CI.

**Warning signs:**
- App boots but `SELECT` against a new column returns "no such column"
- v2 ratings sync runs to completion but the database has no rated tracks
- User reports "all my data is gone" after upgrade (it's actually still in the file but unreachable)

**Phase to address:**
The first phase that introduces a schema change (likely the rating sync phase). Alembic and the migration-on-startup pattern must ship in that phase.

---

### Pitfall 20: v1-generated Plex playlists in user's library — Composer mistreats them

**What goes wrong:**
The user ran v1's mood-chat for weeks and pushed several playlists to Plex ("Sunday morning coffee", "Workout 2026", etc.). Those playlists exist in Plex under the user's account. v2 retires the mood-chat UI and introduces vibe playlists. Composer scans Plex playlists and either: (a) treats v1 generated playlists as user-curated and analyzes them as taste signal (skewing the rated-set model with whatever the user generated months ago), (b) tries to "manage" them (Pitfall 5 territory — Composer has no record they exist in `composer_managed_playlists`), or (c) deletes them as orphans during cleanup.

**Why it happens:**
v1 didn't tag generated playlists distinctively. Composer can't tell the difference between a v1-generated "Sunday morning" and a user-created "Sunday morning."

**How to avoid:**
1. **PROJECT.md hands-off principle**: Composer manages only its own playlists. Existing playlists — including v1-generated ones — stay untouched. Composer never reads or analyzes playlists it doesn't own.
2. **Naming convention for v2-owned playlists**: prefix with a marker like `🎵 ` or a hidden zero-width character, AND record the playlist key in `composer_managed_playlists`. Both must match for Composer to consider a playlist its own.
3. **Migration handling**: on first v2 boot with v1 history, scan Plex for playlists matching v1's naming pattern (whatever it was) and offer the user a one-time choice: "Keep these (Composer leaves alone) / Adopt as vibes (Composer takes over) / Delete." Default to keep.
4. **Don't auto-import v1 chat history into v2's vibe model**. v1 chat data (mood descriptions and resulting playlists) is interesting for a "history" view, but not as taste signal — the user's ratings are the v2 truth.
5. **Document the migration path** in release notes: "v1-generated playlists are not modified by v2. To convert one to a vibe, ..."

**Warning signs:**
- Composer modifies a playlist the user created in Plexamp
- v1 playlists disappear after upgrade
- Vibe model includes tracks from v1 generated playlists that the user didn't actually rate

**Phase to address:**
The migration / first-run setup phase. The hands-off rule must be enforced from the first time v2 reads Plex playlists.

---

### Pitfall 21: Polling fallback hits Plex API rate limits and drifts from real-time

**What goes wrong:**
User doesn't have Plex Pass, so webhooks aren't available. Composer falls back to polling Plex for `userRating` and `lastViewedAt` changes. Naive implementation: every 30 seconds, fetch all tracks and diff. With 10k tracks and Plex's pagination this is hundreds of API calls per poll. After an hour the Plex server logs show Composer hammering it; in some Plex versions, the server starts throttling, polls take longer than 30 seconds, polls overlap, drift increases, eventually a poll fails and the next poll re-fetches everything.

**Why it happens:**
"Polling" sounds simple — fetch all changed items every interval. But the Plex API has no "give me everything changed since timestamp T" endpoint for music libraries that's both reliable and efficient. Developers reach for full-library scans because they're familiar.

**How to avoid:**
1. **Only poll the recently-played view + the recently-rated section**, not the full library. Plex's `/library/sections/{id}/all?addedAt>>=` and recently-viewed views are bounded.
2. **Poll interval scales with library size**: 30s for testing, 2-5 minutes for production. Real-time isn't the goal of the fallback — eventual consistency is.
3. **Track a server-side "last poll timestamp"** and request only deltas. If the API doesn't support a clean delta query, paginate through recently-rated and recently-played, stop when items predate the last seen timestamp.
4. **Coalesce polls with webhooks** if both are configured. Webhook events suppress the next polling cycle.
5. **Back off on API errors**: 503/429 → exponential backoff up to 30 minutes. Never fire continuous failed requests.
6. **Document the latency expectation**: "Without Plex Pass, rating changes appear in Composer within 5 minutes." Sets user expectation correctly.

**Warning signs:**
- Plex server logs show Composer-User-Agent dominating request volume
- Polling takes longer than the polling interval (overlap)
- Poll log shows full library fetched every cycle instead of a small delta

**Phase to address:**
The phase that introduces polling fallback. Bounded queries, scaled interval, and back-off must all ship together.

---

### Pitfall 22: Settings encryption key rotation breaks credential decryption silently

**What goes wrong:**
v1 stores Plex token, Lidarr API key, Anthropic API key encrypted at rest in SQLite. The encryption key is derived from an env var (or app secret). v2 changes the encryption format (e.g., moves from Fernet to AES-GCM, or rotates the key derivation). On v2 upgrade, the existing ciphertext in `settings.encrypted_value` no longer decrypts. App boots, settings page shows "Plex: Not Configured", user has to re-enter every credential. Worse: if the failure is silent (decryption returns None instead of raising), the app appears to work but every API call fails with "no token configured."

**Why it happens:**
v1 didn't explicitly version its encryption format. Any upgrade that changes the format silently invalidates existing data.

**How to avoid:**
1. **Don't change the encryption format in v2** unless required. v1's pattern works — keep it.
2. If format must change, **version the ciphertext**: store as `v1:base64(...)` or `v2:base64(...)`. Decrypt path tries the version prefix first; on miss, attempt v1 format and re-encrypt with v2.
3. **Fail loudly on decryption errors**: raise an exception with "encrypted credential could not be decrypted — re-enter in settings". Don't return None and let the app boot in a broken state.
4. **Test the upgrade path**: copy a v1 settings DB into the v2 dev environment, boot, verify all credentials decrypt correctly.
5. **Document the recovery path** for users: "If credentials don't decrypt after upgrade, go to settings and re-enter."

**Warning signs:**
- Settings page shows everything as not configured after upgrade
- API calls fail with "no token" even though settings appear configured
- Logs show silent `Decryption failed` without surfacing to the user

**Phase to address:**
The migration / upgrade phase. Avoid format changes; if necessary, add versioning before any change ships.

---

### Pitfall 23: Race between simultaneous rating-change events for same track

**What goes wrong:**
The user rates a track via Plexamp on phone. They tap the wrong rating (3 stars instead of 4), tap again to fix, and Plexamp sends two `media.rate` events 200ms apart. Both webhooks arrive almost simultaneously. Composer's slot-into-vibe logic runs twice: first thread fetches centroid distances based on rating=3, second thread fetches with rating=4. They race on writing to vibe membership. Final state is ambiguous — could be slotted into the wrong vibe, slotted twice, or rolled back.

**Why it happens:**
v1 had no concurrent rating events (no rating sync). v2's webhook architecture introduces concurrency for the first time. SQLite handles multiple writers but not via "last writer wins" the way developers expect — without explicit locking, two simultaneous transactions can both commit if they touch different rows, leaving inconsistent state.

**How to avoid:**
1. **Per-track lock** at the application level: `asyncio.Lock` keyed by ratingKey, held for the duration of slot-in processing. Concurrent events for the same track serialize.
2. **Always read latest rating from Plex inside the lock**, not from the webhook payload. The first event writes rating=3 to the DB; the second event re-reads from Plex (which has the final rating=4 by now) and processes the corrected value. The first event's slot-in might be wasted work, but state is consistent.
3. **Use SQLite `BEGIN IMMEDIATE`** for transactions that modify vibe membership — fails fast on concurrent writes rather than producing inconsistent state.
4. **Idempotency from Pitfall 1**: dedupe on `(ratingKey, userRating)`. The second event's payload has the new rating; the dedupe key is different from the first event; both process. But the per-track lock serializes them.
5. **Debounce**: after a rating event, wait 500ms before processing. If another event arrives for the same track during the debounce, take the latest. Trades latency for correctness.

**Warning signs:**
- Same track in two vibes after a rapid rate-correct sequence
- Vibe membership flickers (track added then immediately removed)
- Logs show concurrent slot-in calls for the same ratingKey

**Phase to address:**
The vibe slot-in phase. Locks and the latest-read pattern must be in the first slot-in implementation.

---

### Pitfall 24: Multi-vibe membership — ambiguous track placement

**What goes wrong:**
A track scores 0.82 distance to "Workout Vibes" centroid and 0.79 distance to "Late Night Drives" centroid (lower is closer). Hard-assignment slots it into Late Night Drives. The user thinks "but this is also a workout track" — Composer's UX says the track has *one* vibe, the user thinks it has multiple. Soft-assignment puts it in both — but now the same track appears in two playlists in Plexamp, which is confusing the other direction. Eight months in, the user has 200 tracks visible in 2-3 vibes each, and the feature feels muddy.

**Why it happens:**
Music doesn't cluster cleanly. Real listening sessions cross vibes. Hard-assigned clustering enforces a clean boundary that doesn't match the user's mental model; soft-assignment creates duplication.

**How to avoid:**
1. **Hard-assign by default with a configurable margin**: track goes to closest vibe only if distance to second-closest is >X% greater. Otherwise the track is "ambient" — fits everywhere, slotted nowhere.
2. **Show vibe affinity in the track detail view**: "Best fit: Late Night Drives (82%) · Also matches: Workout (79%)". User sees the multi-fit without it being expressed in playlist membership.
3. **Allow user override**: long-press a track in a vibe to "also add to Workout Vibes" — explicit user intent overrides automatic clustering. Persist the override in `track_vibe_overrides`.
4. **Make the "not in any vibe" case visible**: a "Unsorted Vibes" section in the home screen lists tracks that didn't slot anywhere — gives the user a queue to review.
5. **Document the model**: in setup wizard help text, "Each track lives in one vibe. You can add a track to multiple vibes manually, but Composer assigns to one by default."

**Warning signs:**
- User confusion about why a track is in one vibe but not another
- High user-override rate (>20% of slotted tracks get manually moved)
- Vibe playlists feel "wrong" — too many edge-case tracks

**Phase to address:**
The vibe slot-in phase. The hard-assign + margin pattern is simpler and the better default; soft-assign is harder to UX.

---

### Pitfall 25: Lidarr-imported new tracks bypass Composer's analysis pipeline

**What goes wrong:**
Composer recommends an artist via Lidarr discovery. User adds the artist. Lidarr downloads the artist's discography over several days. Tracks land in the music folder, Plex picks them up on its next library scan, Plex shows them. But Composer's local DB doesn't know about them — its sync ran before the import — so Essentia hasn't analyzed them, vibes don't include them, Suggestions queue can't recommend them. The user notices "I added this artist a week ago and Composer never shows their tracks" — the loop is broken.

**Why it happens:**
v1's library sync runs on APScheduler (configurable interval, default 6/12/24 hours per Phase 02 decisions). New tracks are picked up eventually but not promptly. The Essentia analysis depends on sync running first. The analysis pipeline isn't event-driven on Lidarr import.

**How to avoid:**
1. **Listen to Lidarr's webhook** (Lidarr supports webhooks for `Download` and `OnImport` events). When a Lidarr import completes, trigger a Composer sync of the affected library section, then trigger Essentia analysis on the new tracks.
2. **Listen to Plex `library.new` webhook** (Plex Pass): same effect, downstream of Plex's library scan.
3. **Faster post-discovery polling**: when Composer adds an artist via Lidarr, schedule an aggressive sync check every 10 minutes for the next 24 hours, then back off. Catches the import without waiting for the regular sync interval.
4. **Surface progress in the UI**: "Velvet Echoes added — Lidarr is downloading. Composer will analyze and recommend tracks once they arrive." User has visibility into the multi-step pipeline.
5. **Idempotent analysis pipeline**: if a track gets seen twice (sync re-runs after Lidarr import), Essentia analysis is keyed on track ID and already-analyzed tracks are skipped — already true per v1 Phase 03 (audio features cached permanently in SQLite).

**Warning signs:**
- New Lidarr artists' tracks don't appear in Suggestions for >24 hours
- User reports "I added this artist days ago, where are the tracks?"
- Analysis queue stays empty even though new files exist on disk

**Phase to address:**
The Lidarr discovery phase, OR a dedicated "auto-ingest" phase. The Lidarr webhook listener and the post-discovery polling must coexist for resilience.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems. v2-specific shortcuts beyond v1's list.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Skip webhook dedup, "we'll add it later" | Faster initial implementation | Duplicate slot-ins, inflated LLM cost, corrupted vibe membership | Never — INSERT OR IGNORE is a 5-line addition |
| Use `create_all()` for v2 schema instead of Alembic | No migration tooling overhead | Existing user's data inaccessible after upgrade | Never — first user upgrade IS the migration |
| Re-fetch Plex track on every webhook to get "fresh" data | Feels safer than trusting payload | Race condition (Pitfall 7), 2× API load | Only when payload genuinely lacks needed fields |
| Hard-code `ttl: "5m"` (default) for Anthropic prompt caching | Matches docs example | March 2026 default change inflates cost vs. 1h | Never for production — always specify ttl explicitly |
| Skip cluster-quality threshold (silhouette > 0.25) | Always produce vibes regardless of data | Bad clusters → bad slot-ins → user trust collapses | Never — threshold check is one line |
| Run PlexAPI calls inside `async def` handlers | Faster to write | Event loop stalls, user-visible freezes | Never — `to_thread` or sync handler |
| Auto-import v1 playlists as taste signal | "Don't lose user's history" | Skews vibe model with stale generated playlists | Never — ratings are the only taste signal in v2 |
| Soft-assign tracks to multiple vibes by default | Models reality more accurately | UX confusion, duplicated playlist content | Only when the UI is explicitly designed for it |
| Treat `userRating` as 0-5 integer | Matches what users see | Loses half-star info, off-by-2 display bugs | Never — divide-by-2 helper is trivial |
| Hover-only desktop UX with "we'll add tap fallback later" | Faster initial implementation | Mobile users miss critical info | Never — mobile-first means designing for tap first |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Plex webhooks | Parsing as JSON body | Parse as multipart/form-data; the `payload` field contains JSON; ignore the `thumb` field |
| Plex webhooks | Returning 500 on processing error | Return 200 always (idempotent dedup handles real duplicates); log errors separately |
| Plex webhooks | Trusting webhook payload's user/owner identity | Webhooks are tied to a Plex account; verify the `user.id` matches the configured account |
| Plex `userRating` | Treating as 0-5 scale | Plex stores 0-10; divide by 2 for stars; preserve raw value for logic |
| Plex playlists | Re-creating vibe playlist on every push | Add/remove/reorder operations on existing playlist; only create on first run |
| Plex playlists | Composer overwrites user edits | Source-of-truth is Plex playlist; compute additive deltas only |
| PlexAPI | Calling sync methods inside `async def` | Wrap with `await asyncio.to_thread(...)` or use sync `def` handlers |
| Anthropic API | Setting `cache_control` without `ttl` | Default is 5 minutes; specify `ttl: "1h"` for steady-state event-driven workloads |
| Anthropic API | Trusting `cache_control` is set without verifying hits | Log `cache_read_input_tokens` from every response; alert on 0 |
| Anthropic API | LLM-returned IDs trusted blindly | Validate against candidate set; use indices not strings |
| Lidarr API | Hardcoding quality/metadata profile IDs | Fetch dynamically from `/api/v1/qualityprofile` and `/api/v1/metadataprofile` |
| Lidarr API | Treating add-artist 200 as "success, downloaded" | Add returns success once accepted; download is async; monitor for 24h |
| MusicBrainz | Treating LLM artist name as canonical | Validate via MusicBrainz lookup before showing to user |
| HTMX + Alpine | Naive `hx-swap` clobbers Alpine state | Use `alpine-morph` extension; hoist global state to body |
| iOS Safari | `100vh` for full-height layout | Use `100dvh`; add `safe-area-inset-bottom` for fixed bottom UI |

## Performance Traps

Patterns that work at small scale but fail as v2 usage grows. v1 covered the broad strokes; these are v2-specific.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Synchronous PlexAPI call in async webhook handler | Health check slows during listening sessions | `to_thread` or sync `def` handler; semaphore-cap concurrency | Any concurrent webhook load >2 events/sec |
| Polling Plex full library every 30s | Plex server CPU spikes; Composer drifts | Bounded delta queries; 2-5 min interval | 1000+ tracks |
| LLM call per Suggestions panel render | Cost runaway; latency spike | Per-event debounce + daily quota + UI shows cached state | Any auto-refresh trigger |
| K-means re-clustering on every rating event | CPU spike on NAS; vibe membership churns | User-requested only; threshold-based prompt | 50+ ratings/day |
| Per-track Plex re-fetch on every webhook | 2× API load; staleness race | Trust webhook payload | High-frequency listening |
| Anthropic prompt cache with default 5min TTL on event-driven calls | Cost equivalent to no caching | `ttl: "1h"` explicitly; verify cache_read_tokens | Calls spaced > 5min apart (the v2 norm) |
| Loading entire `tracks` table to compute vibe centroid distances | Memory spike; slow ranking | Pre-compute and cache centroid embeddings; use indexed feature columns | 10k+ tracks |
| HTMX swap with default `innerHTML` blowing away Alpine state in long-running views | UI flickers; nav state lost | `alpine-morph` extension project-wide | Any swap inside an Alpine component |

## Security Mistakes

v2-specific risks beyond v1's existing security pitfalls.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Webhook endpoint accepts unauthenticated POSTs | Anyone on the LAN/Tailscale can fake rating events | Use a per-deployment webhook secret in path or header; reject mismatched |
| Webhook endpoint exposed on the public internet | Replay attacks; spam | Tailscale-only access (already the deployment model); document this |
| Anthropic API key included in error logs from cost-runaway events | Key exposure if logs are shared | Mask `Authorization` headers in all log middleware; verify by grep before any log share |
| New v2 settings UI returns vibe centroid embeddings | Reveals taste model in API responses (low risk but unnecessary) | Don't return computed model state in API responses; only render server-side |
| Plex token cached in webhook payload signature | Token in deduplication keys | Hash payload fields, not raw token; never persist tokens outside the encrypted settings table |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| First-run wizard requires 30 ratings before vibes work | User opens app, gets nothing, bounces | Show what *will* work; offer "rate-as-you-go" mode that builds vibes incrementally with current best k |
| Vibe names auto-generated from feature ranges ("Energy 0.6–0.8, Tempo 100–120 BPM") | Meaningless to users | LLM-suggested names ("Late Night Drives", "Workout Vibes") with user edit; never expose feature numbers |
| Suggestions queue refills silently when track is consumed | User doesn't know it's working | Subtle indicator: "12 new suggestions" or fade-in animation when the queue refreshes |
| Lidarr "added!" message with no follow-up | User loses track of what they added | Persistent "Recently added to Lidarr" list; show download status if available |
| LLM ranking takes 3-5 seconds with no feedback | Feels broken | Optimistic UI: show ranked-by-distance immediately, swap in LLM-ranked result when ready |
| Hover-only "why was this suggested?" details | Mobile users never see the rationale | Persistent rationale chip below each suggestion |
| Settings page changes apply only after restart | User changes a setting, nothing happens | Apply immediately; show "applied" toast; restart only for profile-level changes |
| Vibe playlist push errors silently | User sees old playlist in Plexamp | Surface push status: "Last updated 2 minutes ago" / "Failed — retry?" |
| Cost dashboard hidden in advanced settings | User can't preempt cost runaway | Show on home screen if monthly spend > 50% of budget |

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces. v2-specific.

- [ ] **Webhook handler:** Returns 200 in <500ms — verify with synthetic load (50 webhooks in 5 seconds)
- [ ] **Webhook dedup:** Same event delivered 3 times only processes once — verify with replay test
- [ ] **userRating display:** A track rated 4.5 stars in Plexamp shows 4.5 stars in Composer (not 9, not 4, not 5)
- [ ] **PlexAPI in async handler:** Health check stays <200ms during a listening session — verify with a 5-minute play test
- [ ] **Vibe clustering:** Refuses to cluster <30 tracks; defaults to k=3 if silhouette <0.25
- [ ] **Vibe playlist edit:** User-removed tracks stay removed across the next 5 slot-in events
- [ ] **Plex playlist push:** Stale ratingKeys are detected and logged; playlist count matches expected after push
- [ ] **Anthropic prompt cache:** `cache_read_input_tokens > 0` on the second LLM call within an hour — verify in tests
- [ ] **LLM hallucination:** Returned track IDs always exist in the candidate set sent to the model — Pydantic validator enforced
- [ ] **Cost circuit breaker:** Daily quota stops processing at threshold; user sees warning rather than runaway bill
- [ ] **Lidarr add:** quality/metadata profile IDs fetched dynamically; not hardcoded
- [ ] **Lidarr discovery:** Already-managed artists filtered out of recommendations
- [ ] **Mobile touch targets:** Every clickable is ≥44px in any direction — verify with Chrome dev-tools touch test
- [ ] **Mobile viewport:** Bottom nav visible on iOS Safari with toolbar showing — verify on real iPhone
- [ ] **Schema migration:** v1 database file boots cleanly on v2 container — verify in CI with snapshot
- [ ] **v1 playlists:** Existing user-created and v1-generated playlists are untouched after v2 first run
- [ ] **Encryption format:** v1 encrypted credentials decrypt under v2 — verify with v1 settings DB
- [ ] **Polling fallback:** Without webhook configured, rating changes appear within 5 minutes — verify by clearing webhook config and rating a track
- [ ] **Lidarr import → Composer ingest:** New tracks from a Lidarr-added artist appear in Suggestions within 24 hours

## Recovery Strategies

When pitfalls occur despite prevention.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Duplicate webhook events processed | LOW | Add INSERT OR IGNORE table; replay last 24h of events with idempotency to detect/clean |
| `userRating` displayed wrong (10 stars) | LOW | Add divide-by-2 helper; refresh UI; no data corruption |
| Cluster of all-same-vibe garbage | MEDIUM | Re-prompt user with setup wizard; re-run clustering with k=3 floor; preserve user-named vibes if any |
| Composer overwrote user's playlist edits | HIGH | If exclusion list unimplemented, recover from Plex backups (if user has any) or accept loss; implement exclusion list immediately |
| Stale ratingKeys in vibe playlists | MEDIUM | Re-resolve by (artist, title, album) + duration; mark unresolvable as accessible=False |
| LLM cost runaway detected | MEDIUM | Stop LLM calls (circuit breaker); investigate logs; Anthropic doesn't refund but daily cap prevents repeat |
| LLM hallucinated artist in Lidarr discovery | LOW | Validation already drops these; if a fake artist was added, Lidarr just won't find anything — remove via Lidarr UI |
| Mainstream-bias recommendations | LOW | Adjust prompt with explicit popularity exclusion; re-run discovery |
| Schema migration failed mid-flight | HIGH | Stop container; restore `data.db.v1backup`; investigate migration; reapply |
| v1 playlists modified by Composer | HIGH | Restore from Plex playlist backups; implement hands-off enforcement; surface in UI which playlists are managed |
| Encryption format incompatible after upgrade | MEDIUM | User re-enters credentials in settings; document recovery in release notes |
| Race condition on rating-change events corrupted vibe membership | MEDIUM | Re-run vibe slotting from scratch (rating signal is in Plex, recoverable); add per-track lock |
| Lidarr import didn't trigger Composer ingest | LOW | Manual sync button in settings; on next scheduled sync, Essentia analyzes the new tracks |
| iOS Safari layout broken (content under toolbar) | LOW | Hot-fix CSS to `100dvh` and `safe-area-inset-bottom`; ship as patch |
| HTMX swap clobbers Alpine state | LOW | Adopt `alpine-morph` project-wide; rebuild and ship |

## Pitfall-to-Phase Mapping

How phases should address these pitfalls. Phase numbering is suggestive — actual roadmap may differ.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| 1. Webhook duplicate events | Webhook ingest | Synthetic replay test: same event 3× → 1 processing |
| 2. `userRating` 0-10 vs 0-5 | Rating sync | Unit test: `userRating=7.0` → 3.5 stars displayed |
| 3. K-means on small/tight set | Vibe clustering | Test with n=20 (refuses), n=50 with similar tracks (warns), n=100 (passes) |
| 4. PlexAPI sync inside async | Webhook ingest | Load test: 30 webhooks/sec, p95 health < 200ms |
| 5. Vibe playlist edit conflicts | Vibe management | Manual test: edit a vibe in Plexamp → next slot-in respects edits |
| 6. Stale ratingKeys silent drop | Vibe management | Verify post-push playlist contents; log drift warnings |
| 7. `lastViewedAt` race condition | Consumption detection | Trust-payload pattern documented; re-fetch only with retry logic |
| 8. Scrobble noise in taste signal | Suggestions queue | Endorsement only from rating ≥3 stars; document in code comments |
| 9. Anthropic cache TTL regression | LLM ranking | Test: `ttl: "1h"` set explicitly; verify `cache_read_input_tokens > 0` after 30 min |
| 10. LLM hallucinates IDs | LLM ranking | Pydantic validator on returned IDs; log validation failures |
| 11. Cost runaway | LLM ranking | Daily quota + burst limit + cost dashboard before any production deploy |
| 12. Cross-surface duplicate recommendations | Suggestions queue | History table consulted before ranking |
| 13. Mainstream popularity bias | Lidarr discovery | Diversity prompt + popularity filter + provenance shown |
| 14. Quality profile mismatch | Lidarr discovery | Upfront warning in UI; post-add monitoring |
| 15. HTMX/Alpine swap target issues | First v2 UI | `alpine-morph` adopted project-wide; convention in CLAUDE.md |
| 16. iOS Safari `100vh` | First v2 UI | `100dvh` + `safe-area-inset-bottom` in CSS reset |
| 17. Touch targets <44px | First v2 UI | CSS token enforced; visual review on real iPhone |
| 18. Hover-only states | First v2 UI | Mobile-first design review for every component |
| 19. SQLite schema migration | First schema-changing phase | Alembic + batch_alter_table; v1 DB snapshot test in CI |
| 20. v1 playlists mistreated | Migration / first-run wizard | Hands-off enforcement; v1-detection one-time prompt |
| 21. Polling rate limits | Polling fallback | Bounded queries; 2-5 min interval; back-off on errors |
| 22. Encryption format breakage | Migration | Don't change format; if needed, version ciphertext |
| 23. Concurrent rating-change race | Vibe slot-in | Per-track lock; latest-read pattern |
| 24. Multi-vibe ambiguity | Vibe slot-in | Hard-assign + margin; UI shows multi-fit |
| 25. Lidarr-imported tracks bypass analysis | Lidarr discovery / auto-ingest | Lidarr webhook listener; post-discovery polling |

## Sources

### Plex
- [Plex Webhooks](https://support.plex.tv/articles/115002267687-webhooks/) — Plex Pass requirement, multipart/form-data, event types
- [Plex Forum: HTTP endpoint for star rating](https://forums.plex.tv/t/is-there-an-http-endpoint-for-changing-the-star-rating-of-a-track/926207/3) — userRating 0-10 scale, half-star support in Plexamp
- [Plex Forum: media.scrobble webhook](https://forums.plex.tv/t/media-scrobble-webhook/879128) — scrobble fires at ~50% played for music
- [Plex Forum: PlexAmp Rating System](https://forums.plex.tv/t/plexamp-rating-system-not-consistent/788399) — half-star ratings on iOS/Plexamp
- [Python PlexAPI Playlist Documentation](https://python-plexapi.readthedocs.io/en/stable/modules/playlist.html) — playlist add/remove/reorder methods
- [Plex Webhook Multipart Proxy](https://github.com/jfklingler/plex-webhook-proxy) — community workaround documenting multipart/form-data quirks

### Anthropic / LLM
- [Anthropic Prompt Caching Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — cache_control, ephemeral type, 1h TTL extension, verifying via usage object
- [Anthropic Cache TTL Regression (March 2026)](https://dev.to/whoffagents/anthropic-silently-dropped-prompt-cache-ttl-from-1-hour-to-5-minutes-16ao) — default TTL changed silently
- [Cache TTL silently regressed GitHub Issue #46829](https://github.com/anthropics/claude-code/issues/46829) — confirmed regression; cost inflation
- [LLMs as Recommender Systems: Popularity Bias (Amazon Science)](https://assets.amazon.science/a7/b5/145fc4734ee6abc2af4ce3b05943/large-language-models-as-recommender-systems-a-study-of-popularity-bias.pdf) — moderate popularity bias, less than CF, can be reduced via prompt
- [Cold-Start LLM Recommender Bias (arXiv 2508.20401)](https://arxiv.org/abs/2508.20401) — bias is worse in cold-start scenarios

### FastAPI / Async
- [FastAPI Concurrency and async/await](https://fastapi.tiangolo.com/async/) — sync `def` runs in threadpool; `async def` requires non-blocking calls
- [Run async ops on separate threads (FastAPI Discussion #6305)](https://github.com/fastapi/fastapi/discussions/6305) — patterns for sync libraries in async handlers
- [APScheduler User Guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html) — misfire grace, coalescing, persistent job stores

### Clustering
- [Selecting clusters with silhouette analysis (scikit-learn)](https://scikit-learn.org/stable/auto_examples/cluster/plot_kmeans_silhouette_analysis.html) — silhouette interpretation
- [silhouette_score docs](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.silhouette_score.html) — small-sample warnings
- [Silhouette (clustering) - Wikipedia](https://en.wikipedia.org/wiki/Silhouette_(clustering)) — 0.25/0.5/0.7 interpretation thresholds
- [How to Handle Outliers in K-Means](https://www.flyrank.com/blogs/ai-insights/how-to-handle-outliers-in-k-means-clustering) — outlier centroid skew; k-medoids and HDBSCAN as alternatives
- [HDBSCAN: Comparing Python Clustering Algorithms](https://hdbscan.readthedocs.io/en/latest/comparing_clustering_algorithms.html) — density-based clustering for variable-density data

### Webhook patterns
- [Webhook Best Practices: Idempotency and Event Ordering (BoldSign)](https://boldsign.com/blogs/webhook-best-practices-retries-idempotency/) — at-least-once delivery, dedup keys
- [Webhook Idempotency: How to Handle Duplicate Events (HookReplay)](https://hookreplay.dev/blog/webhook-idempotency) — implementation patterns
- [Idempotency and Deduplication (Svix)](https://www.svix.com/resources/webhook-university/reliability/idempotency-and-deduplication/) — practical patterns

### Mobile / iOS Safari
- [Fixing iOS Toolbar Overlap with CSS Viewport Units (Opus)](https://opus.ing/posts/fixing-ios-safaris-menu-bar-overlap-css-viewport-units) — `dvh` solution
- [Safari 15 viewport height (Luke Channings)](https://lukechannings.com/blog/2021-06-09-does-safari-15-fix-the-vh-bug/) — historical context, dvh introduction
- [iOS 26: Drawer leaves bottom gap (MUI #46953)](https://github.com/mui/material-ui/issues/46953) — current iOS Safari issues with safe-area
- [Tailwind: -webkit-fill-available for 100vh](https://github.com/tailwindlabs/tailwindcss/discussions/4515) — fallback approaches

### HTMX + Alpine
- [Alpine x-bind not applying after HTMX swap (Discussion #3985)](https://github.com/alpinejs/alpine/discussions/3985) — confirmed swap-clobbers-Alpine issue
- [HTMX alpine-morph Extension](https://v1.htmx.org/extensions/alpine-morph/) — solution: morph extension preserves Alpine state
- [HTMX Events Documentation](https://htmx.org/events/) — HX-Trigger, afterSwap lifecycle hooks

### SQLite / Migrations
- [Running Batch Migrations for SQLite (Alembic)](https://alembic.sqlalchemy.org/en/latest/batch.html) — `batch_alter_table` for SQLite limitations
- [Alembic Database Migrations Guide](https://medium.com/@tejpal.abhyuday/alembic-database-migrations-the-complete-developers-guide-d3fc852a6a9e) — production-safe patterns
- [SQLAlchemy + Alembic + Docker](https://medium.com/@johnidouglasmarangon/using-migrations-in-python-sqlalchemy-with-alembic-docker-solution-bd79b219d6a) — startup migration pattern

### Lidarr
- [Lidarr Add Artist Issue #5534](https://github.com/Lidarr/Lidarr/issues/5534) — known artist-add failure modes
- [Lidarr Settings (Servarr Wiki)](https://wiki.servarr.com/lidarr/settings) — quality/metadata profile semantics
- [Lidarr API Communication Errors #5498](https://github.com/Lidarr/Lidarr/issues/5498) — api.lidarr.audio dependency, 503/524 timeouts (carried over from v1 PITFALLS)

### v1 Project Context
- `.planning/research/v1.0/PITFALLS.md` — v1 pitfalls (Spotify uncertainty, library sync at scale, credential redaction, Plex ratingKey stability, Lidarr quality/metadata profile)
- `.planning/STATE.md` — v1 decisions: JSON mode for Instructor, track ID validation against hallucination, AsyncIOScheduler singleton, lazy engine pattern, Alpine $dispatch issue
- `.planning/notes/connection-test-bugs.md` — Lidarr connection test carried over to v2
- `.planning/notes/hardware-profile.md` — Synology DS423+ amd64, 32GB RAM, synobridge Docker network, Tailscale VPN
- `.planning/notes/mobile-first.md` — touch targets ≥44px, no hover-only UX, portrait-first design

---
*Pitfalls research for: Composer v2.0 Music Companion — adding webhooks, clustering, LLM ranking, mobile-first UI to existing v1 system*
*Researched: 2026-05-08*
