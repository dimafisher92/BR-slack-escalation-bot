# Braverave Escalation Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slack-бот, который отвечает в тред при дубликате эскалации в трёх каналах воркспейса braverave и напоминает про payments-эскалации, висящие 7 дней без `:white_check_mark:`.

**Architecture:** Одно внутреннее Slack-приложение, один процесс на Linux VPS, Socket Mode. Локальная SQLite работает индексом сообщений и реакций: события пишутся в неё в реальном времени, при каждом старте догоняется 7 дней истории. Проверка дубликата — SELECT по индексу, без обращений к Slack API. Ежечасный фоновый поток находит висяки и чистит старые записи.

**Tech Stack:** Python 3.11+, `slack-bolt` (sync, Socket Mode), stdlib `sqlite3`, `pytest`. Планировщик — daemon-поток на `threading`, отдельная зависимость не нужна.

**Spec:** `docs/superpowers/specs/2026-09-01-braverave-escalation-bot-design.md`

### Три уточнения к спеке

Осознанные отступления от раздела 3.1 спеки, всё остальное реализуется дословно:

1. Тексты сообщений вынесены из `config.py` в отдельный `messages.py` — это чистые функции без обращений к Slack, их можно тестировать напрямую.
2. Добавлен `poster.py` — тонкая обёртка над Slack-клиентом, которая знает про `--dry-run`. Без неё режим сухого прогона размазывается по всем модулям.
3. Channel ID берутся из переменных окружения, а не хардкодятся в `config.py`. Так один и тот же код работает на проде и на тестовом воркспейсе.

Код лежит в пакете `guard/`, запуск `python -m guard.main`.

## Global Constraints

- Python 3.11 или новее (используется синтаксис `X | None`).
- Зависимости прода: только `slack-bolt`. Зависимость разработки: только `pytest`.
- Все тексты, уходящие в Slack, на английском языке.
- Секреты и channel ID только из переменных окружения. В репозиторий не попадает ни один токен.
- Окна времени задаются константами в `guard/config.py`, магические числа в коде запрещены.
- Тесты не делают сетевых вызовов. Slack-клиент в тестах подменяется фейком.
- Время везде передаётся параметром `now: float`, а не читается внутри функций — иначе тесты границы 7 дней недетерминированы.
- Резолвед-эмодзи: `white_check_mark`, `heavy_check_mark`. In-progress: `eyes`.
- Окно дубликатов 7 дней, порог висяка 7 дней, retention 30 дней.

---

### Task 1: Скелет проекта, конфиг, зависимости

**Files:**
- Create: `guard/__init__.py`
- Create: `guard/config.py`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `pytest.ini`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `guard.config.load_channels() -> dict[str, ChannelRule]`, где `ChannelRule` это `dict` с ключами `name: str`, `match: str` (`"transaction_id"` или `"player_id"`), `stale_tracking: bool`. Константы `DUP_WINDOW_DAYS`, `STALE_AFTER_DAYS`, `RETENTION_DAYS`, `SWEEP_INTERVAL_SECONDS`, `MAX_DUPLICATES_SHOWN`, `RESOLVED_EMOJI: frozenset[str]`, `IN_PROGRESS_EMOJI: frozenset[str]`, `COLD_START_SILENT: bool`.

- [ ] **Step 1: Инициализировать репозиторий и структуру**

```bash
cd "D:/Claude Code/braverave-escalation-bot"
git init
mkdir -p guard tests deploy
touch guard/__init__.py tests/__init__.py
```

- [ ] **Step 2: Написать файлы зависимостей и окружения**

`requirements.txt`:

```
slack-bolt>=1.18
```

`requirements-dev.txt`:

```
-r requirements.txt
pytest>=8.0
```

`.env.example`:

```
SLACK_BOT_TOKEN=xoxb-replace-me
SLACK_APP_TOKEN=xapp-replace-me
CHANNEL_PAYMENTS=C000000PAY
CHANNEL_BONUSES=C000000BON
CHANNEL_KYC=C000000KYC
DB_PATH=/var/lib/escalation-guard/guard.db
LOG_LEVEL=INFO
```

`.gitignore`:

```
.env
*.db
*.db-journal
*.db-wal
*.db-shm
__pycache__/
.venv/
.pytest_cache/
```

`pytest.ini` — без него `import guard` не найдётся при запуске из корня:

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 3: Написать падающий тест**

`tests/test_config.py`:

```python
import pytest

from guard import config


def test_load_channels_builds_rules_from_env(monkeypatch):
    monkeypatch.setenv("CHANNEL_PAYMENTS", "C_PAY")
    monkeypatch.setenv("CHANNEL_BONUSES", "C_BON")
    monkeypatch.setenv("CHANNEL_KYC", "C_KYC")

    channels = config.load_channels()

    assert channels["C_PAY"] == {
        "name": "2-payments",
        "match": "transaction_id",
        "stale_tracking": True,
    }
    assert channels["C_BON"]["match"] == "player_id"
    assert channels["C_BON"]["stale_tracking"] is False
    assert channels["C_KYC"]["match"] == "player_id"
    assert channels["C_KYC"]["stale_tracking"] is False


def test_load_channels_fails_loudly_when_env_missing(monkeypatch):
    monkeypatch.delenv("CHANNEL_PAYMENTS", raising=False)
    monkeypatch.setenv("CHANNEL_BONUSES", "C_BON")
    monkeypatch.setenv("CHANNEL_KYC", "C_KYC")

    with pytest.raises(RuntimeError, match="CHANNEL_PAYMENTS"):
        config.load_channels()


def test_windows_are_seven_days():
    assert config.DUP_WINDOW_DAYS == 7
    assert config.STALE_AFTER_DAYS == 7
    assert config.RETENTION_DAYS == 30
```

- [ ] **Step 4: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_config.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.config'`

- [ ] **Step 5: Написать реализацию**

`guard/config.py`:

```python
"""Настройки бота. Секреты и channel ID приходят из окружения."""

import os

DUP_WINDOW_DAYS = 7
STALE_AFTER_DAYS = 7
RETENTION_DAYS = 30
SWEEP_INTERVAL_SECONDS = 3600
MAX_DUPLICATES_SHOWN = 3

RESOLVED_EMOJI = frozenset({"white_check_mark", "heavy_check_mark"})
IN_PROGRESS_EMOJI = frozenset({"eyes"})

# При первом запуске на пустой базе не поднимать накопившийся архив.
COLD_START_SILENT = True

DAY_SECONDS = 86400


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_channels() -> dict[str, dict]:
    """Карта channel_id -> правило обработки канала."""
    return {
        _require("CHANNEL_PAYMENTS"): {
            "name": "2-payments",
            "match": "transaction_id",
            "stale_tracking": True,
        },
        _require("CHANNEL_BONUSES"): {
            "name": "7-bonuses",
            "match": "player_id",
            "stale_tracking": False,
        },
        _require("CHANNEL_KYC"): {
            "name": "3-kyc",
            "match": "player_id",
            "stale_tracking": False,
        },
    }
```

- [ ] **Step 6: Запустить тесты**

Run: `pytest tests/test_config.py -v`
Expected: PASS, 3 теста

- [ ] **Step 7: Коммит**

```bash
git add guard tests requirements.txt requirements-dev.txt .env.example .gitignore pytest.ini
git commit -m "feat: project skeleton and environment-driven channel config"
```

---

### Task 2: Парсер идентификаторов

**Files:**
- Create: `guard/parser.py`
- Test: `tests/test_parser.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `guard.parser.ParsedIds` — frozen dataclass с полями `player_id: str | None`, `transaction_id: str | None`, `extra_transaction_ids: tuple[str, ...]`.
  - `guard.parser.parse_ids(text: str | None) -> ParsedIds`
  - `guard.parser.normalize(text: str) -> str`
  - `guard.parser.is_trackable_message(event: dict) -> bool` — True только для человеческих сообщений верхнего уровня.

- [ ] **Step 1: Написать падающий тест**

`tests/test_parser.py`:

```python
from guard.parser import ParsedIds, is_trackable_message, normalize, parse_ids

PAYMENTS = (
    "*Player ID:* 688087847\n"
    "*Transaction ID:* 700000001779189773\n"
    "*Type:* Deposit\n"
    "*Amount and Currency:* 4 000 ARS\n"
    "*Issue:* a different amount"
)

BONUSES = (
    "*Player ID:* 743080300\n"
    "*Promo / bonus:* bonus\n"
    "*Issue / need:* The customer's bonus was canceled."
)

KYC = (
    "*Player ID:* 439545299\n"
    "*Document + status:*\n"
    "*Issue:* The customer wants to change their email address."
)


def test_parses_payments_message():
    ids = parse_ids(PAYMENTS)
    assert ids.player_id == "688087847"
    assert ids.transaction_id == "700000001779189773"
    assert ids.extra_transaction_ids == ()


def test_parses_bonuses_message():
    ids = parse_ids(BONUSES)
    assert ids.player_id == "743080300"
    assert ids.transaction_id is None


def test_parses_kyc_message_with_empty_document_field():
    ids = parse_ids(KYC)
    assert ids.player_id == "439545299"
    assert ids.transaction_id is None


def test_tolerates_spacing_case_and_missing_colon():
    ids = parse_ids("player  id 688087847\nTRANSACTION ID - 700000001779189773")
    assert ids.player_id == "688087847"
    assert ids.transaction_id == "700000001779189773"


def test_tolerates_non_breaking_space():
    ids = parse_ids("Player ID:\u00a0688087847")
    assert ids.player_id == "688087847"


def test_returns_none_when_no_ids_present():
    ids = parse_ids("Please take a look at this account, thanks")
    assert ids == ParsedIds(player_id=None, transaction_id=None, extra_transaction_ids=())


def test_handles_none_text():
    assert parse_ids(None) == ParsedIds(None, None, ())


def test_ignores_short_numbers_that_are_not_ids():
    # 3 цифры не могут быть Player ID, порог 4
    assert parse_ids("Player ID: 123").player_id is None


def test_reports_extra_transaction_ids():
    ids = parse_ids(
        "Player ID: 688087847\n"
        "Transaction ID: 700000001779189773\n"
        "Transaction ID: 700000001779189774"
    )
    assert ids.transaction_id == "700000001779189773"
    assert ids.extra_transaction_ids == ("700000001779189774",)


def test_normalize_strips_markdown_and_collapses_spaces():
    assert normalize("*Player   ID:*  `688087847`") == "Player ID: 688087847"


def test_is_trackable_accepts_plain_top_level_message():
    assert is_trackable_message({"ts": "1.0", "user": "U1", "text": "hi"}) is True


def test_is_trackable_rejects_thread_reply():
    assert is_trackable_message({"ts": "2.0", "thread_ts": "1.0", "user": "U1"}) is False


def test_is_trackable_accepts_thread_parent():
    # У родителя треда thread_ts равен ts
    assert is_trackable_message({"ts": "1.0", "thread_ts": "1.0", "user": "U1"}) is True


def test_is_trackable_rejects_bot_message():
    assert is_trackable_message({"ts": "1.0", "bot_id": "B1", "text": "hi"}) is False


def test_is_trackable_rejects_service_subtypes():
    assert is_trackable_message({"ts": "1.0", "user": "U1", "subtype": "channel_join"}) is False
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.parser'`

- [ ] **Step 3: Написать реализацию**

`guard/parser.py`:

```python
"""Извлечение Player ID и Transaction ID из текста эскалации."""

import re
from dataclasses import dataclass

_MARKDOWN_CHARS = str.maketrans({"*": "", "_": "", "`": ""})
_WHITESPACE_RE = re.compile(r"[^\S\n]+")

PLAYER_ID_RE = re.compile(r"player\s*id\s*[:\-]?\s*(\d{4,})", re.IGNORECASE)
TRANSACTION_ID_RE = re.compile(r"transaction\s*id\s*[:\-]?\s*(\d{6,})", re.IGNORECASE)

# Служебные события, которые приходят в поток message, но эскалациями не являются.
IGNORED_SUBTYPES = frozenset(
    {
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "bot_message",
        "message_changed",
        "message_deleted",
        "thread_broadcast",
    }
)


@dataclass(frozen=True)
class ParsedIds:
    player_id: str | None
    transaction_id: str | None
    extra_transaction_ids: tuple[str, ...]


def normalize(text: str) -> str:
    """Снять mrkdwn-разметку, привести пробелы к одинарным."""
    cleaned = text.translate(_MARKDOWN_CHARS).replace("\u00a0", " ")
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def parse_ids(text: str | None) -> ParsedIds:
    if not text:
        return ParsedIds(None, None, ())

    normalized = normalize(text)
    players = PLAYER_ID_RE.findall(normalized)
    transactions = TRANSACTION_ID_RE.findall(normalized)

    return ParsedIds(
        player_id=players[0] if players else None,
        transaction_id=transactions[0] if transactions else None,
        extra_transaction_ids=tuple(transactions[1:]),
    )


def is_trackable_message(event: dict) -> bool:
    """True для человеческого сообщения верхнего уровня."""
    if event.get("bot_id"):
        return False
    if event.get("subtype") in IGNORED_SUBTYPES:
        return False
    if not event.get("user"):
        return False

    thread_ts = event.get("thread_ts")
    if thread_ts and thread_ts != event.get("ts"):
        return False

    return True
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_parser.py -v`
Expected: PASS, 15 тестов

- [ ] **Step 5: Коммит**

```bash
git add guard/parser.py tests/test_parser.py
git commit -m "feat: parse player and transaction IDs from escalation text"
```

---

### Task 3: Схема базы и запись сообщений

**Files:**
- Create: `guard/db.py`
- Test: `tests/test_db_escalations.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `guard.db.connect(path: str) -> sqlite3.Connection` — с `row_factory = sqlite3.Row` и включённым `foreign_keys`.
  - `guard.db.init_schema(conn) -> None`
  - `guard.db.upsert_escalation(conn, *, channel_id: str, ts: str, user_id: str, created_at: float, player_id: str | None, transaction_id: str | None, text: str) -> None` — сохраняет флаги `duplicate_notified` и `stale_notified` при повторном вызове.
  - `guard.db.get_escalation(conn, channel_id: str, ts: str) -> sqlite3.Row | None`
  - `guard.db.delete_escalation(conn, channel_id: str, ts: str) -> None` — вместе с реакциями.
  - `guard.db.mark_duplicate_notified(conn, channel_id: str, ts: str) -> None`
  - `guard.db.mark_stale_notified(conn, channel_id: str, ts: str) -> None`
  - `guard.db.is_empty(conn) -> bool`
  - `guard.db.meta_get(conn, key: str) -> str | None`, `guard.db.meta_set(conn, key: str, value: str) -> None`

- [ ] **Step 1: Написать падающий тест**

`tests/test_db_escalations.py`:

```python
import pytest

from guard import db


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def _insert(conn, ts="1000.0", **overrides):
    payload = {
        "channel_id": "C_PAY",
        "ts": ts,
        "user_id": "U_MARI",
        "created_at": float(ts),
        "player_id": "688087847",
        "transaction_id": "700000001779189773",
        "text": "escalation",
    }
    payload.update(overrides)
    db.upsert_escalation(conn, **payload)
    return payload


def test_insert_and_read_back(conn):
    _insert(conn)
    row = db.get_escalation(conn, "C_PAY", "1000.0")
    assert row["player_id"] == "688087847"
    assert row["transaction_id"] == "700000001779189773"
    assert row["user_id"] == "U_MARI"
    assert row["duplicate_notified"] == 0
    assert row["stale_notified"] == 0


def test_missing_row_returns_none(conn):
    assert db.get_escalation(conn, "C_PAY", "nope") is None


def test_upsert_updates_text_and_ids(conn):
    _insert(conn)
    _insert(conn, transaction_id="700000001779189999", text="edited")
    row = db.get_escalation(conn, "C_PAY", "1000.0")
    assert row["transaction_id"] == "700000001779189999"
    assert row["text"] == "edited"


def test_upsert_preserves_notification_flags(conn):
    _insert(conn)
    db.mark_duplicate_notified(conn, "C_PAY", "1000.0")
    db.mark_stale_notified(conn, "C_PAY", "1000.0")

    _insert(conn, text="edited again")

    row = db.get_escalation(conn, "C_PAY", "1000.0")
    assert row["duplicate_notified"] == 1
    assert row["stale_notified"] == 1


def test_same_ts_in_different_channels_are_separate_rows(conn):
    _insert(conn, channel_id="C_PAY")
    _insert(conn, channel_id="C_KYC")
    assert db.get_escalation(conn, "C_PAY", "1000.0") is not None
    assert db.get_escalation(conn, "C_KYC", "1000.0") is not None


def test_delete_removes_row(conn):
    _insert(conn)
    db.delete_escalation(conn, "C_PAY", "1000.0")
    assert db.get_escalation(conn, "C_PAY", "1000.0") is None


def test_is_empty_reflects_content(conn):
    assert db.is_empty(conn) is True
    _insert(conn)
    assert db.is_empty(conn) is False


def test_meta_roundtrip(conn):
    assert db.meta_get(conn, "last_backfill") is None
    db.meta_set(conn, "last_backfill", "1000.5")
    assert db.meta_get(conn, "last_backfill") == "1000.5"
    db.meta_set(conn, "last_backfill", "2000.5")
    assert db.meta_get(conn, "last_backfill") == "2000.5"
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_db_escalations.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.db'`

- [ ] **Step 3: Написать реализацию**

`guard/db.py`:

```python
"""SQLite-индекс сообщений и реакций."""

import sqlite3

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
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_db_escalations.py -v`
Expected: PASS, 8 тестов

- [ ] **Step 5: Коммит**

```bash
git add guard/db.py tests/test_db_escalations.py
git commit -m "feat: sqlite schema and escalation storage"
```

---

### Task 4: Реакции и вычисление статуса

**Files:**
- Modify: `guard/db.py` (дописать функции в конец файла)
- Test: `tests/test_db_reactions.py`

**Interfaces:**
- Consumes: `guard.db.connect`, `guard.db.init_schema`, `guard.db.upsert_escalation` из Task 3.
- Produces:
  - `guard.db.add_reaction(conn, channel_id: str, ts: str, name: str, user_id: str) -> None` — идемпотентно.
  - `guard.db.remove_reaction(conn, channel_id: str, ts: str, name: str, user_id: str) -> None`
  - `guard.db.reaction_names(conn, channel_id: str, ts: str) -> set[str]`
  - `guard.db.users_with_reaction(conn, channel_id: str, ts: str, names: frozenset[str]) -> list[str]` — user_id в порядке добавления, без повторов.
  - `guard.db.is_resolved(conn, channel_id: str, ts: str) -> bool`

- [ ] **Step 1: Написать падающий тест**

`tests/test_db_reactions.py`:

```python
import pytest

from guard import config, db


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    db.upsert_escalation(
        connection,
        channel_id="C_PAY",
        ts="1000.0",
        user_id="U_MARI",
        created_at=1000.0,
        player_id="688087847",
        transaction_id="700000001779189773",
        text="escalation",
    )
    yield connection
    connection.close()


def test_add_and_read_reaction_names(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    assert db.reaction_names(conn, "C_PAY", "1000.0") == {"eyes"}


def test_add_reaction_is_idempotent(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    assert db.users_with_reaction(conn, "C_PAY", "1000.0", config.IN_PROGRESS_EMOJI) == ["U_OP1"]


def test_unresolved_without_checkmark(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is False


def test_resolved_with_checkmark(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is True


def test_heavy_check_mark_also_resolves(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "heavy_check_mark", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is True


def test_stays_resolved_when_one_of_two_removes_checkmark(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP2")

    db.remove_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")

    assert db.is_resolved(conn, "C_PAY", "1000.0") is True


def test_becomes_unresolved_when_last_checkmark_removed(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    db.remove_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is False


def test_users_with_eyes_returned_in_insertion_order(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP2")
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP3")

    assert db.users_with_reaction(conn, "C_PAY", "1000.0", config.IN_PROGRESS_EMOJI) == [
        "U_OP1",
        "U_OP2",
    ]


def test_deleting_escalation_removes_its_reactions(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    db.delete_escalation(conn, "C_PAY", "1000.0")
    assert db.reaction_names(conn, "C_PAY", "1000.0") == set()
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_db_reactions.py -v`
Expected: FAIL, `AttributeError: module 'guard.db' has no attribute 'add_reaction'`

- [ ] **Step 3: Дописать реализацию в конец `guard/db.py`**

```python
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
```

Добавить в начало `guard/db.py`, к существующим импортам:

```python
from guard import config
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_db_reactions.py -v`
Expected: PASS, 9 тестов

- [ ] **Step 5: Коммит**

```bash
git add guard/db.py tests/test_db_reactions.py
git commit -m "feat: reaction storage and resolved-status derivation"
```

---

### Task 5: Поисковые запросы дубликатов и висяков

**Files:**
- Modify: `guard/db.py` (дописать функции в конец файла)
- Test: `tests/test_db_queries.py`

**Interfaces:**
- Consumes: всё из Task 3 и Task 4.
- Produces:
  - `guard.db.find_duplicates(conn, *, channel_id: str, field: str, value: str, exclude_ts: str, since: float) -> list[sqlite3.Row]` — совпадения в том же канале за окно, самые свежие первыми. `field` принимает только `"transaction_id"` и `"player_id"`, иначе `ValueError`.
  - `guard.db.find_stale(conn, *, channel_id: str, older_than: float, newer_than: float) -> list[sqlite3.Row]` — без `stale_notified`, без резолвед-реакции, старые первыми.
  - `guard.db.silence_old_stale(conn, *, channel_id: str, older_than: float) -> int` — проставить `stale_notified = 1` всему старью, вернуть число строк.
  - `guard.db.purge_older_than(conn, cutoff: float) -> int` — удалить эскалации и их реакции, вернуть число удалённых эскалаций.

- [ ] **Step 1: Написать падающий тест**

`tests/test_db_queries.py`:

```python
import pytest

from guard import db

DAY = 86400.0
NOW = 1_000_000.0


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def add(conn, ts, *, channel_id="C_PAY", transaction_id="TX1", player_id="P1", user_id="U1"):
    db.upsert_escalation(
        conn,
        channel_id=channel_id,
        ts=str(ts),
        user_id=user_id,
        created_at=float(ts),
        player_id=player_id,
        transaction_id=transaction_id,
        text="escalation",
    )


def test_finds_duplicate_inside_window(conn):
    add(conn, NOW - 2 * DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert [row["ts"] for row in found] == [str(NOW - 2 * DAY)]


def test_ignores_match_outside_window(conn):
    add(conn, NOW - 8 * DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert found == []


def test_includes_match_exactly_on_window_boundary(conn):
    add(conn, NOW - 7 * DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert len(found) == 1


def test_does_not_match_across_channels(conn):
    add(conn, NOW - DAY, channel_id="C_KYC")
    add(conn, NOW, channel_id="C_PAY")

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert found == []


def test_newest_matches_come_first(conn):
    add(conn, NOW - 5 * DAY)
    add(conn, NOW - DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert [row["ts"] for row in found] == [str(NOW - DAY), str(NOW - 5 * DAY)]


def test_player_field_matching(conn):
    add(conn, NOW - DAY, transaction_id="TX_A", player_id="P9")
    add(conn, NOW, transaction_id="TX_B", player_id="P9")

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="player_id", value="P9",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert len(found) == 1


def test_rejects_unknown_field(conn):
    with pytest.raises(ValueError, match="text"):
        db.find_duplicates(
            conn, channel_id="C_PAY", field="text", value="x",
            exclude_ts="1", since=0.0,
        )


def test_find_stale_returns_old_unresolved(conn):
    add(conn, NOW - 8 * DAY)

    stale = db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    )

    assert [row["ts"] for row in stale] == [str(NOW - 8 * DAY)]


def test_find_stale_skips_fresh(conn):
    add(conn, NOW - 3 * DAY)
    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_find_stale_skips_resolved(conn):
    add(conn, NOW - 8 * DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "white_check_mark", "U_OP")

    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_find_stale_does_not_skip_eyes_only(conn):
    add(conn, NOW - 8 * DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "eyes", "U_OP")

    assert len(db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    )) == 1


def test_find_stale_skips_already_notified(conn):
    add(conn, NOW - 8 * DAY)
    db.mark_stale_notified(conn, "C_PAY", str(NOW - 8 * DAY))

    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_find_stale_ignores_beyond_retention(conn):
    add(conn, NOW - 40 * DAY)

    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_silence_old_stale_marks_and_counts(conn):
    add(conn, NOW - 8 * DAY, transaction_id="TX_A")
    add(conn, NOW - 9 * DAY, transaction_id="TX_B")
    add(conn, NOW - DAY, transaction_id="TX_C")

    marked = db.silence_old_stale(conn, channel_id="C_PAY", older_than=NOW - 7 * DAY)

    assert marked == 2
    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_purge_removes_old_rows_and_reactions(conn):
    add(conn, NOW - 40 * DAY)
    add(conn, NOW - DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 40 * DAY), "eyes", "U_OP")

    removed = db.purge_older_than(conn, NOW - 30 * DAY)

    assert removed == 1
    assert db.get_escalation(conn, "C_PAY", str(NOW - 40 * DAY)) is None
    assert db.reaction_names(conn, "C_PAY", str(NOW - 40 * DAY)) == set()
    assert db.get_escalation(conn, "C_PAY", str(NOW - DAY)) is not None
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_db_queries.py -v`
Expected: FAIL, `AttributeError: module 'guard.db' has no attribute 'find_duplicates'`

- [ ] **Step 3: Дописать реализацию в конец `guard/db.py`**

```python
MATCHABLE_FIELDS = frozenset({"transaction_id", "player_id"})


def find_duplicates(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    field: str,
    value: str,
    exclude_ts: str,
    since: float,
) -> list[sqlite3.Row]:
    """Совпадения по одному полю в том же канале за окно, свежие первыми."""
    if field not in MATCHABLE_FIELDS:
        raise ValueError(f"Unsupported match field: {field}")

    return conn.execute(
        f"SELECT * FROM escalations "
        f"WHERE channel_id = ? AND {field} = ? AND ts != ? AND created_at >= ? "
        f"ORDER BY created_at DESC",
        (channel_id, value, exclude_ts, since),
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
    conn.execute(
        "DELETE FROM reactions WHERE (channel_id, ts) IN "
        "(SELECT channel_id, ts FROM escalations WHERE created_at < ?)",
        (cutoff,),
    )
    cursor = conn.execute("DELETE FROM escalations WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_db_queries.py -v`
Expected: PASS, 15 тестов

- [ ] **Step 5: Прогнать всё, что есть**

Run: `pytest -v`
Expected: PASS, 50 тестов

- [ ] **Step 6: Коммит**

```bash
git add guard/db.py tests/test_db_queries.py
git commit -m "feat: duplicate lookup, stale query and retention purge"
```

---

### Task 6: Тексты сообщений

**Files:**
- Create: `guard/messages.py`
- Test: `tests/test_messages.py`

**Interfaces:**
- Consumes: `guard.config` из Task 1.
- Produces:
  - `guard.messages.Match` — frozen dataclass: `permalink: str | None`, `user_id: str`, `created_at: float`, `reaction_names: frozenset[str]`.
  - `guard.messages.humanize_age(seconds: float) -> str`
  - `guard.messages.status_label(reaction_names: frozenset[str]) -> str`
  - `guard.messages.duplicate_transaction_text(*, transaction_id: str, match: Match, now: float) -> str`
  - `guard.messages.duplicate_player_text(*, player_id: str, matches: list[Match], now: float) -> str`
  - `guard.messages.stale_reminder_text(*, player_id: str | None, transaction_id: str | None, mention_user_ids: list[str]) -> str`

- [ ] **Step 1: Написать падающий тест**

`tests/test_messages.py`:

```python
from guard.messages import (
    Match,
    duplicate_player_text,
    duplicate_transaction_text,
    humanize_age,
    stale_reminder_text,
    status_label,
)

DAY = 86400.0
NOW = 1_000_000.0


def test_humanize_minutes_hours_days():
    assert humanize_age(30) == "1 minute ago"
    assert humanize_age(120) == "2 minutes ago"
    assert humanize_age(3600) == "1 hour ago"
    assert humanize_age(7200) == "2 hours ago"
    assert humanize_age(DAY) == "1 day ago"
    assert humanize_age(2 * DAY) == "2 days ago"


def test_status_label_variants():
    assert status_label(frozenset({"white_check_mark"})) == "resolved :white_check_mark:"
    assert status_label(frozenset({"eyes"})) == "in progress :eyes:"
    assert status_label(frozenset()) == "no reaction yet"


def test_resolved_wins_over_eyes():
    assert status_label(frozenset({"eyes", "white_check_mark"})) == "resolved :white_check_mark:"


def test_duplicate_transaction_text_contains_id_age_author_and_link():
    match = Match(
        permalink="https://braverave.slack.com/archives/C_PAY/p1",
        user_id="U_MARI",
        created_at=NOW - 2 * DAY,
        reaction_names=frozenset({"eyes"}),
    )

    text = duplicate_transaction_text(transaction_id="TX123456", match=match, now=NOW)

    assert "Duplicate escalation" in text
    assert "TX123456" in text
    assert "2 days ago" in text
    assert "<@U_MARI>" in text
    assert "in progress :eyes:" in text
    assert "https://braverave.slack.com/archives/C_PAY/p1" in text


def test_duplicate_transaction_text_without_permalink_still_readable():
    match = Match(permalink=None, user_id="U_MARI", created_at=NOW - DAY, reaction_names=frozenset())
    text = duplicate_transaction_text(transaction_id="TX1", match=match, now=NOW)
    assert "http" not in text
    assert "1 day ago" in text


def test_duplicate_player_text_lists_matches():
    matches = [
        Match(permalink="https://s/1", user_id="U_A", created_at=NOW - DAY, reaction_names=frozenset()),
        Match(
            permalink="https://s/2",
            user_id="U_B",
            created_at=NOW - 3 * DAY,
            reaction_names=frozenset({"white_check_mark"}),
        ),
    ]

    text = duplicate_player_text(player_id="P42", matches=matches, now=NOW)

    assert "Possible duplicate" in text
    assert "P42" in text
    assert "2 escalations" in text
    assert "<@U_A>" in text and "<@U_B>" in text
    assert "resolved :white_check_mark:" in text


def test_duplicate_player_text_uses_singular_for_one_match():
    matches = [Match(permalink=None, user_id="U_A", created_at=NOW - DAY, reaction_names=frozenset())]
    text = duplicate_player_text(player_id="P42", matches=matches, now=NOW)
    assert "1 escalation " in text
    assert "escalations" not in text


def test_duplicate_player_text_truncates_to_three_and_reports_rest():
    matches = [
        Match(permalink=None, user_id=f"U_{i}", created_at=NOW - i * DAY, reaction_names=frozenset())
        for i in range(1, 6)
    ]

    text = duplicate_player_text(player_id="P42", matches=matches, now=NOW)

    assert text.count("\n- ") == 3
    assert "and 2 more" in text


def test_stale_reminder_mentions_author_and_watchers():
    text = stale_reminder_text(
        player_id="688087847",
        transaction_id="700000001779189773",
        mention_user_ids=["U_MARI", "U_OP1"],
    )

    assert "7 days without resolution" in text
    assert "688087847" in text
    assert "700000001779189773" in text
    assert "cc <@U_MARI> <@U_OP1>" in text


def test_stale_reminder_omits_cc_line_when_nobody_to_ping():
    text = stale_reminder_text(player_id="P1", transaction_id=None, mention_user_ids=[])
    assert "cc " not in text
    assert "Transaction" not in text
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_messages.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.messages'`

- [ ] **Step 3: Написать реализацию**

`guard/messages.py`:

```python
"""Тексты, уходящие в Slack. Чистые функции, без обращений к API."""

from dataclasses import dataclass

from guard import config

HOUR_SECONDS = 3600
DAY_SECONDS = 86400


@dataclass(frozen=True)
class Match:
    permalink: str | None
    user_id: str
    created_at: float
    reaction_names: frozenset[str]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def humanize_age(seconds: float) -> str:
    if seconds < HOUR_SECONDS:
        return f"{_plural(max(1, int(seconds // 60)), 'minute')} ago"
    if seconds < DAY_SECONDS:
        return f"{_plural(int(seconds // HOUR_SECONDS), 'hour')} ago"
    return f"{_plural(int(seconds // DAY_SECONDS), 'day')} ago"


def status_label(reaction_names: frozenset[str]) -> str:
    if reaction_names & set(config.RESOLVED_EMOJI):
        return "resolved :white_check_mark:"
    if reaction_names & set(config.IN_PROGRESS_EMOJI):
        return "in progress :eyes:"
    return "no reaction yet"


def _link(match: Match, label: str) -> str:
    return f"<{match.permalink}|{label}>" if match.permalink else label


def duplicate_transaction_text(*, transaction_id: str, match: Match, now: float) -> str:
    age = humanize_age(now - match.created_at)
    lines = [
        ":warning: *Duplicate escalation*",
        f"Transaction `{transaction_id}` was already escalated {age} by <@{match.user_id}>",
        f"Status: {status_label(match.reaction_names)}",
    ]
    if match.permalink:
        lines.append(_link(match, "Open original"))
    return "\n".join(lines)


def duplicate_player_text(*, player_id: str, matches: list[Match], now: float) -> str:
    shown = matches[: config.MAX_DUPLICATES_SHOWN]
    hidden = len(matches) - len(shown)

    lines = [
        ":warning: *Possible duplicate*",
        f"Player `{player_id}` — {_plural(len(matches), 'escalation')} "
        f"in this channel in the last {config.DUP_WINDOW_DAYS} days:",
    ]
    for match in shown:
        age = humanize_age(now - match.created_at)
        lines.append(
            f"- {_link(match, age)} by <@{match.user_id}> — {status_label(match.reaction_names)}"
        )
    if hidden > 0:
        lines.append(f"and {hidden} more")

    return "\n".join(lines)


def stale_reminder_text(
    *, player_id: str | None, transaction_id: str | None, mention_user_ids: list[str]
) -> str:
    lines = [
        f":hourglass_flowing_sand: *{config.STALE_AFTER_DAYS} days without resolution*",
        "No :white_check_mark: on this escalation.",
    ]

    details = []
    if player_id:
        details.append(f"Player `{player_id}`")
    if transaction_id:
        details.append(f"Transaction `{transaction_id}`")
    if details:
        lines.append(" · ".join(details))

    if mention_user_ids:
        mentions = " ".join(f"<@{user_id}>" for user_id in mention_user_ids)
        lines.append(f"cc {mentions}")

    return "\n".join(lines)
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_messages.py -v`
Expected: PASS, 10 тестов

- [ ] **Step 5: Коммит**

```bash
git add guard/messages.py tests/test_messages.py
git commit -m "feat: message templates for duplicates and stale reminders"
```

---

### Task 7: Отправка в Slack и режим сухого прогона

**Files:**
- Create: `guard/poster.py`
- Test: `tests/test_poster.py`

**Interfaces:**
- Consumes: ничего из проекта.
- Produces:
  - `guard.poster.Poster(client, dry_run: bool = False)` — `client` это `slack_sdk.WebClient` или совместимый фейк с методами `chat_getPermalink(channel=..., message_ts=...)` и `chat_postMessage(channel=..., thread_ts=..., text=...)`.
  - `Poster.permalink(channel_id: str, ts: str) -> str | None` — при ошибке API возвращает None, не бросает.
  - `Poster.reply_in_thread(channel_id: str, thread_ts: str, text: str) -> bool` — True если отправлено (в dry-run True после записи в лог), False при ошибке API.

- [ ] **Step 1: Написать падающий тест**

`tests/test_poster.py`:

```python
import logging

from guard.poster import Poster


class FakeClient:
    def __init__(self, permalink="https://s/link", fail=False):
        self._permalink = permalink
        self._fail = fail
        self.posted = []
        self.permalink_calls = []

    def chat_getPermalink(self, *, channel, message_ts):
        self.permalink_calls.append((channel, message_ts))
        if self._fail:
            raise RuntimeError("slack is down")
        return {"ok": True, "permalink": self._permalink}

    def chat_postMessage(self, *, channel, thread_ts, text):
        if self._fail:
            raise RuntimeError("slack is down")
        self.posted.append({"channel": channel, "thread_ts": thread_ts, "text": text})
        return {"ok": True}


def test_permalink_returned():
    client = FakeClient()
    assert Poster(client).permalink("C1", "1.0") == "https://s/link"
    assert client.permalink_calls == [("C1", "1.0")]


def test_permalink_returns_none_on_api_error(caplog):
    with caplog.at_level(logging.WARNING):
        assert Poster(FakeClient(fail=True)).permalink("C1", "1.0") is None
    assert "permalink" in caplog.text.lower()


def test_reply_posts_to_thread():
    client = FakeClient()
    assert Poster(client).reply_in_thread("C1", "1.0", "hello") is True
    assert client.posted == [{"channel": "C1", "thread_ts": "1.0", "text": "hello"}]


def test_reply_returns_false_on_api_error(caplog):
    with caplog.at_level(logging.ERROR):
        assert Poster(FakeClient(fail=True)).reply_in_thread("C1", "1.0", "hello") is False
    assert "failed to post" in caplog.text.lower()


def test_dry_run_does_not_post_but_reports_success(caplog):
    client = FakeClient()
    with caplog.at_level(logging.INFO):
        assert Poster(client, dry_run=True).reply_in_thread("C1", "1.0", "hello") is True
    assert client.posted == []
    assert "DRY-RUN" in caplog.text
    assert "hello" in caplog.text


def test_dry_run_still_resolves_permalinks():
    client = FakeClient()
    assert Poster(client, dry_run=True).permalink("C1", "1.0") == "https://s/link"
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_poster.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.poster'`

- [ ] **Step 3: Написать реализацию**

`guard/poster.py`:

```python
"""Обёртка над Slack-клиентом: единая точка отправки и режим --dry-run."""

import logging

log = logging.getLogger(__name__)


class Poster:
    def __init__(self, client, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run

    def permalink(self, channel_id: str, ts: str) -> str | None:
        """Постоянная ссылка на сообщение. None, если Slack не ответил."""
        try:
            response = self._client.chat_getPermalink(channel=channel_id, message_ts=ts)
            return response["permalink"]
        except Exception:
            log.warning("Could not fetch permalink for %s/%s", channel_id, ts, exc_info=True)
            return None

    def reply_in_thread(self, channel_id: str, thread_ts: str, text: str) -> bool:
        if self._dry_run:
            log.info("DRY-RUN would reply in %s thread %s:\n%s", channel_id, thread_ts, text)
            return True

        try:
            self._client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
            return True
        except Exception:
            log.error("Failed to post reply in %s/%s", channel_id, thread_ts, exc_info=True)
            return False
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_poster.py -v`
Expected: PASS, 6 тестов

- [ ] **Step 5: Коммит**

```bash
git add guard/poster.py tests/test_poster.py
git commit -m "feat: slack poster with dry-run mode"
```

---

### Task 8: Обработчики сообщений и реакций

**Files:**
- Create: `guard/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: `guard.parser`, `guard.db`, `guard.messages`, `guard.poster.Poster`, `guard.config`.
- Produces:
  - `guard.handlers.build_matches(conn, poster, rows) -> list[messages.Match]`
  - `guard.handlers.handle_message(conn, poster, channels: dict, event: dict, now: float) -> None`
  - `guard.handlers.handle_message_changed(conn, poster, channels: dict, event: dict, now: float) -> None`
  - `guard.handlers.handle_message_deleted(conn, event: dict) -> None`
  - `guard.handlers.handle_reaction_added(conn, event: dict) -> None`
  - `guard.handlers.handle_reaction_removed(conn, event: dict) -> None`
  - `guard.handlers.dispatch_message_event(conn, poster, channels: dict, event: dict, now: float) -> None` — разбирает `subtype` и зовёт нужный из трёх.

- [ ] **Step 1: Написать падающий тест**

`tests/test_handlers.py`:

```python
import pytest

from guard import db, handlers
from guard.poster import Poster

DAY = 86400.0
NOW = 1_000_000.0

CHANNELS = {
    "C_PAY": {"name": "2-payments", "match": "transaction_id", "stale_tracking": True},
    "C_KYC": {"name": "3-kyc", "match": "player_id", "stale_tracking": False},
}


class FakeClient:
    def __init__(self):
        self.posted = []

    def chat_getPermalink(self, *, channel, message_ts):
        return {"ok": True, "permalink": f"https://s/{channel}/{message_ts}"}

    def chat_postMessage(self, *, channel, thread_ts, text):
        self.posted.append({"channel": channel, "thread_ts": thread_ts, "text": text})
        return {"ok": True}


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture()
def client():
    return FakeClient()


@pytest.fixture()
def poster(client):
    return Poster(client)


def payments_event(ts, transaction_id="700000001779189773", user="U_MARI"):
    return {
        "channel": "C_PAY",
        "ts": str(ts),
        "user": user,
        "text": f"Player ID: 688087847\nTransaction ID: {transaction_id}",
    }


def kyc_event(ts, player_id="439545299", user="U_BORIS"):
    return {
        "channel": "C_KYC",
        "ts": str(ts),
        "user": user,
        "text": f"Player ID: {player_id}\nDocument + status: Uploaded",
    }


def test_first_message_is_stored_and_silent(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)

    assert db.get_escalation(conn, "C_PAY", str(NOW)) is not None
    assert client.posted == []


def test_duplicate_transaction_gets_thread_reply(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW - 2 * DAY), NOW - 2 * DAY)
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)

    assert len(client.posted) == 1
    reply = client.posted[0]
    assert reply["channel"] == "C_PAY"
    assert reply["thread_ts"] == str(NOW)
    assert "Duplicate escalation" in reply["text"]
    assert "700000001779189773" in reply["text"]


def test_different_transaction_same_player_is_silent_in_payments(conn, poster, client):
    handlers.handle_message(
        conn, poster, CHANNELS, payments_event(NOW - DAY, transaction_id="700000000000000001"), NOW - DAY
    )
    handlers.handle_message(
        conn, poster, CHANNELS, payments_event(NOW, transaction_id="700000000000000002"), NOW
    )

    assert client.posted == []


def test_duplicate_player_gets_reply_in_kyc(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, kyc_event(NOW - DAY), NOW - DAY)
    handlers.handle_message(conn, poster, CHANNELS, kyc_event(NOW), NOW)

    assert len(client.posted) == 1
    assert "Possible duplicate" in client.posted[0]["text"]
    assert "439545299" in client.posted[0]["text"]


def test_match_outside_window_is_silent(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW - 8 * DAY), NOW - 8 * DAY)
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)

    assert client.posted == []


def test_redelivered_event_does_not_double_post(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW - DAY), NOW - DAY)
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)

    assert len(client.posted) == 1


def test_message_without_transaction_id_is_stored_but_not_checked(conn, poster, client):
    event = {
        "channel": "C_PAY",
        "ts": str(NOW),
        "user": "U_MARI",
        "text": "Player ID: 688087847\nIssue: no transaction here",
    }

    handlers.handle_message(conn, poster, CHANNELS, event, NOW)

    row = db.get_escalation(conn, "C_PAY", str(NOW))
    assert row is not None
    assert row["transaction_id"] is None
    assert client.posted == []


def test_unknown_channel_is_ignored(conn, poster, client):
    event = payments_event(NOW)
    event["channel"] = "C_RANDOM"

    handlers.handle_message(conn, poster, CHANNELS, event, NOW)

    assert db.get_escalation(conn, "C_RANDOM", str(NOW)) is None
    assert client.posted == []


def test_thread_reply_is_ignored(conn, poster, client):
    event = payments_event(NOW)
    event["thread_ts"] = str(NOW - DAY)

    handlers.handle_message(conn, poster, CHANNELS, event, NOW)

    assert db.get_escalation(conn, "C_PAY", str(NOW)) is None


def test_bot_message_is_ignored(conn, poster, client):
    event = payments_event(NOW)
    event["bot_id"] = "B_GUARD"

    handlers.handle_message(conn, poster, CHANNELS, event, NOW)

    assert db.get_escalation(conn, "C_PAY", str(NOW)) is None


def test_edit_with_new_transaction_id_triggers_check(conn, poster, client):
    handlers.handle_message(
        conn, poster, CHANNELS, payments_event(NOW - DAY, transaction_id="700000000000000001"), NOW - DAY
    )
    handlers.handle_message(
        conn, poster, CHANNELS, payments_event(NOW, transaction_id="700000000000000002"), NOW
    )
    assert client.posted == []

    changed = {
        "channel": "C_PAY",
        "subtype": "message_changed",
        "message": {
            "ts": str(NOW),
            "user": "U_MARI",
            "text": "Player ID: 688087847\nTransaction ID: 700000000000000001",
        },
    }
    handlers.handle_message_changed(conn, poster, CHANNELS, changed, NOW)

    assert len(client.posted) == 1
    assert "Duplicate escalation" in client.posted[0]["text"]


def test_edit_after_notification_does_not_post_again(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW - DAY), NOW - DAY)
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)
    assert len(client.posted) == 1

    changed = {
        "channel": "C_PAY",
        "subtype": "message_changed",
        "message": {
            "ts": str(NOW),
            "user": "U_MARI",
            "text": "Player ID: 688087847\nTransaction ID: 700000001779189773 (updated)",
        },
    }
    handlers.handle_message_changed(conn, poster, CHANNELS, changed, NOW)

    assert len(client.posted) == 1


def test_deleted_message_is_removed_from_index(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW - DAY), NOW - DAY)

    handlers.handle_message_deleted(
        conn, {"channel": "C_PAY", "subtype": "message_deleted", "deleted_ts": str(NOW - DAY)}
    )

    assert db.get_escalation(conn, "C_PAY", str(NOW - DAY)) is None

    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)
    assert client.posted == []


def test_reaction_added_and_removed(conn, poster):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)

    event = {
        "user": "U_OP1",
        "reaction": "white_check_mark",
        "item": {"type": "message", "channel": "C_PAY", "ts": str(NOW)},
    }
    handlers.handle_reaction_added(conn, event)
    assert db.is_resolved(conn, "C_PAY", str(NOW)) is True

    handlers.handle_reaction_removed(conn, event)
    assert db.is_resolved(conn, "C_PAY", str(NOW)) is False


def test_reaction_on_unknown_message_is_ignored(conn):
    event = {
        "user": "U_OP1",
        "reaction": "eyes",
        "item": {"type": "message", "channel": "C_PAY", "ts": "999.0"},
    }
    handlers.handle_reaction_added(conn, event)

    assert db.reaction_names(conn, "C_PAY", "999.0") == set()


def test_reaction_on_file_item_is_ignored(conn, poster):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW), NOW)
    event = {"user": "U_OP1", "reaction": "eyes", "item": {"type": "file", "file": "F1"}}

    handlers.handle_reaction_added(conn, event)

    assert db.reaction_names(conn, "C_PAY", str(NOW)) == set()


def test_dispatch_routes_by_subtype(conn, poster, client):
    handlers.dispatch_message_event(conn, poster, CHANNELS, payments_event(NOW - DAY), NOW - DAY)
    assert db.get_escalation(conn, "C_PAY", str(NOW - DAY)) is not None

    handlers.dispatch_message_event(
        conn, poster, CHANNELS,
        {"channel": "C_PAY", "subtype": "message_deleted", "deleted_ts": str(NOW - DAY)},
        NOW,
    )
    assert db.get_escalation(conn, "C_PAY", str(NOW - DAY)) is None
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_handlers.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.handlers'`

- [ ] **Step 3: Написать реализацию**

`guard/handlers.py`:

```python
"""Обработка событий Slack: сообщения, правки, удаления, реакции."""

import logging

from guard import config, db, messages, parser
from guard.messages import Match
from guard.poster import Poster

log = logging.getLogger(__name__)


def build_matches(conn, poster: Poster, rows) -> list[Match]:
    """Превратить строки БД в объекты для шаблонов, подтянув permalink."""
    built = []
    for row in rows:
        built.append(
            Match(
                permalink=poster.permalink(row["channel_id"], row["ts"]),
                user_id=row["user_id"],
                created_at=row["created_at"],
                reaction_names=frozenset(db.reaction_names(conn, row["channel_id"], row["ts"])),
            )
        )
    return built


def _check_duplicates(conn, poster: Poster, rule: dict, channel_id: str, ts: str, now: float) -> None:
    row = db.get_escalation(conn, channel_id, ts)
    if row is None or row["duplicate_notified"]:
        return

    field = rule["match"]
    value = row[field]
    if not value:
        return

    since = now - config.DUP_WINDOW_DAYS * config.DAY_SECONDS
    rows = db.find_duplicates(
        conn, channel_id=channel_id, field=field, value=value, exclude_ts=ts, since=since
    )
    if not rows:
        return

    matches = build_matches(conn, poster, rows)
    if field == "transaction_id":
        text = messages.duplicate_transaction_text(transaction_id=value, match=matches[0], now=now)
    else:
        text = messages.duplicate_player_text(player_id=value, matches=matches, now=now)

    if poster.reply_in_thread(channel_id, ts, text):
        db.mark_duplicate_notified(conn, channel_id, ts)


def _store(conn, channel_id: str, ts: str, user_id: str, text: str) -> None:
    ids = parser.parse_ids(text)
    if ids.extra_transaction_ids:
        log.warning(
            "Message %s/%s carries %d extra transaction IDs, using the first one",
            channel_id, ts, len(ids.extra_transaction_ids),
        )

    db.upsert_escalation(
        conn,
        channel_id=channel_id,
        ts=ts,
        user_id=user_id,
        created_at=float(ts),
        player_id=ids.player_id,
        transaction_id=ids.transaction_id,
        text=text,
    )


def handle_message(conn, poster: Poster, channels: dict, event: dict, now: float) -> None:
    channel_id = event.get("channel")
    rule = channels.get(channel_id)
    if rule is None:
        return
    if not parser.is_trackable_message(event):
        return

    ts = event["ts"]
    _store(conn, channel_id, ts, event["user"], event.get("text", ""))
    _check_duplicates(conn, poster, rule, channel_id, ts, now)


def handle_message_changed(conn, poster: Poster, channels: dict, event: dict, now: float) -> None:
    channel_id = event.get("channel")
    rule = channels.get(channel_id)
    if rule is None:
        return

    inner = event.get("message") or {}
    ts = inner.get("ts")
    if not ts or db.get_escalation(conn, channel_id, ts) is None:
        return

    _store(conn, channel_id, ts, inner.get("user", ""), inner.get("text", ""))
    _check_duplicates(conn, poster, rule, channel_id, ts, now)


def handle_message_deleted(conn, event: dict) -> None:
    channel_id = event.get("channel")
    ts = event.get("deleted_ts")
    if channel_id and ts:
        db.delete_escalation(conn, channel_id, ts)


def _reaction_target(event: dict) -> tuple[str, str] | None:
    item = event.get("item") or {}
    if item.get("type") != "message":
        return None
    channel_id, ts = item.get("channel"), item.get("ts")
    if not channel_id or not ts:
        return None
    return channel_id, ts


def handle_reaction_added(conn, event: dict) -> None:
    target = _reaction_target(event)
    if target is None:
        return
    channel_id, ts = target
    # Реакции на сообщения вне индекса игнорируем: они старше окна бэкфилла.
    if db.get_escalation(conn, channel_id, ts) is None:
        return
    db.add_reaction(conn, channel_id, ts, event["reaction"], event["user"])


def handle_reaction_removed(conn, event: dict) -> None:
    target = _reaction_target(event)
    if target is None:
        return
    channel_id, ts = target
    db.remove_reaction(conn, channel_id, ts, event["reaction"], event["user"])


def dispatch_message_event(conn, poster: Poster, channels: dict, event: dict, now: float) -> None:
    subtype = event.get("subtype")
    if subtype == "message_changed":
        handle_message_changed(conn, poster, channels, event, now)
    elif subtype == "message_deleted":
        handle_message_deleted(conn, event)
    else:
        handle_message(conn, poster, channels, event, now)
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_handlers.py -v`
Expected: PASS, 17 тестов

- [ ] **Step 5: Коммит**

```bash
git add guard/handlers.py tests/test_handlers.py
git commit -m "feat: message, edit, delete and reaction handlers"
```

---

### Task 9: Sweeper и retention

**Files:**
- Create: `guard/sweeper.py`
- Test: `tests/test_sweeper.py`

**Interfaces:**
- Consumes: `guard.db`, `guard.messages`, `guard.config`, `guard.poster.Poster`.
- Produces:
  - `guard.sweeper.run_sweep(conn, poster, channels: dict, now: float) -> int` — вернуть число отправленных напоминаний.
  - `guard.sweeper.purge(conn, now: float) -> int`
  - `guard.sweeper.silence_backlog(conn, channels: dict, now: float) -> int` — для холодного старта.

- [ ] **Step 1: Написать падающий тест**

`tests/test_sweeper.py`:

```python
import pytest

from guard import db, sweeper
from guard.poster import Poster

DAY = 86400.0
NOW = 1_000_000.0

CHANNELS = {
    "C_PAY": {"name": "2-payments", "match": "transaction_id", "stale_tracking": True},
    "C_KYC": {"name": "3-kyc", "match": "player_id", "stale_tracking": False},
}


class FakeClient:
    def __init__(self):
        self.posted = []

    def chat_getPermalink(self, *, channel, message_ts):
        return {"ok": True, "permalink": f"https://s/{channel}/{message_ts}"}

    def chat_postMessage(self, *, channel, thread_ts, text):
        self.posted.append({"channel": channel, "thread_ts": thread_ts, "text": text})
        return {"ok": True}


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture()
def client():
    return FakeClient()


def add(conn, ts, *, channel_id="C_PAY", user_id="U_MARI"):
    db.upsert_escalation(
        conn,
        channel_id=channel_id,
        ts=str(ts),
        user_id=user_id,
        created_at=float(ts),
        player_id="688087847",
        transaction_id="700000001779189773",
        text="escalation",
    )


def test_posts_reminder_for_old_unresolved(conn, client):
    add(conn, NOW - 8 * DAY)

    sent = sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW)

    assert sent == 1
    assert client.posted[0]["thread_ts"] == str(NOW - 8 * DAY)
    assert "7 days without resolution" in client.posted[0]["text"]


def test_skips_fresh_escalation(conn, client):
    add(conn, NOW - 3 * DAY)
    assert sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW) == 0
    assert client.posted == []


def test_skips_resolved(conn, client):
    add(conn, NOW - 8 * DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "white_check_mark", "U_OP")

    assert sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW) == 0


def test_eyes_does_not_count_as_resolution(conn, client):
    add(conn, NOW - 8 * DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "eyes", "U_OP")

    assert sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW) == 1


def test_second_sweep_does_not_repeat(conn, client):
    add(conn, NOW - 8 * DAY)
    sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW)
    sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW + 3600)

    assert len(client.posted) == 1


def test_channel_without_stale_tracking_is_skipped(conn, client):
    add(conn, NOW - 8 * DAY, channel_id="C_KYC")

    assert sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW) == 0


def test_mentions_author_and_eyes_watchers(conn, client):
    add(conn, NOW - 8 * DAY, user_id="U_MARI")
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "eyes", "U_OP1")
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "eyes", "U_OP2")

    sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW)

    text = client.posted[0]["text"]
    assert "cc <@U_MARI> <@U_OP1> <@U_OP2>" in text


def test_author_not_mentioned_twice_when_they_added_eyes(conn, client):
    add(conn, NOW - 8 * DAY, user_id="U_MARI")
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "eyes", "U_MARI")

    sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW)

    assert client.posted[0]["text"].count("<@U_MARI>") == 1


def test_failed_post_leaves_row_for_retry(conn):
    class FailingClient(FakeClient):
        def chat_postMessage(self, *, channel, thread_ts, text):
            raise RuntimeError("slack is down")

    add(conn, NOW - 8 * DAY)
    poster = Poster(FailingClient())

    assert sweeper.run_sweep(conn, poster, CHANNELS, NOW) == 0
    assert db.get_escalation(conn, "C_PAY", str(NOW - 8 * DAY))["stale_notified"] == 0


def test_dry_run_counts_but_posts_nothing(conn, client):
    add(conn, NOW - 8 * DAY)

    sent = sweeper.run_sweep(conn, Poster(client, dry_run=True), CHANNELS, NOW)

    assert sent == 1
    assert client.posted == []


def test_silence_backlog_only_touches_tracked_channels(conn, client):
    add(conn, NOW - 8 * DAY, channel_id="C_PAY")
    add(conn, NOW - 8 * DAY, channel_id="C_KYC")

    silenced = sweeper.silence_backlog(conn, CHANNELS, NOW)

    assert silenced == 1
    assert sweeper.run_sweep(conn, Poster(client), CHANNELS, NOW) == 0


def test_purge_drops_rows_beyond_retention(conn):
    add(conn, NOW - 40 * DAY)
    add(conn, NOW - DAY)

    assert sweeper.purge(conn, NOW) == 1
    assert db.get_escalation(conn, "C_PAY", str(NOW - DAY)) is not None
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_sweeper.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.sweeper'`

- [ ] **Step 3: Написать реализацию**

`guard/sweeper.py`:

```python
"""Периодические задачи: напоминания о висяках и чистка старых записей."""

import logging

from guard import config, db, messages
from guard.poster import Poster

log = logging.getLogger(__name__)


def _tracked_channels(channels: dict) -> list[str]:
    return [cid for cid, rule in channels.items() if rule.get("stale_tracking")]


def _mentions(conn, channel_id: str, row) -> list[str]:
    """Автор эскалации плюс те, кто поставил :eyes:, без повторов."""
    ordered = [row["user_id"]]
    for user_id in db.users_with_reaction(conn, channel_id, row["ts"], config.IN_PROGRESS_EMOJI):
        if user_id not in ordered:
            ordered.append(user_id)
    return ordered


def run_sweep(conn, poster: Poster, channels: dict, now: float) -> int:
    """Одно напоминание на каждую висящую эскалацию. Возвращает число отправленных."""
    older_than = now - config.STALE_AFTER_DAYS * config.DAY_SECONDS
    newer_than = now - config.RETENTION_DAYS * config.DAY_SECONDS
    sent = 0

    for channel_id in _tracked_channels(channels):
        for row in db.find_stale(
            conn, channel_id=channel_id, older_than=older_than, newer_than=newer_than
        ):
            text = messages.stale_reminder_text(
                player_id=row["player_id"],
                transaction_id=row["transaction_id"],
                mention_user_ids=_mentions(conn, channel_id, row),
            )
            if poster.reply_in_thread(channel_id, row["ts"], text):
                db.mark_stale_notified(conn, channel_id, row["ts"])
                sent += 1

    if sent:
        log.info("Sweep posted %d stale reminders", sent)
    return sent


def silence_backlog(conn, channels: dict, now: float) -> int:
    """Холодный старт: пометить накопившийся архив как уже обработанный."""
    older_than = now - config.STALE_AFTER_DAYS * config.DAY_SECONDS
    silenced = 0
    for channel_id in _tracked_channels(channels):
        silenced += db.silence_old_stale(conn, channel_id=channel_id, older_than=older_than)

    if silenced:
        log.info("Cold start: silenced %d pre-existing stale escalations", silenced)
    return silenced


def purge(conn, now: float) -> int:
    removed = db.purge_older_than(conn, now - config.RETENTION_DAYS * config.DAY_SECONDS)
    if removed:
        log.info("Purged %d escalations beyond retention", removed)
    return removed
```

Замечание для реализующего: в dry-run `poster.reply_in_thread` возвращает True, поэтому напоминание считается и `stale_notified` в базе проставляется. Это осознанно — сухой прогон идёт на отдельной базе, которая потом выбрасывается (см. README в Task 12). Запускать `--dry-run` на боевой базе нельзя: она пометит висяки как обработанные, и настоящих напоминаний не будет.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_sweeper.py -v`
Expected: PASS, 12 тестов

- [ ] **Step 5: Коммит**

```bash
git add guard/sweeper.py tests/test_sweeper.py
git commit -m "feat: stale sweeper, cold-start silencing and retention purge"
```

---

### Task 10: Бэкфилл истории

**Files:**
- Create: `guard/backfill.py`
- Test: `tests/test_backfill.py`

**Interfaces:**
- Consumes: `guard.db`, `guard.parser`, `guard.config`.
- Produces:
  - `guard.backfill.backfill_channel(conn, client, channel_id: str, oldest: float) -> int` — вернуть число сохранённых сообщений. Идёт по курсору, пока `response_metadata.next_cursor` непустой.
  - `guard.backfill.run_backfill(conn, client, channels: dict, now: float) -> int`

`client` — объект с методом `conversations_history(channel=..., oldest=..., limit=..., cursor=...)`, возвращающим `dict` с ключами `messages: list[dict]` и опциональным `response_metadata: {"next_cursor": str}`.

- [ ] **Step 1: Написать падающий тест**

`tests/test_backfill.py`:

```python
import logging

import pytest

from guard import backfill, db

DAY = 86400.0
NOW = 1_000_000.0

CHANNELS = {
    "C_PAY": {"name": "2-payments", "match": "transaction_id", "stale_tracking": True},
    "C_KYC": {"name": "3-kyc", "match": "player_id", "stale_tracking": False},
}


class FakeHistoryClient:
    def __init__(self, pages_by_channel):
        self.pages_by_channel = pages_by_channel
        self.calls = []

    def conversations_history(self, *, channel, oldest, limit, cursor=None):
        self.calls.append({"channel": channel, "oldest": oldest, "cursor": cursor})
        pages = self.pages_by_channel.get(channel, [{"messages": []}])
        index = 0 if cursor is None else int(cursor)
        return pages[index]


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def message(ts, *, user="U_MARI", text=None, reactions=None):
    payload = {
        "type": "message",
        "ts": str(ts),
        "user": user,
        "text": text if text is not None else "Player ID: 688087847\nTransaction ID: 700000001779189773",
    }
    if reactions is not None:
        payload["reactions"] = reactions
    return payload


def test_stores_messages_from_history(conn):
    client = FakeHistoryClient({"C_PAY": [{"messages": [message(NOW - DAY)]}]})

    stored = backfill.backfill_channel(conn, client, "C_PAY", NOW - 7 * DAY)

    assert stored == 1
    row = db.get_escalation(conn, "C_PAY", str(NOW - DAY))
    assert row["transaction_id"] == "700000001779189773"


def test_stores_reactions_with_users(conn):
    client = FakeHistoryClient(
        {
            "C_PAY": [
                {
                    "messages": [
                        message(
                            NOW - DAY,
                            reactions=[
                                {"name": "eyes", "users": ["U_OP1", "U_OP2"], "count": 2},
                                {"name": "white_check_mark", "users": ["U_OP3"], "count": 1},
                            ],
                        )
                    ]
                }
            ]
        }
    )

    backfill.backfill_channel(conn, client, "C_PAY", NOW - 7 * DAY)

    assert db.is_resolved(conn, "C_PAY", str(NOW - DAY)) is True
    from guard import config
    assert db.users_with_reaction(conn, "C_PAY", str(NOW - DAY), config.IN_PROGRESS_EMOJI) == [
        "U_OP1",
        "U_OP2",
    ]


def test_follows_cursor_pagination(conn):
    client = FakeHistoryClient(
        {
            "C_PAY": [
                {"messages": [message(NOW - DAY)], "response_metadata": {"next_cursor": "1"}},
                {"messages": [message(NOW - 2 * DAY)], "response_metadata": {"next_cursor": ""}},
            ]
        }
    )

    stored = backfill.backfill_channel(conn, client, "C_PAY", NOW - 7 * DAY)

    assert stored == 2
    assert len(client.calls) == 2
    assert client.calls[1]["cursor"] == "1"


def test_skips_bot_and_thread_replies(conn):
    bot = message(NOW - DAY)
    bot["bot_id"] = "B1"
    reply = message(NOW - 2 * DAY)
    reply["thread_ts"] = str(NOW - 3 * DAY)
    client = FakeHistoryClient({"C_PAY": [{"messages": [bot, reply, message(NOW - 4 * DAY)]}]})

    stored = backfill.backfill_channel(conn, client, "C_PAY", NOW - 7 * DAY)

    assert stored == 1
    assert db.get_escalation(conn, "C_PAY", str(NOW - 4 * DAY)) is not None


def test_backfill_preserves_existing_notification_flags(conn):
    db.upsert_escalation(
        conn,
        channel_id="C_PAY",
        ts=str(NOW - DAY),
        user_id="U_MARI",
        created_at=NOW - DAY,
        player_id="688087847",
        transaction_id="700000001779189773",
        text="escalation",
    )
    db.mark_duplicate_notified(conn, "C_PAY", str(NOW - DAY))
    client = FakeHistoryClient({"C_PAY": [{"messages": [message(NOW - DAY)]}]})

    backfill.backfill_channel(conn, client, "C_PAY", NOW - 7 * DAY)

    assert db.get_escalation(conn, "C_PAY", str(NOW - DAY))["duplicate_notified"] == 1


def test_run_backfill_covers_every_channel_with_seven_day_window(conn):
    client = FakeHistoryClient(
        {
            "C_PAY": [{"messages": [message(NOW - DAY)]}],
            "C_KYC": [{"messages": [message(NOW - 2 * DAY, text="Player ID: 439545299")]}],
        }
    )

    stored = backfill.run_backfill(conn, client, CHANNELS, NOW)

    assert stored == 2
    assert {call["channel"] for call in client.calls} == {"C_PAY", "C_KYC"}
    assert all(call["oldest"] == NOW - 7 * DAY for call in client.calls)


def test_channel_failure_does_not_abort_the_rest(conn, caplog):
    caplog.set_level(logging.ERROR)

    class PartlyFailingClient(FakeHistoryClient):
        def conversations_history(self, *, channel, oldest, limit, cursor=None):
            if channel == "C_PAY":
                raise RuntimeError("not_in_channel")
            return super().conversations_history(
                channel=channel, oldest=oldest, limit=limit, cursor=cursor
            )

    client = PartlyFailingClient({"C_KYC": [{"messages": [message(NOW - DAY, text="Player ID: 439545299")]}]})

    stored = backfill.run_backfill(conn, client, CHANNELS, NOW)

    assert stored == 1
    assert "C_PAY" in caplog.text
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_backfill.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.backfill'`

- [ ] **Step 3: Написать реализацию**

`guard/backfill.py`:

```python
"""Догон истории каналов при старте, чтобы простой не оставлял дыр."""

import logging

from guard import config, db, parser

log = logging.getLogger(__name__)

PAGE_SIZE = 200


def _store_message(conn, channel_id: str, msg: dict) -> bool:
    if not parser.is_trackable_message(msg):
        return False

    ts = msg["ts"]
    ids = parser.parse_ids(msg.get("text", ""))
    db.upsert_escalation(
        conn,
        channel_id=channel_id,
        ts=ts,
        user_id=msg["user"],
        created_at=float(ts),
        player_id=ids.player_id,
        transaction_id=ids.transaction_id,
        text=msg.get("text", ""),
    )

    for reaction in msg.get("reactions", []):
        for user_id in reaction.get("users", []):
            db.add_reaction(conn, channel_id, ts, reaction["name"], user_id)

    return True


def backfill_channel(conn, client, channel_id: str, oldest: float) -> int:
    stored = 0
    cursor = None

    while True:
        response = client.conversations_history(
            channel=channel_id, oldest=oldest, limit=PAGE_SIZE, cursor=cursor
        )
        for msg in response.get("messages", []):
            if _store_message(conn, channel_id, msg):
                stored += 1

        cursor = (response.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break

    return stored


def run_backfill(conn, client, channels: dict, now: float) -> int:
    oldest = now - config.DUP_WINDOW_DAYS * config.DAY_SECONDS
    total = 0

    for channel_id in channels:
        try:
            count = backfill_channel(conn, client, channel_id, oldest)
        except Exception:
            log.error("Backfill failed for channel %s", channel_id, exc_info=True)
            continue
        log.info("Backfilled %d messages from %s", count, channel_id)
        total += count

    return total
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_backfill.py -v`
Expected: PASS, 7 тестов

- [ ] **Step 5: Прогнать весь набор**

Run: `pytest -v`
Expected: PASS, 102 теста

- [ ] **Step 6: Коммит**

```bash
git add guard/backfill.py tests/test_backfill.py
git commit -m "feat: seven-day history backfill on startup"
```

---

### Task 11: Точка входа и планировщик

**Files:**
- Create: `guard/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: всё из Task 1-10.
- Produces:
  - `guard.main.parse_args(argv: list[str]) -> argparse.Namespace` — флаги `--dry-run`, `--db`, `--sweep-once`.
  - `guard.main.bootstrap(conn, client, channels: dict, now: float, cold_start_silent: bool) -> None` — бэкфилл, затем при пустой базе гашение архива.
  - `guard.main.scheduler_loop(conn, poster, channels: dict, stop_event, interval: float, clock) -> None` — цикл, выходящий по `stop_event`.
  - `guard.main.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Написать падающий тест**

`tests/test_main.py`:

```python
import threading

import pytest

from guard import db, main
from guard.poster import Poster

DAY = 86400.0
NOW = 1_000_000.0

CHANNELS = {
    "C_PAY": {"name": "2-payments", "match": "transaction_id", "stale_tracking": True},
}


class FakeClient:
    def __init__(self, messages_by_channel=None):
        self.messages_by_channel = messages_by_channel or {}
        self.posted = []

    def conversations_history(self, *, channel, oldest, limit, cursor=None):
        return {"messages": self.messages_by_channel.get(channel, [])}

    def chat_getPermalink(self, *, channel, message_ts):
        return {"ok": True, "permalink": f"https://s/{channel}/{message_ts}"}

    def chat_postMessage(self, *, channel, thread_ts, text):
        self.posted.append({"channel": channel, "thread_ts": thread_ts, "text": text})
        return {"ok": True}


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def old_message(ts):
    return {
        "type": "message",
        "ts": str(ts),
        "user": "U_MARI",
        "text": "Player ID: 688087847\nTransaction ID: 700000001779189773",
    }


def test_parse_args_defaults():
    args = main.parse_args([])
    assert args.dry_run is False
    assert args.sweep_once is False


def test_parse_args_flags():
    args = main.parse_args(["--dry-run", "--sweep-once", "--db", "/tmp/x.db"])
    assert args.dry_run is True
    assert args.sweep_once is True
    assert args.db == "/tmp/x.db"


def test_cold_start_silences_backlog(conn):
    client = FakeClient({"C_PAY": [old_message(NOW - 8 * DAY)]})

    main.bootstrap(conn, client, CHANNELS, NOW, cold_start_silent=True)

    assert db.get_escalation(conn, "C_PAY", str(NOW - 8 * DAY))["stale_notified"] == 1


def test_without_cold_start_backlog_stays_actionable(conn):
    client = FakeClient({"C_PAY": [old_message(NOW - 8 * DAY)]})

    main.bootstrap(conn, client, CHANNELS, NOW, cold_start_silent=False)

    assert db.get_escalation(conn, "C_PAY", str(NOW - 8 * DAY))["stale_notified"] == 0


def test_cold_start_does_not_silence_on_restart_with_existing_data(conn):
    db.upsert_escalation(
        conn,
        channel_id="C_PAY",
        ts=str(NOW - 20 * DAY),
        user_id="U_OLD",
        created_at=NOW - 20 * DAY,
        player_id="1",
        transaction_id="100000",
        text="already known",
    )
    client = FakeClient({"C_PAY": [old_message(NOW - 8 * DAY)]})

    main.bootstrap(conn, client, CHANNELS, NOW, cold_start_silent=True)

    assert db.get_escalation(conn, "C_PAY", str(NOW - 8 * DAY))["stale_notified"] == 0


def test_scheduler_loop_runs_sweep_then_exits(conn):
    db.upsert_escalation(
        conn,
        channel_id="C_PAY",
        ts=str(NOW - 8 * DAY),
        user_id="U_MARI",
        created_at=NOW - 8 * DAY,
        player_id="688087847",
        transaction_id="700000001779189773",
        text="escalation",
    )
    client = FakeClient()
    stop = threading.Event()

    def clock():
        stop.set()  # выйти после первой итерации
        return NOW

    main.scheduler_loop(conn, Poster(client), CHANNELS, stop, interval=0.01, clock=clock)

    assert len(client.posted) == 1
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_main.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'guard.main'`

- [ ] **Step 3: Написать реализацию**

`guard/main.py`:

```python
"""Точка входа: бэкфилл, планировщик, Socket Mode."""

import argparse
import logging
import os
import sys
import threading
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from guard import backfill, config, db, handlers, sweeper
from guard.poster import Poster

log = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="escalation-guard")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Считать всё как обычно, но ничего не отправлять в Slack",
    )
    parser.add_argument(
        "--sweep-once",
        action="store_true",
        help="Прогнать один проход по висякам и выйти",
    )
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "guard.db"))
    return parser.parse_args(argv)


def bootstrap(conn, client, channels: dict, now: float, cold_start_silent: bool) -> None:
    """Догнать историю; на пустой базе погасить накопившийся архив."""
    was_empty = db.is_empty(conn)
    backfill.run_backfill(conn, client, channels, now)

    if was_empty and cold_start_silent:
        sweeper.silence_backlog(conn, channels, now)

    db.meta_set(conn, "last_backfill", str(now))


def scheduler_loop(conn, poster, channels: dict, stop_event, interval: float, clock=time.time) -> None:
    """Периодический проход. Выход по stop_event."""
    while not stop_event.is_set():
        now = clock()
        try:
            sweeper.run_sweep(conn, poster, channels, now)
            sweeper.purge(conn, now)
        except Exception:
            log.error("Sweep iteration failed", exc_info=True)

        if stop_event.wait(interval):
            return


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or [])
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    channels = config.load_channels()
    conn = db.connect(args.db)
    db.init_schema(conn)

    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    poster = Poster(app.client, dry_run=args.dry_run)

    bootstrap(conn, app.client, channels, time.time(), config.COLD_START_SILENT)

    if args.sweep_once:
        sweeper.run_sweep(conn, poster, channels, time.time())
        return 0

    @app.event("message")
    def on_message(event, logger):
        handlers.dispatch_message_event(conn, poster, channels, event, time.time())

    @app.event("reaction_added")
    def on_reaction_added(event, logger):
        handlers.handle_reaction_added(conn, event)

    @app.event("reaction_removed")
    def on_reaction_removed(event, logger):
        handlers.handle_reaction_removed(conn, event)

    stop_event = threading.Event()
    scheduler = threading.Thread(
        target=scheduler_loop,
        args=(conn, poster, channels, stop_event, config.SWEEP_INTERVAL_SECONDS),
        daemon=True,
        name="sweeper",
    )
    scheduler.start()

    log.info("Escalation Guard starting, dry_run=%s, channels=%s", args.dry_run, list(channels))
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_main.py -v`
Expected: PASS, 6 тестов

- [ ] **Step 5: Прогнать весь набор**

Run: `pytest -v`
Expected: PASS, 108 тестов

- [ ] **Step 6: Коммит**

```bash
git add guard/main.py tests/test_main.py
git commit -m "feat: entrypoint with backfill, scheduler thread and socket mode"
```

---

### Task 12: Манифест приложения, деплой, README

**Files:**
- Create: `deploy/manifest.yaml`
- Create: `deploy/escalation-guard.service`
- Create: `deploy/backup.sh`
- Create: `README.md`

**Interfaces:**
- Consumes: `guard.main` из Task 11.
- Produces: артефакты установки. Кода, от которого зависят другие задачи, здесь нет.

- [ ] **Step 1: Написать манифест приложения**

`deploy/manifest.yaml`:

```yaml
display_information:
  name: Escalation Guard
  description: Flags duplicate escalations and unresolved requests
  background_color: "#2c2d30"
features:
  bot_user:
    display_name: Escalation Guard
    always_online: true
oauth_config:
  scopes:
    bot:
      - channels:history
      - channels:read
      - chat:write
      - reactions:read
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - reaction_added
      - reaction_removed
  interactivity:
    is_enabled: false
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

- [ ] **Step 2: Написать systemd unit**

`deploy/escalation-guard.service`:

```ini
[Unit]
Description=Braverave Escalation Guard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=escalation
WorkingDirectory=/opt/escalation-guard
EnvironmentFile=/opt/escalation-guard/.env
ExecStart=/opt/escalation-guard/.venv/bin/python -m guard.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Написать скрипт бэкапа**

`deploy/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${DB_PATH:-/var/lib/escalation-guard/guard.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/escalation-guard}"
STAMP="$(date +%Y-%m-%d)"

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/guard-$STAMP.db'"
find "$BACKUP_DIR" -name 'guard-*.db' -mtime +14 -delete
```

- [ ] **Step 4: Написать README**

`README.md`:

````markdown
# Braverave Escalation Guard

Slack-бот для воркспейса braverave. Отвечает в тред, когда эскалация дублирует
предыдущую, и напоминает про payments-эскалации, висящие 7 дней без решения.

| Канал         | Дубликат по    | Напоминание о 7 днях |
|---------------|----------------|----------------------|
| `#2-payments` | Transaction ID | да                   |
| `#7-bonuses`  | Player ID      | нет                  |
| `#3-kyc`      | Player ID      | нет                  |

Решением считается реакция `:white_check_mark:`. Реакция `:eyes:` показывает,
что запрос взяли в работу, но напоминание не отменяет. Если дубликатов нет,
бот молчит.

## Установка приложения в Slack

1. Открыть https://api.slack.com/apps, нажать Create New App, выбрать
   From an app manifest, выбрать воркспейс braverave.
2. Вставить содержимое `deploy/manifest.yaml`, создать приложение.
3. Basic Information -> App-Level Tokens -> Generate Token and Scopes:
   имя любое, скоуп `connections:write`. Полученный `xapp-...` это `SLACK_APP_TOKEN`.
4. Install App -> Install to Workspace. Полученный `xoxb-...` это `SLACK_BOT_TOKEN`.
5. Пригласить бота в каналы: в каждом из трёх набрать `/invite @Escalation Guard`.
6. Взять channel ID: правый клик по каналу -> Copy link, ID это последний
   сегмент URL, вида `C0123ABCDEF`.

## Развёртывание на VPS

```bash
sudo useradd --system --home /opt/escalation-guard escalation
sudo mkdir -p /opt/escalation-guard /var/lib/escalation-guard
sudo chown -R escalation:escalation /opt/escalation-guard /var/lib/escalation-guard

sudo -u escalation git clone <repo-url> /opt/escalation-guard
cd /opt/escalation-guard
sudo -u escalation python3 -m venv .venv
sudo -u escalation .venv/bin/pip install -r requirements.txt

sudo -u escalation cp .env.example .env
sudo -u escalation nano .env   # вписать токены и channel ID
sudo chmod 600 .env

sudo cp deploy/escalation-guard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now escalation-guard
journalctl -u escalation-guard -f
```

## Первый запуск: сухой прогон

Первые сутки прогнать без отправки сообщений, на отдельной базе:

```bash
sudo -u escalation DB_PATH=/tmp/dry.db .venv/bin/python -m guard.main --dry-run
```

В логе будут строки `DRY-RUN would reply ...` с полным текстом. Убедиться, что
бот срабатывает там, где ожидается, и молчит там, где дубликатов нет.
Затем удалить `/tmp/dry.db` и запустить сервис обычным образом.

## Бэкап

```bash
sudo crontab -e
# 0 4 * * * DB_PATH=/var/lib/escalation-guard/guard.db /opt/escalation-guard/deploy/backup.sh
```

## Разработка

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -v
```

## Настройка

Пороги и эмодзи лежат в `guard/config.py`. Добавить четвёртый канал — одна
строка в `load_channels()` плюс переменная окружения. Правок кода не требуется.
````

- [ ] **Step 5: Проверить, что манифест валиден как YAML, а сервис запускается**

Run: `python -c "import yaml,sys; yaml.safe_load(open('deploy/manifest.yaml')); print('manifest ok')"`
Expected: `manifest ok`

Если `yaml` не установлен, поставить его разово: `pip install pyyaml`. В
`requirements.txt` он не нужен, бот YAML не читает.

Run: `chmod +x deploy/backup.sh && bash -n deploy/backup.sh && echo "backup script ok"`
Expected: `backup script ok`

- [ ] **Step 6: Прогнать весь набор тестов последний раз**

Run: `pytest -v`
Expected: PASS, 108 тестов

- [ ] **Step 7: Коммит**

```bash
git add deploy README.md
git commit -m "docs: app manifest, systemd unit, backup script and setup guide"
```

---

## Проверка готовности перед передачей в прод

- [ ] `pytest -v` зелёный, 108 тестов.
- [ ] Приложение создано из манифеста, оба токена получены.
- [ ] Бот приглашён во все три канала.
- [ ] Три channel ID прописаны в `.env`, права на файл 600.
- [ ] Сутки сухого прогона пройдены, лог просмотрен.
- [ ] Сервис под systemd поднят, `journalctl` показывает `Escalation Guard starting`.
- [ ] Cron бэкапа базы добавлен.
