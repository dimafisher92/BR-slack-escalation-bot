import threading

import pytest

from guard import config, db, main
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
        messages = self.messages_by_channel.get(channel, [])
        # oldest приходит строкой в формате Slack, как в проде.
        return {"messages": [m for m in messages if float(m["ts"]) >= float(oldest)]}

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


def test_with_retries_returns_immediately_on_success():
    slept = []
    assert main.with_retries(lambda: "ok", label="x", sleep=slept.append) == "ok"
    assert slept == []


def test_with_retries_survives_a_transient_failure():
    calls = []
    slept = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("Temporary failure in name resolution")
        return "ok"

    assert main.with_retries(flaky, label="x", sleep=slept.append) == "ok"
    assert len(calls) == 3
    # Экспоненциальная пауза, и после удачной попытки больше не ждём.
    assert slept == [config.STARTUP_RETRY_BASE_SECONDS, config.STARTUP_RETRY_BASE_SECONDS * 2]


def test_with_retries_gives_up_and_reraises():
    slept = []

    def always_fails():
        raise OSError("Temporary failure in name resolution")

    with pytest.raises(OSError, match="name resolution"):
        main.with_retries(always_fails, label="x", sleep=slept.append)

    # Попыток ровно столько, сколько в конфиге, и после последней не спим.
    assert len(slept) == config.STARTUP_RETRY_ATTEMPTS - 1
