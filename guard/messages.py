"""Тексты, уходящие в Slack. Чистые функции, без обращений к API."""

from dataclasses import dataclass

from guard import config

HOUR_SECONDS = 3600


@dataclass(frozen=True)
class Match:
    permalink: str | None
    user_id: str
    created_at: float
    reaction_names: frozenset[str]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def humanize_age(seconds: float) -> str:
    if seconds < HOUR_SECONDS:
        return f"{_plural(max(1, int(seconds // 60)), 'minute')} ago"
    if seconds < config.DAY_SECONDS:
        return f"{_plural(int(seconds // HOUR_SECONDS), 'hour')} ago"
    return f"{_plural(int(seconds // config.DAY_SECONDS), 'day')} ago"


def _window_label(days: float) -> str:
    """Окно словами. У каналов оно разное, в bonuses меньше суток."""
    if days < 1:
        return _plural(int(round(days * 24)), "hour")
    return _plural(int(days), "day")


def status_label(reaction_names: frozenset[str]) -> str:
    if reaction_names & set(config.RESOLVED_EMOJI):
        return "resolved :white_check_mark:"
    if reaction_names & set(config.IN_PROGRESS_EMOJI):
        return "in progress :eyes:"
    return "no reaction yet"


def _link(match: Match, label: str) -> str:
    return f"<{match.permalink}|{label}>" if match.permalink else label


def duplicate_transaction_text(*, transaction_id: str, match: Match, now: float) -> str:
    age = humanize_age(now - match.created_at)
    lines = [
        ":warning: *Duplicate escalation*",
        f"Transaction `{transaction_id}` was already escalated {age} by <@{match.user_id}>",
        f"Status: {status_label(match.reaction_names)}",
    ]
    if match.permalink:
        lines.append(_link(match, "Open original"))
    return "\n".join(lines)


def duplicate_player_text(
    *, player_id: str, matches: list[Match], now: float, window_days: float
) -> str:
    shown = matches[: config.MAX_DUPLICATES_SHOWN]
    hidden = len(matches) - len(shown)

    lines = [
        ":warning: *Possible duplicate*",
        f"Player `{player_id}` — {_plural(len(matches), 'escalation')} "
        f"in this channel in the last {_window_label(window_days)}:",
    ]
    for match in shown:
        age = humanize_age(now - match.created_at)
        lines.append(
            f"- {_link(match, age)} by <@{match.user_id}> — {status_label(match.reaction_names)}"
        )
    if hidden > 0:
        lines.append(f"and {hidden} more")

    return "\n".join(lines)


def stale_reminder_text(
    *, player_id: str | None, transaction_id: str | None, mention_user_ids: list[str]
) -> str:
    lines = [
        f":hourglass_flowing_sand: *{config.STALE_AFTER_DAYS} days without resolution*",
        "No :white_check_mark: on this escalation.",
    ]

    details = []
    if player_id:
        details.append(f"Player `{player_id}`")
    if transaction_id:
        details.append(f"Transaction `{transaction_id}`")
    if details:
        lines.append(" · ".join(details))

    if mention_user_ids:
        mentions = " ".join(f"<@{user_id}>" for user_id in mention_user_ids)
        lines.append(f"cc {mentions}")

    return "\n".join(lines)
