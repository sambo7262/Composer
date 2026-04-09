# Phase 4: Playlist Generation - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

AI-powered playlist generation via a chat interface. User describes a mood, AI interprets it, app filters the library, AI curates the final playlist. User can iteratively refine via conversation, manually edit the playlist, and push to Plex. This is the app's core experience — the chat IS the home page.

</domain>

<decisions>
## Implementation Decisions

### Chat UI layout
- **D-01:** Chat IS the home page — `/` becomes the chat interface after Plex is configured (replaces welcome page). Library stays at `/library`.
- **D-02:** Input at the top of the page (like a search engine), results flow below. Not fixed-bottom like iMessage.
- **D-03:** Playlist appears as an inline card inside the chat flow — single column, scroll to see everything. No split panels.
- **D-04:** Mood preset buttons (Chill, Energetic, Focus, Late Night, Workout) shown on empty state only — they disappear once the conversation starts. Conversation starters, not permanent UI.
- **D-05:** Mobile-first / portrait-first layout — everything stacks vertically, no side-by-side panels.
- **D-06:** "Generate" or "Compose" added to the top nav bar as the primary link (replaces home).

### AI conversation flow
- **D-07:** Hybrid approach — LLM interprets mood into feature criteria + genre/artist preferences, app pre-filters SQLite to ~100-500 candidates, LLM makes final selection and ordering from those candidates.
- **D-08:** Full conversation context — AI sees conversation history + current playlist state on each message. Each refinement builds on the last.
- **D-09:** Structured output via Instructor — LLM returns typed Pydantic models (FeatureCriteria for filtering, PlaylistSelection for track picks) not free-form text.
- **D-10:** Three conversation flows supported:
  - Generate from scratch: "Give me an energetic late night electronic playlist"
  - Build on existing Plex playlist: "Take my 'Weekend Vibes' and suggest additions" (Phase 5 scope, but chat should handle the request gracefully)
  - Library exploration: "What jazz do I have?"
- **D-11:** Tracks without audio features use metadata fallback (genre/year/artist) — the pipeline works even before Essentia analysis completes.

### Playlist editing
- **D-12:** Interactive playlist card — rendered as a track list with X buttons to remove and drag handles to reorder. Manual edits are immediate.
- **D-13:** Smart edits via chat — "add more like track 3", "remove the downtempo tracks", "swap track 5 for something by Bicep". AI handles these using conversation context.
- **D-14:** Track count set via a slider/input control near the chat input. Default 20. User can adjust before or after generating.

### Push to Plex
- **D-15:** "Push to Plex" button on the playlist card. Shows a name input field. User names the playlist, hits push.
- **D-16:** After push: success message in the chat, conversation continues. User can keep refining or start a new playlist. Old playlist saved to history.
- **D-17:** Pushed playlists are recorded in the database (playlist history — for Phase 5's history view).

### Claude's Discretion
- Exact Instructor model/schema for feature criteria and playlist selection
- How to format the candidate track list for the LLM (compact CSV vs structured)
- Chat message rendering (markdown? plain text?)
- How to handle "I don't have enough tracks matching this mood" edge case
- Drag-and-drop implementation (Alpine.js + Sortable.js or HTMX)
- Track count slider range and step values
- System prompt for the LLM (persona, instructions, constraints)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Feature notes (user's vision)
- `.planning/notes/playlist-refinement.md` — AI-first chat UX with three core flows, iterative refinement, design principles
- `.planning/notes/feature-ideas.md` — Mood presets, "Surprise me", mood timeline, playlist templates
- `.planning/notes/mobile-first.md` — Portrait-first design requirements, chat input position, touch targets

### Project docs
- `.planning/REQUIREMENTS.md` — PLAY-01 through PLAY-06
- `.planning/research/STACK.md` — Instructor library for structured LLM output, Ollama via OpenAI SDK
- `.planning/research/API-REFERENCE.md` — Ollama OpenAI compatibility, Plex playlist API
- `.planning/research/ESSENTIA-REFERENCE.md` — Feature ranges and normalization for scoring

### Existing code
- `app/services/ollama_client.py` — Ollama connection (extend for chat completions)
- `app/models/track.py` — Track model with audio features (query target)
- `app/services/audio_analyzer.py` — `metadata_feature_vector()` for fallback scoring
- `app/database.py` — SQLite engine, session management
- `app/templates/base.html` — Base template, nav bar (update nav links)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ollama_client.py`: Has OpenAI SDK setup with `base_url` — extend for chat completions via Instructor
- `Track` model: All audio feature columns + metadata ready for querying
- `metadata_feature_vector()`: Fallback scoring when Essentia features unavailable
- HTMX patterns: Established for dynamic partial swaps
- Alpine.js: Available for client-side interactivity (drag/drop, toggles)

### Established Patterns
- `asyncio.to_thread()` for blocking calls (Ollama via OpenAI SDK is sync)
- FastAPI router organization: `api_*.py` for API, `pages.py` for HTML
- Jinja2 template inheritance from `base.html`
- HTMX `hx-post`/`hx-get` with `hx-target` for partial updates

### Integration Points
- `pages.py` — Change `/` route from welcome/home to chat interface
- Nav bar — Add "Compose" as primary link
- New `api_chat.py` router — Chat message endpoint
- New `app/services/playlist_engine.py` — Feature scoring and track selection
- New `app/services/chat_service.py` — Conversation state and LLM orchestration
- PlexAPI — Create playlist and add tracks (for push to Plex)

</code_context>

<specifics>
## Specific Ideas

- The chat should feel conversational, not like a form. "Give me something chill" should just work.
- Mood presets are conversation starters: tapping "Chill" is the same as typing "Give me a chill playlist"
- The playlist card inline in the chat should feel like a rich message bubble — not a separate page
- Even before Essentia finishes analyzing all tracks, the app should work using metadata fallback
- User specifically wants to be able to say things like "add more house, remove the downtempo" — iterative refinement is core

</specifics>

<deferred>
## Deferred Ideas

- "Surprise me" button — could be Phase 4 stretch or Phase 5
- Mood timeline visualization — Phase 5
- Playlist templates (save a prompt as reusable) — Phase 5
- "What's playing" context from Plexamp — v2+
- Smart shuffle into existing playlists — Phase 5
- Build on existing Plex playlist flow — technically Phase 5 scope, but chat should handle the request gracefully (e.g., "I'll add that in a future update" or route to Phase 5 functionality)

</deferred>

---

*Phase: 04-playlist-generation*
*Context gathered: 2026-04-09*
