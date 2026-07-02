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
    suggested_en: str = ""          # model answer for the coach's next question
    suggested_mn: str = ""          # its Mongolian meaning
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


def start_daily_session(user_id: int) -> SessionIntro:
    user = db.get_user(user_id)
    scenario = pick_scenario(user["level"], user["sessions_done"])
    db.start_session(user_id, scenario.id)

    parts = [
        f"🏋️ *Өнөөдрийн дасгал: {scenario.title_mn}*",
        f"\n📖 {scenario.setup_mn}",
        f"\n💬 *{config.coach_name_mn}:* {scenario.opener_en}",
        f"🇲🇳 _{scenario.opener_mn}_",
    ]
    if user["level"] != "advanced":
        # Beginners must never face a question they can't answer:
        # give a model answer to read aloud and adapt.
        parts.append(
            f"\n📝 *Жишээ хариулт* — уншаад, өөрийн мэдээллээр өөрчлөөрэй:\n`{scenario.example_en}`"
        )
        parts.append(f"🇲🇳 _{scenario.example_mn}_")
        parts.append(
            "\n🎤 Одоо дуут мессежээр хэлээрэй! Жишээг шууд уншсан ч болно — "
            "чанга уншина гэдэг чинь дасгал шүү! 💪"
        )
    else:
        parts.append("\n🎤 Хариугаа дуут мессежээр илгээгээрэй!")
    return SessionIntro(scenario=scenario, text_mn="\n".join(parts))


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
        suggested_en=str(data.get("suggested_en", "")).strip(),
        suggested_mn=str(data.get("suggested_mn", "")).strip(),
        turn_no=turn,
        max_turns=max_turns,
    )

    db.record_turn(user_id, f'Learner: "{transcript}" / Coach: "{reply.reply_en}"')

    reply.xp_earned = xp_for_turn(reply.score, reply.done)
    db.add_xp(user_id, reply.xp_earned)

    if reply.done:
        streak, best = db.complete_session(user_id)
        reply.streak, reply.best_streak = streak, best
    return reply


def format_reply(reply: CoachReply, transcript: str) -> str:
    """Render a CoachReply as Markdown text (channel adapters may reuse this)."""
    parts = [f"{turn_dots(reply.turn_no, reply.max_turns)}  _{reply.turn_no}/{reply.max_turns}_\n"]
    parts.append(f'🗣 *Таны хэлсэн:* _"{transcript}"_')
    if reply.corrected and reply.corrected.lower() != transcript.lower():
        parts.append(f'✅ *Зөв хувилбар:* "{reply.corrected}"')
    else:
        parts.append("✅ Маш зөв хэллээ!")
    if reply.feedback_mn:
        parts.append(f"💡 {reply.feedback_mn}")
    parts.append(f"{score_bar(reply.score)}  *{reply.score}*  ⭐ +{reply.xp_earned} XP")
    if reply.reply_en and not reply.done:
        parts.append(f"\n💬 *{config.coach_name_mn}:* {reply.reply_en}")
        if reply.suggested_en:
            # backticks inside a code span would break Markdown parsing
            parts.append(f"📝 *Жишээ:*\n`{reply.suggested_en.replace('`', chr(39))}`")
            if reply.suggested_mn:
                parts.append(f"🇲🇳 _{reply.suggested_mn}_")
    if reply.done:
        if reply.reply_en:
            parts.append(f"\n💬 *{config.coach_name_mn}:* {reply.reply_en}")
        parts.append(f"\n🎉 *Өнөөдрийн дасгал дууслаа!*")
        if reply.streak is not None:
            line = f"🔥 Стрик: *{reply.streak} өдөр*"
            if reply.best_streak and reply.streak >= reply.best_streak and reply.streak > 1:
                line += " — шинэ дээд амжилт! 🏆"
            parts.append(line)
            if reply.streak in STREAK_MILESTONES:
                parts.append(f"🏅 *{reply.streak} хоногийн стрик!* Гайхалтай тууштай байна! 👏")
    return "\n".join(parts)


def progress_card(user) -> str:
    """Duolingo-style profile/progress card."""
    xp = user["xp"] or 0
    rank, cur, nxt = rank_for(xp)
    lines = [f"🏅 *Зэрэглэл:* {rank}"]
    if nxt is not None:
        lines.append(f"{progress_bar(xp - cur, nxt - cur)}  {xp}/{nxt} XP")
    else:
        lines.append(f"⭐ {xp} XP — дээд зэрэглэл!")
    lines.append("")
    lines.append(f"🔥 Стрик: *{user['streak']} өдөр*  (дээд: {user['best_streak']})")
    lines.append(f"✅ Нийт дасгал: {user['sessions_done']}")
    return "\n".join(lines)
