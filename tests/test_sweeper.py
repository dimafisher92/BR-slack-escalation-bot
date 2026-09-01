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
