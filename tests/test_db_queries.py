import pytest

from guard import db

DAY = 86400.0
NOW = 1_000_000.0


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def add(conn, ts, *, channel_id="C_PAY", transaction_id="TX1", player_id="P1", user_id="U1"):
    db.upsert_escalation(
        conn,
        channel_id=channel_id,
        ts=str(ts),
        user_id=user_id,
        created_at=float(ts),
        player_id=player_id,
        transaction_id=transaction_id,
        text="escalation",
    )


def test_finds_duplicate_inside_window(conn):
    add(conn, NOW - 2 * DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert [row["ts"] for row in found] == [str(NOW - 2 * DAY)]


def test_ignores_match_outside_window(conn):
    add(conn, NOW - 8 * DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert found == []


def test_includes_match_exactly_on_window_boundary(conn):
    add(conn, NOW - 7 * DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert len(found) == 1


def test_does_not_match_across_channels(conn):
    add(conn, NOW - DAY, channel_id="C_KYC")
    add(conn, NOW, channel_id="C_PAY")

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert found == []


def test_newest_matches_come_first(conn):
    add(conn, NOW - 5 * DAY)
    add(conn, NOW - DAY)
    add(conn, NOW)

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="transaction_id", value="TX1",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert [row["ts"] for row in found] == [str(NOW - DAY), str(NOW - 5 * DAY)]


def test_player_field_matching(conn):
    add(conn, NOW - DAY, transaction_id="TX_A", player_id="P9")
    add(conn, NOW, transaction_id="TX_B", player_id="P9")

    found = db.find_duplicates(
        conn, channel_id="C_PAY", field="player_id", value="P9",
        exclude_ts=str(NOW), since=NOW - 7 * DAY,
    )

    assert len(found) == 1


def test_rejects_unknown_field(conn):
    with pytest.raises(ValueError, match="text"):
        db.find_duplicates(
            conn, channel_id="C_PAY", field="text", value="x",
            exclude_ts="1", since=0.0,
        )


def test_find_stale_returns_old_unresolved(conn):
    add(conn, NOW - 8 * DAY)

    stale = db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    )

    assert [row["ts"] for row in stale] == [str(NOW - 8 * DAY)]


def test_find_stale_skips_fresh(conn):
    add(conn, NOW - 3 * DAY)
    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_find_stale_skips_resolved(conn):
    add(conn, NOW - 8 * DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "white_check_mark", "U_OP")

    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_find_stale_does_not_skip_eyes_only(conn):
    add(conn, NOW - 8 * DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 8 * DAY), "eyes", "U_OP")

    assert len(db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    )) == 1


def test_find_stale_skips_already_notified(conn):
    add(conn, NOW - 8 * DAY)
    db.mark_stale_notified(conn, "C_PAY", str(NOW - 8 * DAY))

    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_find_stale_ignores_beyond_retention(conn):
    add(conn, NOW - 40 * DAY)

    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_silence_old_stale_marks_and_counts(conn):
    add(conn, NOW - 8 * DAY, transaction_id="TX_A")
    add(conn, NOW - 9 * DAY, transaction_id="TX_B")
    add(conn, NOW - DAY, transaction_id="TX_C")

    marked = db.silence_old_stale(conn, channel_id="C_PAY", older_than=NOW - 7 * DAY)

    assert marked == 2
    assert db.find_stale(
        conn, channel_id="C_PAY", older_than=NOW - 7 * DAY, newer_than=NOW - 30 * DAY
    ) == []


def test_purge_removes_old_rows_and_reactions(conn):
    add(conn, NOW - 40 * DAY)
    add(conn, NOW - DAY)
    db.add_reaction(conn, "C_PAY", str(NOW - 40 * DAY), "eyes", "U_OP")

    removed = db.purge_older_than(conn, NOW - 30 * DAY)

    assert removed == 1
    assert db.get_escalation(conn, "C_PAY", str(NOW - 40 * DAY)) is None
    assert db.reaction_names(conn, "C_PAY", str(NOW - 40 * DAY)) == set()
    assert db.get_escalation(conn, "C_PAY", str(NOW - DAY)) is not None
