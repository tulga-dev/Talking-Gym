"""Transactional email via Resend (https://resend.com).

Enabled only when RESEND_API_KEY is set. Free tier is generous; the only
setup is a Resend account + a verified sender domain (or resend.dev in test).
Used for password-reset links; the app degrades gracefully when unconfigured.
"""
import logging
import os

import httpx

log = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESET_FROM = os.getenv("RESET_FROM_EMAIL", "Talking Gym <onboarding@resend.dev>")
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def enabled() -> bool:
    return bool(RESEND_API_KEY)


async def send_reset(to_email: str, link: str) -> bool:
    if not RESEND_API_KEY:
        return False
    html = (
        f'<div style="font-family:sans-serif;max-width:440px;margin:auto">'
        f'<h2 style="color:#33A62C">Talking Gym</h2>'
        f'<p>Нууц үгээ шинэчлэх хүсэлт ирлээ. Доорх товчийг дарж шинэ нууц үг тавина уу '
        f'(1 цагийн дараа хүчингүй болно):</p>'
        f'<p><a href="{link}" style="background:#33A62C;color:#fff;padding:12px 22px;'
        f'border-radius:60px;text-decoration:none;font-weight:700">Нууц үг шинэчлэх</a></p>'
        f'<p style="color:#5E6B7A;font-size:13px">Хэрэв та хүсээгүй бол энэ имэйлийг үл ойшоо.</p>'
        f'</div>'
    )
    payload = {"from": RESET_FROM, "to": [to_email],
               "subject": "Talking Gym — нууц үг шинэчлэх", "html": html}
    headers = {"Authorization": f"Bearer {RESEND_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post("https://api.resend.com/emails",
                                     json=payload, headers=headers)
        if resp.status_code >= 300:
            log.error("Resend error %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception:
        log.exception("Resend send failed")
        return False
