# Feature Research

**Domain:** Mood-based playlist generation with Plex/Lidarr/Spotify integration
**Researched:** 2026-04-09
**Confidence:** MEDIUM (Spotify API restrictions create significant uncertainty around core audio features approach)

## Critical Context: Spotify API Restrictions

Before evaluating features, a major constraint must be acknowledged. As of November 27, 2024, Spotify deprecated several key endpoints for new applications:

- **GET /audio-features** (energy, valence, tempo, danceability) -- RESTRICTED
- **GET /audio-analysis** -- RESTRICTED
- **GET /recommendations** -- RESTRICTED

Only apps with extended access approved before November 2024 retain access. New apps get 403 errors. As of April 2025, Spotify further tightened criteria, reserving extended access for apps that "drive platform strategy forward." This directly impacts the project's core pipeline (mood -> audio features -> filter library).

**Implications for Composer:** The project either needs (a) a pre-November-2024 Spotify app registration with extended access, (b) an alternative audio features source, or (c) a purely LLM-driven approach that bypasses audio features entirely. This is the single biggest risk to the project and must be resolved in Phase 1.

**Alternatives to Spotify audio features:**
- **Essentia** (open-source C++ library with Python bindings) -- self-hosted audio analysis that extracts energy, tempo, key, mood, danceability from actual audio files. Heavyweight but fully independent.
- **LLM-only approach** (what MediaSage does) -- skip quantitative audio features entirely; send track metadata (artist, album, genre, year) to an LLM and let it select tracks semantically. Simpler, but less precise for mood matching.
- **Hybrid approach** -- use LLM for interpretation + basic metadata filtering (genre, decade, artist style), with optional Essentia analysis for users who want deeper mood accuracy.

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Natural language mood/vibe input | Core value prop -- "turn a vibe into a playlist" | MEDIUM | LLM interprets mood into search criteria; this is what makes it not just a filter UI |
| Library sync with Plex | Must know what tracks the user owns | MEDIUM | One-time bulk sync + incremental updates; store in local SQLite. MediaSage reports ~2 min for 18k tracks |
| Library-aware results only | Every suggested track must exist in user's library | LOW | Hard constraint on output -- no suggesting tracks user doesn't own. This is the key differentiator vs Sonic Sage |
| Playlist push to Plex | Generated playlist must appear in Plex/Plexamp | LOW | Plex API supports playlist create/update/add items programmatically. Well-documented |
| Track count control | User specifies "give me 20 tracks" or "give me 50" | LOW | Simple parameter on generation request |
| Playlist review and edit before push | User can add/remove/reorder tracks before committing | MEDIUM | Drag-and-drop reordering, remove button, add-from-search. Standard playlist editor UI |
| Configuration page for API keys | Plex token, Spotify creds, Lidarr URL/key, LLM provider key | LOW | Form with secure storage; mask after entry; never expose in API responses |
| Docker deployment | Must run as single container with compose YAML | LOW | Standard for self-hosted arr-stack ecosystem |
| Playlist history | View previously generated playlists | LOW | Store generation history in SQLite with mood prompt, tracks, timestamp |

### Differentiators (Competitive Advantage)

Features that set Composer apart from Sonic Sage, MediaSage, and generic AI playlist tools.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Quantitative audio feature filtering | More precise mood matching than pure LLM guessing -- energy 0.7+, valence 0.3-0.5, etc. | HIGH | Depends on Spotify audio features access OR self-hosted Essentia analysis. Major differentiator if achievable, but highest-risk feature |
| AI + metadata hybrid filtering | LLM interprets mood, then filters by genre/decade/artist metadata before final selection | MEDIUM | This is achievable regardless of Spotify API status. MediaSage's "filter-first" architecture validates this pattern |
| New track suggestions for existing playlists | When library grows, suggest slotting new tracks into playlists that match their vibe | MEDIUM | Requires comparing new track characteristics against existing playlist profiles. Valuable for library growth |
| Artist discovery via Lidarr | Recommend artists similar to library contents; one-click add to Lidarr with quality profile | MEDIUM | Lidify and Aurral validate this market. Use Last.fm or Spotify similar artists for recommendations |
| Existing playlist analysis | Browse Plex playlists, see mood/energy breakdown, identify gaps | MEDIUM | "Your workout playlist is 80% high-energy but has 3 low-energy tracks that break flow" |
| Seed track mode | "Give me 20 tracks that feel like this one" | LOW | Use a specific track as the mood anchor. MediaSage has this; users love it |
| Multiple LLM provider support | OpenAI, Anthropic, Google, Ollama (local) | MEDIUM | MediaSage supports this; privacy-conscious users want local Ollama option. Also protects against provider lock-in |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| In-app playback/streaming | "I want to preview tracks" | Massive complexity (transcoding, audio streaming, DRM); Plex/Plexamp already does this perfectly | Deep link to Plexamp for playback; show a "play in Plexamp" button |
| Multi-user/auth system | "My family uses Plex too" | Adds auth, user management, playlist isolation -- huge scope for a personal tool | Single-user by design; each family member runs their own container if needed |
| Automatic downloading | "Auto-add to Lidarr and download" | Removes user agency; could fill disk with unwanted music; Lidarr already manages this workflow | One-click add artist to Lidarr wanted list; Lidarr handles acquisition |
| Real-time mood detection from current playback | "Detect my mood from what I'm listening to now" | Complex integration, unreliable, privacy concerns | Let user describe their mood -- they know it better than an algorithm |
| Spotify playlist import/sync | "Import my Spotify playlists" | Scope creep; different problem domain; tools like Tubifarry already handle this | Out of scope -- focus on generation, not migration |
| Full audio fingerprinting/analysis at scale | "Analyze all 10k tracks with Essentia" | Hours of CPU time; storage for feature data; maintenance burden | If using Essentia, analyze on-demand or in background batches, not as prerequisite to first use |
| Social/sharing features | "Share playlists with friends" | Single-user tool; sharing adds complexity with no clear value | Export as M3U or share the mood prompt text |
| Cross-platform push (Apple Music, YouTube Music) | "Also push to my other services" | Each platform is a separate integration; Plex is the target | Plex-only; keep scope tight |

## Feature Dependencies

```
[Plex Library Sync]
    +--required-by--> [Natural Language Mood Input]
    |                     +--required-by--> [Playlist Generation]
    |                                           +--required-by--> [Playlist Review/Edit]
    |                                           |                     +--required-by--> [Push to Plex]
    |                                           +--required-by--> [Playlist History]
    +--required-by--> [Existing Playlist Analysis]
    +--required-by--> [New Track Suggestions]

[API Key Configuration]
    +--required-by--> [Plex Library Sync]
    +--required-by--> [Push to Plex]
    +--required-by--> [Lidarr Artist Discovery]

[Audio Feature Data] (Spotify or Essentia)
    +--enhances--> [Playlist Generation] (precision)
    +--enhances--> [Existing Playlist Analysis] (quantitative breakdown)
    +--enhances--> [New Track Suggestions] (matching accuracy)

[Seed Track Mode] --enhances--> [Playlist Generation]

[Lidarr Artist Discovery] --independent-- (can be built in parallel)
```

### Dependency Notes

- **API Key Configuration must come first:** Nothing works without Plex token and LLM provider key at minimum
- **Plex Library Sync is the foundation:** Every feature depends on knowing what tracks exist in the library
- **Audio Feature Data is an enhancement, not a blocker:** The LLM + metadata approach works without audio features; Spotify/Essentia data makes it better but is not required for MVP
- **Lidarr integration is independent:** Can be built in parallel with playlist generation features; no shared dependencies beyond API configuration
- **Playlist editing depends on generation:** Can't edit what doesn't exist yet

## MVP Definition

### Launch With (v1)

Minimum viable product -- validate that mood-to-playlist generation works and is useful.

- [ ] API key configuration page (Plex, LLM provider) -- gating requirement for everything
- [ ] Plex library sync to local SQLite -- foundation for all features
- [ ] Natural language mood input with LLM interpretation -- core value prop
- [ ] Playlist generation using LLM + metadata filtering -- works without Spotify audio features
- [ ] Track count control -- basic customization
- [ ] Playlist review with add/remove/reorder -- user stays in control
- [ ] Push playlist to Plex -- completes the loop
- [ ] Playlist history -- know what you've generated before
- [ ] Docker deployment with compose YAML -- matches target deployment environment

### Add After Validation (v1.x)

Features to add once core generation is working and useful.

- [ ] Spotify audio features integration -- if API access is available; enriches mood matching precision
- [ ] Seed track mode -- "more like this" is a natural follow-on to basic mood generation
- [ ] Existing Plex playlist analysis -- understand what you already have
- [ ] New track suggestions for existing playlists -- library growth workflow
- [ ] Lidarr artist discovery with one-click add -- extends from "play what you have" to "find what you'd like"
- [ ] Multiple LLM provider support (OpenAI, Anthropic, Gemini, Ollama) -- start with one, expand

### Future Consideration (v2+)

Features to defer until product-market fit is established.

- [ ] Essentia-based self-hosted audio analysis -- heavyweight alternative if Spotify API remains inaccessible; significant infrastructure
- [ ] Playlist scheduling/automation -- "refresh my Monday Morning playlist weekly"
- [ ] Mood-based radio mode -- continuous playback queue that adapts, not just static playlists
- [ ] Library statistics dashboard -- "your library is 40% rock, 30% electronic, skews high-energy"

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Natural language mood input | HIGH | MEDIUM | P1 |
| Plex library sync | HIGH | MEDIUM | P1 |
| Playlist generation (LLM + metadata) | HIGH | MEDIUM | P1 |
| Playlist review/edit UI | HIGH | MEDIUM | P1 |
| Push to Plex | HIGH | LOW | P1 |
| API key configuration | HIGH | LOW | P1 |
| Docker deployment | HIGH | LOW | P1 |
| Playlist history | MEDIUM | LOW | P1 |
| Track count control | MEDIUM | LOW | P1 |
| Seed track mode | HIGH | LOW | P2 |
| Spotify audio features | HIGH | HIGH | P2 (risk-gated) |
| Lidarr artist discovery | MEDIUM | MEDIUM | P2 |
| Existing playlist analysis | MEDIUM | MEDIUM | P2 |
| New track suggestions | MEDIUM | MEDIUM | P2 |
| Multiple LLM providers | MEDIUM | MEDIUM | P2 |
| Essentia audio analysis | MEDIUM | HIGH | P3 |
| Playlist automation | LOW | MEDIUM | P3 |
| Library statistics | LOW | MEDIUM | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Sonic Sage (Plexamp) | MediaSage | Lidify | Composer (Our Approach) |
|---------|---------------------|-----------|--------|------------------------|
| Natural language input | Yes (ChatGPT) | Yes (multi-provider) | No | Yes (multi-provider) |
| Library-aware | Partial (also suggests non-owned via Tidal) | Yes (local SQLite) | N/A (artist-only) | Yes (local SQLite, strict library-only) |
| Audio feature filtering | No | No | No | Yes (if Spotify access available) |
| Playlist push to Plex | Yes (native) | Yes | No | Yes |
| Artist discovery | No | No | Yes (Last.fm/Spotify) | Yes (via Lidarr integration) |
| Playlist editing | No (take it or leave it) | No | N/A | Yes (full add/remove/reorder) |
| Existing playlist analysis | No | No | No | Yes (planned) |
| New track suggestions | No | No | No | Yes (planned) |
| Lidarr integration | No | No | Yes (core feature) | Yes |
| Self-hosted Docker | N/A (Plexamp feature) | Yes | Yes | Yes |
| Ollama/local LLM | No (OpenAI only) | Yes | N/A | Yes (planned) |
| Cost per playlist | ~$0.04 (OpenAI) | Free with Gemini free tier | Free | Depends on LLM provider |

**Key competitive gaps Composer fills:**
1. **Playlist editing** -- neither Sonic Sage nor MediaSage let you tweak results before pushing
2. **Audio feature precision** -- no competitor does quantitative mood filtering (if achievable)
3. **Lidarr integration** -- only Lidify does artist discovery, but it doesn't do playlists. Composer bridges both
4. **New track suggestions** -- no competitor proactively suggests where new tracks fit

## Sources

- [Spotify API changes announcement (Nov 2024)](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [Spotify extended access criteria update (Apr 2025)](https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access)
- [MediaSage GitHub](https://github.com/ecwilsonaz/mediasage) -- closest competitor, validates filter-first architecture
- [Lidify GitHub](https://github.com/TheWicklowWolf/Lidify) -- validates Lidarr artist discovery feature
- [Aurral GitHub](https://github.com/lklynet/aurral) -- validates artist discovery + Lidarr integration market
- [Cmdarr GitHub](https://github.com/DeviantEng/Cmdarr) -- validates Lidarr/Plex integration patterns
- [Plex API playlist creation](https://www.plexopedia.com/plex-media-server/api/playlists/create/)
- [Python PlexAPI documentation](https://python-plexapi.readthedocs.io/en/latest/modules/playlist.html)
- [Essentia audio analysis library](https://essentia.upf.edu/) -- self-hosted alternative to Spotify audio features
- [Sonic Sage / Plexamp AI playlists](https://www.techhive.com/article/1921927/how-to-use-plex-ai-music-playlists.html)
- [Spotify API restrictions impact article](https://voclr.it/news/why-spotify-has-restricted-its-api-access-what-changed-and-why-it-matters-in-2026/)

---
*Feature research for: Mood-based playlist generation with Plex/Lidarr/Spotify integration*
*Researched: 2026-04-09*
