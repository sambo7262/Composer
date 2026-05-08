# Stack Research — v2.0 Music Companion (Additions)

**Domain:** Music companion / event-driven recommendation engine
**Researched:** 2026-05-08
**Confidence:** HIGH (versions verified against PyPI / Anthropic docs / Plex docs as of May 2026)

---

## Scope

This document covers **only the additions and changes** required to ship v2.0 on top of the validated v1.0 stack (see `.planning/research/v1.0/STACK.md`). The v1.0 stack — Python 3.12, FastAPI 0.135, SQLModel 0.0.38, SQLite, Jinja2/HTMX/Alpine.js, Tailwind 4 standalone, PlexAPI, pyarr, Anthropic via httpx, Essentia, APScheduler — is **unchanged**.

The v2.0 features driving these additions:
1. Plex rating sync (read `userRating`, detect changes)
2. Plex webhook receiver (multipart/form-data with JSON `payload` field)
3. Polling fallback (when Plex Pass / webhooks unavailable)
4. Audio-feature clustering (k-means on ~460→thousands of rated tracks, 4-D feature space)
5. Vector/distance scoring against vibe centroids
6. Continuous Suggestions queue
7. Anthropic prompt caching for taste profile context
8. First-run setup wizard (HTMX-driven)
9. Mobile-first / portrait-first UI

---

## Recommended Stack — v2.0 Additions

### New Core Libraries

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **anthropic** (Python SDK) | `>=0.100,<1.0` | Replace direct `httpx` calls to Anthropic API | Native `cache_control` support landed in 0.40 (early 2026). `0.100.0` released 2026-05-06. Native `usage.cache_creation_input_tokens` / `cache_read_input_tokens` typed responses make hit-rate measurement trivial. No-beta-header `ttl: "1h"` is GA. Eliminates manual JSON crafting in our existing httpx wrapper. |
| **scikit-learn** | `>=1.8,<2.0` | k-means clustering + silhouette score for vibe discovery | `sklearn.cluster.KMeans` + `sklearn.metrics.silhouette_score` is the industry-standard pairing for picking optimal `k` in the 3–7 range. v1.8.0 (released 2025-12-10) requires Python ≥3.11 (compatible with our 3.12). Wheel size 7.6–9.1 MB; pulls numpy + scipy + joblib + threadpoolctl as transitive deps. **Worth the ~50 MB total** vs. hand-rolling because: (a) silhouette implementation is non-trivial and easy to get wrong; (b) k-means++ init avoids local minima that pure-numpy tutorials don't handle; (c) `n_init='auto'` + `random_state` give us reproducible clustering across container restarts. |

### New Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **numpy** | `>=1.26,<3.0` | Vector math for distance scoring | Already pulled transitively via Essentia and scikit-learn. Use directly for the per-event distance scoring path (`np.linalg.norm(track_features - centroid)`) — no need to spin up a sklearn `NearestNeighbors` index for ~10k tracks scored lazily. |
| **python-multipart** | `>=0.0.24,<1.0` | Parse Plex webhook multipart/form-data | **Already in v1 requirements.txt** — no new dependency. Used implicitly by FastAPI's `Form()`; calling out explicitly because the Plex webhook endpoint is the first place we use it. |

### No New Frontend Dependencies

Tailwind CSS 4 (already pinned) ships native support for everything v2.0's mobile-first UI needs:
- **Dynamic viewport units** — `h-dvh`, `h-svh`, `h-lvh`, `min-h-dvh` etc. landed in Tailwind v3.4 and remain in v4. Use `h-dvh` for full-screen mobile layouts that respect the iOS Safari URL bar.
- **Container queries** — Tailwind v4 ships native `@container` + `@sm:` / `@md:` / `@lg:` prefixes (no plugin needed; `@tailwindcss/container-queries` plugin is no longer required as of v4). Use these on the vibe cards and suggestion list so a card adapts when reused at different sizes (sidebar vs. full-width).
- **Orientation modifiers** — `portrait:` and `landscape:` prefixes are built in. Use sparingly; prefer mobile-first width breakpoints + container queries.
- **Default mobile-first breakpoints** — `sm:` (640px), `md:` (768px), `lg:` (1024px). Unprefixed = phone portrait.

**Pattern to follow:** design every v2 surface unprefixed (phone portrait), then layer `sm:` / `md:` / `lg:` for tablets/desktop. Don't reach for `landscape:` / `portrait:` unless a UI element fundamentally must rearrange on rotation.

### Development Tools

No additions. Existing pytest / pytest-asyncio / ruff / mypy stack is sufficient.

---

## Installation

```bash
# Add to requirements.txt
anthropic>=0.100,<1.0
scikit-learn>=1.8,<2.0
# numpy and python-multipart already present (transitive / explicit)
```

```dockerfile
# Dockerfile — no changes needed.
# scikit-learn ships manylinux wheels for amd64 (our deploy target: Synology DS423+ Intel Celeron J4125).
# arm64 wheels also exist if we ever need them.
# Total image size delta: ~50 MB (sklearn 9 MB + scipy ~30 MB + joblib/threadpoolctl).
```

---

## Integration Points with v1 Stack

### Anthropic SDK ← replaces direct httpx calls
- **What changes:** `app/services/llm.py` (or wherever the Anthropic httpx wrapper lives) swaps `httpx.AsyncClient.post()` for `anthropic.AsyncAnthropic().messages.create()`.
- **What stays:** Pydantic response models, retry logic at the service layer, error handling.
- **New capability:** `cache_control={"type": "ephemeral"}` on system message blocks → cache the taste profile (vibe definitions + ~460 rated track digests) across vibe-clustering runs and suggestion-ranking calls.
- **Note on Instructor (1.15):** v1 STACK.md listed Instructor for structured output, but v1 requirements.txt shows it was removed alongside OpenAI SDK ("openai and instructor removed — using Anthropic API via httpx directly"). v2 continues without it; the Anthropic SDK's `messages.create(...)` + manual Pydantic `model_validate_json()` on the assistant text is sufficient and doesn't fight prompt caching.

### scikit-learn ← new module `app/services/clustering.py`
- **Inputs:** numpy `(N, 4)` array from rated tracks' cached `(energy, tempo, danceability, valence)` columns. Tempo must be normalized (z-score or min-max to `[0, 1]`) so it doesn't dominate Euclidean distance — the other three features are already on `[0, 1]`.
- **Algorithm:** Loop `k` from 3 to 7, fit `KMeans(n_clusters=k, n_init='auto', random_state=42)`, score each with `silhouette_score(X, labels)`, pick `k` with the highest score (tie-break: lowest `k`).
- **Output:** centroids array `(k, 4)` + per-track cluster assignments. Persist centroids to SQLite as JSON in a `vibes` table; track→vibe membership is a many-to-many table.
- **Performance:** ~460 tracks × 5 candidate k values × 4 dims = sub-second on a Celeron. At ~10k rated tracks (year 5 scenario) still well under a second. No async streaming needed; run inside a `run_in_executor()` wrapper so the event loop isn't blocked during the rare re-cluster operation.

### Per-event scoring ← numpy only
- **Don't use sklearn for the hot path.** Per-event scoring of a single new/unrated track against `k≤7` centroids is `np.argmin(np.linalg.norm(features - centroids, axis=1))` — pure numpy, microseconds. Reserve sklearn for the offline clustering pass.

### Plex webhook receiver ← new FastAPI route
- **Plex Pass requirement:** Webhooks are a Plex Pass feature only. The receiver MUST work for users without Plex Pass via the polling fallback (below).
- **Endpoint shape:** `POST /api/webhooks/plex` accepting `multipart/form-data` with a `payload` field (JSON string) and an optional thumbnail file part for `media.play` / `media.rate`.
- **FastAPI signature:**
  ```python
  from typing import Annotated
  from fastapi import Form, UploadFile
  import json

  @router.post("/api/webhooks/plex")
  async def plex_webhook(
      payload: Annotated[str, Form()],
      thumb: Annotated[UploadFile | None, File()] = None,
  ):
      data = json.loads(payload)  # parse the JSON-string-in-form-field
      event = data["event"]       # "media.play" | "media.rate" | "library.new" | ...
      # dispatch to handler
  ```
  Use `Annotated[str, Form()]` + manual `json.loads`, **not** `Annotated[pydantic.Json[Model], Form()]`. The latter has known issues (FastAPI #10997) where the JSON string isn't deserialized correctly when sent via multipart, and Swagger schema generation is broken. Manual `json.loads` is one line and avoids the footgun.
- **Events we care about:**
  - `media.rate` — track was star-rated → trigger vibe re-slot
  - `media.scrobble` — track played past 90% → mark as consumed for Suggestions drain
  - `media.play` — track started → optional, used as soft signal
  - `library.new` — new track imported (Lidarr arrival path) → trigger Essentia analysis + scoring
- **No external library needed.** Don't pull in Plex-webhook-specific helpers (e.g. `plex-webhook-proxy`); the multipart parsing is one `Annotated[str, Form()]` + one `json.loads()` line.

### Polling fallback ← APScheduler (already installed)
- **No new library.** The existing `AsyncIOScheduler` in v1 (already running for Plex library sync) gains two more cron jobs:
  - **Rating poll** — every N minutes (default 5), call PlexAPI `library.search(libtype='track', sort='lastRatedAt:desc', limit=100)` (or query tracks where `userRating > 0` and `lastRatedAt > last_seen_timestamp`); compare against our cached `rating_seen_at` column; emit synthetic `rating_changed` events for diffs.
  - **Playback poll** — every N minutes (default 5), query `lastViewedAt` and `viewCount` per recently-played tracks; emit synthetic `track_consumed` events for `lastViewedAt` advances since last poll.
- **PlexAPI 4.18 attribute access:** Tracks expose `userRating` (float, 0.0–10.0 = 0–5 stars; multiply by 2 for the Plex internal scale, or divide by 2 for stars when displaying), `lastViewedAt` (datetime), and `viewCount` (int). Missing `userRating` = unrated. Sort is supported via `sort='lastViewedAt:desc'` strings.
- **When to enable:** Polling can run unconditionally as a safety net; webhook-delivered events should mark a "last webhook seen" timestamp so the polling job can short-circuit if webhooks are flowing. If webhooks haven't fired in N hours, log a warning to the UI ("Webhooks not configured? Falling back to polling").

### Setup wizard ← HTMX (already installed)
- **No new library.** Multi-step flow is a series of `hx-get` / `hx-post` form submissions returning Jinja2 partial templates. Server-side state lives in SQLite via a `setup_state` row keyed by user (or just `setup_completed_at IS NULL`). HTMX `hx-push-url` updates the browser bar so steps are bookmarkable / refreshable.

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| **scikit-learn** | `scipy.cluster.vq.kmeans2` | If we needed to shave 30 MB off the Docker image. scipy's k-means lacks k-means++ init by default and has no built-in silhouette score (would still need to hand-roll that). Not worth it. |
| **scikit-learn** | Pure numpy k-means + hand-rolled silhouette | If we wanted to avoid the dependency entirely. Tutorials exist (Frolian, Paperspace) but: (a) silhouette is O(N²) memory naive — sklearn does chunking; (b) k-means++ init is non-trivial to implement correctly; (c) the dependency footprint is acceptable for our deploy target (32GB RAM NAS). Not worth the maintenance burden. |
| **anthropic SDK** | Continue with raw `httpx` | Works, but `cache_control` JSON crafting is verbose, response usage parsing is manual, and there's no streaming abstraction if we ever want it. SDK is purpose-built; switching now is one PR and unlocks easier iteration. |
| **anthropic SDK** | LangChain `langchain-anthropic` middleware | Massive dependency graph for what amounts to a single `cache_control` parameter. Rejected on principle (same reason v1 rejected LiteLLM). |
| **APScheduler polling** | Celery / RQ / Dramatiq | Single-user, single-container, single-process — distributed task queue is wildly disproportionate. AsyncIOScheduler is already running, polling is one more job definition. |
| **APScheduler polling** | FastAPI `BackgroundTasks` | `BackgroundTasks` runs after a request handler completes; it's not a scheduler. We need cron-like recurring jobs. APScheduler is the right tool. |
| **FastAPI `Form()` + manual `json.loads`** | `pydantic.Json[Model]` in `Form()` | Looks cleaner but has open issues (FastAPI #10997) where it fails on multipart + breaks OpenAPI schema. One extra line of code beats two days debugging a 422. |
| **Tailwind built-in container queries** | `@tailwindcss/container-queries` plugin | Plugin was required in v3, redundant in v4. Not adding. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **Celery / RQ / Dramatiq** | Distributed task queue for a single-user single-container app. Adds Redis dependency, separate worker process, deployment complexity. Zero benefit at our scale. | APScheduler `AsyncIOScheduler` (already installed) |
| **LangChain / LangGraph** | Heavy framework wrapping LLM calls in chains/graphs. v2's LLM use is two well-defined operations (cluster naming, rank candidates) — direct SDK calls are clearer and cacheable. | `anthropic` SDK directly |
| **LiteLLM** | Multi-provider proxy. We are committed to Anthropic for v2.0 (per Out of Scope). Adds abstraction layer that hides `cache_control` semantics. | `anthropic` SDK directly |
| **plex-webhook-proxy** (external service) | Sidecar container that converts Plex multipart to JSON for downstream apps. Useful if you have many consumers; we have one (Composer itself) and FastAPI handles multipart natively. | `Annotated[str, Form()]` + `json.loads(payload)` |
| **Spotify SDK / Spotipy** | v2 explicitly drops Spotify (audio features come from Essentia locally). Don't carry the v1 dependency forward into v2 code paths. | Essentia (already installed) |
| **Instructor 1.15** | Removed mid-v1 when migrating to direct Anthropic; pulling it back in fights `cache_control` (Instructor reformats system messages). For two structured-output calls per event, direct SDK + manual `Model.model_validate_json()` is cleaner. | Anthropic SDK + Pydantic `model_validate_json` |
| **`pydantic.Json[Model]` inside `Form()`** | Open FastAPI bug (#10997) — fails to deserialize correctly in multipart requests, breaks OpenAPI schema. | `Annotated[str, Form()]` + manual `json.loads()` |
| **scipy.cluster.vq.kmeans** | No k-means++ init by default, no convergence threshold control, scipy docs themselves recommend sklearn for "more functionalities or optimal performance." Brings scipy as a dep anyway (sklearn does too). | `sklearn.cluster.KMeans(init='k-means++', n_init='auto')` |
| **Pure numpy k-means tutorials** | Tutorial code lacks k-means++ init handling, empty-cluster edge cases, and silhouette score is O(N²) memory if you don't chunk. Maintenance burden + correctness risk. | sklearn (one import, vetted) |
| **Re-introducing OpenAI SDK** | v2 is Anthropic-locked (Out of Scope decision); the OpenAI-compatible-base_url pattern from v1 doesn't apply. | `anthropic.AsyncAnthropic` |
| **Tailwind `@tailwindcss/container-queries` plugin** | Functionality is native in Tailwind v4; plugin is a no-op. | Native `@container` + `@sm:` etc. in v4 |
| **A separate "rating cache" library** (e.g. caching decorators) | We already cache audio features in SQLite; rating cache lives in the same `tracks` table as a `user_rating` + `rating_seen_at` column pair. | SQLite, no new lib |

---

## Stack Patterns by Variant

**If user has Plex Pass (preferred):**
- Webhook endpoint is primary signal source; polling job runs at low frequency (every 30 min) as a safety net.
- Setup wizard prompts user to add `http://composer:8085/api/webhooks/plex` to Plex Settings → Webhooks.

**If user does NOT have Plex Pass (fallback):**
- Webhook endpoint still exists (no harm) but never receives events.
- Polling job runs at higher frequency (every 5 min) and is the sole signal source.
- Setup wizard surfaces this as expected and explains the rating-detection latency tradeoff.

**If rated track count > ~5000:**
- k-means is still fast (sub-second), but silhouette score is O(N²) — at 10k tracks that's 100M pairwise distances. sklearn handles this via sampling (`silhouette_score(X, labels, sample_size=1000)`). Add `sample_size` if perf becomes an issue.

**If Anthropic spend creeps above target ($5/year):**
- The 1-hour cache TTL (`cache_control={"type": "ephemeral", "ttl": "1h"}`) is no-cost to refresh and the obvious first lever. Only pay full input price the first time within the hour.
- **Important:** Claude Sonnet 4.6 (default model) requires **2,048 tokens minimum** for a cache breakpoint to actually cache. Sonnet 4.5/Opus 4.1/Sonnet 3.7 require 1,024. Check `usage.cache_creation_input_tokens` is non-zero on first call to confirm caching engaged; if zero, the system message is too short and you're silently paying full price.

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| `anthropic>=0.100` | Python ≥3.8 | Native `cache_control` since 0.40; no beta header needed for `ttl: "1h"` since early 2026. |
| `scikit-learn>=1.8` | Python ≥3.11; numpy ≥1.24.1; scipy ≥1.10.0 | Compatible with our Python 3.12. amd64 manylinux wheel ships in Docker build cleanly. |
| `PlexAPI 4.18.1` | Python ≥3.10 | Already installed; `userRating` is float 0.0–10.0; `lastViewedAt` is datetime; `viewCount` is int. v4.18.1 released 2026-03-22. |
| `pyarr 6.6.0` | Python ≥3.12 | Already installed; v1 requirements.txt pin `<6.0` is **stale** — pyarr 6.6 was released Mar 2026 and v1.0 STACK.md recommends 6.6.0. Bump the pin to `>=6.6,<7.0` when v2 work touches the Lidarr code path. |
| `FastAPI 0.135` + `python-multipart 0.0.24` | — | `Annotated[str, Form()]` is the supported pattern; `pydantic.Json[Model]` inside `Form()` has open issues, avoid. |
| `Tailwind CSS 4` | Standalone CLI binary | Container queries native in v4 (no plugin); dvh/svh/lvh native since v3.4; `portrait:` / `landscape:` always native. |

**Existing requirements.txt pin to fix:**
```diff
- pyarr>=5.2,<6.0
+ pyarr>=6.6,<7.0
```
This pin is on 5.2 in `requirements.txt` even though v1 STACK.md recommended 6.6. Likely an oversight during v1 development. Worth bumping in v2 since pyarr 6.x has improvements relevant to the Lidarr taste-aware discovery feature.

---

## Final requirements.txt (v2.0)

```text
fastapi>=0.135,<0.136
uvicorn>=0.44,<1.0
sqlmodel>=0.0.38,<0.1
httpx>=0.28,<1.0
python-multipart>=0.0.24,<1.0
plexapi>=4.18,<5.0
pyarr>=6.6,<7.0                # bumped from 5.2
cryptography>=46.0,<47.0
jinja2>=3.1,<4.0
apscheduler>=3.11,<4.0

# v2.0 additions
anthropic>=0.100,<1.0          # replaces direct-httpx Anthropic calls; native cache_control
scikit-learn>=1.8,<2.0         # k-means + silhouette for vibe clustering
# essentia installed conditionally in Dockerfile (amd64 only — no arm64 wheel available)
```

---

## Sources

**Anthropic SDK / prompt caching (HIGH confidence):**
- [anthropic on PyPI](https://pypi.org/project/anthropic/) — v0.100.0 released 2026-05-06, supports `cache_control` natively
- [Prompt caching — Claude API docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — `cache_control={"type": "ephemeral", "ttl": "1h"}` GA, system-message caching pattern
- [How to add prompt caching — startdebugging.net (April 2026)](https://startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/) — verified against anthropic 0.42 (Python); cache hit measurement via `usage.cache_creation_input_tokens` / `cache_read_input_tokens`
- [anthropic-sdk-python issue #1194](https://github.com/anthropics/anthropic-sdk-python/issues/1194) — Sonnet 4.6 requires 2048 tokens minimum, not 1024 (model-dependent)

**scikit-learn (HIGH confidence):**
- [scikit-learn 1.8.0 on PyPI](https://pypi.org/project/scikit-learn/) — released 2025-12-10, requires Python ≥3.11, deps numpy ≥1.24.1 / scipy ≥1.10.0 / joblib ≥1.3 / threadpoolctl ≥3.2
- [silhouette_score — sklearn 1.8.0 docs](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.silhouette_score.html) — supports `sample_size` for large N
- [KMeans — sklearn 1.8.0 docs](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.KMeans.html) — `n_init='auto'`, k-means++ default init
- [Selecting k with silhouette analysis — sklearn example](https://scikit-learn.org/stable/auto_examples/cluster/plot_kmeans_silhouette_analysis.html) — canonical pattern
- [scipy.cluster.vq.kmeans2 docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.cluster.vq.kmeans2.html) — explicitly recommends sklearn for "more functionalities or optimal performance"

**Plex webhooks (HIGH confidence — official):**
- [Webhooks — Plex Support](https://support.plex.tv/articles/115002267687-webhooks/) — Plex Pass requirement, multipart/form-data with `payload` JSON field, event types
- [Plex Pro Week '25: Webhooks 101](https://www.plex.tv/blog/plex-pro-week-25-webhooks-101/) — recent (2025) overview confirming format unchanged
- [media.scrobble forum thread](https://forums.plex.tv/t/media-scrobble-webhook/879128) — fires at 90% playback threshold

**FastAPI multipart pattern (MEDIUM confidence — community):**
- [FastAPI Discussion #5666](https://github.com/fastapi/fastapi/discussions/5666) — pattern for accepting both JSON and form data
- [FastAPI Discussion #8976](https://github.com/fastapi/fastapi/discussions/8976) — JSON string in multipart Form, `Annotated[str, Form()]` + manual `json.loads` works; `pydantic.Json[Model]` in `Form()` is buggy
- [FastAPI Issue #10997](https://github.com/fastapi/fastapi/issues/10997) — open issue confirming `pydantic.Json` type as Form data type doesn't work reliably

**PlexAPI userRating / lastViewedAt (HIGH confidence):**
- [Python PlexAPI Documentation (Jan 2026 PDF)](https://python-plexapi.readthedocs.io/_/downloads/en/stable/pdf/) — `userRating` is float on 0.0–10.0 scale (0–5 stars)
- [python-plexapi audio.py source](https://github.com/pkkid/python-plexapi/blob/master/plexapi/audio.py) — Track inherits `userRating`, `lastViewedAt`, `viewCount` from Audio base
- [PlexAPI 4.18.1 on PyPI](https://pypi.org/project/PlexAPI/) — v4.18.1 released 2026-03-22

**APScheduler (HIGH confidence — already validated in v1):**
- [APScheduler 3.x AsyncIOScheduler docs](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html) — runs jobs in same event loop as FastAPI

**Tailwind CSS 4 (HIGH confidence):**
- [Tailwind CSS Responsive Design docs](https://tailwindcss.com/docs/responsive-design) — mobile-first breakpoints, `portrait:` / `landscape:` modifiers
- [Tailwind v4 container queries](https://www.sitepoint.com/tailwind-css-v4-container-queries-modern-layouts/) — native in v4, plugin no longer needed
- [Dynamic viewport unit classes — Tailwind](https://tailscan.com/blog/tailwind-css-dynamic-viewport-unit-classes) — `dvh`, `svh`, `lvh` available since v3.4

---

*Stack research for: Composer v2.0 — Music Companion (additive to v1.0 stack)*
*Researched: 2026-05-08*
