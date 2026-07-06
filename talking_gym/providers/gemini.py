"""Google Gemini chat client — an alternate LLM backend to xAI Grok.

Same `chat(system, user)` contract as providers.llm, so the coach can route a
single turn to either backend based on the learner's model pick. Auth uses the
`x-goog-api-key` header (works with AI Studio keys)."""
import logging

import httpx

from ..config import config
from . import ProviderError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_client = httpx.AsyncClient(timeout=_TIMEOUT)


def enabled() -> bool:
    return bool(config.gemini_api_key)


async def chat(system: str, user: str, effort: str | None = None) -> str:
    """Return Gemini's raw text for a system+user prompt pair.

    `effort` is accepted for signature-parity with providers.llm.chat but has no
    Gemini analogue on the flash tier — ignored."""
    if not config.gemini_api_key:
        raise ProviderError("Gemini not configured (GEMINI_API_KEY missing)")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.4},
    }
    headers = {
        "x-goog-api-key": config.gemini_api_key,
        "Content-Type": "application/json",
    }
    url = (f"{config.gemini_base_url.rstrip('/')}/models/"
           f"{config.gemini_model}:generateContent")
    resp = None
    for attempt in (1, 2):
        try:
            resp = await _client.post(url, json=payload, headers=headers)
        except httpx.TransportError as e:
            if attempt == 2:
                raise ProviderError(f"Gemini transport error: {e}")
            log.warning("Gemini transport error, retrying: %s", e)
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            log.warning("Gemini HTTP %s, retrying once", resp.status_code)
            continue
        break
    if resp.status_code != 200:
        log.error("Gemini error %s: %s", resp.status_code, resp.text[:500])
        raise ProviderError(f"Gemini HTTP {resp.status_code}")
    data = resp.json()
    try:
        cand = data["candidates"][0]
        # A candidate may carry several parts (e.g. a thought part plus the
        # answer); concatenate every part that actually has text.
        parts = cand["content"]["parts"]
        text = "".join(p["text"] for p in parts if "text" in p)
    except (KeyError, IndexError) as e:
        raise ProviderError(f"Gemini response shape unexpected: {e}")
    if not text:
        raise ProviderError("Gemini returned no text (possibly blocked)")
    return text
