"""Grok Text-to-Speech (xAI /v1/tts).

POST JSON {text, voice_id, language}; returns audio (MP3).
Used to read the corrected sentence aloud so the learner hears a good model.
"""
import base64
import logging

import httpx

from ..config import config
from . import ProviderError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


async def speak(text: str, language: str = "en", speed: float | None = None) -> bytes:
    headers = {"Authorization": f"Bearer {config.xai_api_key}"}
    payload = {
        "text": text,
        "voice_id": config.tts_voice,
        "language": language,
        # xAI accepts 0.7-1.5; slower speech is easier for learners to follow.
        "speed": max(0.7, min(1.5, speed if speed is not None else config.tts_speed)),
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.tts_url, headers=headers, json=payload)
    if resp.status_code != 200:
        log.error("TTS error %s: %s", resp.status_code, resp.text[:500])
        raise ProviderError(f"TTS HTTP {resp.status_code}")
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("audio/") or ctype == "application/octet-stream":
        return resp.content
    # JSON envelope fallback (url or base64 payload)
    body = resp.json()
    if isinstance(body.get("audio"), str):
        return base64.b64decode(body["audio"])
    if isinstance(body.get("url"), str):
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            audio_resp = await client.get(body["url"])
        audio_resp.raise_for_status()
        return audio_resp.content
    log.error("TTS unexpected response shape: %s", str(body)[:300])
    raise ProviderError("TTS response shape unexpected")
