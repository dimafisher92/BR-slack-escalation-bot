"""Извлечение Player ID и Transaction ID из текста эскалации."""

import re
from dataclasses import dataclass

_MARKDOWN_CHARS = str.maketrans({"*": "", "_": "", "`": ""})
_WHITESPACE_RE = re.compile(r"[^\S\n]+")

# Slack-permalink несёт ts сообщения слитно: p<10 цифр секунд><6 цифр микросекунд>.
PERMALINK_RE = re.compile(r"/archives/[^/\s]+/p(\d{10})(\d{6})")

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


def referenced_message_ts(text: str | None) -> set[str]:
    """ts сообщений, на которые в тексте есть permalink.

    Считается по сырому тексту, а не по normalize(): в URL встречаются
    подчёркивания, которые нормализация вырезает.
    """
    if not text:
        return set()
    return {f"{seconds}.{micros}" for seconds, micros in PERMALINK_RE.findall(text)}
