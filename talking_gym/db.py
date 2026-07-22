"""Storage layer with two interchangeable backends:

- Supabase / any Postgres  -> set SUPABASE_DB_URL (or DATABASE_URL); used in production
  so data survives redeploys and all testers share one live database.
- SQLite (default)         -> zero-setup local development.

All queries are written with `?` placeholders and converted to `%s` for Postgres.
"""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import config

log = logging.getLogger(__name__)

IS_POSTGRES = bool(config.database_url)

_SQLITE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        user_id       INTEGER PRIMARY KEY,
        chat_id       INTEGER,
        name          TEXT,
        level         TEXT DEFAULT 'beginner',
        streak        INTEGER DEFAULT 0,
        best_streak   INTEGER DEFAULT 0,
        last_session_date TEXT,
        sessions_done INTEGER DEFAULT 0,
        reminder_hour INTEGER,
        xp            INTEGER DEFAULT 0,
        channel       TEXT DEFAULT 'telegram',
        track         TEXT DEFAULT 'business',
        target_lang   TEXT DEFAULT 'en',
        native_lang   TEXT DEFAULT 'mn',
        plan          TEXT DEFAULT 'free',
        plan_expires  TEXT,
        created_at    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS promo_codes (
        code        TEXT PRIMARY KEY,
        plan        TEXT DEFAULT 'gym',
        days        INTEGER DEFAULT 30,
        max_uses    INTEGER DEFAULT 1,
        used_count  INTEGER DEFAULT 0,
        created_at  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS active_sessions (
        user_id      INTEGER PRIMARY KEY,
        scenario_id  TEXT,
        turns        INTEGER DEFAULT 0,
        history      TEXT DEFAULT '',
        last_example TEXT DEFAULT '',
        started_at   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS voice_usage (
        user_id  INTEGER,
        day      TEXT,
        seconds  INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS feedback (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        name       TEXT,
        text       TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS auth_tokens (
        token      TEXT PRIMARY KEY,
        user_id    INTEGER,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS cache_kv (
        k          TEXT PRIMARY KEY,
        v          TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS password_resets (
        token      TEXT PRIMARY KEY,
        user_id    INTEGER,
        expires_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS vocab_assets (
        lang       TEXT,
        word       TEXT,
        mime       TEXT,
        data       BLOB,
        tts_b64    TEXT,
        created_at TEXT,
        PRIMARY KEY (lang, word)
    )""",
    """CREATE TABLE IF NOT EXISTS vocab_progress (
        user_id     INTEGER,
        lang        TEXT,
        word        TEXT,
        status      TEXT DEFAULT 'new',
        reps        INTEGER DEFAULT 0,
        updated_at  TEXT,
        PRIMARY KEY (user_id, lang, word)
    )""",
    """CREATE TABLE IF NOT EXISTS rt_usage (
        user_id  INTEGER,
        day      TEXT,
        seconds  INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )""",
]

# Telegram user/chat ids exceed 32-bit — BIGINT is required on Postgres.
_PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        user_id       BIGINT PRIMARY KEY,
        chat_id       BIGINT,
        name          TEXT,
        level         TEXT DEFAULT 'beginner',
        streak        INTEGER DEFAULT 0,
        best_streak   INTEGER DEFAULT 0,
        last_session_date TEXT,
        sessions_done INTEGER DEFAULT 0,
        reminder_hour INTEGER,
        xp            INTEGER DEFAULT 0,
        channel       TEXT DEFAULT 'telegram',
        track         TEXT DEFAULT 'business',
        target_lang   TEXT DEFAULT 'en',
        native_lang   TEXT DEFAULT 'mn',
        plan          TEXT DEFAULT 'free',
        plan_expires  TEXT,
        created_at    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS promo_codes (
        code        TEXT PRIMARY KEY,
        plan        TEXT DEFAULT 'gym',
        days        INTEGER DEFAULT 30,
        max_uses    INTEGER DEFAULT 1,
        used_count  INTEGER DEFAULT 0,
        created_at  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS active_sessions (
        user_id      BIGINT PRIMARY KEY,
        scenario_id  TEXT,
        turns        INTEGER DEFAULT 0,
        history      TEXT DEFAULT '',
        last_example TEXT DEFAULT '',
        started_at   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS voice_usage (
        user_id  BIGINT,
        day      TEXT,
        seconds  INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS feedback (
        id         BIGSERIAL PRIMARY KEY,
        user_id    BIGINT,
        name       TEXT,
        text       TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS auth_tokens (
        token      TEXT PRIMARY KEY,
        user_id    BIGINT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS cache_kv (
        k          TEXT PRIMARY KEY,
        v          TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS password_resets (
        token      TEXT PRIMARY KEY,
        user_id    BIGINT,
        expires_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS vocab_assets (
        lang       TEXT,
        word       TEXT,
        mime       TEXT,
        data       BYTEA,
        tts_b64    TEXT,
        created_at TEXT,
        PRIMARY KEY (lang, word)
    )""",
    """CREATE TABLE IF NOT EXISTS vocab_progress (
        user_id     BIGINT,
        lang        TEXT,
        word        TEXT,
        status      TEXT DEFAULT 'new',
        reps        INTEGER DEFAULT 0,
        updated_at  TEXT,
        PRIMARY KEY (user_id, lang, word)
    )""",
    """CREATE TABLE IF NOT EXISTS rt_usage (
        user_id  BIGINT,
        day      TEXT,
        seconds  INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )""",
]

_pool = None

# Supabase's dashboard offers Prisma-style URIs with query params libpq
# doesn't understand (e.g. ?pgbouncer=true) — strip them so any of the
# dashboard's connection strings works verbatim as SUPABASE_DB_URL.
_NON_LIBPQ_PARAMS = {"pgbouncer", "connection_limit", "pool_timeout", "schema"}


def _clean_dsn(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _NON_LIBPQ_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def _configure_pg(conn) -> None:
    # Supabase's transaction pooler (Supavisor/PgBouncer) doesn't support
    # server-side prepared statements.
    conn.prepare_threshold = None


def _get_pool():
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            conninfo=_clean_dsn(config.database_url),
            min_size=0,
            max_size=10,   # sized for concurrent_updates(64); DB ops are millisecond-scale
            kwargs={"row_factory": dict_row},
            configure=_configure_pg,
        )
    return _pool


class _PgAdapter:
    """Makes a psycopg connection look like sqlite3 for our queries."""

    def __init__(self, con):
        self._con = con

    def execute(self, sql: str, params=()):
        return self._con.execute(sql.replace("?", "%s"), params)


@contextmanager
def _conn():
    if IS_POSTGRES:
        with _get_pool().connection() as con:  # commits on clean exit
            yield _PgAdapter(con)
    else:
        con = sqlite3.connect(config.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


def init_db() -> None:
    schema = _PG_SCHEMA if IS_POSTGRES else _SQLITE_SCHEMA
    with _conn() as con:
        for statement in schema:
            con.execute(statement)
    _migrate()
    log.info("DB ready (backend: %s)", "postgres/supabase" if IS_POSTGRES else "sqlite")


def _migrate() -> None:
    """Additive migrations for databases created before a column existed."""
    statements = [
        "ALTER TABLE users ADD COLUMN {} xp INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN {} channel TEXT DEFAULT 'telegram'",
        "ALTER TABLE users ADD COLUMN {} track TEXT DEFAULT 'business'",
        "ALTER TABLE users ADD COLUMN {} target_lang TEXT DEFAULT 'en'",
        "ALTER TABLE users ADD COLUMN {} native_lang TEXT DEFAULT 'mn'",
        "ALTER TABLE users ADD COLUMN {} profile_note TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN {} plan TEXT DEFAULT 'free'",
        "ALTER TABLE users ADD COLUMN {} plan_expires TEXT",
        "ALTER TABLE active_sessions ADD COLUMN {} last_example TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN {} email TEXT",
        "ALTER TABLE users ADD COLUMN {} password_hash TEXT",
        "ALTER TABLE users ADD COLUMN {} google_sub TEXT",
    ]
    for template in statements:
        if IS_POSTGRES:
            with _conn() as con:
                con.execute(template.format("IF NOT EXISTS"))
        else:
            try:
                with _conn() as con:
                    con.execute(template.format(""))
            except sqlite3.OperationalError:
                pass  # column already exists


def _today() -> date:
    return datetime.now(config.tz).date()


# ---------- users ----------

def get_or_create_user(user_id: int, chat_id: int, name: str, channel: str = "telegram"):
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row:
            if row["chat_id"] != chat_id:
                con.execute("UPDATE users SET chat_id=? WHERE user_id=?", (chat_id, user_id))
            return row
        con.execute(
            "INSERT INTO users (user_id, chat_id, name, reminder_hour, channel, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, chat_id, name, config.default_reminder_hour, channel,
             datetime.utcnow().isoformat()),
        )
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_user(user_id: int):
    with _conn() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def set_level(user_id: int, level: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET level=? WHERE user_id=?", (level, user_id))


def set_track(user_id: int, track: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET track=? WHERE user_id=?", (track, user_id))


def set_target_lang(user_id: int, target_lang: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET target_lang=? WHERE user_id=?", (target_lang, user_id))


def set_native_lang(user_id: int, native_lang: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET native_lang=? WHERE user_id=?", (native_lang, user_id))


def set_profile_note(user_id: int, note: str) -> None:
    """Sarah's memory of the learner (job, interests, goals, weak points)."""
    with _conn() as con:
        con.execute("UPDATE users SET profile_note=? WHERE user_id=?", (note[:1500], user_id))


# ---------- generated-content cache (survives deploys) ----------

def cache_get(key: str) -> str | None:
    with _conn() as con:
        row = con.execute("SELECT v FROM cache_kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else None


def cache_set(key: str, value: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO cache_kv (k, v, created_at) VALUES (?,?,?) "
            "ON CONFLICT (k) DO UPDATE SET v=excluded.v, created_at=excluded.created_at",
            (key, value, datetime.utcnow().isoformat()),
        )


# ---------- vocabulary assets (image + audio) and per-learner progress ----------

def vocab_asset_get(lang: str, word: str):
    """Row with mime, data (bytes), tts_b64 for a word, or None if not generated."""
    with _conn() as con:
        row = con.execute(
            "SELECT mime, data, tts_b64 FROM vocab_assets WHERE lang=? AND word=?",
            (lang, word),
        ).fetchone()
        return row


def vocab_asset_set_image(lang: str, word: str, mime: str, data: bytes) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO vocab_assets (lang, word, mime, data, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT (lang, word) DO UPDATE SET mime=excluded.mime, data=excluded.data",
            (lang, word, mime, data, datetime.utcnow().isoformat()),
        )


def vocab_asset_set_tts(lang: str, word: str, tts_b64: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO vocab_assets (lang, word, tts_b64, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT (lang, word) DO UPDATE SET tts_b64=excluded.tts_b64",
            (lang, word, tts_b64, datetime.utcnow().isoformat()),
        )


def vocab_progress_map(user_id: int, lang: str) -> dict:
    """word -> status for everything this learner has interacted with."""
    with _conn() as con:
        rows = con.execute(
            "SELECT word, status FROM vocab_progress WHERE user_id=? AND lang=?",
            (user_id, lang),
        ).fetchall()
        return {r["word"]: r["status"] for r in rows}


def vocab_learned(user_id: int, lang: str, limit: int = 100) -> list[str]:
    """Words this learner has marked known, most recent first."""
    with _conn() as con:
        rows = con.execute(
            "SELECT word FROM vocab_progress WHERE user_id=? AND lang=? AND status='known' "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, lang, limit),
        ).fetchall()
        return [r["word"] for r in rows]


def vocab_learned_count(user_id: int, lang: str) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM vocab_progress WHERE user_id=? AND lang=? AND status='known'",
            (user_id, lang),
        ).fetchone()
        return int(row["n"]) if row else 0


def vocab_progress_set(user_id: int, lang: str, word: str, status: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO vocab_progress (user_id, lang, word, status, reps, updated_at) "
            "VALUES (?,?,?,?,1,?) "
            "ON CONFLICT (user_id, lang, word) DO UPDATE SET "
            "status=excluded.status, reps=vocab_progress.reps+1, updated_at=excluded.updated_at",
            (user_id, lang, word, status, datetime.utcnow().isoformat()),
        )


# ---------- plans / promo codes ----------

def set_plan(user_id: int, plan: str, days: int | None = 30) -> str | None:
    """Set a user's plan. days=None (or 0) = never expires. Returns the
    expiry ISO date (or None for lifetime)."""
    expires = None
    if days:
        expires = (_today() + timedelta(days=days)).isoformat()
    with _conn() as con:
        con.execute("UPDATE users SET plan=?, plan_expires=? WHERE user_id=?",
                    (plan, expires, user_id))
    return expires


def create_promo_code(code: str, plan: str, days: int, max_uses: int) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO promo_codes (code, plan, days, max_uses, used_count, created_at) "
            "VALUES (?,?,?,?,0,?)",
            (code, plan, days, max_uses, datetime.utcnow().isoformat()),
        )


def redeem_promo_code(user_id: int, code: str) -> dict | None:
    """Redeem a code for the user. Returns {plan, days} on success, else None."""
    with _conn() as con:
        row = con.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
        if row is None:
            return None
        if row["max_uses"] > 0 and row["used_count"] >= row["max_uses"]:
            return None
        con.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code=?", (code,))
    set_plan(user_id, row["plan"], row["days"] or None)
    return {"plan": row["plan"], "days": row["days"]}


def set_auth(user_id: int, email: str | None = None, password_hash: str | None = None,
             google_sub: str | None = None) -> None:
    with _conn() as con:
        if email is not None:
            con.execute("UPDATE users SET email=? WHERE user_id=?", (email, user_id))
        if password_hash is not None:
            con.execute("UPDATE users SET password_hash=? WHERE user_id=?", (password_hash, user_id))
        if google_sub is not None:
            con.execute("UPDATE users SET google_sub=? WHERE user_id=?", (google_sub, user_id))


def create_reset(token: str, user_id: int, expires_at: str) -> None:
    with _conn() as con:
        con.execute("INSERT INTO password_resets (token, user_id, expires_at) VALUES (?,?,?)",
                    (token, user_id, expires_at))


def user_by_reset(token: str):
    """Return the user for a valid, unexpired reset token, else None."""
    with _conn() as con:
        row = con.execute("SELECT user_id, expires_at FROM password_resets WHERE token=?",
                          (token,)).fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < datetime.utcnow().isoformat():
            return None
        return get_user(row["user_id"])


def delete_reset(token: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM password_resets WHERE token=?", (token,))


def recent_email_accounts(limit: int = 40):
    """Founder support: recent email/password accounts (newest first)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT user_id, name, email, password_hash, google_sub, created_at "
            "FROM users WHERE email IS NOT NULL AND email != '' "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def user_by_email(email: str):
    with _conn() as con:
        return con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()


def user_by_google_sub(sub: str):
    with _conn() as con:
        return con.execute("SELECT * FROM users WHERE google_sub=?", (sub,)).fetchone()


def create_token(token: str, user_id: int) -> None:
    with _conn() as con:
        con.execute("INSERT INTO auth_tokens (token, user_id, created_at) VALUES (?,?,?)",
                    (token, user_id, datetime.utcnow().isoformat()))


def user_by_token(token: str):
    with _conn() as con:
        row = con.execute("SELECT user_id FROM auth_tokens WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        return con.execute("SELECT * FROM users WHERE user_id=?", (row["user_id"],)).fetchone()


def delete_token(token: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM auth_tokens WHERE token=?", (token,))


def set_reminder_hour(user_id: int, hour: int | None) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET reminder_hour=? WHERE user_id=?", (hour, user_id))


def all_users_with_reminders() -> list:
    with _conn() as con:
        return con.execute("SELECT * FROM users WHERE reminder_hour IS NOT NULL").fetchall()


# ---------- sessions & streaks ----------

def get_active_session(user_id: int):
    with _conn() as con:
        return con.execute("SELECT * FROM active_sessions WHERE user_id=?", (user_id,)).fetchone()


def start_session(user_id: int, scenario_id: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO active_sessions (user_id, scenario_id, turns, history, started_at) "
            "VALUES (?,?,0,'',?) "
            "ON CONFLICT (user_id) DO UPDATE SET scenario_id=excluded.scenario_id, "
            "turns=0, history='', started_at=excluded.started_at",
            (user_id, scenario_id, datetime.utcnow().isoformat()),
        )


def set_last_example(user_id: int, example: str) -> None:
    """Remember the example answer the coach just offered — used to recognize
    when the learner reads it aloud (and STT slightly mishears it)."""
    with _conn() as con:
        con.execute("UPDATE active_sessions SET last_example=? WHERE user_id=?",
                    (example or "", user_id))


def record_note(user_id: int, history_line: str) -> None:
    """Append to conversation history WITHOUT consuming a turn (mic tests etc.)."""
    with _conn() as con:
        con.execute("UPDATE active_sessions SET history = history || ? WHERE user_id=?",
                    (history_line + "\n", user_id))


def record_turn(user_id: int, history_line: str) -> int:
    """Increment turn counter, append to history; returns new turn count."""
    with _conn() as con:
        con.execute(
            "UPDATE active_sessions SET turns = turns + 1, history = history || ? WHERE user_id=?",
            (history_line + "\n", user_id),
        )
        row = con.execute("SELECT turns FROM active_sessions WHERE user_id=?", (user_id,)).fetchone()
        return row["turns"] if row else 0


def complete_session(user_id: int) -> tuple[int, int]:
    """Close the active session and update the streak. Returns (streak, best_streak)."""
    today = _today()
    with _conn() as con:
        con.execute("DELETE FROM active_sessions WHERE user_id=?", (user_id,))
        user = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        last = date.fromisoformat(user["last_session_date"]) if user["last_session_date"] else None
        if last == today:
            streak = user["streak"]
        elif last == today - timedelta(days=1):
            streak = user["streak"] + 1
        else:
            streak = 1
        best = max(streak, user["best_streak"])
        con.execute(
            "UPDATE users SET streak=?, best_streak=?, last_session_date=?, "
            "sessions_done=sessions_done+1 WHERE user_id=?",
            (streak, best, today.isoformat(), user_id),
        )
        return streak, best


def did_session_today(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user["last_session_date"] == _today().isoformat())


# ---------- voice usage cap ----------

def add_voice_seconds(user_id: int, seconds: int) -> int:
    """Portable read-then-write (per-user messages are sequential, so no race in practice)."""
    day = _today().isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT seconds FROM voice_usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        if row:
            total = row["seconds"] + seconds
            con.execute(
                "UPDATE voice_usage SET seconds=? WHERE user_id=? AND day=?", (total, user_id, day)
            )
        else:
            total = seconds
            con.execute(
                "INSERT INTO voice_usage (user_id, day, seconds) VALUES (?,?,?)",
                (user_id, day, seconds),
            )
        return total


def voice_seconds_today(user_id: int) -> int:
    day = _today().isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT seconds FROM voice_usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        return row["seconds"] if row else 0


# ---------- live-call usage (separate daily budget; applies to everyone) ----------

def rt_seconds_today(user_id: int) -> int:
    day = _today().isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT seconds FROM rt_usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        return row["seconds"] if row else 0


def add_rt_seconds(user_id: int, seconds: int) -> int:
    day = _today().isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT seconds FROM rt_usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        if row:
            total = row["seconds"] + seconds
            con.execute(
                "UPDATE rt_usage SET seconds=? WHERE user_id=? AND day=?", (total, user_id, day)
            )
        else:
            total = seconds
            con.execute(
                "INSERT INTO rt_usage (user_id, day, seconds) VALUES (?,?,?)",
                (user_id, day, seconds),
            )
        return total


def add_xp(user_id: int, amount: int) -> int:
    """Add XP; returns the new total."""
    with _conn() as con:
        con.execute("UPDATE users SET xp = xp + ? WHERE user_id=?", (amount, user_id))
        row = con.execute("SELECT xp FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["xp"] if row else 0


# ---------- tester feedback & founder stats ----------

def save_feedback(user_id: int, name: str, text: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO feedback (user_id, name, text, created_at) VALUES (?,?,?,?)",
            (user_id, name, text, datetime.utcnow().isoformat()),
        )


def stats() -> dict:
    today = _today().isoformat()
    with _conn() as con:
        users = con.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        sessions = con.execute("SELECT COALESCE(SUM(sessions_done),0) AS c FROM users").fetchone()["c"]
        trained_today = con.execute(
            "SELECT COUNT(*) AS c FROM users WHERE last_session_date=?", (today,)
        ).fetchone()["c"]
        voice_today = con.execute(
            "SELECT COALESCE(SUM(seconds),0) AS c FROM voice_usage WHERE day=?", (today,)
        ).fetchone()["c"]
        feedback_count = con.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"]
        xp_total = con.execute("SELECT COALESCE(SUM(xp),0) AS c FROM users").fetchone()["c"]
    return {
        "users": users,
        "sessions_total": sessions,
        "trained_today": trained_today,
        "voice_seconds_today": voice_today,
        "feedback": feedback_count,
        "xp_total": xp_total,
    }
