"""OpenAI-compatible chat-completions client (defaults to xAI Grok)."""
import json
import logging
import re

import httpx

from ..config import config
from . import ProviderError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Shared client: reuses the TLS connection to api.x.ai across turns.
_client = httpx.AsyncClient(timeout=_TIMEOUT)


async def chat(system: str, user: str, effort: str | None = None) -> str:
    """Return the assistant's raw text for a system+user prompt pair.

    `effort` overrides the reasoning effort per call: live turns stay at the
    fast config default ("none"), while cached one-time generations (scenario
    localization) can afford real reasoning for better output quality."""
    payload = {
        "model": config.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "reasoning_effort": effort or config.llm_reasoning_effort,
    }
    headers = {"Authorization": f"Bearer {config.xai_api_key}"}
    url = f"{config.llm_base_url.rstrip('/')}/chat/completions"
    resp = None
    for attempt in (1, 2):
        try:
            resp = await _client.post(url, json=payload, headers=headers)
        except httpx.TransportError as e:
            # One retry on connection resets/timeouts — common transient blips.
            if attempt == 2:
                raise ProviderError(f"LLM transport error: {e}")
            log.warning("LLM transport error, retrying: %s", e)
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            log.warning("LLM HTTP %s, retrying once", resp.status_code)
            continue
        break
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
