"""Tests for agent-modifiable Clockwork list APIs."""

from __future__ import annotations

import datetime as dt
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
        "INVEST_CLASSIFICATIONS_FILE": tmp_path / "classifications.csv",
        "CODE_LIST_FILE": tmp_path / "to-code.json",
        "CODE_LIST_HISTORY_FILE": tmp_path / "to-code-history.jsonl",
        "TRADILITY_ANALYSIS_FILE": tmp_path / "tradility-analysis.json",
        "GROCERIES_FILE": tmp_path / "groceries.json",
        "GROCERIES_HISTORY_FILE": tmp_path / "groceries-history.jsonl",
        "SHOPPING_FILE": tmp_path / "shopping.json",
        "SHOPPING_HISTORY_FILE": tmp_path / "shopping-history.jsonl",
        "GROCERY_RULES_FILE": tmp_path / "grocery-rules.json",
        "SHOPPING_RULES_FILE": tmp_path / "shopping-rules.json",
    }
    for attr, path in paths.items():
        monkeypatch.setattr(web_app, attr, path)
    paths["PLACEHOLDERS_FILE"] = tmp_path / "placeholders.json"
    monkeypatch.setattr(web_app.placeholders, "FILE", paths["PLACEHOLDERS_FILE"])
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


def test_tradility_api_reports_stale_data_and_refreshes_via_systemd(
    client, list_files, monkeypatch
):
    list_files["TRADILITY_ANALYSIS_FILE"].write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T00:00:00Z",
                "history_period": "5y",
                "tickers": [],
            }
        )
    )
    calls = []
    service_state = ["inactive"]

    def fake_systemctl(*args, scope="user"):
        calls.append((args, scope))
        if args[0] == "is-active":
            return 3, service_state[0]
        return 0, ""

    monkeypatch.setattr(web_app, "_systemctl", fake_systemctl)
    monkeypatch.setattr(web_app, "_scheduler_backend", lambda: "systemd")

    analysis = client.get("/api/tradility-analysis")

    assert analysis.status_code == 200
    payload = analysis.get_json()
    assert payload["freshness"]["stale"] is True
    assert payload["freshness"]["age_seconds"] > 0
    assert payload["refresh"]["active"] is False
    assert payload["artifact_version"]
    assert payload["csrf_token"]

    waiting = client.get("/api/tradility-refresh/status")
    assert waiting.status_code == 200
    assert waiting.get_json()["artifact_version"] == payload["artifact_version"]
    assert waiting.get_json()["refresh"]["active"] is False

    started = client.post(
        "/api/tradility-refresh",
        headers={"X-CSRF-Token": payload["csrf_token"]},
    )

    assert started.status_code == 202
    assert started.get_json()["ok"] is True
    assert (("start", "--no-block", "tradility-refresh.service"), "user") in calls

    service_state[0] = "activating"
    running = client.get("/api/tradility-refresh/status").get_json()
    assert running["refresh"]["active"] is True


def test_tradility_refresh_status_does_not_parse_the_large_artifact(
    client, list_files, monkeypatch
):
    list_files["TRADILITY_ANALYSIS_FILE"].write_text("{not valid json")
    monkeypatch.setattr(web_app, "_systemctl", lambda *_args, **_kwargs: (3, "inactive"))

    status = client.get("/api/tradility-refresh/status")

    assert status.status_code == 200
    assert status.get_json()["artifact_version"]
    assert client.get("/api/tradility-analysis").status_code == 500


def test_tradility_page_uses_one_update_action_and_follows_completion(client):
    html = client.get("/to-tradility").get_data(as_text=True)

    assert "Update market data" in html
    assert 'id="reload-btn"' not in html
    assert "Run refresh" not in html
    assert "Trend 50/200" in html
    assert "async function runRefresh()" in html
    assert "function watchRefresh" in html
    assert "async function monitorRefresh" in html
    assert "'/api/tradility-refresh/status'" in html
    assert "status.artifact_version !== baselineVersion" in html
    assert "loadData({followActiveRefresh: false})" in html
    assert "REFRESH_TIMEOUT_MS = 15 * 60 * 1000" in html
    assert "Update finished, but no new market-data artifact was written" in html
    assert "Historical close" in html
    assert "Technical market rating" in html
    assert "async function openTickerDetail" in html
    assert "URLSearchParams(window.location.search)" in html
    assert "timeZone: 'UTC'" in html
    assert "detailRecord.returns[detailRange]" in html
    assert "Company overview" in html
    assert "Official mission / vision" in html
    assert "safeExternalUrl" in html
    assert 'rel="noopener noreferrer"' in html


def test_tradility_page_can_federate_to_the_standalone_application(client, monkeypatch):
    monkeypatch.setattr(web_app, "TRADILITY_WEB_URL", "https://tradility.example/")

    response = client.get("/to-tradility")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://tradility.example/"


def test_tradility_list_omits_history_and_ticker_detail_returns_it(client, list_files, monkeypatch):
    list_files["TRADILITY_ANALYSIS_FILE"].write_text(
        json.dumps(
            {
                "generated_at": "2026-10-31T06:28:39Z",
                "history_period": "5y",
                "tickers": [
                    {
                        "ticker": "AAPL",
                        "last_price": 313.62,
                        "technical_rating": "buy",
                        "company_profile": {
                            "available": True,
                            "canonical_name": "Apple Inc.",
                            "description": "Designs consumer technology.",
                            "sector": "Technology",
                            "industry": "Consumer Electronics",
                            "website": "https://www.apple.com",
                            "vision_statement": None,
                            "source": "yfinance/yahoo-finance",
                            "retrieved_at": "2026-11-01T00:00:00Z",
                        },
                        "price_history": [
                            {"date": "2025-10-31", "close": 270.0},
                            {"date": "2026-10-30", "close": 313.62},
                        ],
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(web_app, "_systemctl", lambda *_args, **_kwargs: (3, "inactive"))

    summary = client.get("/api/tradility-analysis").get_json()

    assert summary["tickers"][0]["detail_available"] is True
    assert summary["tickers"][0]["profile_available"] is True
    assert "price_history" not in summary["tickers"][0]
    assert "company_profile" not in summary["tickers"][0]

    detail = client.get("/api/tradility-analysis/aapl")

    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["history_period"] == "5y"
    assert payload["ticker"]["technical_rating"] == "buy"
    assert payload["ticker"]["company_profile"]["canonical_name"] == "Apple Inc."
    assert len(payload["ticker"]["price_history"]) == 2
    assert client.get("/api/tradility-analysis/MISSING").status_code == 404


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

    def post(rows):
        return client.post(
            "/to-invest/update-targets",
            data={"csrf_token": csrf, "targets_json": json.dumps(rows)},
            follow_redirects=True,
        )

    def targets():
        items = client.get("/api/invest").get_json()["data"]["categories"][0]["items"]
        return {i["ticker"]: i.get("target_pct") for i in items}

    html = post(
        [
            {"category": "Stocks", "ticker": "AMD", "target_pct": "15"},
            {"category": "Stocks", "ticker": "NVDA", "target_pct": "10"},
        ]
    ).get_data(as_text=True)
    assert "Saved 2 target changes." in html
    # Tickers are shares of the whole portfolio, so they are rescaled to add to 100%.
    assert targets() == {"AMD": "60", "NVDA": "40"}

    invalid = post(
        [
            {"category": "Stocks", "ticker": "AMD", "target_pct": "20"},
            {"category": "Stocks", "ticker": "NVDA", "target_pct": "101"},
        ]
    )
    assert "Target must be a number from 0 to 100." in invalid.get_data(as_text=True)
    assert targets() == {"AMD": "60", "NVDA": "40"}

    missing = post(
        [
            {"category": "Stocks", "ticker": "AMD", "target_pct": "20"},
            {"category": "Stocks", "ticker": "MISSING", "target_pct": "5"},
        ]
    )
    assert "No targets were saved." in missing.get_data(as_text=True)
    assert targets() == {"AMD": "60", "NVDA": "40"}

    # Raising one ticker shrinks the other so the total stays 100%.
    post(
        [
            {"category": "Stocks", "ticker": "AMD", "target_pct": "70"},
            {"category": "Stocks", "ticker": "NVDA", "target_pct": "40"},
        ]
    )
    assert targets() == {"AMD": "70", "NVDA": "30"}

    cleared = post(
        [
            {"category": "Stocks", "ticker": "AMD", "target_pct": ""},
            {"category": "Stocks", "ticker": "NVDA", "target_pct": "30"},
        ]
    )
    assert "Saved 2 target changes." in cleared.get_data(as_text=True)
    assert targets() == {"AMD": None, "NVDA": "100"}


def test_to_invest_basket_targets_fold_into_one_hundred_percent(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Stocks",
                        "target_mode": "basket",
                        "basket_target_pct": "25",
                        "items": [
                            {"ticker": "AMD", "holding": True, "target_pct": "60"},
                            {"ticker": "NVDA", "holding": True, "target_pct": "40"},
                        ],
                    },
                    {
                        "name": "Crypto",
                        "items": [{"ticker": "BTC", "holding": True, "target_pct": "25"}],
                    },
                ]
            }
        )
    )
    csrf = _csrf(client, "/api/invest")
    response = client.post(
        "/to-invest/update-targets",
        data={"csrf_token": csrf, "targets_json": json.dumps({"categories": [], "items": []})},
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Basket total" not in html
    assert "Targets assigned <strong>100.00%</strong>" in html
    categories = client.get("/api/invest").get_json()["data"]["categories"]
    assert all("target_mode" not in c and "basket_target_pct" not in c for c in categories)
    by_ticker = {i["ticker"]: float(i["target_pct"]) for c in categories for i in c["items"]}
    # Basket Stocks counted as 25% x (60%, 40%) before: 15, 10, plus 25 crypto.
    assert by_ticker == {"AMD": 30, "NVDA": 20, "BTC": 50}


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
    assert "clockwork:to-invest:target-scroll" in html
    assert "document.elementFromPoint" in html
    assert "window.scrollBy" in html
    assert 'data-invest-anchor="item:Stocks:OVER"' in html
    assert 'aria-label="Stocks allocation summary"' in html
    assert "Now <strong>21.50%</strong>" in html
    assert "Global target <strong>20.00%</strong>" in html
    assert 'data-delta-scope="category" data-delta="1.5040"' in html
    assert "Δ +1.50% overweight" in html


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
    assert "Repository (blank: auto)" in html
    assert 'name="category" aria-label="Repo group">' in html
    assert "Group: auto" in html
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
        "added": dt.date.today().isoformat(),
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
    assert 'data-query="Brazil 1985"' in html  # watched items can still be searched
    assert html.count('class="search-panel"') == 2
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


def test_a_watched_item_keeps_its_torrent_search(client, list_files):
    list_files["WATCH_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Films",
                        "items": [
                            {"title": "Seen Film", "watched": True},
                            {"title": "Unseen Film", "watched": False},
                        ],
                    }
                ]
            }
        )
    )
    html = client.get("/to-watch").get_data(as_text=True)

    assert html.count('title="Find torrent"') == 2
    assert html.count('class="search-panel"') == 2


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


def _seed_industries(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {"name": "Stocks", "items": [{"ticker": "AAA", "holding": True}]},
                    {
                        "name": "Sectors",
                        "items": [{"ticker": "ENERGY", "holding": False, "target_pct": "7"}],
                    },
                ]
            }
        )
    )
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(
        json.dumps(
            {
                "holdings": [
                    {"ticker": "AAA", "asset_class": "Stocks", "total_ending_value": 50},
                    {"ticker": "BBB", "asset_class": "Stocks", "total_ending_value": 30},
                    {"ticker": "XYZ", "asset_class": "Crypto", "total_ending_value": 20},
                ]
            }
        )
    )
    list_files["INVEST_CLASSIFICATIONS_FILE"].write_text(
        "symbol,cusip,sector,industry,sub_industry\n"
        "AAA,1,Technology,Semiconductors,\n"
        "BBB,2,Technology,Software,\n"
        "CCC,3,Energy,Oil & Gas,\n"
    )
    csrf = _csrf(client, "/api/invest")
    client.post("/to-invest/sync-holdings", data={"csrf_token": csrf})
    return csrf


def _cards(client, name):
    data = client.get("/api/invest").get_json()["data"]
    category = next(c for c in data["categories"] if c["name"] == name)
    return {i["ticker"]: i for i in category["items"]}, [i["ticker"] for i in category["items"]]


def test_sync_builds_sector_and_industry_cards_from_holdings(client, list_files):
    _seed_industries(client, list_files)
    sectors, order = _cards(client, "Sectors")
    assert sectors["TECHNOLOGY"]["allocation_pct"] == "80"
    assert sectors["TECHNOLOGY"]["holding"] is True
    assert sectors["TECHNOLOGY"]["name"] == "AAA 50%, BBB 30%"
    assert sectors["ENERGY"]["allocation_pct"] == "0"
    assert sectors["ENERGY"]["holding"] is False
    # Crypto is its own industry and rolls up once, into Financials.
    assert sectors["FINANCIALS"]["allocation_pct"] == "20"
    assert sectors["FINANCIALS"]["name"] == "XYZ 20%"
    assert "UNCLASSIFIED" not in sectors
    industries, _ = _cards(client, "Industries")
    assert industries["SEMICONDUCTORS"]["allocation_pct"] == "50"
    assert industries["SOFTWARE"]["allocation_pct"] == "30"
    assert industries["CRYPTO"]["allocation_pct"] == "20"
    assert "TECHNOLOGY" not in industries
    assert order[0] == "ENERGY"  # existing card keeps its place


def test_derived_cards_stay_out_of_page_totals(client, list_files):
    _seed_industries(client, list_files)
    html = client.get("/to-invest").get_data(as_text=True)
    # Every position is already listed once; sector cards would push this past 100%.
    assert "Listed now <strong>100.00%</strong>" in html
    assert "Targets assigned <strong>0.00%</strong>" in html


def test_cards_can_be_ordered_by_holding_percent(client, list_files):
    _seed_industries(client, list_files)
    html = client.get("/to-invest?order=holding").get_data(as_text=True)
    sectors = html[html.index('data-invest-anchor="category:Sectors"') :]
    assert sectors.index("TECHNOLOGY") < sectors.index("FINANCIALS") < sectors.index("ENERGY")
    assert '<button class="group-toggle"' not in html
    assert "/to-invest/move-item" not in html  # arrows belong to rank mode only


def test_target_rank_moves_a_card_up_and_down(client, list_files):
    csrf = _seed_industries(client, list_files)
    _, order = _cards(client, "Sectors")
    assert order == ["ENERGY", "TECHNOLOGY", "FINANCIALS"]
    html = client.get("/to-invest?order=rank").get_data(as_text=True)
    assert 'action="/to-invest/move-item"' in html and "#1" in html

    def move(item, direction):
        return client.post(
            "/to-invest/move-item",
            data={"csrf_token": csrf, "category": "Sectors", "item": item, "direction": direction},
        )

    assert "order=rank" in move("FINANCIALS", "up").headers["Location"]
    assert _cards(client, "Sectors")[1] == ["ENERGY", "FINANCIALS", "TECHNOLOGY"]
    move("ENERGY", "up")  # already first: unchanged
    assert _cards(client, "Sectors")[1] == ["ENERGY", "FINANCIALS", "TECHNOLOGY"]
    move("ENERGY", "down")
    assert _cards(client, "Sectors")[1] == ["FINANCIALS", "ENERGY", "TECHNOLOGY"]
    move("NOPE", "up")
    assert _cards(client, "Sectors")[1] == ["FINANCIALS", "ENERGY", "TECHNOLOGY"]


def _targets(client, name):
    cards, _ = _cards(client, name)
    return {k: float(v.get("target_pct") or 0) for k, v in cards.items()}


def _save(client, csrf, rows):
    return client.post(
        "/to-invest/update-targets",
        data={"csrf_token": csrf, "targets_json": json.dumps({"categories": [], "items": rows})},
        follow_redirects=True,
    )


def _seed_tree(client, list_files):
    """Three tickers: two semiconductor stocks and one software ETF, all Technology."""

    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Stocks",
                        "items": [
                            {"ticker": "AAA", "holding": True, "target_pct": "30"},
                            {"ticker": "BBB", "holding": True, "target_pct": "30"},
                            {"ticker": "DDD", "holding": True, "target_pct": "20"},
                        ],
                    },
                    {
                        "name": "ETFs",
                        "items": [{"ticker": "EEE", "holding": True, "target_pct": "20"}],
                    },
                    {"name": "Crypto", "items": [{"ticker": "BTC", "holding": True}]},
                ]
            }
        )
    )
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(
        json.dumps(
            {
                "holdings": [
                    {"ticker": t, "asset_class": c, "total_ending_value": 10}
                    for t, c in (
                        ("AAA", "Stocks"),
                        ("BBB", "Stocks"),
                        ("DDD", "Stocks"),
                        ("EEE", "ETFs"),
                        ("BTC", "Crypto"),
                    )
                ]
            }
        )
    )
    list_files["INVEST_CLASSIFICATIONS_FILE"].write_text(
        "symbol,cusip,sector,industry,sub_industry\n"
        "AAA,1,Technology,Semiconductors,\n"
        "BBB,2,Technology,Semiconductors,\n"
        "EEE,3,Technology,Software,\n"
        "DDD,4,Energy,Oil & Gas,\n"
        "BTC,5,Financials,Asset Management,\n"
    )
    csrf = _csrf(client, "/api/invest")
    client.post("/to-invest/sync-holdings", data={"csrf_token": csrf})
    return csrf


def test_sync_rolls_targets_up_and_counts_crypto_once(client, list_files):
    _seed_tree(client, list_files)
    sectors = _targets(client, "Sectors")
    industries = _targets(client, "Industries")
    assert sectors["TECHNOLOGY"] == 80 and sectors["ENERGY"] == 20
    assert industries["SEMICONDUCTORS"] == 60 and industries["SOFTWARE"] == 20
    # BTC sits in its own Crypto industry, under Financials, and nowhere else.
    assert "ASSET MANAGEMENT" not in {k for k, v in industries.items() if v}
    cards, _ = _cards(client, "Industries")
    assert cards["CRYPTO"]["allocation_pct"] == "20"
    assert cards["ASSET MANAGEMENT"]["allocation_pct"] == "0"
    assert (
        sum(float(v.get("allocation_pct") or 0) for v in _cards(client, "Sectors")[0].values())
        == 100
    )
    assert sum(sectors.values()) == sum(industries.values()) == 100


def test_editing_a_ticker_rolls_up_to_industry_and_sector(client, list_files):
    csrf = _seed_tree(client, list_files)
    _save(client, csrf, [{"category": "Stocks", "ticker": "AAA", "target_pct": "50"}])
    stocks = _targets(client, "Stocks")
    assert stocks["AAA"] == 50
    assert round(sum(stocks.values()) + _targets(client, "ETFs")["EEE"], 3) == 100
    assert _targets(client, "Industries")["SEMICONDUCTORS"] == round(
        stocks["AAA"] + stocks["BBB"], 4
    )
    assert round(_targets(client, "Sectors")["TECHNOLOGY"], 3) == round(
        stocks["AAA"] + stocks["BBB"] + _targets(client, "ETFs")["EEE"], 3
    )


def test_editing_an_industry_or_sector_cascades_down_and_rebalances(client, list_files):
    csrf = _seed_tree(client, list_files)
    _save(
        client, csrf, [{"category": "Industries", "ticker": "SEMICONDUCTORS", "target_pct": "30"}]
    )
    stocks = _targets(client, "Stocks")
    assert stocks["AAA"] == stocks["BBB"] == 15  # split in their old 1:1 ratio
    assert _targets(client, "Industries")["SEMICONDUCTORS"] == 30
    assert round(sum(_targets(client, "Sectors").values()), 3) == 100
    assert (
        round(
            stocks["DDD"] + _targets(client, "ETFs")["EEE"] + _targets(client, "Crypto")["BTC"], 3
        )
        == 70
    )

    _save(client, csrf, [{"category": "Sectors", "ticker": "ENERGY", "target_pct": "50"}])
    assert _targets(client, "Stocks")["DDD"] == 50
    assert _targets(client, "Sectors")["ENERGY"] == 50
    assert round(sum(_targets(client, "Sectors").values()), 3) == 100
    # Technology shrank in proportion: its industries keep their 30:35 ratio.
    industries = _targets(client, "Industries")
    assert round(industries["SEMICONDUCTORS"] / industries["SOFTWARE"], 3) == round(30 / 35, 3)


def test_an_edit_with_nothing_beneath_it_is_refused(client, list_files):
    csrf = _seed_tree(client, list_files)
    before = _targets(client, "Stocks")
    response = _save(
        client, csrf, [{"category": "Industries", "ticker": "BIOTECHNOLOGY", "target_pct": "10"}]
    )
    assert "No targets were saved." in response.get_data(as_text=True)
    assert _targets(client, "Stocks") == before


def test_typed_tiers_hold_and_nested_edits_fit_inside_them(client, list_files):
    csrf = _seed_tree(client, list_files)
    _save(
        client,
        csrf,
        [
            {"category": "Sectors", "ticker": "TECHNOLOGY", "target_pct": "50"},
            {"category": "Stocks", "ticker": "AAA", "target_pct": "10"},
        ],
    )
    stocks = _targets(client, "Stocks")
    assert stocks["AAA"] == 10 and stocks["DDD"] == 50
    assert _targets(client, "Sectors")["TECHNOLOGY"] == 50  # typed value kept
    # BBB and EEE fill the rest of Technology in their old 30:20 ratio.
    assert stocks["BBB"] == 24 and _targets(client, "ETFs")["EEE"] == 16


def test_an_unrelated_edit_does_not_move_a_typed_sector(client, list_files):
    csrf = _seed_tree(client, list_files)
    _save(
        client,
        csrf,
        [
            {"category": "Sectors", "ticker": "TECHNOLOGY", "target_pct": "30"},
            {"category": "Industries", "ticker": "OIL & GAS", "target_pct": "40"},
        ],
    )
    sectors = _targets(client, "Sectors")
    assert sectors["TECHNOLOGY"] == 30 and sectors["ENERGY"] == 40
    assert round(sum(sectors.values()), 3) == 100


def test_typed_targets_that_cannot_all_hold_are_refused(client, list_files):
    csrf = _seed_tree(client, list_files)
    before = _targets(client, "Stocks")
    too_big = _save(
        client,
        csrf,
        [
            {"category": "Sectors", "ticker": "TECHNOLOGY", "target_pct": "30"},
            {"category": "Stocks", "ticker": "AAA", "target_pct": "40"},
        ],
    )
    html = too_big.get_data(as_text=True)
    assert "No targets were saved." in html and "under Technology" in html
    over = _save(
        client,
        csrf,
        [
            {"category": "Sectors", "ticker": "TECHNOLOGY", "target_pct": "70"},
            {"category": "Sectors", "ticker": "ENERGY", "target_pct": "50"},
        ],
    )
    assert "No targets were saved." in over.get_data(as_text=True)
    assert _targets(client, "Stocks") == before


def test_tradility_analysis_with_bare_nan_is_served_as_strict_json_without_live_bars(
    client, list_files
):
    list_files["TRADILITY_ANALYSIS_FILE"].write_text(
        '{"generated_at": "2026-11-05T16:00:00Z", "tickers": [{"ticker": "AAPL", '
        '"price_history": [{"date": "2026-11-04", "close": 1}], '
        '"live_bars": {"high": [NaN, 2.0], "close": [1.0, 2.0]}, "rsi_14": NaN}]}'
    )

    def strict(text):
        return json.loads(text, parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))

    listing = strict(client.get("/api/tradility-analysis").get_data(as_text=True))
    assert "live_bars" not in listing["tickers"][0] and listing["tickers"][0]["rsi_14"] is None
    detail = strict(client.get("/api/tradility-analysis/aapl").get_data(as_text=True))
    assert "live_bars" not in detail["ticker"]


def test_to_invest_links_each_ticker_with_a_tradility_card_and_only_those(client, list_files):
    list_files["TRADILITY_ANALYSIS_FILE"].write_text(
        json.dumps(
            {
                "generated_at": "2026-11-05T16:00:00Z",
                "tickers": [
                    {"ticker": "AAA", "price_history": [{"date": "2026-11-04", "close": 1}]},
                    {"ticker": "BBB", "price_history": []},  # analysed, but no detail view to open
                    {"ticker": "TECHNOLOGY", "price_history": [{"date": "2026-11-04", "close": 1}]},
                ],
            }
        )
    )
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Stocks",
                        "items": [{"ticker": t, "holding": False} for t in ("AAA", "BBB", "CCC")],
                    },
                    {"name": "Crypto", "items": [{"ticker": "A/B", "holding": False}]},
                ]
            }
        )
    )
    html = client.get("/to-invest").get_data(as_text=True)
    assert 'href="/to-tradility?ticker=AAA"' in html
    assert 'aria-label="Open AAA in Tradility"' in html
    assert "ticker=BBB" not in html and "ticker=CCC" not in html  # no card, no link
    assert html.count("/to-tradility?ticker=") == 1


def test_to_invest_card_links_follow_a_configured_tradility_url(client, list_files, monkeypatch):
    list_files["TRADILITY_ANALYSIS_FILE"].write_text(
        json.dumps({"tickers": [{"ticker": "AAA", "price_history": [{"date": "d", "close": 1}]}]})
    )
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {"categories": [{"name": "Stocks", "items": [{"ticker": "AAA", "holding": False}]}]}
        )
    )
    monkeypatch.setenv("CLOCKWORK_TRADILITY_CARD_URL", "https://tradility.example/?ticker={ticker}")
    assert 'href="https://tradility.example/?ticker=AAA"' in client.get("/to-invest").get_data(
        as_text=True
    )
    monkeypatch.setenv("CLOCKWORK_TRADILITY_CARD_URL", "https://tradility.example/no-placeholder")
    assert 'href="/to-tradility?ticker=AAA"' in client.get("/to-invest").get_data(as_text=True)


def test_to_invest_rows_have_anchors_for_real_tickers_only_and_a_reveal_script(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {"name": "Stocks", "items": [{"ticker": "AAA", "holding": True}]},
                    {"name": "Sectors", "items": [{"ticker": "TECHNOLOGY", "holding": False}]},
                ]
            }
        )
    )
    html = client.get("/to-invest").get_data(as_text=True)
    assert 'id="invest-item-AAA"' in html
    assert "invest-item-TECHNOLOGY" not in html  # derived cards are not tickers
    assert "revealLinkedRow" in html and "hashchange" in html and "group-collapsed" in html
    assert "scroll-margin-top" in html and "setTimeout(revealLinkedRow, 60)" in html


def _seed_duplicate_btc(client, list_files):
    """BTC is a Schwab ETF and a Binance coin: one ticker, two rows, two categories."""
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "ETFs",
                        "items": [{"ticker": "BTC", "holding": False, "target_pct": "10"}],
                    },
                    {
                        "name": "Crypto",
                        "items": [
                            {"ticker": "BTC", "holding": False, "target_pct": "30"},
                            {"ticker": "ETH", "holding": False, "target_pct": "60"},
                        ],
                    },
                ]
            }
        )
    )
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(
        json.dumps(
            {
                "holdings": [
                    {
                        "ticker": "BTC",
                        "asset_class": "ETFs",  # the export labels a merged row by its first account
                        "total_ending_value": 1000,
                        "accounts": [
                            {"asset_class": "ETFs", "ending_value": 200},
                            {"asset_class": "Crypto", "ending_value": 800},
                        ],
                    },
                    {"ticker": "ETH", "asset_class": "Crypto", "total_ending_value": 1000},
                ]
            }
        )
    )
    csrf = _csrf(client, "/api/invest")
    client.post("/to-invest/sync-holdings", data={"csrf_token": csrf})
    return csrf


def test_a_ticker_in_two_categories_splits_its_holding_by_account_class(client, list_files):
    _seed_duplicate_btc(client, list_files)
    etfs, _ = _cards(client, "ETFs")
    crypto, _ = _cards(client, "Crypto")
    # portfolio 2000: BTC ETF $200 = 10%, BTC coin $800 = 40%, ETH $1000 = 50%
    assert etfs["BTC"]["allocation_pct"] == "10" and etfs["BTC"]["holding"] is True
    assert crypto["BTC"]["allocation_pct"] == "40"
    assert crypto["ETH"]["allocation_pct"] == "50"


def test_each_row_of_a_duplicated_ticker_keeps_its_own_target(client, list_files):
    csrf = _seed_duplicate_btc(client, list_files)
    before = {"etf": _targets(client, "ETFs")["BTC"], "coin": _targets(client, "Crypto")["BTC"]}
    assert round(before["etf"] + before["coin"] + _targets(client, "Crypto")["ETH"], 3) == 100
    _save(client, csrf, [{"category": "Crypto", "ticker": "BTC", "target_pct": "50"}])
    assert _targets(client, "Crypto")["BTC"] == 50  # the coin row moved
    assert _targets(client, "ETFs")["BTC"] < before["etf"]  # the ETF row only rescaled around it
    total = _targets(client, "ETFs")["BTC"] + sum(_targets(client, "Crypto").values())
    assert round(total, 3) == 100  # both BTC rows are counted once each


def test_a_duplicated_ticker_gets_one_anchor_not_two(client, list_files):
    _seed_duplicate_btc(client, list_files)
    html = client.get("/to-invest").get_data(as_text=True)
    assert html.count('id="invest-item-BTC"') == 1
    assert html.count('id="invest-item-ETH"') == 1


def test_duplicated_tickers_are_marked_in_to_invest_with_links_to_their_other_rows(
    client, list_files
):
    _seed_duplicate_btc(client, list_files)
    html = client.get("/to-invest").get_data(as_text=True)
    # the ETFs row points at the Crypto row and the Crypto row at the ETFs row
    assert html.count('class="dup-badge"') == 2
    assert 'href="#invest-item-BTC--crypto">Crypto</a>' in html
    assert 'href="#invest-item-BTC">ETFs</a>' in html
    assert 'id="invest-item-BTC"' in html and 'id="invest-item-BTC--crypto"' in html
    # only the two BTC rows carry the badge; ETH (a single row) does not


def test_a_single_ticker_has_no_duplicate_badge(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {"categories": [{"name": "Stocks", "items": [{"ticker": "AAA", "holding": False}]}]}
        )
    )
    html = client.get("/to-invest").get_data(as_text=True)
    assert 'class="dup-badge"' not in html


def test_sync_adds_the_missing_row_when_a_holding_spans_two_categories(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {"name": "Crypto", "items": [{"ticker": "BTC", "holding": True}]},
                    {"name": "ETFs", "items": []},
                ]
            }
        )
    )
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(
        json.dumps(
            {
                "holdings": [
                    {
                        "ticker": "BTC",
                        "description": "Grayscale Bitcoin Mini Trust ETF",
                        "asset_class": "ETFs",
                        "total_ending_value": 1000,
                        "accounts": [
                            {"asset_class": "ETFs", "ending_value": 250},
                            {"asset_class": "Crypto", "ending_value": 750},
                        ],
                    },
                    # a ticker held in one class only never gets a second row
                    {
                        "ticker": "VOO",
                        "asset_class": "ETFs",
                        "total_ending_value": 1000,
                        "accounts": [{"asset_class": "ETFs", "ending_value": 1000}],
                    },
                ]
            }
        )
    )
    csrf = _csrf(client, "/api/invest")
    reply = client.post(
        "/to-invest/sync-holdings", data={"csrf_token": csrf}, follow_redirects=True
    )
    assert "2 new tickers added" in reply.get_data(as_text=True)  # the BTC ETF row and VOO
    etfs, _ = _cards(client, "ETFs")
    crypto, _ = _cards(client, "Crypto")
    assert set(etfs) == {"BTC", "VOO"} and set(crypto) == {"BTC"}
    assert etfs["BTC"]["allocation_pct"] == "12.5" and crypto["BTC"]["allocation_pct"] == "37.5"
    assert (
        etfs["BTC"]["name"] == "Grayscale Bitcoin Mini Trust ETF" and etfs["BTC"]["holding"] is True
    )
    again = client.post(
        "/to-invest/sync-holdings", data={"csrf_token": csrf}, follow_redirects=True
    )
    assert "new tickers added" not in again.get_data(as_text=True)  # a second sync adds nothing


def test_adding_a_ticker_to_a_second_category_says_it_is_a_duplicate(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {"name": "Stocks", "items": [{"ticker": "AAA", "holding": False}]},
                    {"name": "ETFs", "items": []},
                ]
            }
        )
    )
    csrf = _csrf(client, "/api/invest")
    reply = client.post(
        "/to-invest/add-item",
        data={"csrf_token": csrf, "category": "ETFs", "item": "AAA"},
        follow_redirects=True,
    )
    html = reply.get_data(as_text=True)
    assert "&#34;AAA&#34; is also in Stocks" in html or '"AAA" is also in Stocks' in html
    assert [i["ticker"] for i in _cards(client, "ETFs")[0].values()] == ["AAA"]


def _sync_holdings(client, list_files, categories, holdings):
    list_files["INVEST_LIST_FILE"].write_text(json.dumps({"categories": categories}))
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(json.dumps({"holdings": holdings}))
    csrf = _csrf(client, "/api/invest")
    return client.post(
        "/to-invest/sync-holdings", data={"csrf_token": csrf}, follow_redirects=True
    ).get_data(as_text=True)


def test_a_fund_labelled_differently_by_two_custodians_is_not_duplicated(client, list_files):
    # VOO is ETFs at Schwab and Stocks at Fidelity: one fund, so one row (the real-data regression).
    html = _sync_holdings(
        client,
        list_files,
        [
            {"name": "ETFs", "items": [{"ticker": "VOO", "holding": True}]},
            {"name": "Stocks", "items": []},
        ],
        [
            {
                "ticker": "VOO",
                "asset_class": "ETFs",
                "total_ending_value": 1000,
                "accounts": [
                    {"asset_class": "ETFs", "ending_value": 600},
                    {"asset_class": "Stocks", "ending_value": 400},
                ],
            }
        ],
    )
    assert "new tickers added" not in html
    assert _cards(client, "Stocks")[1] == [] and list(_cards(client, "ETFs")[0]) == ["VOO"]
    assert _cards(client, "ETFs")[0]["VOO"]["allocation_pct"] == "100"  # the whole holding, once


def test_the_same_symbol_as_a_stock_and_a_coin_gets_a_row_for_each(client, list_files):
    # T is AT&T at Schwab and a small Binance coin.
    html = _sync_holdings(
        client,
        list_files,
        [
            {"name": "Stocks", "items": [{"ticker": "T", "holding": True}]},
            {"name": "Crypto", "items": []},
        ],
        [
            {
                "ticker": "T",
                "asset_class": "Stocks",
                "total_ending_value": 1000,
                "accounts": [
                    {"asset_class": "Stocks", "ending_value": 990},
                    {"asset_class": "Crypto", "ending_value": 10},
                ],
            }
        ],
    )
    assert "1 new tickers added" in html
    assert _cards(client, "Crypto")[0]["T"]["allocation_pct"] == "1"
    assert _cards(client, "Stocks")[0]["T"]["allocation_pct"] == "99"


def test_another_custodians_label_goes_to_the_stock_or_fund_row_not_nowhere(client, list_files):
    # BTC the ETF is held at Schwab (ETFs) and Fidelity (Stocks); the coin is at Binance.
    _sync_holdings(
        client,
        list_files,
        [
            {"name": "ETFs", "items": [{"ticker": "BTC", "holding": True}]},
            {"name": "Crypto", "items": [{"ticker": "BTC", "holding": True}]},
        ],
        [
            {
                "ticker": "BTC",
                "asset_class": "ETFs",
                "total_ending_value": 1000,
                "accounts": [
                    {"asset_class": "ETFs", "ending_value": 100},
                    {"asset_class": "Stocks", "ending_value": 100},
                    {"asset_class": "Crypto", "ending_value": 800},
                ],
            }
        ],
    )
    assert _cards(client, "ETFs")[0]["BTC"]["allocation_pct"] == "20"
    assert _cards(client, "Crypto")[0]["BTC"]["allocation_pct"] == "80"
