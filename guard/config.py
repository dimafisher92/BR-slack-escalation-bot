"""Настройки бота. Секреты и channel ID приходят из окружения."""

import os

DUP_WINDOW_DAYS = 7
# В bonuses совпадение идёт по одному player id, то есть значит всего лишь
# "тот же человек". На реальной истории 7 дней давали 24 срабатывания на
# 74 сообщения, почти все про уже закрытые обращения по другому поводу.
# 12 часов оставляют 3, и все они укладываются в одну смену.
BONUSES_DUP_WINDOW_DAYS = 0.5
STALE_AFTER_DAYS = 7
RETENTION_DAYS = 30
SWEEP_INTERVAL_SECONDS = 3600
MAX_DUPLICATES_SHOWN = 3

RESOLVED_EMOJI = frozenset({"white_check_mark", "heavy_check_mark"})
IN_PROGRESS_EMOJI = frozenset({"eyes"})

# При первом запуске на пустой базе не поднимать накопившийся архив.
COLD_START_SILENT = True

DAY_SECONDS = 86400

# Запуск переживает кратковременный сетевой сбой сам. 25 сентября 2026 года
# резолвер на VPS моргнул, Slack Web API упал с name resolution, процесс
# вышел с ошибкой, и systemd после пяти попыток сдался насовсем.
STARTUP_RETRY_ATTEMPTS = 6
STARTUP_RETRY_BASE_SECONDS = 5


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
            "dup_window_days": DUP_WINDOW_DAYS,
        },
        _require("CHANNEL_BONUSES"): {
            "name": "7-bonuses",
            "match": "player_id",
            "stale_tracking": False,
            "dup_window_days": BONUSES_DUP_WINDOW_DAYS,
        },
        _require("CHANNEL_KYC"): {
            "name": "3-kyc",
            "match": "player_id",
            "stale_tracking": False,
            "dup_window_days": DUP_WINDOW_DAYS,
        },
    }
