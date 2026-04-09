---
phase: 1
slug: foundation-configuration-deployment
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-09
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | none — Wave 0 installs |
| **Quick run command** | `python -m pytest tests/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | CONF-01 | — | N/A | integration | `python -m pytest tests/test_settings.py -k plex` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONF-02 | — | N/A | integration | `python -m pytest tests/test_settings.py -k ollama` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONF-03 | — | N/A | integration | `python -m pytest tests/test_settings.py -k lidarr` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONF-04 | T-1-01 | Credentials never in API response | unit | `python -m pytest tests/test_settings.py -k security` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONF-05 | — | N/A | unit | `python -m pytest tests/test_settings.py -k persist` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONF-06 | — | N/A | integration | `python -m pytest tests/test_settings.py -k media_dir` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEPL-01 | — | N/A | manual | GitHub Actions run check | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEPL-02 | — | N/A | manual | Docker Hub image pull check | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_settings.py` — stubs for CONF-01 through CONF-06
- [ ] `tests/conftest.py` — shared fixtures (test client, mock services)
- [ ] `pytest` + `httpx` install — test framework and async test client

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Docker compose up works | CONF-05 | Requires Docker runtime | Run `docker compose up -d`, verify app accessible at configured port |
| GitHub Actions builds image | DEPL-01 | Requires GitHub push | Push to main, verify Actions workflow completes |
| Image published to Docker Hub | DEPL-02 | Requires Docker Hub account | Check Docker Hub for latest tag after Actions run |
| Media directory accessible | CONF-06 | Requires Plex media volume | Mount a test directory, verify read access inside container |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
