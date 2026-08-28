"""Tests for agent-modifiable Clockwork list APIs."""

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
_SPEC = spec_from_file_location("clockwork_web_app_lists", _APP_PATH)
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
sys.modules["clockwork_web_app_lists"] = web_app
_SPEC.loader.exec_module(web_app)


@pytest.fixture()
def list_files(tmp_path, monkeypatch):
    paths = {
        "TODO_FILE": tmp_path / "todo.json",
        "TODO_HISTORY_FILE": tmp_path / "todo-history.jsonl",
        "WATCH_LIST_FILE": tmp_path / "to-watch.json",
        "WATCH_LIST_HISTORY_FILE": tmp_path / "to-watch-history.jsonl",
        "READ_LIST_FILE": tmp_path / "to-read.json",
        "READ_LIST_HISTORY_FILE": tmp_path / "to-read-history.jsonl",
        "LISTEN_LIST_FILE": tmp_path / "to-listen.json",
        "LISTEN_LIST_HISTORY_FILE": tmp_path / "to-listen-history.jsonl",
        "INVEST_LIST_FILE": tmp_path / "to-invest.json",
        "INVEST_LIST_HISTORY_FILE": tmp_path / "to-invest-history.jsonl",
        "GROCERIES_FILE": tmp_path / "groceries.json",
        "GROCERIES_HISTORY_FILE": tmp_path / "groceries-history.jsonl",
        "SHOPPING_FILE": tmp_path / "shopping.json",
        "SHOPPING_HISTORY_FILE": tmp_path / "shopping-history.jsonl",
        "GROCERY_RULES_FILE": tmp_path / "grocery-rules.json",
        "SHOPPING_RULES_FILE": tmp_path / "shopping-rules.json",
    }
    for attr, path in paths.items():
        monkeypatch.setattr(web_app, attr, path)
    paths["GROCERIES_FILE"].write_text(json.dumps({"categories": []}))
    paths["SHOPPING_FILE"].write_text(json.dumps({"categories": []}))
    return paths


@pytest.fixture()
def client(list_files, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    web_app.app.config["TESTING"] = True
    web_app.app.config["SECRET_KEY"] = "test-secret"
    return web_app.app.test_client()


def _csrf(client, path: str) -> str:
    resp = client.get(path)
    assert resp.status_code == 200
    return resp.get_json()["csrf_token"]


@pytest.mark.parametrize(
    ("page", "api_path", "category", "payload", "status_key"),
    [
        ("to-watch", "/api/watch", "Movies", {"title": "Brazil", "year": 1985}, "watched"),
        ("to-read", "/api/read", "Books", {"title": "The Dispossessed"}, "watched"),
        ("to-listen", "/api/listen", "Albums", {"title": "Kind of Blue"}, "watched"),
        (
            "to-invest",
            "/api/invest",
            "Stocks",
            {"ticker": "AMD", "name": "Advanced Micro Devices"},
            "holding",
        ),
    ],
)
def test_to_list_api_add_toggle_delete_item(client, page, api_path, category, payload, status_key):
    csrf = _csrf(client, api_path)
    add_resp = client.post(
        f"{api_path}/add-item",
        json={"category": category, **payload},
        headers={"X-CSRF-Token": csrf},
    )
    assert add_resp.status_code == 200
    assert add_resp.get_json()["ok"] is True

    value = payload.get("ticker") or payload["title"]
    toggle_resp = client.post(
        f"{api_path}/toggle-item",
        json={"category": category, "item": value, status_key: True},
        headers={"X-CSRF-Token": csrf},
    )
    assert toggle_resp.status_code == 200
    data = toggle_resp.get_json()["data"]
    cat = next(c for c in data["categories"] if c["name"] == category)
    assert cat["items"][0][status_key] is True

    delete_resp = client.post(
        f"{api_path}/delete-item",
        json={"category": category, "item": value},
        headers={"X-CSRF-Token": csrf},
    )
    assert delete_resp.status_code == 200
    assert delete_resp.get_json()["data"]["categories"][0]["items"] == []

    page_resp = client.get(f"/{page}")
    assert page_resp.status_code == 200
    assert "localStorage" not in page_resp.get_data(as_text=True)


def test_list_store_storage_is_an_explicit_opt_in_bridge(list_files, monkeypatch):
    calls = []

    class FakeAdapter:
        def export_legacy_json(self):
            return {"categories": [{"name": "Movies", "items": []}]}

        def reconcile_legacy_json(self, data, **kwargs):
            calls.append((data, kwargs))
            return None

    monkeypatch.setenv(
        "CLOCKWORK_LIST_STORE_DB", str(list_files["TODO_FILE"].with_suffix(".sqlite3"))
    )
    monkeypatch.setenv("CLOCKWORK_LIST_STORE_ORIGIN", "air")
    monkeypatch.setattr(web_app, "_simple_list_adapter", lambda key: FakeAdapter())

    assert web_app.load_simple_list("watch") == {"categories": [{"name": "Movies", "items": []}]}
    web_app.save_simple_list(
        "watch", {"categories": [{"name": "Movies", "items": []}]}, operation_id="op"
    )

    assert calls == [
        (
            {"categories": [{"name": "Movies", "items": []}]},
            {"origin_node": "air", "operation_id": "op", "actor": "clockwork-web"},
        )
    ]


@pytest.mark.parametrize(
    ("api_path", "category", "item", "status_key"),
    [
        ("/api/groceries", "Produce", "Spinach", "stocked"),
        ("/api/shopping", "Hardware", "GPU support bracket", "owned"),
    ],
)
def test_existing_list_api_add_toggle_delete_item(client, api_path, category, item, status_key):
    csrf = _csrf(client, api_path)
    add_resp = client.post(
        f"{api_path}/add-item",
        json={"category": category, "item": item},
        headers={"X-CSRF-Token": csrf},
    )
    assert add_resp.status_code == 200
    assert add_resp.get_json()["ok"] is True

    toggle_resp = client.post(
        f"{api_path}/toggle-item",
        json={"category": category, "item": item, status_key: True},
        headers={"X-CSRF-Token": csrf},
    )
    assert toggle_resp.status_code == 200
    data = toggle_resp.get_json()["data"]
    cat = next(c for c in data["categories"] if c["name"] == category)
    assert cat["items"][0][status_key] is True

    delete_resp = client.post(
        f"{api_path}/delete-item",
        json={"category": category, "item": item},
        headers={"X-CSRF-Token": csrf},
    )
    assert delete_resp.status_code == 200
    assert delete_resp.get_json()["ok"] is True
