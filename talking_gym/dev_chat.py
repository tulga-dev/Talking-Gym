"""Console REPL to exercise the coaching engine without Telegram (and optionally
without any API key, using a canned mock LLM).

    python -m talking_gym.dev_chat            # real LLM (needs XAI_API_KEY)
    python -m talking_gym.dev_chat --mock     # offline, canned replies
"""
import asyncio
import json
import re
import sys

# Windows consoles often default to cp1252, which can't print Mongolian Cyrillic.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from . import coach, db
from .providers import llm

MOCK_REPLY = {
    "reply_en": "That sounds great! And what do you usually do on weekends?",
    "corrected": "I like playing basketball with my friends.",
    "feedback_mn": "«I like play» биш «I like playing» гэж хэлээрэй — like-ийн дараа үйл үг -ing авдаг.",
    "score": 78,
    "done": False,
    "suggested_en": "On weekends, I usually meet my friends and we watch movies together.",
    "suggested_mn": "Амралтын өдрөөр би ихэвчлэн найзуудтайгаа уулзаж, хамт кино үздэг.",
}


async def _mock_chat(system: str, user: str) -> str:
    reply = dict(MOCK_REPLY)
    if "final turn" in user or "Turn 3" in user:
        reply["done"] = True
        reply["reply_en"] = "Great job today — see you tomorrow!"
    return json.dumps(reply)


async def run() -> None:
    if "--mock" in sys.argv:
        llm.chat = _mock_chat  # type: ignore[assignment]
        print("[mock LLM enabled — no API calls]\n")

    db.init_db()
    user_id = 1
    db.get_or_create_user(user_id, chat_id=1, name="Dev")

    def plain(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text)

    intro = coach.start_daily_session(user_id)
    print(plain(intro.text_mn))
    print("\n(type your English answers; 'q' to quit)\n")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() == "q":
            break
        reply = await coach.handle_turn(user_id, text)
        print("\n" + plain(coach.format_reply(reply, text)) + "\n")
        if reply.done:
            break


if __name__ == "__main__":
    asyncio.run(run())
