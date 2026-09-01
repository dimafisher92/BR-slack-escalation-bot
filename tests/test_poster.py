import logging

from guard.poster import Poster


class FakeClient:
    def __init__(self, permalink="https://s/link", fail=False):
        self._permalink = permalink
        self._fail = fail
        self.posted = []
        self.permalink_calls = []

    def chat_getPermalink(self, *, channel, message_ts):
        self.permalink_calls.append((channel, message_ts))
        if self._fail:
            raise RuntimeError("slack is down")
        return {"ok": True, "permalink": self._permalink}

    def chat_postMessage(self, *, channel, thread_ts, text):
        if self._fail:
            raise RuntimeError("slack is down")
        self.posted.append({"channel": channel, "thread_ts": thread_ts, "text": text})
        return {"ok": True}


def test_permalink_returned():
    client = FakeClient()
    assert Poster(client).permalink("C1", "1.0") == "https://s/link"
    assert client.permalink_calls == [("C1", "1.0")]


def test_permalink_returns_none_on_api_error(caplog):
    with caplog.at_level(logging.WARNING):
        assert Poster(FakeClient(fail=True)).permalink("C1", "1.0") is None
    assert "permalink" in caplog.text.lower()


def test_reply_posts_to_thread():
    client = FakeClient()
    assert Poster(client).reply_in_thread("C1", "1.0", "hello") is True
    assert client.posted == [{"channel": "C1", "thread_ts": "1.0", "text": "hello"}]


def test_reply_returns_false_on_api_error(caplog):
    with caplog.at_level(logging.ERROR):
        assert Poster(FakeClient(fail=True)).reply_in_thread("C1", "1.0", "hello") is False
    assert "failed to post" in caplog.text.lower()


def test_dry_run_does_not_post_but_reports_success(caplog):
    client = FakeClient()
    with caplog.at_level(logging.INFO):
        assert Poster(client, dry_run=True).reply_in_thread("C1", "1.0", "hello") is True
    assert client.posted == []
    assert "DRY-RUN" in caplog.text
    assert "hello" in caplog.text


def test_dry_run_still_resolves_permalinks():
    client = FakeClient()
    assert Poster(client, dry_run=True).permalink("C1", "1.0") == "https://s/link"
