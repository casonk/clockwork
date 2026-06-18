"""Tests for the server-backed Clockwork to-do list."""

from __future__ import annotations

import json
import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_APP_PATH = _WEB_DIR / "app.py"
_SPEC = spec_from_file_location("clockwork_web_app_todo", _APP_PATH)
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
sys.modules["clockwork_web_app_todo"] = web_app
_SPEC.loader.exec_module(web_app)


@pytest.fixture()
def todo_file(tmp_path, monkeypatch):
    p = tmp_path / "todo.json"
    monkeypatch.setattr(web_app, "TODO_FILE", p)
    return p


@pytest.fixture()
def todo_history_file(tmp_path, monkeypatch):
    p = tmp_path / "todo-history.jsonl"
    monkeypatch.setattr(web_app, "TODO_HISTORY_FILE", p)
    return p


@pytest.fixture()
def sample_todo_data():
    return {
        "categories": [
            {
                "name": "Tasks",
                "items": [
                    {"title": "Move RTX 3090 to CPU x16 slot", "done": False},
                    {"title": "Rerun PCIe load probe", "done": True},
                ],
            },
            {"name": "Errands", "items": []},
        ]
    }


@pytest.fixture()
def populated_todo(todo_file, sample_todo_data):
    todo_file.write_text(json.dumps(sample_todo_data))
    return todo_file


@pytest.fixture()
def client(populated_todo, todo_history_file):
    web_app.app.config["TESTING"] = True
    web_app.app.config["SECRET_KEY"] = "test-secret"
    return web_app.app.test_client()


def _csrf(client) -> str:
    resp = client.get("/api/todo")
    assert resp.status_code == 200
    return resp.get_json()["csrf_token"]


def test_load_todo_defaults_when_missing(todo_file):
    assert not todo_file.exists()
    data = web_app.load_todo()
    assert [c["name"] for c in data["categories"]] == ["Tasks", "Projects", "Errands", "Goals"]


def test_load_todo_migrates_localstorage_watched_key(todo_file):
    todo_file.write_text(
        json.dumps(
            {"categories": [{"name": "Tasks", "items": [{"title": "Legacy", "watched": True}]}]}
        )
    )
    data = web_app.load_todo()
    assert data["categories"][0]["items"][0] == {"title": "Legacy", "done": True}


def test_to_do_page_renders_server_backed_items(client):
    resp = client.get("/to-do")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Move RTX 3090 to CPU x16 slot" in body
    assert "Rerun PCIe load probe" in body
    assert "localStorage" not in body


def test_form_add_item_persists(todo_file, client):
    csrf = _csrf(client)
    resp = client.post(
        "/to-do/add-item",
        data={"csrf_token": csrf, "category": "Tasks", "item": "Inspect top slot for AMD card"},
    )
    assert resp.status_code == 302
    saved = json.loads(todo_file.read_text())
    titles = [i["title"] for i in saved["categories"][0]["items"]]
    assert "Inspect top slot for AMD card" in titles


def test_api_todo_add_item_creates_category_for_agents(todo_file, client):
    csrf = _csrf(client)
    resp = client.post(
        "/api/todo/add-item",
        json={"category": "Hardware", "title": "Check BIOS PEG settings"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    saved = json.loads(todo_file.read_text())
    hardware = next(c for c in saved["categories"] if c["name"] == "Hardware")
    assert hardware["items"] == [{"title": "Check BIOS PEG settings", "done": False}]


def test_api_todo_toggle_item_sets_done(todo_file, client):
    csrf = _csrf(client)
    resp = client.post(
        "/api/todo/toggle-item",
        json={"category": "Tasks", "title": "Move RTX 3090 to CPU x16 slot", "done": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    saved = json.loads(todo_file.read_text())
    item = next(
        i for i in saved["categories"][0]["items"] if i["title"] == "Move RTX 3090 to CPU x16 slot"
    )
    assert item["done"] is True


def test_api_todo_rejects_duplicate(client):
    csrf = _csrf(client)
    resp = client.post(
        "/api/todo/add-item",
        json={"category": "Tasks", "title": "Move RTX 3090 to CPU x16 slot"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409
