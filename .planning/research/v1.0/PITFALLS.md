# Pitfalls Research

**Domain:** Self-hosted mood-based playlist generation with Plex/Lidarr/Spotify integration
**Researched:** 2026-04-09
**Confidence:** HIGH

## Critical Pitfalls

### Pitfall 1: Spotify Audio Features API Access Uncertainty

**What goes wrong:**
The project's entire matching pipeline depends on Spotify's audio features API (energy, tempo, valence, danceability), but this endpoint has been progressively restricted since November 2024. New apps created after that date may not have access. As of February 2026, Development Mode requires a Spotify Premium subscription and limits apps to 5 test users. The audio features endpoint was not explicitly removed from dev mode, but apps created post-November 2024 may need to request explicit access that may or may not be granted.

**Why it happens:**
Developers assume a public API endpoint will remain stable and accessible. Spotify has been aggressively locking down its API to prevent third-party tools from competing with Spotify's own features. The restrictions came in waves (Nov 2024, May 2025, Feb 2026), each more restrictive.

**How to avoid:**
1. Register a Spotify developer app immediately and verify audio features access works before writing any code. The developer must have Spotify Premium.
2. Design the audio features layer as an abstraction -- an interface that can be backed by Spotify, a local cache, or a fallback provider.
3. Cache all audio features aggressively in the local database. Once fetched, never re-fetch. The local DB becomes the source of truth.
4. Build a fallback path: if Spotify access is lost, the system should still function with cached data and degrade gracefully for unmatched tracks.
5. Evaluate Essentia (open-source audio analysis library) as a self-hosted alternative that can compute similar features (energy, danceability, tempo, key) directly from audio files. This eliminates the Spotify dependency entirely but adds computational cost and complexity.

**Warning signs:**
- 403 errors from `/v1/audio-features` endpoint during initial development
- Spotify developer dashboard shows "restricted" next to audio features
- New app registration flow mentions access limitations

**Phase to address:**
Phase 1 (Foundation) -- validate API access before building anything else. This is a go/no-go gate.

---

### Pitfall 2: Track Matching Between Local Library and Spotify Catalog

**What goes wrong:**
Matching 10k+ local tracks to Spotify's catalog produces a high rate of false matches, missed matches, and duplicates. Common failures: (1) local files have inconsistent or incomplete metadata tags, (2) Spotify search returns the wrong version (remaster vs original, live vs studio, deluxe edition), (3) artist name variations ("The Beatles" vs "Beatles"), (4) tracks not on Spotify at all, (5) the search API only matches on the first-listed artist for multi-artist tracks.

**Why it happens:**
Developers assume clean metadata and straightforward 1:1 matching. In reality, music metadata is notoriously messy. Lidarr helps with normalization via MusicBrainz, but ISRCs can be wrong, missing, or mapped to incorrect recordings. Spotify's search API does fuzzy matching with no exact-match option, returning "Madonna" when searching "Mad".

**How to avoid:**
1. Use a multi-strategy matching pipeline: first try ISRC (if available from file tags or MusicBrainz), then fall back to `artist:X track:Y` search with normalization.
2. Normalize before matching: strip parenthetical suffixes like "(Remastered)", "(Deluxe)", "(feat. X)"; lowercase; remove accents; normalize whitespace.
3. Verify matches using track duration comparison (within +/- 5 seconds) as a sanity check.
4. Store match confidence scores and allow the user to manually correct mismatches.
5. Accept that some tracks will not match. Design the UI to show "X of Y tracks matched" and let the system work with partial coverage.
6. Batch the matching process and make it resumable -- do not try to match all 10k tracks in one run.

**Warning signs:**
- Match rate below 70% during initial sync
- Playlists containing obviously wrong tracks (wrong artist, wrong song)
- Users reporting "this track doesn't sound right for this mood"

**Phase to address:**
Phase 2 (Spotify Integration) -- the matching pipeline is core infrastructure that must be solid before mood filtering can work.

---

### Pitfall 3: Building the Entire System Around an Unstable External Dependency

**What goes wrong:**
The project architecture becomes tightly coupled to Spotify's API for its core value proposition (mood-based filtering). If Spotify further restricts or eliminates audio features access, the app becomes useless. This is not hypothetical -- Spotify has made three rounds of restrictions in 18 months.

**Why it happens:**
The project was conceived when Spotify audio features were more accessible. The convenient availability of pre-computed audio features (energy, valence, danceability) makes it the obvious choice. Self-hosted alternatives require more work.

**How to avoid:**
1. Define an `AudioFeaturesProvider` interface from day one. Spotify is one implementation.
2. Seriously evaluate Essentia as a primary or fallback provider. Essentia can compute BPM/tempo, energy, danceability, key, and mood classifications directly from audio files. It runs locally with no external API dependency.
3. Store all features in a local database with a provider tag (source: "spotify" vs source: "essentia" vs source: "user-tagged"). The mood filtering engine queries the local DB, not Spotify directly.
4. If using Essentia, batch-analyze tracks during initial library sync. For 10k tracks this takes time but only needs to happen once, with incremental updates for new tracks.

**Warning signs:**
- Code that calls Spotify API directly from the playlist generation logic
- No local feature cache -- features fetched on demand each time
- No abstraction layer between feature data and filtering logic

**Phase to address:**
Phase 1 (Architecture) -- the abstraction layer must be designed upfront, even if only Spotify is implemented initially.

---

### Pitfall 4: Plex Library Sync Performance at Scale

**What goes wrong:**
Naively fetching all tracks from a Plex library with 10k+ tracks causes timeouts, memory issues, or extremely slow startup. The Plex API returns verbose XML/JSON with metadata fields most operations do not need. Full library scans block the UI and make the app feel broken.

**Why it happens:**
Developers test with small libraries (50-200 tracks) and the full sync works fine. At 10k+ tracks, the response payload is massive, especially if `musicAnalysis` or full metadata is included. The Plex API does not have efficient pagination for music libraries out of the box.

**How to avoid:**
1. Use Plex API's `X-Plex-Container-Start` and `X-Plex-Container-Size` headers for pagination. Fetch in batches of 100-250 tracks.
2. Request only the fields needed: `ratingKey`, `title`, `grandparentTitle` (artist), `parentTitle` (album), `duration`, `addedAt`. Exclude `musicAnalysis` and other heavy fields.
3. Store the library state locally and only sync deltas (tracks added after `lastSyncTimestamp`). Use the `addedAt` field to detect new tracks.
4. Run the initial sync as a background job with progress reporting in the UI.
5. Never block the UI on a full library scan.

**Warning signs:**
- Library sync takes more than 30 seconds for 10k tracks
- Browser tab crashes or shows "out of memory" during sync
- API responses larger than 10MB

**Phase to address:**
Phase 2 (Plex Integration) -- efficient sync must be built from the start, not retrofitted.

---

### Pitfall 5: Spotify API Rate Limiting During Bulk Operations

**What goes wrong:**
Initial library matching requires fetching audio features for thousands of tracks. Spotify's rate limit is approximately 250 requests per 30-second window. The audio features batch endpoint accepts up to 100 track IDs per request. For 10k tracks, that is 100 batch requests minimum. Hitting the rate limit triggers a 429 response, and aggressive retry logic can result in 24-hour bans.

**Why it happens:**
Developers implement the matching pipeline without rate limiting, test with a small library, then deploy against a real 10k track library. The first full sync hammers the API and gets rate-limited.

**How to avoid:**
1. Use the batch audio features endpoint (`GET /v1/audio-features?ids=id1,...,id100`) -- 1 request instead of 100.
2. Implement a rate limiter: maximum 5 batch requests per 30-second window with exponential backoff on 429 responses.
3. Respect the `Retry-After` header in 429 responses exactly.
4. Make the sync interruptible and resumable. Track which tracks have been processed. If rate-limited, pause and resume later.
5. Show progress to the user: "Fetching audio features: 3,400 / 10,000 tracks processed"
6. After initial sync, only fetch features for newly added tracks.

**Warning signs:**
- 429 responses during sync
- Sync process stalling for hours
- Spotify developer dashboard showing quota warnings

**Phase to address:**
Phase 2 (Spotify Integration) -- rate limiting must be built into the Spotify client from the beginning.

---

### Pitfall 6: Mood Interpretation Producing Inconsistent or Nonsensical Results

**What goes wrong:**
The AI interprets "chill summer evening" and maps it to audio feature ranges, but the results feel wrong -- high-energy dance tracks mixed with ambient. Different phrasings of the same mood produce wildly different playlists. Users lose trust in the system quickly.

**Why it happens:**
(1) Mapping natural language moods to numeric audio feature ranges is inherently fuzzy. (2) LLMs are inconsistent -- the same prompt produces different parameter ranges each run. (3) Relying solely on audio features misses genre/style context. (4) No feedback loop to learn from user corrections.

**How to avoid:**
1. Use structured output from the LLM -- force it to return a JSON object with specific feature ranges (energy: [0.2, 0.5], valence: [0.3, 0.7], tempo: [80, 120]) rather than free-form text.
2. Define mood presets as anchors: "chill" always means energy < 0.4, "upbeat" always means energy > 0.7. Use the LLM to interpolate between presets, not generate from scratch.
3. Apply soft filtering, not hard cutoffs. Score each track against the mood criteria and sort by fit, rather than applying binary include/exclude thresholds.
4. Let users preview and edit the interpreted parameters before generating: "I interpreted 'chill summer evening' as: low energy, medium-high valence, tempo 80-110. Adjust?"
5. Store successful playlist generations and their parameters to build a feedback corpus over time.

**Warning signs:**
- Users consistently editing more than 50% of generated playlist tracks
- Same mood description producing very different playlists each time
- Playlists that "technically match" the features but feel wrong

**Phase to address:**
Phase 3 (Mood Engine) -- this is the core value proposition and needs careful iteration.

---

### Pitfall 7: API Credentials Exposed in Docker Image or Logs

**What goes wrong:**
Plex tokens, Spotify client secrets, and Lidarr API keys get baked into the Docker image, exposed via `docker inspect`, logged to stdout, or returned in API responses. For a self-hosted app this seems low-risk, but credentials in image layers persist in Docker Hub, and leaked Spotify credentials can be revoked by Spotify.

**Why it happens:**
Developers use environment variables for convenience, which are visible via `docker inspect`. Or they log HTTP requests including Authorization headers during debugging and forget to remove the logging. Or the settings API returns the full configuration including secrets.

**How to avoid:**
1. Use Docker Compose secrets or file-based mounts (`/run/secrets/`) for API credentials, not environment variables.
2. Support both: environment variables for simple setups, file mounts for security-conscious users. Read from file first, fall back to env var.
3. Never log credential values. Mask them in debug output: `plex_token=plx...k3f`.
4. API responses for settings must redact secrets: return `"spotify_secret": "****"` not the actual value. Store a "configured: true/false" flag for the UI.
5. Never bake credentials into the Docker image at build time. The image on Docker Hub must be credential-free.

**Warning signs:**
- `docker inspect` reveals API keys in the environment section
- Application logs contain full Authorization headers
- Settings API endpoint returns actual credential values

**Phase to address:**
Phase 1 (Foundation) -- security patterns must be established in the initial configuration system.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Fetch all Plex tracks without pagination | Simpler code, works for small libraries | Breaks at 10k+ tracks, causes timeouts | Never -- pagination is simple to implement upfront |
| Store Spotify audio features in-memory only | Faster development, no DB schema needed | Every restart re-fetches all features, hits rate limits | Never -- the cache is essential |
| Hardcode mood-to-feature mappings | No LLM dependency, deterministic results | Rigid, cannot handle nuanced descriptions | MVP only -- replace with LLM interpretation in Phase 3 |
| Skip track match verification | Faster matching pipeline | Wrong tracks in playlists, erodes user trust | Never -- duration check is trivial to add |
| Use environment variables for all secrets | Simpler Docker Compose config | Exposed via inspect, leaked in logs | Only for initial local dev, switch to file mounts before release |
| Sync entire library on every startup | Ensures data freshness | 10k tracks = minutes of blocking sync every restart | Never -- implement delta sync from Phase 2 |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Spotify Search | Searching `track:Song Name artist:Artist Name` without normalization | Strip "(Remastered)", "(feat. X)", normalize case/accents, compare duration to verify |
| Spotify Audio Features | Fetching features one track at a time | Use batch endpoint with up to 100 IDs per request |
| Spotify Auth | Using Authorization Code flow when Client Credentials suffices | Audio features only need Client Credentials flow (no user login). Only use Auth Code if accessing user data. |
| Plex API | Creating playlists with `smart=1` flag incorrectly | Use regular (non-smart) playlists with explicit track lists via ratingKey. Smart playlists have filter bugs. |
| Plex API | Not handling the machineIdentifier correctly | Each Plex server has a unique machineIdentifier. Store it during setup and include in playlist API calls. |
| Plex API | Assuming track ratingKeys are stable across library rebuilds | ratingKeys can change if a library is deleted and re-added. Sync should handle re-mapping. |
| Lidarr API | Not handling timeout errors from api.lidarr.audio | Lidarr's internal API (for artist lookup/search) depends on the external api.lidarr.audio service, which frequently has 503/524 timeout errors. Implement retry with backoff and surface errors to the user. |
| Lidarr API | Assuming quality profiles have static IDs | Fetch quality profiles dynamically from `/api/v1/qualityprofile` -- do not hardcode IDs. They vary per installation. |
| Lidarr API | Missing the metadataProfileId requirement | Adding an artist requires both `qualityProfileId` AND `metadataProfileId`. Forgetting the latter causes a 400 error with an unhelpful message. |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Full library fetch from Plex on every request | Slow playlist generation, high memory use | Cache library in local DB, delta sync on schedule | 5k+ tracks |
| Unindexed audio feature queries | Mood filtering takes seconds instead of milliseconds | Index audio feature columns (energy, valence, tempo, danceability) in the database | 3k+ tracks |
| Fetching Spotify features during playlist generation | User waits minutes for a playlist | Pre-fetch and cache all features during sync, not during generation | Any library size |
| LLM API call on every playlist request with no caching | Slow response, high API costs, inconsistent results | Cache mood-to-parameters mappings for identical/similar prompts | Immediately noticeable |
| Loading all tracks into memory for filtering | Memory spikes, potential OOM in Docker container | Use database queries with WHERE clauses on feature ranges | 10k+ tracks |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Plex token in Docker image layers | Token accessible to anyone who pulls the image from Docker Hub | Use runtime secrets/env vars, never build-time ARGs for credentials |
| Settings API returns raw credentials | Anyone on the local network can extract all API keys | Redact secrets in API responses, only return "configured" boolean |
| No HTTPS for the web UI | Credentials sent in plaintext on local network | Document reverse proxy setup with HTTPS; do not send credentials over HTTP |
| Spotify client secret in frontend code | Secret visible in browser dev tools | Keep Spotify auth server-side only; frontend never touches client secret |
| Log files contain Authorization headers | Credentials persist in container logs | Sanitize all log output; never log auth headers or token values |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| No progress indicator during library sync | User thinks app is broken, refreshes, restarts | Show real-time progress: "Syncing library: 4,200 / 10,340 tracks" |
| Showing raw audio feature numbers to users | "energy: 0.73" means nothing to most users | Translate to natural language: "High energy", "Moderate danceability" |
| No way to see why a track was included in a playlist | User cannot understand or trust the system | Show match score breakdown: "Included because: high energy (0.8), upbeat mood (0.7)" |
| Requiring all API keys before any functionality works | User bounces during setup if they do not have all credentials ready | Progressive setup: Plex first (core), then Spotify (features), then Lidarr (optional). Each unlocks more functionality. |
| Mood input as a single text field with no guidance | Users do not know what to type, get bad results | Provide example prompts, mood presets/quick-picks, and parameter preview |
| Generating a playlist then losing it if user navigates away | Frustrating loss of work | Auto-save generated playlists to history before the user explicitly pushes to Plex |

## "Looks Done But Isn't" Checklist

- [ ] **Library sync:** Handles tracks with missing/incomplete metadata (no artist, no album) -- verify with edge-case tracks
- [ ] **Track matching:** Handles tracks not on Spotify at all -- verify these are gracefully skipped, not errors
- [ ] **Playlist push to Plex:** Handles duplicate track names in the same playlist -- verify Plex accepts them
- [ ] **Mood interpretation:** Handles nonsensical input ("asdfjkl") -- verify the system returns a helpful error, not a crash
- [ ] **Lidarr add artist:** Handles artist already in Lidarr -- verify it detects existing artists and does not create duplicates
- [ ] **Settings page:** Handles invalid API keys -- verify the app validates credentials on save and shows clear error messages
- [ ] **Docker container:** Handles restart gracefully -- verify cached data persists (volume mounts configured), sync state is preserved
- [ ] **Rate limiting:** Handles mid-sync interruption -- verify sync resumes from where it left off, not from scratch
- [ ] **Audio features cache:** Handles Spotify returning partial results in a batch -- verify missing tracks are retried individually

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Spotify revokes API access | MEDIUM | Switch to cached features for existing tracks; implement Essentia fallback for new tracks; no new Spotify matching until access restored |
| Corrupted track match data | LOW | Re-run matching pipeline for affected tracks; matching is idempotent |
| Plex ratingKeys changed after library rebuild | MEDIUM | Detect stale ratingKeys (404 on push), trigger full re-sync of Plex library IDs |
| Rate limit ban (24-hour) | LOW | Wait it out; sync resumes automatically if built with resumability |
| Mood engine producing bad results | LOW | Adjust prompt engineering and feature range presets; no data migration needed |
| Credentials leaked in logs | HIGH | Rotate all affected API keys/tokens immediately; audit log files; implement log sanitization |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Spotify API access uncertainty | Phase 1 (Foundation) | Can successfully call `/v1/audio-features` for a test track before proceeding |
| Track matching quality | Phase 2 (Spotify Integration) | Match rate above 80% for a test library of 100 known tracks |
| Tight coupling to Spotify | Phase 1 (Architecture) | AudioFeaturesProvider interface exists with at least one implementation |
| Plex sync performance | Phase 2 (Plex Integration) | 10k track library syncs in under 60 seconds with pagination |
| Spotify rate limiting | Phase 2 (Spotify Integration) | Full 10k library feature fetch completes without 429 errors |
| Mood interpretation quality | Phase 3 (Mood Engine) | 5 standard mood descriptions produce sensible, consistent playlists |
| Credential security | Phase 1 (Foundation) | `docker inspect` shows no credentials; settings API returns redacted values |
| Progressive setup UX | Phase 1 (Foundation) | App is usable with only Plex configured; Spotify/Lidarr unlock additional features |

## Sources

- [Spotify Web API Changes (Nov 2024)](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [Spotify Extended Access Criteria Update (Apr 2025)](https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access)
- [Spotify Dev Mode Changes (Feb 2026)](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Spotify Developer Mode Requires Premium (TechCrunch)](https://techcrunch.com/2026/02/06/spotify-changes-developer-mode-api-to-require-premium-accounts-limits-test-users/)
- [Spotify Rate Limits Documentation](https://developer.spotify.com/documentation/web-api/concepts/rate-limits)
- [Spotify Search API Exact Match Issue](https://github.com/spotify/web-api/issues/138)
- [Python PlexAPI Playlist Documentation](https://python-plexapi.readthedocs.io/en/stable/modules/playlist.html)
- [Plex Smart Playlist Creation Issues](https://github.com/pkkid/python-plexapi/issues/551)
- [Lidarr API Communication Errors](https://github.com/Lidarr/Lidarr/issues/5498)
- [Lidarr API Docs](https://lidarr.audio/docs/api/)
- [Pyarr Lidarr Documentation](https://docs.totaldebug.uk/pyarr/modules/lidarr.html)
- [Docker Secrets Best Practices](https://docs.docker.com/compose/how-tos/use-secrets/)
- [Docker Secrets Security Guide](https://blog.gitguardian.com/how-to-handle-secrets-in-docker/)
- [Essentia Audio Analysis Library](https://essentia.upf.edu/)
- [MusicBrainz ISRC Matching Issues](https://blog.ortham.net/posts/2025-01-04-spotify-streaming-history-part-3/)

---
*Pitfalls research for: Self-hosted mood-based playlist generation (Composer)*
*Researched: 2026-04-09*
