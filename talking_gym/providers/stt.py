"""Grok Speech-to-Text (xAI /v1/stt).

Batch REST endpoint: POST multipart with model + audio file.
Priced ~$0.10 per audio hour (Apr 2026) — the cost reason this provider is default.
Telegram voice notes are OGG/Opus, which the API accepts among its supported formats.
"""
import logging

import httpx

from ..config import config
from . import ProviderError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_client = httpx.AsyncClient(timeout=_TIMEOUT)


async def transcribe(audio: bytes, filename: str = "voice.ogg", mime: str = "audio/ogg") -> str:
    headers = {"Authorization": f"Bearer {config.xai_api_key}"}
    files = {"file": (filename, audio, mime)}
    data = {
        "model": config.stt_model,
        "format": "json",
        "language": config.stt_language,
    }
    resp = await _client.post(config.stt_url, headers=headers, data=data, files=files)
    if resp.status_code != 200:
        log.error("STT error %s: %s", resp.status_code, resp.text[:500])
        raise ProviderError(f"STT HTTP {resp.status_code}")
    body = resp.json()
    # Be tolerant about the response key naming.
    for key in ("text", "transcript", "transcription"):
        if isinstance(body.get(key), str):
            return body[key].strip()
    # Some APIs nest segments
    if isinstance(body.get("segments"), list):
        return " ".join(s.get("text", "") for s in body["segments"]).strip()
    log.error("STT unexpected response shape: %s", str(body)[:500])
    raise ProviderError("STT response shape unexpected")
