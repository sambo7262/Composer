# API Integration Reference

**Researched:** 2026-04-09
**Domain:** Plex, Lidarr (pyarr), and Ollama API integration patterns
**Confidence:** HIGH (verified from installed source code and official documentation)

This document is a practical reference for executor agents. It documents verified behavior, known gotchas, and correct usage patterns for all external API integrations in Composer.

---

## 1. Plex API (via PlexAPI Python Library)

### 1.1 Filter Operators

Plex filter operators are appended directly to the field name in the `filters` dict. The available operators depend on the field data type.

**String operators:**

| Operator | Meaning | Example |
|----------|---------|---------|
| (none) | contains | `{"title": "Love"}` |
| `!` | does not contain | `{"title!": "Live"}` |
| `=` | is (exact) | `{"title=": "Lucky"}` |
| `!=` | is not | `{"title!=": "Lucky"}` |
| `<` | begins with | `{"title<": "The"}` |
| `>` | ends with | `{"title>": "Mix"}` |

**Integer operators:**

| Operator | Meaning | Example |
|----------|---------|---------|
| `>>` | is greater than | `{"year>>": 1990}` |
| `<<` | is less than | `{"year<<": 2000}` |

**Datetime operators (addedAt, updatedAt):**

| Operator | Meaning | Example |
|----------|---------|---------|
| `>>` | is after | `{"addedAt>>": "2024-01-15"}` |
| `<<` | is before | `{"addedAt<<": "2024-06-01"}` |

[VERIFIED: plexapi/library.py lines 1148-1157 in installed source]

### 1.2 Date Format for Filters -- CRITICAL GOTCHA

**BUG WE HIT:** The `addedAt>>` and `addedAt<<` filters require **date-only format** (`YYYY-MM-DD`), NOT ISO timestamps.

The PlexAPI `_validateFieldValueDate` method (library.py line 1157) does this:

```python
# From plexapi/library.py _validateFieldValueDate():
int(utils.toDatetime(value, '%Y-%m-%d').timestamp())
```

This means the library parses date filter values with `strptime('%Y-%m-%d')`. If you pass `2024-01-15T10:30:00Z` or `2024-01-15 10:30:00`, it will **raise an exception**.

**Accepted date formats:**
- `"2024-01-15"` -- YYYY-MM-DD (correct)
- `"-30d"` -- relative: last 30 days
- `"-2w"` -- relative: last 2 weeks
- `"-6mon"` -- relative: last 6 months
- `"-1y"` -- relative: last 1 year
- `datetime` object -- Python datetime (converted to unix timestamp)

**Relative date suffixes:** `s` (seconds), `m` (minutes), `h` (hours), `d` (days), `w` (weeks), `mon` (months), `y` (years)

**DO THIS:**
```python
# Correct: date-only string
tracks = section.searchTracks(filters={"addedAt>>": "2024-01-15"})

# Correct: relative date
tracks = section.searchTracks(filters={"addedAt>>": "30d"})

# Correct: datetime object
from datetime import datetime
tracks = section.searchTracks(filters={"addedAt>>": datetime(2024, 1, 15)})
```

**NOT THIS:**
```python
# WRONG: ISO timestamp -- will crash
tracks = section.searchTracks(filters={"addedAt>>": "2024-01-15T10:30:00Z"})

# WRONG: ISO with timezone
tracks = section.searchTracks(filters={"addedAt>>": "2024-01-15T10:30:00+00:00"})

# WRONG: datetime isoformat string
tracks = section.searchTracks(filters={"addedAt>>": some_datetime.isoformat()})
```

**When converting from stored ISO timestamps** (as in our `last_sync_completed` field):
```python
# Our sync_service stores ISO timestamps. Truncate to date-only for Plex:
date_only = since_date_str[:10]  # "2024-01-15T10:30:00Z" -> "2024-01-15"
```

[VERIFIED: plexapi/library.py source code, confirmed by production bug fix in plex_client.py line 69]

### 1.3 Pagination

**Parameters:**
- `container_start` (int): Starting offset, default 0
- `container_size` (int): Items per page, default `X_PLEX_CONTAINER_SIZE` which is **100** (configurable in plexapi config)

**How pagination works:**
The `search()` / `searchTracks()` method internally batches HTTP requests using `container_start` and `container_size`. These map to `X-Plex-Container-Start` and `X-Plex-Container-Size` HTTP headers.

**Getting total count:**
The `totalSize` property on `LibrarySection` returns the total number of items. It is a `cached_data_property` that calls `totalViewSize(includeCollections=False)` under the hood. This makes a server query -- it is NOT free.

Alternatively, each paginated response includes a `totalSize` attribute in the XML `MediaContainer`, which the library exposes.

**End-of-results behavior:**
When `container_start + container_size` exceeds the total, Plex returns whatever items remain. If `container_start` is past the end, an empty list is returned. No error is raised.

**Recommended batch sizes:** 100-250. Larger sizes increase memory usage per request but reduce HTTP round trips. Our current code uses 200, which is a good balance.

```python
# Paginated fetch pattern (from our sync_service.py)
batch_size = 200
offset = 0
total = None

while True:
    batch, batch_total = await get_library_tracks(
        url, token, library_id,
        container_start=offset,
        container_size=batch_size,
    )
    if total is None:
        total = batch_total

    process(batch)

    if len(batch) < batch_size or offset + len(batch) >= total:
        break
    offset += batch_size
```

**Gotcha:** `maxresults` is a separate parameter that caps the total items returned across all pages. If you set `maxresults=1, container_size=1`, you get exactly 1 item but the response still includes `totalSize` for the full library. This is useful for getting just the count.

[VERIFIED: plexapi/__init__.py line 21, plexapi/base.py lines 302-303, plexapi/library.py lines 1291-1549]

### 1.4 Track Metadata Fields

Complete list of fields on a `plexapi.audio.Track` object, from source inspection:

**From Audio base class (inherited):**

| Field | Type | Can be None | Notes |
|-------|------|-------------|-------|
| `addedAt` | `datetime` | Yes | When added to library |
| `art` | `str` | Yes | URL to artwork |
| `guid` | `str` | Yes | Plex GUID (plex://track/...) |
| `index` | `int` | Yes | Track number |
| `key` | `str` | No | API URL (/library/metadata/<ratingKey>) |
| `librarySectionID` | `int` | Yes | Library section ID |
| `librarySectionTitle` | `str` | Yes | Library section name |
| `listType` | `str` | No | Always 'audio' |
| `moods` | `List[Mood]` | No | List of mood tags (can be empty list) |
| `musicAnalysisVersion` | `int` | Yes | Plex music analysis version |
| `ratingKey` | `int` | No | Unique identifier for this item |
| `summary` | `str` | Yes | Track description |
| `thumb` | `str` | Yes | URL to thumbnail |
| `title` | `str` | Yes | Track name |
| `titleSort` | `str` | No | Defaults to title |
| `type` | `str` | No | Always 'track' |
| `updatedAt` | `datetime` | Yes | Last update time |
| `userRating` | `float` | Yes | User rating 0.0-10.0 |
| `viewCount` | `int` | No | Play count (defaults to 0) |

**Track-specific fields:**

| Field | Type | Can be None | Notes |
|-------|------|-------------|-------|
| `audienceRating` | `float` | Yes | |
| `chapterSource` | `str` | Yes | |
| `duration` | `int` | Yes | Length in milliseconds |
| `grandparentArt` | `str` | Yes | Album artist artwork URL |
| `grandparentGuid` | `str` | Yes | Album artist Plex GUID |
| `grandparentKey` | `str` | Yes | Album artist API URL |
| `grandparentRatingKey` | `int` | Yes | Album artist unique key |
| `grandparentThumb` | `str` | Yes | Album artist thumbnail |
| `grandparentTitle` | `str` | Yes | **Album artist name** |
| `originalTitle` | `str` | Yes | The track artist (may differ from album artist) |
| `parentGuid` | `str` | Yes | Album Plex GUID |
| `parentIndex` | `int` | Yes | **Disc number** |
| `parentKey` | `str` | Yes | Album API URL |
| `parentRatingKey` | `int` | Yes | Album unique key |
| `parentThumb` | `str` | Yes | Album thumbnail |
| `parentTitle` | `str` | Yes | **Album name** |
| `primaryExtraKey` | `str` | Yes | Extra content URL |
| `rating` | `float` | Yes | Track rating |
| `ratingCount` | `int` | Yes | Last.fm scrobble count |
| `skipCount` | `int` | Yes | Times skipped |
| `sourceURI` | `str` | Yes | Remote playlist item only |
| `viewOffset` | `int` | No | Resume position (defaults to 0) |
| `year` | `int` | Yes | Release year |

**Lazy-loaded (cached_data_property) fields:**

| Field | Type | Notes |
|-------|------|-------|
| `chapters` | `List[Chapter]` | |
| `collections` | `List[Collection]` | |
| `genres` | `List[Genre]` | Each has `.tag` property for the genre name |
| `guids` | `List[Guid]` | External IDs (MusicBrainz, etc.) |
| `labels` | `List[Label]` | |
| `media` | `List[Media]` | **Contains file info, see 1.5** |

**The hierarchy:** Track.grandparentTitle = artist, Track.parentTitle = album, Track.title = track name.

[VERIFIED: plexapi/audio.py lines 505-624 in installed source]

### 1.5 File Path Access

**Path:** `track.media[0].parts[0].file`

**Structure:**
- `track.media` -- `List[Media]` -- usually has exactly 1 item for music
- `media.parts` -- `List[MediaPart]` -- usually has exactly 1 item
- `part.file` -- `str` -- absolute file path on the Plex server filesystem

**When can media/parts be empty?**
- `media` can be an empty list for items that haven't been fully scanned
- `parts` can be empty if the file was deleted but the metadata remains
- The `file` attribute can be `None` if the path data wasn't included in the response

**Safe access pattern (used in our code):**
```python
file_path = None
try:
    if hasattr(t, "media") and t.media:
        parts = t.media[0].parts
        if parts:
            file_path = parts[0].file
except (IndexError, AttributeError):
    pass
```

**Convenience alternative:** `track.locations` returns `List[str]` of all file paths. This property iterates all parts across all media items:
```python
# Simpler but equivalent for single-file tracks:
file_paths = track.locations  # List of file paths
```

**Path format:** Plex returns the absolute path as it exists on the **server's** filesystem. In Docker, this is the path inside the Plex container (e.g., `/music/Artist/Album/track.flac`). This may differ from the path seen by Composer's container. Ensure both containers mount the same music directory.

[VERIFIED: plexapi/audio.py lines 602-614]

### 1.6 Library Sections: sectionByID vs section

| Method | Parameter | Lookup | Use When |
|--------|-----------|--------|----------|
| `plex.library.sectionByID(int)` | Integer section ID | Exact match by ID | You have the section key/ID (recommended) |
| `plex.library.section(str)` | Section title string | Title match | You have the section name |

**Gotcha with `section()`:** If multiple sections share the same title, it returns the **last** one and issues a warning. Use `sectionByID()` for unambiguous access.

**Getting section ID:** During connection test, we enumerate sections and store the key:
```python
sections = plex.library.sections()
music_libs = [s for s in sections if s.type == "artist"]
# s.key is the integer section ID
```

[VERIFIED: plexapi/library.py lines 84-118]

### 1.7 searchTracks vs all

`searchTracks(**kwargs)` is just a convenience wrapper:
```python
def searchTracks(self, **kwargs):
    return self.search(libtype='track', **kwargs)
```

There is no separate `all()` method that behaves differently for performance. Both go through the same `search()` -> `fetchItems()` pipeline with pagination.

The `MusicSection.all()` method (inherited from `LibrarySection`) returns all items of the default library type, which for music is 'artist' NOT 'track'. To get all tracks, you must use `searchTracks()`.

[VERIFIED: plexapi/library.py lines 1983-1985]

### 1.8 Connection Handling

**Timeout:** Configurable via `PlexServer(url, token, timeout=N)`. Default is 30 seconds.

**No auto-reconnect:** PlexAPI does not automatically reconnect or retry failed requests. If the connection drops, subsequent calls will raise exceptions. Our code creates a new `PlexServer` instance for each operation (in `get_library_tracks` and `get_tracks_since`), which is correct -- it avoids stale connection issues.

**No token refresh needed:** Plex tokens for local servers do not expire. The token obtained from Plex Web settings or `PlexServer.myPlexAccount().authenticationToken` remains valid indefinitely unless manually revoked.

**Session reuse:** You can pass a `requests.Session()` to `PlexServer` for connection pooling. Not currently used in our code but could improve performance for batch operations.

[VERIFIED: plexapi server.py docs, plexapi/__init__.py line 20]

### 1.9 Rate Limiting

**Local Plex servers do NOT enforce rate limits** on API calls. There are no 429 responses from a local Plex server.

However, the Plex database (SQLite-based) can become a bottleneck under heavy concurrent access. Plex logs "Sleeping for 200ms" messages when this happens. Our sequential pagination approach avoids this issue.

**plex.tv cloud API** does enforce rate limits (for remote access, account operations), but we only communicate with the local server.

[CITED: https://forums.plex.tv/t/rate-limiting-for-plex-api-calls/877894]

---

## 2. Lidarr API (via pyarr Library)

### 2.1 Class Names -- CRITICAL VERSION ISSUE

**BUG WE HIT:** pyarr renamed its classes between major versions.

| pyarr Version | Import | Class Name |
|---------------|--------|------------|
| < 6.0 (old) | `from pyarr import LidarrAPI` | `LidarrAPI` |
| >= 6.0 (new) | `from pyarr import Lidarr` | `Lidarr` |

**Our situation:**
- `requirements.txt` specifies `pyarr>=6.6,<7.0`
- Our code uses `from pyarr import Lidarr` (correct for v6.6+)
- The `.venv` in the repo has an old pyarr version (still exports `LidarrAPI` in its `__init__.py`), which is a stale venv from Python 3.9 development

**The old `__init__.py` (from installed .venv):**
```python
from .lidarr import LidarrAPI  # OLD -- pyarr < 6.0
```

**The new import (pyarr >= 6.0):**
```python
from pyarr import Lidarr  # NEW -- pyarr >= 6.0
```

**Constructor in both versions:**
```python
# Same signature in both versions:
lidarr = Lidarr(host_url="http://lidarr:8686", api_key="your-api-key")
```

**If you see `ImportError: cannot import name 'Lidarr' from 'pyarr'`:** Your pyarr version is too old. Upgrade to >= 6.0.

**If you see `ImportError: cannot import name 'LidarrAPI' from 'pyarr'`:** Your pyarr version is >= 6.0. Change import to `from pyarr import Lidarr`.

Similarly, other classes were renamed: `SonarrAPI` -> `Sonarr`, `RadarrAPI` -> `Radarr`, `ReadarrAPI` -> `Readarr`.

[VERIFIED: pyarr/__init__.py in installed .venv shows old names; pyarr >= 6.0 docs show new names]

### 2.2 Authentication

**API Key format:** A 32-character hexadecimal string (e.g., `a1b2c3d4e5f6...`).

**Where to find it:** Lidarr web UI > Settings > General > API Key

**How pyarr sends it:** As an `X-Api-Key` HTTP header on every request.

```python
lidarr = Lidarr(host_url="http://lidarr:8686", api_key="your-api-key")
# All subsequent calls include: X-Api-Key: your-api-key
```

**Common auth errors:**
- 401 Unauthorized: Wrong API key or API key not provided
- Connection refused: Wrong URL/port, or Lidarr not running

[VERIFIED: pyarr/base.py request handler source]

### 2.3 Artist Operations

#### Searching for artists

```python
# Search by name
results = lidarr.lookup_artist(term="Radiohead")

# Search by MusicBrainz ID
results = lidarr.lookup_artist(term="lidarr:a74b1b7f-71a5-4011-9441-d0b5e4122711")

# General search (artists + albums + songs)
results = lidarr.lookup(term="Radiohead")
```

**Gotcha:** `lookup_artist` hits the `/api/v1/artist/lookup` endpoint, which depends on **api.lidarr.audio** (external metadata service). This service frequently has 503/524 timeout errors. Always wrap in try/except with retry logic.

#### Adding an artist

```python
# Step 1: Search for the artist
results = lidarr.lookup_artist(term="Radiohead")
artist = results[0]  # First match

# Step 2: Get required IDs (DO NOT hardcode these!)
quality_profiles = lidarr.get_quality_profile()
metadata_profiles = lidarr.get_metadata_profile()
root_folders = lidarr.get_root_folder()

# Step 3: Add the artist
added = lidarr.add_artist(
    artist=artist,                              # Dict from lookup
    root_dir=root_folders[0]["path"],            # e.g., "/music/"
    quality_profile_id=quality_profiles[0]["id"],
    metadata_profile_id=metadata_profiles[0]["id"],
    monitored=True,
    artist_monitor="all",                        # or "future", "existing", "first", "latest", "none"
    artist_search_for_missing_albums=False,
)
```

**CRITICAL:** If you omit `quality_profile_id` or `metadata_profile_id`, pyarr auto-selects the first available profile. This is convenient but fragile -- if no profiles exist, it raises `PyarrMissingProfile`.

**The `artist` dict from lookup** must be passed as-is. pyarr mutates it internally, adding `id`, `metadataProfileId`, `qualityProfileId`, `rootFolderPath`, `addOptions`, and `monitored` fields before POSTing to Lidarr.

[VERIFIED: pyarr/lidarr.py lines 122-172 in installed source]

#### Getting existing artists

```python
# Get all artists
all_artists = lidarr.get_artist()

# Get specific artist by database ID
artist = lidarr.get_artist(id_=42)

# Get artist by MusicBrainz ID (pass as string)
artist = lidarr.get_artist(id_="a74b1b7f-71a5-4011-9441-d0b5e4122711")
```

[VERIFIED: pyarr/lidarr.py lines 102-120]

### 2.4 Quality Profiles

```python
profiles = lidarr.get_quality_profile()
# Returns: [{"id": 1, "name": "Any", ...}, {"id": 2, "name": "Lossless", ...}]

# Get specific profile
profile = lidarr.get_quality_profile(id_=1)
```

**Gotcha:** Profile IDs are NOT standard across Lidarr installations. Always fetch dynamically. The default installation typically has profiles like "Any", "Lossless", "Standard" but IDs vary.

[VERIFIED: pyarr/base.py lines 325-335]

### 2.5 Metadata Profiles

```python
profiles = lidarr.get_metadata_profile()
# Returns: [{"id": 1, "name": "Standard", ...}, ...]

# Get specific profile
profile = lidarr.get_metadata_profile(id_=1)
```

**Required for adding artists.** If no metadata profile exists in Lidarr, `add_artist` will raise `PyarrMissingProfile`. The default Lidarr installation creates a "Standard" metadata profile.

[VERIFIED: pyarr/lidarr.py lines 515-525]

### 2.6 Root Folders

```python
folders = lidarr.get_root_folder()
# Returns: [{"id": 1, "path": "/music/", "freeSpace": 123456789, ...}]

# Get specific folder
folder = lidarr.get_root_folder(id_=1)
```

**At least one root folder must be configured in Lidarr** before artists can be added. If the list is empty, adding artists will fail with a validation error.

[VERIFIED: pyarr/base.py lines 111-121]

### 2.7 Error Handling

**Common error patterns:**

| HTTP Status | Cause | How to Handle |
|-------------|-------|---------------|
| 401 | Invalid API key | Check settings, re-validate API key |
| 400 | Missing required field | Usually missing qualityProfileId or metadataProfileId |
| 404 | Artist/album not found | ID doesn't exist in Lidarr |
| 500 | Lidarr internal error | Retry with backoff |
| 503/524 | api.lidarr.audio timeout | External metadata service down, retry later |
| Connection refused | Lidarr not running | Check URL and port |

**pyarr exceptions:**
- `PyarrMissingProfile`: No quality/metadata profiles configured
- `PyarrMissingArgument`: Required argument not provided (e.g., no artist/album ID for `get_tracks`)

**Timeout behavior:** pyarr does not set a default request timeout. For long-running operations, the request may hang indefinitely. Consider setting timeouts at the requests session level.

[VERIFIED: pyarr source code inspection]

### 2.8 Lidarr API Endpoints Reference

All endpoints use `/api/v1` prefix. API key sent as `X-Api-Key` header.

| Endpoint | Method | pyarr Method | Notes |
|----------|--------|--------------|-------|
| `/api/v1/artist` | GET | `get_artist()` | List all or get by ID |
| `/api/v1/artist` | POST | `add_artist()` | Add new artist |
| `/api/v1/artist/{id}` | PUT | `upd_artist()` | Update artist |
| `/api/v1/artist/{id}` | DELETE | `delete_artist()` | Remove artist |
| `/api/v1/artist/lookup` | GET | `lookup_artist()` | Search by name/MBID |
| `/api/v1/album` | GET | `get_album()` | List albums |
| `/api/v1/album` | POST | `add_album()` | Add album |
| `/api/v1/album/lookup` | GET | `lookup_album()` | Search albums |
| `/api/v1/search` | GET | `lookup()` | General search |
| `/api/v1/qualityprofile` | GET | `get_quality_profile()` | List quality profiles |
| `/api/v1/metadataprofile` | GET | `get_metadata_profile()` | List metadata profiles |
| `/api/v1/rootfolder` | GET | `get_root_folder()` | List root folders |
| `/api/v1/track` | GET | `get_tracks()` | Get tracks (requires artist/album/release ID) |
| `/api/v1/command` | POST | `post_command()` | Run Lidarr commands |

[VERIFIED: pyarr/lidarr.py and pyarr/base.py source]

---

## 3. Ollama API (via OpenAI Python SDK)

### 3.1 Connection Setup

Ollama exposes an OpenAI-compatible API at `/v1`. Use the standard OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",  # MUST include /v1
    api_key="ollama",                       # Required by SDK, ignored by Ollama
)
```

**CRITICAL:** The `base_url` MUST end with `/v1`. Without it, requests go to the wrong endpoints.

**DO THIS:**
```python
client = OpenAI(base_url="http://ollama:11434/v1", api_key="ollama")
```

**NOT THIS:**
```python
# WRONG: missing /v1
client = OpenAI(base_url="http://ollama:11434", api_key="ollama")

# WRONG: trailing slash after /v1
client = OpenAI(base_url="http://ollama:11434/v1/", api_key="ollama")
```

[CITED: https://docs.ollama.com/api/openai-compatibility]

### 3.2 Available Endpoints

| Endpoint | Method | OpenAI SDK Method | Supported |
|----------|--------|-------------------|-----------|
| `/v1/chat/completions` | POST | `client.chat.completions.create()` | Yes |
| `/v1/completions` | POST | `client.completions.create()` | Yes |
| `/v1/embeddings` | POST | `client.embeddings.create()` | Yes |
| `/v1/models` | GET | `client.models.list()` | Yes |

**Not supported by Ollama:** `/v1/files`, `/v1/fine-tuning`, `/v1/images`, `/v1/audio`, `/v1/assistants`, and other OpenAI-specific endpoints.

### 3.3 Listing Models

```python
models_response = client.models.list()
model_names = [m.id for m in models_response.data]
# e.g., ["llama3.2:latest", "qwen2.5:7b", "mistral:latest"]
```

This is the simplest reliable way to test that Ollama is running and accessible.

### 3.4 Structured Output

Ollama supports structured output via the `response_format` parameter:

```python
response = client.chat.completions.create(
    model="llama3.2",
    messages=[{"role": "user", "content": "..."}],
    response_format={"type": "json_schema", "json_schema": {...}},
)
```

**However, we use Instructor** which wraps this for Pydantic model validation. Instructor handles the structured output negotiation automatically.

[CITED: https://docs.ollama.com/api/openai-compatibility]

### 3.5 Docker Networking -- Common Issues

When Composer runs in Docker and needs to reach Ollama:

**Scenario 1: Ollama on host machine, Composer in Docker**
```yaml
# docker-compose.yml
services:
  composer:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - OLLAMA_URL=http://host.docker.internal:11434
```

**Scenario 2: Both in Docker (same compose)**
```yaml
services:
  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
  composer:
    environment:
      - OLLAMA_URL=http://ollama:11434  # Use service name
```

**Scenario 3: Both in Docker (different compose)**
```yaml
# Ollama must be on a shared network
# Create: docker network create shared
services:
  composer:
    networks:
      - shared
    environment:
      - OLLAMA_URL=http://ollama:11434

networks:
  shared:
    external: true
```

**Common mistakes:**
- Using `localhost` from inside a container (refers to the container itself, not the host)
- Not binding Ollama to `0.0.0.0` (it may only listen on `127.0.0.1` by default). Set `OLLAMA_HOST=0.0.0.0` in the Ollama container environment.
- Firewall blocking container-to-host traffic

**Connection test pattern (our code):**
```python
async def test_ollama_connection(url: str) -> dict:
    try:
        client = OpenAI(base_url=f"{url}/v1", api_key="ollama")
        models_response = await asyncio.to_thread(client.models.list)
        models = [m.id for m in models_response.data]
        return {"success": True, "models": models}
    except Exception as e:
        # Handle timeout, connection refused, etc.
        ...
```

[CITED: https://github.com/ollama/ollama/issues/3652, https://github.com/ollama/ollama/issues/7444]

---

## 4. Cross-Cutting Patterns

### 4.1 asyncio.to_thread Pattern

All three API clients are synchronous. Our codebase wraps them with `asyncio.to_thread()` to avoid blocking the FastAPI event loop:

```python
# Correct: run synchronous API call in thread pool
plex = await asyncio.to_thread(PlexServer, url, token, timeout=30)
section = await asyncio.to_thread(lambda: plex.library.sectionByID(int(library_id)))
tracks = await asyncio.to_thread(section.searchTracks, container_start=0, container_size=200)
```

**Gotcha with lambdas:** When passing method calls that need arguments, use a lambda:
```python
# Works:
await asyncio.to_thread(lambda: plex.library.sectionByID(42))

# Also works (passing callable + args):
await asyncio.to_thread(section.searchTracks, container_start=0, container_size=200)

# Does NOT work (calls immediately in main thread):
await asyncio.to_thread(plex.library.sectionByID(42))  # WRONG: already called!
```

### 4.2 Connection Test Error Pattern

All three connection testers follow the same error classification:

```python
try:
    # ... attempt connection ...
    return {"success": True, ...}
except Exception as e:
    error_msg = str(e)
    if "401" in error_msg or "Unauthorized" in error_msg:
        return {"success": False, "error": "Authentication failed. ..."}
    if "timeout" in error_msg.lower():
        return {"success": False, "error": "Connection timed out. ..."}
    return {"success": False, "error": "Could not connect. ..."}
```

### 4.3 Token/Key Security

**Never log raw credentials.** Our `sync_service.py` includes a `_sanitize_error()` function that strips the Plex token from error messages:

```python
def _sanitize_error(error_msg: str, token: str) -> str:
    if token:
        error_msg = error_msg.replace(token, "[REDACTED]")
    return error_msg
```

Apply the same pattern to Lidarr API keys and any other credentials in error messages.

---

## 5. Known Issues and Gotchas Summary

| # | API | Issue | Impact | Mitigation |
|---|-----|-------|--------|------------|
| 1 | Plex | `addedAt>>` requires YYYY-MM-DD, not ISO | Crashes delta sync | Truncate to `[:10]` |
| 2 | pyarr | Class renamed from `LidarrAPI` to `Lidarr` in v6+ | Import fails | Use `from pyarr import Lidarr` |
| 3 | Plex | `section()` returns last match for duplicate titles | Wrong library | Use `sectionByID()` |
| 4 | Plex | `totalSize` is a server query, not cached data | Unexpected latency | Cache the value |
| 5 | Lidarr | `lookup_artist` depends on api.lidarr.audio (external) | 503/524 errors | Retry with backoff |
| 6 | Lidarr | Profile IDs vary per installation | Hardcoded IDs break | Always fetch dynamically |
| 7 | Lidarr | `add_artist` requires metadataProfileId | 400 error | Fetch profiles first |
| 8 | Lidarr | pyarr mutates the artist dict passed to `add_artist` | Unexpected side effects | Pass a copy if reusing |
| 9 | Ollama | base_url must end with `/v1` | Connection fails | Always append `/v1` |
| 10 | Ollama | Uses `localhost` in Docker | Connection refused | Use service name or host.docker.internal |
| 11 | Plex | Track fields can be None (grandparentTitle, duration, year) | NoneType errors | Always use defensive access with defaults |
| 12 | Plex | `media` list can be empty for unscanned items | IndexError on file path access | Check `t.media` before indexing |
| 13 | Plex | ratingKeys can change after library rebuild | Stale references in DB | Detect 404s, trigger re-sync |

---

## Sources

### Primary (HIGH confidence -- verified from source code)
- PlexAPI installed source: `plexapi/library.py`, `plexapi/audio.py`, `plexapi/base.py`, `plexapi/__init__.py`
- pyarr installed source: `pyarr/__init__.py`, `pyarr/lidarr.py`, `pyarr/base.py`
- Project source: `app/services/plex_client.py`, `app/services/lidarr_client.py`, `app/services/ollama_client.py`, `app/services/sync_service.py`

### Secondary (HIGH confidence -- official documentation)
- [PlexAPI Library Docs](https://python-plexapi.readthedocs.io/en/latest/modules/library.html)
- [PlexAPI Audio Docs](https://python-plexapi.readthedocs.io/en/latest/modules/audio.html)
- [pyarr Lidarr Docs](https://docs.totaldebug.uk/pyarr/modules/lidarr.html)
- [pyarr Quick Start](https://docs.totaldebug.uk/pyarr/quickstart.html)
- [Ollama OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility)
- [pyarr GitHub Releases](https://github.com/totaldebug/pyarr/releases)

### Tertiary (MEDIUM confidence -- community sources)
- [Plex Forum: Rate Limiting](https://forums.plex.tv/t/rate-limiting-for-plex-api-calls/877894) -- local servers not rate-limited
- [Ollama Docker Networking Issues](https://github.com/ollama/ollama/issues/3652) -- container connection patterns
- [Ollama Connection Refused](https://github.com/ollama/ollama/issues/7444) -- bind to 0.0.0.0

---

*API Reference for Composer project*
*Researched: 2026-04-09*
*Valid until: 2026-05-09 (30 days -- APIs are stable)*
