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
        created_at    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS active_sessions (
        user_id      INTEGER PRIMARY KEY,
        scenario_id  TEXT,
        turns        INTEGER DEFAULT 0,
        history      TEXT DEFAULT '',
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
        created_at    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS active_sessions (
        user_id      BIGINT PRIMARY KEY,
        scenario_id  TEXT,
        turns        INTEGER DEFAULT 0,
        history      TEXT DEFAULT '',
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
