# Project Research Summary

**Project:** Composer
**Domain:** Self-hosted music playlist generation (Plex/Lidarr/Spotify integration)
**Researched:** 2026-04-09
**Confidence:** MEDIUM-HIGH

## Executive Summary

Composer is a single-user, self-hosted tool that converts natural language mood descriptions into playlists drawn from a Plex music library. The dominant expert pattern — validated by MediaSage, Sonic Sage, and Lidify — is filter-first: sync the local library to SQLite, pre-compute audio features per track, then let an LLM interpret a mood description into feature targets and score tracks via weighted distance. The recommended stack is Python 3.12 + FastAPI + SQLModel + SQLite, with a server-rendered frontend (Jinja2 + HTMX + Alpine.js + Tailwind CSS standalone CLI) — no Node.js in Docker, no frontend build pipeline, one container.

The single biggest risk is Spotify's progressive API restriction. Audio features (`/audio-features`) are blocked for new apps since November 2024, and February 2026 further restricted developer mode to Premium accounts with 5-user limits. The architecture must treat audio features as coming from a pluggable `AudioFeaturesProvider` interface. The recommended primary source is **Essentia** (open-source C++ library with Python bindings), which extracts energy/tempo/danceability/valence from local audio files. Spotify becomes optional metadata enrichment only. This design survives further Spotify changes.

The build order is a strict dependency chain: config/Plex sync → audio feature extraction → mood generation → playlist push. Lidarr and extended Spotify integration are leaf nodes that can come later. Audio analysis via Essentia is CPU-intensive (1–3s per track; 3–8 hours for 10k tracks on first run) and must be a background job with progress tracking and resume capability from day one.

## Key Findings

### Recommended Stack

**Core technologies:**
- **Python 3.12 + FastAPI 0.135 + Uvicorn**: async-native; required by pyarr >=3.12; concurrent API calls require async
- **SQLModel 0.0.38 + SQLite**: single model definition for DB schema + API; zero-config single-container persistence
- **Jinja2 + HTMX 2.x + Alpine.js 3.x + Tailwind CSS 4 (standalone CLI)**: full interactive UI, no Node.js in Docker image
- **Spotipy 2.26 / PlexAPI 4.18 / pyarr 6.6**: battle-tested integration clients with auth/pagination/quirks handled
- **Instructor 1.15 + OpenAI SDK**: provider-agnostic structured LLM output; works with OpenAI, Anthropic, or Ollama
- **Essentia**: self-hosted audio feature extraction; no API dependency; requires read-only volume mount to Plex media directory
- **APScheduler + in-process asyncio jobs**: no Celery/Redis needed for single-container single-user tool

### Expected Features

**Must have (table stakes):**
- Progressive API key configuration (Plex first, then Spotify optional, then Lidarr optional)
- Plex library sync to SQLite (background job, paginated, delta sync)
- Natural language mood input with LLM interpretation
- Playlist generation (LLM + metadata/feature filtering) — works without Spotify
- Playlist review/edit (add/remove/reorder) before push — key differentiator vs competitors
- Push to Plex, track count control, playlist history
- Docker single-container deployment with compose YAML

**Should have (competitive):**
- Seed track mode ("20 tracks like this one")
- Lidarr artist discovery with one-click add
- Existing Plex playlist mood/energy analysis
- New track suggestions for existing playlists
- Multiple LLM provider support (OpenAI, Anthropic, Ollama)

**Defer (v2+):**
- Essentia full-library analysis (deferred unless Spotify access unavailable)
- Playlist scheduling/automation
- Library statistics dashboard
- Mood-based radio mode

### Architecture Approach

**Major components:**
1. **Plex Client** — paginated library sync, playlist CRUD, ratingKey management
2. **Audio Analyzer** (Essentia wrapper) — background CPU-intensive feature extraction; results cached permanently in SQLite
3. **Mood Interpreter** — LLM call via Instructor returning structured FeatureVector (energy/valence/tempo/danceability ranges)
4. **Playlist Generator** — weighted Euclidean distance scoring; numpy linear scan sufficient for <=50k tracks
5. **Background Job Manager** — in-process asyncio task tracker with progress API
6. **Integration Clients** — isolated wrappers for Plex, Spotify (metadata only), Lidarr, LLM providers
7. **SQLite Data Layer** — WAL mode; tracks, audio features, playlists, history, config

### Top Pitfalls

1. **Spotify audio features blocked for new apps** — validate in Phase 1; design AudioFeaturesProvider interface from day one
2. **Essentia analysis blocks API if synchronous** — background job with progress tracking, resume-on-restart, incremental-only
3. **Plex sync timeout at scale** — paginate from the start; delta sync; never full re-sync on startup
4. **Raw LLM output as DB query params** — always use Instructor + Pydantic FeatureTargets to validate and clamp
5. **Credentials exposed via docker inspect or logs** — file mounts > env vars; redacted settings API; sanitized logs — must be Phase 1

## Suggested Phase Structure

1. **Foundation** — Config, Plex sync, Docker skeleton, security patterns; Spotify access go/no-go validation
2. **Audio Feature Extraction** — Essentia background analysis pipeline; Spotify audio features (if access confirmed); feature cache in SQLite
3. **Core Playlist Generation** — LLM mood interpretation, feature distance scoring, playlist review/edit UI, push to Plex, history
4. **Extended Discovery Features** — Seed track mode, existing playlist analysis, new track suggestions, Lidarr artist discovery
5. **Multi-Provider LLM + Polish** — Ollama/Anthropic/Gemini support, GitHub Actions CI/CD, UX refinement

**Phase ordering rationale:** config → Plex sync → features → generation → push is a hard dependency chain. Lidarr and Spotify enrichment are leaf nodes with no downstream dependencies.

## Research Flags

**Needs research before planning:**
- **Phase 2:** Essentia Python bindings in `python:3.12-slim` Docker image — C++ build complexity, image size, multi-stage build patterns
- **Phase 4:** Last.fm similar artists API — terms of service and rate limits not researched

**Standard patterns (skip research-phase):**
- Phase 1: FastAPI + SQLModel + SQLite is extremely well-documented
- Phase 3: Instructor structured output is well-documented
- Phase 5: GitHub Actions Docker build/push is boilerplate

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All libraries verified on PyPI with current versions |
| Features | MEDIUM | Competitor analysis solid; Spotify API restriction scope still evolving |
| Architecture | MEDIUM-HIGH | Monolith + SQLite + background jobs pattern validated |
| Pitfalls | HIGH | Grounded in official Spotify changelogs, GitHub issues, community reports |

---
*Research completed: 2026-04-09*
