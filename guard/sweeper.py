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
