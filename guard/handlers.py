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
        conn,
        channel_id=channel_id,
        field=field,
        value=value,
        exclude_ts=ts,
        since=since,
        before=row["created_at"],
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
    if not ts:
        return

    existing = db.get_escalation(conn, channel_id, ts)
    if existing is None:
        return

    # Правки, не трогающие ID сверки, не должны переоткрывать проверку дубля
    # (спека §5: "Если Transaction ID изменился..."). Иначе правка более
    # раннего из пары дублей находит более позднего и постит "назад во времени".
    field = rule["match"]
    previous_value = existing[field]
    # Payload правки без "user" не должен стирать сохранённого автора.
    user_id = inner.get("user") or existing["user_id"]

    _store(conn, channel_id, ts, user_id, inner.get("text", ""))

    updated = db.get_escalation(conn, channel_id, ts)
    if updated[field] != previous_value:
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
