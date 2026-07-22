"""Central configuration, loaded from environment / .env."""
import logging
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _admin_chat_id() -> int | None:
    """Optional feature — a malformed value must never crash the bot."""
    raw = os.getenv("ADMIN_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "ADMIN_CHAT_ID=%r is not a numeric Telegram id — admin features disabled. "
            "Send a message to @userinfobot to get your numeric id.", raw
        )
        return None


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    xai_api_key: str = os.getenv("XAI_API_KEY", "")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.x.ai/v1")
    llm_model: str = os.getenv("LLM_MODEL", "grok-4.3")
    # Alternate Grok tier, selectable per turn (A/B and the in-app switcher).
    llm_model_45: str = os.getenv("LLM_MODEL_45", "grok-4.5")

    stt_url: str = os.getenv("STT_URL", "https://api.x.ai/v1/stt")
    stt_model: str = os.getenv("STT_MODEL", "grok-stt")
    stt_language: str = os.getenv("STT_LANGUAGE", "en")

    # "none" skips grok-4.3's thinking phase — coaching JSON doesn't need it.
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "none")

    # OpenAI — GPT-5.6 Luna as an alternate live-turn backend; realtime probe.
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_realtime_model: str = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-1.5")
    # Realtime voice for Kitty. marin = bright female (OpenAI's recommended
    # female voice); other options: shimmer, coral, sage, cedar (male), ...
    openai_realtime_voice: str = os.getenv("OPENAI_REALTIME_VOICE", "marin")
    # Transcribes the learner's speech so the call shows both sides as text.
    openai_transcribe_model: str = os.getenv("OPENAI_TRANSCRIBE_MODEL",
                                             "gpt-4o-mini-transcribe")

    # Google Gemini — alternate LLM backend learners can switch to mid-session.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    gemini_image_model: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    gemini_base_url: str = os.getenv(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    # Gemini flash "thinks" by default (~700 tokens/turn, ~4s slower). Live
    # coaching turns don't need it — 0 disables, matching Grok's effort="none".
    gemini_thinking_budget: int = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))

    tts_enabled: bool = field(default_factory=lambda: _bool("TTS_ENABLED", True))
    tts_url: str = os.getenv("TTS_URL", "https://api.x.ai/v1/tts")
    tts_voice: str = os.getenv("TTS_VOICE", "ara")
    # Base speech speed; per-level speeds in web_api scale learners down further.
    tts_speed: float = float(os.getenv("TTS_SPEED", "1.0"))

    daily_voice_seconds_cap: int = int(os.getenv("DAILY_VOICE_SECONDS_CAP", "1500"))
    # Comma-separated user ids exempt from the voice cap (founder/test accounts).
    # The founder's Telegram id and email-login account id are baked in;
    # FOUNDER_IDS extends the set.
    founder_ids: frozenset = field(default_factory=lambda: frozenset(
        int(x) for x in os.getenv("FOUNDER_IDS", "").split(",") if x.strip().isdigit()
    ) | {4688200350224636253, 8072857934932995731})
    turns_per_session: int = int(os.getenv("TURNS_PER_SESSION", "5"))
    default_reminder_hour: int = int(os.getenv("DEFAULT_REMINDER_HOUR", "19"))
    tz_name: str = os.getenv("TIMEZONE", "Asia/Ulaanbaatar")

    # Postgres (Supabase) when set; falls back to local SQLite otherwise.
    database_url: str = os.getenv("SUPABASE_DB_URL", "") or os.getenv("DATABASE_URL", "")
    db_path: str = os.getenv("DB_PATH", "talking_gym.db")

    # Founder chat: receives /feedback forwards, may call /stats.
    admin_chat_id: int | None = field(default_factory=_admin_chat_id)

    # Coach persona name (English + Mongolian Cyrillic rendering).
    coach_name_en: str = os.getenv("COACH_NAME_EN", "Kitty")
    coach_name_mn: str = os.getenv("COACH_NAME_MN", "Китти")

    # --- Facebook Messenger channel (optional; enabled when page token is set) ---
    messenger_page_token: str = os.getenv("MESSENGER_PAGE_TOKEN", "")
    messenger_verify_token: str = os.getenv("MESSENGER_VERIFY_TOKEN", "talking-gym-verify")
    messenger_app_secret: str = os.getenv("MESSENGER_APP_SECRET", "")
    graph_api_version: str = os.getenv("GRAPH_API_VERSION", "v21.0")
    web_port: int = int(os.getenv("PORT", "8080"))

    @property
    def messenger_enabled(self) -> bool:
        return bool(self.messenger_page_token)

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)


config = Config()
