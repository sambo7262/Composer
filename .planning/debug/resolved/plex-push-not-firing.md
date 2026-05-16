---
slug: plex-push-not-firing
status: resolved
trigger: |
  Phase 7.1 NAS UAT: TrackPlay webhooks rotate the Composer SuggestionsMirror
  correctly (drain + SQL refill → new picks; Composer UI reflects the change),
  but the Plex Suggestions playlist never receives the additive Plex push that
  refill_mirror_sql is supposed to fire via update_playlist_items. The
  managedplaylist row's last_pushed_at is stuck at the Phase 7 bootstrap
  timestamp (2026-05-14T17:43:03+00:00) — no successful push since the 7.1
  cost-architecture refactor went live.
created: 2026-05-16T16:45:14Z
updated: 2026-05-16T16:45:14Z
phase: 07.1-suggestions-cost-architecture-sql-refill-weekly-discovery
deploy_head: d158c74
---

# Plex Push Not Firing (Phase 7.1 NAS UAT)

## Symptoms

**Expected behavior:** On every TrackPlay webhook for a track currently in
`SuggestionsMirror`, the flow is:
1. `handle_track_played` (`app/services/event_handlers.py:230`) fires
2. `drain_track_from_mirror(rating_key)` removes the played track from
   `SuggestionsMirror` (count: 30 → 29)
3. `maybe_schedule_refill()` runs; deficit = 1, calls `refill_mirror_sql`
4. `refill_mirror_sql` picks a new track via SQL (TrackVibe.distance ASC),
   writes to `SuggestionsMirror` (count: 29 → 30), and then enters the
   `if picks:` branch (`suggestions_service.py:832`)
5. `update_playlist_items(plex_url, plex_token, '77830', rating_keys)`
   (`plex_playlist_service.py:205`) computes delta = desired - current,
   calls `playlist.addItems(delta)`, post-push verifies, then unconditionally
   updates `managedplaylist.last_pushed_at` + `track_count` via
   `_update_managed_playlist_sync` (`plex_playlist_service.py:262-266`)

**Actual behavior:**
- Steps 1–4 work: Composer UI shows the mirror rotating correctly on each
  play (played track gone, new track added)
- Step 5 does NOT complete: Plex Suggestions playlist (rating_key=77830,
  managedplaylist row matches) shows ZERO new tracks since the 7.1 deploy.
  `last_pushed_at` is stuck at `2026-05-14T17:43:03+00:00` — the Phase 7
  bootstrap-era timestamp, ~38 hours before the symptom was recorded.
- `track_count` stuck at 30.

**Error messages:** None observable. A targeted log grep on
`trackplayed|refill|drain|playlist|push|error|warning|except` over a
recent play window returned EMPTY — neither success-path logs nor the
swallowed-exception log at `suggestions_service.py:854-857` are firing
for those keywords.

**Timeline:**
- Phase 7 bootstrap: 2026-05-14T15:31:01Z (`managedplaylist.kind=suggestions`
  row created, plex_rating_key=77830 populated)
- Phase 7 last-known-working push: 2026-05-14T17:43:03Z (last_pushed_at
  on the managedplaylist row)
- Phase 7.1 main plans (01–03) merged + deployed: 2026-05-15
- Phase 7.1 gap-closure plans (04–05, GAP-01 + GAP-02) merged + deployed:
  2026-05-16
- Symptom confirmed: 2026-05-16T07:30Z (this debug session) — 3 plays since
  7.1 deploy, mirror rotated each time, Plex playlist unchanged.
- Last `last_pushed_at` update: never since 7.1 → strong signal that the
  push code path has NOT successfully completed once since the refactor.

**Reproduction:**
1. NAS running `origin/main` HEAD d158c74 (Phase 7.1 + GAP-01/02 closures)
2. Open the "Composer · Suggestions" playlist in Plexamp (rating_key=77830)
3. Play one track from it
4. Verify in Composer UI: mirror rotates (the played track is gone, a new
   track loaded) — confirms drain + refill ran
5. Re-query the managedplaylist row: `last_pushed_at` unchanged, still
   `2026-05-14T17:43:03+00:00`
6. Check Plex: no new track added; played track still present

## Confirmed State (verified)

- `managedplaylist`: kind=suggestions, plex_rating_key='77830',
  composer_name='Composer · Suggestions', last_pushed_at='2026-05-14T17:43:03',
  track_count=30
- Plex side: playlist at /playlists/77830 exists and is visible
- Code reads: `_update_managed_playlist_sync` is called UNCONDITIONALLY at
  `plex_playlist_service.py:262` inside the `try:` block of
  `update_playlist_items` — there is no early return path that skips it
- Code reads: `refill_mirror_sql`'s push branch at `suggestions_service.py:832`
  wraps `update_playlist_items` in `try/except Exception → logger.exception`
  at lines 849-857 — any exception would print with full traceback
- The fact that `last_pushed_at` has not moved means EITHER:
  (a) the push branch is not being entered (picks list empty? deficit zero?
      `_find_suggestions_managed_playlist_sync` returning None silently?)
  (b) `update_playlist_items` is raising AND the exception is being swallowed
      somewhere we cannot see (uvicorn log filter? container stderr redirect?)
  (c) something between entering the try and reaching line 262 is silently
      returning without raising

## Current Focus

```
hypothesis: TBD — gsd-debugger to investigate
test: TBD
expecting: TBD
next_action: |
  Begin scientific-method investigation. First evidence-gathering step:
  ask the user to run a one-shot Python diagnostic inside the Composer
  container that exercises the exact code path manually (call
  refill_mirror_sql once, capture stack/return value, re-query
  managedplaylist row). This will bisect between branch-not-entered vs.
  exception-being-swallowed without needing more web UI plays.
reasoning_checkpoint: null
tdd_checkpoint: null
```

## Evidence

- timestamp: 2026-05-16T16:45:14Z
  type: db_query_result
  source: NAS sqlite3 via `docker exec composer python -c "..."`
  query: SELECT kind, plex_rating_key, composer_name, last_pushed_at, track_count FROM managedplaylist WHERE kind='suggestions'
  result: |
    [('suggestions', '77830', 'Composer · Suggestions',
      '2026-05-14T17:43:03.653498+00:00', 30)]
  inference: |
    plex_rating_key matches the Plex-side rating key (77830 — verified via
    Plex Web URL `/playlist?key=%2Fplaylists%2F77830`). last_pushed_at is
    pinned at Phase 7 bootstrap time — no push since 7.1 deploy. Rules out
    stale-rating-key cause; rules out Pitfall 6 silent-drop (push isn't
    even being attempted, otherwise `_update_managed_playlist_sync` at
    `plex_playlist_service.py:262` would have advanced last_pushed_at).

- timestamp: 2026-05-16T16:45:14Z
  type: log_grep
  source: NAS `docker logs -f --tail 5 composer 2>&1 | grep -iE "trackplayed|refill|drain|playlist|push|error|warning|except"`
  result: empty (no matches over a recent live play window)
  inference: |
    Either the keywords are wrong (grep too narrow for success-path INFO
    messages), or the entire drain → refill → push chain is firing silently,
    or the chain is not firing at all on this particular code path.

## Eliminated

(none yet)

## Resolution

**Root cause:** `update_playlist_items` in `app/services/plex_playlist_service.py:205`
passes `playlist_rating_key` (a TEXT-typed string from `ManagedPlaylist.plex_rating_key`,
e.g. `'77830'`) directly into PlexAPI's `plex.fetchItem(ekey)` without converting to
`int` or prefixing with `/library/metadata/`. PlexAPI 4.18.1's `fetchItem` does
naive URL concatenation when ekey is a bare string without leading `/`, producing
broken URLs like `http://192.168.86.37:32400` + `77830` = `http://192.168.86.37:3240077830`,
which `requests` rejects with `InvalidURL: Failed to parse: ...`.

The exception was being caught at `suggestions_service.py:853` and routed through
`logger.exception(...)`, but the message did not contain any of the keywords used
by the initial NAS log grep (`trackplayed|refill|drain|playlist|push|error|warning|except`),
masking the failure during manual UAT. The `[PLEX-PUSH-DEBUG]` instrumentation
surfaced it cleanly via the typed exception name + sanitized message.

**Confirmed via** [PLEX-PUSH-DEBUG] log trail captured 2026-05-16T10:05:55Z on NAS
(commit b36ce23):
1. handle_track_played fired: rating_key=21518
2. drain_track_from_mirror returned removed=True
3. refill_mirror_sql entered: current=29 target=30 deficit=1
4. weights_count=6
5. picks_built=1
6. push branch entered: rating_keys=['21536']
7. mp_lookup: mp_is_none=False plex_rating_key='77830' kind='suggestions'
8. branch=update calling update_playlist_items rating_key=77830 n_keys=1
9. plex_creds_read plex_url_set=True plex_token_set=True
10. **update_playlist_items RAISED: InvalidURL: Failed to parse: http://192.168.86.37:3240077830**

Every step BEFORE 10 worked as designed. The break is exactly at the PlexAPI call.

**Fix direction:**
- In `app/services/plex_playlist_service.py`, convert `playlist_rating_key` and
  each item rating key to `int` before passing to `plex.fetchItem(...)`. Two sites
  affected: `_get_current_keys` (line ~234) and `_add_items` (line ~239 + ~241).
- Add a unit test that mocks `PlexServer.fetchItem` and asserts the call arg is
  type `int`, not `str`. Existing mocks accept any arg, so they didn't catch this.
- The bug was introduced by Phase 7.1 D-D1's mirroring of the Plex push branch from
  the deleted Phase 7 LLM-ranking refill. The deleted Phase 7 code presumably
  passed ints (or PlexAPI's older path was tolerant). Worth a brief git-blame
  archaeology to confirm and document.

**Eliminated hypotheses (5 of 5):**
1. Webhook not arriving — REJECTED. `POST /api/webhooks/plex 200 OK` + `handle_track_played fired` log confirmed.
2. Drain not firing — REJECTED. `drain_track_from_mirror returned removed=True` confirms drain executed against a mirror member.
3. `refill_mirror_sql` exiting early on deficit≤0 — REJECTED. `deficit=1` log confirms entry.
4. `picks` list empty / weights empty / pool empty — REJECTED. `weights_count=6`, `picks_built=1` confirm normal candidate generation.
5. ManagedPlaylist row mismatch — REJECTED. `mp_is_none=False plex_rating_key='77830' kind='suggestions'` matches Plex's actual playlist ID.

**Fix applied:** commit f483b1f — `fix(07.1): cast rating_key to int for PlexAPI fetchItem (Plex push GAP-03)`. Three call sites in `app/services/plex_playlist_service.py::update_playlist_items` (`_get_current_keys` line 234, `_add_items` lines 239 + 241). Regression test added in `tests/test_plex_playlist_service.py::test_update_playlist_items_passes_int_to_fetchitem` using a `StrictFakePlexServer` that rejects bare-string ekeys.

**Verification:** Live NAS UAT 2026-05-16T10:41:24Z confirmed `[PLEX-PUSH-DEBUG] update_playlist_items SUCCESS: added=1 unchanged=0 silently_dropped=0 retried=0`. User observed Plex playlist grow from 30 → 31 tracks after a single play. `managedplaylist.last_pushed_at` advanced. The full success-path log trail (all 9 [PLEX-PUSH-DEBUG] lines) appeared in order, exactly as expected.

**Debug instrumentation reverted:** commit ca5a3f2 reverted the [PLEX-PUSH-DEBUG] logs introduced in b36ce23 — production runs clean again.

**Files changed (final):**
- app/services/plex_playlist_service.py (3 lines: int casts at fetchItem call sites)
- tests/test_plex_playlist_service.py (+1 strict-fake regression test, 2 existing tests updated to numeric rating keys)

**Lessons learned:**
1. **Production-only failure mode:** PlexAPI's URL concat behavior was not exercised by any unit test because the existing `FakePlexServer.fetchItem` did `key = str(key)` on entry, accepting any input shape. The new `StrictFakePlexServer` mimics real behavior and would have caught this. Generalizable: when wrapping external libraries with fakes, the fake should be at least as strict as the real thing on input-type contracts.
2. **Log-grep filter blindspot:** the initial NAS log grep on `trackplayed|refill|drain|playlist|push|error|warning|except` missed the failure because the swallowed exception's logger message was `"Plex update_playlist_items failed"` — the word `failed` wasn't in the filter, and `logger.exception(...)`'s ERROR-level prefix didn't match either (the production logs prefix with `ERROR:` not `[ERROR]`, no match for `error` regex unless case-insensitive — which it was, but stderr lines didn't match the docker-log streaming filter). Lesson: when filter returns empty, widen the filter or remove it entirely before concluding "nothing is happening".
3. **Debug-instrumentation-in-prod pattern:** committing temporary `[PLEX-PUSH-DEBUG]` logs to main, deploying via the normal pipeline, observing live, then reverting — was a much faster bisect than trying to reproduce the bug locally. Took two NAS deploys (one to instrument, one to fix) and ~30 minutes of clock time. Worth keeping in the toolbox for similar production-only bugs.
