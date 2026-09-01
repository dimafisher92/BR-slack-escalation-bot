from guard.messages import (
    Match,
    duplicate_player_text,
    duplicate_transaction_text,
    humanize_age,
    stale_reminder_text,
    status_label,
)

DAY = 86400.0
NOW = 1_000_000.0


def test_humanize_minutes_hours_days():
    assert humanize_age(30) == "1 minute ago"
    assert humanize_age(120) == "2 minutes ago"
    assert humanize_age(3600) == "1 hour ago"
    assert humanize_age(7200) == "2 hours ago"
    assert humanize_age(DAY) == "1 day ago"
    assert humanize_age(2 * DAY) == "2 days ago"


def test_status_label_variants():
    assert status_label(frozenset({"white_check_mark"})) == "resolved :white_check_mark:"
    assert status_label(frozenset({"eyes"})) == "in progress :eyes:"
    assert status_label(frozenset()) == "no reaction yet"


def test_resolved_wins_over_eyes():
    assert status_label(frozenset({"eyes", "white_check_mark"})) == "resolved :white_check_mark:"


def test_duplicate_transaction_text_contains_id_age_author_and_link():
    match = Match(
        permalink="https://braverave.slack.com/archives/C_PAY/p1",
        user_id="U_MARI",
        created_at=NOW - 2 * DAY,
        reaction_names=frozenset({"eyes"}),
    )

    text = duplicate_transaction_text(transaction_id="TX123456", match=match, now=NOW)

    assert "Duplicate escalation" in text
    assert "TX123456" in text
    assert "2 days ago" in text
    assert "<@U_MARI>" in text
    assert "in progress :eyes:" in text
    assert "https://braverave.slack.com/archives/C_PAY/p1" in text


def test_duplicate_transaction_text_without_permalink_still_readable():
    match = Match(permalink=None, user_id="U_MARI", created_at=NOW - DAY, reaction_names=frozenset())
    text = duplicate_transaction_text(transaction_id="TX1", match=match, now=NOW)
    assert "http" not in text
    assert "1 day ago" in text


def test_duplicate_player_text_lists_matches():
    matches = [
        Match(permalink="https://s/1", user_id="U_A", created_at=NOW - DAY, reaction_names=frozenset()),
        Match(
            permalink="https://s/2",
            user_id="U_B",
            created_at=NOW - 3 * DAY,
            reaction_names=frozenset({"white_check_mark"}),
        ),
    ]

    text = duplicate_player_text(player_id="P42", matches=matches, now=NOW)

    assert "Possible duplicate" in text
    assert "P42" in text
    assert "2 escalations" in text
    assert "<@U_A>" in text and "<@U_B>" in text
    assert "resolved :white_check_mark:" in text


def test_duplicate_player_text_uses_singular_for_one_match():
    matches = [Match(permalink=None, user_id="U_A", created_at=NOW - DAY, reaction_names=frozenset())]
    text = duplicate_player_text(player_id="P42", matches=matches, now=NOW)
    assert "1 escalation " in text
    assert "escalations" not in text


def test_duplicate_player_text_truncates_to_three_and_reports_rest():
    matches = [
        Match(permalink=None, user_id=f"U_{i}", created_at=NOW - i * DAY, reaction_names=frozenset())
        for i in range(1, 6)
    ]

    text = duplicate_player_text(player_id="P42", matches=matches, now=NOW)

    assert text.count("\n- ") == 3
    assert "and 2 more" in text


def test_stale_reminder_mentions_author_and_watchers():
    text = stale_reminder_text(
        player_id="688087847",
        transaction_id="700000001779189773",
        mention_user_ids=["U_MARI", "U_OP1"],
    )

    assert "7 days without resolution" in text
    assert "688087847" in text
    assert "700000001779189773" in text
    assert "cc <@U_MARI> <@U_OP1>" in text


def test_stale_reminder_omits_cc_line_when_nobody_to_ping():
    text = stale_reminder_text(player_id="P1", transaction_id=None, mention_user_ids=[])
    assert "cc " not in text
    assert "Transaction" not in text
