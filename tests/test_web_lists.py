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
        "CODE_LIST_FILE": tmp_path / "to-code.json",
        "CODE_LIST_HISTORY_FILE": tmp_path / "to-code-history.jsonl",
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


def test_to_code_list_renders_grouped_repositories_and_accepts_backlog_items(client, list_files):
    page = client.get("/to-code")
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    assert all(group in html for group in ("archive", "doc", "health", "util", "sec", "dedicated"))
    assert 'href="/to-code"' in html
    assert 'name="repo"' in html
    assert "personal-finance" in html
    assert 'name="category" aria-label="Repo group" required' in html
    assert html.count('action="/to-code/add-item"') == 1

    csrf = _csrf(client, "/api/code")
    added = client.post(
        "/api/code/add-item",
        json={
            "category": "dedicated",
            "repo": "personal-finance",
            "item": "Add reconciliation report",
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert added.status_code == 200
    item = added.get_json()["data"]["categories"][-1]["items"][0]
    assert item == {
        "title": "Add reconciliation report",
        "done": False,
        "repo": "personal-finance",
    }
    assert json.loads(list_files["CODE_LIST_FILE"].read_text()) == added.get_json()["data"]
    grouped_page = client.get("/to-code").get_data(as_text=True)
    assert "Add reconciliation report" in grouped_page
    assert 'data-type="personal-finance"' in grouped_page

    form_add = client.post(
        "/to-code/add-item",
        data={
            "csrf_token": csrf,
            "category": "util",
            "repo": "clockwork",
            "item": "Document scheduler setup",
        },
        follow_redirects=True,
    )
    assert form_add.status_code == 200
    assert "Document scheduler setup" in form_add.get_data(as_text=True)
    saved_categories = json.loads(list_files["CODE_LIST_FILE"].read_text())["categories"]
    util_items = next(
        category["items"] for category in saved_categories if category["name"] == "util"
    )
    assert any(item["title"] == "Document scheduler setup" for item in util_items)

    invalid_group = client.post(
        "/api/code/add-item",
        json={"category": "archvie", "repo": "clockwork", "item": "Wrong group"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid_group.status_code == 404
    assert "archvie" not in {
        category["name"]
        for category in json.loads(list_files["CODE_LIST_FILE"].read_text())["categories"]
    }


def test_to_code_list_distinguishes_same_item_across_repositories(client):
    csrf = _csrf(client, "/api/code")
    for repo in ("personal-finance", "clockwork"):
        response = client.post(
            "/api/code/add-item",
            json={"category": "dedicated", "repo": repo, "item": "Improve docs"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200

    toggled = client.post(
        "/api/code/toggle-item",
        json={"category": "dedicated", "repo": "clockwork", "item": "Improve docs"},
        headers={"X-CSRF-Token": csrf},
    )

    items = toggled.get_json()["data"]["categories"][-1]["items"]
    assert [item["done"] for item in items] == [False, True]


def test_list_store_is_an_explicit_opt_in_bridge(list_files, monkeypatch):
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


def test_list_store_module_is_resolved_from_config_not_hardcoded(list_files, monkeypatch):
    """The adapter package is named only via CLOCKWORK_LIST_STORE_MODULE.

    No private package name is hard-coded in this repository; the store module is
    imported dynamically from configuration. A stub module stands in here.
    """

    import types

    stub = types.ModuleType("stub_list_store_adapter")
    built = {}

    class SQLiteResourceStore:
        def __init__(self, database):
            built["database"] = database

    class PersonalListSpec:
        def __init__(self, key, primary, status, extras):
            built["spec"] = (key, primary, status, extras)

    class ClockworkPersonalListAdapter:
        def __init__(self, store, spec):
            built["store"] = store
            built["spec_obj"] = spec

    stub.SQLiteResourceStore = SQLiteResourceStore
    stub.PersonalListSpec = PersonalListSpec
    stub.ClockworkPersonalListAdapter = ClockworkPersonalListAdapter
    monkeypatch.setitem(sys.modules, "stub_list_store_adapter", stub)

    db = str(list_files["TODO_FILE"].with_suffix(".sqlite3"))
    monkeypatch.setenv("CLOCKWORK_LIST_STORE_DB", db)
    monkeypatch.setenv("CLOCKWORK_LIST_STORE_ORIGIN", "air")

    # Module unset -> explicit error, not a hard-coded fallback.
    monkeypatch.delenv("CLOCKWORK_LIST_STORE_MODULE", raising=False)
    with pytest.raises(RuntimeError, match="CLOCKWORK_LIST_STORE_MODULE is required"):
        web_app._simple_list_adapter("watch")

    # Module configured -> adapter built from the stub, no name in this repo.
    monkeypatch.setenv("CLOCKWORK_LIST_STORE_MODULE", "stub_list_store_adapter")
    adapter = web_app._simple_list_adapter("watch")
    assert isinstance(adapter, ClockworkPersonalListAdapter)
    assert built["database"] == db


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


def test_watch_check_get_describes_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(web_app, "_EXTRA_WATCH_DIRS", [tmp_path / "missing"])

    resp = client.get("/api/watch-check")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["available"] is True
    assert body["dirs"] == [
        {"path": str(tmp_path), "exists": True},
        {"path": str(tmp_path / "movies"), "exists": False},
        {"path": str(tmp_path / "shows"), "exists": False},
        {"path": str(tmp_path / "missing"), "exists": False},
    ]
    assert body["usage"].startswith("POST")


def test_to_watch_renders_torrent_and_library_controls(client, list_files):
    list_files["WATCH_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Movies",
                        "items": [
                            {"title": "Napoleon Dynamite", "year": 2004, "watched": False},
                            {"title": "Brazil", "year": 1985, "watched": True},
                        ],
                    }
                ]
            }
        )
    )

    html = client.get("/to-watch").get_data(as_text=True)

    assert 'id="lib-check-btn"' in html
    assert 'href="/api/watch-check"' not in html
    assert 'data-query="Napoleon Dynamite 2004"' in html
    assert 'data-query="Brazil' not in html
    assert html.count('class="search-panel"') == 1
    assert "/api/torrent-add" in html


def test_other_lists_do_not_render_torrent_controls(client):
    html = client.get("/to-read").get_data(as_text=True)

    assert "find-btn" not in html.split("</style>")[1]
    assert "lib-check-btn" not in html.split("</style>")[1]


def test_watch_check_finds_titles_filed_under_movies(client, tmp_path, monkeypatch):
    (tmp_path / "movies" / "Napoleon Dynamite (2004)").mkdir(parents=True)
    (tmp_path / "shows").mkdir()
    monkeypatch.setattr(web_app, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(web_app, "_EXTRA_WATCH_DIRS", [])

    resp = client.post(
        "/api/watch-check",
        json={"titles": [{"title": "Napoleon Dynamite"}, {"title": "Movies"}]},
        headers={"X-CSRF-Token": _csrf(client, "/api/watch")},
    )

    matches = resp.get_json()["matches"]
    assert matches["Napoleon Dynamite"] == {"found": True, "match": "Napoleon Dynamite (2004)"}
    assert matches["Movies"]["found"] is False


def test_library_page_and_search_api(client, tmp_path, monkeypatch):
    media = web_app.media_library
    db = tmp_path / "lib.sqlite"
    root = tmp_path / "watch"
    video = root / "movies" / "Seven (1995)" / "Seven (1995) [1080p].mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x")

    class Provider:
        name = "fake"

        def search(self, title, year, kind):
            return media.Metadata(
                directors=["David Fincher"], genres=["thriller"], sources=["fake"]
            )

    conn = media.connect(db)
    media.scan_library(
        root,
        conn,
        providers=[Provider()],
        prober=lambda _p: media.StreamInfo(width=1920, height=800),
    )
    conn.close()
    monkeypatch.setattr(media, "CATALOG_DB", db)

    assert b"Library" in client.get("/library").data
    data = client.get("/api/library?director=fincher&year_from=1990").get_json()
    assert data["available"] is True
    assert data["count"] == 1
    item = data["items"][0]
    assert (item["title"], item["quality"], item["folder_name"]) == (
        "Seven",
        "1080p",
        "Seven (1995)",
    )
    assert "folder" not in item
    assert data["facets"]["directors"] == ["David Fincher"]
    assert client.get("/api/library?director=nolan").get_json()["count"] == 0


def test_library_search_reports_missing_catalog(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_app.media_library, "CATALOG_DB", tmp_path / "missing.sqlite")

    assert client.get("/api/library").get_json() == {
        "available": False,
        "count": 0,
        "items": [],
        "facets": {},
    }


def test_to_code_items_carry_priority_and_in_person_flags(client, list_files):
    csrf = _csrf(client, "/api/code")
    added = client.post(
        "/api/code/add-item",
        json={
            "category": "dedicated",
            "repo": "clockwork",
            "item": "Flash firmware",
            "priority": "2",
            "in_person": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    item = added.get_json()["data"]["categories"][-1]["items"][0]
    assert item["priority"] == "P2"
    assert item["in_person"] == "yes"

    updated = client.post(
        "/api/code/update-item",
        json={
            "category": "dedicated",
            "repo": "clockwork",
            "item": "Flash firmware",
            "priority": "p1",
            "in_person": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    item = updated.get_json()["data"]["categories"][-1]["items"][0]
    assert item["priority"] == "P1"
    assert "in_person" not in item

    bad = client.post(
        "/api/code/update-item",
        json={
            "category": "dedicated",
            "repo": "clockwork",
            "item": "Flash firmware",
            "priority": "urgent",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400

    form = client.post(
        "/to-code/update-item",
        data={
            "csrf_token": csrf,
            "category": "dedicated",
            "repo": "clockwork",
            "item": "Flash firmware",
            "in_person": "yes",
        },
    )
    assert form.status_code == 302
    saved = json.loads(list_files["CODE_LIST_FILE"].read_text())["categories"][-1]["items"][0]
    assert saved["priority"] == "P1"  # untouched by the in-person-only form
    assert saved["in_person"] == "yes"

    html = client.get("/to-code").get_data(as_text=True)
    assert 'data-priority="P1"' in html
    assert 'data-in-person="true"' in html
    assert client.post("/to-do/update-item", data={"csrf_token": csrf}).status_code == 404
