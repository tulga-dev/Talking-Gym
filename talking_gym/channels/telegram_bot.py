"""Telegram channel adapter: Duolingo-style button UI, handlers, reminders, voice pipeline."""
import datetime as dt
import io
import logging

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .. import coach, db
from ..config import config
from ..providers import ProviderError
from ..providers import stt, tts

log = logging.getLogger(__name__)

LEVELS = {
    "beginner": "🌱 Анхан (A1–A2)",
    "intermediate": "🌿 Дунд (B1)",
    "advanced": "🌳 Ахисан (B2+)",
}

# Persistent big-button keyboard — users should never need to type a command.
BTN_TODAY = "🏋️ Өнөөдрийн дасгал"
BTN_PROGRESS = "🔥 Миний ахиц"
BTN_HELP = "❓ Тусламж"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_TODAY], [BTN_PROGRESS, BTN_HELP]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="🎤 Дуут мессежээр хариулаарай...",
)

NEXT_WORKOUT_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔁 Дахин дасгал хийх", callback_data="new_session")]]
)

WELCOME = (
    "Сайн байна уу, {name}! 👋\n\n"
    f"Би *{config.coach_name_mn}* — таны хувийн англи хэлний дасгалжуулагч. 🏋️\n\n"
    "*Яагаад Talking Gym гэж?*\n"
    "Хэл сурах нь биеийн тамирын заал шиг: өдөр бүр багахан дасгал → бодит ахиц. "
    "Өдөрт ердөө *5 минут* — гэхдээ өдөр бүр! 💪\n\n"
    "🎯 *Танд юу өгөх вэ:*\n"
    "• Өдөр бүр нэг бодит нөхцөлт яриа (кофе захиалах, ажлын ярилцлага...)\n"
    "• Дуут хариултад тань засвар + зөвлөгөө *монголоор*\n"
    "• Оноо, ⭐ XP, зэрэглэл, 🔥 стрик\n\n"
    "Эхлээд түвшнээ сонгоно уу: 👇"
)

QUICK_TIPS = (
    "📋 *Товч заавар:*\n"
    "1️⃣ Би нөхцөл байдал + 📝 *жишээ хариулт* өгнө\n"
    "2️⃣ Жишээг уншаад (өөрийн мэдээллээр өөрчилж болно) 🎤 *дуут мессежээр* хэлнэ\n"
    "3️⃣ Би засвар, зөвлөгөө, XP өгнө — 3 солилцоод дасгал дуусна\n\n"
    "Юу хэлэхээ мэдэхгүй бол жишээг *шууд уншаад л болно* — "
    "чанга унших нь өөрөө дасгал! 😊\n"
    "За, эхний дасгал: 👇"
)

GUIDE = (
    "📖 *Talking Gym — хэрэглэх заавар*\n\n"
    "🏋️ *Дасгал хэрхэн хийх вэ*\n"
    f"1. «{BTN_TODAY}» товчийг дарна\n"
    "2. Би нөхцөл байдал өгнө (жишээ: кафед кофе захиалах)\n"
    "3. Та дүрдээ орж, хариугаа 🎤 *дуут мессежээр* англиар хэлнэ\n"
    "    💡 Микрофоноо дараад 10–30 секунд яриарай\n"
    "4. Би хариу өгнө:\n"
    "    ✅ Зөв хувилбар — өгүүлбэрийн тань сайжруулсан хэлбэр\n"
    "    💡 Зөвлөгөө — монголоор, нэг л гол засвар\n"
    "    🟩 Оноо (0–100) + ⭐ XP\n"
    "    🔊 Дуут хариу — зөв хэллэгийг чихээрээ сонсоорой\n"
    "5. Гурван солилцоо = нэг дасгал. Өдөрт нэг дасгал л хангалттай!\n\n"
    "🔥 *Стрик ба XP*\n"
    "• Өдөр бүр дасгал хийвэл стрик өснө — нэг өдөр тасалвал 1-ээс дахин эхэлнэ\n"
    "• XP цуглуулж зэрэглэл ахина: 🌱 → 🥉 → 🥈 → 🥇 → 🏆 → 💎\n"
    f"• Ахицаа «{BTN_PROGRESS}» товчоор хараарай\n\n"
    "⚙️ *Тохиргоо*\n"
    "/remind 19 — өдөр бүр 19:00 цагт сануулна (/remind off — унтраана)\n"
    "/level — түвшнээ өөрчлөх\n"
    "/feedback [санал] — бидэнд санал хүсэлт илгээх\n\n"
    "❓ *Түгээмэл асуулт*\n"
    "• *Бичээд хариулж болох уу?* Болно, гэхдээ ярих нь хамаагүй илүү үр дүнтэй!\n"
    "• *Өдөрт хэд хийж болох вэ?* Хүссэн хэрээрээ — дуут дасгал өдрийн хязгаартай.\n"
    f"• *Буруу ярихаас эмээж байна...* Бүү ай! {config.coach_name_mn} хэзээ ч шүүмжлэхгүй, зөвхөн тусална. 💪"
)


REMIND_ASK = (
    "⏰ *Өдөр бүр хэдэн цагт сануулах вэ?*\n"
    "Стрикээ хадгалахад өдөр бүрийн сануулга их тусалдаг шүү! 🔥"
)

REMIND_HOURS = [(8, "🌅 08:00"), (13, "🌞 13:00"), (19, "🌆 19:00"), (21, "🌙 21:00")]


def _level_keyboard(onboarding: bool) -> InlineKeyboardMarkup:
    """'olevel:' buttons continue into full onboarding (reminder -> first lesson);
    'level:' buttons (from /level) just change the level."""
    prefix = "olevel" if onboarding else "level"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"{prefix}:{lv}")] for lv, label in LEVELS.items()]
    )


def _remind_keyboard() -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(label, callback_data=f"remindh_{h}") for h, label in REMIND_HOURS]
    return InlineKeyboardMarkup(
        [row[:2], row[2:], [InlineKeyboardButton("🔕 Сануулга хэрэггүй", callback_data="remindh_off")]]
    )


def _needs_onboarding(user) -> bool:
    """True for users who have never finished (or started) a session — they
    should pick a level first instead of silently defaulting to beginner."""
    return user["sessions_done"] == 0 and db.get_active_session(user["user_id"]) is None


async def _send_session_intro(chat, user_id: int) -> None:
    intro = coach.start_daily_session(user_id)
    await chat.send_message(intro.text_mn, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)


# ---------- commands & buttons ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db.get_or_create_user(u.id, update.effective_chat.id, u.first_name or "")
    await update.message.reply_text(
        WELCOME.format(name=u.first_name or ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_level_keyboard(onboarding=True),
    )


async def cmd_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Түвшнээ сонгоно уу:", reply_markup=_level_keyboard(onboarding=False))


async def on_level_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    prefix, level = query.data.split(":", 1)
    label = LEVELS[level]
    db.get_or_create_user(query.from_user.id, query.message.chat.id, query.from_user.first_name or "")
    db.set_level(query.from_user.id, level)
    await query.edit_message_text(f"Түвшин: {label} ✅")
    if prefix == "olevel":
        # Onboarding continues: pick the daily reminder time next.
        await query.message.chat.send_message(
            REMIND_ASK, parse_mode=ParseMode.MARKDOWN, reply_markup=_remind_keyboard()
        )
    # Plain /level change: done.


async def on_remind_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    db.get_or_create_user(query.from_user.id, query.message.chat.id, query.from_user.first_name or "")
    choice = query.data.split("_", 1)[1]
    if choice == "off":
        db.set_reminder_hour(query.from_user.id, None)
        await query.edit_message_text("🔕 За, сануулгагүй. Хэрэгтэй болбол: /remind 19")
    else:
        hour = int(choice)
        db.set_reminder_hour(query.from_user.id, hour)
        await query.edit_message_text(
            f"⏰ Болно! Өдөр бүр {hour:02d}:00 цагт сануулна. (Өөрчлөх: /remind)"
        )
    # Duolingo-style: a 3-line primer, then straight into the first workout.
    await query.message.chat.send_message(QUICK_TIPS, parse_mode=ParseMode.MARKDOWN)
    await _send_session_intro(query.message.chat, query.from_user.id)


async def on_new_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Шинэ дасгал! 🏋️")
    user = db.get_or_create_user(query.from_user.id, query.message.chat.id, query.from_user.first_name or "")
    if _needs_onboarding(user):
        await query.message.chat.send_message(
            "Эхлээд түвшнээ сонгоно уу:", reply_markup=_level_keyboard(onboarding=True)
        )
        return
    await _send_session_intro(query.message.chat, query.from_user.id)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, update.effective_chat.id, u.first_name or "")
    if _needs_onboarding(user):
        await update.message.reply_text(
            "Эхлээд түвшнээ сонгоно уу:", reply_markup=_level_keyboard(onboarding=True)
        )
        return
    if db.did_session_today(u.id) and db.get_active_session(u.id) is None:
        await update.message.reply_text(
            "Өнөөдрийнхөө дасгалыг хийчихсэн байна! 🎉 Нэмэлт дасгал — бүр ч сайн. 💪"
        )
    await _send_session_intro(update.effective_chat, u.id)


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, update.effective_chat.id, u.first_name or "")
    await update.message.reply_text(
        coach.progress_card(user), parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD
    )


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db.get_or_create_user(u.id, update.effective_chat.id, u.first_name or "")
    args = context.args or []
    if args and args[0].lower() in ("off", "unench", "untraah"):
        db.set_reminder_hour(u.id, None)
        await update.message.reply_text("Сануулгыг унтраалаа. Дахин асаах бол: /remind 19")
        return
    try:
        hour = int(args[0])
        assert 0 <= hour <= 23
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Жишээ: /remind 19  (өдөр бүр 19:00 цагт сануулна)")
        return
    db.set_reminder_hour(u.id, hour)
    await update.message.reply_text(f"За! Өдөр бүр {hour:02d}:00 цагт ({config.tz_name}) сануулга илгээнэ. ⏰")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(GUIDE, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KEYBOARD)


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db.get_or_create_user(u.id, update.effective_chat.id, u.first_name or "")
    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text(
            "Санал хүсэлтээ ингэж бичээрэй:\n/feedback Дуут хариу нь хэтэрхий удаан байна"
        )
        return
    db.save_feedback(u.id, u.first_name or "", text)
    await update.message.reply_text("Баярлалаа! 🙏 Таны санал бидэнд маш чухал.")
    if config.admin_chat_id:
        try:
            await context.bot.send_message(
                config.admin_chat_id,
                f"💬 Feedback — {u.first_name} (id {u.id}):\n{text}",
            )
        except Exception:
            log.warning("Could not forward feedback to admin chat")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Founder-only dashboard for watching testers."""
    if config.admin_chat_id is None or update.effective_user.id != config.admin_chat_id:
        return  # silently ignore for non-admins
    s = db.stats()
    await update.message.reply_text(
        "📈 *Talking Gym stats*\n"
        f"Users: {s['users']}\n"
        f"Sessions total: {s['sessions_total']}\n"
        f"Trained today: {s['trained_today']}\n"
        f"Voice today: {s['voice_seconds_today']}s\n"
        f"XP total: {s['xp_total']}\n"
        f"Feedback items: {s['feedback']}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- the voice / text turn pipeline ----------

async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db.get_or_create_user(u.id, update.effective_chat.id, u.first_name or "")
    voice = update.message.voice or update.message.audio
    if voice is None:
        return

    duration = int(getattr(voice, "duration", 0) or 0)
    used = db.voice_seconds_today(u.id)
    if used + duration > config.daily_voice_seconds_cap:
        await update.message.reply_text(
            "Өнөөдрийн дуут дасгалын хязгаарт хүрлээ. 🙌 Маргааш үргэлжлүүлье!\n"
            "(Бичгээр хариулж болно.)"
        )
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    tg_file = await voice.get_file()
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    audio_bytes = buf.getvalue()

    try:
        transcript = await stt.transcribe(audio_bytes)
    except ProviderError:
        await update.message.reply_text(
            "Уучлаарай, дууг тань таньж чадсангүй. 🙏 Дахин нэг илгээгээрэй?"
        )
        return

    db.add_voice_seconds(u.id, duration)

    if not transcript:
        await update.message.reply_text(
            "Дуут мессеж хоосон юм шиг байна — арай удаан, тод ярьж дахин илгээгээрэй. 🎤"
        )
        return

    await _run_turn(update, context, transcript, spoken=True)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db.get_or_create_user(u.id, update.effective_chat.id, u.first_name or "")
    text = (update.message.text or "").strip()
    if not text:
        return
    # Big-button routing (Duolingo-style: taps, not typed commands)
    if text == BTN_TODAY:
        await cmd_today(update, context)
        return
    if text == BTN_PROGRESS:
        await cmd_progress(update, context)
        return
    if text == BTN_HELP:
        await cmd_help(update, context)
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    await _run_turn(update, context, text, spoken=False)


async def _run_turn(update: Update, context: ContextTypes.DEFAULT_TYPE, transcript: str, spoken: bool) -> None:
    u = update.effective_user
    try:
        reply = await coach.handle_turn(u.id, transcript)
    except ProviderError:
        await update.message.reply_text(
            "Түр зуурын алдаа гарлаа. 🙏 Хэдэн секундын дараа дахин оролдоорой."
        )
        return

    await update.message.reply_text(
        coach.format_reply(reply, transcript),
        parse_mode=ParseMode.HTML,
        reply_markup=NEXT_WORKOUT_KEYBOARD if reply.done else None,
    )

    # Voice model answer: learner HEARS the corrected sentence + coach's next line.
    if config.tts_enabled and spoken:
        speak_text = ". ".join(x for x in (reply.corrected, reply.reply_en) if x)[:500]
        if speak_text:
            try:
                audio = await tts.speak(speak_text)
                # Voice note, NOT audio file: audio files land in Telegram's music
                # player, which queues every audio in the chat as a playlist.
                await update.message.reply_voice(voice=io.BytesIO(audio))
            except ProviderError:
                log.warning("TTS failed; text-only reply sent")


# ---------- daily reminders ----------

async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs hourly; nudges every user whose reminder hour is now and who hasn't trained today."""
    now_hour = dt.datetime.now(config.tz).hour
    for user in db.all_users_with_reminders():
        if user["reminder_hour"] == now_hour and not db.did_session_today(user["user_id"]):
            try:
                await context.bot.send_message(
                    user["chat_id"],
                    "🏋️ Англи хэлний дасгалын цаг боллоо!\n"
                    f"Стрикээ хадгалъя — 🔥 {user['streak']} өдөр.",
                    reply_markup=NEXT_WORKOUT_KEYBOARD,
                )
            except Exception:  # blocked bot, deleted chat, etc.
                log.info("Reminder failed for user %s", user["user_id"])


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error: %s", context.error)


async def _post_init(app: Application) -> None:
    """Populate the '/' command menu and the bot's profile texts."""
    await app.bot.set_my_commands(
        [
            BotCommand("today", "🏋️ Өнөөдрийн дасгал"),
            BotCommand("progress", "🔥 Миний ахиц"),
            BotCommand("guide", "📖 Хэрэглэх заавар"),
            BotCommand("level", "📶 Түвшнээ өөрчлөх"),
            BotCommand("remind", "⏰ Сануулгын цаг"),
            BotCommand("feedback", "💬 Санал хүсэлт"),
        ]
    )
    # Profile texts: what people see BEFORE pressing Start (empty chat + share card).
    # Telegram rate-limits changes to these — never let a failure block startup.
    try:
        await app.bot.set_my_short_description(
            "Хувийн AI англи хэлний дасгалжуулагч 🏋️ Өдөрт 5 минут ярианы дасгал — засвар, оноо, стрик."
        )
        await app.bot.set_my_description(
            f"Би {config.coach_name_mn} — таны хувийн англи хэлний дасгалжуулагч. 🏋️\n\n"
            "Өдөр бүр 5 минутын ярианы дасгал: би бодит нөхцөл байдал өгнө, "
            "та 🎤 дуут мессежээр англиар хариулна, би засвар, зөвлөгөө, оноо өгнө.\n\n"
            "🗣 Ярианы дадал — жинхэнэ нөхцөлт яриагаар\n"
            "💡 Зөвлөгөө монголоор\n"
            "🔊 Зөв хэллэгийг дуугаар сонсоно\n"
            "🔥 Стрик, XP, зэрэглэл\n\n"
            "«Start» дарж эхлээрэй!"
        )
    except Exception:
        log.info("Profile texts unchanged (rate-limited or identical)")


def build_application() -> Application:
    app = Application.builder().token(config.telegram_token).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler(["progress", "streak"], cmd_progress))
    app.add_handler(CommandHandler("level", cmd_level))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler(["help", "guide"], cmd_help))
    app.add_handler(CallbackQueryHandler(on_level_chosen, pattern=r"^o?level:"))
    app.add_handler(CallbackQueryHandler(on_remind_chosen, pattern=r"^remindh_"))
    app.add_handler(CallbackQueryHandler(on_new_session, pattern=r"^new_session$"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    # Hourly tick keeps per-user reminder hours simple (no per-user jobs to manage).
    app.job_queue.run_repeating(send_reminders, interval=3600, first=60)
    return app
