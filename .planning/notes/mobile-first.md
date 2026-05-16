---
type: design-note
created: 2026-04-09
priority: high
---

# Mobile-First / Portrait-First Design

**Applies to:** All phases with UI (especially Phase 4 chat interface)

The primary use case is accessing Composer from a phone in portrait mode. The app must be designed portrait-first, desktop second.

## Key implications

### Chat interface (Phase 4)
- Chat input at the bottom (thumb-reachable), like any messaging app
- Playlist results scroll vertically, not side-by-side panels
- Mood preset buttons should wrap/scroll horizontally in a compact row
- Playlist tracks displayed as a simple vertical list, not a wide table

### Library browse (Phase 2 — may need retrofit)
- Current table layout may not work well on narrow screens
- Consider: card-based track list on mobile, table on desktop
- Search bar should be prominent and always accessible

### Settings (Phase 1 — may need retrofit)
- Service cards should stack vertically (likely already do)
- Form inputs full-width on mobile

### General patterns
- Max-width container (already 720px from Phase 1) works well for mobile
- Touch targets minimum 44px (already in UI-SPEC)
- No hover-dependent interactions — everything must work with tap
- Nav bar should collapse or simplify on narrow screens (hamburger or bottom nav)

## When to implement
- Phase 4 UI-SPEC should be designed portrait-first from the start
- Phases 1 & 2 UI may need responsive tweaks — address during Phase 4 or as a polish pass
