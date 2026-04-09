# Phase 1: Foundation, Configuration & Deployment - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-09
**Phase:** 01-foundation-configuration-deployment
**Areas discussed:** Settings page flow, App shell & first run, Docker & compose setup, CI/CD pipeline

---

## Settings Page Flow

| Option | Description | Selected |
|--------|-------------|----------|
| Setup wizard | Step-by-step guided flow: Plex first, then Ollama, then Lidarr | |
| Single settings page | One page with sections for each service. Fill in what you want, save. | |
| You decide | Claude picks the best approach | |

**User's choice:** Asked clarification — "are the setups really just inputting values (ip/port/token/etc) or is it more than that?" After explanation that some steps benefit from testing connection and pulling options (library list, model list, quality profiles):

| Option | Description | Selected |
|--------|-------------|----------|
| Test & configure | Enter URL/token, hit 'Test Connection', then pick options pulled from the service | ✓ |
| Just fields | Plain form fields for everything. No connection testing. | |
| You decide | Claude picks based on what makes sense | |

**User's choice:** Test & configure

| Option | Description | Selected |
|--------|-------------|----------|
| Dedicated page | Full page at /settings with sections | ✓ |
| Slide-out panel | Settings slide in from the side, accessible from any page | |
| You decide | Claude picks the best UX pattern | |

**User's choice:** Dedicated page

---

## App Shell & First Run

| Option | Description | Selected |
|--------|-------------|----------|
| Redirect to setup | Automatically land on settings page with welcome message | ✓ |
| Show empty home | Show main app with a banner linking to settings | |
| You decide | Claude picks the smoothest first-run experience | |

**User's choice:** Redirect to setup

| Option | Description | Selected |
|--------|-------------|----------|
| Sidebar nav | Left sidebar with links, always visible | |
| Top nav bar | Horizontal nav across the top. Clean, minimal. | ✓ |
| You decide | Claude picks a clean layout | |

**User's choice:** Top nav bar

| Option | Description | Selected |
|--------|-------------|----------|
| Dark theme | Dark background, light text | |
| Light theme | White/light background | |
| Match Plex | Dark theme styled to feel like Plex ecosystem | ✓ |
| You decide | Claude picks something clean | |

**User's choice:** Match Plex

---

## Docker & Compose Setup

| Option | Description | Selected |
|--------|-------------|----------|
| Include Ollama | Compose has both composer and ollama services | ✓ |
| Ollama separate | Compose only has composer | |
| Both options | Ship two compose files | |

**User's choice:** Include Ollama

| Option | Description | Selected |
|--------|-------------|----------|
| Environment vars | Set port, DB path, log level via env vars. Service connections in-app. | |
| All in-app | Everything through web UI, nothing in env vars except port | ✓ |
| You decide | Claude picks standard pattern | |

**User's choice:** All in-app

---

## CI/CD Pipeline

| Option | Description | Selected |
|--------|-------------|----------|
| On tag only | Push a git tag to trigger build | |
| On main push | Every push to main builds and pushes latest. Tags also build versioned. | ✓ |
| You decide | Claude picks standard CI/CD pattern | |

**User's choice:** On main push

| Option | Description | Selected |
|--------|-------------|----------|
| Use GitHub username | e.g., yourusername/composer | ✓ |
| Custom name | Specify the Docker Hub repo name | |
| You decide | Claude picks sensible default | |

**User's choice:** Use GitHub username

---

## Claude's Discretion

- Exact Tailwind color palette for Plex-matching dark theme
- Internal project structure and module organization
- FastAPI lifecycle details
- SQLite WAL mode configuration
- Health check endpoint
- Default port number

## Deferred Ideas

None
