"""OpenAI chat client — GPT-5.6 Luna as an alternate live-turn backend.

Same chat(system, user) contract as providers.llm / providers.gemini so the
coach can route per-turn. Self-heals on parameter rejections (newer OpenAI
models are picky about temperature/reasoning params)."""
import logging

import httpx

from ..config import config
from . import ProviderError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_client = httpx.AsyncClient(timeout=_TIMEOUT)


def enabled() -> bool:
    return bool(config.openai_api_key)


async def chat(system: str, user: str, effort: str | None = None) -> str:
    """Return the assistant's raw text. `effort` accepted for signature parity;
    Luna is the fast tier and runs without a reasoning knob."""
    if not config.openai_api_key:
        raise ProviderError("OpenAI not configured (OPENAI_API_KEY missing)")
    payload = {
        "model": config.openai_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
    }
    headers = {"Authorization": f"Bearer {config.openai_api_key}"}
    url = f"{config.openai_base_url.rstrip('/')}/chat/completions"
    resp = None
    for attempt in (1, 2, 3):
        try:
            resp = await _client.post(url, json=payload, headers=headers)
        except httpx.TransportError as e:
            if attempt == 3:
                raise ProviderError(f"OpenAI transport error: {e}")
            log.warning("OpenAI transport error, retrying: %s", e)
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            log.warning("OpenAI HTTP %s, retrying once", resp.status_code)
            continue
        if resp.status_code == 400 and "temperature" in payload and \
                "temperature" in resp.text.lower():
            # Some tiers pin temperature — drop it and retry.
            log.warning("OpenAI rejected temperature for %s; retrying without",
                        payload["model"])
            payload.pop("temperature")
            continue
        break
    if resp.status_code != 200:
        log.error("OpenAI error %s: %s", resp.status_code, resp.text[:500])
        raise ProviderError(f"OpenAI HTTP {resp.status_code}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"OpenAI response shape unexpected: {e}")
