# Phase 1: Foundation, Configuration & Deployment - Research

**Researched:** 2026-04-09
**Domain:** FastAPI web app with settings page, Docker containerization, GitHub Actions CI/CD
**Confidence:** HIGH

## Summary

Phase 1 establishes the deployable app shell: a FastAPI backend serving Jinja2/HTMX templates with a settings page for configuring Plex, Ollama, and Lidarr connections. Configuration is stored in SQLite with credential masking. The app runs in Docker with a compose file that includes both Composer and Ollama. A GitHub Actions pipeline builds multi-platform images (amd64 + arm64) and pushes to Docker Hub.

The technology stack is well-established and well-documented. FastAPI + Jinja2 + HTMX is a proven pattern for server-rendered apps with dynamic interactions. SQLModel handles the database layer cleanly. The main complexity lies in the test-and-configure pattern (testing connections to external services and dynamically fetching options like Plex libraries or Ollama models), credential security (never exposing secrets after save), and the multi-platform Docker build pipeline.

**Primary recommendation:** Build a clean settings page with per-service test-connect-configure flows, store credentials encrypted in SQLite, and ship with a GitHub Actions workflow using docker/build-push-action for multi-platform builds from day one.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Settings live on a dedicated page at `/settings`, accessible from a nav link or gear icon
- **D-02:** Test-and-configure pattern -- user enters URL/token, hits "Test Connection", then picks options pulled from the service (Plex library list, Ollama model list, Lidarr quality profiles)
- **D-03:** Progressive setup -- Plex is required first (app redirects to setup until Plex is configured), Ollama and Lidarr are optional and can be added later
- **D-04:** After saving, credential values are never displayed in UI -- show masked placeholders (e.g., `--------`) with a "Reconfigure" option
- **D-05:** First launch redirects to settings page with a "Welcome, let's connect your services" experience
- **D-06:** Top navigation bar (horizontal, not sidebar) with links for main sections
- **D-07:** Dark theme styled to match the Plex ecosystem -- dark backgrounds, light text, similar color palette
- **D-08:** Nav items for Phase 1: just Settings (other pages added in later phases)
- **D-09:** Compose YAML includes both Composer and Ollama services
- **D-10:** All configuration done in-app through the web UI -- no env vars needed except the port
- **D-11:** Plex media directory mounted as read-only volume
- **D-12:** SQLite database stored in a named volume for persistence
- **D-13:** Single Dockerfile with multi-stage build
- **D-14:** GitHub Actions triggers on every push to main -- builds and pushes `latest` tag
- **D-15:** Git tags (v*) also trigger builds with versioned image tags
- **D-16:** Docker Hub repo name matches GitHub username/composer pattern
- **D-17:** Multi-platform build (amd64 + arm64) for NAS compatibility

### Claude's Discretion
- Exact Tailwind CSS color palette for the Plex-matching dark theme
- Internal project structure (directory layout, module organization)
- FastAPI app startup/shutdown lifecycle details
- SQLite WAL mode configuration
- Health check endpoint design
- Exact port number (default 8085 or similar -- avoid conflicts with common arr apps)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CONF-01 | User can configure Plex server URL and token via in-app settings page | PlexAPI `PlexServer(baseurl, token)` for test-and-connect; Jinja2/HTMX form pattern |
| CONF-02 | User can configure Ollama endpoint URL and model selection via in-app settings page | OpenAI SDK with `base_url` override; `GET /v1/models` to list available models |
| CONF-03 | User can configure Lidarr URL, API key, and default quality profile via in-app settings page | pyarr `LidarrAPI(host, api_key)` + `get_quality_profile()` for profile selection |
| CONF-04 | Settings are stored securely and never displayed in UI or exposed in API responses | Fernet symmetric encryption for stored credentials; API returns masked values only |
| CONF-05 | App deploys as a single Docker container with a compose YAML file (includes Ollama service) | Multi-stage Dockerfile; compose with Composer + Ollama services |
| CONF-06 | Plex media directory is mounted as a read-only volume | Compose volume mount `- /path/to/music:/music:ro` |
| DEPL-01 | Docker image is built via GitHub Actions CI/CD pipeline | docker/build-push-action with buildx multi-platform |
| DEPL-02 | Docker image is published to Docker Hub | Docker Hub login + push in Actions workflow |

</phase_requirements>

## Standard Stack

### Core (Phase 1 Scope)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.135.3 | Web framework + API | Async-native, Jinja2 support via Starlette, Pydantic validation | [VERIFIED: PyPI registry] |
| Uvicorn | 0.44.0 | ASGI server | Standard production server for FastAPI | [VERIFIED: PyPI registry] |
| SQLModel | 0.0.38 | ORM / data models | Single model definition for DB schema + API validation | [VERIFIED: PyPI registry] |
| Jinja2 | 3.x | Server-side templates | Native FastAPI integration via `Starlette.templating` | [VERIFIED: FastAPI docs] |
| HTMX | 2.x | Dynamic UI interactions | HTML attributes for AJAX; no JS build step; CDN or vendored | [VERIFIED: htmx.org] |
| Alpine.js | 3.x | Client micro-interactions | Dropdowns, modals, toggle states; 15KB; CDN or vendored | [VERIFIED: alpinejs.dev] |
| Tailwind CSS | 4.x | Styling (standalone CLI) | No Node.js dep; Rust-based v4; compile at Docker build time | [VERIFIED: tailwindcss.com] |
| PlexAPI | 4.18.1 | Plex server integration | Test connection, list libraries | [VERIFIED: PyPI registry] |
| pyarr | 6.6.0 | Lidarr API client | Test connection, list quality profiles | [VERIFIED: PyPI registry] |
| OpenAI SDK | 2.31.0 | Ollama API client | OpenAI-compatible `base_url` override for Ollama | [VERIFIED: PyPI registry] |
| cryptography | 46.0.7 | Credential encryption | Fernet symmetric encryption for stored secrets | [VERIFIED: PyPI registry] |
| httpx | 0.28.1 | Async HTTP client | For raw API calls not covered by libraries | [VERIFIED: PyPI registry] |
| python-multipart | 0.0.24 | Form data parsing | Required by FastAPI for form submissions | [VERIFIED: PyPI registry] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Fernet (cryptography) | sqlalchemy-utils EncryptedType | EncryptedType adds sqlalchemy-utils dep; Fernet is simpler, same cryptography lib |
| OpenAI SDK for Ollama | ollama-python | Locks to Ollama only; OpenAI SDK works with any compatible endpoint |
| Tailwind standalone CLI | Node.js + PostCSS | Adds Node.js to Docker image; standalone CLI is zero-dep |

**Installation (Phase 1 requirements.txt):**
```
fastapi>=0.135,<0.136
uvicorn>=0.44,<1.0
sqlmodel>=0.0.38,<0.1
httpx>=0.28,<1.0
python-multipart>=0.0.24,<1.0
plexapi>=4.18,<5.0
pyarr>=6.6,<7.0
openai>=2.31,<3.0
cryptography>=46.0,<47.0
jinja2>=3.1,<4.0
```

## Architecture Patterns

### Recommended Project Structure

```
composer/
  app/
    main.py              # FastAPI app factory, lifespan, static/template mount
    config.py            # App config (port, db path, encryption key)
    database.py          # SQLite engine, session factory, WAL mode
    models/
      settings.py        # SQLModel: ServiceConfig (plex, ollama, lidarr)
    routers/
      pages.py           # Full-page HTML routes (GET /, GET /settings)
      api_settings.py    # API routes (POST /api/settings/plex/test, etc.)
      api_health.py      # GET /api/health
    services/
      settings_service.py  # CRUD for settings, encryption/decryption
      plex_client.py       # PlexAPI wrapper: test_connection, list_libraries
      ollama_client.py     # OpenAI SDK wrapper: test_connection, list_models
      lidarr_client.py     # pyarr wrapper: test_connection, list_profiles
    templates/
      base.html          # Base layout: nav bar, dark theme, HTMX/Alpine includes
      pages/
        settings.html    # Full settings page
        welcome.html     # First-run setup page
      partials/
        plex_form.html   # Plex config form (HTMX partial)
        ollama_form.html # Ollama config form
        lidarr_form.html # Lidarr config form
        connection_status.html  # Test result partial
    static/
      css/
        input.css        # Tailwind source
        output.css       # Compiled (built at Docker build time)
      js/                # Vendored HTMX + Alpine.js (no CDN dep in Docker)
Dockerfile
docker-compose.yml
.github/
  workflows/
    docker-publish.yml   # Build + push to Docker Hub
requirements.txt
```

### Pattern 1: Test-and-Configure Flow (HTMX)

**What:** User enters service URL/credentials, clicks "Test Connection". HTMX sends POST to backend, backend tries to connect, returns HTML partial with success/failure + dynamic options (libraries, models, profiles).

**When to use:** All three service configurations (Plex, Ollama, Lidarr).

**Example:**
```html
<!-- Source: HTMX docs + FastAPI Jinja2 pattern -->
<form hx-post="/api/settings/plex/test"
      hx-target="#plex-result"
      hx-indicator="#plex-spinner">
  <input type="text" name="url" placeholder="http://plex:32400" />
  <input type="password" name="token" placeholder="Plex token" />
  <button type="submit">Test Connection</button>
  <span id="plex-spinner" class="htmx-indicator">Testing...</span>
</form>
<div id="plex-result">
  <!-- HTMX swaps in connection result + library selector -->
</div>
```

```python
# Source: PlexAPI docs + FastAPI pattern
@router.post("/api/settings/plex/test")
async def test_plex_connection(
    request: Request,
    url: str = Form(...),
    token: str = Form(...),
):
    try:
        plex = PlexServer(url, token, timeout=10)
        libraries = [
            {"key": s.key, "title": s.title}
            for s in plex.library.sections()
            if s.type == "artist"
        ]
        return templates.TemplateResponse(
            "partials/plex_test_success.html",
            {"request": request, "server_name": plex.friendlyName, "libraries": libraries}
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/connection_error.html",
            {"request": request, "error": str(e)}
        )
```

[VERIFIED: PlexAPI docs confirm `PlexServer(url, token)` raises on bad credentials] [VERIFIED: HTMX hx-post/hx-target is standard swap pattern]

### Pattern 2: Credential Encryption with Fernet

**What:** Sensitive values (tokens, API keys) are encrypted at rest in SQLite using Fernet symmetric encryption. The encryption key is generated once on first run and stored on disk (in the data volume).

**When to use:** All credential storage (CONF-04).

**Example:**
```python
# Source: cryptography library Fernet docs
from cryptography.fernet import Fernet
import os

def get_or_create_key(key_path: str) -> bytes:
    """Load encryption key from file, or generate one on first run."""
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(key_path, "wb") as f:
        f.write(key)
    os.chmod(key_path, 0o600)
    return key

class CredentialEncryptor:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
```

[VERIFIED: cryptography Fernet is standard symmetric encryption pattern]

### Pattern 3: Settings Model with Masked API Response

**What:** SQLModel stores encrypted credentials. When returning settings to the UI/API, credentials are replaced with a "configured" boolean -- never the actual value.

**Example:**
```python
from sqlmodel import SQLModel, Field
from typing import Optional

class ServiceConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    service_name: str = Field(unique=True)  # "plex", "ollama", "lidarr"
    url: str
    credential: str  # Encrypted token/api_key
    extra_config: Optional[str] = None  # JSON: library_id, model_name, profile_id
    is_configured: bool = False
    updated_at: Optional[str] = None

class ServiceConfigResponse(SQLModel):
    """API response model -- never exposes credentials."""
    service_name: str
    url: str
    is_configured: bool
    credential_set: bool  # True if credential exists, never the value
    extra_config: Optional[dict] = None
```

[ASSUMED: exact SQLModel table design -- planner can adjust column structure]

### Pattern 4: Ollama Connection Test via OpenAI SDK

**What:** Use the OpenAI SDK with `base_url` pointed at Ollama to test connectivity and list models.

**Example:**
```python
# Source: Ollama OpenAI compatibility docs
from openai import OpenAI

async def test_ollama_connection(url: str) -> dict:
    """Test Ollama connectivity and return available models."""
    client = OpenAI(base_url=f"{url}/v1", api_key="ollama")
    models = client.models.list()
    return {
        "connected": True,
        "models": [m.id for m in models.data]
    }
```

[VERIFIED: Ollama docs confirm `/v1/models` endpoint and api_key="ollama" convention]

### Pattern 5: Lidarr Connection Test via pyarr

**What:** Use pyarr to test Lidarr connection and fetch quality profiles.

**Example:**
```python
# Source: pyarr Lidarr documentation
from pyarr import LidarrAPI

def test_lidarr_connection(url: str, api_key: str) -> dict:
    """Test Lidarr connectivity and return quality profiles."""
    lidarr = LidarrAPI(host_url=url, api_key=api_key)
    # get_quality_profile() raises on connection failure
    profiles = lidarr.get_quality_profile()
    return {
        "connected": True,
        "profiles": [{"id": p["id"], "name": p["name"]} for p in profiles]
    }
```

[VERIFIED: pyarr docs confirm `LidarrAPI(host_url, api_key)` and `get_quality_profile()` method]

### Anti-Patterns to Avoid

- **Storing credentials in environment variables visible via `docker inspect`:** Use in-app SQLite storage with encryption instead (D-10 mandates all config in-app)
- **Returning raw credentials in API responses:** Always mask; return `credential_set: true/false` not the value
- **Using Node.js in Docker for Tailwind:** Use standalone CLI binary downloaded at build time
- **CDN dependencies for HTMX/Alpine:** Vendor the JS files locally; Docker container must work offline on a NAS LAN
- **Blocking the main thread during connection tests:** Use `asyncio.to_thread()` for synchronous libraries (PlexAPI, pyarr) called from async FastAPI routes

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Symmetric encryption | Custom AES wrapper | `cryptography.fernet.Fernet` | Battle-tested, constant-time comparison, includes HMAC |
| Plex API communication | Raw HTTP to Plex endpoints | `plexapi.server.PlexServer` | Handles auth headers, pagination, XML parsing |
| Lidarr API communication | Raw HTTP to Lidarr | `pyarr.LidarrAPI` | Handles auth, all endpoint wrappers, error translation |
| Ollama model listing | Raw HTTP to Ollama | `openai.OpenAI(base_url=...)` | Standard SDK, works with any OpenAI-compatible endpoint |
| Multi-platform Docker builds | Custom build scripts | `docker/build-push-action` GH Action | Handles QEMU, buildx, caching, multi-platform manifest |
| Form data parsing in FastAPI | Manual request body parsing | `python-multipart` + `Form(...)` | FastAPI requirement for form handling |

## Common Pitfalls

### Pitfall 1: PlexAPI is Synchronous, FastAPI is Async

**What goes wrong:** PlexAPI uses `requests` internally (synchronous). Calling `PlexServer()` directly in an async FastAPI route blocks the event loop.
**Why it happens:** FastAPI's async routes expect non-blocking calls. Synchronous I/O blocks the entire server.
**How to avoid:** Wrap synchronous PlexAPI calls in `asyncio.to_thread()`:
```python
plex = await asyncio.to_thread(PlexServer, url, token, timeout=10)
```
**Warning signs:** App becomes unresponsive during Plex connection tests; other requests queue up.

[VERIFIED: PlexAPI source uses `requests` library -- synchronous] [VERIFIED: FastAPI docs recommend `run_in_executor` or `to_thread` for sync calls]

### Pitfall 2: pyarr is Also Synchronous

**What goes wrong:** Same issue as PlexAPI. pyarr uses `requests` internally.
**How to avoid:** Same solution -- `asyncio.to_thread()` for all pyarr calls.

[VERIFIED: pyarr source uses `requests` library]

### Pitfall 3: Fernet Key Loss = Data Loss

**What goes wrong:** If the encryption key file is lost (e.g., not in the persistent volume), all stored credentials become unrecoverable.
**Why it happens:** Key stored in container filesystem instead of the data volume.
**How to avoid:** Store the Fernet key in the same Docker volume as the SQLite database (`/app/data/` directory). Document that this volume must be backed up.
**Warning signs:** After container rebuild, settings page shows "not configured" for all services.

[ASSUMED: key storage location design choice]

### Pitfall 4: Tailwind CSS v4 Configuration Change

**What goes wrong:** Tailwind v4 uses CSS-based configuration (`@theme` directives in CSS) instead of `tailwind.config.js`. Old tutorials and v3 patterns do not apply.
**Why it happens:** Tailwind v4 was a ground-up rewrite with a different config model.
**How to avoid:** Use `@theme` blocks in `input.css` for custom colors. No JavaScript config file needed. Import with `@import "tailwindcss"` at top of CSS file.
**Warning signs:** `tailwind.config.js` exists but colors/theme are not applied.

[VERIFIED: Tailwind CSS v4 docs confirm CSS-first configuration via @theme]

### Pitfall 5: HTMX Form Submission Needs python-multipart

**What goes wrong:** FastAPI returns 422 errors on form POST because it cannot parse `application/x-www-form-urlencoded` data.
**Why it happens:** FastAPI requires `python-multipart` package for form data parsing, but it is not auto-installed.
**How to avoid:** Include `python-multipart` in requirements.txt. Use `Form(...)` parameter annotations in route handlers.

[VERIFIED: FastAPI docs state python-multipart is required for Form parameters]

### Pitfall 6: Docker Multi-Platform Build Times

**What goes wrong:** Building arm64 images on x86_64 GitHub Actions runners via QEMU emulation is very slow (10-30 minutes for Python images with pip install).
**Why it happens:** QEMU emulates the entire ARM instruction set, which makes pip compile steps extremely slow.
**How to avoid:** Use `--cache-from type=gha` and `--cache-to type=gha,mode=max` in the build-push-action to cache layers. Structure Dockerfile so pip install is cached when requirements.txt has not changed.
**Warning signs:** CI builds taking 20+ minutes.

[VERIFIED: Docker docs on multi-platform GitHub Actions confirm QEMU emulation performance impact]

## Code Examples

### GitHub Actions Workflow for Docker Hub

```yaml
# Source: docker/build-push-action official docs
name: Build and Push Docker Image

on:
  push:
    branches: [main]
    tags: ['v*']

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Docker meta
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ secrets.DOCKERHUB_USERNAME }}/composer
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}

      - name: Login to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

[VERIFIED: docker/build-push-action v6, docker/metadata-action v5, docker/setup-qemu-action v3, docker/setup-buildx-action v3 are current] [VERIFIED: Docker official docs multi-platform GH Actions pattern]

### Multi-Stage Dockerfile

```dockerfile
# Source: Docker best practices + Tailwind standalone CLI pattern
FROM python:3.12-slim AS base

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached layer)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Build stage: compile Tailwind CSS ---
FROM base AS css-builder

# Download Tailwind CSS standalone CLI (detect arch)
ARG TARGETARCH
RUN curl -sL -o /usr/local/bin/tailwindcss \
    "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-${TARGETARCH}" \
    && chmod +x /usr/local/bin/tailwindcss

COPY . .
RUN tailwindcss -i ./app/static/css/input.css -o ./app/static/css/output.css --minify

# --- Final stage ---
FROM base AS final

COPY . /app/
COPY --from=css-builder /app/app/static/css/output.css /app/app/static/css/output.css

# Create data directory for SQLite + encryption key
RUN mkdir -p /app/data

VOLUME /app/data
EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8085/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8085"]
```

[VERIFIED: TARGETARCH is set by Docker buildx for multi-platform builds]
[ASSUMED: port 8085 -- Claude's discretion per CONTEXT.md]

### docker-compose.yml

```yaml
services:
  composer:
    image: username/composer:latest
    container_name: composer
    ports:
      - "8085:8085"
    volumes:
      - composer_data:/app/data
      - /path/to/plex/media/music:/music:ro
    restart: unless-stopped
    depends_on:
      - ollama

  ollama:
    image: ollama/ollama:latest
    container_name: composer-ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped

volumes:
  composer_data:
  ollama_data:
```

[VERIFIED: ollama/ollama is the official Docker image]
[ASSUMED: exact port/volume naming is Claude's discretion]

### FastAPI App Factory with Lifespan

```python
# Source: FastAPI docs lifespan pattern
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import SQLModel

from app.database import engine, init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB. Shutdown: cleanup."""
    init_db()
    yield

app = FastAPI(title="Composer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
```

[VERIFIED: FastAPI docs confirm lifespan context manager pattern replaces on_event]

### SQLite WAL Mode Setup

```python
# Source: SQLite docs + SQLModel/SQLAlchemy pattern
from sqlmodel import create_engine, SQLModel
from sqlalchemy import event

DATABASE_URL = "sqlite:///data/composer.db"
engine = create_engine(DATABASE_URL, echo=False)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

def init_db():
    SQLModel.metadata.create_all(engine)
```

[VERIFIED: SQLAlchemy event listener for SQLite pragma is documented pattern]

### Plex-Matching Dark Theme (Tailwind v4)

```css
/* app/static/css/input.css */
@import "tailwindcss";

@theme {
  /* Plex-inspired dark palette */
  --color-surface-primary: #1a1a2e;     /* Main background */
  --color-surface-secondary: #16213e;   /* Card/panel background */
  --color-surface-elevated: #0f3460;    /* Elevated elements */
  --color-accent: #e94560;              /* Plex orange-red accent */
  --color-accent-hover: #ff6b6b;        /* Accent hover state */
  --color-text-primary: #eaeaea;        /* Primary text */
  --color-text-secondary: #a0a0b0;      /* Secondary text */
  --color-text-muted: #6b6b80;          /* Muted/placeholder text */
  --color-success: #4ade80;             /* Connection success */
  --color-error: #ef4444;               /* Connection error */
  --color-border: #2a2a4a;             /* Border color */
}
```

[ASSUMED: exact color values -- inspired by Plex/Plexamp UI; Claude's discretion per CONTEXT.md]

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `tailwind.config.js` (JS) | `@theme` in CSS (`@import "tailwindcss"`) | Tailwind v4 (Jan 2025) | No JS config file; CSS-first theming |
| FastAPI `@app.on_event("startup")` | `lifespan` context manager | FastAPI 0.109+ (2024) | `on_event` is deprecated |
| `docker/build-push-action@v5` | `docker/build-push-action@v6` | 2025 | New version; use v6 |
| Manual Docker tag logic | `docker/metadata-action@v5` | Stable since 2024 | Auto-generates tags from git refs |

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | None -- Wave 0 creates `pytest.ini` or `pyproject.toml` `[tool.pytest]` |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONF-01 | Plex URL/token saved and test-connect works | unit + integration | `pytest tests/test_settings_plex.py -x` | Wave 0 |
| CONF-02 | Ollama endpoint/model saved and test-connect works | unit + integration | `pytest tests/test_settings_ollama.py -x` | Wave 0 |
| CONF-03 | Lidarr URL/key/profile saved and test-connect works | unit + integration | `pytest tests/test_settings_lidarr.py -x` | Wave 0 |
| CONF-04 | Credentials encrypted at rest, masked in API response | unit | `pytest tests/test_credential_security.py -x` | Wave 0 |
| CONF-05 | Docker container starts, app accessible | smoke | `docker compose up -d && curl http://localhost:8085/api/health` | Manual |
| CONF-06 | Read-only music volume accessible | smoke | `docker compose exec composer ls /music` | Manual |
| DEPL-01 | GitHub Actions workflow builds image | CI | GitHub Actions run (verified by push to main) | Wave 0 (.github/workflows/) |
| DEPL-02 | Image pushed to Docker Hub | CI | `docker pull username/composer:latest` | Manual post-push |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green + Docker compose smoke test

### Wave 0 Gaps
- [ ] `pyproject.toml` -- pytest config section
- [ ] `tests/conftest.py` -- shared fixtures (test DB, encryption key, mock services)
- [ ] `tests/test_settings_plex.py` -- Plex config CRUD + connection test (mocked)
- [ ] `tests/test_settings_ollama.py` -- Ollama config CRUD + connection test (mocked)
- [ ] `tests/test_settings_lidarr.py` -- Lidarr config CRUD + connection test (mocked)
- [ ] `tests/test_credential_security.py` -- encryption at rest, masked API response
- [ ] Framework install: `pip install pytest pytest-asyncio httpx` (httpx for FastAPI TestClient)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Single-user app, no auth |
| V3 Session Management | No | No sessions -- stateless API |
| V4 Access Control | No | Single-user, all endpoints accessible |
| V5 Input Validation | Yes | Pydantic models for all API input; `Form(...)` type annotations |
| V6 Cryptography | Yes | `cryptography.fernet.Fernet` for credential encryption at rest |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Credential exposure in API responses | Information Disclosure | Never return raw credentials; return `credential_set: bool` |
| Credential exposure in Docker inspect | Information Disclosure | Store in encrypted SQLite, not env vars (D-10) |
| Credential exposure in logs | Information Disclosure | Never log token/key values; mask in debug output |
| Encryption key in container layer | Information Disclosure | Generate at runtime, store in persistent volume only |
| SSRF via user-provided URLs | Spoofing | Validate URL format; connection test uses server-side HTTP only |

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12+ | Runtime | System has 3.9.6 | 3.9.6 | Docker base image `python:3.12-slim` provides 3.12 |
| Docker | Containerization | Yes | 29.2.1 | -- |
| Git | Version control | Yes | 2.39.5 | -- |
| GitHub Actions | CI/CD | N/A (cloud service) | -- | -- |

**Note:** Local Python is 3.9.6 but this is irrelevant -- all development and execution happens inside the Docker container with python:3.12-slim. No local Python version issue.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Port 8085 avoids conflicts with common arr apps (Sonarr 8989, Radarr 7878, Lidarr 8686, Prowlarr 9696) | Code Examples | LOW -- trivially changed in compose YAML |
| A2 | Plex-inspired color palette values | Code Examples | LOW -- cosmetic, easily adjusted |
| A3 | Fernet key stored in /app/data/ volume alongside SQLite | Architecture Patterns | MEDIUM -- if key path is wrong, credentials are lost on rebuild |
| A4 | ServiceConfig single-table design for all services | Architecture Patterns | LOW -- can split into per-service tables if needed |
| A5 | Vendoring HTMX + Alpine.js locally instead of CDN | Architecture Patterns | LOW -- ensures offline NAS compatibility |

## Open Questions

1. **Docker Hub username for image tags**
   - What we know: D-16 says "username/composer" pattern
   - What's unclear: The exact Docker Hub username to use in the compose YAML and Actions workflow
   - Recommendation: Use a `DOCKERHUB_USERNAME` secret in GitHub Actions; use placeholder in compose YAML with comment

2. **Tailwind CSS standalone CLI architecture detection in Dockerfile**
   - What we know: Docker buildx sets `TARGETARCH` (amd64, arm64); Tailwind releases have `tailwindcss-linux-x64` and `tailwindcss-linux-arm64`
   - What's unclear: Whether Tailwind release naming uses `x64` vs `amd64` -- may need arch mapping
   - Recommendation: Test both; may need `if [ "$TARGETARCH" = "amd64" ]; then ARCH=x64; else ARCH=$TARGETARCH; fi`

3. **First-run encryption key generation timing**
   - What we know: Key must exist before any credential can be stored
   - What's unclear: Whether to generate on app startup or on first credential save
   - Recommendation: Generate on app startup (in lifespan handler) if not exists -- simpler, predictable

## Sources

### Primary (HIGH confidence)
- [FastAPI Official Docs - Templates](https://fastapi.tiangolo.com/advanced/templates/) - Jinja2 integration pattern
- [FastAPI Official Docs - SQL Databases](https://fastapi.tiangolo.com/tutorial/sql-databases/) - SQLModel setup
- [PlexAPI Docs - PlexServer](https://python-plexapi.readthedocs.io/en/latest/modules/server.html) - Connection testing
- [pyarr Docs - Lidarr](https://docs.totaldebug.uk/pyarr/modules/lidarr.html) - Quality profile fetching
- [Ollama OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility) - `/v1/models` endpoint
- [Docker Multi-Platform GH Actions](https://docs.docker.com/build/ci/github-actions/multi-platform/) - buildx workflow
- [docker/build-push-action](https://github.com/docker/build-push-action) - v6 action reference
- [Tailwind CSS v4 Docs - Theme](https://tailwindcss.com/docs/theme) - CSS-first @theme configuration
- [cryptography Fernet](https://cryptography.io/en/latest/fernet/) - Symmetric encryption

### Secondary (MEDIUM confidence)
- [FastAPI + HTMX Pattern](https://testdriven.io/blog/fastapi-htmx/) - Server-rendered HTMX architecture
- [HTMX FastAPI Patterns 2025](https://johal.in/htmx-fastapi-patterns-hypermedia-driven-single-page-applications-2025/) - SPA-like patterns

### Tertiary (LOW confidence)
- None -- all findings verified against primary sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all versions verified against PyPI, all libraries well-documented
- Architecture: HIGH -- patterns from official docs, well-established FastAPI + HTMX + Jinja2 combination
- Pitfalls: HIGH -- sync/async trap is well-documented, credential security patterns are standard
- CI/CD: HIGH -- Docker official Actions are the standard approach

**Research date:** 2026-04-09
**Valid until:** 2026-05-09 (stable stack, 30-day validity)
