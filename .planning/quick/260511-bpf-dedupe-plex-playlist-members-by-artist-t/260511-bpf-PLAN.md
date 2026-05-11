---
phase: quick-260511-bpf
plan: 01
type: tdd
wave: 1
depends_on: []
files_modified:
  - tests/test_vibe_service.py
  - app/services/vibe_service.py
autonomous: true
requirements:
  - BUGFIX-260511-BPF-01
must_haves:
  truths:
    - "When the user has rated the same song on two albums (Best Of + original), the vibe playlist push contains only ONE ratingKey for that song."
    - "When the user has rated two DIFFERENT songs by the same artist, both ratingKeys are still returned (no false dedupe)."
    - "Artist + title comparison is case-insensitive and whitespace-tolerant ('Air ' == 'air')."
    - "The canonical-pick heuristic is the lexicographically smallest plex_rating_key per (artist, title) group — deterministic across runs."
    - "TrackVibe rows for both album versions still exist after the fix (only the playlist-push read filters them; slot-in logic is untouched)."
  artifacts:
    - path: "app/services/vibe_service.py"
      provides: "_canonical_rating_keys_sync helper + dedupe call in _read_vibe_member_rating_keys_sync"
      contains: "def _canonical_rating_keys_sync"
    - path: "tests/test_vibe_service.py"
      provides: "4 regression tests covering canonical-key dedupe behavior"
      contains: "test_canonical_keys_dedupes_same_artist_title_different_rating_key"
  key_links:
    - from: "app/services/vibe_service.py::_read_vibe_member_rating_keys_sync"
      to: "app/services/vibe_service.py::_canonical_rating_keys_sync"
      via: "Direct call on the returned list before return"
      pattern: "_canonical_rating_keys_sync\\(rating_keys\\)"
---

<objective>
Fix the cross-album duplicate-song bug surfaced in UAT (2026-05-11): when a user rates the same song on both a "Best Of" compilation AND the original album, the song appears twice in a vibe's Plex playlist because each version has a distinct `plex_rating_key`.

Add a dedupe helper that collapses ratingKeys by `(artist.casefold().strip(), title.casefold().strip())`, picking the lexicographically smallest ratingKey per group (Plex assigns ratingKeys in import order — original album was usually imported first). Apply the helper inside `_read_vibe_member_rating_keys_sync` ONLY, leaving slot-in logic untouched so both TrackVibe rows still exist.

Purpose: Eliminate duplicate songs from vibe playlists without scope creep. Quality-aware dedupe (bitrate/format preference) is DEFERRED to Phase 6.2 per ROADMAP — that phase adds `bitrate` + `file_format` columns to Track.

Output:
- `app/services/vibe_service.py` gains `_canonical_rating_keys_sync` (placed immediately before `_read_vibe_member_rating_keys_sync`) and `_read_vibe_member_rating_keys_sync` pipes its output through it.
- `tests/test_vibe_service.py` gains 4 regression tests proving dedupe correctness, case-insensitivity, whitespace tolerance, and false-dedupe avoidance.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md
@app/services/vibe_service.py
@app/models/track.py
@tests/test_vibe_service.py
@tests/conftest.py

<interfaces>
<!-- Pre-extracted contracts so executor needs zero codebase exploration. -->

From `app/models/track.py` (Track schema — DO NOT MODIFY):
```python
class Track(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plex_rating_key: str = Field(unique=True, index=True)
    title: str = Field(index=True)
    artist: str = Field(index=True)
    album: str = Field(default="")
    # ... (bitrate/file_format do NOT exist — that's Phase 6.2)
```

From `app/services/vibe_service.py` (line 253-264, the bug site):
```python
def _read_vibe_member_rating_keys_sync(vibe_id: int) -> List[str]:
    """Return all Plex ratingKeys currently in a vibe (for additive playlist push)."""
    with Session(get_engine()) as session:
        rows = list(
            session.exec(
                select(Track.plex_rating_key)
                .join(TrackVibe, TrackVibe.track_id == Track.id)
                .where(TrackVibe.vibe_id == vibe_id)
            ).all()
        )
        return [str(r) for r in rows]
```

From `tests/test_vibe_service.py` existing fixture patterns (use these — do not invent new fixtures):
- `db_with_phase6(test_engine) -> Session` — full schema with Track, TrackVibe, Vibe tables.
- `_add_track(session, rk, ...)` — helper for inserting a Track. NOTE: the existing helper hardcodes `title=f"Title {rk}"` and `artist="Artist"`. Your new tests need DIFFERENT `(artist, title)` values per track, so either:
  (a) extend the helper with optional `title=` + `artist=` kwargs (preferred — small, additive), OR
  (b) construct `Track(...)` directly in the new tests.
  Pick (a) — it's reused by 12+ existing tests and the kwargs default to today's values for back-compat.
- `reset_vibe_service_singletons` is `autouse=True` — it touches `vibe_service._slot_in_locks` and `vibe_service._status` which both EXIST today, so no fixture changes needed.

From `tests/conftest.py`:
- `test_engine` fixture provides an in-memory-style SQLite engine wired to `app.database.get_engine()` via `tmp_data_dir`.
- Tests should use `db_with_phase6` (Session yielding fixture) not raw `test_engine`.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: RED — add 4 regression tests for canonical-key dedupe</name>
  <files>tests/test_vibe_service.py</files>
  <behavior>
    All 4 tests target the helper `_canonical_rating_keys_sync(rating_keys: List[str]) -> List[str]` which does NOT yet exist. Tests must FAIL on the current code with `ImportError` / `AttributeError`, then PASS after Task 2.

    Test 1 — `test_canonical_keys_dedupes_same_artist_title_different_rating_key`:
      Setup: insert 2 Track rows via `_add_track` (extended with `artist=` / `title=` kwargs):
        - `rk="100"`, artist="Air", title="La Femme d'argent", album="Moon Safari"
        - `rk="200"`, artist="Air", title="La Femme d'argent", album="Best Of"
      Call: `_canonical_rating_keys_sync(["100", "200"])`
      Expect: `["100"]` — the lexicographically smaller ratingKey wins. ONLY one entry.

    Test 2 — `test_canonical_keys_keeps_distinct_titles_same_artist`:
      Setup: 2 tracks, both artist="Air", DIFFERENT titles:
        - `rk="100"`, title="La Femme d'argent"
        - `rk="200"`, title="Sexy Boy"
      Call: `_canonical_rating_keys_sync(["100", "200"])`
      Expect: `sorted(["100", "200"])` — BOTH kept (no false dedupe). Use sorted() in the assertion to match the helper's deterministic sort.

    Test 3 — `test_canonical_keys_case_insensitive_matching`:
      Setup: 2 tracks with case-mismatched artist + title:
        - `rk="100"`, artist="Air", title="La Femme"
        - `rk="200"`, artist="air", title="LA FEMME"
      Call: `_canonical_rating_keys_sync(["100", "200"])`
      Expect: `["100"]` — casefold normalization treats them as the same song.

    Test 4 — `test_canonical_keys_whitespace_tolerance`:
      Setup: 2 tracks with trailing/leading whitespace:
        - `rk="100"`, artist="Air", title="La Femme"
        - `rk="200"`, artist="Air ", title=" La Femme"  (spaces around)
      Call: `_canonical_rating_keys_sync(["100", "200"])`
      Expect: `["100"]` — `.strip()` normalization treats them as the same.

    Place all 4 tests at the END of `tests/test_vibe_service.py` under a comment header:
    ```python
    # ---------------------------------------------------------------------------
    # Canonical-key dedupe regression tests (bugfix 260511-bpf)
    # Cross-album dupes: same song on Best Of + original album must collapse
    # to a single ratingKey in the playlist-push read path.
    # ---------------------------------------------------------------------------
    ```

    All 4 tests synchronous (no `@pytest.mark.asyncio`) — the helper is sync. Use `db_with_phase6` fixture to get the Session + ensure the Track table exists. Import the helper inside each test:
    ```python
    from app.services.vibe_service import _canonical_rating_keys_sync
    ```

    Also extend `_add_track` helper (top of file, ~line 98) to accept `artist=` and `title=` kwargs with their current hardcoded values as defaults — DO NOT change the existing call signature for any of the 12 existing call sites.
  </behavior>
  <action>
    1. Open `tests/test_vibe_service.py`.
    2. Extend the `_add_track` helper to accept `artist: str = "Artist"` and `title: Optional[str] = None` kwargs. When `title is None`, fall back to `f"Title {rk}"` (existing behavior). Wire `Track(plex_rating_key=rk, title=..., artist=artist, ...)`. Verify the existing call sites (all positional or single-kwarg) still work unchanged.
    3. Append the 4 new tests at the end of the file using the patterns described in `<behavior>`. Each test:
       - Uses `db_with_phase6` fixture.
       - Calls the helper via `from app.services.vibe_service import _canonical_rating_keys_sync`.
       - Asserts deterministic output (use `==` against expected list).
    4. Run the new tests — they MUST fail with `ImportError: cannot import name '_canonical_rating_keys_sync'`. If they pass or fail for a different reason, the tests are wrong.
    5. Commit: `test(260511-bpf): add failing canonical-key dedupe tests`
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest tests/test_vibe_service.py -k "canonical_keys" -x 2>&amp;1 | tail -20</automated>
    Expected: 4 tests collected, all 4 FAIL with `ImportError` or `AttributeError` on `_canonical_rating_keys_sync`. This is the RED state.

    Also verify no existing tests broke from the `_add_track` signature extension:
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest tests/test_vibe_service.py -x --ignore-glob="*canonical*" 2>&amp;1 | tail -5</automated>
    Expected: existing 12+ tests still pass (they use the default kwarg values).
  </verify>
  <done>
    - `tests/test_vibe_service.py` has 4 new `test_canonical_keys_*` tests appended at the end.
    - `_add_track` helper accepts optional `artist=` and `title=` kwargs with back-compat defaults.
    - All 4 new tests FAIL with ImportError (helper doesn't exist yet).
    - All pre-existing tests in `test_vibe_service.py` still PASS.
    - Commit created with message `test(260511-bpf): add failing canonical-key dedupe tests`.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: GREEN — implement _canonical_rating_keys_sync and wire it into the playlist-push read</name>
  <files>app/services/vibe_service.py</files>
  <behavior>
    After this task: the 4 RED tests from Task 1 pass; the existing 121-test phase 6 / 6.1 suite still passes; the 36 vibe_clusterer tests and 8 anthropic_client tests still pass.

    Add `_canonical_rating_keys_sync(rating_keys: List[str]) -> List[str]` immediately before `_read_vibe_member_rating_keys_sync` (currently line 253). The helper must:
    - Return `[]` early if `rating_keys` is empty.
    - Open `Session(get_engine())` ONCE (do not re-open per row).
    - Query `select(Track.plex_rating_key, Track.artist, Track.title).where(Track.plex_rating_key.in_(rating_keys))`.
    - Group rows by `(artist.casefold().strip(), title.casefold().strip())`.
    - Per group, keep the ratingKey with `min(...)` lexicographic on the string.
    - Return `sorted(canonical.values())` — deterministic ordering for tests and idempotent playlist-push.

    Then modify `_read_vibe_member_rating_keys_sync` (line 253-264) to pass its `[str(r) for r in rows]` result through `_canonical_rating_keys_sync` before returning.

    Slot-in logic (`slot_track`, `reslot_all_rated_tracks`) and `_read_all_rated_track_keys_sync` MUST remain untouched — TrackVibe rows for both album versions still exist; only the playlist-push read filters them.
  </behavior>
  <action>
    1. Open `app/services/vibe_service.py`.
    2. Insert `_canonical_rating_keys_sync` immediately before line 253 (`_read_vibe_member_rating_keys_sync`). Implementation:
       ```python
       def _canonical_rating_keys_sync(rating_keys: List[str]) -> List[str]:
           """Dedupe ratingKeys by (artist.casefold().strip(), title.casefold().strip()).

           When the user has rated the same song on multiple albums (Best Of +
           original), return only the smallest plex_rating_key per (artist, title)
           group. The smallest ratingKey is the deterministic pick because Plex
           assigns ratingKeys in import order — the original album was usually
           imported first.

           Quality-aware dedupe (bitrate/format preference) is deferred to Phase 6.2
           per ROADMAP — that phase adds bitrate + file_format columns to Track.
           """
           if not rating_keys:
               return []
           with Session(get_engine()) as session:
               rows = list(
                   session.exec(
                       select(Track.plex_rating_key, Track.artist, Track.title)
                       .where(Track.plex_rating_key.in_(rating_keys))
                   ).all()
               )
           canonical: dict[tuple[str, str], str] = {}
           for rating_key, artist, title in rows:
               key = (artist.casefold().strip(), title.casefold().strip())
               existing = canonical.get(key)
               if existing is None or rating_key < existing:
                   canonical[key] = rating_key
           return sorted(canonical.values())
       ```
    3. Update `_read_vibe_member_rating_keys_sync` (lines 253-264 in the current file, will be shifted down by the insertion above):
       ```python
       def _read_vibe_member_rating_keys_sync(vibe_id: int) -> List[str]:
           """Return all Plex ratingKeys currently in a vibe (for additive playlist push).

           Dedupes by (artist, title) canonical key — cross-album dupes (same song on
           Best Of + original album) collapse to a single ratingKey to prevent the
           same song appearing twice in a vibe playlist. See _canonical_rating_keys_sync.
           """
           with Session(get_engine()) as session:
               rows = list(
                   session.exec(
                       select(Track.plex_rating_key)
                       .join(TrackVibe, TrackVibe.track_id == Track.id)
                       .where(TrackVibe.vibe_id == vibe_id)
                   ).all()
               )
           rating_keys = [str(r) for r in rows]
           return _canonical_rating_keys_sync(rating_keys)
       ```
       Note the `Session(...)` block now closes BEFORE the `_canonical_rating_keys_sync` call — that's intentional, because the helper opens its own Session. Avoids nested session weirdness.
    4. Run the canonical_keys tests — they must now PASS (GREEN state).
    5. Run the full test suite to confirm no regressions.
    6. Commit: `fix(260511-bpf): dedupe vibe playlist members by (artist, title) canonical key`
  </action>
  <verify>
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest tests/test_vibe_service.py -k "canonical_keys" -x 2>&amp;1 | tail -10</automated>
    Expected: 4 passed.

    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest tests/test_vibe_service.py -x 2>&amp;1 | tail -5</automated>
    Expected: all tests in test_vibe_service.py pass (12+ existing + 4 new).

    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest tests/test_vibe_clusterer.py tests/test_anthropic_client.py -x 2>&amp;1 | tail -5</automated>
    Expected: 36 + 8 = 44 tests pass.

    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; pytest -x 2>&amp;1 | tail -10</automated>
    Expected: full suite passes — no regressions in the 121-test phase 6 / 6.1 baseline.

    Static check — ensure scope discipline (`_read_all_rated_track_keys_sync` and slot-in functions are NOT touched):
    <automated>cd /Users/Oreo/Projects/Composer &amp;&amp; git diff app/services/vibe_service.py | grep -E "^-" | grep -vE "^---" | grep -E "(_read_all_rated_track_keys_sync|def slot_track|def unslot_track|def reslot_all|def maybe_reslot)" | wc -l</automated>
    Expected: `0` — no lines deleted from those functions. (Helper insertion + the single `_read_vibe_member_rating_keys_sync` edit are the only changes.)
  </verify>
  <done>
    - `app/services/vibe_service.py::_canonical_rating_keys_sync` exists immediately before `_read_vibe_member_rating_keys_sync`.
    - `_read_vibe_member_rating_keys_sync` pipes its rating-key list through the new helper.
    - 4 new tests pass (GREEN).
    - All pre-existing tests in `test_vibe_service.py` pass.
    - `tests/test_vibe_clusterer.py` (36 tests) and `tests/test_anthropic_client.py` (8 tests) pass.
    - Full repo test suite passes — no regressions in the 121-test baseline.
    - `slot_track`, `unslot_track`, `reslot_all_rated_tracks`, `maybe_reslot_pending_track`, `_read_all_rated_track_keys_sync` are byte-identical to pre-fix (only the playlist-push read path changed).
    - Track schema unchanged — no migration, no new columns.
    - Commit created with message `fix(260511-bpf): dedupe vibe playlist members by (artist, title) canonical key`.
  </done>
</task>

</tasks>

<verification>
End-to-end behavior verification (sanity check after both tasks complete):

1. Pre-fix state reproduces the bug: with two Track rows sharing `(artist, title)` and both in a TrackVibe row for the same vibe, the OLD `_read_vibe_member_rating_keys_sync` returned BOTH ratingKeys → `update_playlist_items` adds both to Plex → duplicate song in playlist.

2. Post-fix state: same setup → `_read_vibe_member_rating_keys_sync` returns ONE ratingKey (the smaller) → playlist contains the song once.

3. TrackVibe rows untouched: query `select(TrackVibe).where(TrackVibe.vibe_id == X)` still returns 2 rows after the fix — the slot-in logic placed both there and the fix preserves that.

4. The slot_in pipeline (`slot_track` → TrackVibe insert → playlist push) is unchanged structurally; the dedupe only happens at the playlist-push read boundary.
</verification>

<success_criteria>
- [ ] 4 new tests in `tests/test_vibe_service.py` covering: dedupe positive case, distinct-title negative case, case-insensitive normalization, whitespace normalization.
- [ ] `_canonical_rating_keys_sync` helper exists in `app/services/vibe_service.py`, placed immediately before `_read_vibe_member_rating_keys_sync`.
- [ ] `_read_vibe_member_rating_keys_sync` calls the new helper.
- [ ] All 4 new tests pass; all 12+ existing `test_vibe_service.py` tests pass.
- [ ] Full repo test suite passes (121 phase 6/6.1 + 36 vibe_clusterer + 8 anthropic_client + everything else).
- [ ] `app/models/track.py` unchanged — no schema migration.
- [ ] `slot_track`, `unslot_track`, `reslot_all_rated_tracks`, `maybe_reslot_pending_track`, `_read_all_rated_track_keys_sync` are unmodified.
- [ ] Two atomic commits exist: RED test commit, GREEN fix commit.
- [ ] SUMMARY notes that quality-aware dedupe (bitrate/format) is explicitly DEFERRED to Phase 6.2.
</success_criteria>

<output>
After completion, create `.planning/quick/260511-bpf-dedupe-plex-playlist-members-by-artist-t/260511-bpf-SUMMARY.md` covering:
- The bug (cross-album duplicates from user rating same song on multiple albums)
- The fix (canonical-key dedupe at playlist-push read boundary)
- Heuristic choice rationale (`min(plex_rating_key)` — Plex import order proxy)
- **Explicit deferral note**: quality-aware dedupe (bitrate/format preference) is DEFERRED to Phase 6.2 per ROADMAP; that phase adds `bitrate` + `file_format` columns to Track and can then upgrade the helper to pick by quality instead of import order.
- Files changed: `app/services/vibe_service.py` (helper added, one call-site edit), `tests/test_vibe_service.py` (4 new tests, `_add_track` helper extended).
- Test counts before/after.
</output>
