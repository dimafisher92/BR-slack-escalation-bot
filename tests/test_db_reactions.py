import pytest

from guard import config, db


@pytest.fixture()
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    db.upsert_escalation(
        connection,
        channel_id="C_PAY",
        ts="1000.0",
        user_id="U_MARI",
        created_at=1000.0,
        player_id="688087847",
        transaction_id="700000001779189773",
        text="escalation",
    )
    yield connection
    connection.close()


def test_add_and_read_reaction_names(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    assert db.reaction_names(conn, "C_PAY", "1000.0") == {"eyes"}


def test_add_reaction_is_idempotent(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    assert db.users_with_reaction(conn, "C_PAY", "1000.0", config.IN_PROGRESS_EMOJI) == ["U_OP1"]


def test_unresolved_without_checkmark(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is False


def test_resolved_with_checkmark(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is True


def test_heavy_check_mark_also_resolves(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "heavy_check_mark", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is True


def test_stays_resolved_when_one_of_two_removes_checkmark(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP2")

    db.remove_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")

    assert db.is_resolved(conn, "C_PAY", "1000.0") is True


def test_becomes_unresolved_when_last_checkmark_removed(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    db.remove_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP1")
    assert db.is_resolved(conn, "C_PAY", "1000.0") is False


def test_users_with_eyes_returned_in_insertion_order(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP2")
    db.add_reaction(conn, "C_PAY", "1000.0", "white_check_mark", "U_OP3")

    assert db.users_with_reaction(conn, "C_PAY", "1000.0", config.IN_PROGRESS_EMOJI) == [
        "U_OP1",
        "U_OP2",
    ]


def test_deleting_escalation_removes_its_reactions(conn):
    db.add_reaction(conn, "C_PAY", "1000.0", "eyes", "U_OP1")
    db.delete_escalation(conn, "C_PAY", "1000.0")
    assert db.reaction_names(conn, "C_PAY", "1000.0") == set()
