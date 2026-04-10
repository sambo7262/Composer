"""LLM client: Anthropic Claude API for playlist generation.

Replaces Ollama for AI inference — Ollama's local LLM is too CPU-intensive
for NAS hardware (J4125 Celeron). Anthropic Claude Haiku provides fast,
cheap cloud inference (~$0.01/month at typical usage).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.services.settings_service import get_setting, get_decrypted_credential

logger = logging.getLogger(__name__)


async def test_anthropic_connection(api_key: str) -> dict:
    """Test Anthropic API connectivity by listing available models."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                models = [m["id"] for m in data.get("data", [])]
                # Filter to haiku/sonnet models (most relevant for this use case)
                relevant = [m for m in models if "haiku" in m or "sonnet" in m]
                if not relevant:
                    relevant = models[:5]
                logger.info("Anthropic connection successful, found %d models", len(relevant))
                return {"success": True, "models": relevant}
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API key. Check your Anthropic API key."}
            else:
                return {"success": False, "error": f"API returned status {response.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "error": "Connection timed out. Check your internet connection."}
    except Exception as e:
        logger.error("Anthropic connection failed: %s", str(e))
        return {"success": False, "error": f"Could not connect: {str(e)[:200]}"}


def get_anthropic_client(db_session) -> tuple:
    """Get Anthropic API key and model name from settings.

    Returns:
        Tuple of (api_key, model_name).

    Raises:
        ValueError: If Anthropic is not configured.
    """
    setting = get_setting(db_session, "anthropic")
    if setting is None or not setting.is_configured:
        raise ValueError("Anthropic is not configured. Set up your API key in Settings first.")

    api_key = get_decrypted_credential(db_session, "anthropic")
    if not api_key:
        raise ValueError("Anthropic API key not found. Please reconfigure in Settings.")

    model_name = "claude-3-5-haiku-latest"
    if setting.extra_config and setting.extra_config.get("model_name"):
        model_name = setting.extra_config["model_name"]

    return api_key, model_name


async def chat_completion(
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 300,
    temperature: float = 0.7,
) -> str:
    """Call Anthropic Messages API and return the text response.

    Uses httpx directly (no SDK dependency) for minimal footprint.
    """
    # Convert OpenAI-style messages to Anthropic format
    anthropic_messages = []
    for msg in messages:
        if msg["role"] in ("user", "assistant"):
            anthropic_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": anthropic_messages,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]
