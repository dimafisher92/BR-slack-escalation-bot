import logging
import re

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
        page = pages[index]
        # Настоящий Slack получает oldest строкой и парсит её сам, поэтому
        # фейк обязан принимать строку, а не только float. Пока он сравнивал
        # float с float, он был согласен сам с собой и пропустил боевой баг.
        filtered = [m for m in page.get("messages", []) if float(m["ts"]) >= float(oldest)]
        return {**page, "messages": filtered}


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


def test_run_backfill_covers_every_channel_with_retention_window(conn):
    # Не 7-дневное dup-окно: бэкфилл должен догонять весь retention-период
    # (30 дней), иначе реакции, добавленные во время простоя на эскалациях
    # 6.5-7 дней от роду, никогда не переиндексируются (finding 2).
    client = FakeHistoryClient(
        {
            "C_PAY": [{"messages": [message(NOW - DAY)]}],
            "C_KYC": [{"messages": [message(NOW - 2 * DAY, text="Player ID: 439545299")]}],
        }
    )

    stored = backfill.run_backfill(conn, client, CHANNELS, NOW)

    assert stored == 2
    assert {call["channel"] for call in client.calls} == {"C_PAY", "C_KYC"}
    assert all(float(call["oldest"]) == NOW - 30 * DAY for call in client.calls)


def test_backfill_sends_slack_formatted_timestamp(conn):
    # Slack принимает ts как десять цифр, точка и ровно шесть знаков. Сырой
    # Python-float стрингуется в семь и более знаков (1785691099.8141618),
    # и Slack на такой оценке молча возвращает пустую выборку: ни ошибки,
    # ни warning. Именно так бэкфилл в проде вернул 0 сообщений из каналов,
    # где их сотни.
    client = FakeHistoryClient({"C_PAY": [{"messages": []}]})

    backfill.run_backfill(conn, client, {"C_PAY": CHANNELS["C_PAY"]}, NOW)

    sent = client.calls[0]["oldest"]
    assert isinstance(sent, str), f"oldest ушёл как {type(sent).__name__}, а не строкой"
    assert re.fullmatch(r"-?\d+\.\d{6}", sent), f"неверный формат ts: {sent!r}"


def test_backfill_truncates_a_real_epoch_float_to_six_places(conn):
    client = FakeHistoryClient({"C_PAY": [{"messages": []}]})
    now = 1788283099.8141618  # даёт семь знаков после точки через str()

    backfill.run_backfill(conn, client, {"C_PAY": CHANNELS["C_PAY"]}, now)

    assert client.calls[0]["oldest"] == "1785691099.814162"


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
