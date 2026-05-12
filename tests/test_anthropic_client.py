"""Tests for app/services/anthropic_client.py — v2 SDK wrapper with prompt caching (D-01, D-03, OPS-02).

Critical invariants verified:
- cache_control={"type":"ephemeral","ttl":"1h"} on the system message (Pitfall 4 / OPS-02).
- Per-call LLMUsage row insertion (cost circuit breaker scaffolding D-04).
- Pydantic.model_validate_json parsing — NO Instructor (D-03).
- Markdown-fence stripping for Sonnet's tendency to wrap JSON in ```json ... ```.
- Factory raises ValueError when Anthropic not configured.
- Logger warns when caching not engaged (input below the 2048-token Sonnet 4.6 minimum).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def db_with_phase5(test_engine):
    """Create all Phase 5 tables and yield the engine."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    SQLModel.metadata.drop_all(test_engine)


def _make_mock_response(
    *,
    input_tokens: int = 2500,
    cache_creation_input_tokens: int = 2048,
    cache_read_input_tokens: int = 0,
    output_tokens: int = 120,
    text: str = '{"bar":"hello"}',
    stop_reason: str = "end_turn",
) -> MagicMock:
    """Build a mock anthropic Response object.

    Phase 6.2 Plan 01 Task 1 — block must have ``type="text"`` to be picked up
    by the robust text-block iterator. Mock now uses SimpleNamespace so
    ``b.type == "text"`` compares cleanly (MagicMock equality short-circuits
    on attribute access).
    """
    from types import SimpleNamespace
    resp = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.cache_creation_input_tokens = cache_creation_input_tokens
    resp.usage.cache_read_input_tokens = cache_read_input_tokens
    resp.usage.output_tokens = output_tokens
    resp.stop_reason = stop_reason
    resp.content = [SimpleNamespace(type="text", text=text)]
    return resp


def _make_thinking_response(
    *,
    thinking_text: str = "Let me think...",
    text: str = '{"bar":"hello"}',
    input_tokens: int = 2500,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 2048,
    output_tokens: int = 200,
    stop_reason: str = "end_turn",
    include_text_block: bool = True,
) -> MagicMock:
    """Build a mock Anthropic Response with a ThinkingBlock-first content list.

    Phase 6.2 Plan 01 Task 1 — exercises the robust extractor against the
    shape Anthropic returns when extended thinking is enabled:
    ``[ThinkingBlock(type="thinking", thinking=..., signature=...),
       TextBlock(type="text", text=...)]``.
    """
    from types import SimpleNamespace
    resp = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.cache_creation_input_tokens = cache_creation_input_tokens
    resp.usage.cache_read_input_tokens = cache_read_input_tokens
    resp.usage.output_tokens = output_tokens
    resp.stop_reason = stop_reason
    blocks = [
        SimpleNamespace(
            type="thinking",
            thinking=thinking_text,
            signature="WaUjzkypQ2mU",
        )
    ]
    if include_text_block:
        blocks.append(SimpleNamespace(type="text", text=text))
    resp.content = blocks
    return resp


@pytest.mark.asyncio
class TestAnthropicClient:
    """Mock-SDK tests for AnthropicClient.call_with_structured_output."""

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_explicit_ttl_1h(self, mock_anthropic_cls, db_with_phase5):
        """OPS-02 / Pitfall 4 — system message MUST carry cache_control ttl=1h, NOT the 5m default."""
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_make_mock_response())
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        result = await client.call_with_structured_output(
            system_prompt="x" * 5000,  # padded above 2048 token cache breakpoint
            user_prompt="hi",
            response_model=Foo,
            purpose="test",
        )
        # The CRITICAL invariant assertion (the entire reason this client exists).
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"][0]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }
        assert call_kwargs["system"][0]["type"] == "text"
        assert call_kwargs["system"][0]["text"] == "x" * 5000
        assert result == Foo(bar="hello")

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_logs_usage_to_llmusage_table(self, mock_anthropic_cls, db_with_phase5):
        """Per-call LLMUsage row inserted with all 4 token counts + cost_estimate_usd > 0 (D-04)."""
        from app.models.llm_usage import LLMUsage
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(
                input_tokens=2500,
                cache_creation_input_tokens=2048,
                cache_read_input_tokens=0,
                output_tokens=120,
            )
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="taste_profile_summary",
        )

        with Session(db_with_phase5) as session:
            rows = session.exec(select(LLMUsage)).all()
            assert len(rows) == 1, f"Expected 1 LLMUsage row, got {len(rows)}"
            row = rows[0]
            assert row.purpose == "taste_profile_summary"
            assert row.model == "claude-sonnet-4-6"
            assert row.input_tokens == 2500
            assert row.cache_creation_input_tokens == 2048
            assert row.cache_read_input_tokens == 0
            assert row.output_tokens == 120
            assert row.cost_estimate_usd > 0
            # called_at must be ISO-parseable
            datetime.fromisoformat(row.called_at)

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_warns_when_caching_not_engaged(
        self, mock_anthropic_cls, db_with_phase5, caplog
    ):
        """When response shows cache_creation=0 AND cache_read=0, logger.warning fires.

        This catches the silent regression where the system prompt fell below the
        2048-token Sonnet 4.6 minimum — caching disabled but billed at full input rate.
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(
                input_tokens=500,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        with caplog.at_level(logging.WARNING, logger="app.services.anthropic_client"):
            await client.call_with_structured_output(
                system_prompt="short prompt",
                user_prompt="hi",
                response_model=Foo,
                purpose="vibe_naming",
            )
        # Find a warning record about caching
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        joined = " ".join(r.getMessage() for r in warns)
        assert "caching not engaged" in joined.lower()
        assert "vibe_naming" in joined

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_parses_pydantic_response(self, mock_anthropic_cls, db_with_phase5):
        """response.content[0].text is parsed via response_model.model_validate_json — no Instructor (D-03)."""
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(text='{"bar":"hello"}')
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        result = await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="test",
        )
        assert isinstance(result, Foo)
        assert result.bar == "hello"

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_strips_markdown_fences(self, mock_anthropic_cls, db_with_phase5):
        """When the model wraps JSON in ```json ... ``` fences, we strip and parse."""
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(text='```json\n{"bar":"hi"}\n```')
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        result = await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="test",
        )
        assert result == Foo(bar="hi")

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_tolerates_trailing_prose_after_json(self, mock_anthropic_cls, db_with_phase5):
        """Production bug 2026-05-10: Sonnet emits valid JSON followed by commentary.

        Pydantic.model_validate_json rejects with 'Invalid JSON: trailing characters'.
        The parser must tolerate trailing prose via json.JSONDecoder.raw_decode.
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(
                text='{"proposals": [{"cluster_index": 0, "name": "Workout", "fit": "strong", "fit_reason": null}]}\n\nNote: cluster 0 is a stretch.'
            )
        )
        mock_anthropic_cls.return_value = mock_client

        class _Resp(BaseModel):
            proposals: list

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        result = await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=_Resp,
            purpose="test",
        )
        assert isinstance(result, _Resp)
        assert len(result.proposals) == 1
        assert result.proposals[0]["name"] == "Workout"

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_tolerates_prose_before_and_after_json(self, mock_anthropic_cls, db_with_phase5):
        """The parser must also handle the 'Here is the JSON: {...} Let me know...' case."""
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(
                text='Here is the JSON:\n{"proposals": []}\nLet me know if you need changes.'
            )
        )
        mock_anthropic_cls.return_value = mock_client

        class _Resp(BaseModel):
            proposals: list

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        result = await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=_Resp,
            purpose="test",
        )
        assert isinstance(result, _Resp)
        assert result.proposals == []

    async def test_factory_raises_when_not_configured(self, db_with_phase5):
        """get_anthropic_client_v2 raises ValueError if Anthropic isn't configured."""
        from app.services.anthropic_client import get_anthropic_client_v2

        with Session(db_with_phase5) as session:
            with pytest.raises(ValueError) as exc_info:
                get_anthropic_client_v2(session)
            assert "not configured" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Phase 6.2 Plan 01 Task 1 — adaptive-thinking + robust text-block extraction.
#
# The two changes ship in one commit because they are coupled: enabling
# ``thinking="adaptive"`` makes ``response.content[0]`` a ThinkingBlock, which
# the legacy ``response.content[0].text`` access would crash on. The seven
# tests below pin both halves.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAnthropicThinkingAndRobustExtraction:
    """Phase 6.2 Plan 01 Task 1 — thinking + iterate-for-text-block."""

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_anthropic_client_thinking_default_sends_disabled(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """Hotfix 260512-k3n: Default thinking='off' sends thinking={'type':'disabled'}.

        Previously the kwarg was omitted entirely. The hotfix sends an
        explicit disabled shape so the BadRequestError retry path has a
        known-good fallback. RESEARCH §2.2's byte-identical-when-off
        constraint was softened in the hotfix because the production
        BadRequestError on Sonnet 4.6 forces the request body shape change.
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_make_mock_response())
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="taste_profile_summary",
        )
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs.get("thinking") == {"type": "disabled"}, (
            f"Default thinking='off' must send thinking={{'type':'disabled'}}; "
            f"got thinking={kwargs.get('thinking')}"
        )

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_anthropic_client_thinking_adaptive_param_shape(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """Hotfix 260512-k3n: thinking='adaptive' translates to the documented
        Anthropic API shape {'type':'enabled','budget_tokens':2000}.

        The previous literal {'type':'adaptive'} was rejected by the API
        with 'adaptive thinking is not supported on this model'.
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_thinking_response()
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="vibe_assign_pass2",
            thinking="adaptive",
        )
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs.get("thinking") == {
            "type": "enabled",
            "budget_tokens": 2000,
        }, (
            f"Expected thinking={{'type':'enabled','budget_tokens':2000}}; "
            f"got thinking={kwargs.get('thinking')}"
        )

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_anthropic_client_thinking_off_param_shape(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """Hotfix 260512-k3n: Explicit thinking='off' sends {'type':'disabled'}.

        Same shape as the default-off case; confirms explicit and default
        callers produce identical request bodies.
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_make_mock_response())
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="vibe_assign_pass1",
            thinking="off",
        )
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs.get("thinking") == {"type": "disabled"}, (
            f"thinking='off' must send thinking={{'type':'disabled'}}; "
            f"got thinking={kwargs.get('thinking')}"
        )

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_anthropic_client_extracts_text_from_thinking_response(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """ThinkingBlock-first content list → robust extractor still finds the text block.

        This is the regression that ``response.content[0].text`` would hit:
        AttributeError on ThinkingBlock. Iterate instead.
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_thinking_response(
                text='{"definitions": []}'
            )
        )
        mock_anthropic_cls.return_value = mock_client

        class _Defs(BaseModel):
            definitions: list

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        result = await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="define them",
            response_model=_Defs,
            purpose="vibe_definitions_preamble",
            thinking="adaptive",
        )
        assert isinstance(result, _Defs)
        assert result.definitions == []

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_anthropic_client_extracts_text_from_text_only_response(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """Backward-compat: thinking-off responses (text-only blocks) still parse."""
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(text='{"definitions":[]}')
        )
        mock_anthropic_cls.return_value = mock_client

        class _Defs(BaseModel):
            definitions: list

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        result = await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=_Defs,
            purpose="test",
        )
        assert isinstance(result, _Defs)
        assert result.definitions == []

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_anthropic_client_raises_clear_error_when_no_text_block(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """No text block in response → ValueError that names 'text' + block types.

        Helps debug stop_reason='max_tokens' truncation (Pitfall E).
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_thinking_response(
                text="", include_text_block=False
            )
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        with pytest.raises(ValueError) as exc_info:
            await client.call_with_structured_output(
                system_prompt="x" * 5000,
                user_prompt="hi",
                response_model=Foo,
                purpose="vibe_assign_pass2",
                thinking="adaptive",
            )
        msg = str(exc_info.value)
        assert "text" in msg.lower(), msg
        # The block types list should appear for diagnostic ease.
        assert "thinking" in msg, msg
        # The purpose should be in the message for cost attribution.
        assert "vibe_assign_pass2" in msg, msg

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_anthropic_client_logs_stop_reason_when_max_tokens(
        self, mock_anthropic_cls, db_with_phase5, caplog
    ):
        """stop_reason='max_tokens' → WARNING log entry mentions max_tokens + purpose.

        Pre-emptive for T6 / Pitfall E. If real Pass 2 calls hit this we'll
        see it surfaced in /debug/vibes via the LLM cost panel.
        """
        import logging
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response(stop_reason="max_tokens")
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        with caplog.at_level(logging.WARNING, logger="app.services.anthropic_client"):
            await client.call_with_structured_output(
                system_prompt="x" * 5000,
                user_prompt="hi",
                response_model=Foo,
                purpose="vibe_assign_pass2",
            )
        joined = " ".join(
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        )
        assert "max_tokens" in joined.lower(), joined
        assert "vibe_assign_pass2" in joined, joined

    # ------------------------------------------------------------------
    # Hotfix 260512-k3n — three regression tests for the adaptive-thinking
    # translation + one-shot BadRequestError retry fallback.
    # ------------------------------------------------------------------

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_adaptive_translates_to_enabled_dict(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """Hotfix 260512-k3n (a): thinking='adaptive' MUST translate to
        ``{'type':'enabled','budget_tokens':2000}`` — the documented Anthropic
        API shape. The previous ``{'type':'adaptive'}`` literal AND any
        string-literal ``'adaptive'`` are both rejected by the API.
        """
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_thinking_response()
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="vibe_assign_pass2",
            thinking="adaptive",
        )
        thinking_kwarg = mock_client.messages.create.call_args.kwargs.get(
            "thinking"
        )
        assert thinking_kwarg == {"type": "enabled", "budget_tokens": 2000}, (
            f"Expected {{'type':'enabled','budget_tokens':2000}}; "
            f"got {thinking_kwarg!r}"
        )
        # Explicitly confirm the bug shape — string literal — is gone.
        assert thinking_kwarg != "adaptive"

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_retries_once_when_model_rejects_thinking(
        self, mock_anthropic_cls, db_with_phase5, caplog
    ):
        """Hotfix 260512-k3n (b): On BadRequestError mentioning 'thinking',
        retry ONCE with thinking={'type':'disabled'} and log a WARNING.

        Mirrors the production traceback path where Anthropic returns
        ``BadRequestError: 400 — 'adaptive thinking is not supported on this
        model'`` for certain Sonnet/Opus revisions.
        """
        from anthropic import BadRequestError
        from app.services.anthropic_client import AnthropicClient

        # Construct a BadRequestError without going through the full
        # APIStatusError protocol — set .message directly so str(exc) works.
        bad_request_exc = BadRequestError.__new__(BadRequestError)
        bad_request_exc.message = (
            "adaptive thinking is not supported on this model"
        )
        # Also set the default Python Exception args so str(exc) returns the
        # message (anthropic SDK overrides __str__ on its base exception, so
        # both paths are covered).
        Exception.__init__(
            bad_request_exc,
            "adaptive thinking is not supported on this model",
        )

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[bad_request_exc, _make_mock_response()]
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        with caplog.at_level(
            logging.WARNING, logger="app.services.anthropic_client"
        ):
            result = await client.call_with_structured_output(
                system_prompt="x" * 5000,
                user_prompt="hi",
                response_model=Foo,
                purpose="vibe_assign_pass2",
                thinking="adaptive",
            )

        # Two calls made — the original (with enabled) plus the retry.
        assert mock_client.messages.create.await_count == 2, (
            f"Expected exactly 2 create() calls (original + 1 retry); "
            f"got {mock_client.messages.create.await_count}"
        )
        # The SECOND call must have flipped thinking to disabled.
        second_call_kwargs = (
            mock_client.messages.create.call_args_list[1].kwargs
        )
        assert second_call_kwargs.get("thinking") == {"type": "disabled"}, (
            f"Retry call must use thinking={{'type':'disabled'}}; "
            f"got {second_call_kwargs.get('thinking')!r}"
        )
        # The result must parse cleanly into the Pydantic model.
        assert isinstance(result, Foo)
        assert result.bar == "hello"
        # A WARNING about extended thinking must have been logged.
        joined = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
        assert "extended thinking" in joined.lower(), joined

    @patch("app.services.anthropic_client.AsyncAnthropic")
    async def test_off_sends_disabled_dict(
        self, mock_anthropic_cls, db_with_phase5
    ):
        """Hotfix 260512-k3n (c): thinking='off' explicitly sends
        ``thinking={'type':'disabled'}`` in the request body."""
        from app.services.anthropic_client import AnthropicClient

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_response()
        )
        mock_anthropic_cls.return_value = mock_client

        class Foo(BaseModel):
            bar: str

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
        await client.call_with_structured_output(
            system_prompt="x" * 5000,
            user_prompt="hi",
            response_model=Foo,
            purpose="vibe_assign_pass1",
            thinking="off",
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs.get("thinking") == {"type": "disabled"}, (
            f"thinking='off' must send {{'type':'disabled'}}; "
            f"got {call_kwargs.get('thinking')!r}"
        )


def test_response_content_indexed_text_access_eradicated():
    """T-062-01 mitigation: the fragile ``response.content[0].text`` pattern
    is eradicated from anthropic_client.py.

    Companion to the seven tests above. The regex matches the exact fragile
    access pattern that breaks the moment a ThinkingBlock lands at index 0.
    """
    from pathlib import Path

    src = (
        Path(__file__).parent.parent
        / "app" / "services" / "anthropic_client.py"
    ).read_text()
    assert "response.content[0]" not in src, (
        "T-062-01 regression: response.content[0] access reintroduced in "
        "anthropic_client.py — this WILL AttributeError the moment thinking "
        "is enabled. Use a robust iterator over response.content for "
        "type=='text' instead."
    )
