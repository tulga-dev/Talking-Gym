"""Talking Gym — entrypoint.

Runs the aiohttp web server: landing page, installable PWA, and the /api
that powers it (register, sessions, voice/text turns via Grok).

The Telegram bot has been retired as a product surface (2026-07) — the PWA
is the focus. Set ENABLE_TELEGRAM=true to also run the legacy bot poller.

Usage:
    cp .env.example .env   # fill in XAI_API_KEY
    pip install -r requirements.txt
    python main.py
"""
import asyncio
import logging
import os
import signal
import sys

from talking_gym import db
from talking_gym.config import config
from talking_gym.channels.messenger import start_web_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "").strip().lower() in ("1", "true", "yes")


async def run() -> None:
    db.init_db()

    application = None
    if ENABLE_TELEGRAM and config.telegram_token:
        from talking_gym.channels.telegram_bot import build_application

        application = build_application()
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=["message", "callback_query"])
        logging.info("Legacy Telegram bot: polling")

    web_runner = await start_web_server()
    logging.info("Talking Gym is running (web + API%s). Ctrl+C to stop.",
                 " + telegram" if application else "")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    try:
        await stop.wait()
    finally:
        await web_runner.cleanup()
        if application:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()


def main() -> None:
    if not config.xai_api_key:
        sys.exit("XAI_API_KEY is not set. Copy .env.example to .env and fill it in.")
    if config.database_url:
        logging.info("Storage: Supabase/Postgres")
    else:
        logging.warning("Storage: local SQLite (%s) — fine for dev, data is lost on redeploy. "
                        "Set SUPABASE_DB_URL for production.", config.db_path)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
