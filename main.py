"""Talking Gym — Telegram entrypoint.

Usage:
    cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN and XAI_API_KEY
    pip install -r requirements.txt
    python main.py
"""
import logging
import sys

from talking_gym import db
from talking_gym.config import config
from talking_gym.channels.telegram_bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    if not config.telegram_token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not config.xai_api_key:
        sys.exit("XAI_API_KEY is not set. Copy .env.example to .env and fill it in.")
    db.init_db()
    app = build_application()
    logging.info("Talking Gym is running (long polling). Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
