<!-- GSD:project-start source:PROJECT.md -->
## Project

**Composer**

A self-hosted web app that generates mood-based playlists from your personal music library using Spotify's audio features and AI interpretation. It integrates with Plex/Plexamp for playlist playback and Lidarr for discovering and adding new artists. Runs as a Docker container alongside your existing media stack.

**Core Value:** Turn a vibe description into a curated playlist from your own library — intelligently, without manual curation.

### Constraints

- **Deployment**: Must run as a single Docker container with compose YAML — no complex multi-service setup beyond what's needed
- **Security**: API keys must never be exposed in UI or API responses after initial configuration
- **Dependencies**: Requires Plex server, Lidarr instance, and Spotify developer credentials to function
- **Tech stack**: No preference stated — research should determine best fit
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Critical: Spotify API Access Restriction
- If you have an existing Spotify developer app created before Nov 27, 2024: **no problem** -- audio features are fully available
- If creating a new app: you must apply for extended quota access, which requires a registered business and 250K MAU (effectively blocks personal projects)
- **Mitigation strategy:** The app should be designed so that audio feature data, once fetched, is cached permanently in the local database. This minimizes API calls and means even limited access can bootstrap a full library over time.
- **Fallback:** If audio features are unavailable, the app should gracefully degrade to metadata-only matching (genre, artist, album era) with LLM interpretation. This is less precise but still functional.
- [Spotify Web API Changes Nov 2024](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [February 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [February 2026 Changelog](https://developer.spotify.com/documentation/web-api/references/changes/february-2026)
## Recommended Stack
### Core Framework
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Python** | 3.12+ | Runtime | Required by pyarr >=3.12; strong ecosystem for all integrations (Plex, Lidarr, Spotify, LLM). Single language for entire backend. | HIGH |
| **FastAPI** | 0.135.3 | Web framework / API | Async-native (ASGI), auto-generates OpenAPI docs, native Pydantic integration, 5x faster than Flask, built-in WebSocket support for future streaming. Same author as SQLModel -- deep integration. | HIGH |
| **Uvicorn** | latest | ASGI server | Standard production server for FastAPI. Lightweight, performant. | HIGH |
### Database
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **SQLite** | 3.x (system) | Primary database | Single-user app, single Docker container, no need for a database server. SQLite handles 10K+ track libraries easily. Persisted via Docker volume mount. Zero configuration. | HIGH |
| **SQLModel** | 0.0.38 | ORM | Built on SQLAlchemy + Pydantic by FastAPI's author. Single model definition serves as both DB schema and API schema -- eliminates duplication. Type-safe, excellent FastAPI integration. | HIGH |
### Frontend
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Jinja2** | 3.x | Server-side templates | FastAPI has native Jinja2 support via Starlette. Server-rendered HTML means no separate frontend build, no SPA complexity, no client-side state management. Perfect for a "clean, simple UI." | HIGH |
| **HTMX** | 2.x | Dynamic interactions | Replaces JavaScript SPA frameworks with HTML attributes. Server returns HTML fragments, HTMX swaps them into the DOM. Handles playlist editing, search results, real-time updates -- all without writing JavaScript. No build step. | HIGH |
| **Alpine.js** | 3.x | Client-side micro-interactions | 15KB. Handles dropdowns, modals, toggle states, drag-and-drop reordering -- things HTMX doesn't cover. Declared inline in HTML, no build step. Together with HTMX, covers 100% of Composer's UI needs. | HIGH |
| **Tailwind CSS** | 4.x | Styling | Utility-first CSS. Standalone CLI available (no Node.js required). v4 is a Rust-based rewrite -- 5x faster builds. Compile CSS during Docker build, ship static output. Clean, consistent styling with zero custom CSS. | HIGH |
### API Integrations
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Spotipy** | 2.26.0 | Spotify Web API client | The standard Python Spotify library. Mature (well-maintained), handles OAuth flow, supports all endpoints including audio-features. Lightweight wrapper, doesn't hide the API. | HIGH |
| **PlexAPI** | 4.18.1 | Plex server integration | Official community Python library for Plex. Handles auth, library browsing, playlist CRUD, metadata access. Requires Python >=3.10. Well-documented, actively maintained. | HIGH |
| **pyarr** | 6.6.0 | Lidarr API client | Supports Lidarr, Sonarr, Radarr, and the full arr ecosystem. Synchronous and async support. Actively maintained (March 2026 release). Requires Python >=3.12. | HIGH |
### AI / LLM Integration
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Instructor** | 1.15.1 | Structured LLM output | Wraps any LLM client (OpenAI, Anthropic, Ollama) with Pydantic model validation. Define a `MoodCriteria` Pydantic model, get structured output every time. Automatic retries on validation failure. 3M+ monthly downloads. | HIGH |
| **OpenAI Python SDK** | latest | Default LLM provider | OpenAI-compatible API format is the lingua franca. Works with OpenAI, Ollama, any OpenAI-compatible endpoint. Instructor uses it under the hood. | MEDIUM |
### Infrastructure
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Docker** | -- | Containerization | Required by project spec. Single-container deployment alongside arr stack. | HIGH |
| **python:3.12-slim** | -- | Base Docker image | Slim variant keeps image small (~150MB base). Includes everything needed for the Python stack. | HIGH |
| **GitHub Actions** | -- | CI/CD | Required by project spec. Build Docker image, push to Docker Hub. | HIGH |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **Pydantic** | 2.x | Data validation | Core dependency of FastAPI, SQLModel, and Instructor. Single validation layer across the entire app. |
| **python-dotenv** | latest | Environment config | Load `.env` file for local development. Docker uses environment variables directly. |
| **httpx** | latest | Async HTTP client | For any API calls not covered by dedicated libraries. FastAPI's recommended HTTP client (async-native). |
| **APScheduler** | latest | Background tasks | Periodic Plex library sync, Spotify audio feature fetching. Lightweight scheduler that runs in-process. |
## Alternatives Considered
| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Framework | FastAPI | Flask | Synchronous only; no native Pydantic; slower |
| Framework | FastAPI | Django | Overkill for single-user tool; heavy ORM/auth overhead |
| ORM | SQLModel | SQLAlchemy | SQLModel wraps it with better FastAPI/Pydantic integration |
| Database | SQLite | PostgreSQL | Requires separate container; overkill for single-user |
| Frontend | HTMX + Alpine.js | React/Vue/Svelte | Adds build pipeline, Node.js dep, SPA complexity |
| Frontend | HTMX + Alpine.js | Streamlit/Gradio | No control over UI; can't build playlist editing UX |
| Spotify | Spotipy | Raw requests | Auth flow handling, pagination, rate limiting built in |
| Lidarr | pyarr | lidarr-py | Auto-generated, less ergonomic, smaller community |
| LLM | Instructor + OpenAI SDK | LangChain | Absurd dependency graph for a simple structured extraction |
| LLM | Instructor + OpenAI SDK | LiteLLM | Over-abstraction; base_url override covers the use case |
| Styling | Tailwind CSS 4 | Bootstrap | Utility-first is more flexible; standalone CLI, no Node.js |
## Installation
# Core application
# Frontend (no npm -- Tailwind standalone CLI)
# Download tailwindcss standalone binary for your platform
# https://github.com/tailwindlabs/tailwindcss/releases
# Supporting
# Dev dependencies
## Docker Strategy
# Install Tailwind CSS standalone CLI
# Install Python dependencies
# Copy application
# Build Tailwind CSS
# Data persistence via volume
## Version Pin Strategy
## Sources
- [Spotify Web API Changes](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) - Endpoint restrictions for new apps
- [Spotify Feb 2026 Changelog](https://developer.spotify.com/documentation/web-api/references/changes/february-2026) - Audio features confirmed still available
- [PlexAPI PyPI](https://pypi.org/project/PlexAPI/) - v4.18.1, Python >=3.10
- [PlexAPI Docs](https://python-plexapi.readthedocs.io/) - Official documentation
- [pyarr PyPI](https://pypi.org/project/pyarr/) - v6.6.0, Python >=3.12
- [Spotipy PyPI](https://pypi.org/project/spotipy/) - v2.26.0
- [FastAPI PyPI](https://pypi.org/project/fastapi/) - v0.135.3
- [SQLModel PyPI](https://pypi.org/project/sqlmodel/) - v0.0.38
- [Instructor PyPI](https://pypi.org/project/instructor/) - v1.15.1
- [Instructor Docs](https://python.useinstructor.com/) - Structured LLM output with Pydantic
- [Tailwind CSS v4](https://tailwindcss.com/blog/tailwindcss-v4) - Standalone CLI, Rust rewrite
- [FastAPI Templates](https://fastapi.tiangolo.com/advanced/templates/) - Jinja2 integration
- [FastAPI + HTMX](https://testdriven.io/blog/fastapi-htmx/) - Server-rendered pattern
- [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs) - OpenAI-compatible endpoint
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
