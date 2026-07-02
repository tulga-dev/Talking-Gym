"""Channel-agnostic coaching engine.

Knows nothing about Telegram. A channel adapter (telegram_bot.py, later
messenger.py) calls start_daily_session() and handle_turn() and renders
the returned CoachReply however it likes.
"""
import logging
from dataclasses import dataclass

from . import db
from .config import config
from .prompts import FINISH_HINT, SYSTEM_PROMPT, TURN_TEMPLATE
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
    streak: int | None = None       # set when the session completed
    best_streak: int | None = None


def start_daily_session(user_id: int) -> SessionIntro:
    user = db.get_user(user_id)
    scenario = pick_scenario(user["level"], user["sessions_done"])
    db.start_session(user_id, scenario.id)
    text = (
        f"🏋️ *Өнөөдрийн дасгал: {scenario.title_mn}*\n\n"
        f"{scenario.setup_mn}\n\n"
        f"🗣 *Тамир:* {scenario.opener_en}\n\n"
        f"Хариугаа 🎤 *дуут мессежээр* илгээгээрэй "
        f"(бичиж болно, гэхдээ ярих нь илүү үр дүнтэй!)"
    )
    return SessionIntro(scenario=scenario, text_mn=text)


async def handle_turn(user_id: int, transcript: str) -> CoachReply:
    """Run one learner turn through the LLM coach; manage session lifecycle."""
    session = db.get_active_session(user_id)
    if session is None:
        # Auto-start: makes "just send a voice note" work with zero ceremony.
        start_daily_session(user_id)
        session = db.get_active_session(user_id)

    user = db.get_user(user_id)
    scenario = by_id(session["scenario_id"])
    turn = session["turns"] + 1
    max_turns = config.turns_per_session
    finish_hint = FINISH_HINT if turn >= max_turns else ""

    prompt = TURN_TEMPLATE.format(
        title=scenario.title_mn,
        opener=scenario.opener_en,
        focus=scenario.focus,
        level=user["level"],
        turn=turn,
        max_turns=max_turns,
        finish_hint=finish_hint,
        history=session["history"] or "(start of conversation)",
        transcript=transcript,
    )

    raw = await llm.chat(SYSTEM_PROMPT, prompt)
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
    )

    db.record_turn(user_id, f'Learner: "{transcript}" / Coach: "{reply.reply_en}"')

    if reply.done:
        streak, best = db.complete_session(user_id)
        reply.streak, reply.best_streak = streak, best
    return reply


def format_reply(reply: CoachReply, transcript: str) -> str:
    """Render a CoachReply as Markdown text (channel adapters may reuse this)."""
    parts = [f'🗣 *Таны хэлсэн:* _"{transcript}"_']
    if reply.corrected and reply.corrected.lower() != transcript.lower():
        parts.append(f'✅ *Зөв хувилбар:* "{reply.corrected}"')
    else:
        parts.append("✅ Маш зөв хэллээ!")
    if reply.feedback_mn:
        parts.append(f"💡 {reply.feedback_mn}")
    parts.append(f"📊 Оноо: *{reply.score}/100*")
    if reply.reply_en:
        parts.append(f"\n🗣 *Тамир:* {reply.reply_en}")
    if reply.done and reply.streak is not None:
        parts.append(
            f"\n🎉 *Өнөөдрийн дасгал дууслаа!* Стрик: 🔥 {reply.streak} өдөр"
            + (f" (дээд амжилт: {reply.best_streak})" if reply.best_streak else "")
            + "\nМаргааш мөн адил цагт уулзацгаая! 💪"
        )
    return "\n".join(parts)
