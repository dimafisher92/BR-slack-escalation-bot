import pytest

from guard import config


def test_load_channels_builds_rules_from_env(monkeypatch):
    monkeypatch.setenv("CHANNEL_PAYMENTS", "C_PAY")
    monkeypatch.setenv("CHANNEL_BONUSES", "C_BON")
    monkeypatch.setenv("CHANNEL_KYC", "C_KYC")

    channels = config.load_channels()

    assert channels["C_PAY"] == {
        "name": "2-payments",
        "match": "transaction_id",
        "stale_tracking": True,
    }
    assert channels["C_BON"]["match"] == "player_id"
    assert channels["C_BON"]["stale_tracking"] is False
    assert channels["C_KYC"]["match"] == "player_id"
    assert channels["C_KYC"]["stale_tracking"] is False


def test_load_channels_fails_loudly_when_env_missing(monkeypatch):
    monkeypatch.delenv("CHANNEL_PAYMENTS", raising=False)
    monkeypatch.setenv("CHANNEL_BONUSES", "C_BON")
    monkeypatch.setenv("CHANNEL_KYC", "C_KYC")

    with pytest.raises(RuntimeError, match="CHANNEL_PAYMENTS"):
        config.load_channels()


def test_windows_are_seven_days():
    assert config.DUP_WINDOW_DAYS == 7
    assert config.STALE_AFTER_DAYS == 7
    assert config.RETENTION_DAYS == 30
