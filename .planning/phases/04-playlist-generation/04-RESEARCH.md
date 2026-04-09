# Phase 4: Playlist Generation - Research

**Researched:** 2026-04-09
**Domain:** AI chat interface, structured LLM output, feature-based track scoring, Plex playlist API
**Confidence:** HIGH

## Summary

Phase 4 is the app's core experience: a chat interface where users describe a mood and the AI generates a playlist from their library. The architecture is a three-step pipeline: (1) LLM interprets mood into structured feature criteria via Instructor, (2) app pre-filters SQLite candidates by feature distance scoring, (3) LLM makes final selection and ordering from candidates. The chat IS the home page, with playlist cards rendered inline in the conversation flow.

The stack is well-supported: Instructor v1.14.5 works with Ollama via OpenAI SDK's `base_url` override, PlexAPI has full playlist CRUD (create, addItems, removeItems, moveItem), and Alpine.js has a first-party Sort plugin backed by SortableJS for drag-and-drop reordering. Conversation state is server-side (in-memory dict keyed by session ID), keeping the architecture simple for a single-user app.

**Primary recommendation:** Use `instructor.from_openai()` with an OpenAI client pointed at Ollama for structured output, weighted Euclidean distance for track scoring, server-side session state for chat history, and HTMX `hx-post` with partial swaps for the chat message flow (no SSE/streaming needed for v1).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Chat IS the home page -- `/` becomes the chat interface after Plex is configured (replaces welcome page). Library stays at `/library`.
- **D-02:** Input at the top of the page (like a search engine), results flow below. Not fixed-bottom like iMessage.
- **D-03:** Playlist appears as an inline card inside the chat flow -- single column, scroll to see everything. No split panels.
- **D-04:** Mood preset buttons (Chill, Energetic, Focus, Late Night, Workout) shown on empty state only -- they disappear once the conversation starts. Conversation starters, not permanent UI.
- **D-05:** Mobile-first / portrait-first layout -- everything stacks vertically, no side-by-side panels.
- **D-06:** "Generate" or "Compose" added to the top nav bar as the primary link (replaces home).
- **D-07:** Hybrid approach -- LLM interprets mood into feature criteria + genre/artist preferences, app pre-filters SQLite to ~100-500 candidates, LLM makes final selection and ordering from those candidates.
- **D-08:** Full conversation context -- AI sees conversation history + current playlist state on each message. Each refinement builds on the last.
- **D-09:** Structured output via Instructor -- LLM returns typed Pydantic models (FeatureCriteria for filtering, PlaylistSelection for track picks) not free-form text.
- **D-10:** Three conversation flows supported: generate from scratch, build on existing Plex playlist (Phase 5 scope), library exploration.
- **D-11:** Tracks without audio features use metadata fallback (genre/year/artist).
- **D-12:** Interactive playlist card -- track list with X buttons to remove and drag handles to reorder. Manual edits are immediate.
- **D-13:** Smart edits via chat -- "add more like track 3", "remove the downtempo tracks", "swap track 5 for something by Bicep".
- **D-14:** Track count set via a slider/input control near the chat input. Default 20.
- **D-15:** "Push to Plex" button on the playlist card. Shows a name input field.
- **D-16:** After push: success message in the chat, conversation continues.
- **D-17:** Pushed playlists are recorded in the database (playlist history -- for Phase 5's history view).

### Claude's Discretion
- Exact Instructor model/schema for feature criteria and playlist selection
- How to format the candidate track list for the LLM (compact CSV vs structured)
- Chat message rendering (markdown? plain text?)
- How to handle "I don't have enough tracks matching this mood" edge case
- Drag-and-drop implementation (Alpine.js + Sortable.js or HTMX)
- Track count slider range and step values
- System prompt for the LLM (persona, instructions, constraints)

### Deferred Ideas (OUT OF SCOPE)
- "Surprise me" button -- Phase 4 stretch or Phase 5
- Mood timeline visualization -- Phase 5
- Playlist templates (save a prompt as reusable) -- Phase 5
- "What's playing" context from Plexamp -- v2+
- Smart shuffle into existing playlists -- Phase 5
- Build on existing Plex playlist flow -- technically Phase 5 scope, but chat should handle the request gracefully
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PLAY-01 | User can describe a mood and receive a matching playlist | Chat interface + LLM interpretation via Instructor + feature scoring pipeline |
| PLAY-02 | Ollama LLM interprets mood into structured audio feature criteria | Instructor with OpenAI SDK + FeatureCriteria Pydantic model |
| PLAY-03 | App scores library tracks against mood criteria using weighted distance | Weighted Euclidean distance on normalized features (energy, tempo, danceability, valence) |
| PLAY-04 | User can specify track count for generated playlist | Track count slider/input control, passed to scoring pipeline |
| PLAY-05 | User can review and edit playlist (add, remove, reorder) before pushing | Alpine.js Sort plugin for drag-drop, HTMX for remove, chat for smart edits |
| PLAY-06 | User can push finalized playlist to Plex as a named playlist | PlexAPI `createPlaylist()` with track items fetched by ratingKey |
</phase_requirements>

## Standard Stack

### Core (New for Phase 4)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Instructor | 1.14.5 | Structured LLM output | Wraps OpenAI SDK with Pydantic model validation, auto-retries on parse failure. 3M+ monthly PyPI downloads. Works with Ollama via from_openai() or from_provider(). | 
| Alpine.js Sort Plugin | 3.x | Drag-and-drop reordering | First-party Alpine.js plugin backed by SortableJS. Integrates with existing Alpine.js already in the project. |

[VERIFIED: instructor 1.14.5 on PyPI, released 2026-01-29]
[VERIFIED: Alpine.js Sort plugin at alpinejs.dev/plugins/sort]

### Already in Project (Reuse)

| Library | Version | Purpose | Role in Phase 4 |
|---------|---------|---------|-----------------|
| OpenAI Python SDK | >=2.31 | LLM transport | Already in requirements.txt. Instructor wraps it for structured output. |
| PlexAPI | >=4.18 | Plex integration | Playlist creation via `createPlaylist()`, track fetch via `fetchItem()` |
| HTMX | 2.x | Dynamic UI | Chat message submission, partial swaps for responses |
| Alpine.js | 3.x | Client interactivity | Drag-drop, mood preset buttons, playlist card state |
| Tailwind CSS | 4.x | Styling | Chat UI, playlist card, responsive layout |
| SQLModel | 0.0.38 | ORM | Track queries, new Playlist/PlaylistTrack models |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Instructor | Raw Ollama JSON mode | No validation, no retries, manual schema enforcement |
| Alpine.js Sort | SortableJS directly | Extra JS setup; Alpine Sort plugin is declarative and already fits the stack |
| Server-side session dict | Redis / SQLite sessions | Overkill for single-user app; in-memory dict is sufficient |
| Weighted Euclidean distance | Cosine similarity | Euclidean with weights is more intuitive for feature ranges; cosine better for high-dimensional sparse vectors |
| HTMX hx-post | SSE streaming | Streaming adds complexity; for v1, synchronous response is fine since Ollama responds in 2-10s |

**Installation:**
```bash
pip install instructor>=1.14,<2.0
```

**Alpine.js Sort Plugin (CDN):**
```html
<script defer src="https://cdn.jsdelivr.net/npm/@alpinejs/[email protected]/dist/cdn.min.js"></script>
```

## Architecture Patterns

### Recommended Project Structure

```
app/
├── routers/
│   ├── api_chat.py          # POST /api/chat/message, POST /api/chat/push-to-plex
│   └── pages.py             # Modified: / route becomes chat page
├── services/
│   ├── chat_service.py      # Conversation state, LLM orchestration
│   ├── playlist_engine.py   # Feature scoring, candidate filtering, track selection
│   └── ollama_client.py     # Extended: Instructor-patched client for structured output
├── models/
│   ├── track.py             # Existing Track model (query target)
│   ├── playlist.py          # NEW: Playlist + PlaylistTrack models (D-17 history)
│   └── schemas.py           # NEW: Pydantic schemas for LLM I/O (FeatureCriteria, PlaylistSelection)
├── templates/
│   ├── pages/
│   │   └── chat.html        # NEW: Chat page (replaces home.html as / route)
│   └── partials/
│       ├── chat_message.html     # User/AI message bubble
│       ├── playlist_card.html    # Inline playlist with drag-drop, remove, push
│       └── nav.html              # Modified: add "Compose" link
```

### Pattern 1: Instructor + Ollama Client Setup

**What:** Create an Instructor-patched OpenAI client that connects to the user's configured Ollama endpoint.
**When to use:** Every LLM call in the chat service.

```python
# Source: https://python.useinstructor.com/integrations/ollama/
import instructor
from openai import OpenAI
from app.services.settings_service import get_setting

def get_instructor_client(session) -> instructor.Instructor:
    """Create an Instructor-patched OpenAI client from saved Ollama settings."""
    ollama_setting = get_setting(session, "ollama")
    url = ollama_setting.url.rstrip("/")
    model_name = ollama_setting.extra_config.get("model_name", "llama3.1:8b")

    openai_client = OpenAI(
        base_url=f"{url}/v1",
        api_key="ollama",
    )
    return instructor.from_openai(openai_client, mode=instructor.Mode.JSON)
```

[VERIFIED: instructor from_openai() with OpenAI client pointed at Ollama]

### Pattern 2: Two-Phase LLM Pipeline (D-07)

**What:** Separate mood interpretation from track selection. Two Instructor calls per generation.
**When to use:** Every "generate playlist" request.

```
User: "Give me energetic late night electronic"
  │
  ▼
[LLM Call 1: Interpret Mood → FeatureCriteria]
  │  Returns: energy_range, tempo_range, danceability_range, valence_range,
  │           genre_filters, artist_preferences, explanation
  ▼
[App: Score & Filter SQLite → ~100-500 candidates]
  │  Weighted Euclidean distance on features
  │  Metadata fallback for unanalyzed tracks
  ▼
[LLM Call 2: Curate Playlist → PlaylistSelection]
  │  Input: Candidate list (compact format) + user request + conversation context
  │  Returns: selected track IDs in order, explanation
  ▼
[Render: Playlist card in chat]
```

### Pattern 3: Chat Message Flow with HTMX

**What:** User submits message via HTMX, server processes and returns HTML partials.
**When to use:** Every chat interaction.

```html
<!-- Chat input form -->
<form hx-post="/api/chat/message"
      hx-target="#chat-messages"
      hx-swap="beforeend"
      hx-indicator="#chat-loading">
    <input type="text" name="message" placeholder="Describe a mood..."
           class="w-full" autocomplete="off">
    <input type="hidden" name="session_id" value="{{ session_id }}">
    <input type="hidden" name="track_count" value="20">
</form>

<!-- Chat messages container -->
<div id="chat-messages">
    <!-- Messages and playlist cards appended here via HTMX -->
</div>
```

Server returns two HTML fragments concatenated:
1. User message bubble
2. AI response (text + playlist card if applicable)

Both are appended to `#chat-messages` via `hx-swap="beforeend"`.

### Pattern 4: Server-Side Session State

**What:** In-memory dict stores conversation history and current playlist per session.
**When to use:** Single-user app, no persistence needed across server restarts.

```python
from dataclasses import dataclass, field
from typing import Optional
import uuid

@dataclass
class ChatSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict] = field(default_factory=list)  # OpenAI message format
    current_playlist: list[int] = field(default_factory=list)  # Track IDs in order
    track_count: int = 20

# Module-level singleton (single-user app)
_sessions: dict[str, ChatSession] = {}

def get_or_create_session(session_id: Optional[str] = None) -> ChatSession:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    session = ChatSession()
    _sessions[session.session_id] = session
    return session
```

[ASSUMED: In-memory session is sufficient for single-user. If the user restarts the server, conversations are lost -- acceptable for v1.]

### Pattern 5: Playlist Push to Plex

**What:** Fetch track objects by ratingKey, create a Plex playlist.
**When to use:** When user clicks "Push to Plex".

```python
# Source: PlexAPI source code (verified from .venv/plexapi/playlist.py, server.py)
import asyncio
from plexapi.server import PlexServer

async def push_playlist_to_plex(
    plex_url: str, plex_token: str, library_id: str,
    playlist_name: str, rating_keys: list[str]
) -> dict:
    """Create a Plex playlist from a list of track ratingKeys."""
    def _create():
        plex = PlexServer(plex_url, plex_token, timeout=30)
        # Fetch track objects by ratingKey
        tracks = [plex.fetchItem(int(key)) for key in rating_keys]
        # Create playlist with all tracks
        playlist = plex.createPlaylist(title=playlist_name, items=tracks)
        return {"title": playlist.title, "count": len(tracks)}

    return await asyncio.to_thread(_create)
```

[VERIFIED: PlexAPI `createPlaylist(title=, items=)` accepts list of track objects]
[VERIFIED: PlexAPI `fetchItem(int_key)` fetches item by ratingKey when int is passed]
[VERIFIED: PlexAPI playlist has `addItems()`, `removeItems()`, `moveItem()` methods]

### Anti-Patterns to Avoid

- **Streaming LLM responses to UI in v1:** Adds SSE/WebSocket complexity. Ollama llama3.1:8b responds in 2-10 seconds -- show a loading indicator instead. Streaming can be added in v2.
- **Sending all tracks to the LLM:** Token limit issue. A 10K track library would exceed any context window. Always pre-filter to ~100-500 candidates first (D-07).
- **Storing chat history in SQLite:** Overkill for v1. In-memory dict works for single-user. History of *pushed playlists* goes to SQLite (D-17), but conversation ephemeral state does not.
- **Using cosine similarity for 4-8 feature dimensions:** Cosine similarity is designed for high-dimensional sparse vectors. With only 4-8 weighted features, weighted Euclidean distance is more interpretable and gives better results.
- **Hardcoding the Ollama model name:** The model is already configurable in settings (stored in `extra_config.model_name`). Always read from settings.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Structured LLM output | JSON parsing + regex extraction | Instructor library | Automatic retries, Pydantic validation, handles LLM quirks |
| Drag-and-drop reordering | Custom drag event handlers | Alpine.js Sort plugin | Declarative, handles touch devices, backed by battle-tested SortableJS |
| Plex playlist CRUD | Raw HTTP to Plex API | PlexAPI library | Handles auth tokens, XML parsing, ratingKey resolution |
| Session ID generation | Custom ID scheme | Python uuid4 | Cryptographically random, zero collisions |
| Feature distance calculation | Custom ML pipeline | Simple weighted Euclidean | 4-8 dimensions, no need for sklearn/numpy -- pure Python math is fine |

**Key insight:** The complexity in this phase is in the *orchestration* (LLM interpretation -> filtering -> selection -> rendering), not in individual components. Each component has a well-supported library. The value is in gluing them together correctly.

## Common Pitfalls

### Pitfall 1: Instructor Mode Selection for Ollama
**What goes wrong:** Using `instructor.Mode.TOOLS` with Ollama models that don't support function calling, causing parse failures.
**Why it happens:** Not all Ollama models support tool/function calling. The auto-detection in `from_provider()` handles this, but manual `from_openai()` does not.
**How to avoid:** Use `instructor.Mode.JSON` explicitly when wrapping an OpenAI client for Ollama. JSON mode is universally supported by all Ollama models. Alternatively, use `from_provider("ollama/llama3.1")` which auto-selects the correct mode.
**Warning signs:** `instructor.exceptions.InstructorRetryException` with repeated failures.

[VERIFIED: instructor docs state JSON mode works universally; TOOLS mode requires specific model support]

### Pitfall 2: Token Limits with Candidate Track Lists
**What goes wrong:** Sending too many candidate tracks to the LLM, exceeding context window or degrading quality.
**Why it happens:** llama3.1:8b has 128K context but quality degrades significantly after ~4K tokens of structured data.
**How to avoid:** Format candidates compactly. Use CSV-like format: `"ID|Title|Artist|Energy|Tempo|Dance|Valence"` -- one line per track. Cap candidates at 200-300 tracks. This keeps the candidate section under 2K tokens.
**Warning signs:** LLM responses become incoherent, repeat track selections, or ignore later candidates.

[ASSUMED: Quality degradation threshold of ~4K tokens for structured data is based on general local LLM behavior]

### Pitfall 3: Blocking the Event Loop with Ollama Calls
**What goes wrong:** LLM inference takes 2-30 seconds, blocking all other requests.
**Why it happens:** The OpenAI SDK (and thus Instructor) is synchronous. FastAPI runs on asyncio.
**How to avoid:** Wrap all Instructor calls with `asyncio.to_thread()`. This is already the established pattern in the codebase (see `ollama_client.py`, `plex_client.py`).
**Warning signs:** UI freezes during generation, other endpoints become unresponsive.

[VERIFIED: Existing codebase uses asyncio.to_thread() pattern consistently]

### Pitfall 4: Unanalyzed Tracks Dominating Results
**What goes wrong:** When most tracks lack Essentia features, the metadata fallback produces homogeneous scores (all tracks score ~0.5), making selection meaningless.
**Why it happens:** `metadata_feature_vector()` returns coarse estimates based on genre alone -- many tracks share the same genre.
**How to avoid:** Weight analyzed tracks higher in scoring. Add a small bonus (e.g., 0.1) to distance scores for tracks with real audio features. Or: partition candidates into "analyzed" and "fallback" groups, preferring analyzed tracks but filling from fallback if needed.
**Warning signs:** Playlist contains random tracks with no coherent mood.

[ASSUMED: Weighting strategy is a heuristic recommendation]

### Pitfall 5: Energy Feature Range Mismatch
**What goes wrong:** LLM outputs energy criteria in [0, 1] range but stored `spectral_rms.mean` values are in [0.0001, 0.3].
**Why it happens:** Essentia's spectral RMS is a physical measurement, not a perceptual 0-1 scale.
**How to avoid:** Normalize energy values before scoring. Either: (a) normalize across the library (min-max scaling), or (b) use percentile ranking. The LLM should output criteria in the normalized space.
**Warning signs:** Energy filtering produces unexpected results -- "high energy" returns quiet ambient tracks.

[VERIFIED: Essentia spectral_rms.mean range is [0, ~0.3] for real music -- ESSENTIA-REFERENCE.md]

### Pitfall 6: PlexAPI fetchItem N+1 Problem
**What goes wrong:** Fetching 20 tracks individually for playlist creation makes 20 HTTP requests to Plex.
**Why it happens:** `plex.fetchItem(ratingKey)` makes one HTTP call per track.
**How to avoid:** Use `plex.fetchItems(f"/library/metadata/{','.join(rating_keys)}")` to batch fetch. PlexAPI supports comma-separated ratingKeys in a single URL.
**Warning signs:** "Push to Plex" takes 5-10 seconds instead of <1 second.

[VERIFIED: PlexAPI addItems() internally joins ratingKeys with commas in the URI]

## Code Examples

### FeatureCriteria Pydantic Schema (Instructor Response Model)

```python
# Source: Claude's discretion per CONTEXT.md
from pydantic import BaseModel, Field
from typing import Optional

class FeatureCriteria(BaseModel):
    """LLM's interpretation of a mood/vibe into audio feature ranges."""
    energy_min: float = Field(ge=0, le=1, description="Minimum energy level (0=calm, 1=intense)")
    energy_max: float = Field(ge=0, le=1, description="Maximum energy level")
    tempo_min: float = Field(ge=40, le=220, description="Minimum BPM")
    tempo_max: float = Field(ge=40, le=220, description="Maximum BPM")
    danceability_min: float = Field(ge=0, le=1, description="Minimum danceability (0=freeform, 1=steady beat)")
    danceability_max: float = Field(ge=0, le=1, description="Maximum danceability")
    valence_min: float = Field(ge=0, le=1, description="Minimum positivity (0=dark/sad, 1=bright/happy)")
    valence_max: float = Field(ge=0, le=1, description="Maximum positivity")
    genres: list[str] = Field(default_factory=list, description="Preferred genres (empty = no preference)")
    artists: list[str] = Field(default_factory=list, description="Preferred artists (empty = no preference)")
    exclude_genres: list[str] = Field(default_factory=list, description="Genres to exclude")
    explanation: str = Field(description="Brief explanation of the mood interpretation")


class TrackSelection(BaseModel):
    """LLM's final track selection from candidates."""
    track_ids: list[int] = Field(description="Ordered list of track IDs for the playlist")
    explanation: str = Field(description="Brief description of the playlist vibe and why these tracks were chosen")
```

[ASSUMED: Schema design is Claude's discretion. Field descriptions guide the LLM.]

### Weighted Euclidean Distance Scoring

```python
# Source: Standard distance scoring pattern
import math
from app.models.track import Track

# Feature weights -- higher = more important for mood matching
FEATURE_WEIGHTS = {
    "energy": 1.0,
    "danceability": 0.8,
    "valence": 0.7,
    "tempo": 0.5,  # Tempo is less discriminating for mood
}

def score_track(track: Track, criteria: FeatureCriteria) -> float:
    """Score a track against mood criteria. Lower = better match."""
    distance = 0.0
    count = 0

    if track.energy is not None:
        # Energy needs normalization: raw [0, 0.3] -> [0, 1]
        energy_norm = min(track.energy / 0.3, 1.0)
        target = (criteria.energy_min + criteria.energy_max) / 2
        d = (energy_norm - target) ** 2 * FEATURE_WEIGHTS["energy"]
        # Penalize if outside range
        if energy_norm < criteria.energy_min or energy_norm > criteria.energy_max:
            d *= 2.0
        distance += d
        count += 1

    if track.danceability is not None:
        target = (criteria.danceability_min + criteria.danceability_max) / 2
        d = (track.danceability - target) ** 2 * FEATURE_WEIGHTS["danceability"]
        if track.danceability < criteria.danceability_min or track.danceability > criteria.danceability_max:
            d *= 2.0
        distance += d
        count += 1

    if track.valence is not None:
        target = (criteria.valence_min + criteria.valence_max) / 2
        d = (track.valence - target) ** 2 * FEATURE_WEIGHTS["valence"]
        if track.valence < criteria.valence_min or track.valence > criteria.valence_max:
            d *= 2.0
        distance += d
        count += 1

    if track.tempo is not None and criteria.tempo_min and criteria.tempo_max:
        # Normalize tempo to [0, 1] range (40-220 BPM)
        tempo_norm = (track.tempo - 40) / 180
        target_norm = ((criteria.tempo_min + criteria.tempo_max) / 2 - 40) / 180
        d = (tempo_norm - target_norm) ** 2 * FEATURE_WEIGHTS["tempo"]
        if track.tempo < criteria.tempo_min or track.tempo > criteria.tempo_max:
            d *= 2.0
        distance += d
        count += 1

    if count == 0:
        return 1.0  # No features available -- worst score

    return math.sqrt(distance / count)
```

[ASSUMED: Weight values and normalization ranges are initial recommendations. Tuning will be needed.]

### Compact Candidate Format for LLM

```python
def format_candidates_for_llm(tracks: list[Track], limit: int = 300) -> str:
    """Format candidate tracks compactly for LLM context.

    Uses pipe-delimited format to minimize tokens while keeping data readable.
    ~10 tokens per track line = 300 tracks ~ 3000 tokens.
    """
    lines = ["ID|Title|Artist|Genre|Energy|Tempo|Dance|Valence"]
    for t in tracks[:limit]:
        energy = f"{t.energy:.2f}" if t.energy else "?"
        tempo = f"{t.tempo:.0f}" if t.tempo else "?"
        dance = f"{t.danceability:.2f}" if t.danceability else "?"
        valence = f"{t.valence:.2f}" if t.valence else "?"
        genre = t.genre[:20] if t.genre else "?"
        lines.append(f"{t.id}|{t.title[:40]}|{t.artist[:30]}|{genre}|{energy}|{tempo}|{dance}|{valence}")
    return "\n".join(lines)
```

[ASSUMED: Pipe-delimited format is a token-efficient choice; could also use JSON]

### Instructor Call with asyncio.to_thread

```python
# Source: Established project pattern + instructor docs
import asyncio
import instructor
from openai import OpenAI

async def interpret_mood(
    client: instructor.Instructor,
    model: str,
    messages: list[dict],
) -> FeatureCriteria:
    """Call LLM to interpret mood into feature criteria."""
    return await asyncio.to_thread(
        client.chat.completions.create,
        model=model,
        response_model=FeatureCriteria,
        messages=messages,
        max_retries=2,
        timeout=30.0,
    )
```

[VERIFIED: asyncio.to_thread pattern used throughout codebase]
[VERIFIED: instructor supports timeout parameter for Ollama]

### PlexAPI Playlist Creation

```python
# Source: PlexAPI source code (verified from installed .venv)
import asyncio
from plexapi.server import PlexServer

async def create_plex_playlist(
    url: str, token: str, name: str, rating_keys: list[str]
) -> dict:
    """Create a named playlist on Plex from track ratingKeys."""
    def _create():
        plex = PlexServer(url, token, timeout=30)
        # Batch fetch: comma-separated ratingKeys in a single request
        key_str = ",".join(str(k) for k in rating_keys)
        tracks = plex.fetchItems(f"/library/metadata/{key_str}")
        playlist = plex.createPlaylist(title=name, items=tracks)
        return {
            "success": True,
            "title": playlist.title,
            "ratingKey": playlist.ratingKey,
            "track_count": len(tracks),
        }
    return await asyncio.to_thread(_create)
```

[VERIFIED: PlexAPI `fetchItems(ekey)` supports comma-separated ratingKeys in path]
[VERIFIED: PlexAPI `createPlaylist(title=, items=)` creates regular playlist]

### Alpine.js Sort Plugin for Playlist Card

```html
<!-- Source: https://alpinejs.dev/plugins/sort -->
<div x-data="{ tracks: {{ playlist_tracks_json }} }">
    <ul x-sort="$el.dispatchEvent(new CustomEvent('reorder', {detail: {item: $item, position: $position}}))"
        hx-trigger="reorder"
        hx-post="/api/chat/reorder"
        hx-vals='js:{session_id: "{{ session_id }}", track_id: event.detail.item, position: event.detail.position}'
        hx-swap="none">
        <template x-for="track in tracks" :key="track.id">
            <li x-sort:item="track.id" class="flex items-center gap-2 py-2">
                <span x-sort:handle class="cursor-grab text-text-secondary">&#9776;</span>
                <span x-text="track.title" class="flex-1 truncate"></span>
                <span x-text="track.artist" class="text-text-secondary text-sm truncate"></span>
                <button hx-post="/api/chat/remove-track"
                        hx-vals='{"session_id": "{{ session_id }}", "track_id": track.id}'
                        hx-target="closest li"
                        hx-swap="outerHTML"
                        class="text-text-secondary hover:text-red-400">
                    &times;
                </button>
            </li>
        </template>
    </ul>
</div>
```

[VERIFIED: Alpine.js Sort plugin uses x-sort, x-sort:item, x-sort:handle directives]

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `instructor.patch()` | `instructor.from_openai()` or `from_provider()` | Instructor 1.x | Cleaner API, better mode auto-detection |
| Ollama native API | OpenAI-compatible `/v1` endpoint | Ollama 0.1.24+ | Standard SDK works, no Ollama-specific client needed |
| Manual JSON extraction from LLM | Instructor structured output | 2024 | Eliminates regex/parsing, adds validation + retries |

**Deprecated/outdated:**
- `instructor.patch()` still works but `from_openai()` / `from_provider()` is the recommended approach [VERIFIED: instructor docs]
- Old Ollama Python client (`ollama` package) -- not needed when using OpenAI SDK compatibility [VERIFIED: project already uses OpenAI SDK]

## Assumptions Log

> List all claims tagged [ASSUMED] in this research.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | In-memory session dict sufficient for single-user | Architecture Patterns | Conversations lost on restart; if user expects persistence, need SQLite sessions |
| A2 | Feature weight values (energy=1.0, dance=0.8, valence=0.7, tempo=0.5) | Code Examples | Poor playlist quality; weights need tuning based on user feedback |
| A3 | Energy normalization divisor of 0.3 for spectral_rms | Code Examples | If library has tracks with RMS > 0.3, normalization clips to 1.0 (acceptable) |
| A4 | ~4K token quality degradation for structured data with llama3.1:8b | Pitfalls | If quality degrades earlier, reduce candidate count further |
| A5 | Pipe-delimited format is token-efficient for LLM | Code Examples | JSON might be more reliably parsed by the LLM; test both |
| A6 | Spectral complexity and loudness not needed for scoring | Code Examples | Missing features that could improve mood matching |

**If this table is empty:** N/A -- several assumptions flagged above.

## Open Questions

1. **Energy normalization strategy**
   - What we know: Raw spectral_rms ranges [0, 0.3] typically. The LLM will output criteria in [0, 1].
   - What's unclear: Should we normalize per-library (min-max across all tracks) or use a fixed divisor?
   - Recommendation: Start with fixed divisor (0.3), add per-library normalization later if results are poor.

2. **System prompt design for llama3.1:8b**
   - What we know: The system prompt must instruct the LLM on feature ranges, output format (handled by Instructor), and music knowledge.
   - What's unclear: How much musical knowledge llama3.1:8b has for genre/artist interpretation.
   - Recommendation: Keep system prompt concise (<500 tokens). Test with simple mood descriptions first. Include feature range descriptions so LLM outputs sensible values.

3. **Handling "not enough matching tracks"**
   - What we know: If fewer than `track_count` tracks score well, the playlist is thin.
   - What's unclear: What threshold defines "good enough" vs "poor match"?
   - Recommendation: If fewer than 50% of requested tracks are in the "good match" zone, include a warning in the AI response. Always fill the playlist even with mediocre matches.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Ollama | LLM inference | Yes (configured) | User-managed | None -- core dependency |
| llama3.1:8b model | LLM inference | User must pull | -- | Any Ollama model; settings already configurable |
| Instructor (Python) | Structured output | Not installed | 1.14.5 on PyPI | Must install: `pip install instructor` |
| Alpine.js Sort plugin | Drag-and-drop | Not included | 3.x | Must add CDN script tag |

**Missing dependencies with no fallback:**
- Instructor must be added to `requirements.txt`

**Missing dependencies with fallback:**
- Alpine.js Sort plugin: could alternatively use raw SortableJS, but plugin is simpler

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | None detected -- may need pytest.ini or pyproject.toml section |
| Quick run command | `python -m pytest tests/ -x -q` |
| Full suite command | `python -m pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PLAY-01 | Mood description returns playlist | integration | `pytest tests/test_chat_service.py::test_generate_playlist -x` | Wave 0 |
| PLAY-02 | LLM interprets mood into FeatureCriteria | unit | `pytest tests/test_schemas.py::test_feature_criteria_validation -x` | Wave 0 |
| PLAY-03 | Track scoring against criteria | unit | `pytest tests/test_playlist_engine.py::test_score_track -x` | Wave 0 |
| PLAY-04 | Track count parameter respected | unit | `pytest tests/test_playlist_engine.py::test_track_count -x` | Wave 0 |
| PLAY-05 | Playlist edit operations (add/remove/reorder) | unit | `pytest tests/test_chat_service.py::test_playlist_edits -x` | Wave 0 |
| PLAY-06 | Push to Plex creates playlist | integration | `pytest tests/test_plex_playlist.py::test_push_to_plex -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/ -x -q`
- **Per wave merge:** `python -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_playlist_engine.py` -- covers PLAY-03, PLAY-04 (scoring, filtering, track count)
- [ ] `tests/test_schemas.py` -- covers PLAY-02 (Pydantic model validation for FeatureCriteria, TrackSelection)
- [ ] `tests/test_chat_service.py` -- covers PLAY-01, PLAY-05 (chat flow, session state, playlist edits)
- [ ] `tests/test_plex_playlist.py` -- covers PLAY-06 (push to Plex, mocked PlexAPI)
- [ ] Instructor install: `pip install instructor>=1.14,<2.0` -- add to requirements.txt

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Single-user app, no auth |
| V3 Session Management | Minimal | Session IDs are UUID4, no auth to protect |
| V4 Access Control | No | Single-user app |
| V5 Input Validation | Yes | Pydantic models for all LLM I/O; user input sanitized before LLM prompt |
| V6 Cryptography | No | No crypto operations in this phase (existing encryption handles API keys) |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection via user chat input | Tampering | System prompt hardening; Instructor validates output structure |
| LLM outputting invalid track IDs | Tampering | Validate all track IDs against database before use |
| XSS in chat messages rendered in HTML | Tampering | Jinja2 auto-escaping (default); never use `| safe` on user input |
| Plex token in error messages | Information Disclosure | Existing `_sanitize_error()` pattern; apply to playlist creation errors |

## Sources

### Primary (HIGH confidence)
- [Instructor PyPI](https://pypi.org/project/instructor/) - v1.14.5, released 2026-01-29
- [Instructor Ollama Integration](https://python.useinstructor.com/integrations/ollama/) - from_provider() and from_openai() patterns
- [Instructor Patching Concepts](https://python.useinstructor.com/concepts/patching/) - Mode selection (JSON, TOOLS)
- PlexAPI installed source: `.venv/lib/python3.9/site-packages/plexapi/playlist.py` - create, addItems, removeItems, moveItem methods verified
- PlexAPI installed source: `.venv/lib/python3.9/site-packages/plexapi/server.py` - createPlaylist, fetchItem methods verified
- [Alpine.js Sort Plugin](https://alpinejs.dev/plugins/sort) - x-sort directive, drag handles, event callbacks
- ESSENTIA-REFERENCE.md - Feature ranges and normalization (spectral_rms, danceability, tempo, valence)
- API-REFERENCE.md - Ollama OpenAI compatibility, Plex connection patterns

### Secondary (MEDIUM confidence)
- [HTMX SSE Extension](https://htmx.org/extensions/sse/) - Referenced for future streaming; not used in v1
- [SortableJS](https://github.com/SortableJS/Sortable) - Underlying library for Alpine Sort plugin

### Tertiary (LOW confidence)
- Quality degradation threshold for llama3.1:8b structured data -- general LLM behavior observation, not measured

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries verified on PyPI/docs, versions confirmed
- Architecture: HIGH - Patterns based on existing codebase conventions and verified library APIs
- Pitfalls: MEDIUM - Some based on general LLM/Essentia experience, not project-specific testing
- Scoring algorithm: MEDIUM - Weight values and normalization are initial recommendations needing tuning

**Research date:** 2026-04-09
**Valid until:** 2026-05-09 (30 days -- Instructor and PlexAPI are stable)
