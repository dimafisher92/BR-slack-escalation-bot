"""Обёртка над Slack-клиентом: единая точка отправки и режим --dry-run."""

import logging

log = logging.getLogger(__name__)


class Poster:
    def __init__(self, client, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run

    def permalink(self, channel_id: str, ts: str) -> str | None:
        """Постоянная ссылка на сообщение. None, если Slack не ответил."""
        try:
            response = self._client.chat_getPermalink(channel=channel_id, message_ts=ts)
            return response["permalink"]
        except Exception:
            log.warning("Could not fetch permalink for %s/%s", channel_id, ts, exc_info=True)
            return None

    def reply_in_thread(self, channel_id: str, thread_ts: str, text: str) -> bool:
        if self._dry_run:
            log.info("DRY-RUN would reply in %s thread %s:\n%s", channel_id, thread_ts, text)
            return True

        try:
            self._client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
            return True
        except Exception:
            log.error("Failed to post reply in %s/%s", channel_id, thread_ts, exc_info=True)
            return False
