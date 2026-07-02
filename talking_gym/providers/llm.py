"""OpenAI-compatible chat-completions client (defaults to xAI Grok)."""
import json
import logging
import re

import httpx

from ..config import config
from . import ProviderError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


async def chat(system: str, user: str) -> str:
    """Return the assistant's raw text for a system+user prompt pair."""
    payload = {
        "model": config.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
    }
    headers = {"Authorization": f"Bearer {config.xai_api_key}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{config.llm_base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
        )
    if resp.status_code != 200:
        log.error("LLM error %s: %s", resp.status_code, resp.text[:500])
        raise ProviderError(f"LLM HTTP {resp.status_code}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"LLM response shape unexpected: {e}")


def parse_json_block(text: str) -> dict:
    """Extract the first JSON object from an LLM reply (tolerates code fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise ProviderError("LLM did not return valid JSON")
