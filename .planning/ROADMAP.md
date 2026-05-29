# Roadmap: Composer

## Milestones

- ✅ **v1.0 Mood-to-Playlist** — Phases 1–4 (shipped 2026-04-10)
- ✅ **v2.0 Music Companion** — Phases 5–8 + 6.1 / 6.2 / 7.1 (shipped 2026-05-29)
- 📋 **v2.1 (TBD)** — start with `/gsd-new-milestone`

Full phase detail for each shipped milestone is archived under `.planning/milestones/`.

## Shipped Phases

<details>
<summary>✅ v1.0 Mood-to-Playlist (Phases 1–4) — shipped 2026-04-10</summary>

- [x] **Phase 1: Foundation, Configuration & Deployment** — Docker container, CI/CD to Docker Hub, settings page, security patterns (2026-04-09)
- [x] **Phase 2: Library Sync** — Full Plex music library synced to local SQLite with delta updates (2026-04-09)
- [x] **Phase 3: Audio Feature Extraction** — Essentia analyzes local audio files for energy, tempo, danceability, valence (2026-04-09)
- [x] **Phase 4: Playlist Generation** — Mood-to-playlist pipeline (2026-04-10) *(retired in v2.0; capabilities absorbed)*

Archive: [`.planning/milestones/v2.0-ROADMAP.md`](milestones/v2.0-ROADMAP.md) (v1.0 was carried forward inline into the v2.0 archive)

</details>

<details>
<summary>✅ v2.0 Music Companion (Phases 5, 6, 6.1, 6.2, 7, 7.1, 8) — shipped 2026-05-29</summary>

- [x] **Phase 5: Plex Event Foundation + Rating Sync** — webhook + polling event ingest, dedupe, RatingChanged propagation (2026-05-10)
- [x] **Phase 6: Vibe Clustering + Setup Wizard** — first-run wizard ends with 3–7 named vibe playlists in Plex, auto-slotting on rating (2026-05-13)
- [x] **Phase 6.1: Vibe Wizard Foundations** (INSERTED) — server-led k-means membership + user-typed vibe names (2026-05-13)
- [x] **Phase 6.2: LLM-Direct Vibe Assignment** (INSERTED) — replaced k-means membership with LLM-direct two-pass assignment (2026-05-12)
- [x] **Phase 7: Suggestions Queue + v1 Chat Retirement** — continuous Composer · Suggestions queue, vibes home as landing page, v1 chat retired (2026-05-14)
- [x] **Phase 7.1: Suggestions Cost Architecture** (INSERTED) — SQL refill + weekly LLM discovery; steady-state cost from ~$150/mo → ~$0.20/mo (2026-05-16)
- [x] **Phase 8: Lidarr Discovery + Polish** — taste-aware artist discovery, one-click add to Lidarr, auto-ingest, mobile-first polish (2026-05-17)
- ❌ **Phase 9: Feed the Engine** — **cut** (covered by Suggestions queue + weekly discovery + Lidarr discovery)

Archives: [`v2.0-ROADMAP.md`](milestones/v2.0-ROADMAP.md), [`v2.0-REQUIREMENTS.md`](milestones/v2.0-REQUIREMENTS.md)

</details>

## Next Milestone

v2.0 is shipped and feature-complete. Run `/gsd-new-milestone` to start the next cycle.

Carry-forward candidates noted during v2.0 close:

- **ENG-02 (played-but-not-rated nudge)** — surface tracks with `viewCount ≥ 5 AND user_rating = 0` on the vibes home; tap → Plexamp deeplink. Not covered by the existing Suggestions / weekly discovery loop. Small scope (~1–2 hrs).
