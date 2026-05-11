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
) -> MagicMock:
    """Build a mock anthropic Response object."""
    resp = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.cache_creation_input_tokens = cache_creation_input_tokens
    resp.usage.cache_read_input_tokens = cache_read_input_tokens
    resp.usage.output_tokens = output_tokens
    content = MagicMock()
    content.text = text
    resp.content = [content]
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
