from __future__ import annotations

import asyncio
import logging

from openai import OpenAI

logger = logging.getLogger(__name__)


async def test_ollama_connection(url: str) -> dict:
    """Test Ollama connectivity and return available models."""
    try:
        # Strip trailing slash if present
        url = url.rstrip("/")
        logger.info("Testing Ollama connection at %s/v1", url)
        client = OpenAI(base_url=f"{url}/v1", api_key="ollama")
        models_response = await asyncio.to_thread(client.models.list)
        models = [m.id for m in (models_response.data or [])]
        if not models:
            logger.info("Ollama connected but no models found — user needs to pull a model")
            return {
                "success": True,
                "models": [],
                "warning": "Connected, but no models found. Pull a model first: docker exec composer-ollama ollama pull llama3.1:8b",
            }
        logger.info("Ollama connection successful, found %d models: %s", len(models), models)
        return {"success": True, "models": models}
    except Exception as e:
        error_msg = str(e)
        logger.error("Ollama connection failed: %s", error_msg)
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            return {
                "success": False,
                "error": "Connection timed out. Make sure the service is running and accessible from this container.",
            }
        if "connection" in error_msg.lower() or "refused" in error_msg.lower():
            return {
                "success": False,
                "error": f"Connection refused at {url}. Make sure Ollama is running and the URL is correct.",
            }
        return {
            "success": False,
            "error": f"Could not connect: {error_msg[:200]}",
        }
