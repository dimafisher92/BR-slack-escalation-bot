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


class FailingClient(FakeClient):
    def chat_postMessage(self, *, channel, thread_ts, text):
        raise RuntimeError("Slack is down")


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


def test_editing_earlier_duplicate_without_id_change_stays_silent(conn, poster, client):
    # Finding 1's exact shape: A (earlier) and B (later) share a Transaction ID,
    # so B correctly gets the "already escalated" reply. Editing A afterwards
    # (a typo fix, ID unchanged) must NOT re-open the duplicate check on A --
    # if it did, find_duplicates would return B (which is newer than A), and
    # the bot would post a backwards "already escalated" reply on A's thread.
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW - 2 * DAY), NOW - 2 * DAY)
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW - DAY), NOW - DAY)
    assert len(client.posted) == 1
    assert client.posted[0]["thread_ts"] == str(NOW - DAY)

    changed = {
        "channel": "C_PAY",
        "subtype": "message_changed",
        "message": {
            "ts": str(NOW - 2 * DAY),
            "user": "U_MARI",
            "text": "Player ID: 688087847\nTransaction ID: 700000001779189773 (typo fixed)",
        },
    }
    handlers.handle_message_changed(conn, poster, CHANNELS, changed, NOW)

    assert len(client.posted) == 1
    assert db.get_escalation(conn, "C_PAY", str(NOW - 2 * DAY))["duplicate_notified"] == 0


def test_message_changed_without_user_keeps_existing_author(conn, poster, client):
    handlers.handle_message(conn, poster, CHANNELS, payments_event(NOW, user="U_MARI"), NOW)

    changed = {
        "channel": "C_PAY",
        "subtype": "message_changed",
        "message": {
            "ts": str(NOW),
            "text": "Player ID: 688087847\nTransaction ID: 700000001779189773 (typo fixed)",
        },
    }
    handlers.handle_message_changed(conn, poster, CHANNELS, changed, NOW)

    assert db.get_escalation(conn, "C_PAY", str(NOW))["user_id"] == "U_MARI"


def test_own_thread_reply_arriving_as_message_event_is_ignored(conn, poster, client):
    # The bot's own duplicate/stale replies come back through the message
    # stream as a bot-authored reply inside the original message's thread.
    # is_trackable_message's guards (bot_id, thread reply) must hold at the
    # handler level too, or the bot would index and re-process its own posts.
    event = payments_event(NOW)
    event["bot_id"] = "B_GUARD"
    event["subtype"] = "bot_message"
    event["thread_ts"] = str(NOW - DAY)

    handlers.handle_message(conn, poster, CHANNELS, event, NOW)

    assert db.get_escalation(conn, "C_PAY", str(NOW)) is None
    assert client.posted == []


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


def test_failed_post_leaves_flag_unset_and_allows_retry(conn):
    failing_client = FailingClient()
    failing_poster = Poster(failing_client)

    handlers.handle_message(conn, failing_poster, CHANNELS, payments_event(NOW - DAY), NOW - DAY)
    handlers.handle_message(conn, failing_poster, CHANNELS, payments_event(NOW), NOW)

    assert failing_client.posted == []
    assert db.get_escalation(conn, "C_PAY", str(NOW))["duplicate_notified"] == 0

    working_client = FakeClient()
    working_poster = Poster(working_client)
    handlers.handle_message(conn, working_poster, CHANNELS, payments_event(NOW), NOW)

    assert len(working_client.posted) == 1
    assert "Duplicate escalation" in working_client.posted[0]["text"]
    assert db.get_escalation(conn, "C_PAY", str(NOW))["duplicate_notified"] == 1
