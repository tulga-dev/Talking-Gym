"""JSON API for the PWA client (served by the same aiohttp app).

Auth model (beta): /api/register issues a random 63-bit user id which the
client stores in localStorage and sends as the X-User header. Good enough
for focus testing; replace with phone/OTP auth before public launch.
"""
import base64
import logging
import secrets

from aiohttp import web

from . import coach, db
from .config import config
from .providers import ProviderError
from .providers import stt, tts
from .scenarios import by_id, pick_scenario

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 4 * 1024 * 1024  # ~4MB ≈ well over a minute of opus


def _user_from(request: web.Request):
    raw = request.headers.get("X-User", "")
    if not raw.isdigit():
        return None
    return db.get_user(int(raw))


def _err(status: int, code: str) -> web.Response:
    return web.json_response({"error": code}, status=status)


def _me_payload(user) -> dict:
    rank, cur, nxt = coach.rank_for(user["xp"] or 0)
    return {
        "user_id": str(user["user_id"]),
        "name": user["name"],
        "level": user["level"],
        "track": user["track"] if "track" in user.keys() else "business",
        "streak": user["streak"],
        "best_streak": user["best_streak"],
        "sessions_done": user["sessions_done"],
        "xp": user["xp"] or 0,
        "rank": rank,
        "rank_next_xp": nxt,
        "rank_cur_xp": cur,
        "reminder_hour": user["reminder_hour"],
        "trained_today": db.did_session_today(user["user_id"]),
        "voice_seconds_today": db.voice_seconds_today(user["user_id"]),
        "voice_seconds_cap": config.daily_voice_seconds_cap,
    }


def _scenario_payload(sc, level: str) -> dict:
    show_example = level != "advanced"
    return {
        "id": sc.id,
        "title_mn": sc.title_mn,
        "setup_mn": sc.setup_mn,
        "opener_en": sc.opener_en,
        "opener_mn": sc.opener_mn,
        "example_en": sc.example_en if show_example else "",
        "example_mn": sc.example_mn if show_example else "",
        "max_turns": config.turns_per_session,
    }


async def api_register(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    name = str(body.get("name", "")).strip()[:60]
    level = body.get("level", "beginner")
    if level not in ("beginner", "intermediate", "advanced"):
        level = "beginner"
    if not name:
        return _err(400, "name_required")
    user_id = secrets.randbits(62) | (1 << 62)  # positive, non-colliding with TG ids
    db.get_or_create_user(user_id, user_id, name, channel="pwa")
    db.set_level(user_id, level)
    user = db.get_user(user_id)
    return web.json_response({"token": str(user_id), "me": _me_payload(user)})


async def api_me(request: web.Request) -> web.Response:
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    return web.json_response(_me_payload(user))


async def api_profile(request: web.Request) -> web.Response:
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    if body.get("level") in ("beginner", "intermediate", "advanced"):
        db.set_level(user["user_id"], body["level"])
    if isinstance(body.get("track"), str) and body["track"] in (
        "business", "sales", "logistics", "travel", "movies", "daily", "dating"
    ):
        db.set_track(user["user_id"], body["track"])
    if "reminder_hour" in body:
        rh = body["reminder_hour"]
        db.set_reminder_hour(user["user_id"], int(rh) if rh is not None else None)
    return web.json_response(_me_payload(db.get_user(user["user_id"])))


async def api_session_start(request: web.Request) -> web.Response:
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    sc = pick_scenario(user["level"], user["sessions_done"])
    db.start_session(user["user_id"], sc.id)
    return web.json_response({"scenario": _scenario_payload(sc, user["level"]), "turn": 1})


async def api_session_state(request: web.Request) -> web.Response:
    """Current session if any, plus a preview of the next scenario."""
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    session = db.get_active_session(user["user_id"])
    payload = {"active": None}
    if session:
        sc = by_id(session["scenario_id"])
        payload["active"] = {
            "scenario": _scenario_payload(sc, user["level"]),
            "turn": session["turns"] + 1,
        }
    nxt = pick_scenario(user["level"], user["sessions_done"])
    payload["next_title_mn"] = nxt.title_mn
    return web.json_response(payload)


async def api_turn(request: web.Request) -> web.Response:
    """One learner turn: multipart with `audio` file OR JSON {text}."""
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    uid = user["user_id"]

    transcript = ""
    spoken = False
    ctype = request.content_type or ""

    if ctype.startswith("multipart/"):
        reader = await request.multipart()
        field = await reader.next()
        while field is not None and field.name != "audio":
            field = await reader.next()
        if field is None:
            return _err(400, "audio_missing")
        audio = await field.read(decode=False)
        if len(audio) > MAX_AUDIO_BYTES:
            return _err(413, "audio_too_large")
        est_seconds = max(1, len(audio) // 4000)
        if db.voice_seconds_today(uid) + est_seconds > config.daily_voice_seconds_cap:
            return _err(429, "voice_cap")
        filename = field.filename or "voice.webm"
        mime = field.headers.get("Content-Type", "audio/webm")
        try:
            transcript = await stt.transcribe(audio, filename=filename, mime=mime)
        except ProviderError:
            return _err(502, "stt_failed")
        db.add_voice_seconds(uid, est_seconds)
        spoken = True
        if not transcript:
            return _err(422, "empty_transcript")
    else:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "bad_json")
        transcript = str(body.get("text", "")).strip()[:600]
        if not transcript:
            return _err(400, "text_required")

    try:
        reply = await coach.handle_turn(uid, transcript)
    except ProviderError:
        return _err(502, "coach_failed")

    out = {
        "transcript": transcript,
        "reply_en": reply.reply_en,
        "corrected": reply.corrected,
        "feedback_mn": reply.feedback_mn,
        "score": reply.score,
        "done": reply.done,
        "turn_no": reply.turn_no,
        "max_turns": reply.max_turns,
        "xp_earned": reply.xp_earned,
        "streak": reply.streak,
        "best_streak": reply.best_streak,
    }

    if spoken and config.tts_enabled:
        speak_parts = []
        if reply.corrected:
            speak_parts.append(f"Listen and repeat: {reply.corrected}")
        if reply.reply_en:
            speak_parts.append(reply.reply_en if reply.done else f"Now, my question: {reply.reply_en}")
        if speak_parts:
            try:
                audio_out = await tts.speak(". ".join(speak_parts)[:500])
                out["tts_b64"] = base64.b64encode(audio_out).decode()
            except ProviderError:
                log.warning("TTS failed for API turn; text-only response")
    return web.json_response(out)


def add_api_routes(app: web.Application) -> None:
    app.router.add_post("/api/register", api_register)
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/profile", api_profile)
    app.router.add_post("/api/session/start", api_session_start)
    app.router.add_get("/api/session", api_session_state)
    app.router.add_post("/api/turn", api_turn)
