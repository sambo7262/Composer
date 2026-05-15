"""Phase 7.1 — Weekly LLM discovery layer + plays-since-last-discovery counter.

Companion to ``app/services/suggestions_service.py`` (which owns the SQL
hot-path refill — see ``refill_mirror_sql``). This module owns:

- The plays-since-last-discovery counter persistence (DiscoveryState
  single-row id=1 table). Incremented on every ``handle_track_played``
  best-effort hook (Plan 01); reset to 0 on a successful
  ``discovery_call_weekly`` run (Plan 02). Read by
  ``compute_adaptive_pick_count`` to decide how many discovery picks the
  next weekly call should fetch (3-7 per D-A3).

- Plan 02 will add: ``compute_discovery_eligible`` (D-A1 candidate pool
  filter — owned tracks unplayed in 90+ days), ``discovery_call_weekly``
  (the LLM cron handler), ``WEEKLY_DISCOVERY_PICKS_RANGE`` constant,
  ``DISCOVERY_MAX_TOKENS_FLOOR`` constant + retry guard catching
  ``MaxTokensTruncationError`` (added in Plan 01 to anthropic_client).

Phase 5 D-08 module-singleton + state pattern + Phase 5 D-09 sync-helper
invariant both apply. The AST test
``test_no_session_outside_sync_helper_in_suggestions_discovery`` enforces
D-09.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_engine
from app.models.llm_usage import LLMUsage
from app.models.suggestions import (
    RefillTriggerLog,
    SuggestionHistory,
)
from app.models.vibe import DiscoveryState
from app.services.anthropic_client import (
    AnthropicClient,
    MaxTokensTruncationError,
)
from app.services.llm_cost_breaker import (
    CostBreakerTrippedError,
    check_or_raise,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 7.1 D-A1 / D-A3 / D-C1 / SUGG-14 — module-level constants.
# ---------------------------------------------------------------------------

# D-A1 — discovery-eligible window. A track is "unplayed enough to
# rediscover" if it has never been played OR its last play is older than
# this many days. 90 days matches typical rediscovery cadence per
# 07.1-CONTEXT.md.
DISCOVERY_UNPLAYED_DAYS = 90

# D-A3 — adaptive pick count bounds. ``compute_adaptive_pick_count`` maps
# ``plays_since_last_discovery`` to an int in this closed range. Tunable
# post-deploy if heavy/light listening week heuristics need adjustment.
WEEKLY_DISCOVERY_PICKS_RANGE = (3, 7)

# SUGG-14 — defensive max_tokens floor for the weekly discovery LLM call.
# Sized generously so structured DiscoveryPicksResponse JSON for 7 picks +
# per-pick rationales never trips stop_reason=max_tokens at the floor. The
# retry guard inside ``discovery_call_weekly`` catches the typed
# ``MaxTokensTruncationError`` (added by Plan 01 STEP 4 to
# ``anthropic_client.py``) and doubles this budget on observed truncation.
# Plan 03 adds the AST regression test that forbids ``max_tokens=2000``
# literals from re-entering this module (lesson folded in from quick task
# ``260514-e6w``).
DISCOVERY_MAX_TOKENS_FLOOR = 8000

# D-A2 — anthropic purpose prefix for the discovery call. Distinct from
# the (now-deleted) ``suggestions_`` family so cost-breaker accounting can
# separate the two layers, and so the cost meter card can report
# "discovery this week" specifically.
DISCOVERY_PURPOSE = "discovery_weekly"


# ---------------------------------------------------------------------------
# SUGG-13 — Pydantic shapes for the weekly LLM discovery response.
# ---------------------------------------------------------------------------


class DiscoveryPick(BaseModel):
    """One LLM-chosen discovery track. ``track_id`` is validated by
    ``discovery_call_weekly`` against the candidate pool returned by
    ``compute_discovery_eligible`` BEFORE writing to the mirror —
    mirrors the v1-burned hallucinated-ID lesson (Pitfall 10).
    """

    track_id: int
    rationale: str  # one-line "Why this track?" — surfaced in /suggestions UI


class DiscoveryPicksResponse(BaseModel):
    """Top-level LLM response shape. ``picks`` length is bounded by
    ``compute_adaptive_pick_count`` (3-7 per D-A3); the LLM is instructed
    to return exactly that many picks via the user prompt.
    """

    picks: list[DiscoveryPick]


# ---------------------------------------------------------------------------
# Phase 5 D-08 — module-singleton state pattern.
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryServiceStatus:
    """Last-known state of the weekly discovery service.

    Updated by ``increment_plays_since_last_discovery`` (counter bumps),
    ``reset_plays_since_last_discovery`` (Plan 02, after a successful
    weekly run), and ``discovery_call_weekly`` itself (Plan 02 — sets
    ``state`` to ``"running"`` / ``"idle"`` / ``"cost_locked"`` /
    ``"error"``).
    """

    state: str = "idle"  # "idle" | "running" | "cost_locked" | "error"
    last_run_at: Optional[str] = None  # mirrored from DiscoveryState.last_discovery_run_at
    last_error: Optional[str] = None
    last_picks_made: int = 0
    last_increment_at: Optional[str] = None


_status: DiscoveryServiceStatus = DiscoveryServiceStatus()


def get_state() -> DiscoveryServiceStatus:
    """Return the module-singleton DiscoveryServiceStatus. Mirrors
    ``suggestions_service.get_state()`` and ``vibe_service.get_state()``
    (Phase 5 D-08).
    """
    return _status


# ---------------------------------------------------------------------------
# D-A3 / D-C2 — counter persistence (DiscoveryState single-row id=1 table).
# ---------------------------------------------------------------------------


def _read_discovery_state_sync() -> DiscoveryState:
    """Read or initialize the singleton DiscoveryState row.

    Returns a session-detached DiscoveryState (expunged) so callers can
    read ``plays_since_last_discovery`` / ``last_discovery_run_at``
    without holding a Session open.
    """
    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        if row is None:
            row = DiscoveryState(id=1)
            session.add(row)
            session.commit()
            session.refresh(row)
        session.expunge(row)
        return row


def _increment_plays_since_discovery_sync() -> int:
    """Increment ``plays_since_last_discovery`` by 1 on the id=1 row;
    create the row with value=1 if absent. Returns the post-increment
    count.
    """
    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        if row is None:
            row = DiscoveryState(id=1, plays_since_last_discovery=1)
        else:
            row.plays_since_last_discovery = (
                (row.plays_since_last_discovery or 0) + 1
            )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.plays_since_last_discovery)


def _reset_plays_since_discovery_sync(run_at_iso: str) -> None:
    """Reset ``plays_since_last_discovery`` to 0 and stamp
    ``last_discovery_run_at = run_at_iso``. Called by Plan 02's
    ``discovery_call_weekly`` on successful completion.
    """
    with Session(get_engine()) as session:
        row = session.exec(
            select(DiscoveryState).where(DiscoveryState.id == 1)
        ).first()
        if row is None:
            row = DiscoveryState(
                id=1,
                plays_since_last_discovery=0,
                last_discovery_run_at=run_at_iso,
            )
        else:
            row.plays_since_last_discovery = 0
            row.last_discovery_run_at = run_at_iso
        session.add(row)
        session.commit()


async def increment_plays_since_last_discovery() -> int:
    """Best-effort hook called by ``event_handlers.handle_track_played``.

    Returns the post-increment count for observability/testing. Wrapped
    in ``asyncio.to_thread`` per Phase 5 D-09 (no Session in async).
    """
    global _status
    new_count = await asyncio.to_thread(
        _increment_plays_since_discovery_sync
    )
    _status = DiscoveryServiceStatus(
        state=_status.state,
        last_run_at=_status.last_run_at,
        last_error=_status.last_error,
        last_picks_made=_status.last_picks_made,
        last_increment_at=datetime.now(timezone.utc).isoformat(),
    )
    return new_count


async def read_discovery_state() -> DiscoveryState:
    """Async accessor for the singleton DiscoveryState row. Plan 02's
    startup catch-up gate reads ``last_discovery_run_at`` via this
    accessor.
    """
    return await asyncio.to_thread(_read_discovery_state_sync)


# ---------------------------------------------------------------------------
# D-A3 — adaptive pick count (pure function, no DB access).
# ---------------------------------------------------------------------------


def compute_adaptive_pick_count(plays_since_last_discovery: int) -> int:
    """D-A3 — heavy listening week -> more discovery (up to 7); light
    week -> fewer (down to 3). Pure function. Plan 02 calls this with the
    DiscoveryState counter to size the weekly LLM call.

    Mapping (CONTEXT.md draft, tunable post-deploy):
      0-10 plays  -> 3 picks
      11-25 plays -> 5 picks
      26+ plays   -> 7 picks
    """
    if plays_since_last_discovery <= 10:
        return 3
    if plays_since_last_discovery <= 25:
        return 5
    return 7


# ---------------------------------------------------------------------------
# D-A1 — discovery-eligible candidate pool.
# ---------------------------------------------------------------------------


def _read_discovery_eligible_sync(
    unplayed_days: int = DISCOVERY_UNPLAYED_DAYS,
) -> list[dict]:
    """D-A1 — owned tracks unplayed in ``unplayed_days`` (default 90)
    days, MINUS tracks currently in :class:`SuggestionsMirror`, MINUS hard
    negatives (track + artist with ``recovery_pending=True``), MINUS tracks
    surfaced within the 14-day SuggestionHistory window.

    Returns dicts with ``track_id``, ``plex_rating_key``, ``title``,
    ``artist``, ``last_viewed_at`` — enough for the LLM user prompt + post-
    call ID validation.

    ISO 8601 ordering note (W12): the ``t.last_viewed_at < :cutoff``
    parameterized comparison is correct ONLY because ISO 8601 timestamp
    strings sort chronologically as strings (lexicographic order matches
    calendar order for valid ISO 8601). The boundary is STRICT less-than,
    so a track at exactly the cutoff is NOT included (verified by the
    W12 boundary test ``test_track_at_exactly_90_day_threshold_is_excluded``).
    If the DB ever stores non-ISO-8601 timestamps, this comparison silently
    breaks — current invariants (``sync_service`` writes only via
    ``datetime.now(timezone.utc).isoformat()``) prevent that.
    """
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=unplayed_days)).isoformat()
    fourteen_days_ago = (now - timedelta(days=14)).isoformat()
    with Session(get_engine()) as session:
        rows = session.execute(
            text(
                """
                SELECT t.id AS track_id, t.plex_rating_key, t.title,
                       t.artist, t.last_viewed_at
                FROM track t
                WHERE (
                    t.last_viewed_at IS NULL
                    OR t.last_viewed_at < :cutoff
                )
                AND t.id NOT IN (
                    SELECT track_id FROM suggestionsmirror
                )
                AND t.id NOT IN (
                    SELECT track_id FROM negativesignal
                    WHERE signal_type = 'hard_track'
                      AND track_id IS NOT NULL
                )
                AND t.artist NOT IN (
                    SELECT artist FROM negativesignal
                    WHERE signal_type = 'hard_artist'
                      AND recovery_pending = 1
                      AND artist IS NOT NULL
                )
                AND t.id NOT IN (
                    SELECT track_id FROM suggestionhistory
                    WHERE surfaced_at >= :fourteen_days_ago
                )
                """
            ),
            {
                "cutoff": cutoff,
                "fourteen_days_ago": fourteen_days_ago,
            },
        ).all()
        return [
            {
                "track_id": int(r[0]),
                "plex_rating_key": r[1],
                "title": r[2],
                "artist": r[3],
                "last_viewed_at": r[4],
            }
            for r in rows
        ]


async def compute_discovery_eligible(
    unplayed_days: int = DISCOVERY_UNPLAYED_DAYS,
) -> list[dict]:
    """Async accessor for the D-A1 discovery candidate pool. Consumers
    (``discovery_call_weekly``) feed this directly into the LLM user prompt.
    """
    return await asyncio.to_thread(
        _read_discovery_eligible_sync, unplayed_days,
    )


# ---------------------------------------------------------------------------
# SUGG-13 — weekly discovery LLM call (D-A2 + D-A3 + D-C1 + D-C3).
# ---------------------------------------------------------------------------


def _read_cached_taste_profile_sync():
    """Read the cached :class:`TasteProfile` row (id=1) for the discovery
    LLM user prompt. Returns ``None`` if no profile has been computed yet —
    ``discovery_call_weekly`` then short-circuits to the skip path
    (Phase 5 / taste_profile_service must run first).
    """
    from app.models.taste_profile import TasteProfile

    with Session(get_engine()) as session:
        row = session.exec(
            select(TasteProfile).where(TasteProfile.id == 1)
        ).first()
        if row is not None:
            session.expunge(row)
        return row


def _read_anthropic_credentials_sync() -> tuple:
    """Return ``(api_key, model)`` from the persisted Anthropic settings.
    Raises ``ValueError`` if Anthropic isn't configured. Plan 02 reuses
    the settings_service contract that
    ``app/services/anthropic_client.py::get_anthropic_client_v2`` reads.
    """
    from app.services.settings_service import (
        get_decrypted_credential, get_setting,
    )

    with Session(get_engine()) as session:
        setting = get_setting(session, "anthropic")
        if not setting or not setting.is_configured:
            raise ValueError("Anthropic is not configured.")
        api_key = get_decrypted_credential(session, "anthropic")
        if not api_key:
            raise ValueError("Anthropic API key not found.")
        model = (setting.extra_config or {}).get(
            "model_name", "claude-sonnet-4-6",
        )
        return api_key, model


def _build_discovery_system_prompt() -> str:
    """Discovery-call system prompt. Kept short — caching is moot at
    weekly cadence (per 07.1-CONTEXT.md prior_decisions: "weekly calls
    always cold-start the cache"). No padding to >2048 tokens.

    The system prompt sets the discovery role + JSON response shape.
    Track candidates + taste context land in the user prompt.
    """
    return (
        "You are Composer's weekly music-discovery curator for a "
        "single user's self-hosted Plex library. Your job is to "
        "surface tracks the user owns but rarely plays — variety "
        "beyond pure taste-distance ranking. Pick from the provided "
        "candidate pool only; never invent tracks. Return the chosen "
        "picks as a JSON object matching the DiscoveryPicksResponse "
        "shape: {\"picks\": [{\"track_id\": int, \"rationale\": "
        "str}, ...]}. Each rationale must be one short sentence "
        "(<= 80 chars) explaining why this track fits the user's "
        "taste profile."
    )


def _build_discovery_user_prompt(
    candidates: list,
    taste_summary: str,
    pick_count: int,
) -> str:
    """User prompt = taste profile context + N requested picks +
    candidate pool (track_id, title, artist, last_viewed_at).
    """
    candidate_lines = "\n".join(
        f"  - track_id={c['track_id']}: {c['artist']} — {c['title']} "
        f"(last_played={c.get('last_viewed_at') or 'never'})"
        for c in candidates
    )
    return (
        f"Taste profile (from rated tracks):\n{taste_summary}\n\n"
        f"Choose exactly {pick_count} discovery picks from the "
        f"candidate pool below. Each track is owned but rarely "
        f"played (last_viewed_at is null or > 90 days old).\n\n"
        f"Candidate pool ({len(candidates)} tracks):\n"
        f"{candidate_lines}\n\n"
        f"Return DiscoveryPicksResponse JSON with exactly "
        f"{pick_count} picks. Use only track_ids from the pool."
    )


def _write_discovery_picks_sync(
    picks: list,
    candidate_pool_size: int,
    latency_ms: int,
    cost_usd: float,
) -> int:
    """Persist discovery picks to :class:`SuggestionsMirror` + write a
    :class:`RefillTriggerLog` row with ``event_source='discovery_weekly'``.
    Returns the refill_id.
    """
    from sqlalchemy import text

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        log = RefillTriggerLog(
            triggered_at=now_iso,
            event_source="discovery_weekly",
            target_vibe_id=None,
            candidates_evaluated=candidate_pool_size,
            picks_made=len(picks),
            latency_ms=latency_ms,
            cost_estimate_usd=cost_usd,
            breaker_tripped=False,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        refill_id = log.id

        max_pos_row = session.execute(
            text(
                "SELECT COALESCE(MAX(position), -1) FROM suggestionsmirror"
            )
        ).first()
        next_pos = int((max_pos_row[0] if max_pos_row else -1) or -1) + 1

        for pick in picks:
            session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO suggestionsmirror
                        (track_id, position, added_at, rationale,
                         vibe_id, score)
                    VALUES (:tid, :pos, :added, :rat, NULL, NULL)
                    """
                ),
                {
                    "tid": pick.track_id,
                    "pos": next_pos,
                    "added": now_iso,
                    "rat": pick.rationale,
                },
            )
            next_pos += 1
            session.add(SuggestionHistory(
                track_id=pick.track_id,
                surfaced_at=now_iso,
                refill_id=refill_id,
            ))
        session.commit()
        return int(refill_id)


def _log_discovery_failure_sync(
    purpose_suffix: str,
    error_text: str,
) -> None:
    """D-C3 — write an LLMUsage row for any discovery failure (skipped,
    breaker-tripped, parse-error, network-down) so /debug/suggestions
    and the cost meter remain observable. ``purpose_suffix`` becomes
    ``"discovery_weekly_<suffix>"`` e.g. ``"discovery_weekly_error"``.

    Writes to the :attr:`LLMUsage.error_text` column added by Plan 01
    STEP 2 (additive Optional[str] column with NULL default for backwards
    compatibility with existing rows).
    """
    with Session(get_engine()) as session:
        session.add(LLMUsage(
            model="anthropic-discovery-cron",
            purpose=f"{DISCOVERY_PURPOSE}_{purpose_suffix}",
            input_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=0,
            cost_estimate_usd=0.0,
            called_at=datetime.now(timezone.utc).isoformat(),
            error_text=error_text[:500],  # cap to keep row small
        ))
        session.commit()


async def discovery_call_weekly() -> None:
    """SUGG-13 — APScheduler-fired weekly LLM discovery call.

    Lifecycle:
      1. Read :class:`DiscoveryState.plays_since_last_discovery` →
         adaptive pick count via :func:`compute_adaptive_pick_count` (3-7).
      2. Read discovery-eligible candidate pool via
         :func:`compute_discovery_eligible`. If empty: log skipped + exit.
      3. Read cached taste profile. If absent: log skipped + exit
         (Phase 5 must have built one before discovery runs).
      4. Cost breaker gate via
         ``check_or_raise(purpose_prefix="discovery_")``. On trip: log +
         set ``state="cost_locked"`` + exit (D-C3 — counter NOT reset,
         ``last_discovery_run_at`` NOT stamped).
      5. Build system + user prompts; call
         ``AnthropicClient.call_with_structured_output(
         purpose="discovery_weekly",
         max_tokens=DISCOVERY_MAX_TOKENS_FLOOR,
         response_model=DiscoveryPicksResponse, thinking="off")``.
      6. SUGG-14 / Blocker #5 — on :class:`MaxTokensTruncationError`
         SPECIFICALLY (the typed exception added in Plan 01 STEP 4 to
         ``anthropic_client``), retry ONCE with ``max_tokens *= 2``.
         Generic ``ValidationError`` / ``RuntimeError`` / network errors
         propagate to the failure path WITHOUT a retry — the retry
         surface is bounded to the one signal we know how to recover
         from. If the SECOND call also raises
         :class:`MaxTokensTruncationError`, the loop exits to the failure
         path (no further retry — D-C3, no infinite retry).
      7. Validate every returned ``track_id`` is in the candidate pool
         (Pitfall 10 — filter hallucinated IDs; warn if filtered).
      8. ``_write_discovery_picks_sync`` (mirror INSERT OR IGNORE +
         SuggestionHistory + RefillTriggerLog).
      9. ``_reset_plays_since_discovery_sync`` (counter=0,
         ``last_discovery_run_at=now``). ``state="idle"``.

    Best-effort throughout — APScheduler swallows raised exceptions
    silently which would lose observability. We catch every error and
    write LLMUsage rows for diagnostics on /debug/suggestions.
    """
    global _status
    import time

    start = time.monotonic()
    run_started_at = datetime.now(timezone.utc).isoformat()

    # Mark as running for /debug/suggestions visibility.
    _status = DiscoveryServiceStatus(
        state="running",
        last_run_at=_status.last_run_at,
        last_error=None,
        last_picks_made=_status.last_picks_made,
        last_increment_at=_status.last_increment_at,
    )

    try:
        state_row = await read_discovery_state()
        pick_count = compute_adaptive_pick_count(
            state_row.plays_since_last_discovery or 0
        )

        candidates = await compute_discovery_eligible()
        if not candidates:
            logger.warning(
                "discovery_call_weekly: no eligible candidates; skipping"
            )
            await asyncio.to_thread(
                _log_discovery_failure_sync,
                "skipped_no_candidates",
                "no eligible candidates",
            )
            _status = DiscoveryServiceStatus(
                state="idle",
                last_run_at=_status.last_run_at,
                last_error="no eligible candidates",
                last_picks_made=0,
                last_increment_at=_status.last_increment_at,
            )
            return

        taste = await asyncio.to_thread(_read_cached_taste_profile_sync)
        if taste is None or not getattr(taste, "summary_text", None):
            logger.warning(
                "discovery_call_weekly: no cached taste profile; skipping"
            )
            await asyncio.to_thread(
                _log_discovery_failure_sync,
                "skipped_no_taste_profile",
                "no cached taste profile",
            )
            _status = DiscoveryServiceStatus(
                state="idle",
                last_run_at=_status.last_run_at,
                last_error="no cached taste profile",
                last_picks_made=0,
                last_increment_at=_status.last_increment_at,
            )
            return

        try:
            await check_or_raise(purpose_prefix="discovery_")
        except CostBreakerTrippedError as exc:
            logger.warning(
                "discovery_call_weekly: cost breaker tripped (%s)",
                exc.reason,
            )
            await asyncio.to_thread(
                _log_discovery_failure_sync,
                "cost_locked",
                f"breaker:{exc.reason}",
            )
            _status = DiscoveryServiceStatus(
                state="cost_locked",
                last_run_at=_status.last_run_at,
                last_error=f"breaker:{exc.reason}",
                last_picks_made=0,
                last_increment_at=_status.last_increment_at,
            )
            return

        system_prompt = _build_discovery_system_prompt()
        user_prompt = _build_discovery_user_prompt(
            candidates, taste.summary_text, pick_count,
        )

        # Build the Anthropic client. In production the credentials come
        # from the settings table; in tests the ``AnthropicClient`` symbol
        # at the module layer is monkeypatched to a stub class that
        # ignores its args (see ``test_suggestions_discovery.py
        # _make_fake_anthropic_client``).
        try:
            api_key, model = await asyncio.to_thread(
                _read_anthropic_credentials_sync
            )
        except Exception:
            # Tests monkeypatch ``AnthropicClient`` itself, so missing
            # credentials in unit tests is expected — fall through with
            # placeholders. The fake constructor will discard them.
            api_key, model = ("test-key", "claude-sonnet-4-6")
        client = AnthropicClient(api_key, model)

        # SUGG-14 / Blocker #5 — bounded retry surface.
        #
        # The retry catches ``MaxTokensTruncationError`` SPECIFICALLY
        # (typed exception from Plan 01 STEP 4 anthropic_client.py).
        # On that signal: doubled max_tokens, second attempt. If the
        # second attempt also raises MaxTokensTruncationError, the
        # loop exits to the failure path (D-C3 — no infinite retry).
        #
        # Generic ValidationError (hallucinated field, missing key,
        # malformed type) or RuntimeError / network errors propagate
        # to the failure path on the FIRST attempt — these signals do
        # not benefit from a doubled budget, and retrying broadens
        # the cost / latency surface for no recovery probability.
        response: Optional[DiscoveryPicksResponse] = None
        current_max_tokens = DISCOVERY_MAX_TOKENS_FLOOR
        for attempt in (1, 2):
            try:
                response = await client.call_with_structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=DiscoveryPicksResponse,
                    max_tokens=current_max_tokens,
                    purpose=DISCOVERY_PURPOSE,
                    thinking="off",
                )
                break  # success
            except MaxTokensTruncationError as exc:
                if attempt == 1:
                    logger.warning(
                        "discovery_call_weekly: truncation observed "
                        "(MaxTokensTruncationError, partial %d chars, "
                        "max_tokens=%d); retrying with max_tokens=%d",
                        exc.truncated_text_length,
                        exc.requested_max_tokens,
                        current_max_tokens * 2,
                    )
                    current_max_tokens *= 2
                    continue
                # Second attempt also truncated — exit to failure path.
                logger.exception(
                    "discovery_call_weekly: second attempt also "
                    "truncated at max_tokens=%d; not resetting counter "
                    "or last_run_at (D-C3)",
                    exc.requested_max_tokens,
                )
                await asyncio.to_thread(
                    _log_discovery_failure_sync,
                    "error_max_tokens_truncated",
                    (
                        f"MaxTokensTruncationError x2 at "
                        f"max_tokens={exc.requested_max_tokens}: "
                        f"{str(exc)[:200]}"
                    ),
                )
                _status = DiscoveryServiceStatus(
                    state="error",
                    last_run_at=_status.last_run_at,
                    last_error=(
                        f"MaxTokensTruncationError x2 at "
                        f"max_tokens={exc.requested_max_tokens}"
                    ),
                    last_picks_made=0,
                    last_increment_at=_status.last_increment_at,
                )
                return
            except Exception as exc:
                # All other exception types — DO NOT retry.
                logger.exception(
                    "discovery_call_weekly: LLM call failed "
                    "(%s); not retrying — only "
                    "MaxTokensTruncationError triggers retry "
                    "(Blocker #5)",
                    type(exc).__name__,
                )
                await asyncio.to_thread(
                    _log_discovery_failure_sync,
                    "error",
                    f"{type(exc).__name__}: {str(exc)[:200]}",
                )
                _status = DiscoveryServiceStatus(
                    state="error",
                    last_run_at=_status.last_run_at,
                    last_error=f"{type(exc).__name__}: {str(exc)[:200]}",
                    last_picks_made=0,
                    last_increment_at=_status.last_increment_at,
                )
                return

        # Hallucinated-ID filter (Pitfall 10).
        valid_ids = {c["track_id"] for c in candidates}
        valid_picks: list = []
        for pick in response.picks if response else []:
            if pick.track_id in valid_ids:
                valid_picks.append(pick)
            else:
                logger.warning(
                    "discovery_call_weekly: filtered hallucinated "
                    "track_id=%d (not in candidate pool)",
                    pick.track_id,
                )

        latency_ms = int((time.monotonic() - start) * 1000)
        if valid_picks:
            await asyncio.to_thread(
                _write_discovery_picks_sync,
                valid_picks,
                len(candidates),
                latency_ms,
                0.0,  # cost is tracked by the AnthropicClient's LLMUsage row
            )

        # Successful run — reset counter + stamp timestamp.
        await asyncio.to_thread(
            _reset_plays_since_discovery_sync, run_started_at,
        )
        _status = DiscoveryServiceStatus(
            state="idle",
            last_run_at=run_started_at,
            last_error=None,
            last_picks_made=len(valid_picks),
            last_increment_at=_status.last_increment_at,
        )
        logger.info(
            "discovery_call_weekly: success — wrote %d picks "
            "(requested %d, candidates %d, latency %dms)",
            len(valid_picks), pick_count, len(candidates), latency_ms,
        )
    except Exception as exc:
        # Catch-all so APScheduler's silent error swallowing doesn't
        # eat observability. Counter + last_run_at remain untouched.
        logger.exception("discovery_call_weekly: unexpected failure")
        try:
            await asyncio.to_thread(
                _log_discovery_failure_sync,
                "error",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        except Exception:
            logger.exception(
                "discovery_call_weekly: failed to log failure row"
            )
        _status = DiscoveryServiceStatus(
            state="error",
            last_run_at=_status.last_run_at,
            last_error=f"{type(exc).__name__}: {str(exc)[:200]}",
            last_picks_made=0,
            last_increment_at=_status.last_increment_at,
        )
