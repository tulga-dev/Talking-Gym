"""Channel-agnostic coaching engine.

Knows nothing about Telegram. A channel adapter (telegram_bot.py, later
messenger.py) calls start_daily_session() and handle_turn() and renders
the returned CoachReply however it likes.

Rendered texts use Telegram-compatible HTML (send with parse_mode=HTML):
bold for the load-bearing items, italics for translations, and
<blockquote> to make example answers unmissable.
"""
import html
import logging
from dataclasses import dataclass

from . import db
from .config import config
from .langs import lang_meta, target_of
from .prompts import (FINISH_HINT, LOCALIZE_SYSTEM, LOCALIZE_TEMPLATE,
                      TURN_TEMPLATE, system_prompt)
from .providers import ProviderError
from .providers import llm
from .scenarios import Scenario, by_id, pick_scenario

log = logging.getLogger(__name__)


@dataclass
class SessionIntro:
    scenario: Scenario
    text_mn: str


@dataclass
class CoachReply:
    reply_en: str
    corrected: str
    feedback_mn: str
    score: int
    done: bool
    suggested_en: str = ""          # model answer for the coach's next question
    suggested_mn: str = ""          # its Mongolian meaning
    reply_latin: str = ""           # romanization for non-Latin target languages
    corrected_latin: str = ""
    suggested_latin: str = ""
    turn_no: int = 1
    max_turns: int = 3
    xp_earned: int = 0
    streak: int | None = None       # set when the session completed
    best_streak: int | None = None


# ---------- gamification (Duolingo-style) ----------

RANKS = [
    (0, "🌱 Шинэхэн"),
    (100, "🥉 Хичээнгүй"),
    (300, "🥈 Тууштай"),
    (700, "🥇 Дадлагатай"),
    (1500, "🏆 Аварга"),
    (3000, "💎 Мастер"),
]

STREAK_MILESTONES = {3, 7, 14, 30, 60, 100}


def rank_for(xp: int) -> tuple[str, int, int | None]:
    """Returns (rank_name, current_threshold, next_threshold or None)."""
    name, cur = RANKS[0][1], RANKS[0][0]
    nxt: int | None = None
    for i, (threshold, rank_name) in enumerate(RANKS):
        if xp >= threshold:
            name, cur = rank_name, threshold
            nxt = RANKS[i + 1][0] if i + 1 < len(RANKS) else None
    return name, cur, nxt


def score_bar(score: int) -> str:
    """Visual 10-block score bar, colored by tier."""
    filled = max(0, min(10, round(score / 10)))
    block = "🟩" if score >= 80 else ("🟨" if score >= 60 else "🟥")
    return block * filled + "⬜" * (10 - filled)


def progress_bar(value: int, total: int, width: int = 10) -> str:
    filled = 0 if total <= 0 else max(0, min(width, round(width * value / total)))
    return "▰" * filled + "▱" * (width - filled)


def turn_dots(turn: int, max_turns: int) -> str:
    return "●" * min(turn, max_turns) + "○" * max(0, max_turns - turn)


def xp_for_turn(score: int, done: bool) -> int:
    xp = max(4, round(score / 10))       # 4-10 XP per turn
    if done:
        xp += 10                          # session completion bonus
    return xp


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def start_daily_session(user_id: int) -> SessionIntro:
    user = db.get_user(user_id)
    scenario = pick_scenario(user["level"], user["sessions_done"])
    db.start_session(user_id, scenario.id)

    parts = [
        f"🏋️ <b>Өнөөдрийн дасгал: {_esc(scenario.title_mn)}</b>",
        f"\n📖 {_esc(scenario.setup_mn)}",
        f"\n💬 <b>{_esc(config.coach_name_mn)}:</b> {_esc(scenario.opener_en)}",
        f"🇲🇳 <i>{_esc(scenario.opener_mn)}</i>",
    ]
    if user["level"] != "advanced":
        # Beginners must never face a question they can't answer:
        # give a model answer to read aloud and adapt.
        parts.append("\n📝 <b>ЖИШЭЭ ХАРИУЛТ</b> — уншаад, өөрийн мэдээллээр өөрчлөөрэй:")
        parts.append(
            f"<blockquote>🗣 <b>{_esc(scenario.example_en)}</b>\n"
            f"🇲🇳 <i>{_esc(scenario.example_mn)}</i></blockquote>"
        )
        parts.append(
            "🎤 Одоо дуут мессежээр хэлээрэй! Жишээг шууд уншсан ч болно — "
            "чанга уншина гэдэг чинь дасгал шүү! 💪"
        )
    else:
        parts.append("\n🎤 Хариугаа дуут мессежээр илгээгээрэй!")
    return SessionIntro(scenario=scenario, text_mn="\n".join(parts))


# Generated opener/example per (scenario, target language) — static, so cache
# them for the process lifetime instead of regenerating every session.
_loc_cache: dict[tuple, dict] = {}


async def localize_scenario(scenario: Scenario, target_lang: str) -> dict:
    """The scenario's opener + model answer in the target language (+ Mongolian
    translations). English uses the authored text; other languages are
    LLM-generated once and cached, with an English fallback on any failure."""
    authored = {
        "opener": scenario.opener_en, "opener_mn": scenario.opener_mn,
        "opener_latin": "",
        "example": scenario.example_en, "example_mn": scenario.example_mn,
        "example_latin": "",
    }
    if target_lang == "en":
        return authored
    key = (scenario.id, target_lang)
    if key in _loc_cache:
        return _loc_cache[key]
    meta = lang_meta(target_lang)
    prompt = LOCALIZE_TEMPLATE.format(
        lang=meta["name_en"],
        roman=meta["roman"] or "Latin letters",
        setup_mn=scenario.setup_mn,
        coach=config.coach_name_en,
        opener_en=scenario.opener_en,
        example_en=scenario.example_en,
        level="beginner",
    )
    try:
        # Localizations are generated once and cached — spend real reasoning
        # here for natural phrasing; latency doesn't matter on this path.
        data = llm.parse_json_block(await llm.chat(LOCALIZE_SYSTEM, prompt, effort="low"))
        loc = {
            "opener": str(data.get("opener", "")).strip() or authored["opener"],
            "opener_mn": str(data.get("opener_mn", "")).strip() or authored["opener_mn"],
            "opener_latin": str(data.get("opener_latin", "")).strip(),
            "example": str(data.get("example", "")).strip() or authored["example"],
            "example_mn": str(data.get("example_mn", "")).strip() or authored["example_mn"],
            "example_latin": str(data.get("example_latin", "")).strip(),
        }
    except Exception:
        log.warning("Scenario localization failed for %s/%s; using English", scenario.id, target_lang)
        loc = authored
    _loc_cache[key] = loc
    return loc


async def handle_turn(user_id: int, transcript: str) -> CoachReply:
    """Run one learner turn through the LLM coach; manage session lifecycle."""
    session = db.get_active_session(user_id)
    if session is None:
        # Auto-start: makes "just send a voice note" work with zero ceremony.
        start_daily_session(user_id)
        session = db.get_active_session(user_id)

    user = db.get_user(user_id)
    scenario = by_id(session["scenario_id"])
    target_lang = target_of(user)
    lmeta = lang_meta(target_lang)
    lang_name = lmeta["name_en"]
    loc = await localize_scenario(scenario, target_lang)
    turn = session["turns"] + 1
    max_turns = config.turns_per_session
    finish_hint = FINISH_HINT if turn >= max_turns else ""

    prompt = TURN_TEMPLATE.format(
        title=scenario.title_mn,
        opener=loc["opener"],
        focus=scenario.focus,
        level=user["level"],
        turn=turn,
        max_turns=max_turns,
        finish_hint=finish_hint,
        history=session["history"] or "(start of conversation)",
        transcript=transcript,
    )

    raw = await llm.chat(system_prompt(lang_name, lmeta["roman"]), prompt)
    try:
        data = llm.parse_json_block(raw)
    except ProviderError:
        log.warning("Non-JSON LLM reply, using raw text fallback")
        data = {"reply_en": raw[:400], "corrected": transcript, "feedback_mn": "", "score": 60, "done": False}

    reply = CoachReply(
        reply_en=str(data.get("reply_en", "")).strip(),
        corrected=str(data.get("corrected", "")).strip(),
        feedback_mn=str(data.get("feedback_mn", "")).strip(),
        score=int(data.get("score", 0) or 0),
        done=bool(data.get("done", False)) or turn >= max_turns,
        suggested_en=str(data.get("suggested_en", "")).strip(),
        suggested_mn=str(data.get("suggested_mn", "")).strip(),
        reply_latin=str(data.get("reply_latin", "")).strip(),
        corrected_latin=str(data.get("corrected_latin", "")).strip(),
        suggested_latin=str(data.get("suggested_latin", "")).strip(),
        turn_no=turn,
        max_turns=max_turns,
    )

    line = f'Learner: "{transcript}" / Coach: "{reply.reply_en}"'
    if reply.suggested_en:
        line += f' / Coach offered example answer: "{reply.suggested_en}"'
    db.record_turn(user_id, line)
    if not reply.done:
        db.set_last_example(user_id, reply.suggested_en)

    reply.xp_earned = xp_for_turn(reply.score, reply.done)
    db.add_xp(user_id, reply.xp_earned)

    if reply.done:
        streak, best = db.complete_session(user_id)
        reply.streak, reply.best_streak = streak, best
    return reply


def format_reply(reply: CoachReply, transcript: str) -> str:
    """Render a CoachReply as Telegram HTML (channel adapters may reuse this)."""
    parts = [f"{turn_dots(reply.turn_no, reply.max_turns)}  <i>{reply.turn_no}/{reply.max_turns}</i>\n"]
    parts.append(f'🗣 Таны хэлсэн: <i>"{_esc(transcript)}"</i>')
    if reply.corrected and reply.corrected.lower() != transcript.lower():
        parts.append(f'✅ Зөв хувилбар: <b>"{_esc(reply.corrected)}"</b>')
    else:
        parts.append("✅ Маш зөв хэллээ!")
    if reply.feedback_mn:
        parts.append(f"💡 {_esc(reply.feedback_mn)}")
    parts.append(f"{score_bar(reply.score)}  <b>{reply.score}</b>  ⭐ +{reply.xp_earned} XP")
    if reply.reply_en and not reply.done:
        parts.append(f"\n💬 <b>{_esc(config.coach_name_mn)}:</b> {_esc(reply.reply_en)}")
        if reply.suggested_en:
            parts.append("📝 <b>ЖИШЭЭ</b> — уншаад хэлээрэй:")
            block = f"🗣 <b>{_esc(reply.suggested_en)}</b>"
            if reply.suggested_mn:
                block += f"\n🇲🇳 <i>{_esc(reply.suggested_mn)}</i>"
            parts.append(f"<blockquote>{block}</blockquote>")
    if reply.done:
        if reply.reply_en:
            parts.append(f"\n💬 <b>{_esc(config.coach_name_mn)}:</b> {_esc(reply.reply_en)}")
        parts.append("\n🎉 <b>Өнөөдрийн дасгал дууслаа!</b>")
        if reply.streak is not None:
            line = f"🔥 Стрик: <b>{reply.streak} өдөр</b>"
            if reply.best_streak and reply.streak >= reply.best_streak and reply.streak > 1:
                line += " — шинэ дээд амжилт! 🏆"
            parts.append(line)
            if reply.streak in STREAK_MILESTONES:
                parts.append(f"🏅 <b>{reply.streak} хоногийн стрик!</b> Гайхалтай тууштай байна! 👏")
    return "\n".join(parts)


def progress_card(user) -> str:
    """Duolingo-style profile/progress card (Telegram HTML)."""
    xp = user["xp"] or 0
    rank, cur, nxt = rank_for(xp)
    lines = [f"🏅 Зэрэглэл: <b>{rank}</b>"]
    if nxt is not None:
        lines.append(f"{progress_bar(xp - cur, nxt - cur)}  {xp}/{nxt} XP")
    else:
        lines.append(f"⭐ {xp} XP — дээд зэрэглэл!")
    lines.append("")
    lines.append(f"🔥 Стрик: <b>{user['streak']} өдөр</b>  (дээд: {user['best_streak']})")
    lines.append(f"✅ Нийт дасгал: {user['sessions_done']}")
    return "\n".join(lines)
