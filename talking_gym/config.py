"""Central configuration, loaded from environment / .env."""
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    xai_api_key: str = os.getenv("XAI_API_KEY", "")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.x.ai/v1")
    llm_model: str = os.getenv("LLM_MODEL", "grok-4.3")

    stt_url: str = os.getenv("STT_URL", "https://api.x.ai/v1/stt")
    stt_model: str = os.getenv("STT_MODEL", "grok-stt")
    stt_language: str = os.getenv("STT_LANGUAGE", "en")

    tts_enabled: bool = field(default_factory=lambda: _bool("TTS_ENABLED", True))
    tts_url: str = os.getenv("TTS_URL", "https://api.x.ai/v1/tts")
    tts_voice: str = os.getenv("TTS_VOICE", "ara")

    daily_voice_seconds_cap: int = int(os.getenv("DAILY_VOICE_SECONDS_CAP", "300"))
    turns_per_session: int = int(os.getenv("TURNS_PER_SESSION", "3"))
    default_reminder_hour: int = int(os.getenv("DEFAULT_REMINDER_HOUR", "19"))
    tz_name: str = os.getenv("TIMEZONE", "Asia/Ulaanbaatar")

    # Postgres (Supabase) when set; falls back to local SQLite otherwise.
    database_url: str = os.getenv("SUPABASE_DB_URL", "") or os.getenv("DATABASE_URL", "")
    db_path: str = os.getenv("DB_PATH", "talking_gym.db")

    # Founder chat: receives /feedback forwards, may call /stats.
    admin_chat_id: int | None = (
        int(os.getenv("ADMIN_CHAT_ID")) if os.getenv("ADMIN_CHAT_ID", "").strip() else None
    )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)


config = Config()
