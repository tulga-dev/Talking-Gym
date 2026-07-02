"""Facebook Messenger channel adapter.

Messenger pushes events to a webhook, so this module runs a small aiohttp
server (alongside the Telegram poller in the same process):

    GET  /                       -> health check
    GET  /webhooks/messenger     -> Meta webhook verification handshake
    POST /webhooks/messenger     -> messaging events (signature-verified)

Messenger has no rich text, so coach HTML is flattened to plain text.
Navigation uses the persistent menu (postbacks) and quick replies.
"""
import asyncio
import hashlib
import hmac
import html as html_mod
import json
import logging
import re

import httpx
from aiohttp import web

from .. import coach, db
from ..config import config
from ..providers import ProviderError
from ..providers import stt, tts

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

LEVEL_OPTIONS = [
    ("🌱 Анхан (A1–A2)", "LEVEL:beginner"),
    ("🌿 Дунд (B1)", "LEVEL:intermediate"),
    ("🌳 Ахисан (B2+)", "LEVEL:advanced"),
]
REMIND_OPTIONS = [
    ("🌅 08:00", "REMINDH:8"),
    ("🌞 13:00", "REMINDH:13"),
    ("🌆 19:00", "REMINDH:19"),
    ("🌙 21:00", "REMINDH:21"),
    ("🔕 Хэрэггүй", "REMINDH:off"),
]
NEXT_OPTIONS = [("🔁 Дахин дасгал", "TODAY"), ("🔥 Миний ахиц", "PROGRESS")]

WELCOME = (
    "Сайн байна уу! 👋\n\n"
    f"Би {config.coach_name_mn} — таны хувийн англи хэлний дасгалжуулагч. 🏋️\n"
    "Өдөр бүр 5 минутын ярианы дасгал хийж, англиар ЯРИХ чадвараа хөгжүүлье.\n\n"
    "1️⃣ Би нөхцөл байдал + жишээ хариулт өгнө\n"
    "2️⃣ Та 🎤 дуут мессежээр англиар хариулна\n"
    "3️⃣ Би засвар, зөвлөгөө, оноо, XP өгнө\n\n"
    "Эхлээд түвшнээ сонгоно уу: 👇"
)

QUICK_TIPS = (
    "📋 Товч заавар:\n"
    "1️⃣ Би нөхцөл байдал + 📝 жишээ хариулт өгнө\n"
    "2️⃣ Жишээг уншаад (өөрийнхөөрөө өөрчилж болно) 🎤 дуут мессежээр хэлнэ\n"
    "3️⃣ 3 солилцоод дасгал дуусна\n\n"
    "Юу хэлэхээ мэдэхгүй бол жишээг шууд уншаад л болно! 😊"
)

GUIDE = (
    "📖 Хэрэглэх заавар\n\n"
    "🏋️ Доод цэснээс «Өнөөдрийн дасгал» гэснийг дарна.\n"
    "🎤 Хариугаа дуут мессежээр (10–30 сек) илгээнэ — бичиж ч болно.\n"
    "✅ Би зөв хувилбар, 💡 зөвлөгөө (монголоор), оноо, XP өгнө.\n"
    "🔥 Өдөр бүр хийвэл стрик өснө, XP-ээр зэрэглэл ахина.\n\n"
    "Санал хүсэлт байвал 'санал: ...' гэж бичээд илгээгээрэй. 🙏"
)


def html_to_plain(text: str) -> str:
    """Flatten coach HTML for Messenger (no rich text support)."""
    text = re.sub(r"</?blockquote>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------- Send API ----------

def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{config.graph_api_version}/{path}"


async def _send(payload: dict) -> None:
    params = {"access_token": config.messenger_page_token}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_graph_url("me/messages"), params=params, json=payload)
    if resp.status_code != 200:
        log.error("Messenger send error %s: %s", resp.status_code, resp.text[:400])


async def send_text(psid: str, text: str) -> None:
    text = text.strip()
    while text:
        chunk, text = text[:1900], text[1900:]
        await _send({
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"text": chunk},
        })


async def send_quick_replies(psid: str, text: str, options: list[tuple[str, str]]) -> None:
    await _send({
        "recipient": {"id": psid},
        "messaging_type": "RESPONSE",
        "message": {
            "text": text[:1900],
            "quick_replies": [
                {"content_type": "text", "title": title[:20], "payload": payload}
                for title, payload in options
            ],
        },
    })


async def send_audio(psid: str, audio: bytes) -> None:
    """Upload MP3 bytes as an audio attachment."""
    params = {"access_token": config.messenger_page_token}
    data = {
        "recipient": json.dumps({"id": psid}),
        "messaging_type": "RESPONSE",
        "message": json.dumps({"attachment": {"type": "audio", "payload": {"is_reusable": False}}}),
    }
    files = {"filedata": ("coach.mp3", audio, "audio/mpeg")}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        resp = await client.post(_graph_url("me/messages"), params=params, data=data, files=files)
    if resp.status_code != 200:
        log.error("Messenger audio send error %s: %s", resp.status_code, resp.text[:400])


async def setup_profile() -> None:
    """Get-started button + persistent menu (the Messenger 'command menu')."""
    payload = {
        "get_started": {"payload": "GET_STARTED"},
        "greeting": [{
            "locale": "default",
            "text": "Өдөрт 5 минут — англиар ярьж сур! 🏋️🎤",
        }],
        "persistent_menu": [{
            "locale": "default",
            "composer_input_disabled": False,
            "call_to_actions": [
                {"type": "postback", "title": "🏋️ Өнөөдрийн дасгал", "payload": "TODAY"},
                {"type": "postback", "title": "🔥 Миний ахиц", "payload": "PROGRESS"},
                {"type": "postback", "title": "❓ Тусламж", "payload": "HELP"},
            ],
        }],
    }
    params = {"access_token": config.messenger_page_token}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_graph_url("me/messenger_profile"), params=params, json=payload)
    if resp.status_code != 200:
        log.warning("Messenger profile setup failed %s: %s", resp.status_code, resp.text[:300])


async def send_reminder(user) -> None:
    """Daily nudge (called from the shared reminder job). Must be within
    Meta's 24h standard-messaging window; Graph rejects it otherwise and we
    just log that."""
    await send_quick_replies(
        str(user["chat_id"]),
        f"🏋️ Англи хэлний дасгалын цаг боллоо!\nСтрикээ хадгалъя — 🔥 {user['streak']} өдөр.",
        [("🏋️ Эхлэх", "TODAY")],
    )


# ---------- event handling ----------

async def _run_turn(psid: str, transcript: str, spoken: bool) -> None:
    user_id = int(psid)
    try:
        reply = await coach.handle_turn(user_id, transcript)
    except ProviderError:
        await send_text(psid, "Түр зуурын алдаа гарлаа. 🙏 Хэдэн секундын дараа дахин оролдоорой.")
        return

    await send_text(psid, html_to_plain(coach.format_reply(reply, transcript)))

    if config.tts_enabled and spoken:
        speak_parts = []
        if reply.corrected:
            speak_parts.append(f"Listen and repeat: {reply.corrected}")
        if reply.reply_en:
            speak_parts.append(reply.reply_en if reply.done else f"Now, my question: {reply.reply_en}")
        if speak_parts:
            try:
                audio = await tts.speak(". ".join(speak_parts)[:500])
                await send_text(psid, "🔊 Сонсоод давтаарай:")
                await send_audio(psid, audio)
            except ProviderError:
                log.warning("Messenger TTS failed; text-only reply sent")

    if reply.done:
        await send_quick_replies(psid, "Дараагийн алхам? 👇", NEXT_OPTIONS)


async def _start_today(psid: str, user) -> None:
    if user["sessions_done"] == 0 and db.get_active_session(user["user_id"]) is None:
        await send_quick_replies(psid, WELCOME, LEVEL_OPTIONS)
        return
    intro = coach.start_daily_session(user["user_id"])
    await send_text(psid, html_to_plain(intro.text_mn))


async def _handle_payload(psid: str, user, payload: str) -> None:
    if payload == "GET_STARTED":
        await send_quick_replies(psid, WELCOME, LEVEL_OPTIONS)
    elif payload.startswith("LEVEL:"):
        level = payload.split(":", 1)[1]
        db.set_level(user["user_id"], level)
        await send_quick_replies(psid, "Түвшин тохирлоо ✅\n\n⏰ Өдөр бүр хэдэн цагт сануулах вэ?", REMIND_OPTIONS)
    elif payload.startswith("REMINDH:"):
        choice = payload.split(":", 1)[1]
        db.set_reminder_hour(user["user_id"], None if choice == "off" else int(choice))
        confirm = "🔕 За, сануулгагүй." if choice == "off" else f"⏰ Болно! Өдөр бүр {int(choice):02d}:00 цагт сануулна."
        await send_text(psid, confirm + "\n\n" + QUICK_TIPS)
        intro = coach.start_daily_session(user["user_id"])
        await send_text(psid, html_to_plain(intro.text_mn))
    elif payload == "TODAY":
        await _start_today(psid, user)
    elif payload == "PROGRESS":
        await send_text(psid, html_to_plain(coach.progress_card(db.get_user(user["user_id"]))))
    elif payload == "HELP":
        await send_text(psid, GUIDE)
    else:
        log.info("Unknown Messenger payload: %s", payload)


async def _handle_audio(psid: str, user, url: str) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        await send_text(psid, "Дуут мессежийг татаж чадсангүй. 🙏 Дахин илгээгээрэй?")
        return
    audio = resp.content
    # Messenger doesn't report duration; estimate from size (~4 KB/s AAC).
    est_seconds = max(1, len(audio) // 4000)
    if db.voice_seconds_today(user["user_id"]) + est_seconds > config.daily_voice_seconds_cap:
        await send_text(psid, "Өнөөдрийн дуут дасгалын хязгаарт хүрлээ. 🙌 Маргааш үргэлжлүүлье!")
        return
    try:
        transcript = await stt.transcribe(audio, filename="voice.mp4", mime="audio/mp4")
    except ProviderError:
        await send_text(psid, "Уучлаарай, дууг тань таньж чадсангүй. 🙏 Дахин нэг илгээгээрэй?")
        return
    db.add_voice_seconds(user["user_id"], est_seconds)
    if not transcript:
        await send_text(psid, "Дуут мессеж хоосон юм шиг байна — арай тод ярьж дахин илгээгээрэй. 🎤")
        return
    await _run_turn(psid, transcript, spoken=True)


async def _handle_event(ev: dict) -> None:
    sender = ev.get("sender", {}).get("id")
    if not sender:
        return
    psid = str(sender)
    user = db.get_or_create_user(int(psid), int(psid), "", channel="messenger")

    if "postback" in ev:
        await _handle_payload(psid, user, ev["postback"].get("payload", ""))
        return

    message = ev.get("message")
    if not message or message.get("is_echo"):
        return
    quick = message.get("quick_reply", {}).get("payload")
    if quick:
        await _handle_payload(psid, user, quick)
        return
    for att in message.get("attachments") or []:
        if att.get("type") == "audio" and att.get("payload", {}).get("url"):
            await _handle_audio(psid, user, att["payload"]["url"])
            return
    text = (message.get("text") or "").strip()
    if not text:
        return
    if text.lower().startswith(("санал:", "санал ", "feedback")):
        db.save_feedback(user["user_id"], "", text)
        await send_text(psid, "Баярлалаа! 🙏 Таны санал бидэнд маш чухал.")
        return
    if user["sessions_done"] == 0 and db.get_active_session(user["user_id"]) is None:
        await send_quick_replies(psid, WELCOME, LEVEL_OPTIONS)
        return
    await _run_turn(psid, text, spoken=False)


# ---------- webhook server ----------

def _verify_signature(body: bytes, signature: str) -> bool:
    if not config.messenger_app_secret:
        return True  # not configured; accept (log once at startup)
    expected = "sha256=" + hmac.new(
        config.messenger_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature or "", expected)


async def _webhook_get(request: web.Request) -> web.Response:
    q = request.query
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == config.messenger_verify_token:
        return web.Response(text=q.get("hub.challenge", ""))
    return web.Response(status=403, text="verification failed")


async def _webhook_post(request: web.Request) -> web.Response:
    if not config.messenger_enabled:
        return web.Response(status=403, text="messenger not configured")
    body = await request.read()
    if not _verify_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        return web.Response(status=403, text="bad signature")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="bad json")
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for ev in entry.get("messaging", []):
                # Reply 200 fast; process in the background (Meta retries slow webhooks).
                asyncio.create_task(_safe_handle(ev))
    return web.Response(text="ok")


async def _safe_handle(ev: dict) -> None:
    try:
        await _handle_event(ev)
    except Exception:
        log.exception("Messenger event handling failed")


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="Talking Gym is up 🏋️")


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/webhooks/messenger", _webhook_get)
    app.router.add_post("/webhooks/messenger", _webhook_post)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.web_port)
    await site.start()
    log.info("Web server on :%s (messenger %s)", config.web_port,
             "enabled" if config.messenger_enabled else "not configured")
    if config.messenger_enabled:
        if not config.messenger_app_secret:
            log.warning("MESSENGER_APP_SECRET not set — webhook signature checks disabled")
        await setup_profile()
    return runner
