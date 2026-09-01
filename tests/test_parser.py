from guard.parser import ParsedIds, is_trackable_message, normalize, parse_ids

PAYMENTS = (
    "*Player ID:* 688087847\n"
    "*Transaction ID:* 700000001779189773\n"
    "*Type:* Deposit\n"
    "*Amount and Currency:* 4 000 ARS\n"
    "*Issue:* a different amount"
)

BONUSES = (
    "*Player ID:* 743080300\n"
    "*Promo / bonus:* bonus\n"
    "*Issue / need:* The customer's bonus was canceled."
)

KYC = (
    "*Player ID:* 439545299\n"
    "*Document + status:*\n"
    "*Issue:* The customer wants to change their email address."
)


def test_parses_payments_message():
    ids = parse_ids(PAYMENTS)
    assert ids.player_id == "688087847"
    assert ids.transaction_id == "700000001779189773"
    assert ids.extra_transaction_ids == ()


def test_parses_bonuses_message():
    ids = parse_ids(BONUSES)
    assert ids.player_id == "743080300"
    assert ids.transaction_id is None


def test_parses_kyc_message_with_empty_document_field():
    ids = parse_ids(KYC)
    assert ids.player_id == "439545299"
    assert ids.transaction_id is None


def test_tolerates_spacing_case_and_missing_colon():
    ids = parse_ids("player  id 688087847\nTRANSACTION ID - 700000001779189773")
    assert ids.player_id == "688087847"
    assert ids.transaction_id == "700000001779189773"


def test_tolerates_non_breaking_space():
    ids = parse_ids("Player ID:\u00a0688087847")
    assert ids.player_id == "688087847"


def test_returns_none_when_no_ids_present():
    ids = parse_ids("Please take a look at this account, thanks")
    assert ids == ParsedIds(player_id=None, transaction_id=None, extra_transaction_ids=())


def test_handles_none_text():
    assert parse_ids(None) == ParsedIds(None, None, ())


def test_ignores_short_numbers_that_are_not_ids():
    # 3 цифры не могут быть Player ID, порог 4
    assert parse_ids("Player ID: 123").player_id is None


def test_reports_extra_transaction_ids():
    ids = parse_ids(
        "Player ID: 688087847\n"
        "Transaction ID: 700000001779189773\n"
        "Transaction ID: 700000001779189774"
    )
    assert ids.transaction_id == "700000001779189773"
    assert ids.extra_transaction_ids == ("700000001779189774",)


def test_normalize_strips_markdown_and_collapses_spaces():
    assert normalize("*Player   ID:*  `688087847`") == "Player ID: 688087847"


def test_is_trackable_accepts_plain_top_level_message():
    assert is_trackable_message({"ts": "1.0", "user": "U1", "text": "hi"}) is True


def test_is_trackable_rejects_thread_reply():
    assert is_trackable_message({"ts": "2.0", "thread_ts": "1.0", "user": "U1"}) is False


def test_is_trackable_accepts_thread_parent():
    # У родителя треда thread_ts равен ts
    assert is_trackable_message({"ts": "1.0", "thread_ts": "1.0", "user": "U1"}) is True


def test_is_trackable_rejects_bot_message():
    assert is_trackable_message({"ts": "1.0", "bot_id": "B1", "text": "hi"}) is False


def test_is_trackable_rejects_service_subtypes():
    assert is_trackable_message({"ts": "1.0", "user": "U1", "subtype": "channel_join"}) is False
