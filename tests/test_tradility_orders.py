"""Average entry, order history and open orders recorded per Tradility ticker."""

import json

import pytest

from tests.test_web_lists import client, list_files, web_app  # noqa: F401
from web import tradility_orders as t

HOLDINGS = [
    {"ticker": "AAPL", "total_quantity": 10.0, "total_cost_basis": 1500.0},
    {"ticker": "BTC", "total_quantity": 2.0, "total_cost_basis": 100000.0},
    {"ticker": "MSFT", "total_quantity": 4.0, "total_cost_basis": None},
    {"ticker": "PART", "total_quantity": 3.0, "total_cost_basis": 300.0},
    {"ticker": "PART", "total_quantity": 1.0, "total_cost_basis": None},
]


def test_average_entry_is_cost_basis_over_quantity_and_crypto_matches_its_usd_pair():
    assert t.position_from_holdings("AAPL", HOLDINGS) == {
        "quantity": 10.0,
        "avg_entry": 150.0,
        "source": "holdings",
    }
    assert t.position_from_holdings("BTC-USD", HOLDINGS)["avg_entry"] == 50000.0
    assert t.position_from_holdings("NOPE", HOLDINGS) is None


def test_a_missing_or_partial_cost_basis_gives_no_average():
    assert t.position_from_holdings("MSFT", HOLDINGS)["avg_entry"] is None
    assert t.position_from_holdings("PART", HOLDINGS)["avg_entry"] is None  # one lot unpriced


def test_filled_buys_are_the_fallback_average_and_open_orders_are_not_counted():
    orders = {
        "MSFT": [
            {"side": "buy", "status": "filled", "quantity": 2, "price": 100, "date": "2026-01-01"},
            {"side": "buy", "status": "filled", "quantity": 2, "price": 200, "date": "2026-02-01"},
            {"side": "buy", "status": "open", "quantity": 100, "price": 1, "date": "2026-03-01"},
            {"side": "sell", "status": "filled", "quantity": 1, "price": 999, "date": "2026-03-02"},
        ]
    }
    got = t.summary("MSFT", orders, HOLDINGS)
    assert got["position"]["avg_entry"] == 150.0 and got["position"]["source"] == "orders"
    assert got["position"]["quantity"] == 4.0
    assert [o["status"] for o in got["orders"]][0] == "open"  # open orders first


@pytest.mark.parametrize(
    "bad",
    [
        {"side": "hold", "quantity": 1, "price": 1},
        {"side": "buy", "quantity": 0, "price": 1},
        {"side": "buy", "quantity": "x", "price": 1},
        {"side": "buy", "quantity": 1},  # limit needs a price
        {"side": "buy", "kind": "market", "status": "filled", "quantity": 1},
        {"side": "buy", "quantity": 1, "price": 1, "date": "2026-13-40"},
        {"side": "buy", "quantity": 1, "price": 1, "note": "x" * 121},
        {"side": "buy", "quantity": float("inf"), "price": 1},
        "not an object",
    ],
)
def test_bad_orders_are_refused(bad):
    with pytest.raises(ValueError):
        t.clean_order(bad)


def test_an_open_market_order_needs_no_price():
    assert t.clean_order({"side": "buy", "kind": "market", "quantity": 1})["price"] is None


@pytest.fixture
def api(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_FILE", str(tmp_path / "orders.json"))
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_EXPORT", str(tmp_path / "broker-orders.json"))
    held = tmp_path / "holdings.json"
    held.write_text(json.dumps({"holdings": HOLDINGS}))
    monkeypatch.setattr(web_app, "HOLDINGS_AGGREGATE_FILE", held)
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-token"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "test-token"
    return client, tmp_path / "orders.json"


def test_orders_are_added_changed_and_deleted_over_http_and_stored_owner_only(api):
    c, store = api
    first = c.get("/api/tradility-orders/aapl").get_json()
    assert first["position"]["avg_entry"] == 150.0 and first["orders"] == []

    made = c.post(
        "/api/tradility-orders/AAPL",
        json={"side": "buy", "kind": "limit", "quantity": 5, "price": 140},
    )
    assert made.status_code == 201
    order = made.get_json()["orders"][0]
    assert order["status"] == "open" and len(order["id"]) == 12
    assert (store.stat().st_mode & 0o777) == 0o600

    full = {
        **{k: order[k] for k in ("side", "kind", "quantity", "price", "date", "note")},
        "status": "filled",
    }
    done = c.put(f"/api/tradility-orders/AAPL/{order['id']}", json=full).get_json()
    assert done["orders"][0]["status"] == "filled"

    gone = c.delete(f"/api/tradility-orders/AAPL/{order['id']}").get_json()
    assert gone["orders"] == [] and "AAPL" not in json.loads(store.read_text())["orders"]
    assert c.delete(f"/api/tradility-orders/AAPL/{order['id']}").status_code == 404


def test_bad_input_and_bad_tickers_are_refused_over_http(api):
    c, store = api
    assert c.post("/api/tradility-orders/AAPL", json={"side": "buy"}).status_code == 400
    assert (
        c.post("/api/tradility-orders/AAPL", data="nope", content_type="text/plain").status_code
        == 400
    )
    assert c.get("/api/tradility-orders/not a ticker!").status_code == 404
    assert not store.exists()


def test_a_missing_holdings_file_still_serves_orders(api, monkeypatch, tmp_path):
    c, _ = api
    monkeypatch.setattr(web_app, "HOLDINGS_AGGREGATE_FILE", tmp_path / "absent.json")
    got = c.get("/api/tradility-orders/AAPL").get_json()
    assert got["ok"] and got["position"]["avg_entry"] is None


def test_the_detail_view_draws_the_average_entry_as_a_horizontal_line_and_lists_orders():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "function chartLevels",
        "Avg entry",
        "setLineDash",
        "/api/tradility-orders/",
        "detail-orders-card",
    ):
        assert needle in html, needle


EXPORT = {
    "generated_at": "2026-11-01T01:00:00+00:00",
    "sources": {"schwab": {"ok": True, "orders": 2}, "binance": {"ok": False, "error": "x"}},
    "orders": {
        "AAPL": [
            {
                "id": "schwab-1",
                "source": "schwab",
                "side": "buy",
                "kind": "limit",
                "status": "filled",
                "quantity": 1,
                "price": 10.0,
                "date": "2026-10-01",
                "note": "",
            },
            {
                "id": "schwab-2",
                "source": "schwab",
                "side": "sell",
                "kind": "limit",
                "status": "open",
                "quantity": 1,
                "price": 20.0,
                "date": "2026-10-20",
                "note": "",
            },
        ],
        "BTC": [
            {
                "id": "binance-1",
                "source": "binance",
                "side": "buy",
                "kind": "limit",
                "status": "open",
                "quantity": 1,
                "price": 5.0,
                "date": "2026-10-21",
                "note": "",
            },
        ],
    },
}


def test_broker_orders_are_listed_beside_manual_ones_marked_by_source_and_open_first():
    manual = {
        "AAPL": [
            {
                "id": "m1",
                "side": "buy",
                "kind": "limit",
                "status": "filled",
                "quantity": 1,
                "price": 1.0,
                "date": "2026-10-10",
                "note": "",
            }
        ]
    }
    got = t.summary("AAPL", manual, HOLDINGS, EXPORT)
    assert [(o["id"], o["source"]) for o in got["orders"]] == [
        ("schwab-2", "schwab"),
        ("m1", "manual"),
        ("schwab-1", "schwab"),
    ]
    assert got["synced"] == {
        "generated_at": EXPORT["generated_at"],
        "sources": {"schwab": True, "binance": False},
    }


def test_synced_fills_never_become_the_fallback_average_entry():
    got = t.summary("MSFT", {}, HOLDINGS, {**EXPORT, "orders": {"MSFT": EXPORT["orders"]["AAPL"]}})
    assert (
        got["position"]["avg_entry"] is None
    )  # a year of fills is not the cost of the whole position


def test_crypto_export_rows_match_the_usd_pair(api, tmp_path, monkeypatch):
    c, _ = api
    export = tmp_path / "export.json"
    export.write_text(json.dumps(EXPORT))
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_EXPORT", str(export))
    got = c.get("/api/tradility-orders/BTC-USD").get_json()
    assert [o["id"] for o in got["orders"]] == ["binance-1"]
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_EXPORT", str(tmp_path / "absent.json"))
    assert c.get("/api/tradility-orders/BTC-USD").get_json()["orders"] == []


def test_the_table_shows_the_source_and_leaves_broker_rows_read_only():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "function syncNote",
        "<th>Source</th>",
        "o.source && o.source !== 'manual' ? ''",
    ):
        assert needle in html, needle


# -- placing orders goes through the local trader service ---------------------------------------


def test_trade_status_says_unavailable_when_the_service_is_not_running(api, monkeypatch):
    c, _ = api
    monkeypatch.setenv("CLOCKWORK_TRADILITY_TRADER_URL", "http://127.0.0.1:9")  # nothing listens
    got = c.get("/api/tradility-trade/status").get_json()
    assert got == {"ok": True, "available": False, "enabled": False}
    code = c.post("/api/tradility-trade/preview", json={"ticker": "AAPL"})
    assert code.status_code == 503 and "not running" in code.get_json()["error"]


def test_trade_steps_are_forwarded_with_the_service_header_and_errors_pass_through(
    api, monkeypatch
):
    c, _ = api
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path, body))
        if path == "/preview":
            return 200, {"ok": True, "preview_id": "tok", "phrase": "BUY AAPL"}
        if path == "/confirm":
            return 422, {"ok": False, "error": 'type exactly "BUY AAPL" to confirm'}
        return 200, {"ok": True, "enabled": True}

    monkeypatch.setattr(t, "call_trader", fake)
    assert c.get("/api/tradility-trade/status").get_json()["available"] is True
    assert (
        c.post("/api/tradility-trade/preview", json={"ticker": "AAPL"}).get_json()["preview_id"]
        == "tok"
    )
    refused = c.post("/api/tradility-trade/confirm", json={"preview_id": "tok", "phrase": "no"})
    assert refused.status_code == 422 and "type exactly" in refused.get_json()["error"]
    assert [s[1] for s in seen] == ["/status", "/preview", "/confirm"]


def test_only_the_three_steps_exist_and_a_body_is_required_and_csrf_applies(api, monkeypatch):
    c, _ = api
    monkeypatch.setattr(t, "call_trader", lambda *a, **k: (200, {"ok": True}))
    assert c.post("/api/tradility-trade/withdraw", json={}).status_code == 404
    assert (
        c.post("/api/tradility-trade/preview", data="x", content_type="text/plain").status_code
        == 400
    )
    del c.environ_base["HTTP_X_CSRF_TOKEN"]
    c.environ_base["HTTP_ORIGIN"] = "https://evil.example"
    assert c.post("/api/tradility-trade/preview", json={"ticker": "AAPL"}).status_code == 403


def test_the_form_previews_then_requires_the_typed_phrase_and_never_auto_sends():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "startPreview(",
        'id="trade-phrase"',
        "This sends a real order.",
        "This cancels a real order.",
        "tradeStep = null;   // a preview is single use",
        "broker-cancel",
        "canTrade()",
    ):
        assert needle in html, needle
    assert html.index("tradeCall('preview'") < html.index("tradeCall('confirm'")


def test_the_quote_route_forwards_to_the_service_and_rejects_bad_tickers(api, monkeypatch):
    c, _ = api
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path))
        return 200, {"ok": True, "mark": 100.0, "buy_price": 97.5, "sell_price": 102.5}

    monkeypatch.setattr(t, "call_trader", fake)
    assert c.get("/api/tradility-trade/quote/aapl").get_json()["buy_price"] == 97.5
    assert seen == [("GET", "/quote/AAPL")]
    assert c.get("/api/tradility-trade/quote/not a ticker!").status_code == 404


def test_the_form_defaults_to_a_gtc_limit_around_the_mark_and_makes_you_pick_the_account():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        '<option value="">Account…</option>',
        "function fillDefaults",
        "good-till-cancelled limit",
        "tradeForm.elements.side.addEventListener('change', fillDefaults)",
        "includes('*')",
        "account: f.account || ''",
    ):
        assert needle in html, needle
