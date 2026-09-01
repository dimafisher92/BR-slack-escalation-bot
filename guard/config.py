"""Настройки бота. Секреты и channel ID приходят из окружения."""

import os

DUP_WINDOW_DAYS = 7
STALE_AFTER_DAYS = 7
RETENTION_DAYS = 30
SWEEP_INTERVAL_SECONDS = 3600
MAX_DUPLICATES_SHOWN = 3

RESOLVED_EMOJI = frozenset({"white_check_mark", "heavy_check_mark"})
IN_PROGRESS_EMOJI = frozenset({"eyes"})

# При первом запуске на пустой базе не поднимать накопившийся архив.
COLD_START_SILENT = True

DAY_SECONDS = 86400


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_channels() -> dict[str, dict]:
    """Карта channel_id -> правило обработки канала."""
    return {
        _require("CHANNEL_PAYMENTS"): {
            "name": "2-payments",
            "match": "transaction_id",
            "stale_tracking": True,
        },
        _require("CHANNEL_BONUSES"): {
            "name": "7-bonuses",
            "match": "player_id",
            "stale_tracking": False,
        },
        _require("CHANNEL_KYC"): {
            "name": "3-kyc",
            "match": "player_id",
            "stale_tracking": False,
        },
    }
