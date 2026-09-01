"""SQLite-индекс сообщений и реакций."""

import sqlite3

from guard import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS escalations (
    channel_id         TEXT    NOT NULL,
    ts                 TEXT    NOT NULL,
    user_id            TEXT    NOT NULL,
    created_at         REAL    NOT NULL,
    player_id          TEXT,
    transaction_id     TEXT,
    text               TEXT    NOT NULL DEFAULT '',
    duplicate_notified INTEGER NOT NULL DEFAULT 0,
    stale_notified     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (channel_id, ts)
);

CREATE INDEX IF NOT EXISTS idx_escalations_transaction
    ON escalations (channel_id, transaction_id, created_at);
CREATE INDEX IF NOT EXISTS idx_escalations_player
    ON escalations (channel_id, player_id, created_at);
CREATE INDEX IF NOT EXISTS idx_escalations_created
    ON escalations (channel_id, created_at);

CREATE TABLE IF NOT EXISTS reactions (
    channel_id TEXT NOT NULL,
    ts         TEXT NOT NULL,
    name       TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    PRIMARY KEY (channel_id, ts, name, user_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Текст режется, чтобы база не росла на скриншотах и простынях.
MAX_TEXT_LENGTH = 2000


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_escalation(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    ts: str,
    user_id: str,
    created_at: float,
    player_id: str | None,
    transaction_id: str | None,
    text: str,
) -> None:
    """Вставить или обновить эскалацию, не трогая флаги уведомлений."""
    conn.execute(
        """
        INSERT INTO escalations
            (channel_id, ts, user_id, created_at, player_id, transaction_id, text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (channel_id, ts) DO UPDATE SET
            user_id        = excluded.user_id,
            created_at     = excluded.created_at,
            player_id      = excluded.player_id,
            transaction_id = excluded.transaction_id,
            text           = excluded.text
        """,
        (channel_id, ts, user_id, created_at, player_id, transaction_id, text[:MAX_TEXT_LENGTH]),
    )
    conn.commit()


def get_escalation(conn: sqlite3.Connection, channel_id: str, ts: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM escalations WHERE channel_id = ? AND ts = ?",
        (channel_id, ts),
    ).fetchone()


def delete_escalation(conn: sqlite3.Connection, channel_id: str, ts: str) -> None:
    conn.execute("DELETE FROM escalations WHERE channel_id = ? AND ts = ?", (channel_id, ts))
    conn.execute("DELETE FROM reactions WHERE channel_id = ? AND ts = ?", (channel_id, ts))
    conn.commit()


def mark_duplicate_notified(conn: sqlite3.Connection, channel_id: str, ts: str) -> None:
    conn.execute(
        "UPDATE escalations SET duplicate_notified = 1 WHERE channel_id = ? AND ts = ?",
        (channel_id, ts),
    )
    conn.commit()


def mark_stale_notified(conn: sqlite3.Connection, channel_id: str, ts: str) -> None:
    conn.execute(
        "UPDATE escalations SET stale_notified = 1 WHERE channel_id = ? AND ts = ?",
        (channel_id, ts),
    )
    conn.commit()


def is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM escalations LIMIT 1").fetchone()
    return row is None


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def add_reaction(conn: sqlite3.Connection, channel_id: str, ts: str, name: str, user_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO reactions (channel_id, ts, name, user_id) VALUES (?, ?, ?, ?)",
        (channel_id, ts, name, user_id),
    )
    conn.commit()


def remove_reaction(conn: sqlite3.Connection, channel_id: str, ts: str, name: str, user_id: str) -> None:
    conn.execute(
        "DELETE FROM reactions WHERE channel_id = ? AND ts = ? AND name = ? AND user_id = ?",
        (channel_id, ts, name, user_id),
    )
    conn.commit()


def reaction_names(conn: sqlite3.Connection, channel_id: str, ts: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT name FROM reactions WHERE channel_id = ? AND ts = ?",
        (channel_id, ts),
    ).fetchall()
    return {row["name"] for row in rows}


def users_with_reaction(
    conn: sqlite3.Connection, channel_id: str, ts: str, names: frozenset[str]
) -> list[str]:
    if not names:
        return []
    placeholders = ",".join("?" for _ in names)
    # GROUP BY, а не DISTINCT: SQLite не даёт сортировать DISTINCT-выборку
    # по колонке, которой нет в списке выбора.
    rows = conn.execute(
        f"SELECT user_id, MIN(rowid) AS first_seen FROM reactions "
        f"WHERE channel_id = ? AND ts = ? AND name IN ({placeholders}) "
        f"GROUP BY user_id ORDER BY first_seen",
        (channel_id, ts, *sorted(names)),
    ).fetchall()
    return [row["user_id"] for row in rows]


def is_resolved(conn: sqlite3.Connection, channel_id: str, ts: str) -> bool:
    return bool(reaction_names(conn, channel_id, ts) & set(config.RESOLVED_EMOJI))
