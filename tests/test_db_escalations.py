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
