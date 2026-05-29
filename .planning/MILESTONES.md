# Milestones

## v2.0 Music Companion — Shipped 2026-05-29

**Phases:** 7 (5, 6, 6.1, 6.2, 7, 7.1, 8) — 25 plans, ~49 tasks
**Timeline:** 2026-04-09 → 2026-05-29 (50 days)

### Delivered

Composer pivoted from a one-shot vibe-to-playlist generator into a continuous music companion. Your Plex star ratings are now the taste signal: rated tracks auto-organize into 3–7 named vibe playlists, and a continuously-refilling Suggestions queue + weekly LLM-driven discovery surfaces unrated tracks you'd probably like next. Lidarr-aware artist discovery brings net-new music into the library; mobile-first UI throughout.

### Key Accomplishments

1. **Plex Event Foundation (Phase 5)** — webhook + polling event ingest with `EventLog UNIQUE(dedupe_key)` race-free dedup; `RatingChanged` propagation end-to-end; AST-enforced `asyncio.to_thread` boundary for sync PlexAPI calls.
2. **Vibe Clustering + Setup Wizard (Phases 6 / 6.1 / 6.2)** — first-run wizard ends with 3–7 named `Composer · {name}` Plex playlists; rated tracks auto-slot within ~10s of rating in Plexamp; LLM-direct two-pass assignment replaced unreliable k-means membership; user-triggered re-cluster path + `/debug/vibes` diagnostic surface.
3. **Suggestions Queue + v1 Chat Retirement (Phase 7)** — continuous `Composer · Suggestions` Plex playlist drains and refills with taste-matched picks; SuggestionsMirror as source of truth; mobile-first navigational shell (bottom tab bar, `h-dvh`, safe-area-inset, 44px tap targets); v1 mood-chat retired.
4. **Cost Architecture Pivot (Phase 7.1)** — replaced Phase 7's per-play LLM ranking (~$0.05/play, $150/mo) with SQL refill against pre-computed `TrackVibe.distance` ($0 in hot path) + once-weekly LLM discovery cron (~$0.20/mo). 99% steady-state cost reduction. Cost meter relabeled to weekly framing.
5. **Lidarr Discovery + Polish (Phase 8)** — `/discover` page with vibe-grouped artist recommendations from ListenBrainz + MusicBrainz + LLM re-rank; one-click Add-to-Lidarr with stale-warning chip; auto-ingest of Lidarr arrivals through analyze → vibe-slot pipeline; vibe colors propagated across surfaces; home-page cost chip; `/debug/discovery` 5-section diagnostic surface; OPS-06 legacy-playlist guard.
6. **Mobile-first UI + Operational Surface** — `/library`, `/discover`, `/suggestions`, vibes home all portrait-first at 375px; all DB timestamps render in PT/PDT via `local_time` Jinja filter; SQLite `busy_timeout=5000` PRAGMA eliminates concurrent-write lock errors.

### Notes

- Phase 9 (Feed the Engine — bulk rating, play-rated nudge, Surprise Me) was **cut**. Suggestions queue + weekly discovery + Lidarr discovery already cover the "drive new music to be rated" loop. ENG-02 (played-but-not-rated nudge) is the only genuinely additive item and is captured for future v2.x consideration.
- 13 quick tasks shipped during v2.0 milestone (audit metadata flagged `unknown` due to missing frontmatter; all have SUMMARY.md on disk and are in production).
- v1.0 phases (1–4) shipped 2026-04-09 and remain in production; mood-chat UI surface retired in Phase 7 but data layer preserved.

### Archives

- `.planning/milestones/v2.0-ROADMAP.md`
- `.planning/milestones/v2.0-REQUIREMENTS.md`

---
