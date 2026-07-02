"""SQLite storage: users, streaks, sessions, daily voice usage."""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from .config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,        -- telegram user id (or synthetic id for other channels)
    chat_id       INTEGER,
    name          TEXT,
    level         TEXT DEFAULT 'beginner',    -- beginner | intermediate | advanced
    streak        INTEGER DEFAULT 0,
    best_streak   INTEGER DEFAULT 0,
    last_session_date TEXT,                   -- ISO date of last COMPLETED session (local tz)
    sessions_done INTEGER DEFAULT 0,
    reminder_hour INTEGER,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS active_sessions (
    user_id      INTEGER PRIMARY KEY,
    scenario_id  TEXT,
    turns        INTEGER DEFAULT 0,
    history      TEXT DEFAULT '',             -- compact transcript history for LLM context
    started_at   TEXT
);

CREATE TABLE IF NOT EXISTS voice_usage (
    user_id  INTEGER,
    day      TEXT,
    seconds  INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(config.db_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(_SCHEMA)


def _today() -> date:
    return datetime.now(config.tz).date()


# ---------- users ----------

def get_or_create_user(user_id: int, chat_id: int, name: str) -> sqlite3.Row:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row:
            if row["chat_id"] != chat_id:
                con.execute("UPDATE users SET chat_id=? WHERE user_id=?", (chat_id, user_id))
            return row
        con.execute(
            "INSERT INTO users (user_id, chat_id, name, reminder_hour, created_at) VALUES (?,?,?,?,?)",
            (user_id, chat_id, name, config.default_reminder_hour, datetime.utcnow().isoformat()),
        )
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_user(user_id: int) -> sqlite3.Row | None:
    with _conn() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def set_level(user_id: int, level: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET level=? WHERE user_id=?", (level, user_id))


def set_reminder_hour(user_id: int, hour: int | None) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET reminder_hour=? WHERE user_id=?", (hour, user_id))


def all_users_with_reminders() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM users WHERE reminder_hour IS NOT NULL").fetchall()


# ---------- sessions & streaks ----------

def get_active_session(user_id: int) -> sqlite3.Row | None:
    with _conn() as con:
        return con.execute("SELECT * FROM active_sessions WHERE user_id=?", (user_id,)).fetchone()


def start_session(user_id: int, scenario_id: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO active_sessions (user_id, scenario_id, turns, history, started_at) "
            "VALUES (?,?,0,'',?)",
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
            "UPDATE users SET streak=?, best_streak=?, last_session_date=?, sessions_done=sessions_done+1 "
            "WHERE user_id=?",
            (streak, best, today.isoformat(), user_id),
        )
        return streak, best


def did_session_today(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user["last_session_date"] == _today().isoformat())


# ---------- voice usage cap ----------

def add_voice_seconds(user_id: int, seconds: int) -> int:
    day = _today().isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO voice_usage (user_id, day, seconds) VALUES (?,?,?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET seconds = seconds + excluded.seconds",
            (user_id, day, seconds),
        )
        row = con.execute(
            "SELECT seconds FROM voice_usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        return row["seconds"]


def voice_seconds_today(user_id: int) -> int:
    day = _today().isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT seconds FROM voice_usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        return row["seconds"] if row else 0
