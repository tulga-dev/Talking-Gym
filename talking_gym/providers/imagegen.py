"""Vocabulary illustrations via Google Gemini image models (Nano Banana).

One image per word, generated on first request and cached in the DB, then
shared by every learner. A fixed art-direction prompt keeps the whole set in
one consistent, on-brand style."""
import base64
import logging

import httpx

from ..config import config
from . import ProviderError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
_client = httpx.AsyncClient(timeout=_TIMEOUT)

# One art direction for the whole word set — warm, flat, matches the app.
_STYLE = (
    "Clean, friendly flat vector illustration for a children's language-learning "
    "app. A single subject, centered, simple rounded shapes, soft warm coral "
    "(#FFEDE6) background, gentle shadows, cheerful. No text, no letters, no words "
    "in the image."
)


def enabled() -> bool:
    return bool(config.gemini_api_key)


async def generate(word: str, hint: str = "", concrete: bool = True) -> tuple[str, bytes]:
    """Return (mime, image_bytes) illustrating `word`. `hint` is an example
    sentence giving the model context. `concrete=False` (abstract words like
    "plan", "please") asks for a symbolic scene instead of a literal object."""
    if not config.gemini_api_key:
        raise ProviderError("image gen not configured (GEMINI_API_KEY missing)")
    subject = f'the word "{word}"'
    if hint:
        subject += f' (as in: "{hint}")'
    if concrete:
        instruction = f"Illustrate {subject}."
    else:
        instruction = (
            f"Show one simple, clear picture that captures the idea of {subject} — "
            f"a symbolic object or a small everyday scene a learner instantly connects "
            f"to the concept (for example, a calendar or checklist for 'plan')."
        )
    payload = {
        "contents": [{"role": "user", "parts": [
            {"text": f"{_STYLE} {instruction}"}
        ]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    headers = {"x-goog-api-key": config.gemini_api_key, "Content-Type": "application/json"}
    url = (f"{config.gemini_base_url.rstrip('/')}/models/"
           f"{config.gemini_image_model}:generateContent")
    try:
        resp = await _client.post(url, json=payload, headers=headers)
    except httpx.TransportError as e:
        raise ProviderError(f"image gen transport error: {e}")
    if resp.status_code != 200:
        log.error("image gen error %s: %s", resp.status_code, resp.text[:400])
        raise ProviderError(f"image gen HTTP {resp.status_code}")
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"image gen response shape unexpected: {e}")
    for p in parts:
        blob = p.get("inlineData") or p.get("inline_data")
        if blob and blob.get("data"):
            mime = blob.get("mimeType") or blob.get("mime_type") or "image/png"
            return mime, base64.b64decode(blob["data"])
    raise ProviderError("image gen returned no image (possibly blocked)")
