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
    # Не только окно сверки дублей: если бэкфилл смотрит только на 7 дней назад,
    # реакции, добавленные во время простоя на эскалациях 6.5-7 дней от роду,
    # никогда не переиндексируются, и sweeper шлёт ложные напоминания про уже
    # решённые тикеты. Догоняем весь retention-период, а не только dup-окно.
    window_days = max(config.DUP_WINDOW_DAYS, config.RETENTION_DAYS)
    oldest = now - window_days * config.DAY_SECONDS
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
