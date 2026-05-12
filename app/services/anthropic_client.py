"""v2 Anthropic client (NEW — D-01).

Built on the official anthropic>=0.100 SDK with explicit `cache_control={"type":"ephemeral","ttl":"1h"}`
on the system message. v1's `llm_client.py` stays untouched until Phase 7's chat retirement.

Key invariants (do NOT silently change):
- cache_control TTL is "1h" explicitly. The 5-minute default silently regressed in March 2026 (Pitfall 4).
  Without explicit ttl="1h" every call is a cache miss after 5 minutes — budget blows fast.
- Structured output uses Pydantic.model_validate_json — Instructor reformats system messages and fights
  prompt caching (D-03). Removed mid-v1; do not reintroduce.
- Every call inserts one row into LLMUsage for cost telemetry + circuit breaker scaffolding (D-04).

# DESIGN NOTE — Phase 7 forward-compat (D-04):
# Phase 5 ships LLMUsage as the audit log; circuit breaker counters (daily_calls, burst_window_calls,
# last_call_at) are COMPUTED via aggregate SELECT against this table on demand. This is correct for
# Phase 5 because the only LLM call here is the taste-profile recompute, which is rate-limited by the
# >=10% rated-set delta gate (D-18) — at most ~1/week realistically. Phase 7 (suggestions ranking) will
# exercise the breaker on a much hotter path. If profiling shows the aggregate-SELECT pattern is too slow
# for Phase 7's per-event check, refactor to either dedicated counter columns on a CircuitBreakerState
# row or an in-memory counter cache (rebuilt from LLMUsage at startup). Phase 5 schema is sufficient
# without changes for Phase 7's CORRECTNESS requirements; the open question is performance only.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Literal, Type, TypeVar

from anthropic import AsyncAnthropic, BadRequestError
from pydantic import BaseModel
from sqlmodel import Session

from app.database import get_engine
from app.models.llm_usage import LLMUsage

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Phase 6.2 Plan 01 Task 1 — adaptive extended thinking on Sonnet 4.6.
# RESEARCH §1.1: manual ``budget_tokens`` is deprecated on Sonnet 4.6 and
# already returns 400 on Opus 4.7. ``adaptive`` is the recommended path and
# is forward-compatible.
ThinkingMode = Literal["off", "adaptive"]

# Sonnet 4.6 cache breakpoint minimum (per platform.claude.com/docs).
# System prompts shorter than this cannot be cached; the SDK silently does nothing.
SONNET_4_6_CACHE_MIN_TOKENS = 2048

# Sonnet 4.6 pricing as of May 2026 (USD per 1M tokens).
# NOTE: Pricing changes — re-verify before adding production billing alerts in Phase 7.
PRICING_INPUT_USD_PER_MTOK = 3.0
PRICING_CACHE_WRITE_1H_USD_PER_MTOK = 6.0
PRICING_CACHE_READ_USD_PER_MTOK = 0.30
PRICING_OUTPUT_USD_PER_MTOK = 15.0


class AnthropicClient:
    """Wraps AsyncAnthropic with prompt caching + structured output + per-call usage logging."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        # Note: api_key is kept inside the SDK instance only — never stored on `self`
        # for log-message safety (T-05-17 mitigation).
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def call_with_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        max_tokens: int = 1024,
        purpose: str = "unspecified",
        thinking: ThinkingMode = "off",
    ) -> T:
        """Call Anthropic with cacheable system prompt; parse JSON response into Pydantic.

        Args:
            system_prompt: System message text. SHOULD be >=2048 tokens to engage Sonnet
                4.6's prompt cache (Pitfall 4). Caller is responsible for padding.
            user_prompt: User message text (NOT cached — varies per call).
            response_model: Pydantic class. Output is parsed via .model_validate_json (D-03).
            max_tokens: Output token budget. Includes thinking tokens when
                ``thinking='adaptive'`` (RESEARCH §1.3 — thinking tokens roll
                into ``response.usage.output_tokens`` at the output rate).
            purpose: Logical label written to LLMUsage.purpose for cost attribution.
            thinking: Phase 6.2 Plan 01 Task 1 + hotfix 260512-k3n.
                Extended thinking mode. Sends a ``thinking`` kwarg in BOTH
                modes so the BadRequestError-retry fallback has a known-good
                shape to fall back TO:
                  - ``"off"``      → ``thinking={"type": "disabled"}``
                  - ``"adaptive"`` → ``thinking={"type": "enabled",
                                                 "budget_tokens": 2000}``
                The previous string-literal ``{"type": "adaptive"}`` was
                rejected by the Anthropic API with
                ``BadRequestError: 400 — 'adaptive thinking is not supported
                on this model'`` — see hotfix 260512-k3n. ``budget_tokens``
                is pinned at 2000 per hotfix task scope. If the model still
                rejects extended thinking (e.g. Opus 4.7), the wrapper
                retries ONCE with ``{"type": "disabled"}`` and logs a
                warning.

        Returns:
            An instance of response_model parsed from the FIRST content block
            whose ``type == "text"``. The legacy positional-indexing access
            would crash when thinking is enabled because the first block then
            becomes a ThinkingBlock with no ``.text`` attribute (T-062-01).
        """
        kwargs = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    # OPS-02 / Pitfall 4: explicit ttl=1h. Default silently regressed to 5min Mar 2026.
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            "messages": [{"role": "user", "content": user_prompt}],
        }
        # Hotfix 260512-k3n: translate ThinkingMode to the documented dict
        # form the Anthropic API actually accepts. The previous
        # ``{"type": "adaptive"}`` literal was rejected with
        # ``BadRequestError: 400 — 'adaptive thinking is not supported on this
        # model'``. Send the kwarg in BOTH modes so the retry fallback below
        # has a known-good shape to fall back TO.
        if thinking == "adaptive":
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2000}
        else:
            kwargs["thinking"] = {"type": "disabled"}

        try:
            response = await self._client.messages.create(**kwargs)
        except BadRequestError as exc:
            # Hotfix 260512-k3n: some models reject extended thinking outright
            # (e.g. Opus 4.7, certain Sonnet revisions). Retry ONCE with
            # disabled so the propose flow isn't blocked when the operator
            # points Composer at such a model. Tight match: only retry when
            # the error message specifically references "thinking" AND we
            # were actually trying to enable it.
            if (
                "thinking" in str(exc).lower()
                and kwargs.get("thinking", {}).get("type") == "enabled"
            ):
                logger.warning(
                    "model %s does not support extended thinking; "
                    "retrying with thinking disabled",
                    self._model,
                )
                kwargs["thinking"] = {"type": "disabled"}
                response = await self._client.messages.create(**kwargs)
            else:
                # Not a thinking-rejection — preserve Phase 5 convention of
                # tight per-handler except clauses; re-raise for the caller.
                raise

        cache_creation = response.usage.cache_creation_input_tokens or 0
        cache_read = response.usage.cache_read_input_tokens or 0
        input_tokens = response.usage.input_tokens or 0
        output_tokens = response.usage.output_tokens or 0

        if cache_creation == 0 and cache_read == 0:
            logger.warning(
                "Anthropic caching not engaged for purpose=%s — system prompt likely <%d tokens "
                "(Sonnet 4.6 minimum). Total input %d.",
                purpose,
                SONNET_4_6_CACHE_MIN_TOKENS,
                input_tokens,
            )

        # Phase 6.2 Plan 01 Task 1 — surface stop_reason='max_tokens' as a
        # WARNING so /debug/vibes can flag Pass 2 truncations (Pitfall E /
        # T-062-07). When real Pass 2 calls hit this we raise max_tokens to
        # 6000-8000 per RESEARCH §1.6 follow-up.
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens":
            logger.warning(
                "Anthropic response stop_reason=max_tokens for purpose=%s — "
                "output truncated; consider raising max_tokens.",
                purpose,
            )

        await self._log_usage(
            purpose=purpose,
            model=self._model,
            input_tokens=input_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            output_tokens=output_tokens,
        )

        # Phase 6.2 Plan 01 Task 1 — robust text-block extraction.
        # The legacy positional-indexing access AttributeError'd the moment
        # thinking was enabled (the first content block became a ThinkingBlock
        # with no .text attribute). RESEARCH §1.2 / Pitfall A. Iterating to
        # find the first ``type=="text"`` block is forward-compatible with
        # future block types (e.g. ``redacted_thinking``, future metadata
        # blocks).
        text_block = next(
            (b for b in response.content if getattr(b, "type", None) == "text"),
            None,
        )
        if text_block is None:
            block_types = [getattr(b, "type", "<unknown>") for b in response.content]
            raise ValueError(
                f"Anthropic response missing text block "
                f"(purpose={purpose}, block types={block_types}, "
                f"stop_reason={stop_reason})"
            )
        text = text_block.text.strip()
        # Strip possible markdown fences in case the model wrapped JSON in ```json ... ```.
        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) >= 2:
                text = "\n".join(lines[1:])
            text = text.rstrip()
            if text.endswith("```"):
                text = text[:-3]
        text = text.strip()

        # Tolerate trailing prose AND leading prose: find the first '{' and use
        # json.JSONDecoder.raw_decode to read exactly one JSON object, ignoring
        # anything after it. The Anthropic Sonnet model occasionally appends
        # commentary after a valid JSON response (production traceback
        # 2026-05-10: "trailing characters at line 47 column 1").
        first_brace = text.find("{")
        if first_brace == -1:
            # No JSON object found — let Pydantic raise the canonical error
            # so callers still see a ValidationError, not a custom exception.
            return response_model.model_validate_json(text)
        try:
            obj, _end = json.JSONDecoder().raw_decode(text[first_brace:])
        except json.JSONDecodeError:
            # raw_decode failed — fall back to model_validate_json so the
            # ValidationError surfaces the LLM's malformed output verbatim.
            return response_model.model_validate_json(text[first_brace:])
        return response_model.model_validate(obj)

    async def _log_usage(
        self,
        purpose: str,
        model: str,
        input_tokens: int,
        cache_creation_tokens: int,
        cache_read_tokens: int,
        output_tokens: int,
    ) -> None:
        """Insert one row per call. See DESIGN NOTE at module top re: Phase 7 forward-compat."""
        def _insert():
            with Session(get_engine()) as session:
                cost = (
                    input_tokens * PRICING_INPUT_USD_PER_MTOK / 1_000_000
                    + cache_creation_tokens * PRICING_CACHE_WRITE_1H_USD_PER_MTOK / 1_000_000
                    + cache_read_tokens * PRICING_CACHE_READ_USD_PER_MTOK / 1_000_000
                    + output_tokens * PRICING_OUTPUT_USD_PER_MTOK / 1_000_000
                )
                row = LLMUsage(
                    called_at=datetime.now(timezone.utc).isoformat(),
                    purpose=purpose,
                    model=model,
                    input_tokens=input_tokens,
                    cache_creation_input_tokens=cache_creation_tokens,
                    cache_read_input_tokens=cache_read_tokens,
                    output_tokens=output_tokens,
                    cost_estimate_usd=cost,
                )
                session.add(row)
                session.commit()

        await asyncio.to_thread(_insert)


def get_anthropic_client_v2(session) -> AnthropicClient:
    """Factory using v1's existing 'anthropic' settings row (no new credential introduced).

    v1's `llm_client.get_anthropic_client` stays — this is the v2 path with the new SDK.
    Default model is `claude-sonnet-4-6` (override via setting.extra_config.model_name).
    """
    from app.services.settings_service import get_setting, get_decrypted_credential

    setting = get_setting(session, "anthropic")
    if not setting or not setting.is_configured:
        raise ValueError("Anthropic is not configured. Set up your API key in Settings first.")
    api_key = get_decrypted_credential(session, "anthropic")
    if not api_key:
        raise ValueError("Anthropic API key not found. Please reconfigure in Settings.")
    model = (setting.extra_config or {}).get("model_name", "claude-sonnet-4-6")
    return AnthropicClient(api_key, model)
