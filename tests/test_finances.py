"""The /finances proxy keeps the key server-side and only forwards read routes."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from web import app as web_app
from web import finances


@pytest.fixture()
def client(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("sekrit\n")
    key.chmod(0o600)
    monkeypatch.setenv("CLOCKWORK_FINANCE_API_KEY_FILE", str(key))
    monkeypatch.setenv("CLOCKWORK_FINANCE_URL", "http://127.0.0.1:9")
    return web_app.app.test_client()


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_the_proxy_adds_the_key_and_returns_the_json(client, monkeypatch) -> None:
    seen = {}

    def fake_open(req, timeout=0):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        return FakeResponse(json.dumps({"net_worth": 1}).encode())

    monkeypatch.setattr(finances.urllib.request, "urlopen", fake_open)
    reply = client.get("/api/finances/summary")
    assert reply.status_code == 200 and reply.get_json() == {"net_worth": 1}
    assert seen["auth"] == "Bearer sekrit" and seen["url"].endswith("/api/summary")
    assert b"sekrit" not in reply.data


def test_only_read_routes_are_forwarded(client) -> None:
    for path in ("trade", "accounts/../x", "../health", "summary/extra", "orders"):
        assert client.get(f"/api/finances/{path}").status_code == 404
    assert client.post("/api/finances/summary").status_code in (403, 405)  # CSRF guard or no route


def test_filters_are_forwarded_only_when_valid(client, monkeypatch) -> None:
    urls = []

    def fake_open(req, timeout=0):
        urls.append(req.full_url)
        return FakeResponse(b"{}")

    monkeypatch.setattr(finances.urllib.request, "urlopen", fake_open)
    client.get("/api/finances/summary?accounts=a-1,b-2&from=2026-01&to=2026-06-30&evil=1")
    client.get("/api/finances/networth?accounts=BAD%20ID&from=2026-1&to=x")
    client.get("/api/finances/accounts?accounts=a-1&from=2026-01")
    assert urls[0].endswith("/api/summary?accounts=a-1%2Cb-2&from=2026-01&to=2026-06-30") or urls[
        0
    ].endswith("/api/summary?accounts=a-1,b-2&from=2026-01&to=2026-06-30")
    assert urls[1].endswith("/api/networth")
    assert urls[2].endswith("/api/accounts")  # only summary and networth take filters


def test_months_is_forwarded_only_as_a_number(client, monkeypatch) -> None:
    urls = []

    def fake_open(req, timeout=0):
        urls.append(req.full_url)
        return FakeResponse(b"{}")

    monkeypatch.setattr(finances.urllib.request, "urlopen", fake_open)
    client.get("/api/finances/networth?months=12")
    client.get("/api/finances/networth?months=1;drop")
    assert urls[0].endswith("/api/networth?months=12") and urls[1].endswith("/api/networth")


def test_a_missing_or_open_key_file_fails_closed(tmp_path, monkeypatch) -> None:
    client = web_app.app.test_client()
    monkeypatch.setenv("CLOCKWORK_FINANCE_API_KEY_FILE", str(tmp_path / "none"))
    assert client.get("/api/finances/summary").status_code == 503
    open_key = tmp_path / "open"
    open_key.write_text("sekrit")
    open_key.chmod(0o644)
    monkeypatch.setenv("CLOCKWORK_FINANCE_API_KEY_FILE", str(open_key))
    assert client.get("/api/finances/summary").status_code == 503


def test_an_unreachable_service_is_a_clean_502(client, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(finances.urllib.request, "urlopen", boom)
    reply = client.get("/api/finances/accounts")
    assert reply.status_code == 502 and "reachable" in reply.get_json()["error"]


def test_the_page_renders_and_is_in_the_menu(client) -> None:
    html = client.get("/finances").get_data(as_text=True)
    assert "Net worth over time" in html and 'href="/finances"' in html
