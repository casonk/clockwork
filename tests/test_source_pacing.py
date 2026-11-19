"""arXiv lookups and YouTube calls are paced; a throttled YouTube account is left alone."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from web import read_sources, youtube_api


class Clock:
    def __init__(self):
        self.now = 5000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    for module in (read_sources, youtube_api):
        monkeypatch.setattr(module.time, "monotonic", c.monotonic)
        monkeypatch.setattr(module.time, "sleep", c.sleep)
    monkeypatch.setattr(read_sources, "_arxiv_next_call", 0.0)
    monkeypatch.setattr(youtube_api, "_next_call", {})
    monkeypatch.setattr(youtube_api, "_paused_until", {})
    return c


def test_two_different_arxiv_titles_are_three_seconds_apart(clock, monkeypatch):
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    monkeypatch.setattr(read_sources, "urlopen", lambda request, timeout: io.BytesIO(feed))
    read_sources.search_arxiv("First paper")
    read_sources.search_arxiv("Second paper")
    read_sources.search_arxiv("Third paper")
    assert clock.slept == [pytest.approx(3.0), pytest.approx(3.0)]


def _request(token):
    return urllib.request.Request(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        headers={"Authorization": f"Bearer {token}"},
    )


def _http_error(code, *, retry_after=None, reason=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    body = {"error": {"message": "slow", "errors": [{"reason": reason}] if reason else []}}
    return urllib.error.HTTPError(
        "https://x", code, "x", headers, io.BytesIO(json.dumps(body).encode())
    )


def test_pages_for_one_account_are_spaced(clock, monkeypatch):
    monkeypatch.setattr(
        youtube_api.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b'{"items": []}')
    )
    for _ in range(3):
        youtube_api._json_request(_request("a"), error_context="YouTube playlistItems")
    assert clock.slept == [pytest.approx(0.25), pytest.approx(0.25)]


@pytest.mark.parametrize(
    "code, reason", [(429, None), (503, None), (403, "quotaExceeded"), (403, "rateLimitExceeded")]
)
def test_a_throttled_account_is_paused_but_another_is_not(clock, monkeypatch, code, reason):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.get_header("Authorization"))
        if request.get_header("Authorization") == "Bearer a":
            raise _http_error(code, retry_after="20", reason=reason)
        return io.BytesIO(b'{"items": []}')

    monkeypatch.setattr(youtube_api.urllib.request, "urlopen", urlopen)
    with pytest.raises(youtube_api.YouTubeError, match=str(code)):
        youtube_api._json_request(_request("a"), error_context="YouTube x")
    with pytest.raises(youtube_api.YouTubeError, match="paused"):
        youtube_api._json_request(_request("a"), error_context="YouTube x")
    youtube_api._json_request(_request("b"), error_context="YouTube x")
    assert calls == ["Bearer a", "Bearer b"]
    clock.now += 21
    with pytest.raises(youtube_api.YouTubeError, match=str(code)):
        youtube_api._json_request(_request("a"), error_context="YouTube x")  # tried again


def test_an_ordinary_403_does_not_pause(clock, monkeypatch):
    def urlopen(request, timeout):
        raise _http_error(403, reason="forbidden")

    monkeypatch.setattr(youtube_api.urllib.request, "urlopen", urlopen)
    for _ in range(2):
        with pytest.raises(youtube_api.YouTubeError, match="403"):
            youtube_api._json_request(_request("a"), error_context="YouTube x")


def test_token_refresh_is_not_paced(clock, monkeypatch):
    monkeypatch.setattr(
        youtube_api.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b'{"access_token": "t"}')
    )
    for _ in range(2):
        youtube_api.access_token({"client_id": "i", "client_secret": "s", "refresh_token": "r"})
    assert clock.slept == []
