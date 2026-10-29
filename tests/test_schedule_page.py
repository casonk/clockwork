"""The /schedule page and /api/schedule list timers soonest first with the last outcome."""

from __future__ import annotations

import json

import pytest

from web import app as web_app

TIMERS = [
    {"unit": "later.timer", "activates": "later.service", "next": 2_000_000_000_000_000, "last": 0},
    {
        "unit": "soon.timer",
        "activates": "soon.service",
        "next": 1_000_000_000_000_000,
        "last": 900_000_000_000_000,
    },
    {"unit": "idle.timer", "activates": "idle.service", "next": 0, "last": 800_000_000_000_000},
    {
        "unit": "bad.timer",
        "activates": "bad.service",
        "next": 1_500_000_000_000_000,
        "last": 700_000_000_000_000,
    },
]
SHOWN = (
    "Id=later.service\nActiveState=inactive\nResult=success\nExecMainStatus=0\n\n"
    "Id=soon.service\nActiveState=activating\nResult=success\nExecMainStatus=0\n\n"
    "Id=idle.service\nActiveState=inactive\nResult=success\nExecMainStatus=0\n\n"
    "Id=bad.service\nActiveState=failed\nResult=exit-code\nExecMainStatus=1\n"
)


@pytest.fixture()
def client():
    web_app.app.config["TESTING"] = True
    return web_app.app.test_client()


def fake_run(*cmd, timeout=30):
    if "list-timers" in cmd:
        return (0, json.dumps(TIMERS)) if "--user" in cmd else (0, "[]")
    return 0, SHOWN


def test_rows_are_ordered_and_carry_status(client, monkeypatch):
    monkeypatch.setattr(web_app, "_run", fake_run)
    rows = client.get("/api/schedule").get_json()["rows"]
    assert [r["timer"] for r in rows] == ["soon.timer", "bad.timer", "later.timer", "idle.timer"]
    status = {r["timer"]: r["status"] for r in rows}
    assert status == {
        "soon.timer": "running",
        "bad.timer": "failed",
        "later.timer": "never run",
        "idle.timer": "ok",
    }
    assert rows[-1]["next_epoch"] is None


def test_page_renders_rows_and_failure_result(client, monkeypatch):
    monkeypatch.setattr(web_app, "_run", fake_run)
    body = client.get("/schedule").get_data(as_text=True)
    assert "soon" in body and "failed (exit-code)" in body and "not scheduled" in body


def test_unreadable_systemctl_gives_an_empty_page(client, monkeypatch):
    monkeypatch.setattr(web_app, "_run", lambda *a, **k: (1, "no bus"))
    assert client.get("/api/schedule").get_json()["rows"] == []
    assert "No timers found" in client.get("/schedule").get_data(as_text=True)


def test_home_lists_the_schedule_card(client):
    assert "Schedule" in client.get("/home").get_data(as_text=True)
