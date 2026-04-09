from __future__ import annotations

import asyncio

from openai import OpenAI


async def test_ollama_connection(url: str) -> dict:
    """Test Ollama connectivity and return available models."""
    try:
        client = OpenAI(base_url=f"{url}/v1", api_key="ollama")
        models_response = await asyncio.to_thread(client.models.list)
        models = [m.id for m in models_response.data]
        return {"success": True, "models": models}
    except Exception as e:
        error_msg = str(e)
        if "timeout" in error_msg.lower():
            return {
                "success": False,
                "error": "Connection timed out. Make sure the service is running and accessible from this container.",
            }
        return {
            "success": False,
            "error": "Could not connect. Check the URL and credentials, then try again.",
        }
