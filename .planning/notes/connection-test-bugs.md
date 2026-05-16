---
type: bug-note
created: 2026-04-09
priority: high
---

# Ollama and Lidarr Connection Tests Failing

**Reported during:** Phase 3 session
**Affects:** Phase 4 (Ollama) and Phase 6 (Lidarr)

Both Ollama and Lidarr connection tests are failing in the deployed app settings page. The services are running but the test connection flow doesn't work.

## To investigate
- Ollama: Is the URL correct? Should be `http://composer-ollama:11434` (Docker internal network name) not `http://localhost:11434`
- Lidarr: Is the API key correct? Is the URL reachable from inside the Composer container?
- Could be a Docker networking issue — Composer container may not resolve sibling container hostnames depending on compose network config

## When to fix
- Ollama: Must work before Phase 4 (playlist generation depends on it)
- Lidarr: Must work before Phase 6 (artist discovery depends on it)
- Ideally fix both at the start of Phase 4's discuss/planning
