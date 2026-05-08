# Feature Research — Composer v2.0 Music Companion

**Domain:** Continuous music companion (vibe curation + discovery + Lidarr-fed taste-aware artist discovery) for self-hosted Plex/Lidarr stack
**Researched:** 2026-05-08
**Confidence:** MEDIUM-HIGH (industry patterns from Spotify/Apple/Pandora well-documented; self-hosted equivalents thinner; Plex Pass webhook payload semantics verified; popularity-bias and cold-start research solid)

## Critical Context Carried From v1

Composer v1 already ships:
- Plex sync to SQLite (tracks, albums, artists, ratingKey, basic metadata) — **dependency for everything below**
- Essentia analyzer producing energy / tempo / danceability / valence per track — **dependency for vibe slotting and shortlist scoring**
- Settings page for Plex / Anthropic / Lidarr credentials (secrets masked) — **reused for v2; needs Plex Pass / webhook URL field**
- Library browse UI — **stays; needs mobile-responsive pass**
- Push-playlist-to-Plex code path — **reused for vibe playlists and Composer Suggestions**

What's gone in v2:
- Mood-chat UI (the "type a vibe, get a one-shot playlist" interface) is retired. Vibe-home becomes the new landing page.
- Generation history table — continuous queue model makes a "history of generated playlists" obsolete.

Everything in this document assumes those v1 capabilities exist and need to be **wired into the new companion model**, not rebuilt.

## Feature Landscape

### Table Stakes (Users Expect These)

Features the user already expects after seeing Spotify Daily Mix / Plexamp / typical music apps — missing them makes v2 feel half-baked.

| Feature | Why Expected | Complexity | Notes |
|---|---|---|---|
| **Plex `userRating` sync** (read, never write) | The whole product is built around stars-as-taste-signal; if rating in Plexamp doesn't show up in Composer within minutes, the loop is broken | M | webhook (`media.rate` event, Plex Pass only) primary, polling fallback every N minutes, manual "refresh now" button. Depends on v1 Plex sync; new column `tracks.user_rating` plus `tracks.rated_at`. ([Plex webhooks][plex-webhooks]) |
| **3–7 persistent vibe playlists** | Spotify Daily Mix uses 4–10 (typically 6); users expect "a few mixes that span my taste," not 30 | M | LLM proposes initial K from rated set; user names/edits during wizard; persisted as `vibes` table. Capped at 7 to keep UX scannable. ([Daily Mix][daily-mix-explained], [Daily Mix mechanics][daily-mix-medium]) |
| **One Plex playlist per vibe**, Composer-owned | User wants to play a vibe in Plexamp, not just look at it in Composer | S | Reuses v1 push-to-Plex. Naming convention: `Composer · {vibe_name}` so it's identifiable and Composer never touches non-Composer playlists. Dependency: v1 Plex playlist push code. |
| **Auto-slot newly-rated tracks** | If user rates a track 5 stars and it doesn't appear in any vibe within minutes, the "automatic" promise feels broken | M | Audio-feature distance (energy/tempo/danceability/valence) to each vibe's centroid; threshold determines membership. No global rescoring — slot only the new track. Depends on v1 Essentia features. |
| **Continuous "Composer Suggestions" Plex playlist** that drains as you listen | Core differentiator framed as table stakes by Daily Mix — users assume "always something fresh queued up" | L | Default 30 tracks (matches Discover Weekly's 30); refill triggered by `media.scrobble` (≥90% played) or `lastViewedAt` change via polling. Track removal: when consumed (scrobbled), remove + refill 1. ([Discover Weekly size][discover-weekly]) |
| **First-run setup wizard** | If user lands on a blank app with vague "rate some tracks" copy, abandonment is near-100%; wizards are the standard onboarding for taste-based apps | M | Steps: (1) rating-source confirm → (2) sync ratings → (3) propose K vibes → (4) name/edit → (5) generate first Suggestions queue. Detect cold-start (rated <50) and route to a degraded path. |
| **Why this track? minimal explanation** | Daily Mix shows "Made for you" + seed artists; users distrust black-box recommenders | S | Surface 1–2 sentences per recommended track: similar to {seed artist}, fits {vibe}, energy/tempo match. Generated cheaply from features, not LLM (latency + cost). |
| **Mobile portrait-first navigation** | User accesses primarily from phone (Plexamp playback context); desktop secondary | M | Bottom tab bar (3–4 items: Vibes / Suggestions / Discover / Settings). Persistent. Tap targets ≥44px. Stack everything vertically. Already in design notes. ([Mobile nav patterns][mobile-nav-patterns]) |
| **Manual refresh / "Resync now"** | Polling has a window; user wants to force a refresh after a rating spree | S | Button on home; calls Plex sync + reslot. Reused via v1 sync infra. |
| **Lidarr "add this artist" one-click** | v1 already promised this; users expect it to be a single tap on a recommendation card | S | Reuses v1 Lidarr client. Quality profile pre-configured in settings, picked from dropdown. |
| **Settings page sticks to v1 conventions** | User has already configured Plex/Anthropic/Lidarr; don't make them re-enter | S | Add only new fields: Plex Pass webhook secret, polling interval, vibe count, Suggestions queue size, default Lidarr quality profile. |

### Differentiators (Competitive Advantage)

Features that set Composer apart from MediaSage / Sonic Sage / Lidify / Cmdarr.

| Feature | Value Proposition | Complexity | Notes |
|---|---|---|---|
| **AI-clustered vibes from your stars** (not genre tags, not LLM-only) | Sonic Sage uses one-shot prompts; MediaSage uses metadata-only LLM; Lidify clusters audio features but doesn't use ratings; nobody anchors clustering on user-validated ratings | L | Hybrid: Essentia audio features (numeric) + metadata (genre/artist/decade) sent to Claude as cluster description prompt; Claude returns proposed K clusters with names/seeds; user finalizes. Dependency: v1 Essentia features + Claude integration. |
| **Audio-feature distance for slotting** (cheap, no LLM per rating event) | Spotify likely uses heavy ML for slotting; Composer can match the *feel* with simple Euclidean distance because the user's library is small (~10k tracks) and clusters are user-named | S | Per-vibe cached centroid (mean of energy/tempo/danceability/valence over rated members) + std-dev-scaled distance threshold. No LLM cost on rating events. Multi-membership allowed if track is within threshold of 2+ vibes. |
| **Soft membership** (one track can live in multiple vibes) | Real listening doesn't fit hard partitions — a Tame Impala track can be both "psych chill" and "summer energy"; hard clustering forces awkward choices | M | Junction table `vibe_tracks` (vibe_id, track_id, score). Threshold-gated: track joins all vibes whose centroid is within `dist < threshold`. Cap per-track membership at 2 to avoid noise. ([Soft clustering][soft-clustering]) |
| **Event-driven refill, no scheduled rescans** | Lidify rescans periodically; Spotify refreshes weekly. Composer regenerates only the *delta* when user actually consumes a Suggestion — near-zero idle CPU | M | Plex `media.scrobble` webhook → remove from Suggestions → shortlist via feature distance to top-rated tracks → LLM re-rank top 50 → pick 1 → append. PROJECT.md key decision already locked. |
| **LLM-ranked shortlist for Suggestions only** (cheap; the expensive call only happens on consumption) | LLM-only systems blow through quota; pure feature-distance gives boring "more of the same"; the hybrid is the sweet spot | M | Generate ~50-track shortlist from feature distance to taste centroid; LLM re-ranks with prompt-cached taste profile + recently-played avoidance; pick top N. Stay under $5/year per PROJECT.md constraint. |
| **"Why this track" with feature attribution** | Most music apps have black-box recs; transparency builds trust on a personal taste tool | S | Templated, not LLM: "Energy {0.72} ≈ your '{vibe_name}' centroid {0.74}; similar to {top_artist_in_seed_vibe}" — auto-generated from numbers already computed. ([UX transparency][ux-transparency]) |
| **Lidarr taste-aware artist discovery** (not Last.fm "similar artists" — your-taste-shaped) | Lidify uses Last.fm popularity-skewed similar-artist lookup; Cmdarr does the same. Composer pulls candidate artists from a source (Last.fm seed → expand) and re-ranks by closeness to YOUR rated taste profile, deliberately demoting hits | M | Use Last.fm `artist.getSimilar` for candidates seeded from each vibe; LLM re-rank for taste closeness; demote artists with >X% library penetration in the requested genre to fight popularity bias. ([Popularity bias][popularity-bias]) |
| **Auto-ingest Lidarr arrivals into the loop** | Lidify ends at "added to Lidarr." Composer closes the loop: when Lidarr finishes import, Composer detects the new tracks, runs Essentia, and they become eligible for Suggestions and vibe slotting | M | Lidarr webhook (`OnImportComplete`) or polling Lidarr's queue API. Triggers v1 Essentia analysis path on the new ratingKeys. |
| **"More like this rated track"** seed mode at the vibe level | Per-vibe action: "give me 10 unrated suggestions that anchor on this 5-star track" — bridges rating → discovery | S | Reuses shortlist + LLM rerank with single seed track instead of vibe centroid. Returns to a transient pop-up list, not pushed to Plex. |
| **Vibe edit / re-cluster on demand** | User taste shifts; rare but essential escape hatch | M | "Re-cluster" button on vibes home. Locked behind a confirm modal. Re-runs the wizard's clustering step against current rated set; user can keep names from old clusters. PROJECT.md key decision: only on user request, never scheduled. |
| **Mobile-first portrait UI across all v2 surfaces** | Most self-hosted music tools have desktop-first UIs that look broken on phones; Composer's primary access is the phone in Plexamp's hand | M | Bottom tab bar; full-width cards; no horizontal scrolling on key surfaces; vibe cards 1-up portrait, 2-up landscape. Tap-to-add not hover-to-add. |
| **Cold-start degraded path** | If rated <50, K-clustering is meaningless; UX must explicitly handle this rather than ship 7 weird clusters | S | If rated <50: skip clustering wizard, prompt "rate ~50 tracks in Plexamp to unlock vibes" + offer single "All my favorites" pseudo-vibe. Suggestions still works but uses entire rated set as one centroid. ([Cold start research][cold-start]) |

### Anti-Features (Tempting, Don't Build)

| Feature | Why Tempting | Why Problematic | Alternative |
|---|---|---|---|
| **Touching user-created Plex playlists** ("we noticed your Workout playlist could use these tracks") | Sounds helpful; v1 had this idea | Violates the hands-off principle; users feel possessive over hand-curated playlists; one accidental delete poisons trust forever | Composer manages only `Composer · ` namespaced playlists. Flag locked in PROJECT.md key decisions. |
| **Writing ratings back to Plex** ("rate this track from inside Composer") | Closes the loop; some users would love it | Plex is the source of truth; Composer-as-rating-tool creates write-back conflicts and Plexamp/Plex-Web-UI is the canonical rating surface anyway | Read-only on ratings. Period. |
| **In-app playback / preview** | "I want to hear before I decide" | Massive complexity (transcode, stream, DRM, auth tokens leaking through proxy); Plexamp does it | Deep-link to Plexamp via `plex://` URI on track tap. |
| **Real-time mood detection from current playback** | "Composer should know I'm in a focus mood" | Privacy creep; brittle inference; user-named vibes are already the answer | Trust the user-named vibes; current vibe is whatever they have on. |
| **Continuous re-clustering ("nightly auto-refresh of vibes")** | Sounds smart | Vibes are an *identity* the user named; silently shifting them under their feet erases that identity. PROJECT.md locked in "user-request only." | Manual re-cluster button; show a "your taste has drifted X%" hint when divergence exceeds a threshold. |
| **Generation history / "playlists I made on Tuesday"** | v1 had it | v2 model is continuous, not batch; there's no discrete "generation" event to log; Plex itself stores playlist state | Drop entirely; playlist on Plex side is the artifact. |
| **Multi-user / per-user vibes** | "My partner uses Plex too" | PROJECT.md says single-user; auth is huge scope; each user runs their own container | Out of scope; one Composer per Plex user. |
| **Spotify integration / cross-platform push** | "Push my vibe to Spotify too" | Out of scope per PROJECT.md; Spotify API restrictions also make this fragile | Plex-only. |
| **Full automatic Lidarr download (no confirmation)** | "Just add the artists Composer suggests" | Removes user agency; can fill disk with unwanted music; collateral when LLM hallucinates an artist or matches the wrong MBID | One-click confirmation card (already current behavior); never auto-add without a tap. |
| **In-app mood chat ("type a vibe to get a one-shot playlist")** | v1 did this; some users will miss it | Companion model replaces it; two-mode UX (chat AND vibes) confuses the mental model | Retire UI. If a one-shot vibe is needed, the user can rate a few tracks of that vibe and Composer auto-clusters. |
| **Schedule-based playlist refresh ("regenerate Monday Morning playlist weekly")** | Spotify-style automation feels nice | Triggers global rescore on a schedule, blowing the no-global-rescoring constraint; vibes are already living, not weekly | Vibes auto-update on rating events; no scheduled refresh needed. |
| **Recommending tracks the user already rated low** ("you might want to revisit this 1-star") | Some apps do "your dislikes might've changed" | Annoying; signals the system isn't listening; users can manually unrate in Plexamp | Hard-exclude tracks rated 1–2 stars from all suggestion paths. |
| **Library statistics dashboard** | "Show me my taste" — fun but vibes-shaped already | Feature creep; vibes themselves are the dashboard | Defer indefinitely; vibe pages already show feature distributions if needed. |
| **Showing skipped-track history as a UX surface** | Skip rate is a real signal in Spotify | Plex doesn't expose skip events reliably (no media.skip webhook); inferring skips from short play counts is noisy | Use only Plex `viewCount` + `lastViewedAt` for consumption signal; ignore skip semantics. |

## Feature Dependencies

```
[v1: Plex sync]                      [v1: Essentia features]
      |                                       |
      +---requires---> [Rating sync] <-------+
                              |
             +----------------+-----------------+
             v                                  v
   [Vibe clustering wizard]            [Auto-slot on rating]
             |                                  |
             v                                  |
   [Vibe Plex playlists]  <---reuses v1 push----+
             |
             v
   [Suggestions queue]  <---requires---  [Plex play events (webhook+poll)]
             |
             +-------------> [Why-this-track explanations]
             |
             v
   [Lidarr taste-aware discovery]
             |
             v
   [Auto-ingest Lidarr arrivals]  --triggers--> [v1 Essentia path]
                                               --triggers--> [Auto-slot + Suggestions eligibility]

[Mobile-first UI shell]  <---wraps all v2 surfaces

[Setup wizard]  <---gates---  [Vibe clustering]  ←(blocks first-run)
       |
       +---requires---> [Cold-start fallback path] (if rated < 50)
```

### Dependency Notes

- **Rating sync gates everything**: vibe clustering, auto-slot, Suggestions ranking, and Lidarr discovery all consume the rated set. If `tracks.user_rating` is empty, the whole product is dead. Phase 1 of v2.0 must land rating sync first.
- **Vibe clustering depends on Essentia features being populated**: Users coming from v1 already have Essentia run on their library. New deployments need to wait for Essentia to finish before the wizard can produce meaningful clusters. Wizard should detect missing-features state and gate accordingly.
- **Suggestions queue depends on vibes existing**: The Suggestions ranker uses the vibe centroids (or composite taste centroid) as inputs. Building Suggestions before vibes exist requires a fallback: if no vibes, treat all rated tracks as one centroid.
- **Lidarr discovery depends on rated set + library penetration data**: Both the seeds (which vibes/artists to base recommendations on) and the popularity-bias correction (demoting artists already heavily represented) require library data. No new dependency beyond v1.
- **Auto-ingest Lidarr arrivals enhances vibes + Suggestions**: It's not a blocker; if missing, new tracks just don't show up until next manual sync. But it closes the loop — should ship in the same milestone.
- **Mobile-first UI shell wraps everything**: It's not a feature, it's a constraint on every v2 surface. Builds in parallel.
- **Plex Pass status branches the implementation**: Webhooks are Plex Pass-only. Polling fallback must work for non-Plex Pass users. Detect at config-time and warn if Plex Pass missing — polling cadence (e.g. every 60s) is fine for a single-user tool. ([Plex webhooks][plex-webhooks])

## MVP Definition

### Launch With (v2.0 — single milestone)

The minimum viable companion. Anything less and the pivot story doesn't hold together.

- [ ] **Rating sync** (webhook primary, polling fallback, manual refresh) — **gates everything** (per-track `user_rating`, `rated_at`)
- [ ] **First-run setup wizard** — sync ratings → propose K vibes → user names/edits → push playlists; cold-start branch for <50 ratings
- [ ] **Vibe clustering** (LLM-proposed K=3–7, user-finalized) — persisted with name + centroid + seed members
- [ ] **One Plex playlist per vibe**, Composer-namespaced, soft membership (track in 1–2 vibes)
- [ ] **Auto-slot newly-rated tracks** via audio-feature distance to vibe centroids
- [ ] **Composer Suggestions playlist** (default 30 tracks) draining on Plex play events; refill via shortlist + LLM re-rank
- [ ] **Why-this-track explanations** (templated from features; not LLM)
- [ ] **Lidarr taste-aware artist discovery** with one-click add and popularity-bias demotion
- [ ] **Auto-ingest Lidarr arrivals** into Essentia + scoring pipeline
- [ ] **Mobile-first portrait UI** for all v2 surfaces (vibes home, Suggestions, Discover, Settings)
- [ ] **Retire v1 mood-chat UI** (vibes home becomes landing page)
- [ ] **Manual re-cluster button** (rare-use escape hatch)
- [ ] **Fix Lidarr connection-test bug** carried from v1

### Add After Validation (v2.1+)

Real-world use will surface these.

- [ ] **Per-vibe seed mode** ("give me 10 more like this rated track") — falls out cheap from existing shortlist code
- [ ] **Vibe taste-drift indicator** ("your '{vibe}' has shifted from your old centroid by X%") — informational, not auto-acting
- [ ] **Suggestions queue size override** per session ("give me a bigger queue today")
- [ ] **Skip / dislike feedback on a Suggestion** (since Plex doesn't expose skips reliably, this would be an in-Composer "not for me" button on a Suggestion card that downweights for that track + similar-feature region)
- [ ] **Vibe playback statistics** ("your '{vibe}' played 12h this week") — light analytics
- [ ] **Genre-anchored discovery** ("find me artists like Boards of Canada specifically, not vibe-blended")
- [ ] **Rating-streak / gamification** to encourage rating in cold-start (light touch, easy to over-do)
- [ ] **Configurable Suggestions size** in Settings (default 30, range 10–100)

### Future Consideration (v3+)

Defer until the loop is validated.

- [ ] **Multi-user support** — significant scope, runs counter to PROJECT.md single-user constraint
- [ ] **Last.fm / ListenBrainz scrobble import** as a secondary taste signal (Plex `viewCount` is enough for v2)
- [ ] **Cross-vibe playlist generation** ("blend my 'focus' and 'chill' vibes for a long study session")
- [ ] **Vibe schedule** ("'energetic' vibe at 8am, 'wind-down' at 10pm") — neat but adds clock-aware complexity
- [ ] **Daily/weekly auto-refresh of Suggestions** — runs counter to PROJECT.md event-driven decision
- [ ] **Public vibe sharing** — sharing playlist URLs with friends. Tiny feature, big scope creep on auth.
- [ ] **In-app rating UI** — even if read-only-write-Plex, this is a footgun
- [ ] **Spotify-as-secondary-source** for audio features or seeds — Spotify API restrictions still messy

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---|---|---|---|
| Plex `userRating` sync (webhook + polling) | HIGH | M | P1 |
| Vibe clustering LLM step + setup wizard | HIGH | L | P1 |
| Vibe Plex playlists (push) | HIGH | S | P1 |
| Auto-slot on rating (feature distance) | HIGH | M | P1 |
| Composer Suggestions playlist + drain/refill | HIGH | L | P1 |
| Mobile-first portrait UI shell | HIGH | M | P1 |
| Why-this-track explanation (templated) | MEDIUM | S | P1 |
| Cold-start <50-ratings fallback path | HIGH (1st-run) | S | P1 |
| Manual re-cluster button | MEDIUM | M | P1 |
| Retire v1 mood-chat UI | MEDIUM (clarity) | S | P1 |
| Soft membership (track in 1–2 vibes) | MEDIUM | M | P1 |
| Lidarr taste-aware artist discovery | HIGH | M | P1 |
| Auto-ingest Lidarr arrivals | HIGH | M | P1 |
| Lidarr popularity-bias demotion | MEDIUM | S | P1 |
| Fix Lidarr connection-test bug | MEDIUM | S | P1 |
| Per-vibe "more like this" seed mode | MEDIUM | S | P2 |
| Skip / "not for me" feedback button | MEDIUM | M | P2 |
| Vibe taste-drift indicator | LOW | M | P2 |
| Configurable Suggestions queue size | LOW | S | P2 |
| Vibe playback stats | LOW | M | P2 |
| Cross-vibe blend playlist | LOW | M | P3 |
| Daily/weekly Suggestions auto-refresh | NEG (violates constraint) | M | P3 (anti) |
| Multi-user | LOW (out of scope) | L | P3 |
| Last.fm / ListenBrainz import | LOW | M | P3 |

**Priority key:**
- P1: Must have for v2.0 launch
- P2: Add post-validation (v2.1)
- P3: Defer to v3+ or keep on the anti-feature list

## Competitor Feature Analysis

| Feature | Sonic Sage | MediaSage | Lidify | Cmdarr | **Composer v2** |
|---|---|---|---|---|---|
| Taste signal source | One-shot user prompt | One-shot user prompt | Lidarr seeds (no ratings) | Lidarr + ListenBrainz scrobbles | **Plex `userRating`** |
| Vibe clustering | None | None | Audio fingerprint clusters | Last.fm tag clusters | **LLM-proposed, user-finalized 3–7** |
| Library-aware results | No (also Tidal) | Yes | N/A (artist-only) | Yes | **Yes (strict)** |
| Audio features | None (LLM only) | None (LLM only) | Yes (built-in fingerprint) | None | **Essentia (v1, reused)** |
| Continuous queue | No (one-shot) | No (one-shot) | Auto-playlist refresh | Sync-driven | **Event-driven drain/refill (30 tracks)** |
| Why-this-track explanation | No | No | No | No | **Yes (templated)** |
| Multi-membership tracks | N/A | N/A | Hard cluster | N/A | **Soft (1–2 vibes)** |
| Lidarr artist discovery | No | No | Yes (Last.fm/Spotify) | Yes (Last.fm) | **Yes, taste-ranked w/ pop-bias demotion** |
| Auto-ingest Lidarr arrivals | No | No | No | No | **Yes** |
| Mobile-first UI | N/A (Plexamp) | Desktop | Desktop | Desktop / API-only | **Yes (portrait-first)** |
| Hands-off existing Plex playlists | N/A | Yes | N/A | No (it sync-mutates) | **Yes (Composer-namespaced only)** |
| Re-cluster on demand | N/A | N/A | Auto | N/A | **User-request only** |
| Rating-write-back | N/A | No | No | No | **No (read-only on ratings)** |
| Cost per LLM event | $0.002–0.04 | Free–$0.04 | Free | Free | **<$5/yr total (cached profile + sparse calls)** |

**Key competitive gaps Composer v2 fills:**
1. **Stars-as-taste-signal** — no competitor anchors on Plex ratings; everyone uses one-shot prompts (Sonic Sage, MediaSage), seed artists (Lidify), or scrobbles (Cmdarr)
2. **Persistent user-named vibes** — neither Lidify's auto-clusters nor Spotify's algorithmic Daily Mix lets you *name* a cluster and keep it stable over time
3. **Soft membership** — most clustering tools force hard partitions; soft membership matches real listening
4. **Event-driven, no scheduled rescans** — Lidify and Cmdarr both use periodic refresh; Composer regenerates only deltas
5. **Mobile-first** — every self-hosted competitor is desktop-first
6. **Closed loop with Lidarr** — Lidify ends at "added"; Cmdarr partially closes loop on import; Composer fully closes it through Essentia ingestion → vibe slotting → Suggestions eligibility

## Specific Question Answers (from research brief)

### How many vibes is right? Hard vs soft assignment?
- **Number**: 3–7. Spotify Daily Mix typically shows ~6 mixes; clustering research suggests K=4–8 is the sweet spot for personal libraries (~1k–10k tracks). Below 3 = blunt; above 7 = noisy and UI-cluttered. Let LLM propose, user finalize within 3–7 cap. ([Daily Mix][daily-mix-explained])
- **Hard vs soft**: **Soft**, capped at 2 memberships per track. Real listening doesn't fit hard partitions. Use a distance threshold (track joins all vibes whose centroid is within `dist < threshold`). Cap at 2 to prevent noise. ([Soft clustering][soft-clustering])

### Taste signal: ratings vs play-count vs recency?
- **Composer v2 = ratings only** for the *clustering and centroid* signal. Reasoning:
  - Plex `userRating` is explicit, high-signal, intentional (Plexamp's star button is a deliberate act).
  - Plex `viewCount` is noisy: includes background plays, Plexamp shuffle, etc. Industry research confirms implicit feedback is noisier than explicit. ([Implicit vs explicit][implicit-explicit])
  - Recency-weighting could come later but adds complexity for marginal gain on a single-user tool with a stable library.
- **For consumption tracking** (Suggestions drain), `lastViewedAt` / `media.scrobble` is fine — that's a different signal (did the user listen to it?), not a preference signal.
- **For Spotify**: Discover Weekly heavily uses **collaborative filtering on implicit signals** (plays, saves, skips) plus content-based audio analysis — but Spotify has 600M users to cross-reference. Composer is single-user; explicit ratings beat implicit at our scale. ([Discover Weekly][discover-weekly-explained])

### Continuous queue dynamics
- **Target size**: 30 tracks. Matches Discover Weekly. Big enough for a multi-hour session, small enough that "drained 5" feels meaningful. Configurable later (P2). ([Discover Weekly][discover-weekly])
- **Refill cadence**: Event-driven — 1-in/1-out per `media.scrobble`. No scheduled rebuild. PROJECT.md decision locked.
- **Stale-track removal**: Track removed when consumed (scrobbled). No time-based staleness in v2.0; if user hasn't listened in 2 weeks, the queue still has the same 30 tracks waiting. v2.1 may add "rotate after N days unviewed."
- **Avoid showing same recommendation twice**: Maintain a `suggestion_history` table (track_id, shown_at, consumed_at). Exclude any track shown in last 30 days unless rated 4+ in interim. Apple Music's New Music Mix uses similar exclusion. ([New Music Mix][apple-recs])

### Cold start: <50 / <10 ratings?
- **Hard threshold ~50 ratings** to enable meaningful K-clustering. Below this, K-means on 4-dimensional feature vectors over ~10 points produces meaningless clusters.
- **<10 ratings**: Refuse to cluster; show wizard step "rate ~50 tracks in Plexamp to unlock vibes; Composer reads from Plexamp star button." Offer a single fallback "all my favorites" pseudo-vibe so something exists.
- **10–49 ratings**: Allow degraded mode — single composite centroid, no per-vibe segmentation, Suggestions still works. UI shows "rate more for vibes."
- **User has 460+ already** per PROJECT.md, so they're well past cold-start. But new users need the gate. ([Cold start research][cold-start])

### Lidarr discovery anti-patterns
- **Popularity bias is the dominant failure mode**. Last.fm's `artist.getSimilar` returns popularity-skewed results — Tame Impala's "similar artists" is full of mainstream indie. ([Popularity bias][popularity-bias])
- **Mitigation**: After fetching candidate similar artists, demote any artist whose Plex library penetration in the same genre is already >X% (suggesting it's already covered) AND demote artists with >Y monthly Last.fm listeners. Re-rank with Claude using user's rated taste profile.
- **Another failure mode**: hallucinated artists. LLM-only artist suggestions occasionally invent names. Always validate against Lidarr's MusicBrainz lookup before showing the user; reject any artist not resolvable in MusicBrainz.

### "Why this track" UX patterns
- Spotify shows "Made for you, based on {seed}" — minimal, not per-track.
- Pandora's Music Genome Project surfaces explicit feature attributes ("we picked this for its rolling basslines and folk influences") — most transparent in industry but heavy.
- **Composer v2 approach**: Templated 1–2 lines per Suggestion, generated cheaply: `"{energy_phrase}, similar to {top_3_seed_artists}, fits your '{vibe_name}'"`. No per-track LLM call. ([UX transparency][ux-transparency])
- Surface only on tap-to-expand (don't clutter the card).

### Mobile-first patterns
- **Bottom tab bar, 3–4 items**: Vibes / Suggestions / Discover / Settings. Persistent across all surfaces. Industry standard (Spotify, Apple Music, Plexamp). ([Mobile nav][mobile-nav-patterns])
- **No swipe-as-only-action**: Swipe gestures (e.g., swipe to dismiss a Suggestion) must always have a visible button alternative — gestures are non-discoverable. ([Gesture nav][gesture-nav])
- **No hover dependencies**: every action must work via tap.
- **Tap targets ≥44px**: already in v1 UI spec.
- **No persistent now-playing bar**: Composer doesn't play music — Plexamp does. Don't fake a now-playing bar; instead, deep-link to Plexamp on track tap.
- **Vibe cards 1-up portrait, 2-up landscape**: feature image / name / track count / play button.
- **Vertical-stack everything**: no side-by-side panels (kills the "chat alongside playlist" v1 layout).

## Sources

### Industry music recommendation patterns
- [Discover Weekly — how it works][discover-weekly-explained] — collab filtering + NLP + audio analysis (HIGH; Spotify Engineering blog distillation)
- [Discover Weekly playlist size = 30][discover-weekly] (HIGH)
- [Spotify Daily Mix — how it works][daily-mix-explained] (MEDIUM; secondary source distilling Spotify product behavior)
- [How your Daily Mix just gets you][daily-mix-medium] (MEDIUM)
- [Music recommendation system — exploration vs exploitation][exploration-exploitation] (MEDIUM; academic)
- [Implicit vs explicit feedback in music recommendation][implicit-explicit] (HIGH; ACM RecSys)
- [Apple Music New Music Mix — duplicate avoidance][apple-recs] (MEDIUM)
- [Cold start in recommender systems][cold-start] (HIGH; Wikipedia + ACM)
- [Popularity bias in music recommendation][popularity-bias] (HIGH; arXiv)

### Self-hosted competitors
- [MediaSage GitHub][mediasage] (HIGH; primary source)
- [Lidify GitHub][lidify] (HIGH; primary source)
- [Cmdarr GitHub][cmdarr] (HIGH; primary source)
- [Aurral GitHub][aurral] (HIGH; primary source)
- [Sonic Sage / Plexamp AI playlists][sonic-sage] (MEDIUM)

### Plex integration specifics
- [Plex Webhooks documentation][plex-webhooks] (HIGH; official, Plex Pass requirement)
- [Plex `userRating` API + python-plexapi][plex-userRating] (HIGH; official issue thread)
- [Plex rating endpoint `/library/rate`][plex-rate-endpoint] (MEDIUM; community-confirmed)

### Clustering theory
- [Hard vs soft clustering for music][soft-clustering] (HIGH; data science reference)
- [Clustering songs into vibes][clustering-vibes] (MEDIUM)

### UX patterns
- [Mobile navigation patterns 2026][mobile-nav-patterns] (HIGH; UXPin)
- [Gesture navigation best practices][gesture-nav] (HIGH; NN Group)
- [Sticky / persistent UI elements][sticky-bars] (HIGH; Smashing Magazine)
- [Music app UX transparency / feedback][ux-transparency] (MEDIUM)

[discover-weekly-explained]: https://medium.com/the-sound-of-ai/spotifys-discover-weekly-explained-breaking-from-your-music-bubble-or-maybe-not-b506da144123
[discover-weekly]: https://stratoflow.com/spotify-recommendation-algorithm/
[daily-mix-explained]: https://playlistpilotapp.com/blog/spotify-daily-mix-explained
[daily-mix-medium]: https://medium.com/systems-ai/spotifys-machine-learning-algorithms-and-your-daily-mix-f49d97db4b16
[exploration-exploitation]: https://arxiv.org/abs/1812.03226
[implicit-explicit]: https://dl.acm.org/doi/10.1145/1869446.1869453
[apple-recs]: https://beatstorapon.com/blog/the-apple-music-algorithm-in-2026-a-comprehensive-guide-for-artists-labels-and-data-scientists/
[cold-start]: https://en.wikipedia.org/wiki/Cold_start_(recommender_systems)
[popularity-bias]: https://arxiv.org/pdf/2208.09517
[mediasage]: https://github.com/ecwilsonaz/mediasage
[lidify]: https://github.com/TheWicklowWolf/Lidify
[cmdarr]: https://github.com/DeviantEng/Cmdarr
[aurral]: https://github.com/lklynet/aurral
[sonic-sage]: https://www.techhive.com/article/1921927/how-to-use-plex-ai-music-playlists.html
[plex-webhooks]: https://support.plex.tv/articles/115002267687-webhooks/
[plex-userRating]: https://github.com/pkkid/python-plexapi/issues/259
[plex-rate-endpoint]: https://forums.plex.tv/t/is-there-an-http-endpoint-for-changing-the-star-rating-of-a-track/926207/3
[soft-clustering]: https://www.datasciencebase.com/unsupervised-ml/advanced-clustering-topics/soft-clustering-vs-hard-clustering/
[clustering-vibes]: https://medium.com/inst414-data-science-tech/clustering-songs-to-discover-listener-preferences-3d319d868757
[mobile-nav-patterns]: https://www.uxpin.com/studio/blog/mobile-navigation-examples/
[gesture-nav]: https://www.nngroup.com/articles/mobile-navigation-patterns/
[sticky-bars]: https://www.smashingmagazine.com/2020/01/mobile-pwa-sticky-bars-elements/
[ux-transparency]: https://www.onething.design/post/tuning-ux-for-music-streaming-apps

---
*Feature research for: Composer v2.0 — continuous music companion built on Plex ratings*
*Researched: 2026-05-08*
