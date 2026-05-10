---
status: partial
phase: 06-vibe-clustering-setup-wizard
source: [06-VERIFICATION.md]
started: 2026-05-10T00:00:00Z
updated: 2026-05-10T00:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Full wizard end-to-end produces 3-7 Plex playlists
expected: After completing the wizard with ≥50 rated tracks, Plex Web shows 3–7 playlists titled "Composer · {name}", each populated with rated tracks whose audio features match the vibe's centroid.
result: [pending]

### 2. Rating-to-Plex auto-slot latency
expected: With ≥1 vibe live, rating a previously-unrated track 4 stars in Plexamp causes the track to appear in the matching "Composer · {name}" Plex playlist within ~10 seconds.
result: [pending]

### 3. Vibe rename persists across container restart
expected: Editing a vibe name ("Late Night Drives" → "Night Drives") and committing renames the Plex playlist to "Composer · Night Drives" in place; the rename survives a docker container restart.
result: [pending]

### 4. Manual override preservation across re-cluster
expected: A `TrackVibe(assigned_by='manual')` row is either replayed against a successor vibe with the same name, or surfaced as a "manual_override_lost" entry in the /debug/vibes Last 20 decisions table — never silently dropped.
result: [pending]

### 5. Mobile-first visual rendering at 375px
expected: /setup, /setup/webhook, /setup/propose, /setup/confirm, /setup/done render cleanly at 375px viewport (iPhone SE) — no horizontal scroll, sticky-bottom CTA stays above iOS toolbar, 44px tap targets.
result: [pending]

## Summary

total: 5
passed: 0
issues: 0
pending: 5
skipped: 0
blocked: 0

## Gaps
