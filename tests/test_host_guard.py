"""Clockwork refuses requests whose Host is not one it is reached by (DNS rebinding)."""

from __future__ import annotations

import pytest

from web import app as web_app


@pytest.fixture
def client():
    return web_app.app.test_client()


def test_a_rebinding_page_is_refused_before_anything_is_served_or_changed(client):
    rebind = {"Host": "evil.example", "Origin": "http://evil.example"}
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/home", headers=rebind)
        assert response.status_code == 403, method


def test_a_matching_origin_does_not_rescue_an_unexpected_host(client):
    response = client.post(
        "/to-code/add",
        data={"title": "x"},
        headers={
            "Host": "evil.example",
            "Origin": "http://evil.example",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["localhost", "localhost:5000", "127.0.0.1:5000", "[::1]:5000"])
def test_loopback_hosts_are_answered(client, host):
    assert client.get("/", headers={"Host": host}).status_code != 403


@pytest.mark.parametrize(
    "host", ["localhost.evil.example", "127.0.0.1.evil.example", "evil.example:5000", ""]
)
def test_lookalike_hosts_are_refused(client, host):
    assert client.get("/", headers={"Host": host}).status_code == 403


def test_forwarded_headers_never_authorise_a_request(client):
    headers = {
        "Host": "evil.example",
        "X-Forwarded-Host": "localhost",
        "X-Original-Host": "localhost",
    }
    assert client.get("/", headers=headers).status_code == 403


def test_the_registered_hostname_is_answered(client, tmp_path, monkeypatch):
    entry = tmp_path / "owner__repo"
    entry.mkdir()
    (entry / "site.local.toml").write_text('[site]\nhostname = "config.example.test"\n')
    monkeypatch.setenv("PORTFOLIO_SITE_DIR", str(entry))
    monkeypatch.setenv("PORTFOLIO_STORE", str(tmp_path))
    assert client.get("/", headers={"Host": "config.example.test"}).status_code != 403
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403


def test_the_refusal_does_not_echo_the_host_header(client):
    response = client.get("/", headers={"Host": "<b>x</b>.example"})
    assert response.status_code == 403 and b"<b>x</b>" not in response.data
