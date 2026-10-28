"""The /ecu page and /api/ecu render `ecu status --json` and fail soft."""

from __future__ import annotations

import json

import pytest

from web import app as web_app

DOCUMENT = {
    "lease": {
        "command": "ollama serve qwen",
        "priority": "interactive",
        "mode": "shared",
        "gpu_mib": 19000,
        "pid": 42,
    },
    "gpu": {"total_mib": 24576, "used_mib": 600, "free_mib": 23976, "processes": []},
    "jobs": [
        {
            "unit": "archility-daily.service",
            "class": "heavy",
            "verdict": "defer",
            "running": False,
            "deferrals": 2,
            "reason": "an ecu lease is active",
        }
    ],
}


@pytest.fixture()
def client():
    web_app.app.config["TESTING"] = True
    return web_app.app.test_client()


def test_page_shows_lease_and_deferred_jobs(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ecu_status", lambda: (DOCUMENT, ""))
    body = client.get("/ecu").get_data(as_text=True)
    assert "ollama serve qwen" in body
    assert "interactive" in body
    assert "19000 MiB" in body
    assert "an ecu lease is active" in body


def test_page_without_a_lease_says_so(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ecu_status", lambda: ({**DOCUMENT, "lease": None}, ""))
    assert "No lease is held" in client.get("/ecu").get_data(as_text=True)


def test_page_reports_an_unavailable_ecu(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ecu_status", lambda: (None, "the ecu command was not found"))
    response = client.get("/ecu")
    assert response.status_code == 200
    assert "the ecu command was not found" in response.get_data(as_text=True)


def test_api_returns_the_document_or_503(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ecu_status", lambda: (DOCUMENT, ""))
    assert client.get("/api/ecu").get_json() == DOCUMENT
    monkeypatch.setattr(web_app, "_ecu_status", lambda: (None, "boom"))
    response = client.get("/api/ecu")
    assert response.status_code == 503
    assert response.get_json() == {"error": "boom"}


def test_status_parses_the_command_output(monkeypatch):
    monkeypatch.setattr(web_app, "_ecu_binary", lambda: "/bin/ecu")
    monkeypatch.setattr(web_app, "_run", lambda *a, timeout=0: (0, json.dumps(DOCUMENT)))
    assert web_app._ecu_status() == (DOCUMENT, "")
    monkeypatch.setattr(web_app, "_run", lambda *a, timeout=0: (1, "ecu: broken"))
    assert web_app._ecu_status() == (None, "ecu: broken")
    monkeypatch.setattr(web_app, "_ecu_binary", lambda: None)
    assert web_app._ecu_status()[0] is None
