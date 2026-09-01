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

# Сколько ждать снятия блокировки, прежде чем sqlite3 вернёт "database is locked".
BUSY_TIMEOUT_MS = 5000


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None -> автокоммит: каждый execute() это своя транзакция,
    # что убирает связку между потоком обработчика и потоком sweeper'а через
    # общее соединение. Функции с несколькими операторами сами оборачивают
    # себя в BEGIN/COMMIT там, где нужна атомарность.
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
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
    # BEGIN/COMMIT явно: под автокоммитом (isolation_level=None) каждый execute()
    # иначе был бы отдельной транзакцией, и конкурентный читатель мог бы увидеть
    # эскалацию уже без реакций, а потом ещё раз без самой эскалации, или наоборот.
    conn.execute("BEGIN")
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


MATCHABLE_FIELDS = frozenset({"transaction_id", "player_id"})


def find_duplicates(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    field: str,
    value: str,
    exclude_ts: str,
    since: float,
    before: float = float("inf"),
) -> list[sqlite3.Row]:
    """Совпадения по одному полю в том же канале за окно, свежие первыми.

    `before` не даёт заглянуть вперёд во времени: "was already escalated"
    должно указывать на что-то более раннее, чем проверяемое сообщение.
    """
    if field not in MATCHABLE_FIELDS:
        raise ValueError(f"Unsupported match field: {field}")

    return conn.execute(
        f"SELECT * FROM escalations "
        f"WHERE channel_id = ? AND {field} = ? AND ts != ? "
        f"  AND created_at >= ? AND created_at < ? "
        f"ORDER BY created_at DESC",
        (channel_id, value, exclude_ts, since, before),
    ).fetchall()


_UNRESOLVED_CLAUSE = """
    NOT EXISTS (
        SELECT 1 FROM reactions r
        WHERE r.channel_id = e.channel_id
          AND r.ts = e.ts
          AND r.name IN ({placeholders})
    )
"""


def find_stale(
    conn: sqlite3.Connection, *, channel_id: str, older_than: float, newer_than: float
) -> list[sqlite3.Row]:
    """Незакрытые эскалации старше порога, но внутри retention. Старые первыми."""
    resolved = sorted(config.RESOLVED_EMOJI)
    placeholders = ",".join("?" for _ in resolved)
    clause = _UNRESOLVED_CLAUSE.format(placeholders=placeholders)

    return conn.execute(
        f"SELECT * FROM escalations e "
        f"WHERE e.channel_id = ? AND e.stale_notified = 0 "
        f"  AND e.created_at <= ? AND e.created_at >= ? AND {clause} "
        f"ORDER BY e.created_at ASC",
        (channel_id, older_than, newer_than, *resolved),
    ).fetchall()


def silence_old_stale(conn: sqlite3.Connection, *, channel_id: str, older_than: float) -> int:
    """Пометить всё старое как уже обработанное. Нужно для холодного старта."""
    cursor = conn.execute(
        "UPDATE escalations SET stale_notified = 1 "
        "WHERE channel_id = ? AND created_at <= ? AND stale_notified = 0",
        (channel_id, older_than),
    )
    conn.commit()
    return cursor.rowcount


def purge_older_than(conn: sqlite3.Connection, cutoff: float) -> int:
    # Тот же аргумент, что и в delete_escalation: без явной транзакции конкурентный
    # читатель под автокоммитом мог бы застать промежуточное состояние между
    # двумя DELETE.
    conn.execute("BEGIN")
    conn.execute(
        "DELETE FROM reactions WHERE (channel_id, ts) IN "
        "(SELECT channel_id, ts FROM escalations WHERE created_at < ?)",
        (cutoff,),
    )
    cursor = conn.execute("DELETE FROM escalations WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount
