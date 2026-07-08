"""JSON API for the PWA client (served by the same aiohttp app).

Auth model (beta): /api/register issues a random 63-bit user id which the
client stores in localStorage and sends as the X-User header. Good enough
for focus testing; replace with phone/OTP auth before public launch.
"""
import asyncio
import base64
import difflib
import logging
import os
import secrets
from urllib.parse import quote

from aiohttp import web

from . import coach, db
from .config import config
from .langs import lang_meta, native_of, target_of
from .providers import ProviderError
from .providers import imagegen, stt, tts
from .scenarios import by_id, pick_scenario

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 4 * 1024 * 1024  # ~4MB ≈ well over a minute of opus

# Slower speech for lower levels — easier to follow, feels like a patient tutor.
LEVEL_SPEED = {"beginner": 0.75, "intermediate": 0.9, "advanced": 1.0}

# Spoken scaffolds, in the target language, so the audio stays immersive:
# (correction preface, next-question cue). {c}/{r} = corrected / reply text.
SPEECH_CUES = {
    "en": ("You could say it like this... {c}", "Okay... next question. {r}"),
    "ko": ("이렇게 말할 수 있어요... {c}", "좋아요... 다음 질문이에요. {r}"),
    "zh": ("你可以这样说…… {c}", "好的…… 下一个问题。{r}"),
    "ja": ("こう言えます… {c}", "はい… 次の質問です。{r}"),
}


def _speech_alike(a: str, b: str) -> bool:
    """True when two sentences sound the same (ignoring case/punctuation)."""
    strip = lambda s: "".join(c for c in s.lower() if c.isalnum() or c.isspace()).split()
    return strip(a) == strip(b)


def _snap_to_example(transcript: str, example: str) -> str:
    """Learners are told to read the example aloud — when STT mishears a word
    or two of accented speech, the coach ends up 'correcting' its own example.
    If the transcript is clearly the example, trust the known text instead."""
    if not example or not transcript:
        return transcript
    norm = lambda s: " ".join(
        "".join(c.lower() if (c.isalnum() or c.isspace()) else " " for c in s).split()
    )
    a, b = norm(transcript), norm(example)
    if not a or not b:
        return transcript
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.75:
        return example
    return transcript


def _user_from(request: web.Request):
    from .web_auth import user_from_request
    return user_from_request(request)


def _err(status: int, code: str) -> web.Response:
    return web.json_response({"error": code}, status=status)


def _effective_plan(user) -> str:
    """The plan a user actually has right now: founders are always premium;
    otherwise the stored plan, downgraded to free once it has expired."""
    if user["user_id"] in config.founder_ids:
        return "premium"
    plan = user["plan"] if "plan" in user.keys() else "free"
    if not plan or plan == "free":
        return "free"
    exp = user["plan_expires"] if "plan_expires" in user.keys() else None
    if exp:
        try:
            from datetime import date
            if date.fromisoformat(exp) < db._today():
                return "free"
        except (ValueError, TypeError):
            pass
    return plan


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
        "turns_per_session": config.turns_per_session,
        "target_lang": target_of(user),
        "native_lang": native_of(user),
        "profile_note": (user["profile_note"] if "profile_note" in user.keys() else "") or "",
        "plan": _effective_plan(user),
        "plan_expires": user["plan_expires"] if "plan_expires" in user.keys() else None,
        "trained_today": db.did_session_today(user["user_id"]),
        "voice_seconds_today": db.voice_seconds_today(user["user_id"]),
        "voice_seconds_cap": config.daily_voice_seconds_cap,
        "words_learned": db.vocab_learned_count(user["user_id"], target_of(user)),
    }


async def _scenario_payload(sc, user) -> dict:
    """Scenario for the client, with opener/example in the user's target
    language (keys keep their legacy _en names but carry target-lang text)."""
    level = user["level"]
    show_example = level != "advanced"
    loc = await coach.localize_scenario(sc, target_of(user), native_of(user))
    return {
        "id": sc.id,
        "title_mn": loc.get("title", sc.title_mn),
        "setup_mn": loc.get("setup", sc.setup_mn),
        "opener_en": loc["opener"],
        "opener_mn": loc["opener_mn"],
        "opener_latin": loc.get("opener_latin", ""),
        "example_en": loc["example"] if show_example else "",
        "example_mn": loc["example_mn"] if show_example else "",
        "example_latin": loc.get("example_latin", "") if show_example else "",
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
    if body.get("target_lang") in ("en", "ko", "zh", "ja"):
        db.set_target_lang(user["user_id"], body["target_lang"])
    if body.get("native_lang") in ("mn", "mnt"):
        db.set_native_lang(user["user_id"], body["native_lang"])
    if isinstance(body.get("track"), str) and body["track"] in (
        "business", "sales", "logistics", "travel", "movies", "daily", "dating"
    ):
        db.set_track(user["user_id"], body["track"])
    if "reminder_hour" in body:
        rh = body["reminder_hour"]
        db.set_reminder_hour(user["user_id"], int(rh) if rh is not None else None)
    return web.json_response(_me_payload(db.get_user(user["user_id"])))


_opener_cache: dict[tuple, str] = {}  # (scenario, lang, speed) -> b64; openers are static


async def _opener_tts(sc_id: str, opener_text: str, target_lang: str, level: str) -> str | None:
    if not config.tts_enabled:
        return None
    speed = LEVEL_SPEED.get(level, 1.0)
    key = (sc_id, target_lang, speed)
    if key in _opener_cache:
        return _opener_cache[key]
    # Second layer: DB cache — opener audio survives deploys.
    dbkey = f"tts:v2:{sc_id}:{target_lang}:{speed}"   # v2: openers re-voiced as Kitty
    try:
        stored = db.cache_get(dbkey)
        if stored:
            _opener_cache[key] = stored
            return stored
    except Exception:
        log.exception("cache_get failed for %s", dbkey)
    try:
        audio = await tts.speak(opener_text[:400], language=target_lang, speed=speed)
    except ProviderError:
        return None
    b64 = base64.b64encode(audio).decode()
    _opener_cache[key] = b64
    try:
        db.cache_set(dbkey, b64)
    except Exception:
        log.exception("cache_set failed for %s", dbkey)
    return b64


async def api_session_start(request: web.Request) -> web.Response:
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    target = target_of(user)
    active = db.get_active_session(user["user_id"])
    if active and active["turns"] > 0:
        # resume mid-session instead of silently restarting the conversation
        sc = by_id(active["scenario_id"])
        payload = await _scenario_payload(sc, user)
        return web.json_response({
            "scenario": payload,
            "turn": active["turns"] + 1,
            "resumed": True,
            "tts_b64": await _opener_tts(sc.id, payload["opener_en"], target, user["level"]),
        })
    if user["sessions_done"] == 0:
        # Very first conversation = placement chat: Sarah gets to know the
        # learner and sets their level — no self-assessment at signup.
        sc = by_id("placement")
    else:
        sc = pick_scenario(user["level"], user["sessions_done"])
    db.start_session(user["user_id"], sc.id)
    payload = await _scenario_payload(sc, user)
    db.set_last_example(user["user_id"], payload["example_en"])
    return web.json_response({
        "scenario": payload,
        "turn": 1,
        "resumed": False,
        "tts_b64": await _opener_tts(sc.id, payload["opener_en"], target, user["level"]),
    })


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
            "scenario": await _scenario_payload(sc, user),
            "turn": session["turns"] + 1,
        }
    nxt = pick_scenario(user["level"], user["sessions_done"])
    # Home screen must never wait on generation: cached title or the authored
    # one, and warm the cache in the background for next time.
    nloc = await coach.localize_scenario(nxt, target_of(user), native_of(user), cached_only=True)
    payload["next_title_mn"] = nloc.get("title", nxt.title_mn)
    asyncio.create_task(coach.localize_scenario(nxt, target_of(user), native_of(user)))
    return web.json_response(payload)


async def api_turn(request: web.Request) -> web.Response:
    """One learner turn: multipart with `audio` file OR JSON {text}."""
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    uid = user["user_id"]
    target = target_of(user)

    # Learner's LLM pick rides in the query string so it works uniformly for the
    # multipart (voice) and JSON (text) bodies. Allowlisted to known backends.
    model = request.query.get("model", "grok").strip().lower()
    if model not in ("grok", "gemini"):
        model = "grok"

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
        audio = bytes(await field.read(decode=False))  # aiohttp yields bytearray; httpx needs bytes
        if len(audio) > MAX_AUDIO_BYTES:
            return _err(413, "audio_too_large")
        est_seconds = max(1, len(audio) // 4000)
        if (uid not in config.founder_ids
                and db.voice_seconds_today(uid) + est_seconds > config.daily_voice_seconds_cap):
            return _err(429, "voice_cap")
        filename = field.filename or "voice.webm"
        mime = field.headers.get("Content-Type", "audio/webm")
        try:
            transcript = await stt.transcribe(audio, filename=filename, mime=mime,
                                              language=lang_meta(target)["stt"])
        except ProviderError:
            return _err(502, "stt_failed")
        except Exception:
            log.exception("STT unexpected failure")
            return _err(502, "stt_failed")
        db.add_voice_seconds(uid, est_seconds)
        spoken = True
        if not transcript:
            # Diagnostic breadcrumb: silent uploads have been reported on
            # desktop from repeated mic open/close cycles.
            log.warning("Empty transcript: %d bytes, mime=%s, user=%s", len(audio), mime, uid)
            return _err(422, "empty_transcript")
        sess = db.get_active_session(uid)
        if sess is not None:
            try:
                transcript = _snap_to_example(transcript, sess["last_example"])
            except (KeyError, IndexError):
                pass  # row predates the last_example column
    else:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "bad_json")
        transcript = str(body.get("text", "")).strip()[:600]
        if not transcript:
            return _err(400, "text_required")

    try:
        reply = await coach.handle_turn(uid, transcript, model=model)
    except ProviderError:
        return _err(502, "coach_failed")

    out = {
        "transcript": transcript,
        "reply_en": reply.reply_en,
        "reply_mn": reply.reply_mn,
        "corrected": reply.corrected,
        "feedback_mn": reply.feedback_mn,
        "score": reply.score,
        "done": reply.done,
        "suggested_en": reply.suggested_en,
        "suggested_mn": reply.suggested_mn,
        "reply_latin": reply.reply_latin,
        "corrected_latin": reply.corrected_latin,
        "suggested_latin": reply.suggested_latin,
        "turn_no": reply.turn_no,
        "max_turns": reply.max_turns,
        "xp_earned": reply.xp_earned,
        "streak": reply.streak,
        "best_streak": reply.best_streak,
        "placed_level": reply.placed_level,
        "not_an_answer": reply.not_an_answer,
    }

    if spoken and config.tts_enabled:
        pre, cue = SPEECH_CUES.get(target, SPEECH_CUES["en"])
        speak_parts = []
        # Only model the correction aloud when it actually differs from what
        # the learner said — repeating a perfect sentence back sounds robotic.
        if reply.corrected and not _speech_alike(reply.corrected, transcript):
            speak_parts.append(pre.format(c=reply.corrected))
        if reply.reply_en:
            # Spoken cue so the correction and the question don't blur together.
            speak_parts.append(reply.reply_en if reply.done else cue.format(r=reply.reply_en))
        if speak_parts:
            try:
                audio_out = await tts.speak(
                    " ... ".join(speak_parts)[:500],
                    language=target,
                    speed=LEVEL_SPEED.get(user["level"], 1.0),
                )
                out["tts_b64"] = base64.b64encode(audio_out).decode()
            except ProviderError:
                log.warning("TTS failed for API turn; text-only response")
    return web.json_response(out)


async def api_plan_redeem(request: web.Request) -> web.Response:
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    code = str(body.get("code", "")).strip().upper()
    if not code:
        return _err(400, "code_required")
    result = db.redeem_promo_code(user["user_id"], code)
    if result is None:
        return _err(404, "invalid_code")
    return web.json_response({"ok": True, "plan": result["plan"],
                              "me": _me_payload(db.get_user(user["user_id"]))})


async def api_plan_grant(request: web.Request) -> web.Response:
    """Founder-only: mint a promo code (or grant a plan to self)."""
    user = _user_from(request)
    if user is None or user["user_id"] not in config.founder_ids:
        return _err(403, "forbidden")
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    plan = body.get("plan", "gym")
    if plan not in ("gym", "premium"):
        plan = "gym"
    days = int(body.get("days", 30) or 0)
    uses = int(body.get("uses", 1) or 1)
    code = "GYM-" + secrets.token_hex(3).upper()
    db.create_promo_code(code, plan, days, uses)
    return web.json_response({"code": code, "plan": plan, "days": days, "uses": uses})


_warm_state = {"running": False, "done": 0, "total": 0, "errors": 0}


async def _warm_all():
    """Pre-generate everything a learner would otherwise wait on into the
    durable cache: scenario localizations, opener audio, the daily vocab word
    sets, and each word's illustration + pronunciation audio. Vocab text is
    warmed for every language pair (English/Mongolian skips localization but
    still needs its words); media assets are deduped by (target lang, word)
    since the word list is identical across native languages."""
    from .langs import NATIVE_LANGS, TARGET_LANGS
    from .scenarios import SCENARIOS
    combos = [(t, n) for t in TARGET_LANGS for n in NATIVE_LANGS
              if not (t == "en" and n == "mn")]
    loc_jobs = [(sc, t, n) for sc in SCENARIOS for (t, n) in combos]
    tts_jobs = [(sc, t) for sc in SCENARIOS for t in TARGET_LANGS]
    vocab_combos = [(t, n) for t in TARGET_LANGS for n in NATIVE_LANGS]
    vocab_jobs = [(sc, t, n) for sc in SCENARIOS if sc.id != "placement"
                  for (t, n) in vocab_combos]
    _warm_state.update(running=True, done=0, errors=0,
                       total=len(loc_jobs) + len(tts_jobs) + len(vocab_jobs))
    sem = asyncio.Semaphore(5)

    async def warm_loc(sc, t, n):
        async with sem:
            try:
                await coach.localize_scenario(sc, t, n)
            except Exception:
                _warm_state["errors"] += 1
                log.exception("warm localize failed %s/%s/%s", sc.id, t, n)
            _warm_state["done"] += 1

    async def warm_tts(sc, t):
        async with sem:
            try:
                loc = await coach.localize_scenario(sc, t, "mn")
                await _opener_tts(sc.id, loc["opener"], t, sc.level)
            except Exception:
                _warm_state["errors"] += 1
                log.exception("warm tts failed %s/%s", sc.id, t)
            _warm_state["done"] += 1

    async def warm_vocab(sc, t, n):
        async with sem:
            try:
                await coach.scenario_vocab(sc, t, n, level=sc.level)
            except Exception:
                _warm_state["errors"] += 1
                log.exception("warm vocab failed %s/%s/%s", sc.id, t, n)
            _warm_state["done"] += 1

    await asyncio.gather(*[warm_loc(*j) for j in loc_jobs])
    await asyncio.gather(*[warm_tts(*j) for j in tts_jobs])
    await asyncio.gather(*[warm_vocab(*j) for j in vocab_jobs])

    # Media assets: one image + one audio per unique (target lang, word).
    # Image generation is the only costly warm step, so it is scoped to the
    # rollout language(s) by default — expand via WARM_ASSET_LANGS when other
    # languages get learners. Vocab text above is warmed for every pair.
    asset_langs = [x.strip() for x in os.getenv("WARM_ASSET_LANGS", "en").split(",")
                   if x.strip() in TARGET_LANGS]
    asset_words: dict = {}
    for t in asset_langs:
        for sc in SCENARIOS:
            if sc.id == "placement":
                continue
            try:
                vocab = await coach.scenario_vocab(sc, t, "mn", level=sc.level,
                                                   cached_only=True)
            except Exception:
                vocab = []
            for w in vocab:
                asset_words.setdefault(
                    (t, w["word"]), (w.get("concrete", True), w.get("example", "")))
    _warm_state["total"] += len(asset_words)

    async def warm_asset(t, word, concrete, example):
        async with sem:
            try:
                if imagegen.enabled():
                    await _ensure_vocab_image(t, word, example, concrete)
                if config.tts_enabled:
                    await _ensure_vocab_audio(t, word)
            except Exception:
                _warm_state["errors"] += 1
                log.exception("warm asset failed %s/%s", t, word)
            _warm_state["done"] += 1

    await asyncio.gather(*[warm_asset(t, w, c, ex)
                           for (t, w), (c, ex) in asset_words.items()])
    _warm_state["running"] = False
    log.info("Cache warm-up complete: %s jobs, %s errors",
             _warm_state["total"], _warm_state["errors"])


async def api_admin_warm(request: web.Request) -> web.Response:
    """Founder-only. POST starts a warm-up (no-op if already running);
    GET reports progress."""
    user = _user_from(request)
    if user is None or user["user_id"] not in config.founder_ids:
        return _err(403, "forbidden")
    if request.method == "POST" and not _warm_state["running"]:
        asyncio.create_task(_warm_all())
    return web.json_response(_warm_state)


async def api_admin_accounts(request: web.Request) -> web.Response:
    """Founder-only: recent email accounts (for support / password resets)."""
    user = _user_from(request)
    if user is None or user["user_id"] not in config.founder_ids:
        return _err(403, "forbidden")
    accounts = db.recent_email_accounts()
    return web.json_response({"accounts": [
        {"user_id": str(a["user_id"]), "name": a["name"], "email": a["email"],
         "has_password": bool(a.get("password_hash")),
         "has_google": bool(a.get("google_sub"))}
        for a in accounts
    ]})


async def api_admin_set_password(request: web.Request) -> web.Response:
    """Founder-only: set (or create) a password for an account by email."""
    from .web_auth import hash_password
    user = _user_from(request)
    if user is None or user["user_id"] not in config.founder_ids:
        return _err(403, "forbidden")
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    email = str(body.get("email", "")).strip().lower()
    new_password = str(body.get("new_password", ""))
    if len(new_password) < 6:
        return _err(400, "weak_password")
    target = db.user_by_email(email)
    if target is None:
        return _err(404, "no_such_account")
    db.set_auth(target["user_id"], email=email, password_hash=hash_password(new_password))
    return web.json_response({"ok": True, "user_id": str(target["user_id"]),
                              "name": target["name"], "email": email})


async def api_admin_set_email(request: web.Request) -> web.Response:
    """Founder-only: change an account's login email (must be free)."""
    user = _user_from(request)
    if user is None or user["user_id"] not in config.founder_ids:
        return _err(403, "forbidden")
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    old_email = str(body.get("old_email", "")).strip().lower()
    new_email = str(body.get("new_email", "")).strip().lower()
    if "@" not in new_email:
        return _err(400, "bad_email")
    if db.user_by_email(new_email):
        return _err(409, "email_taken")
    target = db.user_by_email(old_email)
    if target is None:
        return _err(404, "no_such_account")
    db.set_auth(target["user_id"], email=new_email)
    return web.json_response({"ok": True, "user_id": str(target["user_id"]),
                             "name": target["name"], "email": new_email})


# ---------- vocabulary (daily words tied to the day's conversation) ----------

_vocab_locks: dict = {}  # (kind, lang, word) -> asyncio.Lock, serializes generation


def _vocab_lock(key):
    lk = _vocab_locks.get(key)
    if lk is None:
        lk = asyncio.Lock()
        _vocab_locks[key] = lk
    return lk


def _user_media(request: web.Request):
    """Auth for <img>/<audio> loads: header, or a `t` token query param the
    client appends (tags can't set headers)."""
    user = _user_from(request)
    if user is not None:
        return user
    tok = request.query.get("t", "").strip()
    if not tok:
        return None
    return db.get_user(int(tok)) if tok.isdigit() else db.user_by_token(tok)


def _vocab_scenario(user):
    """The scenario whose words are 'today's words' — the same one the lesson
    will run. Placement (very first chat) has no warm-up words."""
    active = db.get_active_session(user["user_id"])
    if active and active["turns"] > 0:
        return by_id(active["scenario_id"])
    if user["sessions_done"] == 0:
        return None
    return pick_scenario(user["level"], user["sessions_done"])


async def _scenario_word(user, lang: str, word: str):
    """The vocab item dict for `word` in the learner's current scenario, or
    None — used to authorize on-demand asset generation."""
    sc = _vocab_scenario(user)
    if sc is None:
        return None
    vocab = await coach.scenario_vocab(sc, lang, native_of(user), user["level"],
                                       cached_only=True)
    for w in vocab:
        if w.get("word") == word:
            return w
    return None


async def _ensure_vocab_image(lang: str, word: str, hint: str, concrete: bool = True) -> None:
    asset = db.vocab_asset_get(lang, word)
    if asset and asset["data"]:
        return
    async with _vocab_lock(("img", lang, word)):
        asset = db.vocab_asset_get(lang, word)
        if asset and asset["data"]:
            return
        try:
            mime, data = await imagegen.generate(word, hint, concrete)
            db.vocab_asset_set_image(lang, word, mime, bytes(data))
        except Exception:
            log.warning("vocab image gen failed for %s/%s", lang, word)


async def _ensure_vocab_audio(lang: str, word: str) -> None:
    asset = db.vocab_asset_get(lang, word)
    if asset and asset["tts_b64"]:
        return
    async with _vocab_lock(("tts", lang, word)):
        asset = db.vocab_asset_get(lang, word)
        if asset and asset["tts_b64"]:
            return
        try:
            audio = await tts.speak(word[:60], language=lang, speed=0.85)
            db.vocab_asset_set_tts(lang, word, base64.b64encode(audio).decode())
        except Exception:
            log.warning("vocab tts gen failed for %s/%s", lang, word)


async def api_vocab_today(request: web.Request) -> web.Response:
    """Today's word set — the vocabulary of the day's conversation scenario,
    with each learner's progress. Kicks off image/audio warming in the
    background so assets are ready by the time a card is tapped."""
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    lang = target_of(user)
    sc = _vocab_scenario(user)
    if sc is None:
        return web.json_response({"scenario_id": None, "title_mn": "", "words": []})
    loc = await coach.localize_scenario(sc, lang, native_of(user), cached_only=True)
    vocab = await coach.scenario_vocab(sc, lang, native_of(user), user["level"])
    prog = db.vocab_progress_map(user["user_id"], lang)
    words = []
    for w in vocab:
        word = w["word"]
        asset = db.vocab_asset_get(lang, word)
        has_img = bool(asset and asset["data"])
        has_aud = bool(asset and asset["tts_b64"])
        if not has_img and imagegen.enabled():
            asyncio.create_task(_ensure_vocab_image(
                lang, word, w.get("example", ""), w.get("concrete", True)))
        if not has_aud and config.tts_enabled:
            asyncio.create_task(_ensure_vocab_audio(lang, word))
        words.append({
            "word": word,
            "latin": w.get("latin", ""),
            "mn": w.get("mn", ""),
            "ipa": w.get("ipa", ""),
            "pos": w.get("pos", ""),
            "example": w.get("example", ""),
            "example_mn": w.get("example_mn", ""),
            "concrete": w.get("concrete", True),
            "status": prog.get(word, "new"),
            "image_url": f"/api/vocab/image?lang={lang}&word={quote(word)}",
            "audio_url": f"/api/vocab/audio?lang={lang}&word={quote(word)}",
        })
    return web.json_response({
        "scenario_id": sc.id,
        "title_mn": loc.get("title", sc.title_mn),
        "words": words,
    })


async def api_vocab_image(request: web.Request) -> web.Response:
    user = _user_media(request)
    if user is None:
        return _err(401, "unauthorized")
    lang = request.query.get("lang", "en")
    word = request.query.get("word", "").strip()
    if not word:
        return _err(400, "word_required")
    asset = db.vocab_asset_get(lang, word)
    if not (asset and asset["data"]):
        item = await _scenario_word(user, lang, word)
        if item is None or not imagegen.enabled():
            return _err(404, "no_image")
        await _ensure_vocab_image(lang, word, item.get("example", ""),
                                  item.get("concrete", True))
        asset = db.vocab_asset_get(lang, word)
        if not (asset and asset["data"]):
            return _err(502, "gen_failed")
    return web.Response(
        body=bytes(asset["data"]),
        content_type=asset["mime"] or "image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


async def api_vocab_audio(request: web.Request) -> web.Response:
    user = _user_media(request)
    if user is None:
        return _err(401, "unauthorized")
    lang = request.query.get("lang", "en")
    word = request.query.get("word", "").strip()
    if not word:
        return _err(400, "word_required")
    asset = db.vocab_asset_get(lang, word)
    if not (asset and asset["tts_b64"]):
        if await _scenario_word(user, lang, word) is None:
            return _err(404, "no_audio")
        await _ensure_vocab_audio(lang, word)
        asset = db.vocab_asset_get(lang, word)
        if not (asset and asset["tts_b64"]):
            return _err(502, "gen_failed")
    return web.Response(
        body=base64.b64decode(asset["tts_b64"]),
        content_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


async def api_vocab_learned(request: web.Request) -> web.Response:
    """Words the learner has marked known, most recent first (for the progress
    screen). Kept lightweight — just the words themselves."""
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    lang = target_of(user)
    words = db.vocab_learned(user["user_id"], lang)
    return web.json_response({"count": len(words), "words": words})


def _word_match(word: str, heard: str) -> bool:
    """Lenient match between a target word and what STT heard — tolerant of
    trailing punctuation, articles the learner might add, and word spacing."""
    norm = lambda s: "".join(c for c in s.lower() if c.isalnum() or c.isspace()).strip()
    nw, nh = norm(word), norm(heard)
    if not nw or not nh:
        return False
    if nw == nh or nw in nh.split() or nw in nh or nh in nw:
        return True
    return nw.replace(" ", "") in nh.replace(" ", "")


async def api_vocab_speak(request: web.Request) -> web.Response:
    """Speaking test: learner records themselves saying a word; STT checks it.
    A match marks the word known. Not counted against the daily voice cap —
    single-word practice should never be blocked."""
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    lang = request.query.get("lang", "").strip() or target_of(user)
    if lang not in ("en", "ko", "zh", "ja"):
        lang = target_of(user)
    word = request.query.get("word", "").strip()[:40]
    if not word:
        return _err(400, "word_required")
    if not (request.content_type or "").startswith("multipart/"):
        return _err(400, "audio_required")
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "audio":
        field = await reader.next()
    if field is None:
        return _err(400, "audio_missing")
    audio = bytes(await field.read(decode=False))
    if len(audio) > MAX_AUDIO_BYTES:
        return _err(413, "audio_too_large")
    filename = field.filename or "word.webm"
    mime = field.headers.get("Content-Type", "audio/webm")
    try:
        heard = await stt.transcribe(audio, filename=filename, mime=mime,
                                     language=lang_meta(lang)["stt"])
    except ProviderError:
        return _err(502, "stt_failed")
    except Exception:
        log.exception("vocab speak STT failed")
        return _err(502, "stt_failed")
    match = _word_match(word, heard or "")
    if match:
        db.vocab_progress_set(user["user_id"], lang, word, "known")
    return web.json_response({"ok": True, "heard": (heard or "").strip()[:80], "match": match})


async def api_vocab_progress(request: web.Request) -> web.Response:
    user = _user_from(request)
    if user is None:
        return _err(401, "unauthorized")
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    word = str(body.get("word", "")).strip()[:40]
    status = body.get("status", "known")
    if status not in ("known", "again", "new"):
        status = "known"
    if not word:
        return _err(400, "word_required")
    db.vocab_progress_set(user["user_id"], target_of(user), word, status)
    return web.json_response({"ok": True})


def add_api_routes(app: web.Application) -> None:
    from .web_auth import add_auth_routes
    add_auth_routes(app)
    app.router.add_post("/api/register", api_register)
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/profile", api_profile)
    app.router.add_post("/api/session/start", api_session_start)
    app.router.add_get("/api/session", api_session_state)
    app.router.add_post("/api/turn", api_turn)
    app.router.add_post("/api/plan/redeem", api_plan_redeem)
    app.router.add_post("/api/plan/grant", api_plan_grant)
    app.router.add_post("/api/admin/warm", api_admin_warm)
    app.router.add_get("/api/admin/warm", api_admin_warm)
    app.router.add_get("/api/admin/accounts", api_admin_accounts)
    app.router.add_post("/api/admin/set-password", api_admin_set_password)
    app.router.add_post("/api/admin/set-email", api_admin_set_email)
    app.router.add_get("/api/vocab/today", api_vocab_today)
    app.router.add_get("/api/vocab/image", api_vocab_image)
    app.router.add_get("/api/vocab/audio", api_vocab_audio)
    app.router.add_get("/api/vocab/learned", api_vocab_learned)
    app.router.add_post("/api/vocab/speak", api_vocab_speak)
    app.router.add_post("/api/vocab/progress", api_vocab_progress)
