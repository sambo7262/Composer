---
type: feature-note
phase: 4
created: 2026-04-09
priority: high
---

# AI Chat is the Primary Interface

**For Phase 4: Playlist Generation**

The AI conversation is the **main way users interact with Composer** — not a secondary feature. The app's core UX is a chat-like interface where the user talks to the AI to build playlists.

## Core Flows

**Flow 1: Generate from scratch**
1. User opens the app and sees a chat/prompt interface as the primary view
2. Types a mood/vibe: "Give me an energetic late night electronic playlist"
3. AI generates a playlist, displayed inline in the conversation
4. User refines: "Add more house, remove the downtempo tracks"
5. AI adjusts the playlist and shows the updated version
6. User: "Perfect, but swap track 3 for something by Bicep"
7. AI makes the swap
8. User: "Push this to Plex as 'Late Night Energy'"
9. Done

**Flow 2: Build on existing Plex playlist**
1. User: "Take my 'Weekend Vibes' playlist from Plex and suggest some additions"
2. AI loads the playlist, analyzes the mood/energy profile of existing tracks
3. AI suggests additions that match the vibe: "Based on what's in there, here are 10 tracks that fit"
4. User: "Add tracks 1, 3, and 7 but not the others"
5. AI updates the playlist
6. User: "Actually, can you also find something similar to track 5 but more upbeat?"
7. AI suggests alternatives
8. User: "Push the updated version back to Plex"
9. Done

**Flow 3: Library exploration**
1. User: "What jazz do I have?"
2. AI queries the library and shows results
3. User: "Make a playlist from the best of those"
4. AI curates a selection based on audio features
5. Normal refinement flow continues

## Key Design Principles

- **Chat is the home page** — the conversation interface should be front and center, not buried behind menus
- **Iterative refinement** — the AI maintains conversation context so each message builds on the last
- **Playlist visible alongside chat** — user can see the current playlist state while chatting
- **Natural language for everything** — add, remove, reorder, rename, push — all via conversation
- **Session memory** — the AI knows what it already generated and can refine incrementally

## Implementation Considerations

- Chat-like session state: LLM sees conversation history + current playlist state
- Playlist displayed as a live-updating panel/section alongside the chat
- Each AI response that modifies the playlist should show what changed
- "Push to Plex" as a conversational action, not a separate button flow
- Consider: should the chat also handle library queries? ("What jazz do I have?")
