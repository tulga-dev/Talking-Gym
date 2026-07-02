"""Talking Gym — entrypoint.

Runs two things in one asyncio loop:
- the Telegram bot (long polling)
- a small aiohttp web server (health endpoint + Facebook Messenger webhook)

Usage:
    cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN and XAI_API_KEY
    pip install -r requirements.txt
    python main.py
"""
import asyncio
import logging
import signal
import sys

from talking_gym import db
from talking_gym.config import config
from talking_gym.channels.messenger import start_web_server
from talking_gym.channels.telegram_bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def run() -> None:
    db.init_db()

    application = build_application()
    await application.initialize()   # also runs post_init (command menu, profile texts)
    await application.start()
    await application.updater.start_polling(allowed_updates=["message", "callback_query"])

    web_runner = await start_web_server()

    logging.info("Talking Gym is running (Telegram polling + web server). Ctrl+C to stop.")

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
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def main() -> None:
    if not config.telegram_token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not config.xai_api_key:
        sys.exit("XAI_API_KEY is not set. Copy .env.example to .env and fill it in.")
    if config.database_url:
        logging.info("Storage: Supabase/Postgres")
    else:
        logging.warning("Storage: local SQLite (%s) — fine for dev, data is lost on redeploy. "
                        "Set SUPABASE_DB_URL for production.", config.db_path)
    if not config.messenger_enabled:
        logging.info("Messenger channel: not configured (set MESSENGER_PAGE_TOKEN to enable)")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
