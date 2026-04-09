---
phase: 1
slug: foundation-configuration-deployment
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-09
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
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

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | Test File | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-----------|--------|
| 01-01-T1 | 01 | 1 | CONF-05 | T-1-01 | Credentials encrypted at rest | unit | `python -m pytest tests/test_database.py tests/test_encryption.py tests/test_settings_service.py -x -v` | tests/test_database.py, tests/test_encryption.py, tests/test_settings_service.py | ⬜ pending |
| 01-01-T2 | 01 | 1 | CONF-04, CONF-06 | T-1-01 | ServiceConfigResponse never exposes raw creds | unit+integration | `python -m pytest tests/test_health.py -x -v` | tests/test_health.py | ⬜ pending |
| 01-02-T1 | 02 | 2 | CONF-01, CONF-02, CONF-03 | T-1-07, T-1-08 | Connection tests use library clients; responses have no raw creds | unit+integration | `python -m pytest tests/test_service_clients.py tests/test_settings_api.py -x -v` | tests/test_service_clients.py, tests/test_settings_api.py | ⬜ pending |
| 01-02-T2 | 02 | 2 | CONF-01, CONF-02, CONF-03, CONF-04 | T-1-08, T-1-09 | No template renders raw credentials | integration+grep | `python -m pytest tests/ -x -v && grep -r "encrypted_credential" app/templates/ && exit 1 \|\| true` | tests/test_settings_api.py (template rendering) | ⬜ pending |
| 01-03-T1 | 03 | 2 | DEPL-01, DEPL-02 | T-1-11 | Secrets in GitHub encrypted secrets only | file check | `test -f .github/workflows/docker-publish.yml && grep "docker/build-push-action" .github/workflows/docker-publish.yml` | N/A (YAML linting) | ⬜ pending |
| 01-03-T2 | 03 | 2 | DEPL-01, DEPL-02 | — | N/A | manual | GitHub Actions run check + Docker Hub pull | N/A (manual checkpoint) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

All test files are created by the plans themselves — no separate Wave 0 stubs needed:

- [x] `tests/conftest.py` — Created by Plan 01, Task 1 (shared fixtures: tmp_data_dir, test_db, test_encryptor)
- [x] `tests/test_database.py` — Created by Plan 01, Task 1 (database init, WAL mode)
- [x] `tests/test_encryption.py` — Created by Plan 01, Task 1 (key generation, encrypt/decrypt roundtrip)
- [x] `tests/test_settings_service.py` — Created by Plan 01, Task 1 (save/get/upsert/masked-response)
- [x] `tests/test_health.py` — Created by Plan 01, Task 2 (health endpoint, welcome page rendering)
- [x] `tests/test_service_clients.py` — Created by Plan 02, Task 1 (mocked Plex/Ollama/Lidarr clients)
- [x] `tests/test_settings_api.py` — Created by Plan 02, Task 1 (API route integration tests)
- [x] `pytest` + `httpx` install — Plan 01, Task 1 installs via requirements.txt + verify command

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

- [x] All tasks have `<automated>` verify with concrete test file references
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all test file creation (handled by plan tasks directly)
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending execution
