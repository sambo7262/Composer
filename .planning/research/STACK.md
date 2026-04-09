# Technology Stack

**Project:** Composer
**Researched:** 2026-04-09

## Critical: Spotify API Access Restriction

**Confidence: HIGH** (verified via official Spotify developer docs)

Spotify restricted access to the `/audio-features` and `/audio-analysis` endpoints for **new apps created after November 27, 2024**. Apps created before that date with extended mode access retain full access. As of February 2026, dev mode apps are further limited to 5 users and require the app owner to have Spotify Premium.

**However:** The February 2026 changelog confirms audio-features and audio-analysis endpoints are **NOT removed** -- they remain operational. The restriction is on *new app access*, not endpoint removal.

**Implications for Composer:**
- If you have an existing Spotify developer app created before Nov 27, 2024: **no problem** -- audio features are fully available
- If creating a new app: you must apply for extended quota access, which requires a registered business and 250K MAU (effectively blocks personal projects)
- **Mitigation strategy:** The app should be designed so that audio feature data, once fetched, is cached permanently in the local database. This minimizes API calls and means even limited access can bootstrap a full library over time.
- **Fallback:** If audio features are unavailable, the app should gracefully degrade to metadata-only matching (genre, artist, album era) with LLM interpretation. This is less precise but still functional.

Sources:
- [Spotify Web API Changes Nov 2024](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [February 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [February 2026 Changelog](https://developer.spotify.com/documentation/web-api/references/changes/february-2026)

---

## Recommended Stack

### Core Framework

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Python** | 3.12+ | Runtime | Required by pyarr >=3.12; strong ecosystem for all integrations (Plex, Lidarr, Spotify, LLM). Single language for entire backend. | HIGH |
| **FastAPI** | 0.135.3 | Web framework / API | Async-native (ASGI), auto-generates OpenAPI docs, native Pydantic integration, 5x faster than Flask, built-in WebSocket support for future streaming. Same author as SQLModel -- deep integration. | HIGH |
| **Uvicorn** | latest | ASGI server | Standard production server for FastAPI. Lightweight, performant. | HIGH |

**Why not Flask:** Flask uses WSGI (synchronous). Composer makes multiple concurrent API calls (Plex, Spotify, Lidarr, LLM) per request -- async is not optional, it's essential. FastAPI's Pydantic integration also eliminates boilerplate for request/response validation.

**Why not Django:** Massive overkill. Composer is a single-user tool, not a CMS. Django's ORM, admin panel, auth system are all dead weight.

### Database

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **SQLite** | 3.x (system) | Primary database | Single-user app, single Docker container, no need for a database server. SQLite handles 10K+ track libraries easily. Persisted via Docker volume mount. Zero configuration. | HIGH |
| **SQLModel** | 0.0.38 | ORM | Built on SQLAlchemy + Pydantic by FastAPI's author. Single model definition serves as both DB schema and API schema -- eliminates duplication. Type-safe, excellent FastAPI integration. | HIGH |

**Why not PostgreSQL:** Requires a separate container or service. Composer is a single-container personal tool with one concurrent user. SQLite handles this workload with zero ops burden.

**Why not raw SQLAlchemy:** SQLModel wraps SQLAlchemy with Pydantic integration. You get the same power with less boilerplate. Can drop down to raw SQLAlchemy when needed.

### Frontend

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Jinja2** | 3.x | Server-side templates | FastAPI has native Jinja2 support via Starlette. Server-rendered HTML means no separate frontend build, no SPA complexity, no client-side state management. Perfect for a "clean, simple UI." | HIGH |
| **HTMX** | 2.x | Dynamic interactions | Replaces JavaScript SPA frameworks with HTML attributes. Server returns HTML fragments, HTMX swaps them into the DOM. Handles playlist editing, search results, real-time updates -- all without writing JavaScript. No build step. | HIGH |
| **Alpine.js** | 3.x | Client-side micro-interactions | 15KB. Handles dropdowns, modals, toggle states, drag-and-drop reordering -- things HTMX doesn't cover. Declared inline in HTML, no build step. Together with HTMX, covers 100% of Composer's UI needs. | HIGH |
| **Tailwind CSS** | 4.x | Styling | Utility-first CSS. Standalone CLI available (no Node.js required). v4 is a Rust-based rewrite -- 5x faster builds. Compile CSS during Docker build, ship static output. Clean, consistent styling with zero custom CSS. | HIGH |

**Why not React/Vue/Svelte:** Composer is a personal tool with a "clean, simple UI." An SPA framework adds: a build pipeline, client-side state management, API serialization layer, and Node.js in the Docker image. HTMX + Alpine.js + Jinja2 delivers the same UX with zero JavaScript build tooling and ~90% less client-side code.

**Why not plain CSS:** Tailwind's utility classes produce consistent, maintainable styling faster than writing custom CSS. The standalone CLI means no Node.js dependency in the Docker image.

### API Integrations

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Spotipy** | 2.26.0 | Spotify Web API client | The standard Python Spotify library. Mature (well-maintained), handles OAuth flow, supports all endpoints including audio-features. Lightweight wrapper, doesn't hide the API. | HIGH |
| **PlexAPI** | 4.18.1 | Plex server integration | Official community Python library for Plex. Handles auth, library browsing, playlist CRUD, metadata access. Requires Python >=3.10. Well-documented, actively maintained. | HIGH |
| **pyarr** | 6.6.0 | Lidarr API client | Supports Lidarr, Sonarr, Radarr, and the full arr ecosystem. Synchronous and async support. Actively maintained (March 2026 release). Requires Python >=3.12. | HIGH |

**Why not lidarr-py:** Auto-generated from OpenAPI spec -- less ergonomic, less community support. pyarr is battle-tested across the arr ecosystem and supports multiple arr services if Composer expands.

**Why not raw HTTP for Plex/Lidarr:** These APIs are XML/JSON with auth token management, pagination, and quirks. The libraries abstract this correctly and are maintained by their communities.

### AI / LLM Integration

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Instructor** | 1.15.1 | Structured LLM output | Wraps any LLM client (OpenAI, Anthropic, Ollama) with Pydantic model validation. Define a `MoodCriteria` Pydantic model, get structured output every time. Automatic retries on validation failure. 3M+ monthly downloads. | HIGH |
| **OpenAI Python SDK** | latest | Default LLM provider | OpenAI-compatible API format is the lingua franca. Works with OpenAI, Ollama, any OpenAI-compatible endpoint. Instructor uses it under the hood. | MEDIUM |

**Architecture decision: Provider-agnostic LLM layer**

Composer should support both cloud LLMs (OpenAI, Anthropic) and local LLMs (Ollama) via a single interface. The approach:

1. **Instructor** handles structured output extraction regardless of provider
2. **OpenAI SDK** is the transport layer (Ollama exposes OpenAI-compatible endpoints)
3. User configures provider URL + model name in settings (e.g., `http://localhost:11434/v1` for Ollama, `https://api.openai.com/v1` for OpenAI)

This means: no LiteLLM dependency (unnecessary abstraction when OpenAI SDK + base_url override handles the same thing), no vendor lock-in, works with whatever LLM the user already runs in their homelab.

**Why not LiteLLM:** LiteLLM is a 100+ provider proxy -- massive dependency for a project that needs exactly one provider at a time. The OpenAI SDK's `base_url` parameter achieves the same thing for OpenAI-compatible APIs (which includes Ollama).

**Why not direct Ollama Python client:** Locks you into Ollama. The OpenAI-compatible endpoint approach works with Ollama, vLLM, LM Studio, text-generation-webui, and any future local LLM runner.

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

```bash
# Core application
pip install fastapi uvicorn sqlmodel spotipy plexapi pyarr instructor openai

# Frontend (no npm -- Tailwind standalone CLI)
# Download tailwindcss standalone binary for your platform
# https://github.com/tailwindlabs/tailwindcss/releases

# Supporting
pip install httpx python-dotenv apscheduler jinja2

# Dev dependencies
pip install pytest pytest-asyncio ruff mypy
```

## Docker Strategy

```dockerfile
FROM python:3.12-slim

# Install Tailwind CSS standalone CLI
ADD https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64 /usr/local/bin/tailwindcss
RUN chmod +x /usr/local/bin/tailwindcss

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . /app
WORKDIR /app

# Build Tailwind CSS
RUN tailwindcss -i ./static/input.css -o ./static/output.css --minify

# Data persistence via volume
VOLUME /app/data

EXPOSE 8000
CMD ["uvicorn", "composer.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Version Pin Strategy

Pin major.minor in `requirements.txt`, allow patch updates:
```
fastapi>=0.135,<0.136
sqlmodel>=0.0.38,<0.1
spotipy>=2.26,<3.0
plexapi>=4.18,<5.0
pyarr>=6.6,<7.0
instructor>=1.15,<2.0
openai>=1.0,<2.0
```

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
