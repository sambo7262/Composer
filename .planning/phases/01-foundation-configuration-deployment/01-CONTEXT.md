# Phase 1: Foundation, Configuration & Deployment - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Docker container running a FastAPI web app with a settings page for configuring Plex, Ollama, and Lidarr connections. Secure credential storage. GitHub Actions CI/CD pipeline building and pushing Docker image to Docker Hub. This phase delivers a deployable, configurable app shell — no playlist functionality yet.

</domain>

<decisions>
## Implementation Decisions

### Settings page flow
- **D-01:** Settings live on a dedicated page at `/settings`, accessible from a nav link or gear icon
- **D-02:** Test-and-configure pattern — user enters URL/token, hits "Test Connection", then picks options pulled from the service (Plex library list, Ollama model list, Lidarr quality profiles)
- **D-03:** Progressive setup — Plex is required first (app redirects to setup until Plex is configured), Ollama and Lidarr are optional and can be added later
- **D-04:** After saving, credential values are never displayed in UI — show masked placeholders (e.g., `••••••••`) with a "Reconfigure" option

### App shell & first run
- **D-05:** First launch redirects to settings page with a "Welcome, let's connect your services" experience
- **D-06:** Top navigation bar (horizontal, not sidebar) with links for main sections
- **D-07:** Dark theme styled to match the Plex ecosystem — dark backgrounds, light text, similar color palette to feel native alongside Plex/Plexamp
- **D-08:** Nav items for Phase 1: just Settings (other pages added in later phases as functionality lands)

### Docker & compose setup
- **D-09:** Compose YAML includes both Composer and Ollama services — one `docker compose up` starts everything
- **D-10:** All configuration done in-app through the web UI — no env vars needed except the port (keep compose file simple)
- **D-11:** Plex media directory mounted as read-only volume (path configured in compose YAML, used by Essentia in Phase 3)
- **D-12:** SQLite database stored in a named volume for persistence across container rebuilds
- **D-13:** Single Dockerfile with multi-stage build for the Composer app

### CI/CD pipeline
- **D-14:** GitHub Actions triggers on every push to main — builds and pushes `latest` tag to Docker Hub
- **D-15:** Git tags (v*) also trigger builds with versioned image tags
- **D-16:** Docker Hub repo name matches GitHub username/composer pattern
- **D-17:** Multi-platform build (amd64 + arm64) for NAS compatibility

### Claude's Discretion
- Exact Tailwind CSS color palette for the Plex-matching dark theme
- Internal project structure (directory layout, module organization)
- FastAPI app startup/shutdown lifecycle details
- SQLite WAL mode configuration
- Health check endpoint design
- Exact port number (default 8085 or similar — avoid conflicts with common arr apps)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above and in:
- `.planning/REQUIREMENTS.md` — CONF-01 through CONF-06, DEPL-01, DEPL-02
- `.planning/research/STACK.md` — Technology recommendations with versions
- `.planning/research/ARCHITECTURE.md` — Component boundaries and data flow
- `.planning/research/PITFALLS.md` — Credential security patterns, Docker best practices

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield project, first phase

### Established Patterns
- None yet — this phase establishes the foundational patterns

### Integration Points
- Plex API (PlexAPI library) — test connection and fetch library list
- Ollama API (OpenAI-compatible endpoint) — test connection and fetch model list
- Lidarr API (pyarr library) — test connection and fetch quality profiles
- Docker Hub — CI/CD push target
- GitHub Actions — CI/CD trigger

</code_context>

<specifics>
## Specific Ideas

- Dark theme should feel like it belongs in the Plex ecosystem — not a generic dark theme, but one that feels native alongside Plex/Plexamp/arr apps
- User runs this on a 32GB NAS alongside other Docker containers (Plex, Lidarr, etc.)
- User wants to iterate by pushing to GitHub, having Actions build the image, and pulling on the NAS — this is the primary development loop
- Keep the compose file simple — a user adding this to their existing stack should just add the YAML and go

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-foundation-configuration-deployment*
*Context gathered: 2026-04-09*
