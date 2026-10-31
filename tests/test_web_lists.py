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
        "HOLDINGS_AGGREGATE_FILE": tmp_path / "holdings-aggregate.json",
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


def test_to_invest_sync_updates_server_backed_list(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Stocks",
                        "items": [{"ticker": "AMD", "holding": False, "target_pct": "25"}],
                    },
                    {"name": "ETFs", "items": []},
                    {"name": "Crypto", "items": []},
                ]
            }
        )
    )
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(
        json.dumps(
            {
                "generated_at": "2026-10-29T00:24:23Z",
                "holdings": [
                    {
                        "ticker": "AMD",
                        "description": "Advanced Micro Devices",
                        "asset_class": "Stocks",
                        "total_ending_value": 30,
                    },
                    {
                        "ticker": "VTI",
                        "description": "Total Stock Market ETF",
                        "asset_class": "ETFs",
                        "total_ending_value": 40,
                    },
                    {
                        "ticker": "VTI",
                        "description": "Duplicate account position",
                        "asset_class": "ETFs",
                        "total_ending_value": 10,
                    },
                    {
                        "ticker": "BTC",
                        "description": "Bitcoin",
                        "asset_class": "Crypto",
                        "total_ending_value": 10,
                    },
                    {
                        "ticker": "CASH",
                        "description": "Cash sweep",
                        "asset_class": "Core Account",
                        "total_ending_value": 10,
                    },
                ],
            }
        )
    )

    page = client.get("/to-invest")
    html = page.get_data(as_text=True)
    assert 'action="/to-invest/sync-holdings"' in html
    assert "↻ Sync Holdings" in html
    assert 'href="/api/invest-holdings"' not in html

    csrf = _csrf(client, "/api/invest")
    response = client.post(
        "/to-invest/sync-holdings",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "1 marked as holding" in response.get_data(as_text=True)
    assert "2 new tickers added" in response.get_data(as_text=True)
    assert "3 allocations refreshed" in response.get_data(as_text=True)

    data = client.get("/api/invest").get_json()["data"]
    by_category = {category["name"]: category["items"] for category in data["categories"]}
    amd = next(item for item in by_category["Stocks"] if item["ticker"] == "AMD")
    assert amd == {
        "ticker": "AMD",
        "name": "Advanced Micro Devices",
        "holding": True,
        "allocation_pct": "30",
        "target_pct": "25",
    }
    assert [item["ticker"] for item in by_category["ETFs"]] == ["VTI"]
    assert by_category["ETFs"][0]["allocation_pct"] == "50"
    assert by_category["Crypto"][0]["allocation_pct"] == "10"
    assert [item["ticker"] for item in by_category["Crypto"]] == ["BTC"]
    assert all(item["ticker"] != "CASH" for items in by_category.values() for item in items)

    page = client.get("/to-invest")
    html = page.get_data(as_text=True)
    assert "Listed now <strong>90.00%</strong>" in html
    assert "Targets assigned <strong>25.00%</strong>" in html
    assert "Current percentages use the latest aggregate market value" in html
    assert "30.000%" in html
    assert 'action="/to-invest/update-targets"' in html
    assert "Save all targets" in html
    assert "allocation-target-input" in html
    sticky_header = html.split("</header>", 1)[0]
    assert 'id="allocation-targets-form"' in sticky_header
    assert "Save all targets" in sticky_header


def test_to_invest_targets_are_saved_as_one_validated_batch(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Stocks",
                        "items": [
                            {"ticker": "AMD", "holding": True, "allocation_pct": "12.5"},
                            {"ticker": "NVDA", "holding": True, "allocation_pct": "8"},
                        ],
                    }
                ]
            }
        )
    )
    csrf = _csrf(client, "/api/invest")

    response = client.post(
        "/to-invest/update-targets",
        data={
            "csrf_token": csrf,
            "targets_json": json.dumps(
                [
                    {"category": "Stocks", "ticker": "AMD", "target_pct": "15.25"},
                    {"category": "Stocks", "ticker": "NVDA", "target_pct": "10"},
                ]
            ),
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Saved 2 target changes." in html
    assert "Target 15.250%" in html
    assert "Δ -2.750% underweight" in html
    data = client.get("/api/invest").get_json()["data"]
    assert data["categories"][0]["items"][0]["target_pct"] == "15.25"
    assert data["categories"][0]["items"][1]["target_pct"] == "10"
    invalid = client.post(
        "/to-invest/update-targets",
        data={
            "csrf_token": csrf,
            "targets_json": json.dumps(
                [
                    {"category": "Stocks", "ticker": "AMD", "target_pct": "20"},
                    {"category": "Stocks", "ticker": "NVDA", "target_pct": "101"},
                ]
            ),
        },
        follow_redirects=True,
    )
    assert "Target must be a number from 0 to 100." in invalid.get_data(as_text=True)
    data = client.get("/api/invest").get_json()["data"]
    assert data["categories"][0]["items"][0]["target_pct"] == "15.25"
    assert data["categories"][0]["items"][1]["target_pct"] == "10"

    missing = client.post(
        "/to-invest/update-targets",
        data={
            "csrf_token": csrf,
            "targets_json": json.dumps(
                [
                    {"category": "Stocks", "ticker": "AMD", "target_pct": "20"},
                    {"category": "Stocks", "ticker": "MISSING", "target_pct": "5"},
                ]
            ),
        },
        follow_redirects=True,
    )
    assert "No targets were saved." in missing.get_data(as_text=True)
    data = client.get("/api/invest").get_json()["data"]
    assert data["categories"][0]["items"][0]["target_pct"] == "15.25"

    cleared = client.post(
        "/to-invest/update-targets",
        data={
            "csrf_token": csrf,
            "targets_json": json.dumps(
                [
                    {"category": "Stocks", "ticker": "AMD", "target_pct": ""},
                    {"category": "Stocks", "ticker": "NVDA", "target_pct": "10"},
                ]
            ),
        },
        follow_redirects=True,
    )
    assert "Saved 1 target change." in cleared.get_data(as_text=True)
    data = client.get("/api/invest").get_json()["data"]
    assert "target_pct" not in data["categories"][0]["items"][0]
    assert data["categories"][0]["items"][1]["target_pct"] == "10"


def test_to_invest_renders_delta_spectrum_and_enter_batch_submit(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Stocks",
                        "items": [
                            {
                                "ticker": "OVER",
                                "holding": True,
                                "allocation_pct": "10",
                                "target_pct": "5",
                            },
                            {
                                "ticker": "SLIGHT",
                                "holding": True,
                                "allocation_pct": "5.5",
                                "target_pct": "5",
                            },
                            {
                                "ticker": "UNDER",
                                "holding": True,
                                "allocation_pct": "1",
                                "target_pct": "5",
                            },
                            {
                                "ticker": "EVEN",
                                "holding": True,
                                "allocation_pct": "5.004",
                                "target_pct": "5",
                            },
                        ],
                    }
                ]
            }
        )
    )

    html = client.get("/to-invest").get_data(as_text=True)

    assert "allocation-delta over" in html
    assert 'data-delta="5.0000"' in html
    assert "--delta-tone:100.0%" in html
    assert 'data-delta="0.5000"' in html
    assert "--delta-tone:41.5%" in html
    assert "allocation-delta under" in html
    assert 'data-delta="-4.0000"' in html
    assert "--delta-tone:87.0%" in html
    assert "allocation-delta on-target" in html
    assert "Δ on target" in html
    assert "event.key !== 'Enter'" in html
    assert "form.requestSubmit(save)" in html


def test_to_invest_sync_reports_missing_holdings_file_without_mutating_list(client, list_files):
    csrf = _csrf(client, "/api/invest")
    response = client.post(
        "/to-invest/sync-holdings",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Holdings sync failed: Holdings file not found" in response.get_data(as_text=True)
    assert not list_files["INVEST_LIST_FILE"].exists()


def test_to_code_list_renders_grouped_repositories_and_accepts_backlog_items(client, list_files):
    page = client.get("/to-code")
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    assert all(group in html for group in ("archive", "doc", "health", "util", "sec", "dedicated"))
    assert 'href="/to-code"' in html
    assert 'name="repo"' in html
    assert "Repository (e.g. my-repo)" in html
    assert 'name="category" aria-label="Repo group" required' in html
    assert html.count('action="/to-code/add-item"') == 1

    csrf = _csrf(client, "/api/code")
    added = client.post(
        "/api/code/add-item",
        json={
            "category": "dedicated",
            "repo": "sample-app",
            "item": "Add reconciliation report",
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert added.status_code == 200
    item = added.get_json()["data"]["categories"][-1]["items"][0]
    assert item == {
        "title": "Add reconciliation report",
        "done": False,
        "repo": "sample-app",
    }
    assert json.loads(list_files["CODE_LIST_FILE"].read_text()) == added.get_json()["data"]
    grouped_page = client.get("/to-code").get_data(as_text=True)
    assert "Add reconciliation report" in grouped_page
    assert 'data-type="sample-app"' in grouped_page

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


def test_to_code_form_suggests_repositories_named_in_the_environment(client, monkeypatch):
    assert "extra-repo" not in client.get("/to-code").get_data(as_text=True)

    monkeypatch.setenv("CLOCKWORK_CODE_EXTRA_REPOS", " extra-repo, ,other-repo ")
    html = client.get("/to-code").get_data(as_text=True)

    assert '<option value="extra-repo">' in html and '<option value="other-repo">' in html


def test_to_code_list_distinguishes_same_item_across_repositories(client):
    csrf = _csrf(client, "/api/code")
    for repo in ("sample-app", "clockwork"):
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
    assert "/api/series-build" in html
    assert "Add selected episodes" in html
    assert 'data-year="2004" data-category="Movies"' in html
    assert "checkLibrary(libBtn, false)" in html
    assert "link.href = match.url" in html


def test_torrent_search_labels_episodes_and_season_packs():
    episode = web_app._series_release_details("Breaking.Bad.S01E02.1080p.mkv")
    pack = web_app._series_release_details("Breaking.Bad.S01.1080p")
    series = web_app._series_release_details("Breaking.Bad.S01-S05.1080p")
    movie = web_app._series_release_details("Hunting.Season.2006.1080p.mkv")

    assert episode == {
        "season": 1,
        "season_end": None,
        "episode": 2,
        "episode_end": None,
        "release_type": "episode",
    }
    assert pack["season"] == 1 and pack["release_type"] == "season_pack"
    assert series["season_end"] == 5 and series["release_type"] == "series_pack"
    assert movie["release_type"] == "other"


def test_build_series_library_links_completed_episodes_without_moving(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    library = tmp_path / "watch"
    pack = downloads / "Breaking.Bad.S01.1080p"
    pack.mkdir(parents=True)
    first = pack / "Breaking.Bad.S01E01.mkv"
    second = pack / "Breaking.Bad.S01E02.mkv"
    subtitle = pack / "Breaking.Bad.S01E02.srt"
    for path in (first, second, subtitle):
        path.write_bytes(b"video")
    unfinished = downloads / "Breaking.Bad.S01E03.mkv"
    unfinished.write_bytes(b"partial")
    duplicate = downloads / "Breaking.Bad.S01E02.720p.mkv"
    duplicate.write_bytes(b"duplicate")
    filed = tmp_path / "magneto-library"
    filed.mkdir()
    moved_episode = filed / "Breaking.Bad.S02E01.mkv"
    moved_episode.write_bytes(b"moved")
    other = downloads / "Other.Show.S01E01.mkv"
    other.write_bytes(b"other")
    monkeypatch.setattr(web_app, "WATCH_DIR", library)
    monkeypatch.setattr(web_app, "_EXTRA_WATCH_DIRS", [downloads])
    torrents = [
        {"name": pack.name, "downloadDir": str(downloads), "percentDone": 1},
        {"name": unfinished.name, "downloadDir": str(downloads), "percentDone": 0.5},
        {"name": duplicate.name, "downloadDir": str(downloads), "percentDone": 1},
        {"name": moved_episode.name, "downloadDir": str(filed), "percentDone": 1},
        {"name": other.name, "downloadDir": str(downloads), "percentDone": 1},
    ]

    first_run = web_app._build_series_library("Breaking Bad", 2008, torrents)
    second_run = web_app._build_series_library("Breaking Bad", 2008, torrents)

    season = library / "shows" / "Breaking Bad (2008)" / "Season 01"
    assert len(first_run["linked"]) == 3
    assert (season / first.name).is_symlink()
    assert (season / second.name).resolve() == second
    assert (season / subtitle.name).resolve() == subtitle
    assert first.exists() and second.exists()
    assert not (season / unfinished.name).exists()
    assert not (season / duplicate.name).exists()
    assert (
        library / "shows" / "Breaking Bad (2008)" / "Season 02" / moved_episode.name
    ).resolve() == moved_episode
    assert any("episode already filed" in item for item in first_run["skipped"])
    assert not (library / "shows" / "Other Show").exists()
    assert second_run["linked"] == []
    media = web_app.media_library
    conn = media.connect(tmp_path / "catalog.sqlite")
    try:
        media.scan_library(
            library, conn, prober=lambda _path: media.StreamInfo(width=1920, height=1080)
        )
        assert conn.execute("SELECT COUNT(*) FROM titles WHERE kind = 'shows'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM files WHERE season = 1").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM files WHERE season = 2").fetchone()[0] == 1
    finally:
        conn.close()


def test_series_build_api_uses_completed_magneto_status(client, tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    episode = downloads / "Example.Show.S02E03.mkv"
    episode.write_bytes(b"video")
    monkeypatch.setattr(web_app, "WATCH_DIR", tmp_path / "watch")
    monkeypatch.setattr(web_app, "_EXTRA_WATCH_DIRS", [downloads])
    monkeypatch.setattr(
        web_app,
        "_completed_local_torrents",
        lambda: [{"name": episode.name, "downloadDir": str(downloads), "percentDone": 1}],
    )

    response = client.post(
        "/api/series-build",
        json={"title": "Example Show", "year": 2020},
        headers={"X-CSRF-Token": _csrf(client, "/api/watch")},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["linked"] == [
        "shows/Example Show (2020)/Season 02/Example.Show.S02E03.mkv"
    ]


def test_list_categories_offer_collapsible_open_and_completed_sections(client):
    csrf = _csrf(client, "/api/todo")
    for title, done in (("Open task", False), ("Finished task", True)):
        added = client.post(
            "/api/todo/add-item",
            json={"category": "Home", "item": title, "done": done},
            headers={"X-CSRF-Token": csrf},
        )
        assert added.status_code == 200

    html = client.get("/to-do").get_data(as_text=True)

    assert 'data-group-toggle="open"' in html and "Open (1)" in html
    assert 'data-group-toggle="done"' in html and "Completed (1)" in html
    assert html.count('class="item" data-group="open"') == 1
    assert html.count('class="item" data-group="done"') == 1


def test_other_lists_do_not_render_torrent_controls(client):
    html = client.get("/to-do").get_data(as_text=True)

    assert "find-btn" not in html.split("</style>")[1]
    assert "lib-check-btn" not in html.split("</style>")[1]


@pytest.mark.parametrize("page", ["to-read", "to-listen"])
def test_read_and_listen_render_library_controls(client, page):
    html = client.get(f"/{page}").get_data(as_text=True)

    assert 'id="media-check-btn"' in html
    assert "media-find-btn" in html
    assert "/api/media/" in html


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
    assert matches["Napoleon Dynamite"] == {
        "found": True,
        "match": "Napoleon Dynamite (2004)",
        "url": "/library/title/movies/Napoleon%20Dynamite%20(2004)",
    }
    assert matches["Movies"]["found"] is False


def test_watch_check_links_same_named_movie_and_show_to_the_right_library_title(
    client, tmp_path, monkeypatch
):
    movie = tmp_path / "movies" / "Sci-Fi" / "Stargate (1994)"
    show = tmp_path / "shows" / "Stargate (1997)"
    movie.mkdir(parents=True)
    show.mkdir(parents=True)
    monkeypatch.setattr(web_app, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(web_app, "_EXTRA_WATCH_DIRS", [])

    resp = client.post(
        "/api/watch-check",
        json={
            "titles": [
                {"title": "Stargate", "year": 1994, "category": "Movies"},
                {"title": "Stargate", "year": 1997, "category": "TV Shows"},
                {"title": "Stargate", "year": 2004, "category": "Movies"},
            ]
        },
        headers={"X-CSRF-Token": _csrf(client, "/api/watch")},
    )

    assert resp.status_code == 200
    movie_result, show_result, missing_result = resp.get_json()["results"]
    assert movie_result["url"] == "/library/title/movies/Sci-Fi/Stargate%20(1994)"
    assert show_result["url"] == "/library/title/shows/Stargate%20(1997)"
    assert missing_result == {"found": False, "match": None, "url": None}


def test_torrent_search_marks_library_duplicates_and_add_rechecks_before_magneto(
    client, tmp_path, monkeypatch
):
    movie = tmp_path / "movies" / "Sci-Fi" / "Stargate (1994)"
    show = tmp_path / "shows" / "Stargate (1997)" / "Season 01"
    movie.mkdir(parents=True)
    show.mkdir(parents=True)
    (movie / "Stargate.1994.mkv").write_bytes(b"video")
    (show / "Stargate.S01E01.mkv").write_bytes(b"video")
    monkeypatch.setattr(web_app, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(web_app, "_EXTRA_WATCH_DIRS", [])
    sent = []
    monkeypatch.setattr(
        web_app, "_proxy_to_magneto", lambda magnet, host: (sent.append(magnet) or True, "")
    )

    def fake_search(query):
        return [
            {"name": "Stargate.S01E01.1080p", "magnet": "magnet:?xt=one"},
            {"name": "Stargate.S01E02.1080p", "magnet": "magnet:?xt=two"},
            {"name": "Stargate.S01.1080p", "magnet": "magnet:?xt=season"},
        ]

    monkeypatch.setattr(web_app, "_search_torrents_csv", fake_search)
    movie_results = client.get(
        "/api/torrent-search?q=Stargate&cat=Movies&title=Stargate&year=1994"
    ).get_json()["results"]
    assert all(result["library_conflict"] for result in movie_results)

    show_results = client.get(
        "/api/torrent-search?q=Stargate&cat=TV%20Shows&title=Stargate&year=1997"
    ).get_json()["results"]
    assert show_results[0]["library_conflict"]
    assert show_results[1]["library_conflict"] is None
    assert show_results[2]["library_conflict"]
    assert show_results[0]["library_url"] == "/library/title/shows/Stargate%20(1997)"

    headers = {"X-CSRF-Token": _csrf(client, "/api/watch")}
    movie_duplicate = client.post(
        "/api/torrent-add",
        json={
            "magnet": "magnet:?xt=movie",
            "name": "Stargate.1994.1080p",
            "title": "Stargate",
            "year": "1994",
            "category": "Movies",
        },
        headers=headers,
    )
    assert movie_duplicate.status_code == 409
    duplicate = client.post(
        "/api/torrent-add",
        json={
            "magnet": "magnet:?xt=one",
            "name": "Stargate.S01E01.1080p",
            "title": "Stargate",
            "year": "1997",
            "category": "TV Shows",
        },
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert sent == []

    missing = client.post(
        "/api/torrent-add",
        json={
            "magnet": "magnet:?xt=two",
            "name": "Stargate.S01E02.1080p",
            "title": "Stargate",
            "year": "1997",
            "category": "TV Shows",
        },
        headers=headers,
    )
    assert missing.status_code == 200
    assert sent == ["magnet:?xt=two"]


def test_episode_search_returns_only_releases_covering_the_requested_episode(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(web_app, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(web_app, "_EXTRA_WATCH_DIRS", [])
    names = [
        "Stargate.S01E01.1080p",
        "Stargate.S01E02.1080p",
        "Stargate.S01.1080p",
        "Stargate.Atlantis.S01E02.1080p",
    ]
    monkeypatch.setattr(
        web_app,
        "_search_torrents_csv",
        lambda _query: [
            {"name": name, "magnet": f"magnet:?xt={index}"} for index, name in enumerate(names)
        ],
    )

    resp = client.get(
        "/api/torrent-search?q=Stargate%20S01E02&cat=TV%20Shows&title=Stargate&season=1&episode=2"
    )

    assert resp.status_code == 200
    assert [result["name"] for result in resp.get_json()["results"]] == names[1:3]
    assert client.get("/api/torrent-search?q=x&season=1").status_code == 400


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
            "requires_human": True,
            "requires_sudo": "yes",
        },
        headers={"X-CSRF-Token": csrf},
    )
    item = added.get_json()["data"]["categories"][-1]["items"][0]
    assert item["priority"] == "P2"
    assert item["in_person"] == "yes"
    assert item["requires_human"] == "yes"
    assert item["requires_sudo"] == "yes"

    updated = client.post(
        "/api/code/update-item",
        json={
            "category": "dedicated",
            "repo": "clockwork",
            "item": "Flash firmware",
            "priority": "p1",
            "in_person": False,
            "requires_human": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    item = updated.get_json()["data"]["categories"][-1]["items"][0]
    assert item["priority"] == "P1"
    assert "in_person" not in item
    assert "requires_human" not in item
    assert item["requires_sudo"] == "yes"  # untouched by a payload that omits it

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
    assert 'data-ready="false"' in html
    assert client.post("/to-do/update-item", data={"csrf_token": csrf}).status_code == 404
