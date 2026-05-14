---
quick_id: 260514-cdl
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/event_handlers.py
  - tests/test_event_handlers.py
autonomous: true
requirements: [HOTFIX-260514-CDL]
must_haves:
  truths:
    - "On a fresh deploy with empty SuggestionsMirror, the first webhook-delivered TrackPlayedEvent triggers a refill that materializes the Composer · Suggestions Plex playlist"
    - "In steady-state (mirror at target=30) a played non-member track does NOT cause refill_suggestions_queue to run (deficit gate inside maybe_schedule_refill short-circuits)"
    - "Phase 5 RATE-04 view_count++ + last_viewed_at update remains intact on every TrackPlayed webhook"
    - "All three tests in TestHandleTrackPlayedSuggestionsDrain pass"
    - "The new regression test fails on current code (gate present) and passes after the fix"
  artifacts:
    - path: "app/services/event_handlers.py"
      provides: "handle_track_played without the if-removed gate"
      contains: "await suggestions_service.maybe_schedule_refill()"
    - path: "tests/test_event_handlers.py"
      provides: "Updated TestHandleTrackPlayedSuggestionsDrain with new bootstrap regression test"
      contains: "test_empty_mirror_track_not_in_mirror_still_schedules_refill"
  key_links:
    - from: "app/services/event_handlers.py::handle_track_played"
      to: "app/services/suggestions_service.py::maybe_schedule_refill"
      via: "unconditional await (no removed gate)"
      pattern: "await suggestions_service\\.maybe_schedule_refill"
    - from: "app/services/suggestions_service.py::maybe_schedule_refill"
      to: "app/services/suggestions_service.py::refill_suggestions_queue"
      via: "deficit > 0 guard (suggestions_service.py:364)"
      pattern: "if deficit > 0"
---

<objective>
Fix Phase 7 suggestions bootstrap deadlock. On a fresh deploy the SuggestionsMirror
is empty, so `drain_track_from_mirror` returns False, so the existing `if removed:`
gate in `handle_track_played` never schedules a refill — the Composer · Suggestions
Plex playlist is therefore never materialized (it is only created inside
`refill_suggestions_queue` when `mp.plex_rating_key == ''`). The fix is to drop the
gate; the deficit check inside `maybe_schedule_refill` (suggestions_service.py:362-364)
preserves the steady-state "no churn" intent at the correct layer.

NAS UAT 2026-05-14 confirmed: 4 webhooks arrived (200 OK), `today_calls` stayed at 0,
`/debug/suggestions` empty across all sections. The bootstrap-via-play path is
deadlocked: a refill is needed to bootstrap the mirror, but the only refill trigger
requires a track to already be in the bootstrap.

Purpose: unblock NAS deployments — first webhook after deploy must materialize the
suggestions playlist.
Output: one-line code change in event_handlers.py + one new regression test + one
docstring/comment update on the existing test.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@app/services/event_handlers.py
@app/services/suggestions_service.py
@tests/test_event_handlers.py

<interfaces>
<!-- Key signatures the executor needs. Already verified via planner Read. -->

From app/services/event_handlers.py (current — to be modified at lines 252-264):
```python
async def handle_track_played(event: TrackPlayedEvent) -> None:
    # ... view_count update via asyncio.to_thread ...
    try:
        from app.services import suggestions_service
        removed = await suggestions_service.drain_track_from_mirror(
            event.plex_rating_key
        )
        if removed:
            await suggestions_service.maybe_schedule_refill()
    except Exception:
        logger.exception(
            "Suggestions drain/refill hook failed; "
            "TrackPlayed view_count update succeeded"
        )
```

From app/services/suggestions_service.py (lines 349-377 — DO NOT MODIFY):
```python
async def maybe_schedule_refill(target: int = SUGGESTIONS_TARGET_SIZE) -> int:
    current = await asyncio.to_thread(_count_mirror_rows_sync)
    deficit = max(0, target - current)
    if deficit > 0:
        try:
            await refill_suggestions_queue(target=target)
        except Exception:
            logger.exception(...)
    return deficit
```

VERIFIED by planner: `maybe_schedule_refill` itself awaits
`refill_suggestions_queue` when `deficit > 0` and short-circuits when
`deficit == 0`. This is the load-bearing fact for the test edits below — when the
existing test seeds 30 mirror rows (deficit=0), mocking
`refill_suggestions_queue` and asserting `assert_not_called()` STILL passes after
the fix because the deficit guard short-circuits before the mock is reached.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Drop if-removed gate in handle_track_played + update tests</name>
  <files>app/services/event_handlers.py, tests/test_event_handlers.py</files>
  <behavior>
    Three coupled changes — single atomic commit.

    BEHAVIORAL CONTRACT (post-fix):
    - handle_track_played always awaits maybe_schedule_refill after drain_track_from_mirror, regardless of drain return value
    - Steady-state (mirror at target=30, non-member play): drain returns False, maybe_schedule_refill is awaited, deficit=0, refill_suggestions_queue is NOT awaited (short-circuit at suggestions_service.py:364)
    - Bootstrap (empty mirror, any play): drain returns False, maybe_schedule_refill is awaited, deficit=30, refill_suggestions_queue IS awaited → playlist materializes
    - Phase 5 RATE-04 view_count++ + last_viewed_at update remains untouched

    TEST EXPECTATIONS:
    - test_drains_mirror_when_track_is_member_revised: unchanged behavior, still passes (mirror has 1 row → drain True → deficit=29 → refill awaited)
    - test_no_op_when_track_not_in_mirror_revised: docstring/comment update only — assertions still hold (30 mirror rows → deficit=0 → refill_suggestions_queue short-circuits inside maybe_schedule_refill, never reaches the mock)
    - test_empty_mirror_track_not_in_mirror_still_schedules_refill (NEW): empty mirror + non-member played track → assert refill_suggestions_queue WAS awaited (because deficit > 0 in an empty mirror)
    - test_drain_failure_does_not_break_rating_update: unchanged, still passes
  </behavior>
  <action>
    **CHANGE 1 — app/services/event_handlers.py lines 252-264.**

    Drop the `if removed:` gate. Drop the `removed = ` assignment too (no longer needed for control flow). Replace this block:

    ```python
    try:
        from app.services import suggestions_service

        removed = await suggestions_service.drain_track_from_mirror(
            event.plex_rating_key
        )
        if removed:
            await suggestions_service.maybe_schedule_refill()
    except Exception:
        logger.exception(
            "Suggestions drain/refill hook failed; "
            "TrackPlayed view_count update succeeded"
        )
    ```

    With:

    ```python
    try:
        from app.services import suggestions_service

        # CDL hotfix (260514): drop the `if removed:` gate. The original gate
        # caused a bootstrap deadlock — on a fresh deploy SuggestionsMirror is
        # empty, drain returns False, so refill never fired and the
        # Composer · Suggestions Plex playlist (created inside
        # refill_suggestions_queue when mp.plex_rating_key == '') was never
        # materialized. The deficit check inside maybe_schedule_refill
        # (suggestions_service.py:362-364) preserves the steady-state
        # "no churn" intent at the correct layer: deficit=0 → short-circuit;
        # deficit>0 → refill fires.
        await suggestions_service.drain_track_from_mirror(
            event.plex_rating_key
        )
        await suggestions_service.maybe_schedule_refill()
    except Exception:
        logger.exception(
            "Suggestions drain/refill hook failed; "
            "TrackPlayed view_count update succeeded"
        )
    ```

    Phase 5 conventions check: both `drain_track_from_mirror` and
    `maybe_schedule_refill` are already async coroutines that internally use
    `asyncio.to_thread` for sync DB work — no new `asyncio.to_thread` wrap is
    needed at the caller site (D-09 / Pitfall 4 unaffected).

    **CHANGE 2 — tests/test_event_handlers.py lines 556-563 (test_no_op_when_track_not_in_mirror_revised).**

    Update the docstring + comment ONLY. Assertions still hold under new
    behavior because the test seeds 30 mirror rows → deficit=0 → the deficit
    guard inside maybe_schedule_refill (suggestions_service.py:362-364)
    short-circuits before refill_suggestions_queue is called, so the mock is
    never reached. Replace the docstring at lines 559-562 with:

    ```python
    """CDL hotfix (260514): handle_track_played now ALWAYS awaits
    maybe_schedule_refill (the if-removed gate was dropped to fix the
    bootstrap deadlock). When the mirror is at target=30, the deficit guard
    inside maybe_schedule_refill (suggestions_service.py:362-364)
    short-circuits before refill_suggestions_queue is reached — so the mock
    here is correctly NOT called. view_count++ still happens. This preserves
    the original "no churn in steady state" intent at the correct layer.
    """
    ```

    And replace the comment at lines 628-629 with:

    ```python
    # Mirror at target → deficit=0 → maybe_schedule_refill short-circuits
    # before reaching refill_suggestions_queue. The mock is never invoked.
    # (Pre-CDL-hotfix this was guaranteed by `if removed:` in event_handlers;
    # post-hotfix it is guaranteed by the deficit guard one layer deeper.)
    ```

    Do NOT change any assertion in this test. Do NOT rename the test.

    **CHANGE 3 — tests/test_event_handlers.py: ADD new test inside class TestHandleTrackPlayedSuggestionsDrain.**

    Add this new test method directly after `test_no_op_when_track_not_in_mirror_revised` (i.e. inserted before `test_drain_failure_does_not_break_rating_update`):

    ```python
    def test_empty_mirror_track_not_in_mirror_still_schedules_refill(
        self, db_with_phase7
    ):
        """Bootstrap regression (CDL hotfix 260514): when SuggestionsMirror
        is empty, handle_track_played MUST schedule a refill even if the
        played track is not a mirror member, so the Composer · Suggestions
        playlist materializes on first play. Pre-fix this deadlocked because
        the `if removed:` gate suppressed the refill on a False drain.
        """
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock

        from app.models.events import TrackPlayedEvent
        from app.models.track import Track
        from app.services import suggestions_service
        from app.services.event_handlers import handle_track_played
        from app.database import get_engine

        # Played track exists; SuggestionsMirror is empty (db_with_phase7
        # provides a fresh DB — no mirror rows seeded by this test).
        played = Track(
            plex_rating_key="777",
            title="Bootstrap",
            artist="Fresh",
        )
        db_with_phase7.add(played)
        db_with_phase7.commit()

        evt = TrackPlayedEvent(
            plex_rating_key="777",
            last_viewed_at="2026-05-14T08:37:13+00:00",
            source="webhook",
            received_at=datetime.now(timezone.utc).isoformat(),
        )

        # Mock maybe_schedule_refill directly (the most direct assertion of
        # the bug fix — the gate is gone, so this MUST be awaited regardless
        # of the drain return value). Also mock refill_suggestions_queue to
        # prevent a real LLM/Plex call inside maybe_schedule_refill if the
        # mock is somehow bypassed.
        original_maybe = suggestions_service.maybe_schedule_refill
        original_refill = suggestions_service.refill_suggestions_queue
        mock_maybe = AsyncMock(return_value=30)  # deficit=30 (empty mirror)
        mock_refill = AsyncMock(return_value=None)
        suggestions_service.maybe_schedule_refill = mock_maybe
        suggestions_service.refill_suggestions_queue = mock_refill
        try:
            _run_async(handle_track_played(evt))
        finally:
            suggestions_service.maybe_schedule_refill = original_maybe
            suggestions_service.refill_suggestions_queue = original_refill

        # Phase 5 RATE-04 view_count++ still happens.
        with Session(get_engine()) as fresh:
            updated = fresh.exec(
                select(Track).where(Track.plex_rating_key == "777")
            ).first()
            assert updated.view_count == 1
            assert updated.last_viewed_at == "2026-05-14T08:37:13+00:00"

        # The bug fix: maybe_schedule_refill MUST be awaited even though
        # drain_track_from_mirror returned False (mirror was empty, so the
        # played track was not a member).
        mock_maybe.assert_awaited_once()
    ```

    Place this method between the existing `test_no_op_when_track_not_in_mirror_revised` (ends ~line 630) and `test_drain_failure_does_not_break_rating_update` (~line 632). Maintain class indentation (4 spaces).
  </action>
  <verify>
    <automated>pytest tests/test_event_handlers.py::TestHandleTrackPlayedSuggestionsDrain -v</automated>
  </verify>
  <done>
    All four tests in TestHandleTrackPlayedSuggestionsDrain pass:
    - test_drains_mirror_when_track_is_member_revised PASSED
    - test_no_op_when_track_not_in_mirror_revised PASSED
    - test_empty_mirror_track_not_in_mirror_still_schedules_refill PASSED (NEW)
    - test_drain_failure_does_not_break_rating_update PASSED

    Sanity check: `git stash` the event_handlers.py change, re-run the new
    test alone — it MUST FAIL (gate present, mock_maybe not called). Then
    restore the change and re-run — it MUST PASS. This proves the regression
    test actually guards the bug.
  </done>
</task>

</tasks>

<verification>
- `pytest tests/test_event_handlers.py::TestHandleTrackPlayedSuggestionsDrain -v` shows 4 passed
- `grep -n "if removed" app/services/event_handlers.py` returns nothing (gate removed)
- `grep -n "await suggestions_service.maybe_schedule_refill" app/services/event_handlers.py` returns the call site (unconditional)
- `grep -n "test_empty_mirror_track_not_in_mirror_still_schedules_refill" tests/test_event_handlers.py` returns one match (test added)
- Full test_event_handlers.py suite still passes: `pytest tests/test_event_handlers.py -v`
</verification>

<success_criteria>
1. Bootstrap regression test passes after fix, fails before fix (proven via git stash sanity check)
2. All existing TestHandleTrackPlayedSuggestionsDrain tests still pass (steady-state behavior preserved by deficit guard)
3. No regressions in the broader test_event_handlers.py suite
4. Single atomic commit message references CDL hotfix ID
5. On next NAS deploy, first TrackPlayed webhook will trigger refill_suggestions_queue, which will create the Composer · Suggestions Plex playlist (mp.plex_rating_key == '' branch) and populate the mirror — unblocking the bootstrap deadlock
</success_criteria>

<output>
After completion, create `.planning/quick/260514-cdl-fix-phase-7-suggestions-bootstrap-deadlo/260514-cdl-SUMMARY.md` capturing:
- Root cause one-liner
- Three changes made (event_handlers.py + 2 test edits)
- Verification result (test counts before/after)
- Deploy note: next NAS push will materialize the Composer · Suggestions playlist on first webhook
</output>
