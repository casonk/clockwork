"""TVmaze calls are spaced, and the host is left alone after a 429 or 503."""

from __future__ import annotations

import io
import urllib.error

import pytest

from web import episode_guide as eg


@pytest.fixture(autouse=True)
def fresh_pacer(monkeypatch):
    monkeypatch.setattr(eg, "_next_call", 0.0)
    monkeypatch.setattr(eg, "_paused_until", 0.0)


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(eg.time, "monotonic", c.monotonic)
    monkeypatch.setattr(eg.time, "sleep", c.sleep)
    return c


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://api.tvmaze.com/x", code, "x", headers, None)


def test_back_to_back_calls_are_spaced(clock, monkeypatch):
    monkeypatch.setattr(eg.urllib.request, "urlopen", lambda *a, **k: Reply(b"[]"))
    for _ in range(3):
        assert eg._fetch_json("/search/shows?q=x") == []
    assert clock.slept == [pytest.approx(eg.MIN_INTERVAL), pytest.approx(eg.MIN_INTERVAL)]


@pytest.mark.parametrize("code", [429, 503])
def test_a_throttle_pauses_the_host_for_retry_after(clock, monkeypatch, code):
    calls = []

    def urlopen(*a, **k):
        calls.append(1)
        raise http_error(code, "12")

    monkeypatch.setattr(eg.urllib.request, "urlopen", urlopen)
    with pytest.raises(eg.GuideError):
        eg._fetch_json("/a")
    with pytest.raises(eg.GuideError, match="slow down"):
        eg._fetch_json("/b")
    assert len(calls) == 1  # the second call never reached the network
    clock.now += 13
    monkeypatch.setattr(eg.urllib.request, "urlopen", lambda *a, **k: Reply(b"{}"))
    assert eg._fetch_json("/c") == {}


def test_pause_defaults_and_is_capped(clock, monkeypatch):
    monkeypatch.setattr(
        eg.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(http_error(429))
    )
    with pytest.raises(eg.GuideError):
        eg._fetch_json("/a")
    assert eg._paused_until - clock.now == pytest.approx(eg.DEFAULT_PAUSE)
    assert eg._retry_after(http_error(429, "99999")) == eg.MAX_PAUSE


def test_other_http_errors_do_not_pause(clock, monkeypatch):
    monkeypatch.setattr(
        eg.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(http_error(404))
    )
    with pytest.raises(eg.GuideError):
        eg._fetch_json("/a")
    assert eg._paused_until == 0.0
