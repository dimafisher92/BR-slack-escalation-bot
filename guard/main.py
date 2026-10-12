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


def with_retries(action, *, label: str, sleep=time.sleep):
    """Выполнить action, переживая кратковременные сетевые сбои.

    Паузы растут вдвое. Исчерпав попытки, пробрасывает исходное исключение:
    тогда процесс действительно нездоров и его должен перезапустить systemd.
    """
    delay = config.STARTUP_RETRY_BASE_SECONDS
    for attempt in range(1, config.STARTUP_RETRY_ATTEMPTS + 1):
        try:
            return action()
        except Exception:
            if attempt == config.STARTUP_RETRY_ATTEMPTS:
                log.error("%s failed after %d attempts, giving up", label, attempt, exc_info=True)
                raise
            log.warning(
                "%s failed on attempt %d/%d, retrying in %ds",
                label, attempt, config.STARTUP_RETRY_ATTEMPTS, delay, exc_info=True,
            )
            sleep(delay)
            delay *= 2


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

    # App() при создании ходит в auth.test, bootstrap - в conversations.history.
    # Оба шага сетевые и до этой обёртки валили процесс на первом же сбое DNS.
    app = with_retries(lambda: App(token=os.environ["SLACK_BOT_TOKEN"]), label="Slack auth")
    poster = Poster(app.client, dry_run=args.dry_run)

    with_retries(
        lambda: bootstrap(conn, app.client, channels, time.time(), config.COLD_START_SILENT),
        label="Backfill",
    )

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
