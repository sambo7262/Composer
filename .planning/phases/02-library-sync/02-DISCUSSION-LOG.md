# Phase 2: Library Sync - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-09
**Phase:** 02-library-sync
**Areas discussed:** Sync trigger & scheduling, Progress display, Library browse view

---

## Sync Trigger & Scheduling

| Option | Description | Selected |
|--------|-------------|----------|
| Manual button | A 'Sync Library' button. You press it when you want. | |
| Auto + manual | Auto-sync on startup plus manual button. | |
| Scheduled + manual | Auto-sync on schedule plus manual button. Most hands-off. | ✓ |

**User's choice:** Scheduled + manual

| Option | Description | Selected |
|--------|-------------|----------|
| Every 6 hours | Catches new music a few times a day | |
| Every 24 hours | Once daily, low overhead | |
| Configurable | User sets the interval in settings | ✓ |

**User's choice:** Configurable

---

## Progress Display

| Option | Description | Selected |
|--------|-------------|----------|
| Inline banner | Progress bar at top of library page with track count | ✓ |
| Status page section | Dedicated sync status section | |
| Toast + background | Small toast, sync runs silently | |

**User's choice:** Inline banner

| Option | Description | Selected |
|--------|-------------|----------|
| HTMX polling | Page polls every 2-3 seconds. Matches existing patterns. | ✓ |
| You decide | Claude picks best approach | |

**User's choice:** HTMX polling

---

## Library Browse View

| Option | Description | Selected |
|--------|-------------|----------|
| Basic browse page | Table with search/filter. Confirms sync worked. | |
| Stats only | Track count, last sync time, Sync Now button. Minimal. | |
| Full browse | Table with columns, pagination, search, sort. | ✓ |

**User's choice:** Full browse

| Option | Description | Selected |
|--------|-------------|----------|
| Home page IS library | After Plex configured, '/' shows library | |
| Separate /library | Add 'Library' link to nav bar | ✓ |

**User's choice:** Separate /library

---

## Bug Report

User reported: Plex library selection bug — selects "Music" library but a different library is shown in settings config. Captured as D-12 for fix in Phase 2.

## Claude's Discretion

- Pagination size, search debounce, Track model fields/indexes
- Background job implementation, delta sync strategy

## Deferred Ideas

None
